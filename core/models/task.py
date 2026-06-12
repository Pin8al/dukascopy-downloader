"""Download task for a single instrument-hour."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from core.models.instrument import Instrument


class TaskStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"  # ticks downloaded, verified and persisted
    EMPTY = "empty"          # hour valid but contains no ticks (404 / market closed)
    FAILED = "failed"        # exhausted retries


@dataclass
class HourTask:
    instrument: Instrument
    hour: datetime  # tz-aware UTC, truncated to the hour
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    tick_count: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if self.hour.tzinfo is None:
            self.hour = self.hour.replace(tzinfo=timezone.utc)

    @property
    def hour_start_ms(self) -> int:
        return int(self.hour.timestamp() * 1000)

    def url(self, base_url: str) -> str:
        h = self.hour
        # Dukascopy months are zero-based in the URL scheme.
        return (
            f"{base_url}/{self.instrument.feed_code}/"
            f"{h.year:04d}/{h.month - 1:02d}/{h.day:02d}/{h.hour:02d}h_ticks.bi5"
        )
