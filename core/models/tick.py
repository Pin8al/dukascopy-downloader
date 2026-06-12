"""A single market tick."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tick:
    timestamp_ms: int  # epoch milliseconds, UTC
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float
