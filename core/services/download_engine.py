"""Concurrent download engine.

Each hour: fetch JSON from JETTA -> decode -> verify -> persist. The API is not
rate-limited like the legacy datafeed, so concurrency is bounded only by
max_workers and local I/O.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

import requests

from config.settings import Settings
from core.exceptions import JobCancelled
from core.models.task import HourTask, TaskProfile, TaskStatus
from core.services.decoder import DecodeError, decode_jetta_table
from core.services.http_client import (
    loads_json,
    session_initializer,
    worker_session,
)
from core.services.progress import ProgressBar
from core.services.retry_manager import PermanentError, RetryableError, RetryManager
from core.services.verification import verify_table
from storage.metadata_db import MetadataDB
from storage.parquet_storage import ParquetStorage

_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
_PROFILE_RECENT_LIMIT = 80


def _profile_entry(task: HourTask) -> dict:
    profile = task.profile or TaskProfile()
    return {
        "symbol": task.instrument.symbol,
        "hour": task.hour.strftime("%Y-%m-%d %H:00"),
        "status": task.status.value,
        "ticks": task.tick_count,
        **profile.as_dict(),
    }


def _profile_summary(entries: list[dict]) -> dict | None:
    active = [entry for entry in entries if not entry.get("skipped")]
    if not active:
        return None
    count = len(active)

    def avg(key: str) -> float:
        return round(sum(entry[key] for entry in active) / count, 1)

    return {
        "fetch_ms": avg("fetch_ms"),
        "decode_ms": avg("decode_ms"),
        "write_ms": avg("write_ms"),
        "total_ms": avg("total_ms"),
        "samples": count,
    }


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
        self._should_cancel: Callable[[], bool] | None = None
        self._refetch = False
        self._profile = False

    def _fetch_body(self, url: str) -> tuple[bytes | None, float]:
        started = time.perf_counter()
        try:
            response = worker_session(self.settings).get(
                url, timeout=self.settings.request_timeout,
            )
        except requests.RequestException as exc:
            raise RetryableError(f"network error: {exc}") from exc
        fetch_ms = (time.perf_counter() - started) * 1000

        status = response.status_code
        if status == 200:
            return response.content, fetch_ms
        if status == 404:
            return None, fetch_ms
        if status in _RETRYABLE_HTTP:
            raise RetryableError(f"HTTP {status}")
        raise PermanentError(f"HTTP {status}")

    def _fetch_decode_verify(self, task: HourTask) -> tuple[object | None, TaskProfile]:
        profile = TaskProfile()
        url = task.tick_url(self.settings.base_url)
        body, profile.fetch_ms = self._fetch_body(url)
        if body is None:
            return None, profile

        decode_started = time.perf_counter()
        try:
            payload = loads_json(body)
            table = decode_jetta_table(
                payload,
                task.hour_start_ms,
                task.hour_end_ms,
            )
        except DecodeError as exc:
            raise RetryableError(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise RetryableError(f"invalid JSON from {url}: {exc}") from exc

        if table.num_rows == 0:
            profile.decode_ms = (time.perf_counter() - decode_started) * 1000
            return None, profile

        check = verify_table(table, task.hour_start_ms)
        profile.decode_ms = (time.perf_counter() - decode_started) * 1000
        if not check.ok:
            raise RetryableError(f"verification failed: {check.reason}")
        return table, profile

    def _process(self, task: HourTask) -> HourTask:
        if self._should_cancel and self._should_cancel():
            raise JobCancelled()
        instrument, hour = task.instrument, task.hour
        started = time.perf_counter()
        if not self._refetch and self.storage.has_hour(instrument, hour):
            self.db.mark(
                instrument.id, hour, TaskStatus.COMPLETED,
                file_path=str(self.storage.hour_path(instrument, hour)),
            )
            task.status = TaskStatus.COMPLETED
            if self._profile:
                task.profile = TaskProfile(
                    skipped=True,
                    total_ms=(time.perf_counter() - started) * 1000,
                )
            return task

        profile = TaskProfile()

        def attempt() -> object | None:
            nonlocal profile
            table, profile = self._fetch_decode_verify(task)
            return table

        table = self.retry.run(attempt)

        if table is None:
            self.db.mark(instrument.id, hour, TaskStatus.EMPTY)
            task.status = TaskStatus.EMPTY
            profile.total_ms = (time.perf_counter() - started) * 1000
            if self._profile:
                task.profile = profile
            return task

        write_started = time.perf_counter()
        tick_count = table.num_rows
        path = self.storage.write_hour_table(instrument, hour, table)
        self.db.mark(
            instrument.id, hour, TaskStatus.COMPLETED,
            tick_count=tick_count, file_path=str(path),
        )
        profile.write_ms = (time.perf_counter() - write_started) * 1000
        profile.total_ms = (time.perf_counter() - started) * 1000
        task.status = TaskStatus.COMPLETED
        task.tick_count = tick_count
        if self._profile:
            task.profile = profile
        return task

    def run(
        self,
        tasks: list[HourTask],
        quiet: bool = False,
        on_progress: Callable[[dict], None] | None = None,
        on_task_done: Callable[[HourTask], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        refetch: bool = False,
        profile: bool = False,
    ) -> DownloadStats:
        self._should_cancel = should_cancel
        self._refetch = refetch
        self._profile = profile
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
            self._refetch = False
            self._profile = False

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

        workers = self.settings.max_workers
        use_console = not quiet and on_progress is None
        progress = ProgressBar(total=len(tasks), label=label) if use_console else None
        started_at = time.monotonic()
        symbol_stats: dict[str, dict[str, int]] = {}
        profile_recent: list[dict] = []
        task_index = 0
        pending: dict[Future, HourTask] = {}

        def emit(task: HourTask | None = None) -> None:
            payload: dict = {
                "label": label,
                "total": len(tasks),
                "done": stats.completed + stats.empty + stats.failed,
                "completed": stats.completed,
                "empty": stats.empty,
                "failed": stats.failed,
                "ticks": stats.ticks,
                "percent": round(
                    100 * (stats.completed + stats.empty + stats.failed) / len(tasks), 1,
                ) if tasks else 100.0,
                "symbol": task.instrument.symbol if task else None,
                "symbols": symbol_stats,
                "workers": workers,
            }
            elapsed = time.monotonic() - started_at
            rate = payload["done"] / elapsed if elapsed > 0 else 0.0
            payload["rate"] = round(rate, 2)
            payload["eta_seconds"] = int((len(tasks) - payload["done"]) / rate) if rate > 0 else 0
            if self._profile:
                payload["profile"] = True
                payload["profile_recent"] = list(profile_recent)
                payload["profile_summary"] = _profile_summary(profile_recent)
            if on_progress:
                on_progress(payload)

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
            if self._profile:
                profile_recent.append(_profile_entry(task))
                if len(profile_recent) > _PROFILE_RECENT_LIMIT:
                    del profile_recent[:-_PROFILE_RECENT_LIMIT]
            emit(task)
            if on_task_done:
                on_task_done(task)

        emit()
        with ThreadPoolExecutor(
            max_workers=workers,
            initializer=session_initializer(self.settings),
        ) as pool:
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
