"""Concurrent download engine.

Each hour task is fully independent: fetch -> decode -> verify -> persist.
Decode and verification failures are treated as retryable (the payload may
simply have been corrupted in transit). Task failures never abort the run;
they are recorded in the ledger and retried in additional rounds, and
whatever still fails is picked up later by the gap scanner.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests

from config.settings import Settings
from core.exceptions import JobCancelled
from core.models.task import HourTask, TaskStatus
from core.services.decoder import DecodeError, decode_bi5
from core.services.progress import ProgressBar
from core.services.retry_manager import PermanentError, RetryableError, RetryManager
from core.services.verification import verify_ticks
from storage.metadata_db import MetadataDB
from storage.parquet_storage import ParquetStorage

_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class DownloadStats:
    completed: int = 0
    empty: int = 0
    failed: int = 0
    ticks: int = 0
    failed_tasks: list[HourTask] = field(default_factory=list)

    def merge(self, other: "DownloadStats") -> None:
        self.completed += other.completed
        self.empty += other.empty
        self.failed += other.failed
        self.ticks += other.ticks
        self.failed_tasks.extend(other.failed_tasks)


class DownloadEngine:
    def __init__(self, settings: Settings, storage: ParquetStorage, db: MetadataDB):
        self.settings = settings
        self.storage = storage
        self.db = db
        self.retry = RetryManager(
            settings.max_attempts,
            settings.backoff_base_seconds,
            settings.backoff_max_seconds,
        )
        self._thread_local = threading.local()
        self._should_cancel: Callable[[], bool] | None = None

    # -- HTTP ----------------------------------------------------------------

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = self.settings.user_agent
            self._thread_local.session = session
        return session

    def _fetch(self, url: str) -> bytes | None:
        """Return payload bytes, or None for a valid-but-empty hour (404)."""
        try:
            response = self._session().get(url, timeout=self.settings.request_timeout)
        except requests.RequestException as exc:
            raise RetryableError(f"network error: {exc}") from exc

        if response.status_code == 200:
            return response.content
        if response.status_code == 404:
            return None
        if response.status_code in _RETRYABLE_HTTP:
            raise RetryableError(f"HTTP {response.status_code}")
        raise PermanentError(f"HTTP {response.status_code}")

    # -- per-task pipeline -----------------------------------------------------

    def _fetch_decode_verify(self, task: HourTask):
        """One retryable unit: a corrupt payload triggers a fresh fetch."""
        raw = self._fetch(task.url(self.settings.base_url))
        if raw is None or len(raw) == 0:
            return None
        try:
            ticks = decode_bi5(raw, task.hour_start_ms, task.instrument.decimal_factor)
        except DecodeError as exc:
            raise RetryableError(str(exc)) from exc
        check = verify_ticks(ticks, task.hour_start_ms)
        if not check.ok:
            raise RetryableError(f"verification failed: {check.reason}")
        return ticks

    def _process(self, task: HourTask) -> HourTask:
        if self._should_cancel and self._should_cancel():
            raise JobCancelled()
        instrument, hour = task.instrument, task.hour
        if self.storage.has_hour(instrument, hour):
            # File already on disk (e.g. ledger was deleted): trust it.
            self.db.mark(instrument.id, hour, TaskStatus.COMPLETED,
                         file_path=str(self.storage.hour_path(instrument, hour)))
            task.status = TaskStatus.COMPLETED
            return task

        ticks = self.retry.run(lambda: self._fetch_decode_verify(task))

        if not ticks:
            self.db.mark(instrument.id, hour, TaskStatus.EMPTY)
            task.status = TaskStatus.EMPTY
            return task

        path = self.storage.write_hour(instrument, hour, ticks)
        self.db.mark(instrument.id, hour, TaskStatus.COMPLETED,
                     tick_count=len(ticks), file_path=str(path))
        task.status = TaskStatus.COMPLETED
        task.tick_count = len(ticks)
        return task

    # -- run --------------------------------------------------------------------

    def run(
        self,
        tasks: list[HourTask],
        quiet: bool = False,
        on_progress: Callable[[dict], None] | None = None,
        on_task_done: Callable[[HourTask], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DownloadStats:
        self._should_cancel = should_cancel
        stats = self._run_pass(
            tasks,
            label="download",
            quiet=quiet,
            on_progress=on_progress,
            on_task_done=on_task_done,
        )
        if should_cancel and should_cancel():
            return stats
        for round_number in range(1, self.settings.retry_rounds + 1):
            if not stats.failed_tasks:
                break
            retry_tasks = stats.failed_tasks
            stats.failed_tasks = []
            stats.failed = 0
            time.sleep(min(30.0, 5.0 * round_number))
            if not quiet:
                print(f"Retry round {round_number}: {len(retry_tasks)} failed hour(s)")
            retry_stats = self._run_pass(
                retry_tasks,
                label=f"retry {round_number}",
                quiet=quiet,
                on_progress=on_progress,
                on_task_done=on_task_done,
            )
            stats.merge(retry_stats)
        return stats

    def _run_pass(
        self,
        tasks: list[HourTask],
        label: str,
        quiet: bool,
        on_progress: Callable[[dict], None] | None = None,
        on_task_done: Callable[[HourTask], None] | None = None,
    ) -> DownloadStats:
        stats = DownloadStats()
        if not tasks:
            return stats
        use_console = not quiet and on_progress is None and on_task_done is None
        progress = ProgressBar(total=len(tasks), label=label) if use_console else None
        started_at = time.monotonic()
        symbol_stats: dict[str, dict[str, int]] = {}

        def emit(task: HourTask | None = None) -> None:
            if not on_progress:
                return
            done = stats.completed + stats.empty + stats.failed
            elapsed = time.monotonic() - started_at
            rate = done / elapsed if elapsed > 0 else 0.0
            on_progress({
                "label": label,
                "total": len(tasks),
                "done": done,
                "completed": stats.completed,
                "empty": stats.empty,
                "failed": stats.failed,
                "ticks": stats.ticks,
                "percent": round(100 * done / len(tasks), 1) if tasks else 100.0,
                "rate": round(rate, 2),
                "eta_seconds": int((len(tasks) - done) / rate) if rate > 0 else 0,
                "symbol": task.instrument.symbol if task else None,
                "symbols": symbol_stats,
            })

        with ThreadPoolExecutor(max_workers=self.settings.max_workers) as pool:
            futures = {pool.submit(self._process, task): task for task in tasks}
            try:
                for future in as_completed(futures):
                    if self._should_cancel and self._should_cancel():
                        for pending in futures:
                            pending.cancel()
                        raise JobCancelled()
                    task = futures[future]
                    try:
                        finished = future.result()
                        sym = finished.instrument.symbol
                        bucket = symbol_stats.setdefault(
                            sym, {"completed": 0, "empty": 0, "failed": 0, "ticks": 0},
                        )
                        if finished.status is TaskStatus.COMPLETED:
                            stats.completed += 1
                            stats.ticks += finished.tick_count
                            bucket["completed"] += 1
                            bucket["ticks"] += finished.tick_count
                            if progress:
                                progress.update(completed=1, ticks=finished.tick_count)
                        else:
                            stats.empty += 1
                            bucket["empty"] += 1
                            if progress:
                                progress.update(empty=1)
                        emit(finished)
                        if on_task_done:
                            on_task_done(finished)
                    except JobCancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001 - record, never abort the run
                        task.status = TaskStatus.FAILED
                        task.error = str(exc)
                        self.db.mark(task.instrument.id, task.hour, TaskStatus.FAILED,
                                     error=task.error)
                        stats.failed += 1
                        stats.failed_tasks.append(task)
                        bucket = symbol_stats.setdefault(
                            task.instrument.symbol,
                            {"completed": 0, "empty": 0, "failed": 0, "ticks": 0},
                        )
                        bucket["failed"] += 1
                        if progress:
                            progress.update(failed=1)
                        emit(task)
                        if on_task_done:
                            on_task_done(task)
            finally:
                if progress:
                    progress.finish()
        return stats
