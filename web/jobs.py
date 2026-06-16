"""Background job runner with live progress for the web UI."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from collections.abc import Callable
from typing import Any

from config.settings import Settings
from core.exceptions import JobCancelled
from core.services.download_engine import DownloadEngine
from core.exceptions import IncompleteDatasetError
from core.services.gap_scanner import GapScanner, require_complete_import
from core.services.instrument_search import InstrumentCatalog, UnknownInstrumentError
from core.services.planner import Planner
from export.mt5_importer import abort_mt5_import, stage_and_import
from storage.metadata_db import MetadataDB
from storage.tick_storage import TickStorage


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
            if result:
                if "ticks_imported" in result:
                    self.progress["ticks_imported"] = result["ticks_imported"]
                if "ticks_total" in result:
                    self.progress["ticks_total"] = result["ticks_total"]
                if result.get("custom_symbol"):
                    self.progress["custom_symbol"] = result["custom_symbol"]

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

    def cancel(self, job_id: str, mt5_config: dict[str, Any] | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None or not job.request_cancel():
            return None
        if job.kind == "mt5_import":
            abort_mt5_import(job_id, mt5_config)
        return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                return False
            del self._jobs[job_id]
            return True

    def has_active_mt5_import(self) -> bool:
        with self._lock:
            for job in self._jobs.values():
                if job.kind != "mt5_import":
                    continue
                if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                    continue
                if job.is_cancelled():
                    continue
                return True
        return False


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _import_span_ms(span_start: date, span_end: date) -> tuple[int, int]:
    first = datetime(span_start.year, span_start.month, span_start.day, tzinfo=timezone.utc)
    last = datetime(span_end.year, span_end.month, span_end.day, 23, 59, 59, tzinfo=timezone.utc)
    return int(first.timestamp() * 1000), int(last.timestamp() * 1000)


def run_download_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: TickStorage,
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
    symbol_count = len(symbols)
    job.set_progress(message="Planning download…", phase="plan", percent=0)

    for index, symbol in enumerate(symbols):
        instrument = cat.get(symbol)
        job.set_progress(
            message=f"Planning {instrument.symbol}…",
            phase="plan",
            percent=round(100 * index / symbol_count, 1) if symbol_count else 0,
            symbol=instrument.symbol,
        )
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
        phase="download",
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
        sym = snapshot.get("symbol") or ""
        job.set_progress(
            **snapshot,
            phase="download",
            message=f"Downloading {sym}".strip() or "Downloading…",
        )

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


def run_mt5_import_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: TickStorage,
    mt5_config: dict[str, Any],
) -> None:
    symbol = job.params["symbol"]
    import_all = bool(job.params.get("import_all", False))
    instrument = cat.get(symbol)
    planner = Planner(settings, metadata)
    cancel = job.is_cancelled
    progress = lambda snapshot: job.set_progress(**snapshot)

    job.set_progress(message="Preparing import…", percent=0, phase="prepare")

    span_start: date | None = None
    span_end: date | None = None
    range_label = str(job.params.get("range_label") or "")

    if job.params.get("gap_checked") and range_label:
        if import_all:
            span = metadata.recorded_span(instrument.id)
            if span is None:
                job.finish({"message": "No data recorded yet", "ticks_imported": 0})
                return
            span_start = span[0].date()
            span_end = span[1].date()
        else:
            span_start = _parse_date(job.params["start"])
            span_end = _parse_date(job.params["end"])
    else:
        scanner = GapScanner(settings, metadata)
        if import_all:
            report, range_label = scanner.scan_for_import(instrument, import_all=True)
            if report is None:
                job.finish({"message": "No data recorded yet", "ticks_imported": 0})
                return
            try:
                require_complete_import(report, instrument.symbol, range_label)
            except IncompleteDatasetError as exc:
                raise ValueError(str(exc)) from exc
            span = metadata.recorded_span(instrument.id)
            if span is None:
                raise ValueError("No recorded span for instrument")
            span_start = span[0].date()
            span_end = span[1].date()
        else:
            span_start = _parse_date(job.params["start"])
            span_end = _parse_date(job.params["end"])
            if span_end < span_start:
                raise ValueError("end date is before start date")
            report, range_label = scanner.scan_for_import(
                instrument, start=span_start, end=span_end,
            )
            try:
                require_complete_import(report, instrument.symbol, range_label)
            except IncompleteDatasetError as exc:
                raise ValueError(str(exc)) from exc

    job.set_progress(message=f"Preparing import · {range_label}", percent=0, phase="prepare")

    result = stage_and_import(
        settings,
        store,
        planner,
        instrument,
        job_id=job.id,
        mt5_raw=mt5_config,
        import_all=import_all,
        start=span_start,
        end=span_end,
        range_label=range_label,
        metadata=metadata,
        on_progress=progress,
        should_cancel=cancel,
    )

    if job.is_cancelled():
        abort_mt5_import(job.id, mt5_config, settings=settings)
        raise JobCancelled()

    job.finish(result)

    custom = result.get("custom_symbol")
    if custom and span_start is not None and span_end is not None:
        first_ms, last_ms = _import_span_ms(span_start, span_end)
        metadata.upsert_mt5_custom_symbol(
            symbol=str(custom),
            source_symbol=symbol,
            ticks=int(result.get("ticks_imported", 0)),
            first_ms=first_ms,
            last_ms=last_ms,
            range_label=str(result.get("range") or ""),
            mark_imported=True,
        )


def run_gaps_job(
    job: Job,
    settings: Settings,
    cat: InstrumentCatalog,
    metadata: MetadataDB,
    store: TickStorage,
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
