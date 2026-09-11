"""Publish stored hour .bin files into an MT5 Common Files job folder.

Each hour file is hard-linked (or copied) as h000000.bin, h000001.bin, … with
an hours.txt manifest. No tick counting — just link and let MT5 pump.
"""
from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from config.settings import Settings
from core.exceptions import JobCancelled
from core.models.instrument import Instrument
from core.services.planner import Planner
from storage.metadata_db import MetadataDB
from storage.tick_storage import TickStorage

HOURS_MANIFEST = "hours.txt"
_PROGRESS_MIN_INTERVAL = 0.35


@dataclass
class PublishResult:
    job_dir: Path
    rows: int
    hours_with_data: int
    files_published: int
    bytes_linked: int


class MT5TickPublisher:
    """Link hour tick files into a job folder for multi-file MT5 import."""

    def __init__(
        self,
        settings: Settings,
        storage: TickStorage,
        planner: Planner,
        metadata: MetadataDB | None = None,
    ):
        self.settings = settings
        self.storage = storage
        self.planner = planner
        self.metadata = metadata

    def publish(
        self,
        instrument: Instrument,
        start_date: date,
        end_date: date,
        job_dir: Path,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        start_hour = datetime(
            start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc,
        )
        end_hour = datetime(
            end_date.year, end_date.month, end_date.day, 23, tzinfo=timezone.utc,
        )
        sources, total_ticks = self._resolve_sources(instrument, start_hour, end_hour)
        return self._publish_sources(
            sources, total_ticks, job_dir,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def publish_all(
        self,
        instrument: Instrument,
        start_hour: datetime,
        end_hour: datetime,
        job_dir: Path,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        sources, total_ticks = self._resolve_sources(instrument, start_hour, end_hour)
        return self._publish_sources(
            sources, total_ticks, job_dir,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def publish_sources(
        self,
        sources: list[Path],
        total_ticks: int,
        job_dir: Path,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        """Publish a caller-selected source batch for a single MT5 import run."""
        return self._publish_sources(
            sources, total_ticks, job_dir,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def resolve_sources(
        self,
        instrument: Instrument,
        start_hour: datetime,
        end_hour: datetime,
    ) -> tuple[list[Path], int]:
        """Return the ordered, non-empty hour files for a requested span."""
        return self._resolve_sources(instrument, start_hour, end_hour)

    def _resolve_sources(
        self,
        instrument: Instrument,
        start_hour: datetime,
        end_hour: datetime,
    ) -> tuple[list[Path], int]:
        sources: list[Path] = []
        total_ticks = 0

        if self.metadata is not None:
            total_ticks = self.metadata.sum_completed_ticks(
                instrument.id, start_hour, end_hour,
            )
            rows = self.metadata.list_completed_sources(
                instrument.id, start_hour, end_hour,
            )
            if rows:
                for file_path, _tick_count, hour_utc in rows:
                    if file_path:
                        path = Path(file_path)
                    else:
                        hour = datetime.fromisoformat(hour_utc).replace(tzinfo=timezone.utc)
                        path = self.storage.hour_path(instrument, hour)
                    sources.append(path)
                return sources, total_ticks

        hours = self.storage.list_stored_hours(instrument, start_hour, end_hour)
        for hour in hours:
            sources.append(self.storage.hour_path(instrument, hour))
        return sources, 0

    def _publish_sources(
        self,
        sources: list[Path],
        total_ticks: int,
        job_dir: Path,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        if not sources:
            raise FileNotFoundError("No hour tick files found for import range")

        job_dir.mkdir(parents=True, exist_ok=True)
        done = 0
        bytes_linked = 0
        last_emit = 0.0
        hour_names: list[str] = []
        total = len(sources)

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise JobCancelled()

        def emit(force: bool = False) -> None:
            nonlocal last_emit
            if not on_progress:
                return
            now = time.monotonic()
            if not force and now - last_emit < _PROGRESS_MIN_INTERVAL:
                return
            last_emit = now
            percent = round(min(99.9, 100 * done / total), 1) if total else 100.0
            on_progress({
                "done": done,
                "files_total": total,
                "rows": total_ticks,
                "total_ticks": total_ticks,
                "hours_with_data": len(hour_names),
                "percent": percent,
                "message": f"Linking · {done:,} / {total:,} hour file(s)",
            })

        emit(force=True)
        for index, src in enumerate(sources):
            check_cancel()
            if not src.is_file():
                raise FileNotFoundError(f"Missing tick file: {src}")

            size = src.stat().st_size
            if size == 0:
                done += 1
                emit()
                continue

            name = f"h{index:06d}.bin"
            dst = job_dir / name
            if dst.exists():
                dst.unlink()
            _link_or_copy(src, dst)

            hour_names.append(name)
            bytes_linked += size
            done += 1
            emit()

        if not hour_names:
            raise FileNotFoundError("No non-empty hour tick files found for import")

        (job_dir / HOURS_MANIFEST).write_text("\n".join(hour_names) + "\n", encoding="utf-8")
        emit(force=True)

        return PublishResult(
            job_dir=job_dir,
            rows=total_ticks,
            hours_with_data=len(hour_names),
            files_published=len(hour_names),
            bytes_linked=bytes_linked,
        )


def _link_or_copy(src: Path, dst: Path) -> None:
    same_drive = (
        os.path.splitdrive(str(src.resolve()))[0].lower()
        == os.path.splitdrive(str(dst.resolve()))[0].lower()
    )
    if same_drive:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)
