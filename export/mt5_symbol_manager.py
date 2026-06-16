"""Launch MetaTrader 5 to list or delete custom symbols (via DukascopyTickImport)."""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from export.mt5_importer import (
    JOB_ROOT,
    MT5_BUNDLE_DIR,
    Mt5Settings,
    SCRIPT_MQ5_NAME,
    common_files_dir,
    ensure_script_installed,
    kill_mt5_terminal,
    launch_mt5_background,
    parse_progress_file,
    wait_mt5_exit,
    write_manifest,
    write_script_set,
    write_startup_ini,
)

logger = logging.getLogger(__name__)

MANAGER_TIMEOUT_SEC = 10 * 60


class Mt5JobError(RuntimeError):
    def __init__(self, message: str, *, job_dir: Path | None = None):
        super().__init__(message)
        self.job_dir = job_dir


@dataclass
class CustomSymbolInfo:
    symbol: str
    ticks: int
    first_ms: int
    last_ms: int

    @property
    def first_utc(self) -> str | None:
        if self.first_ms <= 0:
            return None
        return datetime.fromtimestamp(self.first_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M",
        )

    @property
    def last_utc(self) -> str | None:
        if self.last_ms <= 0:
            return None
        return datetime.fromtimestamp(self.last_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ticks": self.ticks,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
            "first_utc": self.first_utc,
            "last_utc": self.last_utc,
        }


def _append_persistent_log(data_dir: Path, text: str) -> Path:
    log_path = data_dir / "logs" / "mt5_jobs.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{stamp}]\n{text.rstrip()}\n")
    return log_path


def _collect_job_diagnostics(job_dir: Path, mt5: Mt5Settings | None = None) -> str:
    lines = [f"job_dir={job_dir}"]
    for name in ("manifest.txt", "progress.txt", "job.log"):
        path = job_dir / name
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace").rstrip()
            lines.append(f"--- {name} ---\n{content}")
        else:
            lines.append(f"--- {name}: (missing) ---")
    if mt5 is not None:
        compile_log = mt5.data_path / "MQL5" / "Scripts" / "dukascopy" / "compile.log"
        if compile_log.is_file():
            tail = compile_log.read_text(encoding="utf-8", errors="replace")[-2000:].rstrip()
            lines.append(f"--- compile.log (tail) ---\n{tail}")
        script_ex5 = mt5.data_path / "MQL5" / "Scripts" / "dukascopy" / "DukascopyTickImport.ex5"
        script_mq5 = mt5.data_path / "MQL5" / "Scripts" / "dukascopy" / "DukascopyTickImport.mq5"
        lines.append(
            f"--- installed script ---\n"
            f"ex5={script_ex5} exists={script_ex5.is_file()}\n"
            f"mq5={script_mq5} exists={script_mq5.is_file()}",
        )
    return "\n".join(lines)


def _parse_symbols_file(path: Path) -> list[CustomSymbolInfo]:
    if not path.is_file():
        return []
    symbols: list[CustomSymbolInfo] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        try:
            symbols.append(CustomSymbolInfo(
                symbol=parts[0],
                ticks=int(parts[1]),
                first_ms=int(parts[2]),
                last_ms=int(parts[3]),
            ))
        except ValueError:
            continue
    symbols.sort(key=lambda s: s.symbol)
    return symbols


def _wait_for_manager(progress_path: Path, job_dir: Path, mt5: Mt5Settings) -> dict[str, Any]:
    started = time.monotonic()
    last_log_at = 0.0
    while time.monotonic() - started < MANAGER_TIMEOUT_SEC:
        progress = parse_progress_file(progress_path)
        state = str(progress.get("state", ""))
        if state in ("done", "error"):
            return progress

        elapsed = time.monotonic() - started
        if elapsed - last_log_at >= 10.0:
            last_log_at = elapsed
            logger.info(
                "MT5 job waiting (%.0fs): progress=%s job.log exists=%s",
                elapsed,
                progress or "(none yet)",
                (job_dir / "job.log").is_file(),
            )
        time.sleep(0.5)

    diagnostics = _collect_job_diagnostics(job_dir, mt5)
    raise Mt5JobError(
        f"MT5 symbol operation timed out after {MANAGER_TIMEOUT_SEC}s\n\n{diagnostics}",
        job_dir=job_dir,
    )


