"""Prepare tick jobs and launch MetaTrader 5 to import custom symbols."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.settings import BASE_DIR, Settings
from core.exceptions import JobCancelled
from core.models.instrument import Instrument
from core.services.planner import Planner
from export.mt5_tick_publisher import HOURS_MANIFEST, MT5TickPublisher
from storage.metadata_db import MetadataDB
from storage.tick_storage import TickStorage

MT5_BUNDLE_DIR = BASE_DIR / "mt5"
SCRIPT_BASE_NAME = "DukascopyTickImport"
SCRIPT_MQ5_NAME = f"{SCRIPT_BASE_NAME}.mq5"
SCRIPT_EX5_NAME = f"{SCRIPT_BASE_NAME}.ex5"
M30_EA_BASE_NAME = "M30CacheWarmer"
M30_EA_MQ5_NAME = f"{M30_EA_BASE_NAME}.mq5"
M30_EA_EX5_NAME = f"{M30_EA_BASE_NAME}.ex5"
M30_CACHE_SUBFOLDER = "M30Cache"
M30_WARMUP_TIMEOUT_SEC = 5 * 60
IMPORT_STALL_SEC = 90
LEGACY_IMPORT_NAMES = (
    "DukascopyImport.mq5",
    "DukascopyImport.ex5",
)
LEGACY_MANAGER_NAMES = (
    "DukascopySymbolManager.mq5",
    "DukascopySymbolManager.ex5",
)
JOB_ROOT = "dukascopy_jobs"

_import_lock = threading.Lock()
_active_terminal: dict[str, Path] = {}
IMPORT_TIMEOUT_SEC = 6 * 3600
MT5_STARTUP_TIMEOUT_SEC = 120


@dataclass
class Mt5Settings:
    terminal_exe: Path
    data_path: Path
    custom_suffix: str = ".DUK"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Mt5Settings:
        terminal = str(raw.get("terminal_exe") or "").strip()
        if not terminal:
            raise ValueError("MetaTrader 5 path is not configured — set it in Settings")
        terminal_exe = Path(terminal)
        if not terminal_exe.is_file():
            raise ValueError(f"MetaTrader 5 not found: {terminal_exe}")

        data_path_raw = str(raw.get("data_path") or "").strip()
        if data_path_raw:
            data_path = Path(data_path_raw)
        else:
            data_path = resolve_mt5_data_path(terminal_exe)
        if not data_path.is_dir():
            raise ValueError(f"MT5 data folder not found: {data_path}")

        suffix = str(raw.get("custom_suffix") or ".DUK").strip() or ".DUK"
        if not suffix.startswith("."):
            suffix = "." + suffix
        return cls(
            terminal_exe=terminal_exe,
            data_path=data_path,
            custom_suffix=suffix,
        )


def resolve_mt5_data_path(terminal_exe: Path) -> Path:
    """Best-effort MT5 data directory (Roaming\\MetaQuotes\\Terminal\\<id>)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValueError("APPDATA is not set — configure MT5 data path manually in Settings")
    root = Path(appdata) / "MetaQuotes" / "Terminal"
    if not root.is_dir():
        raise ValueError(f"MetaQuotes Terminal folder not found: {root}")

    origin = terminal_exe.parent / "origin.txt"
    if origin.is_file():
        instance_id = origin.read_text(encoding="utf-16-le", errors="ignore").strip()
        if not instance_id:
            instance_id = origin.read_text(encoding="utf-8", errors="ignore").strip()
        candidate = root / instance_id
        if candidate.is_dir():
            return candidate

    candidates = [
        p for p in root.iterdir()
        if p.is_dir() and p.name not in ("Common", "Community", "Help")
    ]
    if not candidates:
        raise ValueError("No MT5 terminal data folders found — configure data path in Settings")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValueError("APPDATA is not set")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def custom_symbol_name(source_symbol: str, suffix: str) -> str:
    name = f"{source_symbol}{suffix}"
    if len(name) > 31:
        raise ValueError(
            f"Custom symbol name '{name}' exceeds MT5 limit of 31 characters — shorten the suffix",
        )
    return name


def parse_progress_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in ("ticks_imported", "ticks_total", "percent", "error_code", "files_done", "files_total"):
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = 0
        else:
            data[key] = value
    return data


