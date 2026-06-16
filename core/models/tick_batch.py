"""Columnar tick batch produced by the JETTA decoder."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TickBatch:
    timestamp_ms: list[int] = field(default_factory=list)
    bid: list[float] = field(default_factory=list)
    ask: list[float] = field(default_factory=list)
    bid_volume: list[float] = field(default_factory=list)
    ask_volume: list[float] = field(default_factory=list)

    @property
    def num_rows(self) -> int:
        return len(self.timestamp_ms)

    @classmethod
    def empty(cls) -> TickBatch:
        return cls()
