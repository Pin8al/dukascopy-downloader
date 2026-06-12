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
    def __init__(
        self,
        max_attempts: int,
        base_seconds: float,
        max_seconds: float,
        *,
        fast: bool = False,
    ):
        self.max_attempts = max_attempts
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self.fast = fast

    def run(self, fn: Callable[[], T]) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except RetryableError:
                if attempt >= self.max_attempts:
                    raise
                delay = self._backoff(attempt)
                if delay > 0:
                    time.sleep(delay)

    def _backoff(self, attempt: int) -> float:
        if self.fast and attempt == 1:
            return 0.0
        cap = min(self.max_seconds, 4.0) if self.fast else self.max_seconds
        delay = min(cap, self.base_seconds * (2 ** (attempt - 1)))
        return delay * (0.5 + random.random())  # jitter: 0.5x .. 1.5x
