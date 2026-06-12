"""Sanity checks on decoded ticks before they are persisted."""
from __future__ import annotations

from dataclasses import dataclass

from core.models.tick import Tick

HOUR_MS = 3_600_000


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def verify_ticks(ticks: list[Tick], hour_start_ms: int) -> VerificationResult:
    previous_ts = -1
    hour_end_ms = hour_start_ms + HOUR_MS
    for index, tick in enumerate(ticks):
        if not (hour_start_ms <= tick.timestamp_ms < hour_end_ms):
            return VerificationResult(False, f"tick {index}: timestamp outside its hour")
        if tick.timestamp_ms < previous_ts:
            return VerificationResult(False, f"tick {index}: timestamps not monotonic")
        if tick.bid <= 0 or tick.ask <= 0:
            return VerificationResult(False, f"tick {index}: non-positive price")
        # Slightly crossed quotes occur in real data; reject only gross corruption.
        if tick.ask < tick.bid * 0.9:
            return VerificationResult(False, f"tick {index}: implausible spread")
        if tick.bid_volume < 0 or tick.ask_volume < 0:
            return VerificationResult(False, f"tick {index}: negative volume")
        previous_ts = tick.timestamp_ms
    return VerificationResult(True)
