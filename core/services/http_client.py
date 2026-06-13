"""Persistent HTTP sessions for JETTA — one keep-alive connection per worker thread."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from config.settings import Settings

_thread_local = threading.local()


def _build_session(settings: Settings) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": settings.user_agent,
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    # One host (jetta.dukascopy.com); each thread runs one request at a time.
    adapter = HTTPAdapter(
        pool_connections=1,
        pool_maxsize=2,
        pool_block=True,
        max_retries=0,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def session_initializer(settings: Settings) -> Callable[[], None]:
    """Per-pool initializer — safe when multiple download jobs run concurrently."""
    warmup_url = f"{settings.base_url.rstrip('/')}/instruments/EUR-USD"

    def _init() -> None:
        session = _build_session(settings)
        _thread_local.session = session
        try:
            response = session.get(warmup_url, timeout=settings.request_timeout)
            response.content  # release socket back to the keep-alive pool
        except requests.RequestException:
            pass

    return _init


def worker_session(settings: Settings) -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = _build_session(settings)
        _thread_local.session = session
    return session


def loads_json(content: bytes) -> Any:
    try:
        import orjson
    except ImportError:
        import json

        return json.loads(content)
    return orjson.loads(content)
