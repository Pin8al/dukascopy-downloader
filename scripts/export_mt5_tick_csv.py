"""Compatibility wrapper for the ``export-csv`` command.

Prefer ``python main.py export-csv`` for new uses.  This script keeps the
earlier year-by-year export workflow available for users who need it.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from export.mt5_csv import export_mt5_csv

ROOT = Path(__file__).resolve().parents[1]


def source_files(data_dir: Path, years: set[int] | None) -> dict[int, list[Path]]:
    groups: dict[int, list[Path]] = defaultdict(list)
    for path in data_dir.glob("*/*/*/*.bin"):
        try:
            year = int(path.relative_to(data_dir).parts[0])
        except (IndexError, ValueError):
            continue
        if years is None or year in years:
            groups[year].append(path)
    return {year: sorted(paths) for year, paths in sorted(groups.items())}


def write_year(year: int | str, paths: list[Path], output_path: Path, decimals: int) -> int:
    def progress(done: int, total: int, ticks: int) -> None:
        if done % 500 == 0 or done == total:
            print(f"{year}: {done:,} / {total:,} hours, {ticks:,} ticks", flush=True)

    return export_mt5_csv(paths, output_path, decimals=decimals, on_progress=progress).ticks


def main() -> int:
    parser = argparse.ArgumentParser(description="Export downloader bin files to MT5 tick CSV files")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--years", nargs="*", type=int, help="optional calendar years to export")
    parser.add_argument("--force", action="store_true", help="replace an existing export")
    parser.add_argument(
        "--single", action="store_true",
        help="write one full-range CSV instead of one CSV per calendar year",
    )
    args = parser.parse_args()

    symbol = args.symbol.upper()
    data_dir = ROOT / "data" / symbol
    output_dir = ROOT / "data" / "mt5_csv" / symbol
    if not data_dir.is_dir():
        raise SystemExit(f"No downloaded data found: {data_dir}")

    groups = source_files(data_dir, set(args.years) if args.years else None)
    if not groups:
        raise SystemExit("No tick files matched the requested years")

    decimals = 5 if symbol == "EURUSD" else 8
    manifest: dict[str, object] = {
        "symbol": symbol,
        "timezone": "UTC",
        "separator": "tab",
        "columns": ["DATE", "TIME", "BID", "ASK", "LAST", "VOLUME"],
        "files": {},
    }
    if args.single:
        all_paths = [path for paths in groups.values() for path in paths]
        first_year, last_year = min(groups), max(groups)
        output_path = output_dir / f"{symbol}_ticks_{first_year}-{last_year}.csv"
        if output_path.exists() and not args.force:
            print(f"full range: already exists, skipping {output_path.name}", flush=True)
            return 0
        print(f"full range: exporting {len(all_paths):,} hourly files to {output_path.name}", flush=True)
        rows = write_year(f"{first_year}-{last_year}", all_paths, output_path, decimals)
        manifest["files"][output_path.name] = {"ticks": rows, "hours": len(all_paths)}
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"full range: complete — {rows:,} ticks", flush=True)
        return 0

    for year, paths in groups.items():
        output_path = output_dir / f"{symbol}_ticks_{year}.csv"
        if output_path.exists() and not args.force:
            print(f"{year}: already exists, skipping {output_path.name}", flush=True)
            continue
        print(f"{year}: exporting {len(paths):,} hourly files to {output_path.name}", flush=True)
        rows = write_year(year, paths, output_path, decimals)
        manifest["files"][output_path.name] = {"ticks": rows, "hours": len(paths)}
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"{year}: complete — {rows:,} ticks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
