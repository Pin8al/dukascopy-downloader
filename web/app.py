"""FastAPI web UI for the Dukascopy downloader."""
from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.exceptions import IncompleteDatasetError
from core.services.gap_scanner import GapScanner, require_complete_export
from core.services.instrument_search import UnknownInstrumentError
from web.deps import catalog, db, settings, storage
from web.jobs import (
    JobManager,
    resolve_symbol,
    run_download_job,
    run_export_job,
    run_gaps_job,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
jobs = JobManager()

app = FastAPI(title="Dukascopy Downloader", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -- request models -----------------------------------------------------------

class DownloadRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    start: str
    end: str
    workers: int | None = None
    force: bool = False


class ExportRequest(BaseModel):
    symbol: str
    start: str | None = None
    end: str | None = None
    export_all: bool = False


class GapsRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    symbol: str | None = None
    start: str | None = None
    end: str | None = None
    all: bool = False
    repair: bool = False
    workers: int | None = None
    refetch_empty: bool = False


# -- helpers ------------------------------------------------------------------

def _instrument_payload(inst) -> dict[str, Any]:
    return {
        "id": inst.id,
        "symbol": inst.symbol,
        "name": inst.name,
        "description": inst.description,
        "decimals": inst.price_decimals,
        "earliest": inst.earliest_tick_utc.isoformat() if inst.earliest_tick_utc else None,
    }


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
        "missing": len(report.missing_hours),
        "gap_count": len(report.gap_hours),
        "complete": report.is_complete,
        "gap_hours": [h.strftime("%Y-%m-%d %H:00") for h in report.gap_hours[:20]],
    }


# -- pages --------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# -- instruments --------------------------------------------------------------

@app.get("/api/instruments/search")
async def search_instruments(q: str = Query("", min_length=0), limit: int = 30) -> dict[str, Any]:
    if not q.strip():
        return {"results": []}
    results = catalog().search(q)[:limit]
    return {"results": [_instrument_payload(i) for i in results]}


@app.get("/api/instruments/{symbol}")
async def get_instrument(symbol: str) -> dict[str, Any]:
    try:
        return resolve_symbol(catalog(), symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


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
async def delete_instrument_data(symbol: str) -> dict[str, Any]:
    metadata = db()
    store = storage()
    cfg = settings()

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

    store.delete_symbol(symbol_dir)
    deleted_hours = metadata.delete_instrument(instrument_id)

    export_dir = cfg.export_dir / symbol_dir
    if export_dir.exists():
        shutil.rmtree(export_dir)

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


# -- exports list -------------------------------------------------------------

@app.get("/api/exports")
async def list_exports() -> dict[str, Any]:
    export_dir = settings().export_dir
    files = []
    if export_dir.exists():
        for path in sorted(export_dir.rglob("*.csv"), reverse=True):
            rel = path.relative_to(export_dir).as_posix()
            files.append({
                "symbol": path.parent.name,
                "filename": path.name,
                "path": rel,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })
    return {"exports": files[:100]}


def _export_file_path(path: str) -> Path:
    export_dir = settings().export_dir.resolve()
    target = (export_dir / path).resolve()
    if not str(target).startswith(str(export_dir)) or not target.is_file():
        raise HTTPException(404, "export file not found")
    return target


@app.get("/api/exports/file")
async def download_export(path: str = Query(...)) -> FileResponse:
    target = _export_file_path(path)
    return FileResponse(target, filename=target.name, media_type="text/csv")


@app.delete("/api/exports/file")
async def delete_export(path: str = Query(...)) -> dict[str, Any]:
    target = _export_file_path(path)
    export_dir = settings().export_dir.resolve()
    rel = target.relative_to(export_dir).as_posix()
    target.unlink()
    parent = target.parent
    if parent != export_dir and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return {"deleted": rel}


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
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(404, "job not found or already finished")
    return job.to_dict()


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


@app.post("/api/export")
async def start_export(body: ExportRequest) -> dict[str, Any]:
    if body.export_all and (body.start or body.end):
        raise HTTPException(400, "export_all cannot be combined with start/end dates")
    if not body.export_all and (not body.start or not body.end):
        raise HTTPException(400, "start and end dates are required unless export_all=true")

    try:
        inst = catalog().get(body.symbol)
    except UnknownInstrumentError as exc:
        raise HTTPException(400, str(exc)) from exc

    cfg = settings()
    metadata = db()
    scanner = GapScanner(cfg, metadata)
    if body.export_all:
        report, range_label = scanner.scan_for_export(inst, export_all=True)
    else:
        start, end = _parse_date(body.start), _parse_date(body.end)
        if end < start:
            raise HTTPException(400, "end date is before start date")
        report, range_label = scanner.scan_for_export(inst, start=start, end=end)
    try:
        require_complete_export(report, inst.symbol, range_label)
    except IncompleteDatasetError as exc:
        raise HTTPException(400, str(exc)) from exc

    job = jobs.submit(
        "export",
        body.model_dump(),
        lambda j: run_export_job(j, cfg, catalog(), metadata, storage()),
    )
    return job.to_dict()


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
