"""Submissions, commitments, window extensions, finding waivers (PRD 8.6, 8.9)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import audit, submissions as sub
from .claims import current_academic_year
from .core import ApiError, now
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal, ensure_uuid, secured
from .routes_claims import _in_scope, _require_institution

router = APIRouter(prefix="/api/v1", tags=["submissions"])


class CreateIn(BaseModel):
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class DeclaredIn(BaseModel):
    declared: dict[str, int]


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(ACCEPT|REQUEST_REVIEW)$")
    notes: str | None = Field(default=None, max_length=1000)


class ExtensionIn(BaseModel):
    new_closes_at: datetime
    reason: str = Field(min_length=10, max_length=500)
    academic_year: str | None = None


class ExtDecideIn(BaseModel):
    approve: bool
    reason: str = Field(min_length=5, max_length=500)


class WaiveIn(BaseModel):
    reason_code: str = Field(pattern="^(DATA_VERIFIED|LEGACY_RECORD|POLICY_EXCEPTION|FALSE_POSITIVE)$")
    note: str = Field(min_length=5, max_length=500)


@router.get("/submissions")
def list_submissions(p: Principal = Depends(secured("dashboards:institution")), status: str | None = None, year: str | None = None):
    year = year or current_academic_year()
    where, params = ["s.academic_year=:y"], {"y": year}
    if p.institution_id:
        where.append("s.institution_id=CAST(:i AS uuid)"); params["i"] = p.institution_id
    if status:
        where.append("s.status=:st"); params["st"] = status
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT s.id, s.status, s.late_flag, s.submitted_at, s.approved_at, s.locked_at, i.name AS institution_name, i.state, i.region, w.label, w.closes_at "
                          "FROM ingest.submissions s JOIN ref.institutions i ON i.id=s.institution_id JOIN ref.submission_windows w ON w.id=s.window_id WHERE "
                          + " AND ".join(where) + " ORDER BY s.submitted_at DESC NULLS LAST, i.name LIMIT 300", **params)
        window = sub.window_for(conn, year)
        ext = None
        if p.institution_id:
            ext = sub.effective_close(conn, p.institution_id, window)
    if not p.institution_id:
        data = [d for d in data if _in_scope(p, d)]
    return {"items": [{**d, "id": str(d["id"])} for d in data], "window": {**window, "id": str(window["id"]), "effective_closes_at": ext}}


@router.post("/submissions")
def create_submission(body: CreateIn, p: Principal = Depends(secured("submissions:validate"))):
    _require_institution(p)
    return sub.create(p, body.academic_year)


@router.get("/submissions/{sid}")
def submission_detail(sid: str, p: Principal = Depends(secured("dashboards:institution"))):
    sid = ensure_uuid(sid, "submission")
    with tx(p.db_ctx()) as conn:
        s = sub.get_submission(conn, sid, p)
        rep = sub.readiness(conn, s)
        w = sub.window_for(conn, s["academic_year"])
        close = sub.effective_close(conn, str(s["institution_id"]), w)
        hist = rows(conn, "SELECT h.from_status, h.to_status, h.note, h.at, u.username AS actor FROM ingest.submission_history h LEFT JOIN iam.users u ON u.id=h.actor_id "
                          "WHERE h.submission_id=:s ORDER BY h.id", s=s["id"])
        counts = row(conn, "SELECT count(*) FILTER (WHERE status='OPEN' AND severity IN ('Blocker','Error')) AS blocking, count(*) FILTER (WHERE status='OPEN' AND severity='Warning') AS warnings "
                           "FROM rules.findings WHERE institution_id=:i AND academic_year=:y", i=s["institution_id"], y=s["academic_year"])
        com = row(conn, "SELECT id, version, merkle_root, leaf_count, status, institution_kid, aicte_kid FROM audit.cohort_commitments WHERE submission_id=:s ORDER BY version DESC LIMIT 1", s=s["id"])
        ext = rows(conn, "SELECT id, new_closes_at, status, reason FROM ref.window_extensions WHERE institution_id=:i AND window_id=:w ORDER BY created_at DESC", i=s["institution_id"], w=w["id"])
    return {"id": sid, "status": s["status"], "institution_name": s["institution_name"], "academic_year": s["academic_year"], "declared": s["declared"], "late_flag": s["late_flag"],
            "review_notes": s["review_notes"], "window": {"label": w["label"], "opens_at": w["opens_at"], "closes_at": w["closes_at"], "effective_closes_at": close, "cutoff_at": w["cutoff_at"],
                                                         "open": w["opens_at"] <= now() <= close},
            "readiness": rep, "blocking_findings": counts["blocking"], "warnings_open": counts["warnings"], "history": hist,
            "commitment": {**com, "id": str(com["id"])} if com else None, "extensions": [{**x, "id": str(x["id"])} for x in ext]}


@router.put("/submissions/{sid}/declared")
def declared(sid: str, body: DeclaredIn, p: Principal = Depends(secured("submissions:validate"))):
    return sub.set_declared(p, ensure_uuid(sid, "submission"), body.declared)


@router.post("/submissions/{sid}/validate")
def validate(sid: str, p: Principal = Depends(secured("submissions:validate"))):
    return sub.validate(p, ensure_uuid(sid, "submission"))


@router.post("/submissions/{sid}/submit")
def submit(sid: str, p: Principal = Depends(secured("submissions:submit"))):
    return sub.submit(p, ensure_uuid(sid, "submission"))


@router.post("/submissions/{sid}/corrected")
def corrected(sid: str, p: Principal = Depends(secured("submissions:submit"))):
    return sub.to_corrected(p, ensure_uuid(sid, "submission"))


@router.post("/submissions/{sid}/decide")
def decide(sid: str, body: DecideIn, p: Principal = Depends(secured("submissions:decide"))):
    return sub.decide(p, ensure_uuid(sid, "submission"), body.decision, body.notes)


@router.post("/submissions/{sid}/commitment/sign")
def sign(sid: str, p: Principal = Depends(secured("commitments:sign"))):
    return sub.sign_commitment(p, ensure_uuid(sid, "submission"))


@router.post("/submissions/{sid}/commitment/countersign")
def countersign(sid: str, p: Principal = Depends(secured("commitments:countersign"))):
    return sub.countersign(p, ensure_uuid(sid, "submission"))


@router.get("/submissions/{sid}/commitment")
def commitment(sid: str, p: Principal = Depends(secured("dashboards:institution"))):
    return sub.get_commitment(p, ensure_uuid(sid, "submission"))


@router.post("/submissions/{sid}/commitment/verify")
def verify(sid: str, p: Principal = Depends(secured("audit:read"))):
    return sub.verify_commitment(p, ensure_uuid(sid, "submission"))


@router.get("/enrolments/{eid}/inclusion-proof")
def proof_for_institution(eid: str, p: Principal = Depends(secured("enrolments:read"))):
    eid = ensure_uuid(eid, "enrolment")
    with tx(p.db_ctx()) as conn:
        if not row(conn, "SELECT 1 AS x FROM core.enrolments WHERE id=CAST(:e AS uuid) AND institution_id=CAST(:i AS uuid)", e=eid, i=_require_institution(p)):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        return sub.inclusion_proof(conn, eid)


# ------------------------------------------------------------------ window extensions
@router.post("/window-extensions")
def request_ext(body: ExtensionIn, p: Principal = Depends(secured("extensions:request"))):
    _require_institution(p)
    return sub.request_extension(p, body.new_closes_at, body.reason, body.academic_year)


@router.get("/window-extensions")
def list_ext(p: Principal = Depends(secured("dashboards:institution"))):
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT x.id, x.new_closes_at, x.reason, x.status, x.decision_reason, x.created_at, i.name AS institution_name, i.state, i.region FROM ref.window_extensions x "
                          "JOIN ref.institutions i ON i.id=x.institution_id ORDER BY x.created_at DESC LIMIT 200")
    if not p.institution_id:
        data = [d for d in data if _in_scope(p, d)]
    else:
        data = [d for d in data]
    return {"items": [{**d, "id": str(d["id"])} for d in data]}


@router.post("/window-extensions/{xid}/decide")
def decide_ext(xid: str, body: ExtDecideIn, p: Principal = Depends(secured("extensions:decide"))):
    return sub.decide_extension(p, ensure_uuid(xid, "request"), body.approve, body.reason)


# ------------------------------------------------------------------ findings: acknowledge all, AICTE waive
@router.post("/findings/acknowledge-all")
def ack_all(p: Principal = Depends(secured("findings:ack"))):
    with tx(p.db_ctx()) as conn:
        n = conn.execute(text("UPDATE rules.findings SET status='ACKNOWLEDGED', acknowledged_by=CAST(:u AS uuid) WHERE institution_id=CAST(:i AS uuid) AND status='OPEN' AND severity IN ('Warning','Info')"),
                         {"u": p.id, "i": _require_institution(p)}).rowcount
        audit.log(conn, "finding.acknowledged_bulk", entity_type="finding", actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"count": n})
    return {"acknowledged": n}


@router.get("/aicte/findings")
def aicte_findings(p: Principal = Depends(secured("findings:waive")), severity: str | None = None, limit: int = Query(default=100, ge=1, le=300)):
    """Open Blocker/Error findings in the reviewer's scope. Identifiers only; no student data."""
    sev = "AND f.severity=:sv" if severity in ("Blocker", "Error") else "AND f.severity IN ('Blocker','Error')"
    with tx(SYSTEM) as conn:
        data = rows(conn, f"SELECT f.id, f.rule_code, f.severity, f.message, f.created_at, i.name AS institution_name, i.state, i.region, e.enrolment_ref FROM rules.findings f "
                          f"JOIN ref.institutions i ON i.id=f.institution_id LEFT JOIN core.enrolments e ON e.id=f.enrolment_id WHERE f.status='OPEN' {sev} ORDER BY f.created_at DESC LIMIT 1000",
                    **({"sv": severity} if severity in ("Blocker", "Error") else {}))
    data = [d for d in data if _in_scope(p, d)][:limit]
    return {"items": [{**d, "id": str(d["id"])} for d in data]}


@router.post("/findings/{fid}/waive")
def waive(fid: str, body: WaiveIn, p: Principal = Depends(secured("findings:waive"))):
    fid = ensure_uuid(fid, "finding")
    with tx(p.db_ctx()) as conn:
        f = row(conn, "SELECT f.id, f.institution_id, f.severity, i.state, i.region FROM rules.findings f JOIN ref.institutions i ON i.id=f.institution_id WHERE f.id=CAST(:f AS uuid) AND f.status='OPEN'", f=fid)
        if not f or not _in_scope(p, f):
            raise ApiError(404, "NOT_FOUND", "Not found", "No open finding with that id.")
        conn.execute(text("UPDATE rules.findings SET status='WAIVED', waived_by=CAST(:u AS uuid), waiver_reason_code=:c, waiver_note=:n, resolved_at=:t WHERE id=:f"),
                     {"u": p.id, "c": body.reason_code, "n": body.note, "t": now(), "f": f["id"]})
        audit.log(conn, "finding.waived", entity_type="finding", entity_id=f["id"], actor_id=p.id, actor_role=p.role, institution_id=f["institution_id"], justification=body.note,
                  request_id=p.request_id, payload={"reason_code": body.reason_code})
    return {"id": fid, "status": "WAIVED"}
