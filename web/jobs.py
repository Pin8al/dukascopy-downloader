"""Background job runner with live progress for the web UI."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from collections.abc import Callable
from typing import Any

from config.settings import Settings
from core.exceptions import JobCancelled
from core.services.download_engine import DownloadEngine
from core.exceptions import IncompleteDatasetError
from core.services.gap_scanner import GapScanner, require_complete_export
from core.services.instrument_search import InstrumentCatalog, UnknownInstrumentError
from core.services.planner import Planner
from export.mt5_csv_exporter import MT5CsvExporter
from storage.metadata_db import MetadataDB
from storage.parquet_storage import ParquetStorage


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")
    params: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status.value,
                "created_at": self.created_at,
                "params": self.params,
                "progress": self.progress,
                "result": self.result,
                "error": self.error,
            }

    def set_running(self, message: str = "") -> None:
        with self._lock:
            self.status = JobStatus.RUNNING
            if message:
                self.progress["message"] = message

    def set_progress(self, **fields: Any) -> None:
        with self._lock:
            self.progress.update(fields)

    def finish(self, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.status = JobStatus.COMPLETED
            self.result = result
            self.progress["percent"] = 100

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = JobStatus.FAILED
            self.error = error

    def request_cancel(self) -> bool:
        with self._lock:
            if self.status in (
                JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
            ):
                return False
        self._cancel.set()
        self.set_progress(message="Cancelling…")
        return True

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def mark_cancelled(self) -> None:
        with self._lock:
            self.status = JobStatus.CANCELLED
            self.progress["message"] = "Cancelled"


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            by_age = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            active = [j for j in by_age if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
            active.sort(key=lambda j: 0 if j.status == JobStatus.RUNNING else 1)
            rest = [j for j in by_age if j.status not in (JobStatus.PENDING, JobStatus.RUNNING)]
            jobs = active + rest
        return [job.to_dict() for job in jobs[:limit]]

    def submit(self, kind: str, params: dict[str, Any], worker: Callable[[Job], None]) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, params=params)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            if job.is_cancelled():
                job.mark_cancelled()
                return
            job.set_running()
            try:
                worker(job)
            except JobCancelled:
                job.mark_cancelled()
            except Exception as exc:  # noqa: BLE001
                if not job.is_cancelled():
                    job.fail(str(exc))

        threading.Thread(target=run, daemon=True).start()
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None or not job.request_cancel():
            return None
        return job


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def run_download_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: ParquetStorage,
) -> None:
    symbols = job.params["symbols"]
    start = _parse_date(job.params["start"])
    end = _parse_date(job.params["end"])
    force = bool(job.params.get("force", False))
    workers = job.params.get("workers")

    if end < start:
        raise ValueError("end date is before start date")

    local_settings = settings.for_job(workers)

    planner = Planner(local_settings, metadata)
    engine = DownloadEngine(local_settings, store, metadata)

    all_tasks = []
    plans: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        instrument = cat.get(symbol)
        plan = planner.plan(instrument, start, end, force=force)
        plans[instrument.symbol] = {
            "total_hours": plan.total_hours,
            "to_download": len(plan.tasks),
            "already_done": plan.already_done,
            "effective_start": plan.effective_start.isoformat() if plan.effective_start else None,
            "effective_end": plan.effective_end.isoformat() if plan.effective_end else None,
            "clamped_start": (
                plan.effective_start.date().isoformat()
                if plan.effective_start and plan.effective_start.date() > start
                else None
            ),
        }
        all_tasks.extend(plan.tasks)

    job.set_progress(
        message="Planning complete",
        symbols=list(plans.keys()),
        plans=plans,
        total=len(all_tasks),
        done=0,
        percent=0 if all_tasks else 100,
    )

    if not all_tasks:
        job.finish({
            "message": "All hours already downloaded",
            "plans": plans,
            "completed": 0,
            "empty": 0,
            "failed": 0,
            "ticks": 0,
        })
        return

    def on_progress(snapshot: dict[str, Any]) -> None:
        job.set_progress(**snapshot, message=f"Downloading {snapshot.get('symbol') or ''}".strip())

    stats = engine.run(
        all_tasks,
        quiet=True,
        on_progress=on_progress,
        should_cancel=job.is_cancelled,
        refetch=force,
        profile=bool(job.params.get("profile", False)),
    )
    if job.is_cancelled():
        raise JobCancelled()
    job.finish({
        "completed": stats.completed,
        "empty": stats.empty,
        "failed": stats.failed,
        "ticks": stats.ticks,
        "plans": plans,
        "symbols": symbols,
    })


def run_export_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: ParquetStorage,
) -> None:
    symbol = job.params["symbol"]
    export_all = bool(job.params.get("export_all", False))
    instrument = cat.get(symbol)
    scanner = GapScanner(settings, metadata)
    planner = Planner(settings, metadata)
    exporter = MT5CsvExporter(settings, store, planner)
    cancel = job.is_cancelled
    progress = lambda snapshot: job.set_progress(**snapshot)

    if export_all:
        report, range_label = scanner.scan_for_export(instrument, export_all=True)
        if report is None:
            job.finish({"message": "No data recorded yet", "rows": 0, "hours_with_data": 0})
            return
        try:
            require_complete_export(report, instrument.symbol, range_label)
        except IncompleteDatasetError as exc:
            raise ValueError(str(exc)) from exc
        span = metadata.recorded_span(instrument.id)
        job.set_progress(message=f"Preparing export · {range_label}", percent=0)
        result = exporter.export_all(
            instrument, span[0], span[1],
            on_progress=progress, should_cancel=cancel,
        )
    else:
        start = _parse_date(job.params["start"])
        end = _parse_date(job.params["end"])
        if end < start:
            raise ValueError("end date is before start date")
        report, range_label = scanner.scan_for_export(
            instrument, start=start, end=end,
        )
        try:
            require_complete_export(report, instrument.symbol, range_label)
        except IncompleteDatasetError as exc:
            raise ValueError(str(exc)) from exc
        job.set_progress(message=f"Preparing export · {range_label}", percent=0)
        result = exporter.export(
            instrument, start, end,
            on_progress=progress, should_cancel=cancel,
        )

    if job.is_cancelled():
        raise JobCancelled()

    rel_path = result.path.relative_to(settings.export_dir).as_posix()
    job.finish({
        "path": rel_path,
        "filename": result.path.name,
        "rows": result.rows,
        "hours_with_data": result.hours_with_data,
        "range": range_label,
        "all": export_all,
    })


def run_gaps_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: ParquetStorage,
) -> None:
    symbols = job.params.get("symbols") or []
    if not symbols and job.params.get("symbol"):
        symbols = [job.params["symbol"]]
    if not symbols:
        raise ValueError("no symbols")

    repair = bool(job.params.get("repair", False))
    scan_all = bool(job.params.get("all", False))
    refetch_empty = bool(job.params.get("refetch_empty", False))
    scanner = GapScanner(settings, metadata)

    scan_results: list[dict[str, Any]] = []
    all_tasks = []
    total_gap_count = 0

    for idx, symbol in enumerate(symbols):
        instrument = cat.get(symbol)
        if scan_all:
            report = scanner.scan_all(instrument)
            if report is None:
                scan_results.append({
                    "symbol": instrument.symbol,
                    "message": "No data recorded yet",
                    "complete": True,
                })
                continue
            span = metadata.recorded_span(instrument.id)
            range_label = (
                f"{span[0]:%Y-%m-%d %H:%M} -> {span[1]:%Y-%m-%d %H:%M} UTC"
            )
        else:
            start = _parse_date(job.params["start"])
            end = _parse_date(job.params["end"])
            if end < start:
                raise ValueError("end date is before start date")
            report = scanner.scan(instrument, start, end)
            range_label = f"{start} -> {end}"

        gap_count = len(report.gap_hours)
        total_gap_count += gap_count
        scan_results.append({
            "symbol": instrument.symbol,
            "range": range_label,
            "complete": report.is_complete,
            "gap_count": gap_count,
            "total_hours": report.total_hours,
            "completed": report.completed,
            "empty": report.empty,
        })

        if repair and not report.is_complete:
            all_tasks.extend(
                scanner.build_repair_tasks(
                    instrument, report, refetch_empty=refetch_empty,
                ),
            )

        job.set_progress(
            message=f"Scanned {instrument.symbol}",
            symbols=symbols,
            percent=int((idx + 1) / len(symbols) * (30 if repair and all_tasks else 100)),
            gap_count=total_gap_count,
        )

    if not repair:
        job.finish({
            "complete": all(r.get("complete") for r in scan_results),
            "symbols": scan_results,
            "gap_count": total_gap_count,
        })
        return

    if not all_tasks:
        job.finish({
            "complete": True,
            "symbols": scan_results,
            "completed": 0,
            "empty": 0,
            "failed": 0,
            "ticks": 0,
            "gap_count": 0,
        })
        return

    local_settings = settings.for_job(job.params.get("workers"))
    engine = DownloadEngine(local_settings, store, metadata)

    def on_progress(snapshot: dict[str, Any]) -> None:
        job.set_progress(**snapshot, message="Downloading")

    stats = engine.run(
        all_tasks,
        quiet=True,
        on_progress=on_progress,
        should_cancel=job.is_cancelled,
        refetch=True,
    )
    if job.is_cancelled():
        raise JobCancelled()
    job.finish({
        "complete": stats.failed == 0,
        "symbols": scan_results,
        "completed": stats.completed,
        "empty": stats.empty,
        "failed": stats.failed,
        "ticks": stats.ticks,
    })


def resolve_symbol(cat: InstrumentCatalog, symbol: str) -> dict[str, Any]:
    try:
        inst = cat.get(symbol)
    except UnknownInstrumentError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "id": inst.id,
        "symbol": inst.symbol,
        "name": inst.name,
        "description": inst.description,
        "decimals": inst.price_decimals,
        "earliest": inst.earliest_tick_utc.isoformat() if inst.earliest_tick_utc else None,
    }
