"""Database access.

One DATABASE_URL (owner connection). Request code runs inside `tx(ctx)`, which does
`SET LOCAL ROLE app_runtime` plus `SET LOCAL app.*` from the *verified token*, so RLS and the
history-table grants apply (REQ-ACL-02/04, DEC-21). Owner-level helpers (`owner_tx`) are for
migrations, seeding, key custody and the mock services only.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .config import get_settings


@dataclass(frozen=True)
class DbContext:
    """Values copied into SET LOCAL app.* - always derived from the verified principal."""
    institution_id: str = ""
    scope: str = ""          # NATIONAL | REGION | STATE | INSTITUTION | SYSTEM
    actor_id: str = ""
    student_id: str = ""


SYSTEM = DbContext(scope="SYSTEM")


@lru_cache
def engine() -> Engine:
    url = get_settings().database_url
    return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, future=True)


def reset_engine() -> None:
    engine.cache_clear()


def _apply_ctx(conn: Connection, ctx: DbContext) -> None:
    conn.execute(text("SET LOCAL ROLE app_runtime"))
    for key, val in (("app.institution_id", ctx.institution_id), ("app.scope", ctx.scope),
                     ("app.actor_id", ctx.actor_id), ("app.student_id", ctx.student_id)):
        conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": val})


@contextmanager
def tx(ctx: DbContext = SYSTEM, *, statement_timeout_ms: int | None = None,
       lock_timeout_ms: int | None = None) -> Iterator[Connection]:
    """A transaction running as app_runtime with RLS context set."""
    with engine().begin() as conn:
        _apply_ctx(conn, ctx)
        if statement_timeout_ms:
            conn.execute(text(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"))
        if lock_timeout_ms:
            conn.execute(text(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"))
        yield conn


@contextmanager
def owner_tx(scope: str = "SYSTEM") -> Iterator[Connection]:
    """Owner-level transaction (RLS still forced; scope set so policies pass)."""
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope', :s, true)"), {"s": scope})
        yield conn


def rows(conn: Connection, sql: str, **params: Any) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def row(conn: Connection, sql: str, **params: Any) -> dict | None:
    r = conn.execute(text(sql), params).first()
    return dict(r._mapping) if r else None


def scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def run(conn: Connection, sql: str, **params: Any) -> int:
    return conn.execute(text(sql), params).rowcount
