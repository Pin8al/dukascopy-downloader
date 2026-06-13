"""Download task for a single instrument-hour."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from core.models.instrument import Instrument


class TaskStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"  # ticks downloaded, verified and persisted
    EMPTY = "empty"          # hour valid but contains no ticks
    FAILED = "failed"        # exhausted retries


@dataclass
class TaskProfile:
    fetch_ms: float = 0.0
    decode_ms: float = 0.0
    write_ms: float = 0.0
    total_ms: float = 0.0
    skipped: bool = False

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "fetch_ms": round(self.fetch_ms, 1),
            "decode_ms": round(self.decode_ms, 1),
            "write_ms": round(self.write_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "skipped": self.skipped,
        }


@dataclass
class HourTask:
    instrument: Instrument
    hour: datetime  # tz-aware UTC, truncated to the hour
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    tick_count: int = 0
    error: str | None = None
    profile: TaskProfile | None = None

    def __post_init__(self) -> None:
        if self.hour.tzinfo is None:
            self.hour = self.hour.replace(tzinfo=timezone.utc)

    @property
    def hour_start_ms(self) -> int:
        return int(self.hour.timestamp() * 1000)

    @property
    def hour_end_ms(self) -> int:
        return self.hour_start_ms + 3_600_000

    def tick_url(self, base_url: str) -> str:
        """JETTA hourly tick endpoint (month is 1-based)."""
        h = self.hour
        root = base_url.rstrip("/")
        return (
            f"{root}/ticks/{self.instrument.jetta_code}/"
            f"{h.year}/{h.month}/{h.day}/{h.hour}"
        )
