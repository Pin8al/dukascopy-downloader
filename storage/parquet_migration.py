"""One-time migration from legacy Parquet hour files to MT5-ready .bin files."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.services.progress import ProgressBar
from storage.metadata_db import MetadataDB
from storage.tick_format import write_hour_file


def _parquet_to_bin(parquet_path: Path, bin_path: Path) -> int:
    import pyarrow.parquet as pq

    table = pq.read_table(
        parquet_path,
        columns=["timestamp_ms", "bid", "ask"],
        memory_map=True,
    )
    count = table.num_rows
    if count == 0:
        return 0
    ts = table.column("timestamp_ms").to_pylist()
    bids = table.column("bid").to_pylist()
    asks = table.column("ask").to_pylist()
    write_hour_file(bin_path, ts, bids, asks)
    return count


def migrate_parquet_to_bin(
    data_dir: Path,
    db_path: Path,
    *,
    dry_run: bool = False,
    delete_parquet: bool = True,
    quiet: bool = False,
) -> dict[str, int]:
    parquet_files = sorted(data_dir.rglob("*.parquet"))
    stats = {
        "found": len(parquet_files),
        "converted": 0,
        "skipped": 0,
        "ticks": 0,
        "deleted": 0,
        "paths_updated": 0,
    }
    if not parquet_files:
        return stats

    progress = None if quiet else ProgressBar(len(parquet_files), label="migrate", style="migrate")

    db = MetadataDB(db_path)
    try:
        for parquet_path in parquet_files:
            bin_path = parquet_path.with_suffix(".bin")
            if bin_path.is_file():
                stats["skipped"] += 1
                if not dry_run and delete_parquet and parquet_path.is_file():
                    parquet_path.unlink()
                    stats["deleted"] += 1
                    if progress:
                        progress.update(deleted=1)
                if progress:
                    progress.update(empty=1)
                continue

            if dry_run:
                stats["converted"] += 1
                if progress:
                    progress.update(completed=1)
                continue

            tick_count = _parquet_to_bin(parquet_path, bin_path)
            stats["converted"] += 1
            stats["ticks"] += tick_count

            old_path = str(parquet_path.resolve())
            new_path = str(bin_path.resolve())
            updated = db.replace_file_path(old_path, new_path)
            if updated == 0:
                updated = db.replace_file_path(str(parquet_path), str(bin_path))
            stats["paths_updated"] += updated

            if delete_parquet:
                parquet_path.unlink(missing_ok=True)
                stats["deleted"] += 1
                if progress:
                    progress.update(deleted=1)

            if progress:
                progress.update(completed=1, ticks=tick_count)

        if not dry_run:
            stats["paths_updated"] += db.rewrite_parquet_paths()
            db.flush()
    finally:
        db.close()
        if progress:
            progress.finish()

    return stats


def remove_leftover_parquet(data_dir: Path, *, quiet: bool = False) -> int:
    """Delete all legacy .parquet hour files under data_dir. Returns files removed."""
    if not data_dir.is_dir():
        return 0

    parquet_files = list(data_dir.rglob("*.parquet"))
    if not parquet_files:
        return 0

    total = len(parquet_files)
    if os.name == "nt" and total > 500:
        if not quiet:
            print(f"Removing {total:,} legacy .parquet files…")
        path_arg = str(data_dir.resolve()).replace("'", "''")
        ps = (
            f"Get-ChildItem -LiteralPath '{path_arg}' -Recurse -Filter '*.parquet' -File "
            f"| Remove-Item -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            check=False,
        )
        return total

    progress = None if quiet else ProgressBar(total, label="cleanup", style="migrate")
    removed = 0
    for path in parquet_files:
        try:
            path.unlink()
            removed += 1
            if progress:
                progress.update(completed=1, deleted=1)
        except OSError:
            if progress:
                progress.update(failed=1)
    if progress:
        progress.finish()
    return removed
