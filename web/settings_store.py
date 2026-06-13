"""Persist UI settings and automation rules to disk."""
from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import Settings

DEFAULT_UI = {
    "theme": "light",
    "default_workers": 15,
}

DEFAULT_AUTOMATION_ACTION = {
    "type": "download",
    "symbols_source": "library",
    "symbols": [],
    "days_ago_start": 2,
    "days_ago_end": 2,
    "workers": 15,
    "force": False,
    "profile": False,
}


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"ui": dict(DEFAULT_UI), "automations": []}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw.get("ui"), dict):
                self._data["ui"] = {**DEFAULT_UI, **raw["ui"]}
            if isinstance(raw.get("automations"), list):
                self._data["automations"] = raw["automations"]
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get_ui(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["ui"])

    def set_ui(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data["ui"] = {**self._data["ui"], **patch}
            self._save()
            return dict(self._data["ui"])

    def list_automations(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._data["automations"])

    def get_automation(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            for rule in self._data["automations"]:
                if rule["id"] == rule_id:
                    return deepcopy(rule)
        return None

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self._normalize_automation(payload)
        rule["id"] = str(uuid.uuid4())
        rule["last_run_date"] = None
        rule["last_run_at"] = None
        rule["last_job_id"] = None
        with self._lock:
            self._data["automations"].append(rule)
            self._save()
        return deepcopy(rule)

    def update_automation(self, rule_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            for i, rule in enumerate(self._data["automations"]):
                if rule["id"] != rule_id:
                    continue
                updated = self._normalize_automation(payload, existing=rule)
                updated["id"] = rule_id
                updated["last_run_date"] = rule.get("last_run_date")
                updated["last_run_at"] = rule.get("last_run_at")
                updated["last_job_id"] = rule.get("last_job_id")
                self._data["automations"][i] = updated
                self._save()
                return deepcopy(updated)
        return None

    def delete_automation(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._data["automations"])
            self._data["automations"] = [
                r for r in self._data["automations"] if r["id"] != rule_id
            ]
            if len(self._data["automations"]) == before:
                return False
            self._save()
            return True

    def mark_automation_run(self, rule_id: str, job_id: str | None, run_date: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self._lock:
            for rule in self._data["automations"]:
                if rule["id"] == rule_id:
                    rule["last_run_date"] = run_date
                    rule["last_run_at"] = now
                    rule["last_job_id"] = job_id
                    self._save()
                    return

    def _normalize_automation(
        self, payload: dict[str, Any], existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = deepcopy(existing) if existing else {}
        schedule = payload.get("schedule") or base.get("schedule") or {}
        action = {**DEFAULT_AUTOMATION_ACTION, **(base.get("action") or {}), **(payload.get("action") or {})}
        return {
            "name": str(payload.get("name") or base.get("name") or "Automation").strip(),
            "enabled": bool(payload.get("enabled", base.get("enabled", True))),
            "schedule": {
                "type": "daily",
                "time": str(schedule.get("time") or payload.get("time") or "00:00"),
            },
            "action": {
                "type": "download",
                "symbols_source": action.get("symbols_source", "library"),
                "symbols": [s.strip() for s in action.get("symbols", []) if str(s).strip()],
                "days_ago_start": max(0, int(action.get("days_ago_start", 2))),
                "days_ago_end": max(0, int(action.get("days_ago_end", 2))),
                "workers": max(1, min(64, int(action.get("workers", 15)))),
                "force": bool(action.get("force", False)),
                "profile": bool(action.get("profile", False)),
            },
        }


_store: SettingsStore | None = None


def settings_store(cfg: Settings | None = None) -> SettingsStore:
    global _store
    if _store is None:
        from web.deps import settings as deps_settings

        cfg = cfg or deps_settings()
        path = cfg.data_dir / "web_settings.json"
        _store = SettingsStore(path)
    return _store
