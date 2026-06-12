"""Turn a date range into the minimal set of hour tasks that still need work.

The planner:
- clamps the range to the instrument's earliest available data and to the
  publication lag at the recent edge;
- skips hours already recorded as completed or empty (resume for free);
- marks guaranteed-closed market hours (Saturdays, early Sundays) as empty
  without issuing any HTTP request, except for 24/7 crypto instruments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from config.settings import Settings
from core.models.instrument import Instrument
from core.models.task import HourTask, TaskStatus
from storage.metadata_db import MetadataDB, _hour_key

HOUR = timedelta(hours=1)


def is_market_closed_hour(hour: datetime) -> bool:
    """Hours when FX/CFD markets are guaranteed closed (UTC).

    Friday close is ~21:00-22:00 UTC and Sunday open is ~21:00-22:00 UTC
    depending on DST, so Saturday is always closed and Sunday is closed at
    least until 20:00 UTC. Friday evening and late Sunday are downloaded
    normally and simply come back empty when the market was closed.
    """
    if hour.weekday() == 5:  # Saturday
        return True
    return hour.weekday() == 6 and hour.hour < 20  # Sunday before 20:00 UTC


@dataclass
class PlanResult:
    tasks: list[HourTask] = field(default_factory=list)
    already_done: int = 0
    auto_empty: int = 0
    total_hours: int = 0
    effective_start: datetime | None = None
    effective_end: datetime | None = None


class Planner:
    def __init__(self, settings: Settings, db: MetadataDB):
        self.settings = settings
        self.db = db

    def hours_in_range(
        self, instrument: Instrument, start_date: date, end_date: date
    ) -> list[datetime]:
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        end = datetime(end_date.year, end_date.month, end_date.day, 23, tzinfo=timezone.utc)

        if instrument.earliest_tick_utc is not None:
            earliest = instrument.earliest_tick_utc.replace(minute=0, second=0, microsecond=0)
            start = max(start, earliest)

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = min(end, now - timedelta(hours=self.settings.min_data_lag_hours))

        hours = []
        cursor = start
        while cursor <= end:
            hours.append(cursor)
            cursor += HOUR
        return hours

    def plan(
        self,
        instrument: Instrument,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> PlanResult:
        result = PlanResult()
        hours = self.hours_in_range(instrument, start_date, end_date)
        result.total_hours = len(hours)
        if not hours:
            return result
        result.effective_start, result.effective_end = hours[0], hours[-1]

        recorded = {} if force else self.db.status_map(instrument.id, hours[0], hours[-1])
        skip_closed = (
            self.settings.skip_closed_market_hours and not instrument.trades_weekends
        )

        for hour in hours:
            status = recorded.get(_hour_key(hour))
            if status in (TaskStatus.COMPLETED.value, TaskStatus.EMPTY.value):
                result.already_done += 1
                continue
            if skip_closed and is_market_closed_hour(hour):
                self.db.mark(instrument.id, hour, TaskStatus.EMPTY, error="market closed")
                result.auto_empty += 1
                continue
            result.tasks.append(HourTask(instrument=instrument, hour=hour))
        return result
