"""Sanity checks on decoded ticks before they are persisted."""
from __future__ import annotations

from dataclasses import dataclass

import pyarrow.compute as pc

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


def verify_table(table, hour_start_ms: int) -> VerificationResult:
    """Vectorized checks — no full-column Python materialization."""
    n = table.num_rows
    if n == 0:
        return VerificationResult(True)

    hour_end_ms = hour_start_ms + HOUR_MS
    ts = table.column("timestamp_ms")
    bids = table.column("bid")
    asks = table.column("ask")
    bid_vols = table.column("bid_volume")
    ask_vols = table.column("ask_volume")

    min_ts = pc.min(ts).as_py()
    max_ts = pc.max(ts).as_py()
    if min_ts < hour_start_ms or max_ts >= hour_end_ms:
        return VerificationResult(False, "timestamp outside its hour")

    if n > 1:
        if not pc.all(pc.greater_equal(ts.slice(1, n - 1), ts.slice(0, n - 1))).as_py():
            return VerificationResult(False, "timestamps not monotonic")

    if pc.any(pc.less_equal(bids, 0)).as_py() or pc.any(pc.less_equal(asks, 0)).as_py():
        return VerificationResult(False, "non-positive price")

    if pc.any(pc.less(asks, pc.multiply(bids, 0.9))).as_py():
        return VerificationResult(False, "implausible spread")

    if pc.any(pc.less(bid_vols, 0)).as_py() or pc.any(pc.less(ask_vols, 0)).as_py():
        return VerificationResult(False, "negative volume")

    return VerificationResult(True)
