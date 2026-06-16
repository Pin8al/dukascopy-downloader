"""MT5-importable binary tick block format (bin_v1).

Each hour file (and each block inside ticks.bin) is:

    uint32 tick_count (little-endian)
    int64[tick_count]   timestamp_ms
    float64[tick_count] bid
    float64[tick_count] ask

Concatenating hour files byte-for-byte produces a valid ticks.bin for import.
"""
from __future__ import annotations

import struct
from array import array
from pathlib import Path
from typing import BinaryIO

BLOCK_HEADER = struct.Struct("<I")
BYTES_PER_TICK = 24
WRITE_BUFFER = 32 * 1024 * 1024


def write_hour_block(
    out: BinaryIO,
    timestamp_ms: list[int],
    bid: list[float],
    ask: list[float],
) -> int:
    """Append one bin_v1 block. Returns tick count written."""
    count = len(timestamp_ms)
    if count != len(bid) or count != len(ask):
        raise ValueError("tick columns have different lengths")
    if count == 0:
        return 0
    if count > 0xFFFFFFFF:
        raise ValueError(f"block exceeds uint32 limit: {count} ticks")

    out.write(BLOCK_HEADER.pack(count))
    out.write(array("q", timestamp_ms).tobytes())
    out.write(array("d", bid).tobytes())
    out.write(array("d", ask).tobytes())
    return count


def write_hour_file(
    path: Path,
    timestamp_ms: list[int],
    bid: list[float],
    ask: list[float],
) -> int:
    """Write a single-hour .bin file atomically. Returns tick count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    count = 0
    with open(tmp_path, "wb", buffering=WRITE_BUFFER) as out:
        count = write_hour_block(out, timestamp_ms, bid, ask)
    tmp_path.replace(path)
    return count


def read_hour_tick_count(path: Path) -> int:
    """Read tick count from a single-hour bin_v1 file."""
    with open(path, "rb") as f:
        header = f.read(BLOCK_HEADER.size)
    if len(header) != BLOCK_HEADER.size:
        return 0
    return BLOCK_HEADER.unpack(header)[0]


def count_ticks_in_files(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        if path.is_file():
            total += read_hour_tick_count(path)
    return total


def hour_file_bytes(tick_count: int) -> int:
    if tick_count <= 0:
        return 0
    return 4 + tick_count * BYTES_PER_TICK
