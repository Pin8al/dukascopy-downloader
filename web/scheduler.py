"""Background scheduler for daily automation rules."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable

from web.automation_runner import run_automation_rule
from web.settings_store import SettingsStore

logger = logging.getLogger(__name__)


class AutomationScheduler:
    def __init__(
        self,
        store: SettingsStore,
        *,
        submit_download: Callable[[dict[str, Any]], str],
        get_catalog,
        get_db,
        tick_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.submit_download = submit_download
        self.get_catalog = get_catalog
        self.get_db = get_db
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="automation-scheduler", daemon=True)
        self._thread.start()
        logger.info("Automation scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("automation scheduler tick failed")
            self._stop.wait(self.tick_seconds)

    def _tick(self) -> None:
        now = datetime.now()
        today_key = now.date().isoformat()
        for rule in self.store.list_automations():
            if not rule.get("enabled"):
                continue
            if not self._is_due(rule, now):
                continue
            if rule.get("last_run_date") == today_key:
                continue
            self._execute(rule, today_key)

    def _is_due(self, rule: dict, now: datetime) -> bool:
        time_str = rule.get("schedule", {}).get("time", "00:00")
        try:
            hour, minute = map(int, time_str.split(":")[:2])
        except ValueError:
            return False
        return now.hour == hour and now.minute == minute

    def _execute(self, rule: dict, today_key: str) -> None:
        logger.info("Running automation %s (%s)", rule.get("name"), rule.get("id"))
        try:
            result = run_automation_rule(
                rule,
                catalog=self.get_catalog(),
                metadata=self.get_db(),
                submit_download=self.submit_download,
            )
            job_id = result.get("job_id")
            self.store.mark_automation_run(rule["id"], job_id, today_key)
            if result.get("skipped"):
                logger.info("Automation %s skipped: %s", rule.get("name"), result.get("reason"))
            else:
                logger.info(
                    "Automation %s started job %s for %s symbols (%s)",
                    rule.get("name"),
                    job_id,
                    result.get("symbol_count"),
                    result.get("date_range"),
                )
        except Exception:
            logger.exception("Automation %s failed", rule.get("name"))
            self.store.mark_automation_run(rule["id"], None, today_key)

    def run_now(self, rule_id: str) -> dict:
        rule = self.store.get_automation(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        today_key = datetime.now().date().isoformat()
        result = run_automation_rule(
            rule,
            catalog=self.get_catalog(),
            metadata=self.get_db(),
            submit_download=self.submit_download,
        )
        self.store.mark_automation_run(rule_id, result.get("job_id"), today_key)
        return result


_scheduler: AutomationScheduler | None = None


def get_scheduler() -> AutomationScheduler | None:
    return _scheduler


def init_scheduler(
    store: SettingsStore,
    *,
    submit_download: Callable,
    get_catalog,
    get_db,
) -> AutomationScheduler:
    global _scheduler
    _scheduler = AutomationScheduler(
        store,
        submit_download=submit_download,
        get_catalog=get_catalog,
        get_db=get_db,
    )
    return _scheduler