def _run_symbol_job(
    mt5_raw: dict[str, Any],
    settings_data_dir: Path,
    manifest: dict[str, str],
    *,
    chart_symbol: str,
) -> dict[str, Any]:
    job_id = manifest["job_id"]
    action = manifest.get("action", "import")
    mt5 = Mt5Settings.from_dict(mt5_raw)
    job_dir = common_files_dir() / JOB_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    progress_path = job_dir / "progress.txt"
    staging_dir = settings_data_dir / "mt5_staging" / job_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    ini_path = staging_dir / "import.ini"

    logger.info(
        "MT5 job start: id=%s action=%s chart=%s job_dir=%s terminal=%s",
        job_id,
        action,
        chart_symbol,
        job_dir,
        mt5.terminal_exe,
    )

    try:
        write_manifest(job_dir / "manifest.txt", manifest)
        script_set = mt5.data_path / "MQL5" / "presets" / "dukascopy_import.set"
        script_set.parent.mkdir(parents=True, exist_ok=True)
        write_script_set(script_set, job_id)
        write_startup_ini(ini_path, symbol=chart_symbol)

        script_path = ensure_script_installed(mt5)
        bundle_mq5 = MT5_BUNDLE_DIR / SCRIPT_MQ5_NAME
        script_hash = hashlib.sha256(bundle_mq5.read_bytes()).hexdigest() if bundle_mq5.is_file() else "?"
        logger.info("MT5 script installed: %s bundle_sha256=%s", script_path, script_hash[:12])

        kill_mt5_terminal(mt5.terminal_exe)
        wait_mt5_exit(mt5.terminal_exe)
        launch_mt5_background(mt5.terminal_exe, ini_path)
        logger.info("MT5 launched with config %s", ini_path)

        final = _wait_for_manager(progress_path, job_dir, mt5)
        state = str(final.get("state", ""))
        if state == "error":
            msg = final.get("message") or "MT5 symbol operation failed"
            err_code = final.get("error_code", "")
            diagnostics = _collect_job_diagnostics(job_dir, mt5)
            raise Mt5JobError(
                f"{msg} (error_code={err_code})\n\n{diagnostics}",
                job_dir=job_dir,
            )
        logger.info("MT5 job done: id=%s state=%s message=%s", job_id, state, final.get("message"))
        return final
    except Mt5JobError:
        raise
    except Exception as exc:
        diagnostics = _collect_job_diagnostics(job_dir, mt5)
        raise Mt5JobError(f"{exc}\n\n{diagnostics}", job_dir=job_dir) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def list_custom_symbols(mt5_raw: dict[str, Any], settings_data_dir: Path) -> list[dict[str, Any]]:
    job_id = str(uuid.uuid4())
    suffix = str(mt5_raw.get("custom_suffix") or ".DUK").strip() or ".DUK"
    if not suffix.startswith("."):
        suffix = "." + suffix

    manifest = {
        "job_id": job_id,
        "action": "list",
        "custom_path": "dukascopy",
        "suffix": suffix,
    }

    job_dir = common_files_dir() / JOB_ROOT / job_id
    try:
        _run_symbol_job(mt5_raw, settings_data_dir, manifest, chart_symbol="EURUSD")
        return [s.to_dict() for s in _parse_symbols_file(job_dir / "symbols.txt")]
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


def delete_custom_symbol(
    mt5_raw: dict[str, Any],
    settings_data_dir: Path,
    symbol: str,
) -> None:
    job_id = str(uuid.uuid4())
    manifest = {
        "job_id": job_id,
        "action": "delete",
        "symbol": symbol,
    }
    job_dir = common_files_dir() / JOB_ROOT / job_id
    ok = False
    try:
        _run_symbol_job(mt5_raw, settings_data_dir, manifest, chart_symbol="EURUSD")
        ok = True
    except Mt5JobError as exc:
        log_path = _append_persistent_log(settings_data_dir, str(exc))
        logger.error(
            "MT5 delete failed for %s — full log: %s — artifacts: %s",
            symbol,
            log_path,
            exc.job_dir or job_dir,
        )
        raise RuntimeError(
            f"MT5 delete failed for {symbol}. Full log: {log_path}"
            + (f"\nJob artifacts: {exc.job_dir}" if exc.job_dir else ""),
        ) from exc
    finally:
        if job_dir.exists():
            if ok:
                shutil.rmtree(job_dir, ignore_errors=True)
            else:
                logger.error("MT5 delete job artifacts kept at %s", job_dir)
