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
from core.services.download_engine import DownloadEngine
from core.services.gap_scanner import GapScanner
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


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [job.to_dict() for job in jobs[:limit]]

    def submit(self, kind: str, params: dict[str, Any], worker: Callable[[Job], None]) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, params=params)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            job.set_running()
            try:
                worker(job)
            except Exception as exc:  # noqa: BLE001
                job.fail(str(exc))

        threading.Thread(target=run, daemon=True).start()
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

    local_settings = Settings(
        data_dir=settings.data_dir,
        export_dir=settings.export_dir,
        db_path=settings.db_path,
        instruments_file=settings.instruments_file,
        max_workers=workers or settings.max_workers,
    )

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

    stats = engine.run(all_tasks, quiet=True, on_progress=on_progress)
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
    start = _parse_date(job.params["start"])
    end = _parse_date(job.params["end"])
    if end < start:
        raise ValueError("end date is before start date")

    instrument = cat.get(symbol)
    planner = Planner(settings, metadata)
    scanner = GapScanner(settings, metadata)
    report = scanner.scan(instrument, start, end)

    job.set_progress(message="Exporting ticks", percent=10)
    exporter = MT5CsvExporter(settings, store, planner)
    result = exporter.export(instrument, start, end)
    rel_path = result.path.relative_to(settings.export_dir).as_posix()
    job.finish({
        "path": rel_path,
        "filename": result.path.name,
        "rows": result.rows,
        "hours_with_data": result.hours_with_data,
        "gaps": len(report.gap_hours),
        "gap_warning": not report.is_complete,
    })


def run_gaps_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: ParquetStorage,
) -> None:
    symbol = job.params["symbol"]
    repair = bool(job.params.get("repair", False))
    scan_all = bool(job.params.get("all", False))
    instrument = cat.get(symbol)
    scanner = GapScanner(settings, metadata)

    if scan_all:
        report = scanner.scan_all(instrument)
        if report is None:
            job.finish({"message": "No data recorded yet", "complete": True})
            return
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
    job.set_progress(
        message="Scan complete",
        percent=30 if repair and gap_count else 100,
        total_hours=report.total_hours,
        completed=report.completed,
        empty=report.empty,
        gap_count=gap_count,
        range=range_label,
    )

    if report.is_complete:
        job.finish({
            "complete": True,
            "range": range_label,
            "total_hours": report.total_hours,
            "completed": report.completed,
            "empty": report.empty,
            "gap_count": 0,
        })
        return

    if not repair:
        job.finish({
            "complete": False,
            "range": range_label,
            "total_hours": report.total_hours,
            "completed": report.completed,
            "empty": report.empty,
            "gap_count": gap_count,
            "gap_hours": [h.isoformat() for h in report.gap_hours[:20]],
        })
        return

    tasks = scanner.build_repair_tasks(instrument, report)
    engine = DownloadEngine(settings, store, metadata)

    def on_progress(snapshot: dict[str, Any]) -> None:
        job.set_progress(**snapshot, message="Repairing gaps")

    stats = engine.run(tasks, quiet=True, on_progress=on_progress)
    job.finish({
        "complete": stats.failed == 0,
        "range": range_label,
        "repaired": stats.completed,
        "empty": stats.empty,
        "still_failed": stats.failed,
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
