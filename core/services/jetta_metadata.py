"""Live tick availability from the Dukascopy JETTA API."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

_CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _parse_tick_history(payload: dict[str, Any]) -> datetime | None:
    for entry in payload.get("histories") or []:
        period = str(entry.get("period", "")).upper()
        if period not in ("TICK", "1T"):
            continue
        raw = entry.get("from")
        if raw is None:
            continue
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            continue
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return None


def fetch_instrument_info(
    jetta_code: str,
    base_url: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch JETTA instrument metadata; results are cached briefly."""
    key = jetta_code.upper()
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    url = f"{base_url.rstrip('/')}/instruments/{key}"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return {"jetta_code": key, "error": str(exc)}

    tick_from = _parse_tick_history(payload)
    info = {
        "jetta_code": payload.get("code") or key,
        "name": payload.get("name"),
        "description": payload.get("description"),
        "tick_from_utc": tick_from.isoformat() if tick_from else None,
        "tick_from_date": tick_from.date().isoformat() if tick_from else None,
    }
    with _lock:
        _cache[key] = (now, info)
    return info
