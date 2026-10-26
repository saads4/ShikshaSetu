"""Dashboards (REQ-DASH). AICTE views read ONLY agg.* (+ reference data); institution views read their own tenant data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from . import claims
from .core import ApiError, now
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal, secured
from .routes_claims import _scope_institution

router = APIRouter(prefix="/api/v1/dashboards", tags=["dashboards"])

SMALL_CELL_MIN = 5
DEFINITIONS = {
    "occupied": "Occupied = seats whose enrolment is CLAIMED or ADMITTED.",
    "cumulative_admitted": "Cumulative admitted = enrolments ever ADMITTED; never reduced by withdrawals.",
    "verified": "Verified = occupied, with all three locks agreeing (institution, registry, student).",
}


def _scope_sql(p: Principal, alias: str) -> tuple[str, dict]:
    if p.scope_type == "REGION":
        return f" AND {alias}.region = :scope_value", {"scope_value": p.scope_value}
    if p.scope_type == "STATE":
        return f" AND {alias}.state = :scope_value", {"scope_value": p.scope_value}
    return "", {}


def _as_of(conn):
    return scalar(conn, "SELECT as_of FROM agg.projector_state WHERE name='main'")


def _suppress(value: int):
    """REQ-DASH-05: groups below the small-cell minimum are never shown."""
    return {"suppressed": True} if 0 < value < SMALL_CELL_MIN else value


@router.get("/institution")
def institution_dashboard(p: Principal = Depends(secured("dashboards:institution")), institution_id: str | None = None, year: str | None = None):
    inst = _scope_institution(p, institution_id)
    year = year or claims.current_academic_year()
    with tx(SYSTEM) as conn:
        info = row(conn, "SELECT id, name, state, district, region, type FROM ref.institutions WHERE id=CAST(:i AS uuid)", i=inst)
        seats = rows(conn, "SELECT programme_id, programme_name, level, bucket_type, cap, approved_extra, occupied, cumulative_admitted, pct FROM agg.seat_utilisation "
                           "WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y ORDER BY programme_name, bucket_type", i=inst, y=year)
        funnel = row(conn, "SELECT total, pending, verified, unconfirmed, disputed, pending_fix, pending_registry, provisional, low_trust_channel "
                           "FROM agg.verification_funnel WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y", i=inst, y=year) or {}
        fsum = rows(conn, "SELECT severity, status, count FROM agg.findings_summary WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y", i=inst, y=year)
        daily = rows(conn, "SELECT day, claims, conflicts, seat_limit, invalid FROM agg.claim_daily WHERE institution_id=CAST(:i AS uuid) "
                           "AND day >= (now() - interval '30 days')::date ORDER BY day", i=inst)
        as_of = _as_of(conn)
    if not p.institution_id:  # AICTE sees aggregates only; student-level lists need a case-gated grant (REQ-ACL-05)
        return {"institution": {**info, "id": str(info["id"])}, "academic_year": year, "as_of": as_of, "definitions": DEFINITIONS,
                "seats": [{**s, "programme_id": str(s["programme_id"]), "pct": float(s["pct"])} for s in seats], "funnel": funnel,
                "findings": fsum, "claims_by_day": daily, "awaiting_confirmation": [], "stale_claims": None, "notifications": [],
                "aggregate_only": True}
    with tx(p.db_ctx()) as conn:
        awaiting = rows(conn, "SELECT e.id, e.enrolment_ref, e.institution_student_id, v.deadline_at, v.overall_state FROM core.enrolments e "
                              "JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.institution_id=CAST(:i AS uuid) AND e.academic_year=:y "
                              "AND e.status IN ('CLAIMED','ADMITTED') AND v.lock3_status='PENDING' ORDER BY v.deadline_at LIMIT 10", i=inst, y=year)
        stale = scalar(conn, "SELECT count(*) FROM core.enrolments WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y AND status='CLAIMED' "
                             "AND claimed_at < :t", i=inst, y=year, t=now().replace(microsecond=0) - __import__("datetime").timedelta(days=45))
        notes = rows(conn, "SELECT id, channel, template_code, status, created_at FROM ops.notifications WHERE institution_id=CAST(:i AS uuid) "
                           "ORDER BY created_at DESC LIMIT 8", i=inst)
    return {"institution": {**info, "id": str(info["id"])}, "academic_year": year, "as_of": as_of, "definitions": DEFINITIONS,
            "seats": [{**s, "programme_id": str(s["programme_id"]), "pct": float(s["pct"])} for s in seats], "funnel": funnel,
            "findings": fsum, "claims_by_day": daily, "awaiting_confirmation": [{**a, "id": str(a["id"])} for a in awaiting],
            "stale_claims": stale, "notifications": [{**n, "id": str(n["id"])} for n in notes]}


@router.get("/aicte/overview")
def aicte_overview(p: Principal = Depends(secured("dashboards:aicte")), year: str | None = None):
    year = year or claims.current_academic_year()
    ssql, sparams = _scope_sql(p, "i")
    with tx(SYSTEM) as conn:
        k = row(conn, "SELECT count(DISTINCT u.institution_id) AS institutions, coalesce(sum(u.cap + u.approved_extra),0) AS seats, "
                      "coalesce(sum(u.occupied),0) AS occupied, coalesce(sum(u.cumulative_admitted),0) AS admitted, "
                      "count(*) FILTER (WHERE u.pct >= 100) AS buckets_full, count(*) FILTER (WHERE u.pct >= 90 AND u.pct < 100) AS buckets_near "
                      "FROM agg.seat_utilisation u JOIN ref.institutions i ON i.id=u.institution_id WHERE u.academic_year=:y" + ssql, y=year, **sparams)
        f = row(conn, "SELECT coalesce(sum(total),0) AS total, coalesce(sum(verified),0) AS verified, coalesce(sum(pending),0) AS pending, "
                      "coalesce(sum(unconfirmed),0) AS unconfirmed, coalesce(sum(disputed),0) AS disputed, coalesce(sum(pending_fix),0) AS pending_fix, "
                      "coalesce(sum(pending_registry),0) AS pending_registry, coalesce(sum(provisional),0) AS provisional, "
                      "coalesce(sum(low_trust_channel),0) AS low_trust FROM agg.verification_funnel f JOIN ref.institutions i ON i.id=f.institution_id "
                      "WHERE f.academic_year=:y" + ssql, y=year, **sparams)
        c = row(conn, "SELECT coalesce(sum(claims),0) AS claims, coalesce(sum(conflicts),0) AS conflicts, coalesce(sum(seat_limit),0) AS seat_limit, "
                      "coalesce(sum(invalid),0) AS invalid FROM agg.claim_daily d JOIN ref.institutions i ON i.id=d.institution_id "
                      "WHERE d.day >= (now() - interval '365 days')::date" + ssql, **sparams)
        states = rows(conn, "SELECT i.state, i.region, count(DISTINCT u.institution_id) AS institutions, sum(u.cap + u.approved_extra) AS seats, "
                            "sum(u.occupied) AS occupied, round(100.0 * sum(u.occupied) / greatest(sum(u.cap + u.approved_extra),1), 1) AS pct "
                            "FROM agg.seat_utilisation u JOIN ref.institutions i ON i.id=u.institution_id WHERE u.academic_year=:y" + ssql +
                            " GROUP BY i.state, i.region ORDER BY pct DESC", y=year, **sparams)
        as_of = _as_of(conn)
    for s in states:
        s["pct"] = float(s["pct"])
        s["institutions"] = _suppress(s["institutions"])
    return {"academic_year": year, "as_of": as_of, "kpis": {**k, **f, **c}, "states": states, "definitions": DEFINITIONS,
            "scope": {"type": p.scope_type, "value": p.scope_value}}


@router.get("/aicte/utilisation")
def aicte_utilisation(p: Principal = Depends(secured("dashboards:aicte")), year: str | None = None, state: str | None = None, region: str | None = None,
                      level: str | None = None, institution_type: str | None = None, band: str | None = None, q: str | None = Query(default=None, max_length=60),
                      sort: str = "pct", limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    """Seat-utilisation explorer. Sorting is limited to an allow-list (DEC-27); offset paging is used here because the
    sort key is derived (pct) - keyset on pct+id is applied to the institution lists in later phases."""
    year = year or claims.current_academic_year()
    ssql, params = _scope_sql(p, "u")
    params.update(y=year, n=limit, o=offset)
    where = "u.academic_year=:y" + ssql
    for col, val in (("state", state), ("region", region), ("level", level), ("institution_type", institution_type)):
        if val:
            where += f" AND u.{col}=:{col}"; params[col] = val
    if q:
        where += " AND (u.institution_name ILIKE :q OR u.programme_name ILIKE :q)"; params["q"] = f"%{q}%"
    bands = {"FULL": "u.pct >= 100", "NEAR": "u.pct >= 90 AND u.pct < 100", "HEALTHY": "u.pct >= 50 AND u.pct < 90", "LOW": "u.pct < 50"}
    if band:
        if band not in bands:
            raise ApiError(422, "VALIDATION_ERROR", "Unknown band", "Use FULL, NEAR, HEALTHY or LOW.")
        where += f" AND {bands[band]}"
    order = {"pct": "u.pct DESC", "occupied": "u.occupied DESC", "institution": "u.institution_name", "cap": "u.cap DESC"}.get(sort)
    if not order:
        raise ApiError(422, "VALIDATION_ERROR", "Unsupported sort", "Sort by pct, occupied, institution or cap.")
    with tx(SYSTEM) as conn:
        data = rows(conn, f"SELECT u.institution_id, u.institution_name, u.state, u.region, u.institution_type, u.programme_id, u.programme_name, u.level, "
                          f"u.bucket_type, u.cap, u.approved_extra, u.occupied, u.pct FROM agg.seat_utilisation u WHERE {where} "
                          f"ORDER BY {order}, u.programme_id LIMIT :n OFFSET :o", **params)
        total = scalar(conn, f"SELECT count(*) FROM agg.seat_utilisation u WHERE {where}", **{k: v for k, v in params.items() if k not in ("n", "o")})
        as_of = _as_of(conn)
    return {"as_of": as_of, "total": total, "items": [{**d, "institution_id": str(d["institution_id"]), "programme_id": str(d["programme_id"]),
                                                      "pct": float(d["pct"])} for d in data]}


@router.get("/aicte/trends")
def aicte_trends(p: Principal = Depends(secured("dashboards:aicte")), days: int = Query(default=60, ge=7, le=365)):
    ssql, params = _scope_sql(p, "i")
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT d.day, sum(d.claims) AS claims, sum(d.conflicts) AS conflicts, sum(d.seat_limit) AS seat_limit "
                          "FROM agg.claim_daily d JOIN ref.institutions i ON i.id=d.institution_id WHERE d.day >= (now() - make_interval(days => :d))::date"
                          + ssql + " GROUP BY d.day ORDER BY d.day", d=days, **params)
        as_of = _as_of(conn)
    cum = 0
    for d in data:
        cum += int(d["claims"])
        d["claims"], d["conflicts"], d["seat_limit"], d["cumulative"] = int(d["claims"]), int(d["conflicts"]), int(d["seat_limit"]), cum
    return {"as_of": as_of, "items": data}
