"""Export stored ``bin_v1`` ticks to MT5's tab-separated tick CSV format.

The resulting file is intentionally headerless and can be selected directly in
the MetaTrader 5 custom-symbol Ticks import dialog.  It contains:

``DATE<TAB>TIME<TAB>BID<TAB>ASK<TAB>LAST<TAB>VOLUME``
"""
from __future__ import annotations

import struct
import sys
from array import array
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from storage.tick_format import BLOCK_HEADER, BYTES_PER_TICK

FLUSH_ROWS = 100_000


@dataclass(frozen=True)
class CsvExportResult:
    """Summary of a completed CSV export."""

    output_path: Path
    ticks: int
    hour_files: int


def iter_hour_ticks(path: Path) -> Iterator[tuple[int, float, float]]:
    """Yield timestamp-ms, bid, and ask values from one bin_v1 hour file."""
    with path.open("rb") as handle:
        raw_count = handle.read(BLOCK_HEADER.size)
        if len(raw_count) != BLOCK_HEADER.size:
            raise ValueError(f"Invalid tick header: {path}")
        count = BLOCK_HEADER.unpack(raw_count)[0]
        expected = BLOCK_HEADER.size + count * BYTES_PER_TICK
        if path.stat().st_size != expected:
            raise ValueError(f"Unexpected size for {path}: expected {expected} bytes")

        timestamps = array("q")
        bids = array("d")
        asks = array("d")
        timestamps.frombytes(handle.read(count * 8))
        bids.frombytes(handle.read(count * 8))
        asks.frombytes(handle.read(count * 8))
        if sys.byteorder != "little":
            timestamps.byteswap()
            bids.byteswap()
            asks.byteswap()

    yield from zip(timestamps, bids, asks)


def export_mt5_csv(
    source_paths: Sequence[Path],
    output_path: Path,
    *,
    decimals: int,
    overwrite: bool = False,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> CsvExportResult:
    """Write ordered hour files to one MT5-compatible CSV atomically.

    ``on_progress`` receives ``(hours_done, hours_total, ticks_written)``.
    An existing output is never replaced unless ``overwrite`` is set.
    """
    if decimals < 0:
        raise ValueError("decimals must be non-negative")
    if not source_paths:
        raise FileNotFoundError("No hour tick files found for CSV export")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    if partial_path.exists():
        partial_path.unlink()

    rows = 0
    total = len(source_paths)
    last_second = -1
    timestamp_prefix = ""
    buffer: list[str] = []

    try:
        with partial_path.open("w", encoding="ascii", newline="\n", buffering=8 * 1024 * 1024) as out:
            for hour_index, path in enumerate(source_paths, start=1):
                for timestamp_ms, bid, ask in iter_hour_ticks(path):
                    second, millisecond = divmod(int(timestamp_ms), 1000)
                    if second != last_second:
                        timestamp_prefix = datetime.fromtimestamp(
                            second, timezone.utc,
                        ).strftime("%Y.%m.%d\t%H:%M:%S")
                        last_second = second
                    buffer.append(
                        f"{timestamp_prefix}.{millisecond:03d}\t"
                        f"{bid:.{decimals}f}\t{ask:.{decimals}f}\t0\t0\n"
                    )
                    rows += 1
                    if len(buffer) >= FLUSH_ROWS:
                        out.write("".join(buffer))
                        buffer.clear()

                if buffer:
                    out.write("".join(buffer))
                    buffer.clear()
                if on_progress:
                    on_progress(hour_index, total, rows)

        partial_path.replace(output_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise

    return CsvExportResult(output_path=output_path, ticks=rows, hour_files=total)
