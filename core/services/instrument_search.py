"""Instrument catalog lookup and fuzzy search.

The catalog (config/instruments.json) is the full Dukascopy instrument
metadata set, including the per-instrument decimal factor required to
decode raw bi5 integer prices.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.models.instrument import Instrument

# Crypto base currencies traded 24/7 on Dukascopy. Used to decide whether
# weekend hours can be safely skipped during planning.
_CRYPTO_BASES = {
    "ada", "ave", "bat", "bch", "btc", "cmp", "dog", "dot", "dsh", "enj",
    "eos", "eth", "lnk", "ltc", "mat", "mkr", "sol", "trx", "uni", "xlm",
    "xmr", "xrp", "yfi", "zec",
}
_FIAT_QUOTES = ("usd", "eur", "gbp", "chf", "jpy")


class UnknownInstrumentError(Exception):
    pass


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _is_crypto(instrument_id: str) -> bool:
    return (
        len(instrument_id) == 6
        and instrument_id[:3] in _CRYPTO_BASES
        and instrument_id[3:] in _FIAT_QUOTES
    )


class InstrumentCatalog:
    def __init__(self, catalog_file: Path):
        with open(catalog_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        self._instruments: dict[str, Instrument] = {}
        for instrument_id, meta in raw.items():
            self._instruments[instrument_id] = Instrument(
                id=instrument_id,
                name=meta.get("name", instrument_id.upper()),
                description=meta.get("description", ""),
                decimal_factor=int(meta.get("decimalFactor", 100000)),
                earliest_tick_utc=_parse_iso(meta.get("startHourForTicks")),
                trades_weekends=_is_crypto(instrument_id),
            )

    def get(self, query: str) -> Instrument:
        """Resolve a user-supplied symbol (EURUSD, eur/usd, XAU.USD...) exactly."""
        key = "".join(ch for ch in query.lower() if ch.isalnum())
        instrument = self._instruments.get(key)
        if instrument is None:
            raise UnknownInstrumentError(
                f"Unknown instrument '{query}'. Try: python main.py search {query}"
            )
        return instrument

    def search(self, text: str) -> list[Instrument]:
        needle = text.lower().strip()
        compact = "".join(ch for ch in needle if ch.isalnum())
        results = []
        for instrument in self._instruments.values():
            haystack = f"{instrument.id} {instrument.name} {instrument.description}".lower()
            if (compact and compact in instrument.id) or needle in haystack:
                results.append(instrument)
        results.sort(key=lambda i: (not i.id.startswith(compact), i.id))
        return results