def write_manifest(path: Path, fields: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in fields.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_script_set(path: Path, job_id: str) -> None:
    path.write_text(f"; generated by dukascopy-downloader\nJobId={job_id}\n", encoding="utf-8")


def write_startup_ini(
    path: Path,
    *,
    symbol: str,
) -> None:
    content = f"""; generated by dukascopy-downloader
[Experts]
AllowLiveTrading=0
Enabled=1

[StartUp]
Symbol={symbol}
Period=M1
Script=dukascopy\\DukascopyTickImport
ScriptParameters=dukascopy_import.set
ShutdownTerminal=0
"""
    path.write_text(content, encoding="utf-8")


def write_expert_startup_ini(
    path: Path,
    *,
    symbol: str,
    expert: str,
    period: str,
    preset: str,
) -> None:
    content = f"""; generated by dukascopy-downloader
[Experts]
AllowLiveTrading=0
Enabled=1

[StartUp]
Symbol={symbol}
Period={period}
Expert={expert}
ExpertParameters={preset}
ShutdownTerminal=0
"""
    path.write_text(content, encoding="utf-8")


def write_m30_expert_set(path: Path, job_id: str) -> None:
    path.write_text(
        "; generated by dukascopy-downloader\n"
        f"InpJobId={job_id}\n"
        "InpForceRebuild=true\n",
        encoding="utf-8",
    )


def m30_cache_file_path(symbol: str, subfolder: str = M30_CACHE_SUBFOLDER) -> Path:
    safe = (
        symbol.replace(".", "_")
        .replace(":", "_")
        .replace("\\", "_")
        .replace("/", "_")
    )
    return common_files_dir() / subfolder / f"M30_{safe}.bin"


def _compile_mq5(
    mt5: Mt5Settings,
    dst_mq5: Path,
    dst_ex5: Path,
    hash_stamp: Path,
    src_hash: str,
) -> Path:
    if dst_ex5.is_file():
        dst_ex5.unlink(missing_ok=True)

    metaeditor = mt5.terminal_exe.parent / "metaeditor64.exe"
    log_tail = ""
    if metaeditor.is_file():
        log_path = dst_mq5.parent / "compile.log"
        subprocess.run(
            [str(metaeditor), f"/compile:{dst_mq5}", f"/log:{log_path}"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if dst_ex5.is_file():
            hash_stamp.write_text(src_hash + "\n", encoding="utf-8")
            return dst_ex5
        if log_path.is_file():
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]

    if metaeditor.is_file():
        raise RuntimeError(
            f"Failed to compile {dst_mq5.name}. Log: {log_tail or 'no details'}",
        )

    raise FileNotFoundError(
        f"{dst_ex5.name} is missing and MetaEditor was not found.",
    )


def ensure_m30_cache_ea_installed(
    mt5: Mt5Settings,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    if should_cancel and should_cancel():
        raise JobCancelled()

    experts_dir = mt5.data_path / "MQL5" / "Experts" / "dukascopy"
    experts_dir.mkdir(parents=True, exist_ok=True)

    src_mq5 = MT5_BUNDLE_DIR / M30_EA_MQ5_NAME
    if not src_mq5.is_file():
        raise FileNotFoundError(f"Missing M30 cache EA source: {src_mq5}")

    dst_mq5 = experts_dir / M30_EA_MQ5_NAME
    dst_ex5 = experts_dir / M30_EA_EX5_NAME
    hash_stamp = experts_dir / f".{M30_EA_BASE_NAME}.mq5.sha256"
    src_hash = _sha256_file(src_mq5)
    installed_hash = hash_stamp.read_text(encoding="utf-8").strip() if hash_stamp.is_file() else ""

    if installed_hash != src_hash or not dst_mq5.is_file():
        shutil.copy2(src_mq5, dst_mq5)

    needs_compile = (
        not dst_ex5.is_file()
        or installed_hash != src_hash
        or dst_mq5.stat().st_mtime > dst_ex5.stat().st_mtime
    )
    if not needs_compile:
        return dst_ex5

    return _compile_mq5(mt5, dst_mq5, dst_ex5, hash_stamp, src_hash)


def _run_m30_cache_warmup(
    mt5: Mt5Settings,
    *,
    custom_symbol: str,
    job_id: str,
    progress_path: Path,
    staging_dir: Path,
    ticks_imported: int,
    ticks_total: int,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    if should_cancel and should_cancel():
        raise JobCancelled()

    if on_progress:
        on_progress({
            "message": "Warming M30 cache…",
            "percent": 92,
            "phase": "warm_m30",
            "ticks_imported": ticks_imported,
            "ticks_total": ticks_total,
            "custom_symbol": custom_symbol,
        })

    kill_mt5_terminal(mt5.terminal_exe)
    wait_mt5_exit(mt5.terminal_exe)
    ensure_m30_cache_ea_installed(mt5, should_cancel=should_cancel)

    preset_path = mt5.data_path / "MQL5" / "presets" / "dukascopy_m30.set"
    preset_path.parent.mkdir(parents=True, exist_ok=True)
    write_m30_expert_set(preset_path, job_id)

    ini_path = staging_dir / "m30_warmup.ini"
    write_expert_startup_ini(
        ini_path,
        symbol=custom_symbol,
        expert=r"dukascopy\M30CacheWarmer",
        period="M30",
        preset="dukascopy_m30.set",
    )

    cache_path = m30_cache_file_path(custom_symbol)
    launch_mt5_background(mt5.terminal_exe, ini_path)

    started = time.monotonic()
    while time.monotonic() - started < M30_WARMUP_TIMEOUT_SEC:
        if should_cancel and should_cancel():
            kill_mt5_terminal(mt5.terminal_exe)
            raise JobCancelled()

        progress = parse_progress_file(progress_path)
        state = str(progress.get("state", ""))
        phase = str(progress.get("phase", ""))
        if state == "done" and phase == "warm_m30":
            return
        if state == "error":
            msg = progress.get("message") or "M30 cache warmup failed"
            code = progress.get("error_code", "")
            raise RuntimeError(f"{msg} (error {code})".strip())

        if cache_path.is_file() and cache_path.stat().st_size > 16:
            return

        if on_progress:
            on_progress({
                "message": progress.get("message") or "Warming M30 cache…",
                "percent": max(92, int(progress.get("percent", 92))),
                "phase": "warm_m30",
                "ticks_imported": ticks_imported,
                "ticks_total": ticks_total,
                "custom_symbol": custom_symbol,
            })
        time.sleep(0.75)

    raise RuntimeError(
        f"M30 cache warmup timed out after {M30_WARMUP_TIMEOUT_SEC}s for {custom_symbol}",
    )


def purge_import_artifacts(mt5: Mt5Settings) -> None:
    """Remove legacy script builds (not active importer or M30 cache EA)."""
    legacy = LEGACY_IMPORT_NAMES + LEGACY_MANAGER_NAMES
    for subdir in ("Experts", "Scripts"):
        folder = mt5.data_path / "MQL5" / subdir / "dukascopy"
        if not folder.is_dir():
            continue
        if subdir == "Experts":
            continue
        for name in legacy:
            path = folder / name
            if path.is_file():
                path.unlink(missing_ok=True)


SCRIPT_HASH_NAME = f".{SCRIPT_BASE_NAME}.mq5.sha256"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_script_installed(mt5: Mt5Settings, should_cancel: Callable[[], bool] | None = None) -> Path:
    """Copy MQ5 to Scripts and compile to EX5 (never reuse stale EA builds)."""
    if should_cancel and should_cancel():
        raise JobCancelled()

    purge_import_artifacts(mt5)

    scripts_dir = mt5.data_path / "MQL5" / "Scripts" / "dukascopy"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    src_mq5 = MT5_BUNDLE_DIR / SCRIPT_MQ5_NAME
    if not src_mq5.is_file():
        raise FileNotFoundError(f"Missing import script source: {src_mq5}")

    dst_mq5 = scripts_dir / SCRIPT_MQ5_NAME
    dst_ex5 = scripts_dir / SCRIPT_EX5_NAME
    hash_stamp = scripts_dir / SCRIPT_HASH_NAME
    src_hash = _sha256_file(src_mq5)
    installed_hash = hash_stamp.read_text(encoding="utf-8").strip() if hash_stamp.is_file() else ""

    if installed_hash != src_hash or not dst_mq5.is_file():
        shutil.copy2(src_mq5, dst_mq5)

    needs_compile = (
        not dst_ex5.is_file()
        or installed_hash != src_hash
        or dst_mq5.stat().st_mtime > dst_ex5.stat().st_mtime
    )

    if not needs_compile:
        return dst_ex5

    if dst_ex5.is_file():
        dst_ex5.unlink(missing_ok=True)

    if should_cancel and should_cancel():
        raise JobCancelled()

    metaeditor = mt5.terminal_exe.parent / "metaeditor64.exe"
    log_tail = ""
    if metaeditor.is_file():
        log_path = scripts_dir / "compile.log"
        subprocess.run(
            [str(metaeditor), f"/compile:{dst_mq5}", f"/log:{log_path}"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if dst_ex5.is_file():
            hash_stamp.write_text(src_hash + "\n", encoding="utf-8")
            return dst_ex5
        if log_path.is_file():
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]

    if metaeditor.is_file():
        raise RuntimeError(
            "Failed to compile DukascopyTickImport.mq5. "
            f"Open MetaEditor, compile mt5/{SCRIPT_MQ5_NAME}, save .ex5 to mt5/. "
            f"Log: {log_tail or 'no details'}",
        )

    raise FileNotFoundError(
        f"DukascopyTickImport.ex5 is missing and MetaEditor was not found. "
        f"Compile mt5/{SCRIPT_MQ5_NAME} in MetaEditor once.",
    )


def cleanup_staging(
    mt5: Mt5Settings,
    job_id: str,
    *,
    remove_job_files: bool = True,
    remove_script: bool = False,
) -> None:
    if remove_job_files:
        job_dir = common_files_dir() / JOB_ROOT / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)

    if remove_script:
        purge_import_artifacts(mt5)

    script_set = mt5.data_path / "MQL5" / "presets" / "dukascopy_import.set"
    if script_set.is_file():
        script_set.unlink(missing_ok=True)

    legacy_tester_set = mt5.data_path / "MQL5" / "Profiles" / "Tester" / "dukascopy_import.set"
    if legacy_tester_set.is_file():
        legacy_tester_set.unlink(missing_ok=True)


def kill_mt5_terminal(terminal_exe: Path) -> None:
    """Stop MT5 instances for the configured terminal executable."""
    if os.name != "nt":
        return
    resolved = str(terminal_exe.resolve()).replace("'", "''")
    process_name = terminal_exe.stem.replace("'", "''")
    script = (
        f"$target = '{resolved}'; "
        f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and ($_.Path -ieq $target) } | "
        "Stop-Process -Force -ErrorAction SilentlyContinue"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=10,
        check=False,
    )


def is_mt5_running(terminal_exe: Path) -> bool:
    if os.name != "nt":
        return False
    resolved = str(terminal_exe.resolve()).replace("'", "''")
    process_name = terminal_exe.stem.replace("'", "''")
    script = (
        f"$target = '{resolved}'; "
        f"$p = Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and ($_.Path -ieq $target) }; "
        "if ($p) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def wait_mt5_exit(terminal_exe: Path, timeout_sec: float = 30.0) -> None:
    """Wait until MT5 has fully exited (needed before /config startup scripts run)."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not is_mt5_running(terminal_exe):
            return
        time.sleep(0.25)
    if is_mt5_running(terminal_exe):
        kill_mt5_terminal(terminal_exe)
        time.sleep(2.0)


def launch_mt5_background(terminal_exe: Path, ini_path: Path) -> None:
    """Launch MT5 minimized and detached (Windows: cmd start /MIN)."""
    exe_dir = str(terminal_exe.parent)
    config_arg = f"/config:{ini_path}"
    if os.name == "nt":
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "/MIN", str(terminal_exe), config_arg],
            cwd=exe_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=(
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
            close_fds=True,
        )
        return
    subprocess.Popen(
        [str(terminal_exe), config_arg],
        cwd=exe_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def terminate_process(job_id: str) -> None:
    terminal = _active_terminal.pop(job_id, None)
    if terminal is not None:
        kill_mt5_terminal(terminal)


def abort_mt5_import(
    job_id: str,
    mt5_raw: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> None:
    """Stop MT5 and remove in-flight import artifacts for a job."""
    terminal = _active_terminal.pop(job_id, None)
    if mt5_raw:
        try:
            terminal = terminal or Mt5Settings.from_dict(mt5_raw).terminal_exe
        except ValueError:
            terminal = terminal
    if terminal is not None:
        kill_mt5_terminal(terminal)

    job_dir = common_files_dir() / JOB_ROOT / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    if mt5_raw:
        try:
            mt5 = Mt5Settings.from_dict(mt5_raw)
            cleanup_staging(mt5, job_id, remove_script=False)
            if settings is not None:
                staging_dir = settings.data_dir / "mt5_staging" / job_id
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
        except ValueError:
            pass


def _progress_snapshot_key(progress: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(progress.get("state", "")),
        str(progress.get("phase", "")),
        int(progress.get("percent", 0)),
        int(progress.get("files_done", 0)),
        int(progress.get("ticks_imported", 0)),
    )


def _synthesize_ticks_done(
    progress: dict[str, Any],
    custom_name: str,
    *,
    message: str,
) -> dict[str, Any]:
    return {
        **progress,
        "state": "ticks_done",
        "phase": "import_ticks",
        "percent": 90,
        "custom_symbol": progress.get("custom_symbol", custom_name),
        "message": message,
    }


def _import_progress_payload(
    progress: dict[str, Any],
    custom_name: str,
) -> dict[str, Any]:
    imported = int(progress.get("ticks_imported", 0))
    total = int(progress.get("ticks_total", 0))
    files_done = int(progress.get("files_done", 0))
    files_total = int(progress.get("files_total", 0))
    payload: dict[str, Any] = {
        "message": progress.get("message") or "Importing in MT5…",
        "percent": int(progress.get("percent", 0)),
        "phase": progress.get("phase", "import"),
        "ticks_imported": imported,
        "ticks_total": total,
        "custom_symbol": progress.get("custom_symbol", custom_name),
    }
    if files_total > 0:
        payload["files_done"] = files_done
        payload["files_total"] = files_total
    return payload


def _wait_for_mt5_import(
    progress_path: Path,
    *,
    job_id: str,
    terminal_exe: Path,
    custom_name: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Poll progress.txt until MT5 reports done or error."""
    _active_terminal[job_id] = terminal_exe
    last_emit = 0.0
    started = time.monotonic()
    startup_deadline = started + MT5_STARTUP_TIMEOUT_SEC
    saw_progress = False
    last_snap: tuple[Any, ...] | None = None
    stall_since: float | None = None

    while time.monotonic() - started < IMPORT_TIMEOUT_SEC:
        if should_cancel and should_cancel():
            kill_mt5_terminal(terminal_exe)
            _active_terminal.pop(job_id, None)
            raise JobCancelled()

        final_progress = parse_progress_file(progress_path)
        if final_progress:
            saw_progress = True
        elif not saw_progress and time.monotonic() > startup_deadline:
            raise RuntimeError(
                "MT5 did not start the import script within 2 minutes. "
                "Close MetaTrader 5 completely and try again.",
            )

        state = str(final_progress.get("state", ""))
        phase = str(final_progress.get("phase", ""))
        if state in ("done", "ticks_done", "error"):
            return final_progress

        files_done = int(final_progress.get("files_done", 0))
        files_total = int(final_progress.get("files_total", 0))
        if (
            state == "running"
            and phase == "import_ticks"
            and files_total > 0
            and files_done >= files_total
        ):
            kill_mt5_terminal(terminal_exe)
            return _synthesize_ticks_done(
                final_progress,
                custom_name,
                message="Hour files imported — warming M30 cache next",
            )

        now = time.monotonic()
        snap = _progress_snapshot_key(final_progress) if final_progress else None
        if snap and snap == last_snap and state == "running" and phase == "import_ticks":
            if stall_since is None:
                stall_since = now
            elif now - stall_since >= IMPORT_STALL_SEC:
                pct = int(final_progress.get("percent", 0))
                if pct >= 40 or files_done > 0:
                    kill_mt5_terminal(terminal_exe)
                    return _synthesize_ticks_done(
                        final_progress,
                        custom_name,
                        message="Import progress stalled — continuing with M30 cache warmup",
                    )
        else:
            stall_since = None
            last_snap = snap

        if on_progress and now - last_emit >= 0.5:
            last_emit = now
            if final_progress:
                on_progress(_import_progress_payload(final_progress, custom_name))
            else:
                on_progress({
                    "message": "Launching MetaTrader 5…",
                    "percent": 0,
                    "phase": "launch",
                    "custom_symbol": custom_name,
                })
        time.sleep(0.75)

    raise RuntimeError("MT5 import timed out waiting for progress")


def import_ticks(
    settings: Settings,
    mt5_raw: dict[str, Any],
    instrument: Instrument,
    *,
    job_id: str,
    manifest_fields: dict[str, str],
    import_all: bool,
    range_start: date | None,
    range_end: date | None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Launch MT5 script import for a prepared job folder."""
    with _import_lock:
        return _import_ticks_locked(
            settings,
            mt5_raw,
            instrument,
            job_id=job_id,
            manifest_fields=manifest_fields,
            import_all=import_all,
            range_start=range_start,
            range_end=range_end,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )


def _import_ticks_locked(
    settings: Settings,
    mt5_raw: dict[str, Any],
    instrument: Instrument,
    *,
    job_id: str,
    manifest_fields: dict[str, str],
    import_all: bool,
    range_start: date | None,
    range_end: date | None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    mt5 = Mt5Settings.from_dict(mt5_raw)
    custom_name = custom_symbol_name(instrument.symbol, mt5.custom_suffix)
    origin = str(mt5_raw.get("origin_symbol") or instrument.symbol).strip() or instrument.symbol

    job_dir = common_files_dir() / JOB_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    progress_path = job_dir / "progress.txt"

    staging_dir = settings.data_dir / "mt5_staging" / job_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    ini_path = staging_dir / "import.ini"

    import_ok = False
    try:
        write_manifest(job_dir / "manifest.txt", manifest_fields)

        script_set = mt5.data_path / "MQL5" / "presets" / "dukascopy_import.set"
        script_set.parent.mkdir(parents=True, exist_ok=True)
        write_script_set(script_set, job_id)

        write_startup_ini(
            ini_path,
            symbol=origin,
        )

        if on_progress:
            on_progress({
                "message": "Installing MT5 importer…",
                "percent": 0,
                "phase": "install",
                "custom_symbol": custom_name,
            })

        ensure_script_installed(mt5, should_cancel=should_cancel)

        if should_cancel and should_cancel():
            raise JobCancelled()

        if on_progress:
            on_progress({
                "message": "Launching MetaTrader 5…",
                "percent": 0,
                "phase": "launch",
                "custom_symbol": custom_name,
            })

        launch_mt5_background(mt5.terminal_exe, ini_path)

        final_progress = _wait_for_mt5_import(
            progress_path,
            job_id=job_id,
            terminal_exe=mt5.terminal_exe,
            custom_name=custom_name,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

        state = str(final_progress.get("state", ""))
        if state == "ticks_done":
            imported = int(final_progress.get("ticks_imported", 0))
            total = int(final_progress.get("ticks_total", 0))
            _run_m30_cache_warmup(
                mt5,
                custom_symbol=str(final_progress.get("custom_symbol", custom_name)),
                job_id=job_id,
                progress_path=progress_path,
                staging_dir=staging_dir,
                ticks_imported=imported,
                ticks_total=total,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            import_ok = True
            return {
                "custom_symbol": final_progress.get("custom_symbol", custom_name),
                "ticks_imported": imported,
                "ticks_total": total,
                "source_symbol": instrument.symbol,
                "import_all": import_all,
            }

        if state == "done":
            imported = int(final_progress.get("ticks_imported", 0))
            total = int(final_progress.get("ticks_total", imported))
            _run_m30_cache_warmup(
                mt5,
                custom_symbol=str(final_progress.get("custom_symbol", custom_name)),
                job_id=job_id,
                progress_path=progress_path,
                staging_dir=staging_dir,
                ticks_imported=imported,
                ticks_total=total,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            import_ok = True
            return {
                "custom_symbol": final_progress.get("custom_symbol", custom_name),
                "ticks_imported": imported,
                "ticks_total": total,
                "source_symbol": instrument.symbol,
                "import_all": import_all,
            }

        if state == "error":
            msg = final_progress.get("message") or "MT5 import failed"
            code = final_progress.get("error_code", "")
            raise RuntimeError(f"{msg} (error {code})".strip())

        raise RuntimeError(
            final_progress.get("message") or "MT5 import finished without a success status",
        )
    finally:
        _active_terminal.pop(job_id, None)
        if import_ok:
            cleanup_staging(mt5, job_id, remove_job_files=True, remove_script=False)
        else:
            cleanup_staging(mt5, job_id, remove_job_files=True, remove_script=False)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def prepare_and_import(
    settings: Settings,
    store: TickStorage,
    planner: Planner,
    instrument: Instrument,
    *,
    job_id: str,
    mt5_raw: dict[str, Any],
    import_all: bool,
    start: date | None,
    end: date | None,
    range_label: str,
    metadata: MetadataDB | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Link hour tick files into the MT5 job folder, then run import."""
    mt5 = Mt5Settings.from_dict(mt5_raw)
    _active_terminal[job_id] = mt5.terminal_exe
    kill_mt5_terminal(mt5.terminal_exe)
    wait_mt5_exit(mt5.terminal_exe)

    job_dir = common_files_dir() / JOB_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = settings.data_dir / "mt5_staging" / job_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    publisher = MT5TickPublisher(settings, store, planner, metadata)
    custom_name = custom_symbol_name(instrument.symbol, mt5.custom_suffix)
    origin = str(mt5_raw.get("origin_symbol") or instrument.symbol).strip() or instrument.symbol

    def publish_progress(snapshot: dict[str, Any]) -> None:
        if not on_progress:
            return
        pct = float(snapshot.get("percent", 0))
        on_progress({
            **snapshot,
            "message": snapshot.get("message") or "Preparing import…",
            "percent": max(1.0, round(pct, 1)),
            "phase": "prepare",
            "ticks_total": snapshot.get("total_ticks") or snapshot.get("rows"),
            "files_done": snapshot.get("done"),
            "files_total": snapshot.get("files_total"),
        })

    try:
        if on_progress:
            on_progress({
                "message": "Preparing import…",
                "percent": 1,
                "phase": "prepare",
            })
        if import_all:
            span_start = start
            span_end = end
            if span_start is None or span_end is None:
                raise ValueError("import span missing for import_all")
            result = publisher.publish_all(
                instrument,
                datetime(span_start.year, span_start.month, span_start.day, tzinfo=timezone.utc),
                datetime(span_end.year, span_end.month, span_end.day, 23, tzinfo=timezone.utc),
                job_dir,
                on_progress=publish_progress,
                should_cancel=should_cancel,
            )
        else:
            if start is None or end is None:
                raise ValueError("start and end dates required")
            result = publisher.publish(
                instrument,
                start,
                end,
                job_dir,
                on_progress=publish_progress,
                should_cancel=should_cancel,
            )

        if should_cancel and should_cancel():
            raise JobCancelled()

        manifest = {
            "job_id": job_id,
            "source_symbol": instrument.symbol,
            "custom_symbol": custom_name,
            "custom_path": "dukascopy",
            "origin_symbol": origin,
            "digits": str(instrument.price_decimals),
            "replace_existing": "1",
            "tick_format": "bin_v1",
            "tick_mode": "hours",
            "hours_file": HOURS_MANIFEST,
            "ticks_total": str(result.rows),
            "hours_total": str(result.files_published),
        }

        import_result = import_ticks(
            settings,
            mt5_raw,
            instrument,
            job_id=job_id,
            manifest_fields=manifest,
            import_all=import_all,
            range_start=start,
            range_end=end,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        import_result["ticks_source"] = result.rows
        import_result["range"] = range_label
        import_result["hours_with_data"] = result.hours_with_data
        return import_result
    except JobCancelled:
        abort_mt5_import(job_id, mt5_raw, settings=settings)
        raise
    finally:
        _active_terminal.pop(job_id, None)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


assemble_and_import = prepare_and_import
stage_and_import = prepare_and_import
