"""Retry policy: exponential backoff with jitter for transient failures."""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Transient failure: worth retrying (timeouts, 5xx, corrupt payload)."""


class PermanentError(Exception):
    """Failure that retrying cannot fix (unexpected 4xx, bad instrument)."""


class RetryManager:
    def __init__(self, max_attempts: int, base_seconds: float, max_seconds: float):
        self.max_attempts = max_attempts
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds

    def run(self, fn: Callable[[], T]) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except RetryableError:
                if attempt >= self.max_attempts:
                    raise
                time.sleep(self._backoff(attempt))

    def _backoff(self, attempt: int) -> float:
        delay = min(self.max_seconds, self.base_seconds * (2 ** (attempt - 1)))
        return delay * (0.5 + random.random())  # jitter: 0.5x .. 1.5x
