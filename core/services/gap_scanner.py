"""Detect and repair holes in the stored dataset.

A gap is any hour inside the requested range that is neither completed nor
empty in the ledger: hours that failed permanently, or were never attempted
(interrupted run, range extension, deleted ledger rows...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from config.settings import Settings
from core.models.instrument import Instrument
from core.models.task import HourTask, TaskStatus
from core.services.planner import Planner, is_market_closed_hour
from storage.metadata_db import MetadataDB, _hour_key

_HOUR = timedelta(hours=1)


@dataclass
class GapReport:
    instrument_id: str
    total_hours: int = 0
    completed: int = 0
    empty: int = 0
    failed_hours: list[datetime] = field(default_factory=list)
    missing_hours: list[datetime] = field(default_factory=list)

    @property
    def gap_hours(self) -> list[datetime]:
        return sorted(self.failed_hours + self.missing_hours)

    @property
    def is_complete(self) -> bool:
        return not self.gap_hours


class GapScanner:
    def __init__(self, settings: Settings, db: MetadataDB):
        self.settings = settings
        self.db = db
        self.planner = Planner(settings, db)

    def scan(self, instrument: Instrument, start_date: date, end_date: date) -> GapReport:
        hours = self.planner.hours_in_range(instrument, start_date, end_date)
        return self._scan_hours(instrument, hours)

    def scan_all(self, instrument: Instrument) -> GapReport | None:
        """Scan every hour between the first and last ledger entry for this symbol."""
        span = self.db.recorded_span(instrument.id)
        if span is None:
            return None
        start_hour, end_hour = span
        hours = []
        cursor = start_hour
        while cursor <= end_hour:
            hours.append(cursor)
            cursor += _HOUR
        return self._scan_hours(instrument, hours)

    def _scan_hours(self, instrument: Instrument, hours: list[datetime]) -> GapReport:
        report = GapReport(instrument_id=instrument.id)
        report.total_hours = len(hours)
        if not hours:
            return report

        recorded = self.db.status_map(instrument.id, hours[0], hours[-1])
        skip_closed = (
            self.settings.skip_closed_market_hours and not instrument.trades_weekends
        )
        for hour in hours:
            status = recorded.get(_hour_key(hour))
            if status == TaskStatus.COMPLETED.value:
                report.completed += 1
            elif status == TaskStatus.EMPTY.value:
                report.empty += 1
            elif status == TaskStatus.FAILED.value:
                report.failed_hours.append(hour)
            elif skip_closed and is_market_closed_hour(hour):
                report.empty += 1  # guaranteed closed; not a gap
            else:
                report.missing_hours.append(hour)
        return report

    def build_repair_tasks(self, instrument: Instrument, report: GapReport) -> list[HourTask]:
        return [HourTask(instrument=instrument, hour=hour) for hour in report.gap_hours]
