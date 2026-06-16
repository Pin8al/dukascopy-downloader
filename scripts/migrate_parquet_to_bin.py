#!/usr/bin/env python3
"""Convert legacy .parquet tick files to MT5-ready .bin hour files.

Run once after upgrading from a Parquet-based install:

    python scripts/migrate_parquet_to_bin.py

Or via the CLI:

    python main.py migrate
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings
from storage.parquet_migration import migrate_parquet_to_bin


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-parquet", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    settings.ensure_directories()
    data_dir = args.data_dir or settings.data_dir
    db_path = args.db_path or settings.db_path

    if not data_dir.is_dir():
        print(f"error: data directory not found: {data_dir}", file=sys.stderr)
        return 2

    try:
        stats = migrate_parquet_to_bin(
            data_dir,
            db_path,
            dry_run=args.dry_run,
            delete_parquet=not args.keep_parquet,
        )
    except ImportError:
        print("error: pyarrow is required. Run: pip install pyarrow", file=sys.stderr)
        return 2

    label = "would convert" if args.dry_run else "converted"
    print(
        f"{label} {stats['converted']} file(s), "
        f"skipped {stats['skipped']}, "
        f"deleted {stats['deleted']} parquet, "
        f"{stats['ticks']:,} ticks, "
        f"{stats['paths_updated']} ledger path(s) updated",
    )
    if stats["found"] == 0:
        print("No .parquet files found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
