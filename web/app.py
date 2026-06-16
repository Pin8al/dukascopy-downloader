"""FastAPI web UI for the Dukascopy downloader."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.exceptions import IncompleteDatasetError
from core.services.gap_scanner import GapScanner, require_complete_import
from core.services.instrument_search import UnknownInstrumentError
from core.services.jetta_metadata import fetch_instrument_info
from export.mt5_importer import kill_mt5_terminal, wait_mt5_exit
from export.mt5_symbol_manager import delete_custom_symbol, list_custom_symbols
from storage.fast_delete import release_import_staging_locks
from storage.metadata_db import MetadataDB
from web.automation_runner import describe_automation_dates
from web.deps import catalog, db, settings, storage
from web.jobs import (
    JobManager,
    resolve_symbol,
    run_download_job,
    run_gaps_job,
    run_mt5_import_job,
)
from web.scheduler import get_scheduler, init_scheduler
from web.settings_store import settings_store

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
jobs = JobManager()


def _submit_automation_download(params: dict[str, Any]) -> str:
    symbols = params.get("symbols") or []
    if not symbols:
        raise ValueError("no symbols for automation download")
    cfg = settings()
    job_params = {
        "symbols": symbols,
        "start": params["start"],
        "end": params["end"],
        "workers": params.get("workers"),
        "force": bool(params.get("force", False)),
        "profile": bool(params.get("profile", False)),
        "automation_id": params.get("automation_id"),
        "automation_name": params.get("automation_name"),
    }
    job = jobs.submit(
        "download",
        job_params,
        lambda j: run_download_job(j, cfg, catalog(), db(), storage()),
    )
    return job.id


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = settings_store()
    sched = init_scheduler(
        store,
        submit_download=_submit_automation_download,
        get_catalog=catalog,
        get_db=db,
    )
    sched.start()
    yield
    sched.stop()


app = FastAPI(title="Dukascopy Downloader", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -- request models -----------------------------------------------------------

class DownloadRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    start: str
    end: str
    workers: int | None = None
    force: bool = False
    profile: bool = False


class Mt5ImportRequest(BaseModel):
    symbol: str
    start: str | None = None
    end: str | None = None
    import_all: bool = False


class GapsRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    symbol: str | None = None
    start: str | None = None
    end: str | None = None
    all: bool = False
    repair: bool = False
    workers: int | None = None
    refetch_empty: bool = False


class UiSettingsUpdate(BaseModel):
    theme: str | None = None
    default_workers: int | None = Field(None, ge=1, le=64)


class Mt5SettingsUpdate(BaseModel):
    terminal_exe: str | None = None
    data_path: str | None = None
    custom_suffix: str | None = None
    origin_symbol: str | None = None


class AutomationScheduleModel(BaseModel):
    type: str = "daily"
    time: str = "00:00"


class AutomationActionModel(BaseModel):
    type: str = "download"
    symbols_source: str = "library"
    symbols: list[str] = Field(default_factory=list)
    days_ago_start: int = Field(2, ge=0, le=3650)
    days_ago_end: int = Field(2, ge=0, le=3650)
    workers: int = Field(15, ge=1, le=64)
    force: bool = False
    profile: bool = False


class AutomationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    schedule: AutomationScheduleModel = Field(default_factory=AutomationScheduleModel)
    action: AutomationActionModel = Field(default_factory=AutomationActionModel)


class AutomationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    enabled: bool | None = None
    schedule: AutomationScheduleModel | None = None
    action: AutomationActionModel | None = None


def _automation_public(rule: dict[str, Any]) -> dict[str, Any]:
    out = dict(rule)
    out["date_preview"] = describe_automation_dates(rule.get("action", {}))
    return out


# -- helpers ------------------------------------------------------------------

def _latest_downloadable_date(cfg) -> date:
    lag = timedelta(hours=cfg.min_data_lag_hours)
    return (datetime.now(timezone.utc) - lag).date()


def _instrument_availability(symbol: str) -> dict[str, Any]:
    cfg = settings()
    metadata = db()
    try:
        inst = catalog().get(symbol)
    except UnknownInstrumentError as exc:
        raise HTTPException(404, str(exc)) from exc

    jetta = fetch_instrument_info(
        inst.jetta_code,
        cfg.base_url,
        timeout=cfg.request_timeout,
    )
    catalog_earliest = (
        inst.earliest_tick_utc.date().isoformat() if inst.earliest_tick_utc else None
    )
    jetta_earliest = jetta.get("tick_from_date")
    effective_earliest = jetta_earliest or catalog_earliest
    if catalog_earliest and jetta_earliest:
        effective_earliest = max(catalog_earliest, jetta_earliest)

    span = metadata.recorded_span(inst.id)
    summary = metadata.summary(inst.id)
    latest = _latest_downloadable_date(cfg)

    return {
        "symbol": inst.symbol,
        "jetta_code": inst.jetta_code,
        "name": inst.name,
        "catalog_earliest_date": catalog_earliest,
        "jetta_earliest_date": jetta_earliest,
        "effective_earliest_date": effective_earliest,
        "latest_downloadable_date": latest.isoformat(),
        "jetta_error": jetta.get("error"),
        "stored": {
            "first_hour": summary["first_hour"],
            "last_hour": summary["last_hour"],
            "completed_hours": summary["by_status"].get("completed", {}).get("hours", 0),
            "empty_hours": summary["by_status"].get("empty", {}).get("hours", 0),
            "ticks": summary["by_status"].get("completed", {}).get("ticks", 0),
        }
        if span
        else None,
    }


def _instrument_payload(inst, metadata=None) -> dict[str, Any]:
    earliest = inst.earliest_tick_utc.date().isoformat() if inst.earliest_tick_utc else None
    payload = {
        "id": inst.id,
        "symbol": inst.symbol,
        "name": inst.name,
        "description": inst.description,
        "decimals": inst.price_decimals,
        "earliest": inst.earliest_tick_utc.isoformat() if inst.earliest_tick_utc else None,
        "earliest_date": earliest,
        "jetta_code": inst.jetta_code,
    }
    if metadata is not None:
        summary = metadata.summary(inst.id)
        first_hour = summary.get("first_hour")
        if first_hour:
            payload["stored_from_date"] = str(first_hour)[:10]
    return payload


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(400, f"invalid date '{value}', expected YYYY-MM-DD") from exc


def _gap_symbols(body: GapsRequest) -> list[str]:
    symbols = [s.strip() for s in body.symbols if s.strip()]
    if not symbols and body.symbol and body.symbol.strip():
        symbols = [body.symbol.strip()]
    if not symbols:
        raise HTTPException(400, "at least one symbol is required")
    return symbols


def _gap_report(body: GapsRequest, inst, scanner: GapScanner, metadata: MetadataDB):
    if body.all:
        report = scanner.scan_all(inst)
        if report is None:
            return None, None
        span = metadata.recorded_span(inst.id)
        range_label = f"{span[0]:%Y-%m-%d %H:%M} -> {span[1]:%Y-%m-%d %H:%M} UTC"
        return range_label, report
    if not body.start or not body.end:
        raise HTTPException(400, "start and end required")
    start, end = _parse_date(body.start), _parse_date(body.end)
    if end < start:
        raise HTTPException(400, "end date is before start date")
    report = scanner.scan(inst, start, end)
    return f"{body.start} -> {body.end}", report


def _gap_preview_item(symbol: str, range_label: str | None, report) -> dict[str, Any]:
    if report is None:
        return {"symbol": symbol, "message": "No data recorded yet", "complete": True}
    return {
        "symbol": symbol,
        "range": range_label,
        "total_hours": report.total_hours,
        "completed": report.completed,
        "empty": report.empty,
        "failed": len(report.failed_hours),
        "missing": report.missing_count,
        "gap_count": report.missing_count + len(report.failed_hours),
        "complete": report.is_complete,
        "gap_hours": [h.strftime("%Y-%m-%d %H:00") for h in report.gap_hours[:20]],
    }


# -- pages --------------------------------------------------------------------

@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    path = STATIC_DIR / "favicon.ico"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/x-icon")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# -- instruments --------------------------------------------------------------

@app.get("/api/instruments/search")
async def search_instruments(q: str = Query("", min_length=0), limit: int = 30) -> dict[str, Any]:
    if not q.strip():
        return {"results": []}
    results = catalog().search(q)[:limit]
    metadata = db()
    return {"results": [_instrument_payload(i, metadata) for i in results]}


@app.get("/api/library/search")
async def search_library(q: str = Query("", min_length=0), limit: int = 30) -> dict[str, Any]:
    """Search instruments that have stored tick data (library only)."""
    needle = q.strip().upper()
    if not needle:
        return {"results": []}
    metadata = db()
    matches: list[tuple[int, dict[str, Any]]] = []
    for instrument_id in metadata.list_instruments():
        if metadata.recorded_span(instrument_id) is None:
            continue
        try:
            inst = catalog().get(instrument_id)
        except UnknownInstrumentError:
            continue
        sym = inst.symbol.upper()
        name = (inst.name or "").upper()
        if needle not in sym and needle not in name:
            continue
        rank = 0 if sym.startswith(needle) else (1 if needle in sym else 2)
        matches.append((rank, _instrument_payload(inst, metadata)))
    matches.sort(key=lambda item: (item[0], item[1]["symbol"]))
    return {"results": [payload for _, payload in matches[:limit]]}


def _require_library_symbol(symbol: str, metadata: MetadataDB):
    try:
        inst = catalog().get(symbol)
    except UnknownInstrumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    if metadata.recorded_span(inst.id) is None:
        raise HTTPException(
            400,
            f"{inst.symbol} is not in your library — download it first",
        )
    return inst


@app.get("/api/instruments/{symbol}")
async def get_instrument(symbol: str) -> dict[str, Any]:
    try:
        return _instrument_availability(symbol)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/instruments/{symbol}/availability")
async def instrument_availability(symbol: str) -> dict[str, Any]:
    return _instrument_availability(symbol)


# -- status -------------------------------------------------------------------

@app.get("/api/status")
async def list_status() -> dict[str, Any]:
    metadata = db()
    items = []
    for instrument_id in metadata.list_instruments():
        try:
            inst = catalog().get(instrument_id)
        except UnknownInstrumentError:
            inst = None
        summary = metadata.summary(instrument_id)
        items.append({
            "id": instrument_id,
            "symbol": inst.symbol if inst else instrument_id.upper(),
            "name": inst.name if inst else instrument_id.upper(),
            "first_hour": summary["first_hour"],
            "last_hour": summary["last_hour"],
            "by_status": summary["by_status"],
        })
    return {"instruments": items}


@app.delete("/api/status/{symbol}")
async def delete_instrument_data(symbol: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    metadata = db()
    store = storage()

    try:
        inst = catalog().get(symbol)
        instrument_id = inst.id
        symbol_dir = inst.symbol
    except UnknownInstrumentError:
        instrument_id = symbol.strip().lower()
        symbol_dir = symbol.strip().upper()
        if instrument_id not in metadata.list_instruments():
            raise HTTPException(404, f"No stored data for {symbol}") from None

    if instrument_id not in metadata.list_instruments():
        raise HTTPException(404, f"No stored data for {symbol}")

    if jobs.has_active_mt5_import():
        raise HTTPException(409, "Wait for the running MT5 import to finish before deleting library data")

    mt5_config = settings_store().get_mt5()
    terminal_exe = str(mt5_config.get("terminal_exe") or "").strip()
    if terminal_exe:
        terminal_path = Path(terminal_exe)
        kill_mt5_terminal(terminal_path)
        wait_mt5_exit(terminal_path)
    release_import_staging_locks()

    try:
        trash_path = store.queue_delete_symbol(symbol_dir)
    except OSError as exc:
        logger.exception("Failed to queue delete for %s", symbol_dir)
        raise HTTPException(
            500,
            f"Could not delete {symbol_dir}: {exc}. Close MetaTrader 5 if it is open, then retry.",
        ) from exc

    deleted_hours = metadata.delete_instrument(instrument_id)
    if trash_path is not None:
        background_tasks.add_task(store.finish_delete, trash_path)

    return {
        "symbol": symbol_dir,
        "instrument_id": instrument_id,
        "deleted_hours": deleted_hours,
    }


@app.get("/api/status/{symbol}")
async def instrument_status(symbol: str) -> dict[str, Any]:
    try:
        inst = catalog().get(symbol)
    except UnknownInstrumentError as exc:
        raise HTTPException(404, str(exc)) from exc
    summary = db().summary(inst.id)
    span = db().recorded_span(inst.id)
    return {
        "instrument": _instrument_payload(inst),
        "summary": summary,
        "span": {
            "start": span[0].isoformat() if span else None,
            "end": span[1].isoformat() if span else None,
        },
    }


# -- jobs ---------------------------------------------------------------------

@app.get("/api/jobs")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": jobs.list_jobs()}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    mt5_config = None
    if job is not None and job.kind == "mt5_import":
        mt5_config = settings_store().get_mt5()
    job = jobs.cancel(job_id, mt5_config=mt5_config)
    if job is None:
        raise HTTPException(404, "job not found or already finished")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
async def dismiss_job(job_id: str) -> dict[str, Any]:
    if not jobs.remove(job_id):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        raise HTTPException(409, "cannot remove a running job")
    return {"ok": True, "id": job_id}


@app.post("/api/download")
async def start_download(body: DownloadRequest) -> dict[str, Any]:
    symbols = [s.strip() for s in body.symbols if s.strip()]
    if not symbols:
        raise HTTPException(400, "at least one symbol is required")
    for symbol in symbols:
        try:
            catalog().get(symbol)
        except UnknownInstrumentError as exc:
            raise HTTPException(400, str(exc)) from exc

    cfg = settings()
    job = jobs.submit(
        "download",
        body.model_dump(),
        lambda j: run_download_job(j, cfg, catalog(), db(), storage()),
    )
    return job.to_dict()


@app.post("/api/mt5/import")
async def start_mt5_import(body: Mt5ImportRequest) -> dict[str, Any]:
    if body.import_all and (body.start or body.end):
        raise HTTPException(400, "import_all cannot be combined with start/end dates")
    if not body.import_all and (not body.start or not body.end):
        raise HTTPException(400, "start and end dates are required unless import_all=true")

    if jobs.has_active_mt5_import():
        raise HTTPException(409, "An MT5 import is already running")

    store = settings_store()
    mt5_config = store.get_mt5()
    if not str(mt5_config.get("terminal_exe") or "").strip():
        raise HTTPException(400, "Configure MetaTrader 5 path in Settings before importing")

    metadata = db()
    inst = _require_library_symbol(body.symbol, metadata)

    cfg = settings()
    scanner = GapScanner(cfg, metadata)
    if body.import_all:
        report, range_label = scanner.scan_for_import(inst, import_all=True)
    else:
        start, end = _parse_date(body.start), _parse_date(body.end)
        if end < start:
            raise HTTPException(400, "end date is before start date")
        report, range_label = scanner.scan_for_import(inst, start=start, end=end)
    try:
        require_complete_import(report, inst.symbol, range_label)
    except IncompleteDatasetError as exc:
        raise HTTPException(400, str(exc)) from exc

    job_params = body.model_dump()
    job_params["range_label"] = range_label
    job_params["gap_checked"] = True

    job = jobs.submit(
        "mt5_import",
        job_params,
        lambda j: run_mt5_import_job(j, cfg, catalog(), metadata, storage(), mt5_config),
    )
    return job.to_dict()


@app.get("/api/mt5/custom-symbols")
def api_list_mt5_custom_symbols() -> dict[str, Any]:
    return {"symbols": db().list_mt5_custom_symbols()}


def _guess_source_symbol(custom_name: str, suffix: str) -> str:
    suffix = suffix.strip()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    if suffix and custom_name.endswith(suffix):
        return custom_name[: -len(suffix)]
    return custom_name


@app.post("/api/mt5/custom-symbols/refresh")
def api_refresh_mt5_custom_symbols() -> dict[str, Any]:
    if jobs.has_active_mt5_import():
        raise HTTPException(409, "Wait for the running MT5 import to finish before refreshing")

    store = settings_store()
    mt5_config = store.get_mt5()
    if not str(mt5_config.get("terminal_exe") or "").strip():
        raise HTTPException(400, "Configure MetaTrader 5 path in Settings")

    suffix = str(mt5_config.get("custom_suffix") or ".DUK").strip() or ".DUK"
    try:
        symbols = list_custom_symbols(mt5_config, settings().data_dir)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    metadata = db()
    for entry in symbols:
        metadata.upsert_mt5_custom_symbol(
            symbol=str(entry["symbol"]),
            source_symbol=_guess_source_symbol(str(entry["symbol"]), suffix),
            ticks=int(entry.get("ticks", 0)),
            first_ms=int(entry.get("first_ms", 0)),
            last_ms=int(entry.get("last_ms", 0)),
        )
    return {"symbols": metadata.list_mt5_custom_symbols()}


@app.delete("/api/mt5/custom-symbols/{symbol}")
def api_delete_mt5_custom_symbol(symbol: str) -> dict[str, Any]:
    symbol = symbol.strip()
    if not symbol:
        raise HTTPException(400, "symbol is required")
    if jobs.has_active_mt5_import():
        raise HTTPException(409, "Wait for the running MT5 import to finish before deleting symbols")

    store = settings_store()
    mt5_config = store.get_mt5()
    if not str(mt5_config.get("terminal_exe") or "").strip():
        raise HTTPException(400, "Configure MetaTrader 5 path in Settings")

    try:
        delete_custom_symbol(mt5_config, settings().data_dir, symbol)
    except Exception as exc:
        logger.exception("DELETE /api/mt5/custom-symbols/%s failed", symbol)
        raise HTTPException(500, str(exc)) from exc
    db().delete_mt5_custom_symbol(symbol)
    return {"ok": True, "symbol": symbol}


@app.post("/api/gaps")
async def start_gaps(body: GapsRequest) -> dict[str, Any]:
    if body.all and (body.start or body.end):
        raise HTTPException(400, "--all cannot be combined with start/end dates")
    if not body.all and (not body.start or not body.end):
        raise HTTPException(400, "start and end dates are required unless all=true")

    symbols = _gap_symbols(body)
    for symbol in symbols:
        try:
            catalog().get(symbol)
        except UnknownInstrumentError as exc:
            raise HTTPException(400, str(exc)) from exc

    cfg = settings()
    params = body.model_dump()
    params["symbols"] = symbols
    job = jobs.submit(
        "gaps",
        params,
        lambda j: run_gaps_job(j, cfg, catalog(), db(), storage()),
    )
    return job.to_dict()


@app.post("/api/gaps/preview")
async def preview_gaps(body: GapsRequest) -> dict[str, Any]:
    """Synchronous gap scan for instant UI feedback."""
    symbols = _gap_symbols(body)
    scanner = GapScanner(settings(), db())
    metadata = db()
    results = []
    for symbol in symbols:
        try:
            inst = catalog().get(symbol)
        except UnknownInstrumentError as exc:
            raise HTTPException(400, str(exc)) from exc
        range_label, report = _gap_report(body, inst, scanner, metadata)
        results.append(_gap_preview_item(inst.symbol, range_label, report))
    return {"results": results, "complete": all(r.get("complete") for r in results)}


# -- settings & automations ---------------------------------------------------

@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    store = settings_store()
    automations = [_automation_public(r) for r in store.list_automations()]
    return {"ui": store.get_ui(), "mt5": store.get_mt5(), "automations": automations}


@app.patch("/api/settings/ui")
async def patch_ui_settings(body: UiSettingsUpdate) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "theme" in patch and patch["theme"] not in ("light", "dark"):
        raise HTTPException(400, "theme must be 'light' or 'dark'")
    ui = settings_store().set_ui(patch)
    return {"ui": ui}


@app.patch("/api/settings/mt5")
async def patch_mt5_settings(body: Mt5SettingsUpdate) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "custom_suffix" in patch:
        suffix = str(patch["custom_suffix"]).strip()
        if suffix and not suffix.startswith("."):
            suffix = "." + suffix
        patch["custom_suffix"] = suffix
    mt5 = settings_store().set_mt5(patch)
    return {"mt5": mt5}


def _validate_automation_symbols(action: AutomationActionModel) -> None:
    if action.symbols_source != "custom":
        return
    if not action.symbols:
        raise HTTPException(400, "custom symbol list is empty")
    for symbol in action.symbols:
        try:
            catalog().get(symbol)
        except UnknownInstrumentError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/automations")
async def create_automation(body: AutomationCreate) -> dict[str, Any]:
    _validate_automation_symbols(body.action)
    rule = settings_store().create_automation(body.model_dump())
    return _automation_public(rule)


@app.put("/api/automations/{rule_id}")
async def update_automation(rule_id: str, body: AutomationUpdate) -> dict[str, Any]:
    existing = settings_store().get_automation(rule_id)
    if existing is None:
        raise HTTPException(404, "automation not found")
    merged = {
        "name": body.name if body.name is not None else existing["name"],
        "enabled": body.enabled if body.enabled is not None else existing["enabled"],
        "schedule": (body.schedule.model_dump() if body.schedule else existing["schedule"]),
        "action": (body.action.model_dump() if body.action else existing["action"]),
    }
    _validate_automation_symbols(AutomationActionModel(**merged["action"]))
    rule = settings_store().update_automation(rule_id, merged)
    if rule is None:
        raise HTTPException(404, "automation not found")
    return _automation_public(rule)


@app.delete("/api/automations/{rule_id}")
async def delete_automation(rule_id: str) -> dict[str, Any]:
    if not settings_store().delete_automation(rule_id):
        raise HTTPException(404, "automation not found")
    return {"deleted": rule_id}


@app.post("/api/automations/{rule_id}/run")
async def run_automation_now(rule_id: str) -> dict[str, Any]:
    sched = get_scheduler()
    if sched is None:
        raise HTTPException(503, "scheduler not running")
    try:
        return sched.run_now(rule_id)
    except KeyError as exc:
        raise HTTPException(404, "automation not found") from exc
    except UnknownInstrumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
