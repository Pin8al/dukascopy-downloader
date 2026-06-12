"""Parquet tick store: the source of truth.

One Parquet file per instrument-hour, mirroring the Dukascopy feed layout:

    data/EURUSD/2025/01/01/14.parquet

Files are written atomically (temp file + rename) and existing files are
never overwritten, so a crash mid-run can never corrupt stored data.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from core.models.instrument import Instrument
from core.models.tick import Tick

TICK_SCHEMA = pa.schema(
    [
        pa.field("timestamp_ms", pa.int64()),
        pa.field("bid", pa.float64()),
        pa.field("ask", pa.float64()),
        pa.field("bid_volume", pa.float32()),
        pa.field("ask_volume", pa.float32()),
    ]
)


class ParquetStorage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def hour_path(self, instrument: Instrument, hour: datetime) -> Path:
        h = hour.astimezone(timezone.utc)
        return (
            self.data_dir / instrument.symbol
            / f"{h.year:04d}" / f"{h.month:02d}" / f"{h.day:02d}"
            / f"{h.hour:02d}.parquet"
        )

    def has_hour(self, instrument: Instrument, hour: datetime) -> bool:
        return self.hour_path(instrument, hour).exists()

    def write_hour(self, instrument: Instrument, hour: datetime, ticks: list[Tick]) -> Path:
        path = self.hour_path(instrument, hour)
        if path.exists():
            return path  # never overwrite valid data
        path.parent.mkdir(parents=True, exist_ok=True)

        table = pa.table(
            {
                "timestamp_ms": [t.timestamp_ms for t in ticks],
                "bid": [t.bid for t in ticks],
                "ask": [t.ask for t in ticks],
                "bid_volume": [t.bid_volume for t in ticks],
                "ask_volume": [t.ask_volume for t in ticks],
            },
            schema=TICK_SCHEMA,
        )
        tmp_path = path.with_name(path.name + ".tmp")
        pq.write_table(table, tmp_path, compression="zstd")
        os.replace(tmp_path, path)
        return path

    def iter_range(
        self, instrument: Instrument, hours: list[datetime]
    ) -> Iterator[tuple[datetime, pa.Table]]:
        """Yield (hour, table) for every stored hour, in the given order."""
        for hour in hours:
            path = self.hour_path(instrument, hour)
            if path.exists():
                yield hour, pq.read_table(path)

    def list_stored_hours(
        self,
        instrument: Instrument,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[datetime]:
        """Return sorted UTC hours that have a Parquet file on disk."""
        root = self.data_dir / instrument.symbol
        if not root.exists():
            return []

        if start is not None:
            start = start.astimezone(timezone.utc)
        if end is not None:
            end = end.astimezone(timezone.utc)

        hours: list[datetime] = []
        for path in root.rglob("*.parquet"):
            try:
                rel = path.relative_to(root)
                if len(rel.parts) != 4:
                    continue
                hour = datetime(
                    int(rel.parts[0]),
                    int(rel.parts[1]),
                    int(rel.parts[2]),
                    int(path.stem),
                    tzinfo=timezone.utc,
                )
            except (ValueError, IndexError):
                continue
            if start is not None and hour < start:
                continue
            if end is not None and hour > end:
                continue
            hours.append(hour)
        hours.sort()
        return hours
