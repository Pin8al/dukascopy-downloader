"""Format per-hour download profiling for CLI output."""
from __future__ import annotations

from core.models.task import HourTask, TaskProfile, TaskStatus


def format_profile_line(task: HourTask) -> str:
    hour = task.hour.strftime("%Y-%m-%d %H:00")
    symbol = task.instrument.symbol
    profile = task.profile or TaskProfile()

    if profile.skipped:
        return f"  {symbol} {hour}  skip (on disk)  {profile.total_ms:.0f}ms"

    if task.status is TaskStatus.FAILED:
        return (
            f"  {symbol} {hour}  FAIL  "
            f"fetch {profile.fetch_ms:.0f}ms  "
            f"decode {profile.decode_ms:.0f}ms  "
            f"write {profile.write_ms:.0f}ms  "
            f"total {profile.total_ms:.0f}ms"
        )

    status = "empty" if task.status is TaskStatus.EMPTY else "ok"
    ticks = f"{task.tick_count:,} ticks" if task.status is TaskStatus.COMPLETED else "no ticks"
    return (
        f"  {symbol} {hour}  {status:<5} {ticks:<12} "
        f"fetch {profile.fetch_ms:5.0f}ms  "
        f"decode {profile.decode_ms:5.0f}ms  "
        f"write {profile.write_ms:5.0f}ms  "
        f"total {profile.total_ms:5.0f}ms"
    )
