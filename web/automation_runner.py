"""Resolve and execute automation rules."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from core.services.instrument_search import InstrumentCatalog, UnknownInstrumentError
from storage.metadata_db import MetadataDB
from web.settings_store import SettingsStore


def resolve_library_symbols(catalog: InstrumentCatalog, metadata: MetadataDB) -> list[str]:
    symbols: list[str] = []
    for instrument_id in metadata.list_instruments():
        try:
            symbols.append(catalog.get(instrument_id).symbol)
        except UnknownInstrumentError:
            symbols.append(instrument_id.upper())
    return symbols


def resolve_automation_dates(action: dict[str, Any], today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now().date()
    start_off = int(action.get("days_ago_start", 2))
    end_off = int(action.get("days_ago_end", 2))
    if end_off > start_off:
        start_off, end_off = end_off, start_off
    start = today - timedelta(days=start_off)
    end = today - timedelta(days=end_off)
    return start, end


def describe_automation_dates(action: dict[str, Any], today: date | None = None) -> str:
    start, end = resolve_automation_dates(action, today)
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} → {end.isoformat()}"


def resolve_automation_symbols(
    action: dict[str, Any],
    catalog: InstrumentCatalog,
    metadata: MetadataDB,
) -> list[str]:
    if action.get("symbols_source") == "library":
        return resolve_library_symbols(catalog, metadata)
    symbols = [s.strip() for s in action.get("symbols", []) if s.strip()]
    valid: list[str] = []
    for symbol in symbols:
        catalog.get(symbol)
        valid.append(symbol)
    return valid


def build_download_params(rule: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    action = rule["action"]
    start, end = resolve_automation_dates(action, today)
    return {
        "symbols": [],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "workers": action.get("workers"),
        "force": action.get("force", False),
        "profile": action.get("profile", False),
        "automation_id": rule["id"],
        "automation_name": rule.get("name"),
    }


def run_automation_rule(
    rule: dict[str, Any],
    *,
    catalog: InstrumentCatalog,
    metadata: MetadataDB,
    submit_download: Callable[[dict[str, Any]], str],
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now().date()
    action = rule["action"]
    if action.get("type") != "download":
        raise ValueError(f"unsupported action type: {action.get('type')}")

    symbols = resolve_automation_symbols(action, catalog, metadata)
    if not symbols:
        return {
            "skipped": True,
            "reason": "no symbols (library empty or list empty)",
            "date_range": describe_automation_dates(action, today),
        }

    params = build_download_params(rule, today)
    params["symbols"] = symbols
    job_id = submit_download(params)
    return {
        "skipped": False,
        "job_id": job_id,
        "symbols": symbols,
        "date_range": f"{params['start']} → {params['end']}",
        "symbol_count": len(symbols),
    }
