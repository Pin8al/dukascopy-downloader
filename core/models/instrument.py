"""Instrument metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Instrument:
    id: str  # Dukascopy id, lowercase, e.g. "eurusd"
    name: str  # e.g. "EUR/USD"
    description: str
    decimal_factor: int  # raw integer price divided by this gives the real price
    earliest_tick_utc: datetime | None = None
    continuous_trading: bool = False  # e.g. crypto — no FX session breaks

    @property
    def symbol(self) -> str:
        """Filesystem/CSV friendly symbol, e.g. EURUSD."""
        return self.id.upper()

    @property
    def feed_code(self) -> str:
        """Legacy alias kept for callers that still refer to feed codes."""
        return self.id.upper()

    @property
    def jetta_code(self) -> str:
        """Instrument code used by the JETTA API (e.g. EUR-USD, 0005.HK-HKD)."""
        if "/" in self.name:
            return self.name.replace("/", "-").upper()
        if len(self.id) == 6:
            return f"{self.id[:3].upper()}-{self.id[3:].upper()}"
        return self.id.upper()

    @property
    def price_decimals(self) -> int:
        """Number of decimal digits implied by the decimal factor."""
        return max(0, len(str(self.decimal_factor)) - 1)
