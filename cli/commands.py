"""Command line interface.

    python main.py search <text>
    python main.py download <SYMBOL> <START> <END> [--workers N] [--force]
    python main.py export   <SYMBOL> <START> <END>
    python main.py export   <SYMBOL> --all
    python main.py gaps     <SYMBOL> <START> <END> [--repair]
    python main.py gaps     <SYMBOL> --all [--repair]
    python main.py status   <SYMBOL>
    python main.py web      [--host HOST] [--port PORT]
"""
from __future__ import annotations

import argparse
import sys
import threading
from datetime import date, datetime

from config.settings import Settings
from core.models.task import TaskStatus
from core.services.download_engine import DownloadEngine
from core.exceptions import IncompleteDatasetError
from core.services.gap_scanner import GapScanner, require_complete_export
from core.services.instrument_search import InstrumentCatalog, UnknownInstrumentError
from core.services.planner import Planner
from export.mt5_csv_exporter import MT5CsvExporter
from storage.metadata_db import MetadataDB, _hour_key
from storage.parquet_storage import ParquetStorage


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date '{value}', expected YYYY-MM-DD"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dukascopy-downloader",
        description="Download Dukascopy tick data into Parquet and export MT5 CSVs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="search the instrument catalog")
    p_search.add_argument("query")

    def add_range_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("symbol")
        p.add_argument("start", type=_parse_date, help="start date YYYY-MM-DD (inclusive)")
        p.add_argument("end", type=_parse_date, help="end date YYYY-MM-DD (inclusive)")

    p_download = sub.add_parser("download", help="download ticks into Parquet storage")
    add_range_args(p_download)
    p_download.add_argument(
        "--workers", type=int, default=None,
        help="max concurrent downloads (adaptive ceiling, default from settings)",
    )
    p_download.add_argument("--force", action="store_true",
                            help="re-process hours even if marked completed/empty")

    p_export = sub.add_parser("export", help="export stored ticks to MT5 tick CSV")
    p_export.add_argument("symbol")
    p_export.add_argument(
        "start",
        nargs="?",
        type=_parse_date,
        help="start date YYYY-MM-DD (required unless --all)",
    )
    p_export.add_argument(
        "end",
        nargs="?",
        type=_parse_date,
        help="end date YYYY-MM-DD (required unless --all)",
    )
    p_export.add_argument(
        "--all",
        action="store_true",
        help="export the full recorded range for this symbol (no start/end dates)",
    )

    p_gaps = sub.add_parser("gaps", help="report (and optionally repair) missing hours")
    p_gaps.add_argument("symbol")
    p_gaps.add_argument(
        "start",
        nargs="?",
        type=_parse_date,
        help="start date YYYY-MM-DD (required unless --all)",
    )
    p_gaps.add_argument(
        "end",
        nargs="?",
        type=_parse_date,
        help="end date YYYY-MM-DD (required unless --all)",
    )
    p_gaps.add_argument(
        "--all",
        action="store_true",
        help="scan the full recorded range for this symbol (no start/end dates)",
    )
    p_gaps.add_argument("--repair", action="store_true", help="download missing/failed hours")
    p_gaps.add_argument(
        "--workers", type=int, default=None,
        help="max concurrent downloads (same as download command)",
    )
    p_gaps.add_argument(
        "--refetch-empty",
        action="store_true",
        help="with --repair, also re-request hours already marked empty",
    )

    p_status = sub.add_parser("status", help="show stored data summary for an instrument")
    p_status.add_argument("symbol")

    p_web = sub.add_parser("web", help="start the web UI")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8080)

    return parser


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise SystemExit("error: end date is before start date")


def cmd_search(catalog: InstrumentCatalog, args) -> int:
    results = catalog.search(args.query)
    if not results:
        print(f"No instruments match '{args.query}'.")
        return 1
    print(f"{len(results)} instrument(s) match '{args.query}':\n")
    print(f"{'SYMBOL':<14} {'NAME':<16} {'DECIMALS':>8}  {'SINCE':<12} DESCRIPTION")
    for inst in results[:50]:
        since = inst.earliest_tick_utc.date().isoformat() if inst.earliest_tick_utc else "?"
        print(f"{inst.symbol:<14} {inst.name:<16} {inst.price_decimals:>8}  "
              f"{since:<12} {inst.description}")
    if len(results) > 50:
        print(f"... and {len(results) - 50} more (refine your query)")
    return 0


