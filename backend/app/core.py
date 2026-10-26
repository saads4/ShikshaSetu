"""Small shared primitives: ids, clock, canonical JSON, problem+json errors."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


# --- ids -------------------------------------------------------------------
def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562 v7); sortable, good for index locality."""
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 68) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value)


# --- clock (DEC-16) ---------------------------------------------------------
_clock_offset_seconds = 0


def set_clock_offset(seconds: int) -> None:
    global _clock_offset_seconds
    _clock_offset_seconds = seconds


def now() -> datetime:
    """All business time goes through here so the demo clock offset applies."""
    return datetime.now(timezone.utc) + timedelta(seconds=_clock_offset_seconds)


def today():
    return now().date()


# --- canonical JSON ----------------------------------------------------------
def _default(o: Any):
    if isinstance(o, (datetime,)):
        return o.astimezone(timezone.utc).isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, (bytes, bytearray, memoryview)):
        return bytes(o).hex()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False).encode()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False)


# --- errors (RFC 9457 problem+json, REQ-INTG-03) ----------------------------
class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str = "", *, errors: list[dict] | None = None,
                 retryable: bool = False, extra: dict | None = None, headers: dict | None = None):
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.errors, self.retryable, self.extra, self.headers = errors or [], retryable, extra or {}, headers or {}
        super().__init__(f"{code}: {title}")


def problem_body(err: ApiError, request_id: str) -> dict:
    body = {"type": f"https://shikshasetu.example/problems/{err.code}", "title": err.title, "status": err.status,
            "code": err.code, "detail": err.detail, "request_id": request_id, "retryable": err.retryable,
            "errors": err.errors}
    body.update(err.extra)
    return body
