"""One shared, keep-alive HTTP client for calls to the mock external services.

A new httpx call per request re-loads the TLS context (~0.4 s) and, on Windows, `localhost` first tries IPv6 (~1 s),
which would blow the registry's 2 s timeout. Hence a shared client and 127.0.0.1 in the default URL.
"""
from __future__ import annotations

import threading

import httpx

_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))
        return _client
