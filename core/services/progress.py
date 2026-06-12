"""Single-line console progress bar for download runs.

Renders in place using carriage returns when attached to a terminal:

  [############........] 61%  295/480 h | ok 287  empty 6  failed 2 | 1,204,331 ticks | 4.1 h/s | ETA 0:00:45

When stdout is not a TTY (piped/redirected), it degrades to occasional
plain log lines so output files stay readable.
"""
from __future__ import annotations

import sys
import time


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


class ProgressBar:
    BAR_WIDTH = 24

    def __init__(self, total: int, label: str = "download"):
        self.total = total
        self.label = label
        self.completed = 0
        self.empty = 0
        self.failed = 0
        self.ticks = 0
        self._started_at = time.monotonic()
        self._last_render = 0.0
        self._is_tty = sys.stdout.isatty()
        # Plain-log fallback: report roughly every 5%.
        self._log_every = max(1, total // 20)

    @property
    def done(self) -> int:
        return self.completed + self.empty + self.failed

    def update(self, completed: int = 0, empty: int = 0, failed: int = 0, ticks: int = 0) -> None:
        self.completed += completed
        self.empty += empty
        self.failed += failed
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

    def _draw(self) -> None:
        fraction = self.done / self.total if self.total else 1.0
        filled = int(self.BAR_WIDTH * fraction)
        bar = "#" * filled + "." * (self.BAR_WIDTH - filled)

        elapsed = time.monotonic() - self._started_at
        rate = self.done / elapsed if elapsed > 0 else 0.0
        eta = _format_eta((self.total - self.done) / rate) if rate > 0 else "-:--:--"
        rate_text = f"{rate:.1f} h/s" if rate else "..."

        line = (
            f"[{bar}] {fraction:4.0%}  {self._stats_text()} | {rate_text} | ETA {eta}"
        )
        # Pad with spaces so a shrinking line doesn't leave artifacts.
        sys.stdout.write("\r" + line.ljust(110)[:110])
        sys.stdout.flush()

    def _stats_text(self) -> str:
        text = (
            f"{self.done}/{self.total} h | "
            f"ok {self.completed}  empty {self.empty}  failed {self.failed} | "
            f"{self.ticks:,} ticks"
        )
        if self.label != "download":
            text = f"[{self.label}] {text}"
        return text
