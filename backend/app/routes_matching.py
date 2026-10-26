"""Duplicate review queue (AICTE) and transfers (institution). PRD 8.7. Candidates expose scores and institutions, never student data."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from . import claims, matching
from .core import ApiError
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal, ensure_uuid, secured
from .routes_claims import _in_scope, _require_institution

router = APIRouter(prefix="/api/v1", tags=["matching"])


class DupDecideIn(BaseModel):
    decision: str = Field(pattern="^(DISMISS|CONFIRM_DUP)$")
    reason: str = Field(min_length=5, max_length=500)


class TransferOutIn(BaseModel):
    effective_date: date | None = None
    note: str | None = Field(default=None, max_length=300)


@router.get("/duplicates")
def list_duplicates(p: Principal = Depends(secured("duplicates:review")), status: str = "REVIEW", timeline: str | None = None, min_score: float = Query(default=0.0, ge=0, le=1),
                    limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    where, params = ["c.status=:st", "c.score >= :ms"], {"st": status, "ms": min_score, "n": 2000}
    if timeline:
        where.append("c.timeline_class=:tl"); params["tl"] = timeline
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT c.id, c.score, c.field_scores, c.timeline_class, c.status, c.flags, c.decision_reason, c.case_id, ia.name AS institution_a, ia.state AS state_a, ia.region AS region_a, "
                          "ib.name AS institution_b, ib.state AS state_b, ib.region AS region_b, pa.name AS programme_a, pb.name AS programme_b, ea.academic_year AS year_a, eb.academic_year AS year_b, "
                          "ea.status AS status_a, eb.status AS status_b, ea.enrolment_ref AS ref_a, eb.enrolment_ref AS ref_b FROM matching.match_candidates c "
                          "JOIN ref.institutions ia ON ia.id=c.institution_a JOIN ref.institutions ib ON ib.id=c.institution_b JOIN core.enrolments ea ON ea.id=c.enrolment_a "
                          "JOIN core.enrolments eb ON eb.id=c.enrolment_b JOIN ref.programmes pa ON pa.id=ea.programme_id JOIN ref.programmes pb ON pb.id=eb.programme_id WHERE "
                          + " AND ".join(where) + " ORDER BY c.score DESC, c.id LIMIT :n", **params)
        counts = rows(conn, "SELECT status, count(*) n FROM matching.match_candidates GROUP BY 1")
    data = [d for d in data if _in_scope(p, {"region": d["region_a"], "state": d["state_a"]}) or _in_scope(p, {"region": d["region_b"], "state": d["state_b"]})]
    total = len(data)
    page = data[offset:offset + limit]
    return {"total": total, "counts": {c["status"]: c["n"] for c in counts}, "items": [{**d, "id": str(d["id"]), "score": float(d["score"]), "case_id": str(d["case_id"]) if d["case_id"] else None} for d in page],
            "privacy": "Pairs are shown as anonymised A/B with scores only. Revealing identities needs a case-gated elevation grant (a later phase)."}


@router.post("/duplicates/{cid}/decide")
def decide(cid: str, body: DupDecideIn, p: Principal = Depends(secured("duplicates:review"))):
    cid = ensure_uuid(cid, "candidate")
    with tx(SYSTEM) as conn:
        c = row(conn, "SELECT ia.state AS sa, ia.region AS ra, ib.state AS sb, ib.region AS rb FROM matching.match_candidates c JOIN ref.institutions ia ON ia.id=c.institution_a "
                      "JOIN ref.institutions ib ON ib.id=c.institution_b WHERE c.id=CAST(:c AS uuid)", c=cid)
    if not c or not (_in_scope(p, {"region": c["ra"], "state": c["sa"]}) or _in_scope(p, {"region": c["rb"], "state": c["sb"]})):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such candidate.")
    with tx(p.db_ctx()) as conn:
        return matching.decide(conn, p, cid, body.decision, body.reason)


@router.post("/enrolments/{eid}/transfer-out")
def transfer_out(eid: str, body: TransferOutIn, p: Principal = Depends(secured("transfers:write"))):
    _require_institution(p)
    return claims.withdraw(p, ensure_uuid(eid, "enrolment"), "TRANSFER", body.effective_date, body.note, transfer_out=True)


@router.get("/transfers")
def list_transfers(p: Principal = Depends(secured("enrolments:read"))):
    inst = _require_institution(p)
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT t.id, t.status, t.effective_date_out, t.orphan_after, t.created_at, oe.enrolment_ref AS out_ref, ie.enrolment_ref AS in_ref, "
                          "oi.name AS out_institution, ii.name AS in_institution, (t.out_institution_id=CAST(:i AS uuid)) AS we_released FROM core.transfers t "
                          "LEFT JOIN core.enrolments oe ON oe.id=t.out_enrolment_id LEFT JOIN core.enrolments ie ON ie.id=t.in_enrolment_id "
                          "LEFT JOIN ref.institutions oi ON oi.id=t.out_institution_id LEFT JOIN ref.institutions ii ON ii.id=t.in_institution_id ORDER BY t.created_at DESC LIMIT 200", i=inst)
    return {"items": [{**d, "id": str(d["id"])} for d in data]}
