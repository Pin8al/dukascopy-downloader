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
    trades_weekends: bool = False

    @property
    def symbol(self) -> str:
        """Filesystem/CSV friendly symbol, e.g. EURUSD."""
        return self.id.upper()

    @property
    def feed_code(self) -> str:
        """Path segment used by the Dukascopy datafeed."""
        return self.id.upper()

    @property
    def price_decimals(self) -> int:
        """Number of decimal digits implied by the decimal factor."""
        return max(0, len(str(self.decimal_factor)) - 1)
