"""Concurrency tuner for Dukascopy HTTP fetches.

Dukascopy punishes high burst concurrency (503 storms). Start moderate,
ramp up only while the feed stays clean, cut on 429 or sustained 503 noise.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import JobCancelled

_SAMPLE_SIZE = 30
_CLEAN_RATIO = 0.88
_NOISY_RATIO = 0.35
_RAMP_STEP = 2
_CUT_STEP = 4
_MIN_LIMIT = 4


@dataclass(frozen=True)
class ThrottleSnapshot:
    inflight: int
    limit: int
    ceiling: int
    state: str
    rate_limit_hits: int

    def as_dict(self) -> dict:
        return {
            "inflight": self.inflight,
            "limit": self.limit,
            "ceiling": self.ceiling,
            "state": self.state,
            "rate_limit_hits": self.rate_limit_hits,
        }


class AdaptiveThrottle:
    """Semaphore whose limit is tuned toward the best ok/s, not max burst."""

    def __init__(
        self,
        ceiling: int,
        state_path: Path,
        *,
        initial: int | None = None,
        enabled: bool = True,
    ):
        self._enabled = enabled
        self.ceiling = max(_MIN_LIMIT, ceiling)
        self._state_path = state_path
        saved = self._read_sustainable()
        start = initial if initial is not None else (saved or min(16, self.ceiling))
        self._limit = max(_MIN_LIMIT, min(self.ceiling, start))
        self._peak_stable_limit = self._limit
        self._inflight = 0
        self._cond = threading.Condition()
        self._ok = 0
        self._err503 = 0
        self._err429 = 0
        self._last_tune_at = time.monotonic()

    @property
    def current_limit(self) -> int:
        with self._cond:
            return self._limit

    def _read_sustainable(self) -> int | None:
        if not self._state_path.is_file():
            return None
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            value = int(data.get("sustainable_limit", 0))
            return value if value > 0 else None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def acquire(self, should_cancel: Callable[[], bool] | None = None) -> None:
        if not self._enabled:
            return
        with self._cond:
            while self._inflight >= self._limit:
                if should_cancel and should_cancel():
                    raise JobCancelled()
                self._cond.wait(timeout=0.1)

    def release(self) -> None:
        if not self._enabled:
            return
        with self._cond:
            self._inflight = max(0, self._inflight - 1)
            self._cond.notify_all()

    def record_fetch(self, status_code: int, latency_seconds: float) -> None:
        del latency_seconds
        if not self._enabled:
            return
        with self._cond:
            if status_code in (200, 404):
                self._ok += 1
            elif status_code == 429:
                self._err429 += 1
                self._cut(max(_MIN_LIMIT, self._limit // 2))
            elif status_code in (500, 502, 503, 504):
                self._err503 += 1

            total = self._ok + self._err503 + self._err429
            if total >= _SAMPLE_SIZE:
                self._tune()
            elif self._err429 >= 2:
                self._cut(max(_MIN_LIMIT, self._limit // 2))

    def _tune(self) -> None:
        total = self._ok + self._err503 + self._err429
        if total == 0:
            return
        ok_ratio = self._ok / total
        self._ok = 0
        self._err503 = 0
        err429 = self._err429
        self._err429 = 0
        self._last_tune_at = time.monotonic()

        if err429:
            return

        if ok_ratio >= _CLEAN_RATIO and self._limit < self.ceiling:
            self._limit = min(self.ceiling, self._limit + _RAMP_STEP)
            self._peak_stable_limit = max(self._peak_stable_limit, self._limit)
            self._cond.notify_all()
        elif ok_ratio < (1 - _NOISY_RATIO):
            self._limit = max(_MIN_LIMIT, self._limit - _CUT_STEP)
            self._cond.notify_all()

    def _cut(self, new_limit: int) -> None:
        if new_limit < self._limit:
            self._limit = new_limit
            self._cond.notify_all()

    def snapshot(self) -> ThrottleSnapshot:
        with self._cond:
            if not self._enabled:
                return ThrottleSnapshot(
                    inflight=0,
                    limit=self.ceiling,
                    ceiling=self.ceiling,
                    state="fixed",
                    rate_limit_hits=0,
                )
            if self._limit >= self.ceiling:
                state = "at_limit"
            elif time.monotonic() - self._last_tune_at < 2.0 and self._err429:
                state = "backing_off"
            elif self._limit < min(16, self.ceiling):
                state = "ramping"
            else:
                state = "steady"
            return ThrottleSnapshot(
                inflight=self._inflight,
                limit=self._limit,
                ceiling=self.ceiling,
                state=state,
                rate_limit_hits=self._err429,
            )

    def save(self) -> None:
        with self._cond:
            sustainable = max(self._peak_stable_limit, self._limit)
        payload = {
            "sustainable_limit": sustainable,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError:
            pass
