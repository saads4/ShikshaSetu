"""Alerts, Trust Index, cases, appeals, elevations (PRD 8.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from . import cases, claims, intel, ops
from .core import ApiError
from .db import SYSTEM, row, rows, tx
from .deps import Principal, ensure_uuid, secured

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class MessageIn(BaseModel):
    message: str = Field(min_length=5, max_length=1000)


class CloseIn(BaseModel):
    outcome_code: str
    notes: str = Field(min_length=5, max_length=1000)


class AppealIn(BaseModel):
    grounds: str = Field(min_length=20, max_length=2000)


class AppealDecideIn(BaseModel):
    decision: str = Field(pattern="^(UPHELD|OVERTURNED)$")
    notes: str = Field(min_length=5, max_length=1000)


class ElevationIn(BaseModel):
    case_id: str
    justification: str = Field(min_length=30, max_length=1000)
    duration_minutes: int = Field(default=30, ge=5, le=240)


# ------------------------------------------------------------------ Trust Index
@router.get("/trust")
def trust_board(p: Principal = Depends(secured("dashboards:aicte")), year: str | None = None, band: str | None = None, q: str | None = Query(default=None, max_length=60),
                limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    year = year or claims.current_academic_year()
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT DISTINCT ON (t.institution_id) t.institution_id, t.score, t.band, t.computed_at, t.config_version, i.name AS institution_name, i.state, i.region, i.type FROM intel.trust_scores t "
                          "JOIN ref.institutions i ON i.id=t.institution_id WHERE t.academic_year=:y ORDER BY t.institution_id, t.computed_date DESC", y=year)
        open_alerts = {r["institution_id"]: r["n"] for r in rows(conn, "SELECT institution_id, count(*) AS n FROM intel.anomaly_alerts WHERE status IN ('OPEN','IN_CASE') GROUP BY 1")}
    data = [d for d in data if cases.in_scope(p, d) and (not band or d["band"] == band) and (not q or q.lower() in d["institution_name"].lower())]
    data.sort(key=lambda d: d["score"])
    return {"notice": "The Trust Index only prioritises reviews. It triggers no penalty and is not a verdict.", "total": len(data), "config_version": intel.CONFIG_VERSION,
            "items": [{**d, "institution_id": str(d["institution_id"]), "score": float(d["score"]), "open_alerts": open_alerts.get(d["institution_id"], 0)} for d in data[offset:offset + limit]]}


@router.get("/trust/{institution_id}")
def trust_detail(institution_id: str, p: Principal = Depends(secured("trust:read")), year: str | None = None):
    institution_id = ensure_uuid(institution_id, "institution")
    year = year or claims.current_academic_year()
    with tx(SYSTEM) as conn:
        i = row(conn, "SELECT id, name, state, region FROM ref.institutions WHERE id=CAST(:i AS uuid)", i=institution_id)
        if not i or not cases.in_scope(p, {"institution_id": i["id"], "region": i["region"], "state": i["state"]}):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such institution.")
        hist = rows(conn, "SELECT computed_date, score, band, components, config_version FROM intel.trust_scores WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y ORDER BY computed_date DESC LIMIT 30", i=institution_id, y=year)
        alerts = rows(conn, "SELECT id, layer, type, severity, status, created_at FROM intel.anomaly_alerts WHERE institution_id=CAST(:i AS uuid) AND status IN ('OPEN','IN_CASE') ORDER BY created_at DESC", i=institution_id)
    if not hist:
        raise ApiError(404, "NOT_FOUND", "No score yet", "The Trust Index has not been computed for this institution.")
    cur = hist[0]
    return {"institution": {"id": str(i["id"]), "name": i["name"]}, "academic_year": year, "score": float(cur["score"]), "band": cur["band"], "components": cur["components"], "config_version": cur["config_version"],
            "history": [{"date": h["computed_date"], "score": float(h["score"])} for h in reversed(hist)], "open_alerts": [{**a, "id": str(a["id"])} for a in alerts] if not p.institution_id else [],
            "notice": "This score only prioritises reviews; it triggers no penalty."}


@router.post("/intel/run-now")
def run_now(p: Principal = Depends(secured("cases:manage"))):
    """On-demand run of the nightly pipeline; idempotent per (institution, year, date)."""
    return intel.run_all()


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(p: Principal = Depends(secured("dashboards:aicte")), status: str | None = "OPEN", layer: str | None = None, severity: str | None = None,
                limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return cases.list_alerts(p, status or None, layer, severity, limit, offset)


@router.get("/alerts/{aid}")
def alert(aid: str, p: Principal = Depends(secured("dashboards:aicte"))):
    return cases.alert_detail(p, ensure_uuid(aid, "alert"))


@router.post("/alerts/{aid}/dismiss")
def dismiss(aid: str, body: ReasonIn, p: Principal = Depends(secured("cases:manage"))):
    return cases.dismiss_alert(p, ensure_uuid(aid, "alert"), body.reason)


@router.post("/alerts/{aid}/open-case")
def open_case(aid: str, p: Principal = Depends(secured("cases:manage"))):
    return cases.case_from_alert(p, ensure_uuid(aid, "alert"))


# ------------------------------------------------------------------ cases (AICTE)
@router.get("/cases")
def list_cases(p: Principal = Depends(secured("cases:manage")), status: str | None = None, type: str | None = None, q: str | None = Query(default=None, max_length=60),
               limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return cases.list_cases(p, status, type, q, limit, offset)


@router.get("/cases/{cid}")
def case_detail(cid: str, p: Principal = Depends(secured("cases:manage"))):
    return cases.detail(p, ensure_uuid(cid, "case"))


@router.post("/cases/{cid}/request-response")
def request_response(cid: str, body: MessageIn, p: Principal = Depends(secured("cases:manage"))):
    return cases.request_response(p, ensure_uuid(cid, "case"), body.message)


@router.post("/cases/{cid}/start-review")
def start_review(cid: str, p: Principal = Depends(secured("cases:manage"))):
    return cases.start_review(p, ensure_uuid(cid, "case"))


@router.post("/cases/{cid}/close")
def close(cid: str, body: CloseIn, p: Principal = Depends(secured("cases:manage"))):
    return cases.close(p, ensure_uuid(cid, "case"), body.outcome_code, body.notes)


@router.post("/cases/{cid}/release-seat")
def release_seat(cid: str, body: ReasonIn, p: Principal = Depends(secured("seats:release"))):
    return cases.release_seat(p, ensure_uuid(cid, "case"), body.reason)


@router.post("/cases/{cid}/appeal/decide")
def decide_appeal(cid: str, body: AppealDecideIn, p: Principal = Depends(secured("appeals:decide"))):
    return cases.decide_appeal(p, ensure_uuid(cid, "case"), body.decision, body.notes)


@router.post("/cases/{cid}/reveal")
def reveal(cid: str, p: Principal = Depends(secured("students:reveal"))):
    return cases.reveal_case(p, ensure_uuid(cid, "case"))


@router.post("/duplicates/{cid}/reveal")
def reveal_dup(cid: str, p: Principal = Depends(secured("students:reveal"))):
    return cases.reveal_duplicate(p, ensure_uuid(cid, "candidate"))


@router.post("/me/elevations")
def elevate(body: ElevationIn, p: Principal = Depends(secured("elevations:request"))):
    return cases.request_elevation(p, ensure_uuid(body.case_id, "case"), body.justification, body.duration_minutes)


@router.get("/me/elevations")
def elevations(p: Principal = Depends(secured("elevations:request"))):
    return cases.my_grants(p)


# ------------------------------------------------------------------ cases (institution)
@router.get("/institution/cases")
def inst_cases(p: Principal = Depends(secured("cases:respond")), status: str | None = None):
    return cases.list_cases(p, status, None, None, 100, 0)


@router.get("/institution/cases/{cid}")
def inst_case(cid: str, p: Principal = Depends(secured("cases:respond"))):
    return cases.detail(p, ensure_uuid(cid, "case"))


@router.post("/institution/cases/{cid}/respond")
def inst_respond(cid: str, body: MessageIn, p: Principal = Depends(secured("cases:respond"))):
    return cases.respond(p, ensure_uuid(cid, "case"), body.message)


@router.post("/institution/cases/{cid}/appeal")
def inst_appeal(cid: str, body: AppealIn, p: Principal = Depends(secured("appeals:file"))):
    return cases.appeal(p, ensure_uuid(cid, "case"), body.grounds)
