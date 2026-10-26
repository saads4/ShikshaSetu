"""Operations page, audit explorer, demo tools and the Dev SMS Inbox (PRD 8.11, 8.12; DEC-03, DEC-32)."""
from __future__ import annotations

import statistics
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import audit, core, http, identity, ops
from .config import get_settings
from .core import ApiError, now
from .db import SYSTEM, owner_tx, row, rows, scalar, tx
from .deps import Principal, public, secured

router = APIRouter(prefix="/api/v1", tags=["operations"])

_latency: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))


def record_latency(path: str, ms: float) -> None:
    group = "/".join(path.split("/")[:4])
    _latency[group].append(ms)


def _demo_only() -> None:
    if not get_settings().demo_mode:
        raise ApiError(404, "DEMO_ONLY", "Not found", "Demo tools are not available.")


@router.get("/ops/status")
def ops_status(p: Principal = Depends(secured("ops:read"))):
    with tx(SYSTEM) as conn:
        queues = rows(conn, "SELECT queue, count(*) FILTER (WHERE status='QUEUED') AS queued, count(*) FILTER (WHERE status='RUNNING') AS running, "
                            "count(*) FILTER (WHERE status='DEAD') AS dead, count(*) FILTER (WHERE status='SUCCEEDED') AS succeeded, "
                            "extract(epoch FROM (now() - min(created_at) FILTER (WHERE status='QUEUED'))) AS oldest_queued_s FROM ops.jobs GROUP BY queue ORDER BY queue")
        outbox_lag = scalar(conn, "SELECT count(*) FROM ops.outbox_events WHERE published_at IS NULL")
        unsealed = scalar(conn, "SELECT count(*) FROM audit.audit_log WHERE seq IS NULL")
        pending_registry = scalar(conn, "SELECT count(*) FROM core.enrolment_verification WHERE identity_status='PENDING_VERIFICATION'")
        schedules = rows(conn, "SELECT name, interval_seconds, last_run_at, next_run_at, enabled FROM ops.schedules ORDER BY name")
        dead = rows(conn, "SELECT id, queue, type, attempts, last_error, finished_at FROM ops.jobs WHERE status='DEAD' ORDER BY finished_at DESC LIMIT 20")
        as_of = scalar(conn, "SELECT as_of FROM agg.projector_state WHERE name='main'")
    lat = {k: {"count": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": round(sorted(v)[int(len(v) * 0.95) - 1], 1) if len(v) >= 2 else round(v[0], 1)}
           for k, v in _latency.items() if v}
    return {"queues": [{**q, "oldest_queued_s": float(q["oldest_queued_s"]) if q["oldest_queued_s"] is not None else None} for q in queues],
            "outbox_lag_events": outbox_lag, "audit_unsealed": unsealed, "pending_registry_checks": pending_registry,
            "projector_as_of": as_of, "registry_breaker": identity.breaker().state, "schedules": schedules,
            "dead_jobs": [{**d, "id": str(d["id"])} for d in dead], "latency": lat, "note": "In-app Operations page replaces Grafana (deferred, DEC-03)."}


# ------------------------------------------------------------------ audit
@router.get("/audit")
def audit_explorer(p: Principal = Depends(secured("audit:read")), action: str | None = None, entity_type: str | None = None,
                   actor_id: str | None = None, institution_id: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                   before_id: int | None = None):
    where, params = ["true"], {"n": limit}
    for col, val in (("action", action), ("entity_type", entity_type)):
        if val:
            where.append(f"a.{col} ILIKE :{col}"); params[col] = f"{val}%"
    if actor_id:
        where.append("a.actor_id=CAST(:actor AS uuid)"); params["actor"] = actor_id
    if institution_id:
        where.append("a.institution_id=CAST(:inst AS uuid)"); params["inst"] = institution_id
    if before_id:
        where.append("a.id < :b"); params["b"] = before_id
    if p.scope_type in ("REGION", "STATE"):
        col = "region" if p.scope_type == "REGION" else "state"
        where.append(f"(a.institution_id IS NULL OR a.institution_id IN (SELECT id FROM ref.institutions WHERE {col}=:sv))"); params["sv"] = p.scope_value
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT a.id, a.seq, a.occurred_at, a.actor_role, a.action, a.entity_type, a.entity_id, a.institution_id, a.justification, "
                          "a.payload, u.username AS actor FROM audit.audit_log a LEFT JOIN iam.users u ON u.id=a.actor_id WHERE " + " AND ".join(where) +
                    " ORDER BY a.id DESC LIMIT :n", **params)
        sealed = scalar(conn, "SELECT coalesce(max(seq),0) FROM audit.audit_log")
        unsealed = scalar(conn, "SELECT count(*) FROM audit.audit_log WHERE seq IS NULL")
        audit.log(conn, "audit.viewed", entity_type="audit", actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    return {"items": [{**d, "institution_id": str(d["institution_id"]) if d["institution_id"] else None} for d in data],
            "chain": {"sealed_through_seq": sealed, "unsealed_entries": unsealed, "worm": "SIMULATED (local append-only directory)"}}


@router.post("/audit/integrity-check")
def integrity_check(p: Principal = Depends(secured("audit:read"))):
    audit.seal()
    result = audit.integrity_check()
    with tx(p.db_ctx()) as conn:
        audit.log(conn, "audit.integrity_check", entity_type="audit", actor_id=p.id, actor_role=p.role, request_id=p.request_id,
                  payload={"ok": result["ok"], "first_broken_seq": result["first_broken_seq"]})
    return result


# ------------------------------------------------------------------ demo tools (AS only, DEMO_MODE)
class RegistryModeIn(BaseModel):
    mode: str = Field(pattern="^(UP|DOWN|SLOW)$")
    latency_ms: int | None = Field(default=None, ge=0, le=10000)


class ClockIn(BaseModel):
    advance_days: int = Field(ge=-400, le=400)


class RunJobIn(BaseModel):
    type: str


@router.post("/demo/registry-mode")
def demo_registry_mode(body: RegistryModeIn, p: Principal = Depends(secured("demo:tools"))):
    _demo_only()
    r = http.client().post(f"{get_settings().mock_services_url}/admin/registry-mode", json=body.model_dump(), timeout=5)
    r.raise_for_status()
    identity.reset_breaker()
    with tx(p.db_ctx()) as conn:
        audit.log(conn, "demo.registry_mode", actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"mode": body.mode})
    return r.json()


