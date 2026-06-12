"""Command line interface.

    python main.py search <text>
    python main.py download <SYMBOL> <START> <END> [--workers N] [--force] [--include-weekends]
    python main.py export   <SYMBOL> <START> <END>
    python main.py gaps     <SYMBOL> <START> <END> [--repair]
    python main.py gaps     <SYMBOL> --all [--repair]
    python main.py status   <SYMBOL>
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from config.settings import Settings
from core.services.download_engine import DownloadEngine
from core.services.gap_scanner import GapScanner
from core.services.instrument_search import InstrumentCatalog, UnknownInstrumentError
from core.services.planner import Planner
from export.mt5_csv_exporter import MT5CsvExporter
from storage.metadata_db import MetadataDB
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
    p_download.add_argument("--workers", type=int, default=None, help="parallel downloads")
    p_download.add_argument("--force", action="store_true",
                            help="re-process hours even if marked completed/empty")
    p_download.add_argument("--include-weekends", action="store_true",
                            help="request market-closed weekend hours too")

    p_export = sub.add_parser("export", help="export stored ticks to MT5 tick CSV")
    add_range_args(p_export)

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
    p_gaps.add_argument("--repair", action="store_true", help="download the missing hours")

    p_status = sub.add_parser("status", help="show stored data summary for an instrument")
    p_status.add_argument("symbol")

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
    if args.workers:
        settings.max_workers = args.workers
    if args.include_weekends:
        settings.skip_closed_market_hours = False

    db = MetadataDB(settings.db_path)
    storage = ParquetStorage(settings.data_dir)
    planner = Planner(settings, db)
    engine = DownloadEngine(settings, storage, db)

    plan = planner.plan(instrument, args.start, args.end, force=args.force)
    print(f"Instrument : {instrument.name} ({instrument.symbol}), "
          f"{instrument.price_decimals} decimals")
    if plan.effective_start is None:
        print("Nothing to do: no downloadable hours in this range "
              "(check the instrument's data start date and the recent-data lag).")
        return 0
    print(f"Range      : {plan.effective_start:%Y-%m-%d %H:%M} -> "
          f"{plan.effective_end:%Y-%m-%d %H:%M} UTC ({plan.total_hours} hours)")
    print(f"Plan       : {len(plan.tasks)} to download, {plan.already_done} already done, "
          f"{plan.auto_empty} market-closed\n")

    if not plan.tasks:
        print("All hours already downloaded. Nothing to do.")
        return 0

    stats = engine.run(plan.tasks)
    print(f"\nDone: {stats.completed} hours with data, {stats.empty} empty, "
          f"{stats.failed} failed, {stats.ticks:,} ticks total.")
    if stats.failed:
        print("Some hours kept failing; run later:\n"
              f"  python main.py gaps {instrument.symbol} {args.start} {args.end} --repair")
        return 1
    return 0


def cmd_export(settings: Settings, catalog: InstrumentCatalog, args) -> int:
    instrument = catalog.get(args.symbol)
    _validate_range(args.start, args.end)

    db = MetadataDB(settings.db_path)
    storage = ParquetStorage(settings.data_dir)
    planner = Planner(settings, db)
    scanner = GapScanner(settings, db)

    report = scanner.scan(instrument, args.start, args.end)
    if not report.is_complete:
        print(f"warning: {len(report.gap_hours)} hour(s) in range are not downloaded "
              f"({len(report.missing_hours)} missing, {len(report.failed_hours)} failed). "
              "The CSV will have gaps. Run the download/gaps command first for full data.\n")

    exporter = MT5CsvExporter(settings, storage, planner)
    result = exporter.export(instrument, args.start, args.end)
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

    if report.is_complete:
        print("Dataset is complete.")
        return 0

    if not args.repair:
        preview = ", ".join(f"{h:%Y-%m-%d %H:00}" for h in report.gap_hours[:10])
        print(f"Gap hours (first 10): {preview}")
        print("Run with --repair to download them.")
        return 1

    storage = ParquetStorage(settings.data_dir)
    engine = DownloadEngine(settings, storage, db)
    tasks = scanner.build_repair_tasks(instrument, report)
    print(f"Repairing {len(tasks)} hour(s)...\n")
    stats = engine.run(tasks)
    print(f"\nRepair done: {stats.completed} with data, {stats.empty} empty, "
          f"{stats.failed} still failing.")
    return 1 if stats.failed else 0


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
    except UnknownInstrumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved; rerun the same command to resume.")
        return 130
    return 0
