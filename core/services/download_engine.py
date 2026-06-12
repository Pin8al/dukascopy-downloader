"""Concurrent download engine.

Each hour: fetch -> decode -> verify -> persist. A tuned concurrency limiter
keeps HTTP pressure in Dukascopy's sweet spot (bursting to 48+ causes 503
storms and *slower* effective throughput than ~12–16 workers).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter

from config.settings import Settings
from core.exceptions import JobCancelled
from core.models.task import HourTask, TaskStatus
from core.services.adaptive_throttle import AdaptiveThrottle
from core.services.decoder import DecodeError, decode_bi5_table
from core.services.progress import ProgressBar
from core.services.retry_manager import PermanentError, RetryableError, RetryManager
from core.services.verification import verify_table
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
            fast=True,
        )
        self._thread_local = threading.local()
        self._should_cancel: Callable[[], bool] | None = None
        self._throttle: AdaptiveThrottle | None = None
        self._refetch = False

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = self.settings.user_agent
            pool = max(self.settings.max_workers, 16)
            adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._thread_local.session = session
        return session

    def _fetch(self, url: str) -> bytes | None:
        throttle = self._throttle
        if throttle is not None:
            throttle.acquire(self._should_cancel)
        start = time.monotonic()
        try:
            response = self._session().get(url, timeout=self.settings.request_timeout)
        except requests.RequestException as exc:
            raise RetryableError(f"network error: {exc}") from exc
        finally:
            if throttle is not None:
                throttle.release()

        elapsed = time.monotonic() - start
        status = response.status_code
        if throttle is not None:
            throttle.record_fetch(status, elapsed)

        if status == 200:
            return response.content
        if status == 404:
            return None
        if status in _RETRYABLE_HTTP:
            raise RetryableError(f"HTTP {status}")
        raise PermanentError(f"HTTP {status}")

    def _fetch_decode_verify(self, task: HourTask):
        raw = self._fetch(task.url(self.settings.base_url))
        if raw is None or len(raw) == 0:
            return None
        try:
            table = decode_bi5_table(raw, task.hour_start_ms, task.instrument.decimal_factor)
        except DecodeError as exc:
            raise RetryableError(str(exc)) from exc
        check = verify_table(table, task.hour_start_ms)
        if not check.ok:
            raise RetryableError(f"verification failed: {check.reason}")
        return table

    def _process(self, task: HourTask) -> HourTask:
        if self._should_cancel and self._should_cancel():
            raise JobCancelled()
        instrument, hour = task.instrument, task.hour
        if not self._refetch and self.storage.has_hour(instrument, hour):
            self.db.mark(
                instrument.id, hour, TaskStatus.COMPLETED,
                file_path=str(self.storage.hour_path(instrument, hour)),
            )
            task.status = TaskStatus.COMPLETED
            return task

        table = self.retry.run(lambda: self._fetch_decode_verify(task))

        if table is None:
            self.db.mark(instrument.id, hour, TaskStatus.EMPTY)
            task.status = TaskStatus.EMPTY
            return task

        tick_count = table.num_rows
        path = self.storage.write_hour_table(instrument, hour, table)
        self.db.mark(
            instrument.id, hour, TaskStatus.COMPLETED,
            tick_count=tick_count, file_path=str(path),
        )
        task.status = TaskStatus.COMPLETED
        task.tick_count = tick_count
        return task

    def run(
        self,
        tasks: list[HourTask],
        quiet: bool = False,
        on_progress: Callable[[dict], None] | None = None,
        on_task_done: Callable[[HourTask], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        refetch: bool = False,
    ) -> DownloadStats:
        self._should_cancel = should_cancel
        self._refetch = refetch
        self._throttle = AdaptiveThrottle(
            self.settings.max_workers,
            self.settings.throttle_state_path,
            initial=self.settings.initial_concurrency,
            enabled=self.settings.adaptive_throttle,
        )
        try:
            stats = self._run_pass(
                tasks, label="download", quiet=quiet,
                on_progress=on_progress, on_task_done=on_task_done,
            )
            if should_cancel and should_cancel():
                return stats
            for round_number in range(1, self.settings.retry_rounds + 1):
                if not stats.failed_tasks:
                    break
                retry_tasks = stats.failed_tasks
                stats.failed_tasks = []
                stats.failed = 0
                time.sleep(min(15.0, 3.0 * round_number))
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
        finally:
            self.db.flush()
            if self._throttle is not None:
                self._throttle.save()
                self._throttle = None
            self._refetch = False

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
        throttle = self._throttle
        assert throttle is not None

        workers = self.settings.max_workers
        use_console = not quiet and on_progress is None and on_task_done is None
        progress = ProgressBar(total=len(tasks), label=label) if use_console else None
        started_at = time.monotonic()
        symbol_stats: dict[str, dict[str, int]] = {}
        task_index = 0
        pending: dict[Future, HourTask] = {}

        def emit(task: HourTask | None = None) -> None:
            snap = throttle.snapshot()
            if progress:
                progress.set_throttle(snap.as_dict())
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
                "throttle": snap.as_dict(),
            })

        def submit_until_full(pool: ThreadPoolExecutor) -> None:
            nonlocal task_index
            while task_index < len(tasks) and len(pending) < workers:
                task = tasks[task_index]
                task_index += 1
                pending[pool.submit(self._process, task)] = task

        def record_finished(task: HourTask) -> None:
            sym = task.instrument.symbol
            bucket = symbol_stats.setdefault(
                sym, {"completed": 0, "empty": 0, "failed": 0, "ticks": 0},
            )
            if task.status is TaskStatus.COMPLETED:
                stats.completed += 1
                stats.ticks += task.tick_count
                bucket["completed"] += 1
                bucket["ticks"] += task.tick_count
                if progress:
                    progress.update(completed=1, ticks=task.tick_count)
            elif task.status is TaskStatus.EMPTY:
                stats.empty += 1
                bucket["empty"] += 1
                if progress:
                    progress.update(empty=1)
            else:
                stats.failed += 1
                stats.failed_tasks.append(task)
                bucket["failed"] += 1
                if progress:
                    progress.update(failed=1)
            emit(task)
            if on_task_done:
                on_task_done(task)

        emit()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            submit_until_full(pool)
            try:
                while pending:
                    if self._should_cancel and self._should_cancel():
                        for future in pending:
                            future.cancel()
                        raise JobCancelled()
                    done, _ = wait(
                        pending, timeout=0.1, return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        continue
                    for future in done:
                        task = pending.pop(future)
                        try:
                            record_finished(future.result())
                        except JobCancelled:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            task.status = TaskStatus.FAILED
                            task.error = str(exc)
                            self.db.mark(
                                task.instrument.id, task.hour, TaskStatus.FAILED,
                                error=task.error,
                            )
                            record_finished(task)
                    submit_until_full(pool)
            except JobCancelled:
                for future in pending:
                    future.cancel()
                raise
            finally:
                self.db.flush()
                if progress:
                    progress.finish()
        return stats
