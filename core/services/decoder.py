"""Decode Dukascopy .bi5 payloads into ticks.

A .bi5 file is an LZMA stream of fixed 20-byte big-endian records:
    int32  millisecond offset within the hour
    int32  ask price  (integer, scaled by the instrument's decimal factor)
    int32  bid price  (integer, scaled by the instrument's decimal factor)
    float32 ask volume
    float32 bid volume
"""
from __future__ import annotations

import lzma
import struct

import pyarrow as pa

from core.models.tick import Tick
from storage.parquet_storage import TICK_SCHEMA

_RECORD = struct.Struct(">3i2f")
RECORD_SIZE = _RECORD.size  # 20 bytes


class DecodeError(Exception):
    pass


def decode_bi5(raw: bytes, hour_start_ms: int, decimal_factor: int) -> list[Tick]:
    if not raw:
        return []
    try:
        payload = lzma.decompress(raw)
    except lzma.LZMAError as exc:
        raise DecodeError(f"LZMA decompression failed: {exc}") from exc

    if len(payload) % RECORD_SIZE != 0:
        raise DecodeError(
            f"Payload size {len(payload)} is not a multiple of {RECORD_SIZE} bytes"
        )

    factor = float(decimal_factor)
    ticks: list[Tick] = []
    for offset_ms, ask_raw, bid_raw, ask_vol, bid_vol in _RECORD.iter_unpack(payload):
        ticks.append(
            Tick(
                timestamp_ms=hour_start_ms + offset_ms,
                bid=bid_raw / factor,
                ask=ask_raw / factor,
                bid_volume=bid_vol,
                ask_volume=ask_vol,
            )
        )
    return ticks


def decode_bi5_table(raw: bytes, hour_start_ms: int, decimal_factor: int) -> pa.Table:
    """Columnar decode — avoids per-tick objects before Parquet write."""
    if not raw:
        return pa.table(
            {name: [] for name in TICK_SCHEMA.names},
            schema=TICK_SCHEMA,
        )
    try:
        payload = lzma.decompress(raw)
    except lzma.LZMAError as exc:
        raise DecodeError(f"LZMA decompression failed: {exc}") from exc

    if len(payload) % RECORD_SIZE != 0:
        raise DecodeError(
            f"Payload size {len(payload)} is not a multiple of {RECORD_SIZE} bytes"
        )

    factor = float(decimal_factor)
    ts: list[int] = []
    bids: list[float] = []
    asks: list[float] = []
    bid_vols: list[float] = []
    ask_vols: list[float] = []
    for offset_ms, ask_raw, bid_raw, ask_vol, bid_vol in _RECORD.iter_unpack(payload):
        ts.append(hour_start_ms + offset_ms)
        bids.append(bid_raw / factor)
        asks.append(ask_raw / factor)
        bid_vols.append(bid_vol)
        ask_vols.append(ask_vol)

    return pa.table(
        {
            "timestamp_ms": ts,
            "bid": bids,
            "ask": asks,
            "bid_volume": bid_vols,
            "ask_volume": ask_vols,
        },
        schema=TICK_SCHEMA,
    )