def cmd_download(settings: Settings, catalog: InstrumentCatalog, args) -> int:
    instrument = catalog.get(args.symbol)
    _validate_range(args.start, args.end)
    local_settings = settings.for_job(args.workers)

    db = MetadataDB(settings.db_path)
    storage = ParquetStorage(settings.data_dir, compression=settings.parquet_compression)
    planner = Planner(local_settings, db)
    engine = DownloadEngine(local_settings, storage, db)

    plan = planner.plan(instrument, args.start, args.end, force=args.force)
    print(f"Instrument : {instrument.name} ({instrument.symbol}), "
          f"{instrument.price_decimals} decimals")
    if plan.effective_start is None:
        print("Nothing to do: no downloadable hours in this range "
              "(check the instrument's data start date and the recent-data lag).")
        return 0
    print(f"Range      : {plan.effective_start:%Y-%m-%d %H:%M} -> "
          f"{plan.effective_end:%Y-%m-%d %H:%M} UTC ({plan.total_hours} hours)")
    print(f"Plan       : {len(plan.tasks)} to download, {plan.already_done} already done\n")

    if not plan.tasks:
        print("All hours already downloaded. Nothing to do.")
        return 0

    stats = engine.run(plan.tasks, refetch=args.force)
    print(f"\nDone: {stats.completed} hours with data, {stats.empty} empty, "
          f"{stats.failed} failed, {stats.ticks:,} ticks total.")
    if stats.failed:
        print("Some hours kept failing; run later:\n"
              f"  python main.py gaps {instrument.symbol} {args.start} {args.end} --repair")
        return 1
    return 0


def cmd_export(settings: Settings, catalog: InstrumentCatalog, args) -> int:
    instrument = catalog.get(args.symbol)
    if args.all and (args.start or args.end):
        raise SystemExit("error: --all cannot be combined with start/end dates")
    if not args.all and (args.start is None or args.end is None):
        raise SystemExit("error: start and end dates are required (or use --all)")

    db = MetadataDB(settings.db_path)
    storage = ParquetStorage(settings.data_dir, compression=settings.parquet_compression)
    planner = Planner(settings, db)
    scanner = GapScanner(settings, db)
    exporter = MT5CsvExporter(settings, storage, planner)

    if args.all:
        report, range_label = scanner.scan_for_export(instrument, export_all=True)
        if report is None:
            print(f"No data recorded for {instrument.symbol} yet.")
            return 0
        span = db.recorded_span(instrument.id)
        try:
            require_complete_export(report, instrument.symbol, range_label)
        except IncompleteDatasetError as exc:
            print(f"error: {exc}")
            return 1
        result = exporter.export_all(instrument, span[0], span[1])
    else:
        _validate_range(args.start, args.end)
        report, range_label = scanner.scan_for_export(
            instrument, start=args.start, end=args.end,
        )
        try:
            require_complete_export(report, instrument.symbol, range_label)
        except IncompleteDatasetError as exc:
            print(f"error: {exc}")
            return 1
        result = exporter.export(instrument, args.start, args.end)

    print(f"Exporting {instrument.symbol} {range_label}")
    print(f"Exported {result.rows:,} ticks from {result.hours_with_data} hours")
    print(f"  -> {result.path}")
    return 0


