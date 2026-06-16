#!/usr/bin/env python3
"""Delete metadata.db and legacy .parquet tick files. Keeps .bin tick data."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings
from storage.parquet_migration import remove_leftover_parquet


def wipe_db(db_path: Path) -> list[Path]:
    removed: list[Path] = []
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="only delete metadata.db, keep .parquet files",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    args = parser.parse_args()

    settings = Settings()
    data_dir = args.data_dir or settings.data_dir
    db_path = args.db_path or settings.db_path

    if not args.yes:
        print("This will delete:")
        print(f"  {db_path}")
        print(f"  {db_path}-wal")
        print(f"  {db_path}-shm")
        if not args.db_only:
            print(f"  all *.parquet files under {data_dir}")
        print(".bin tick data is kept.")
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    try:
        removed_db = wipe_db(db_path)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Stop the web UI or any running download first.", file=sys.stderr)
        return 2

    removed_parquet = 0
    if not args.db_only:
        try:
            removed_parquet = remove_leftover_parquet(data_dir)
        except OSError as exc:
            print(f"error removing parquet: {exc}", file=sys.stderr)
            return 2

    if removed_db:
        print("Deleted db:", ", ".join(p.name for p in removed_db))
    else:
        print("No database files found.")

    if not args.db_only:
        print(f"Deleted {removed_parquet:,} legacy .parquet file(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
