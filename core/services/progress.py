"""Single-line console progress bar for download runs.

Renders in place using carriage returns when attached to a terminal:

  [############........] 61% 295/480 ok287 e6 f2 | 1.20M tks | 4.1 h/s | ETA 0:00:45

When stdout is not a TTY (piped/redirected), it degrades to occasional
plain log lines so output files stay readable.
"""
from __future__ import annotations

import shutil
import sys
import time


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_ticks(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 10_000:
        return f"{count / 1_000:.1f}k"
    return f"{count:,}"


def _enable_windows_vt() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _terminal_width() -> int:
    try:
        return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)
    except OSError:
        return 80


class ProgressBar:
    BAR_WIDTH = 24

    def __init__(self, total: int, label: str = "download", *, style: str = "download"):
        self.total = total
        self.label = label
        self.style = style
        self.completed = 0
        self.empty = 0
        self.failed = 0
        self.deleted = 0
        self.ticks = 0
        self._started_at = time.monotonic()
        self._last_render = 0.0
        self._is_tty = sys.stdout.isatty()
        self._throttle_text = ""
        self._last_len = 0
        self._prev_lines = 1
        self._ansi = _enable_windows_vt() if self._is_tty else False
        # Plain-log fallback: report roughly every 5%.
        self._log_every = max(1, total // 20)

    @property
    def done(self) -> int:
        return self.completed + self.empty + self.failed

    def set_throttle(self, snapshot: dict | None) -> None:
        if not snapshot:
            self._throttle_text = ""
            return
        state = snapshot.get("state", "")
        self._throttle_text = f"{snapshot['inflight']}/{snapshot['limit']} {state[:3]}"
        if snapshot.get("rate_limit_hits"):
            self._throttle_text += f" ·{snapshot['rate_limit_hits']}×429"

    def update(
        self,
        completed: int = 0,
        empty: int = 0,
        failed: int = 0,
        deleted: int = 0,
        ticks: int = 0,
    ) -> None:
        self.completed += completed
        self.empty += empty
        self.failed += failed
        self.deleted += deleted
        self.ticks += ticks

        now = time.monotonic()
        if self._is_tty:
            # Throttle redraws; always draw the final state.
            if self.done < self.total and now - self._last_render < 0.1:
                return
            self._last_render = now
            self._draw()
        elif self.done % self._log_every == 0 or self.done == self.total:
            print(self._stats_text())

    def finish(self) -> None:
        if self._is_tty and self.total > 0:
            self._draw()
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_len = 0
            self._prev_lines = 1

    def _draw(self) -> None:
        width = _terminal_width()
        line = self._build_line(width)
        self._write_in_place(line, width)

    def _build_line(self, width: int) -> str:
        fraction = self.done / self.total if self.total else 1.0
        elapsed = time.monotonic() - self._started_at
        rate = self.done / elapsed if elapsed > 0 else 0.0
        eta = _format_eta((self.total - self.done) / rate) if rate > 0 else "--:--"
        rate_text = f"{rate:.1f}/s" if rate else "..."

        label = f"[{self.label}] " if self.label != "download" else ""
        if self.style == "migrate":
            stats = (
                f"{fraction:3.0%} {self.done}/{self.total} "
                f"conv{self.completed} skip{self.empty} del{self.deleted} "
                f"{_format_ticks(self.ticks)} {rate_text}"
            )
        else:
            stats = (
                f"{fraction:3.0%} {self.done}/{self.total} "
                f"ok{self.completed} e{self.empty} f{self.failed} "
                f"{_format_ticks(self.ticks)} {rate_text}"
            )

        throttle = f" {self._throttle_text}" if self._throttle_text else ""
        eta_part = f" ETA {eta}"

        for show_throttle, show_eta in ((True, True), (False, True), (False, False)):
            tail = ""
            if show_throttle and throttle and width >= 72:
                tail += throttle
            if show_eta and width >= 58:
                tail += eta_part
            fixed = len(label) + len(stats) + len(tail) + 3
            bar_width = min(self.BAR_WIDTH, max(6, width - fixed))
            filled = int(bar_width * fraction)
            bar = "#" * filled + "." * (bar_width - filled)
            line = f"{label}[{bar}] {stats}{tail}"
            if len(line) <= width:
                return line
        return line[:width]

    def _clear_previous(self) -> None:
        if not self._ansi:
            pad = max(self._last_len, 1)
            sys.stdout.write("\r" + " " * pad + "\r")
            return
        if self._prev_lines <= 1:
            sys.stdout.write("\r\033[2K")
            return
        sys.stdout.write(f"\033[{self._prev_lines - 1}A")
        for i in range(self._prev_lines):
            sys.stdout.write("\033[2K")
            if i < self._prev_lines - 1:
                sys.stdout.write("\033[B")
        sys.stdout.write(f"\033[{self._prev_lines - 1}A")

    def _write_in_place(self, line: str, width: int) -> None:
        self._clear_previous()
        if self._ansi:
            sys.stdout.write("\r\033[2K" + line)
        else:
            pad = max(self._last_len, len(line))
            sys.stdout.write("\r" + line.ljust(pad))
        sys.stdout.flush()
        self._last_len = len(line)
        self._prev_lines = max(1, (len(line) + width - 1) // width)

    def _stats_text(self) -> str:
        if self.style == "migrate":
            text = (
                f"{self.done}/{self.total} files | "
                f"conv {self.completed}  skip {self.empty}  del {self.deleted} | "
                f"{self.ticks:,} ticks"
            )
        else:
            text = (
                f"{self.done}/{self.total} h | "
                f"ok {self.completed}  empty {self.empty}  failed {self.failed} | "
                f"{self.ticks:,} ticks"
            )
        if self.label != "download":
            text = f"[{self.label}] {text}"
        return text