def cmd_gaps(settings: Settings, catalog: InstrumentCatalog, args) -> int:
    instrument = catalog.get(args.symbol)
    if args.all and (args.start or args.end):
        raise SystemExit("error: --all cannot be combined with start/end dates")
    if not args.all and (args.start is None or args.end is None):
        raise SystemExit("error: start and end dates are required (or use --all)")

    db = MetadataDB(settings.db_path)
    scanner = GapScanner(settings, db)

    if args.all:
        report = scanner.scan_all(instrument)
        if report is None:
            print(f"No data recorded for {instrument.symbol} yet.")
            return 0
        span = db.recorded_span(instrument.id)
        range_label = (
            f"{span[0]:%Y-%m-%d %H:%M} -> {span[1]:%Y-%m-%d %H:%M} UTC (all recorded)"
        )
    else:
        _validate_range(args.start, args.end)
        report = scanner.scan(instrument, args.start, args.end)
        range_label = f"{args.start} -> {args.end}"

    print(f"{instrument.symbol} {range_label}: "
          f"{report.total_hours} hours total, {report.completed} with data, "
          f"{report.empty} empty, {len(report.failed_hours)} failed, "
          f"{len(report.missing_hours)} never attempted")

    repair_hours = report.repair_hours(refetch_empty=args.refetch_empty)
    if not repair_hours:
        print("Dataset is complete.")
        return 0

    if not args.repair:
        if report.gap_hours:
            print(f"Gap hours ({len(report.gap_hours)}):")
            for hour in report.gap_hours:
                print(f"  {hour:%Y-%m-%d %H:00}")
            print("Run with --repair to download them.")
        if report.empty_hours:
            print(
                f"Also {len(report.empty_hours)} hour(s) marked empty "
                f"(use --repair --refetch-empty to re-request them)."
            )
        return 1

    local_settings = settings.for_job(args.workers)
    storage = ParquetStorage(settings.data_dir, compression=settings.parquet_compression)
    engine = DownloadEngine(local_settings, storage, db)
    tasks = scanner.build_repair_tasks(instrument, report, refetch_empty=args.refetch_empty)
    parts = []
    if report.gap_hours:
        parts.append(f"{len(report.gap_hours)} gap")
    if args.refetch_empty and report.empty_hours:
        parts.append(f"{len(report.empty_hours)} empty refetch")

    reasons: dict[str, str] = {}
    for hour in report.gap_hours:
        reasons[_hour_key(hour)] = "gap"
    for hour in report.empty_hours:
        reasons[_hour_key(hour)] = "empty refetch"

    print(f"Repairing {len(tasks)} hour(s) ({', '.join(parts)}):")
    for task in sorted(tasks, key=lambda t: t.hour):
        reason = reasons.get(_hour_key(task.hour), "repair")
        print(f"  {task.hour:%Y-%m-%d %H:00} UTC  [{reason}]")
    print()

    log_lock = threading.Lock()

    def on_task_done(task) -> None:
        reason = reasons.get(_hour_key(task.hour), "repair")
        with log_lock:
            if task.status is TaskStatus.COMPLETED:
                print(f"  {task.hour:%Y-%m-%d %H:00} UTC  [{reason}] -> {task.tick_count:,} ticks")
            elif task.status is TaskStatus.EMPTY:
                print(f"  {task.hour:%Y-%m-%d %H:00} UTC  [{reason}] -> still empty")
            else:
                print(f"  {task.hour:%Y-%m-%d %H:00} UTC  [{reason}] -> failed: {task.error}")

    stats = engine.run(tasks, on_task_done=on_task_done, refetch=True)
    print(f"\nDone: {stats.completed} hours with data, {stats.empty} empty, "
          f"{stats.failed} failed, {stats.ticks:,} ticks total.")
    return 1 if stats.failed else 0


def _port_available(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _resolve_web_port(host: str, port: int) -> int:
    if _port_available(host, port):
        return port
    for candidate in range(port + 1, port + 20):
        if _port_available(host, candidate):
            print(f"Port {port} is already in use, using {candidate} instead.")
            return candidate
    raise SystemExit(
        f"error: no free port found near {port}. "
        f"Stop the other process or run: python main.py web --port 9000"
    )


def cmd_web(args) -> int:
    import uvicorn

    port = _resolve_web_port(args.host, args.port)
    print(f"Web UI: http://{args.host}:{port}")
    uvicorn.run("web.app:app", host=args.host, port=port, reload=False)
    return 0


def cmd_status(settings: Settings, catalog: InstrumentCatalog, args) -> int:
    instrument = catalog.get(args.symbol)
    db = MetadataDB(settings.db_path)
    summary = db.summary(instrument.id)
    if not summary["by_status"]:
        print(f"No data recorded for {instrument.symbol} yet.")
        return 0
    print(f"{instrument.symbol} ({instrument.name})")
    print(f"  recorded range: {summary['first_hour']} -> {summary['last_hour']} UTC")
    for status, info in sorted(summary["by_status"].items()):
        line = f"  {status:<10} {info['hours']:>8} hours"
        if status == "completed":
            line += f"  ({info['ticks']:,} ticks)"
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to a legacy codepage (e.g. cp1250) that
    # cannot print some instrument descriptions.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = _build_parser().parse_args(argv)
    settings = Settings()
    settings.ensure_directories()
    catalog = InstrumentCatalog(settings.instruments_file)

    try:
        if args.command == "search":
            return cmd_search(catalog, args)
        if args.command == "download":
            return cmd_download(settings, catalog, args)
        if args.command == "export":
            return cmd_export(settings, catalog, args)
        if args.command == "gaps":
            return cmd_gaps(settings, catalog, args)
        if args.command == "status":
            return cmd_status(settings, catalog, args)
        if args.command == "web":
            return cmd_web(args)
    except UnknownInstrumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved; rerun the same command to resume.")
        return 130
    return 0
