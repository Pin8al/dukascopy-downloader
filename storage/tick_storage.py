"""Binary tick store: MT5-ready files, one per instrument-hour.

Layout mirrors the download tree:

    data/EURUSD/2025/01/01/14.bin

Each file is a single bin_v1 block (see storage.tick_format). Files are written
atomically and never overwritten.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.models.instrument import Instrument
from core.models.tick import Tick
from core.models.tick_batch import TickBatch
from storage.fast_delete import fast_remove_tree, queue_remove
from storage.tick_format import write_hour_file

HOUR_SUFFIX = ".bin"


class TickStorage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def hour_path(self, instrument: Instrument, hour: datetime) -> Path:
        h = hour.astimezone(timezone.utc)
        return (
            self.data_dir / instrument.symbol
            / f"{h.year:04d}" / f"{h.month:02d}" / f"{h.day:02d}"
            / f"{h.hour:02d}{HOUR_SUFFIX}"
        )

    def has_hour(self, instrument: Instrument, hour: datetime) -> bool:
        return self.hour_path(instrument, hour).is_file()

    def write_hour(self, instrument: Instrument, hour: datetime, ticks: list[Tick]) -> Path:
        path = self.hour_path(instrument, hour)
        if path.is_file():
            return path
        write_hour_file(
            path,
            [t.timestamp_ms for t in ticks],
            [t.bid for t in ticks],
            [t.ask for t in ticks],
        )
        return path

    def write_hour_batch(self, instrument: Instrument, hour: datetime, batch: TickBatch) -> Path:
        path = self.hour_path(instrument, hour)
        if path.is_file():
            return path
        write_hour_file(path, batch.timestamp_ms, batch.bid, batch.ask)
        return path

    def list_stored_hours(
        self,
        instrument: Instrument,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[datetime]:
        """Return sorted UTC hours that have a .bin file on disk."""
        root = self.data_dir / instrument.symbol
        if not root.is_dir():
            return []

        if start is not None:
            start = start.astimezone(timezone.utc)
        if end is not None:
            end = end.astimezone(timezone.utc)

        hours: list[datetime] = []
        for path in root.rglob(f"*{HOUR_SUFFIX}"):
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

    def delete_symbol(self, symbol: str) -> bool:
        """Delete all tick files for a symbol (blocking). Returns True if data existed."""
        trash = queue_remove(self.data_dir, symbol)
        if trash is None:
            return False
        fast_remove_tree(trash)
        return True

    def queue_delete_symbol(self, symbol: str) -> Path | None:
        """Move symbol data aside instantly; call finish_delete() to free disk."""
        return queue_remove(self.data_dir, symbol)

    @staticmethod
    def finish_delete(trash_path: Path) -> None:
        fast_remove_tree(trash_path)
