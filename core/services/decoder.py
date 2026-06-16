"""Decode Dukascopy JETTA JSON tick payloads into columnar batches.

JETTA returns delta-encoded tick history per hour chunk:
    base timestamp, bid, ask + parallel arrays:
    times[], bids[], asks[], bidVolumes[], askVolumes[]
"""
from __future__ import annotations

import math
from typing import Any

from core.models.tick_batch import TickBatch


class DecodeError(Exception):
    pass


def _price_precision(multiplier: float) -> float:
    if not multiplier:
        return 1.0
    exp = math.floor(math.log10(multiplier))
    return multiplier if exp > 0 else 10 ** abs(exp)


def _apply_delta(base: float, delta: float, multiplier: float, precision: float) -> float:
    return round((base + delta * multiplier) * precision) / precision


def decode_jetta_batch(
    payload: dict[str, Any],
    hour_start_ms: int,
    hour_end_ms: int,
) -> TickBatch:
    """Expand JETTA delta arrays and keep ticks inside [hour_start_ms, hour_end_ms)."""
    times = payload.get("times") or []
    if not times:
        return TickBatch.empty()

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

    batch = TickBatch()

    for index in range(length):
        time_ms += int(times[index])
        bid = _apply_delta(bid, float(bids[index]), multiplier, precision)
        ask = _apply_delta(ask, float(asks[index]), multiplier, precision)
        if hour_start_ms <= time_ms < hour_end_ms:
            batch.timestamp_ms.append(time_ms)
            batch.bid.append(bid)
            batch.ask.append(ask)
            batch.bid_volume.append(float(bid_volumes[index]))
            batch.ask_volume.append(float(ask_volumes[index]))

    return batch
