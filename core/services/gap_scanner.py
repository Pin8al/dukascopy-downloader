"""Detect and repair holes in the stored dataset.

A gap is any hour inside the requested range that is neither completed nor
empty in the ledger: hours that failed permanently, or were never attempted
(interrupted run, range extension, deleted ledger rows...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from config.settings import Settings
from core.exceptions import IncompleteDatasetError
from core.models.instrument import Instrument
from core.models.task import HourTask, TaskStatus
from core.services.planner import Planner, _fx_session_break
from storage.metadata_db import MetadataDB, _hour_key

_HOUR = timedelta(hours=1)
_GAP_SAMPLE_LIMIT = 32


@dataclass
class GapReport:
    instrument_id: str
    total_hours: int = 0
    completed: int = 0
    empty: int = 0
    missing_count: int = 0
    failed_hours: list[datetime] = field(default_factory=list)
    missing_hours: list[datetime] = field(default_factory=list)
    empty_hours: list[datetime] = field(default_factory=list)

    @property
    def gap_hours(self) -> list[datetime]:
        """Hours that were never finished: failed or never attempted."""
        return sorted(self.failed_hours + self.missing_hours)

    def repair_hours(self, refetch_empty: bool = False) -> list[datetime]:
        hours = list(self.gap_hours)
        if refetch_empty:
            hours.extend(self.empty_hours)
        return sorted(hours)

    @property
    def is_complete(self) -> bool:
        return self.missing_count == 0 and not self.failed_hours


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
        return self._scan_span(instrument, span[0], span[1])

    def _scan_span(
        self,
        instrument: Instrument,
        start_hour: datetime,
        end_hour: datetime,
    ) -> GapReport:
        report = GapReport(instrument_id=instrument.id)
        recorded = self.db.status_map(instrument.id, start_hour, end_hour)
        cursor = start_hour
        while cursor <= end_hour:
            if not instrument.continuous_trading and _fx_session_break(cursor):
                cursor += _HOUR
                continue
            report.total_hours += 1
            status = recorded.get(_hour_key(cursor))
            if status == TaskStatus.COMPLETED.value:
                report.completed += 1
            elif status == TaskStatus.EMPTY.value:
                report.empty += 1
                if len(report.empty_hours) < _GAP_SAMPLE_LIMIT:
                    report.empty_hours.append(cursor)
            elif status == TaskStatus.FAILED.value:
                report.failed_hours.append(cursor)
            else:
                report.missing_count += 1
                if len(report.missing_hours) < _GAP_SAMPLE_LIMIT:
                    report.missing_hours.append(cursor)
            cursor += _HOUR
        return report

    def _scan_hours(self, instrument: Instrument, hours: list[datetime]) -> GapReport:
        report = GapReport(instrument_id=instrument.id)
        report.total_hours = len(hours)
        if not hours:
            return report

        recorded = self.db.status_map(instrument.id, hours[0], hours[-1])
        for hour in hours:
            status = recorded.get(_hour_key(hour))
            if status == TaskStatus.COMPLETED.value:
                report.completed += 1
            elif status == TaskStatus.EMPTY.value:
                report.empty += 1
                if len(report.empty_hours) < _GAP_SAMPLE_LIMIT:
                    report.empty_hours.append(hour)
            elif status == TaskStatus.FAILED.value:
                report.failed_hours.append(hour)
            else:
                report.missing_count += 1
                if len(report.missing_hours) < _GAP_SAMPLE_LIMIT:
                    report.missing_hours.append(hour)
        return report

    def build_repair_tasks(
        self,
        instrument: Instrument,
        report: GapReport,
        refetch_empty: bool = False,
    ) -> list[HourTask]:
        return [
            HourTask(instrument=instrument, hour=hour)
            for hour in report.repair_hours(refetch_empty=refetch_empty)
        ]

    def scan_for_import(
        self,
        instrument: Instrument,
        *,
        import_all: bool = False,
        start: date | None = None,
        end: date | None = None,
    ) -> tuple[GapReport | None, str]:
        if import_all:
            span = self.db.recorded_span(instrument.id)
            if span is None:
                return None, ""
            report = self.scan_all(instrument)
            range_label = (
                f"{span[0]:%Y-%m-%d %H:%M} -> {span[1]:%Y-%m-%d %H:%M} UTC (all recorded)"
            )
            return report, range_label
        if start is None or end is None:
            raise ValueError("start and end dates are required")
        report = self.scan(instrument, start, end)
        return report, f"{start} -> {end}"


def require_complete_import(
    report: GapReport | None,
    symbol: str,
    range_label: str,
) -> None:
    if report is None:
        raise IncompleteDatasetError(f"No data recorded for {symbol} yet.")
    if not report.is_complete:
        missing = report.missing_count
        failed = len(report.failed_hours)
        raise IncompleteDatasetError(
            f"{symbol}: {missing + failed} hour(s) not complete in {range_label} "
            f"({missing} missing, {failed} failed). Run gaps --repair first."
        )
