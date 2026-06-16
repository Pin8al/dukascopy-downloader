"""Fast directory removal helpers for large tick trees."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_TRASH_DIR = ".deleting"
_COMMON_JOBS_DIR = "dukascopy_jobs"


def fast_remove_tree(path: Path) -> None:
    """Remove a directory tree; on Windows prefer rd /s /q over shutil."""
    if not path.exists():
        return
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "rd", "/s", "/q", str(path)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        return
    shutil.rmtree(path, ignore_errors=True)


def release_import_staging_locks() -> None:
    """Remove MT5 import job folders that hard-link into data/{symbol}."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    jobs_root = Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / _COMMON_JOBS_DIR
    if jobs_root.is_dir():
        logger.debug("Removing MT5 import staging at %s", jobs_root)
        shutil.rmtree(jobs_root, ignore_errors=True)


def _windows_move_dir(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        fast_remove_tree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "robocopy",
            str(src),
            str(dst),
            "/E",
            "/MOVE",
            "/R:3",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
        ],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode >= 8:
        return False
    if src.exists():
        fast_remove_tree(src)
    return not src.exists()


def _try_rename(root: Path, trash: Path) -> bool:
    try:
        root.replace(trash)
        return True
    except OSError as exc:
        logger.debug("Could not rename %s aside: %s", root, exc)
        return False


def queue_remove(data_dir: Path, symbol: str) -> Path | None:
    """Move symbol folder aside instantly; caller removes the returned path later."""
    root = data_dir / symbol
    if not root.is_dir():
        return None

    trash_root = data_dir / _TRASH_DIR
    trash_root.mkdir(parents=True, exist_ok=True)
    trash = trash_root / f"{symbol}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    if os.name == "nt":
        if _try_rename(root, trash):
            logger.info("Queued delete for %s", symbol)
            return trash
        time.sleep(0.5)
        if _windows_move_dir(root, trash):
            logger.info("Queued delete for %s", symbol)
            return trash
        raise PermissionError(f"Access denied while deleting {symbol}")

    delays = (0.0, 0.25, 0.5, 1.0)
    last_err: OSError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            root.replace(trash)
            logger.info("Queued delete for %s", symbol)
            return trash
        except OSError as exc:
            last_err = exc
            logger.debug("Could not rename %s aside: %s", root, exc)
            if trash.exists() and not root.exists():
                logger.info("Queued delete for %s", symbol)
                return trash
            if trash.exists():
                fast_remove_tree(trash)
                trash = trash_root / f"{symbol}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    if last_err is not None:
        raise last_err
    return None
