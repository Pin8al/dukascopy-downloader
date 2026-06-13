"""Decode Dukascopy JETTA JSON tick payloads into columnar tables.

JETTA returns delta-encoded tick history per hour chunk:
    base timestamp, bid, ask + parallel arrays:
    times[], bids[], asks[], bidVolumes[], askVolumes[]
"""
from __future__ import annotations

import math
from typing import Any

import pyarrow as pa

from storage.parquet_storage import TICK_SCHEMA


class DecodeError(Exception):
    pass


def _price_precision(multiplier: float) -> float:
    if not multiplier:
        return 1.0
    exp = math.floor(math.log10(multiplier))
    return multiplier if exp > 0 else 10 ** abs(exp)


def _apply_delta(base: float, delta: float, multiplier: float, precision: float) -> float:
    return round((base + delta * multiplier) * precision) / precision


def _empty_table() -> pa.Table:
    return pa.table({name: [] for name in TICK_SCHEMA.names}, schema=TICK_SCHEMA)


def decode_jetta_table(
    payload: dict[str, Any],
    hour_start_ms: int,
    hour_end_ms: int,
) -> pa.Table:
    """Expand JETTA delta arrays and keep ticks inside [hour_start_ms, hour_end_ms)."""
    times = payload.get("times") or []
    if not times:
        return _empty_table()

    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    bid_volumes = payload.get("bidVolumes") or []
    ask_volumes = payload.get("askVolumes") or []
    length = len(times)
    if not (len(bids) == length == len(asks) == len(bid_volumes) == len(ask_volumes)):
        raise DecodeError("TICKS history is not consistent")

    multiplier = float(payload.get("multiplier") or 1)
    precision = _price_precision(multiplier)
    time_ms = int(payload.get("timestamp") or 0)
    bid = float(payload.get("bid") or 0)
    ask = float(payload.get("ask") or 0)

    ts_out: list[int] = []
    bids_out: list[float] = []
    asks_out: list[float] = []
    bid_vols_out: list[float] = []
    ask_vols_out: list[float] = []

    for index in range(length):
        time_ms += int(times[index])
        bid = _apply_delta(bid, float(bids[index]), multiplier, precision)
        ask = _apply_delta(ask, float(asks[index]), multiplier, precision)
        if hour_start_ms <= time_ms < hour_end_ms:
            ts_out.append(time_ms)
            bids_out.append(bid)
            asks_out.append(ask)
            bid_vols_out.append(float(bid_volumes[index]))
            ask_vols_out.append(float(ask_volumes[index]))

    if not ts_out:
        return _empty_table()

    return pa.table(
        {
            "timestamp_ms": ts_out,
            "bid": bids_out,
            "ask": asks_out,
            "bid_volume": bid_vols_out,
            "ask_volume": ask_vols_out,
        },
        schema=TICK_SCHEMA,
    )