@router.get("/demo/state")
def demo_state(p: Principal = Depends(secured("demo:tools"))):
    _demo_only()
    try:
        reg = http.client().get(f"{get_settings().mock_services_url}/admin/registry-mode", timeout=3).json()
    except Exception:
        reg = {"mode": "UNREACHABLE"}
    with tx(SYSTEM) as conn:
        offset = scalar(conn, "SELECT (value->>'offset_days')::int FROM ops.system_config WHERE key='demo_clock'")
    return {"registry": reg, "clock_offset_days": offset or 0, "now": core.now(), "breaker": identity.breaker().state,
            "label": "Demo tools - available only because DEMO_MODE=true"}


@router.post("/demo/clock")
def demo_clock(body: ClockIn, p: Principal = Depends(secured("demo:tools"))):
    _demo_only()
    with owner_tx() as conn:
        conn.execute(text("INSERT INTO ops.system_config (key, value) VALUES ('demo_clock', CAST(:v AS jsonb)) ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=now()"),
                     {"v": core.dumps({"offset_days": body.advance_days})})
    core.set_clock_offset(body.advance_days * 86400)
    with tx(p.db_ctx()) as conn:
        audit.log(conn, "demo.clock", actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"offset_days": body.advance_days})
    return {"offset_days": body.advance_days, "now": core.now(),
            "note": "Applies to this API process; the worker reads the same offset from ops.system_config."}


@router.post("/demo/run-job")
def demo_run_job(body: RunJobIn, p: Principal = Depends(secured("demo:tools"))):
    _demo_only()
    if body.type not in ops.HANDLERS:
        raise ApiError(422, "VALIDATION_ERROR", "Unknown job", f"Known jobs: {sorted(ops.HANDLERS)}")
    result = ops.run_job_inline(body.type, {})
    with tx(p.db_ctx()) as conn:
        audit.log(conn, "demo.run_job", actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"type": body.type})
    return {"type": body.type, "result": result}


@router.get("/dev/sms", dependencies=[Depends(public())])
def dev_sms_inbox(limit: int = Query(default=50, ge=1, le=200)):
    """Dev SMS Inbox [SIMULATED]: shows what the mock SMS gateway 'sent', including the OTP (demo only)."""
    _demo_only()
    with owner_tx() as conn:
        data = rows(conn, "SELECT id, recipient_masked, template_code, language, body, otp, created_at FROM mockext.sms_messages ORDER BY id DESC LIMIT :n", n=limit)
    return {"label": "SIMULATED SMS gateway - no real message is ever sent", "items": data}
