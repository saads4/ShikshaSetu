"""Postgres-backed job queue, scheduler and handlers (DEC-02: no Redis, no Celery).

Workers claim jobs with SELECT ... FOR UPDATE SKIP LOCKED under a lease; handlers are idempotent.
"""
from __future__ import annotations

import logging
import os
import random
import socket
import threading
import time
from datetime import date, timedelta

from sqlalchemy import text

from . import audit, identity, notify, projector, verification
from .claims import _enqueue_outbox, _event, next_seq
from .config import get_settings
from .core import dumps, now, uuid7
from .db import DbContext, SYSTEM, owner_tx, row, rows, scalar, tx

log = logging.getLogger("shikshasetu.ops")
LEASE_SECONDS = 60
HANDLERS: dict[str, callable] = {}

SCHEDULES = [  # (name, queue, type, interval seconds)  - REQ-OPS-02
    ("projector", "maintenance", "projector.tick", 2),
    ("audit_sealer", "maintenance", "audit.seal", 5),
    ("registry_revalidation", "identity", "identity.revalidate", 60),
    ("confirmation_deadlines", "notify", "confirmations.deadlines", 3600),
    ("matching", "matching", "matching.process", 5),
    ("orphan_scan", "matching", "transfers.orphan_scan", 86400),
    ("intel_pipeline", "intel", "intel.pipeline", 86400),
    ("retention", "maintenance", "maintenance.retention", 86400),
]


def handler(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


# ------------------------------------------------------------------ queue
def enqueue(queue: str, type_: str, payload: dict | None = None, *, dedupe_key: str | None = None, run_at=None,
            institution_id=None, max_attempts: int = 5) -> str | None:
    jid = uuid7()
    with owner_tx("SYSTEM") as conn:
        r = row(conn, "INSERT INTO ops.jobs (id, queue, type, payload, run_at, max_attempts, institution_id, dedupe_key) VALUES "
                      "(:id,:q,:t,CAST(:p AS jsonb),:r,:m,CAST(:i AS uuid),:d) ON CONFLICT DO NOTHING RETURNING id",
                id=jid, q=queue, t=type_, p=dumps(payload or {}), r=run_at or now(), m=max_attempts,
                i=str(institution_id) if institution_id else None, d=dedupe_key)
    return str(jid) if r else None


def run_job_inline(type_: str, payload: dict | None = None) -> dict:
    return HANDLERS[type_](payload or {}) or {}


def _claim(worker: str) -> dict | None:
    with owner_tx("SYSTEM") as conn:
        t = now()
        conn.execute(text("UPDATE ops.jobs SET status='QUEUED', locked_by=NULL, lease_expires_at=NULL WHERE status='RUNNING' AND lease_expires_at < :t"),
                     {"t": t})
        j = row(conn, "SELECT * FROM ops.jobs WHERE status='QUEUED' AND run_at <= :t ORDER BY priority, run_at LIMIT 1 FOR UPDATE SKIP LOCKED", t=t)
        if not j:
            return None
        conn.execute(text("UPDATE ops.jobs SET status='RUNNING', locked_by=:w, lease_expires_at=:l, attempts=attempts+1 WHERE id=:i"),
                     {"w": worker, "l": t + timedelta(seconds=LEASE_SECONDS), "i": j["id"]})
        return j


def _finish(j: dict, ok: bool, result: dict | None, error: str | None) -> None:
    with owner_tx("SYSTEM") as conn:
        if ok:
            conn.execute(text("UPDATE ops.jobs SET status='SUCCEEDED', result=CAST(:r AS jsonb), finished_at=:t, lease_expires_at=NULL WHERE id=:i"),
                         {"r": dumps(result or {}), "t": now(), "i": j["id"]})
        elif j["attempts"] + 1 >= j["max_attempts"]:
            conn.execute(text("UPDATE ops.jobs SET status='DEAD', last_error=:e, finished_at=:t WHERE id=:i"), {"e": error, "t": now(), "i": j["id"]})
        else:
            delay = min(2 ** (j["attempts"] + 1), 300) + random.random()
            conn.execute(text("UPDATE ops.jobs SET status='QUEUED', last_error=:e, run_at=:r, lease_expires_at=NULL, locked_by=NULL WHERE id=:i"),
                         {"e": error, "r": now() + timedelta(seconds=delay), "i": j["id"]})


def work_once(worker: str = "worker") -> bool:
    j = _claim(worker)
    if not j:
        return False
    try:
        result = HANDLERS[j["type"]](j["payload"]) or {}
        _finish(j, True, result, None)
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s failed", j["type"])
        _finish(j, False, None, f"{type(exc).__name__}: {exc}"[:500])
    return True


def schedule_tick() -> None:
    """Single-leader scheduler: enqueue due schedules. Guarded by an advisory lock so one process leads."""
    with owner_tx("SYSTEM") as conn:
        if not scalar(conn, "SELECT pg_try_advisory_xact_lock(7000002)"):
            return
        t = now()
        for s in rows(conn, "SELECT * FROM ops.schedules WHERE enabled AND next_run_at <= :t", t=t):
            conn.execute(text("INSERT INTO ops.jobs (id, queue, type, run_at, dedupe_key) VALUES (:id,:q,:t,:r,:d) ON CONFLICT DO NOTHING"),
                         {"id": uuid7(), "q": s["queue"], "t": s["job_type"], "r": t, "d": f"sched:{s['name']}"})
            conn.execute(text("UPDATE ops.schedules SET last_run_at=:t, next_run_at=:n WHERE name=:nm"),
                         {"t": t, "n": t + timedelta(seconds=s["interval_seconds"]), "nm": s["name"]})


def ensure_schedules() -> None:
    with owner_tx("SYSTEM") as conn:
        for name, queue, jt, interval in SCHEDULES:
            conn.execute(text("INSERT INTO ops.schedules (name, queue, job_type, interval_seconds, next_run_at) VALUES (:n,:q,:j,:i,now()) "
                              "ON CONFLICT (name) DO UPDATE SET queue=:q, job_type=:j, interval_seconds=:i"), {"n": name, "q": queue, "j": jt, "i": interval})


def sync_clock() -> None:
    """Pick up the demo clock offset (DEC-16) from ops.system_config so API and worker agree."""
    from . import core
    with owner_tx("SYSTEM") as conn:
        v = scalar(conn, "SELECT (value->>'offset_days')::int FROM ops.system_config WHERE key='demo_clock'")
    core.set_clock_offset((v or 0) * 86400)


def _worker_loop(wid: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            busy = work_once(wid)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            busy = False
        if not busy:
            time.sleep(1)


def worker_main(threads: int = 4) -> None:
    """Scheduler (single leader by advisory lock) plus N job threads so a long job (e.g. revalidation) never starves the projector."""
    wid = f"{socket.gethostname()}:{os.getpid()}"
    ensure_schedules()
    log.info("worker %s started with %d job threads", wid, threads)
    stop = threading.Event()
    for i in range(threads):
        threading.Thread(target=_worker_loop, args=(f"{wid}#{i}", stop), daemon=True).start()
    while not stop.is_set():
        try:
            sync_clock()
            schedule_tick()
        except Exception:  # noqa: BLE001
            log.exception("scheduler error")
        time.sleep(1)


# ------------------------------------------------------------------ handlers
@handler("projector.tick")
def _projector(_: dict) -> dict:
    n = 0
    while True:
        k = projector.project()
        n += k
        if k < projector.BATCH:
            break
    return {"events": n}


@handler("audit.seal")
def _seal(_: dict) -> dict:
    return {"sealed": audit.seal()}


def revalidate_enrolment(enrolment_id: str, source: str = "REVALIDATION") -> dict:
    """Re-check an enrolment against the registry using the vault copy of its data (REQ-IDN-10)."""
    with owner_tx("SYSTEM") as conn:
        e = row(conn, "SELECT id, student_id, institution_id, academic_year, status FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        if not e or e["status"] not in ("CLAIMED", "ADMITTED"):
            return {"skipped": True}
        pii = verification._vault(conn, str(e["student_id"]), str(e["institution_id"]))
    if not pii.get("apaar_id"):
        return {"skipped": True}
    from datetime import date as _d
    res = identity.registry_lookup(pii["apaar_id"])          # outside any transaction
    diag = identity.diagnose(res, pii["name"], _d.fromisoformat(pii["dob"]))
    if diag.status == "PENDING_VERIFICATION":
        return {"still_unavailable": True}
    with owner_tx("SYSTEM") as conn:
        lock2 = {"VERIFIED": "PASSED", "PROBABLE_MATCH": "PENDING", "PENDING_FIX": "FAILED", "INVALID": "FAILED"}[diag.status]
        v = row(conn, "SELECT lock3_status FROM core.enrolment_verification WHERE enrolment_id=:e FOR UPDATE", e=e["id"])
        overall = verification.overall_state(lock2, v["lock3_status"])
        fix_deadline = now() + timedelta(days=14) if lock2 == "FAILED" else None
        conn.execute(text("UPDATE core.enrolment_verification SET lock2_status=:l2, overall_state=:ov, identity_status=:ids, "
                          "fix_deadline_at=coalesce(:fd, fix_deadline_at), updated_at=:t WHERE enrolment_id=:e"),
                     {"l2": lock2, "ov": overall, "ids": diag.status if diag.status != "INVALID" else "PENDING_FIX", "fd": fix_deadline,
                      "t": now(), "e": e["id"]})
        conn.execute(text("UPDATE core.students SET identity_status=:s WHERE id=:i"),
                     {"s": diag.status if diag.status != "INVALID" else "PENDING_FIX", "i": e["student_id"]})
        conn.execute(text("INSERT INTO identity.registry_checks (id, enrolment_id, institution_id, source, status, name_score, dob_match, verdict, "
                          "latency_ms, breaker_state, checked_at) VALUES (:id,:e,:i,:s,:st,:ns,:dm,:v,:l,:b,:t)"),
                     {"id": uuid7(), "e": e["id"], "i": e["institution_id"], "s": source, "st": res.status, "ns": diag.name_score,
                      "dm": diag.dob_match, "v": diag.status, "l": res.latency_ms, "b": res.breaker, "t": now()})
        conn.execute(text("UPDATE identity.diagnoses SET resolved_at=:t WHERE enrolment_id=:e AND resolved_at IS NULL"), {"t": now(), "e": e["id"]})
        _event(conn, e["id"], e["institution_id"], next_seq(conn, e["id"]), "VERIFICATION_CHANGE", None, None,
               {"lock2": lock2, "overall": overall, "source": source})
        audit.log(conn, "identity.revalidated", entity_type="enrolment", entity_id=e["id"], institution_id=e["institution_id"],
                  payload={"verdict": diag.status, "overall": overall})
        _enqueue_outbox(conn, "verification.changed", "enrolment", e["id"], e["institution_id"],
                        {"academic_year": e["academic_year"], "overall": overall, "resolved": True})
    return {"verdict": diag.status, "overall": overall}


@handler("identity.revalidate")
def _revalidate(_: dict) -> dict:
    with owner_tx("SYSTEM") as conn:
        ids = [str(r["enrolment_id"]) for r in rows(conn, "SELECT enrolment_id FROM core.enrolment_verification v JOIN core.enrolments e ON e.id=v.enrolment_id "
                                                         "WHERE v.identity_status='PENDING_VERIFICATION' AND e.status IN ('CLAIMED','ADMITTED') LIMIT 200")]
    done = 0
    for i in ids:
        out = revalidate_enrolment(i)
        if out.get("still_unavailable"):
            break  # registry still down: stop and retry next tick (no long negative caching)
        done += 1
    return {"checked": done, "pending": len(ids) - done}


@handler("confirmations.deadlines")
def _deadlines(_: dict) -> dict:
    """REQ-TL-07: reminders at day 7 and 21; at the deadline Lock 3 becomes EXPIRED/UNCONFIRMED (flag only, seat stays occupied)."""
    t = now()
    expired = reminded = 0
    with owner_tx("SYSTEM") as conn:
        due = rows(conn, "SELECT v.enrolment_id, v.institution_id, v.lock2_status, v.reminders_sent, v.deadline_at, e.academic_year, e.student_id "
                         "FROM core.enrolment_verification v JOIN core.enrolments e ON e.id=v.enrolment_id "
                         "WHERE v.lock3_status='PENDING' AND e.status IN ('CLAIMED','ADMITTED')")
        for v in due:
            claimed_days = 30 - (v["deadline_at"] - t).total_seconds() / 86400
            if v["deadline_at"] <= t:
                conn.execute(text("UPDATE core.enrolment_verification SET lock3_status='EXPIRED', overall_state=:o, updated_at=:t WHERE enrolment_id=:e"),
                             {"o": verification.overall_state(v["lock2_status"], "EXPIRED"), "t": t, "e": v["enrolment_id"]})
                _event(conn, v["enrolment_id"], v["institution_id"], next_seq(conn, v["enrolment_id"]), "VERIFICATION_CHANGE", None, None,
                       {"lock3": "EXPIRED", "overall": "UNCONFIRMED"})
                _enqueue_outbox(conn, "verification.changed", "enrolment", v["enrolment_id"], v["institution_id"],
                                {"academic_year": v["academic_year"], "overall": "UNCONFIRMED"})
                audit.log(conn, "confirmation.deadline_passed", entity_type="enrolment", entity_id=v["enrolment_id"], institution_id=v["institution_id"],
                          payload={"policy": "flag only; seat stays occupied until a reviewer acts"})
                expired += 1
            elif (claimed_days >= 21 and v["reminders_sent"] < 2) or (claimed_days >= 7 and v["reminders_sent"] < 1):
                conn.execute(text("UPDATE core.enrolment_verification SET reminders_sent=reminders_sent+1 WHERE enrolment_id=:e"), {"e": v["enrolment_id"]})
                notify.record("INAPP", "REMINDER", "SENT", institution_id=v["institution_id"], enrolment_id=str(v["enrolment_id"]),
                              params={"deadline": v["deadline_at"].date().isoformat()})
                reminded += 1
    return {"expired": expired, "reminded": reminded}


# handler modules register themselves on import
from . import admin, intel, matching, reports, rulesets, uploads  # noqa: E402,F401
