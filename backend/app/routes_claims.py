"""Institution-side endpoints: claims, enrolments, seat ledger, exceptions, findings, identity fixes (PRD 8.4, 8.6)."""
from __future__ import annotations

import base64
import json
from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import audit, claims, crypto, identity, keys, ops, verification
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal, ensure_uuid, secured

router = APIRouter(prefix="/api/v1", tags=["institution"])


def _json(status: int, body: dict, replay: bool = False) -> JSONResponse:
    headers = {"Idempotent-Replay": "true"} if replay else {}
    return JSONResponse(body, status_code=status, headers=headers,
                        media_type="application/problem+json" if status >= 400 else "application/json")


def _require_institution(p: Principal) -> str:
    if not p.institution_id:
        raise ApiError(403, "FORBIDDEN", "Not permitted", "This action needs an institution account.")
    return p.institution_id


# ------------------------------------------------------------------ claims
@router.post("/claims")
def create_claim(body: claims.ClaimIn, request: Request, p: Principal = Depends(secured("claims:write")),
                 idempotency_key: str | None = Header(default=None)):
    _require_institution(p)
    state, stored = claims.idem_begin(p, idempotency_key, "POST /claims", json.loads(body.model_dump_json()))
    if state == "replay":
        return _json(stored["status"], stored["body"], replay=True)
    try:
        status, out = claims.process_claim(p, body, dry_run=False, source="API" if p.kind == "client" else "PORTAL")
    except BaseException:
        claims.idem_finish(p, idempotency_key, 500, {})  # release the key so the client may retry
        raise
    out["request_id"] = p.request_id
    claims.idem_finish(p, idempotency_key, status, out)
    return _json(status, out)


@router.post("/claims:validate")
def validate_claim(body: claims.ClaimIn, p: Principal = Depends(secured("claims:validate"))):
    """Dry-run: all claim-path logic and registry diagnosis with no write and no seat consumed (REQ-GATE-13)."""
    _require_institution(p)
    status, out = claims.process_claim(p, body, dry_run=True)
    out["request_id"] = p.request_id
    out["dry_run"] = True
    return _json(status, out)


class IdentityCheckIn(BaseModel):
    apaar_id: str
    name: str
    dob: date


@router.post("/identity/check")
def identity_check(body: IdentityCheckIn, p: Principal = Depends(secured("claims:validate"))):
    hyg = identity.clean_apaar(body.apaar_id)
    if hyg.issues:
        return {"status": "INVALID", "messages": hyg.issues, "stored": False}
    res = identity.registry_lookup(hyg.value)
    d = identity.diagnose(res, body.name, body.dob)
    with tx(p.db_ctx()) as conn:
        conn.execute(text("INSERT INTO identity.registry_checks (id, institution_id, source, status, name_score, dob_match, verdict, latency_ms, "
                          "breaker_state, checked_at) VALUES (:id, CAST(:i AS uuid), 'CHECK_ONLY', :s, :ns, :dm, :v, :l, :b, :t)"),
                     {"id": uuid7(), "i": p.institution_id, "s": res.status, "ns": d.name_score, "dm": d.dob_match, "v": d.status,
                      "l": res.latency_ms, "b": res.breaker, "t": now()})
    return {"status": d.status, "name_score": d.name_score, "dob_match": d.dob_match, "messages": d.messages, "stored": False,
            "source": "MOCK_APAAR_REGISTRY"}


# ------------------------------------------------------------------ enrolments
def _cursor(sort_val, id_) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(sort_val), str(id_)]).encode()).decode()


def _decode_cursor(c: str | None):
    if not c:
        return None
    try:
        a, b = json.loads(base64.urlsafe_b64decode(c.encode()))
        return a, b
    except Exception:
        raise ApiError(400, "BAD_CURSOR", "Invalid cursor", "The pagination cursor is not valid.")


@router.get("/enrolments")
def list_enrolments(p: Principal = Depends(secured("enrolments:read")), status: str | None = None, overall: str | None = None,
                    programme_code: str | None = None, q: str | None = Query(default=None, max_length=80), year: str | None = None,
                    limit: int = Query(default=50, ge=1, le=500), cursor: str | None = None, include_total: bool = False):
    inst = _require_institution(p)
    year = year or claims.current_academic_year()
    where, params = ["e.institution_id=CAST(:i AS uuid)", "e.academic_year=:y"], {"i": inst, "y": year, "n": limit + 1}
    if status:
        where.append("e.status=:st"); params["st"] = status
    if overall:
        where.append("v.overall_state=:ov"); params["ov"] = overall
    if programme_code:
        where.append("pr.programme_code=:pc"); params["pc"] = programme_code
    if q:
        toks = crypto.name_tokens(q)
        params["q"] = f"{q.strip()}%"
        if toks:
            params["toks"] = toks
            where.append("(e.name_tokens @> :toks OR e.institution_student_id ILIKE :q OR e.enrolment_ref ILIKE :q)")
        else:
            where.append("(e.institution_student_id ILIKE :q OR e.enrolment_ref ILIKE :q)")
    cur = _decode_cursor(cursor)
    if cur:
        where.append("(e.claimed_at, e.id) < (CAST(:ck AS timestamptz), CAST(:cid AS uuid))"); params["ck"], params["cid"] = cur
    sql = ("SELECT e.id, e.enrolment_ref, e.institution_student_id, e.status, e.bucket_type, e.claimed_at, e.admission_date, e.concurrency_class, "
           "e.student_id, pr.programme_code, pr.name AS programme_name, v.overall_state, v.lock2_status, v.lock3_status, v.identity_status, "
           "v.channel_trust, v.deadline_at, v.fix_deadline_at, pii.data_key_id, pii.nonce, pii.ciphertext "
           "FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id JOIN core.enrolment_verification v ON v.enrolment_id=e.id "
           "LEFT JOIN LATERAL (SELECT data_key_id, nonce, ciphertext FROM pii.pii_records r WHERE r.student_id=e.student_id AND "
           "r.institution_id=e.institution_id AND r.ciphertext IS NOT NULL ORDER BY version DESC LIMIT 1) pii ON true "
           f"WHERE {' AND '.join(where)} ORDER BY e.claimed_at DESC, e.id DESC LIMIT :n")
    with tx(p.db_ctx()) as conn:
        data = rows(conn, sql, **params)
        total = None
        if include_total:
            cparams = {k: v for k, v in params.items() if k not in ("n", "ck", "cid")}
            cw = [w for w in where if ":ck" not in w]
            total = scalar(conn, "SELECT count(*) FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id "
                                 "JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE " + " AND ".join(cw), **cparams)
        audit.log(conn, "enrolments.list_viewed", entity_type="enrolment", actor_id=p.id, actor_role=p.role, institution_id=inst,
                  request_id=p.request_id, payload={"count": min(len(data), limit)})
    has_more = len(data) > limit
    data = data[:limit]
    items = []
    dkeys: dict[str, bytes] = {}
    for d in data:
        name = None
        if d["ciphertext"] is not None:
            kid = str(d["data_key_id"])
            dkeys.setdefault(kid, keys.data_key_by_id(kid))
            pii = crypto.decrypt(dkeys[kid], d["nonce"], d["ciphertext"], f"{d['student_id']}|{inst}".encode())
            name = pii.get("name")
        items.append({"id": str(d["id"]), "enrolment_ref": d["enrolment_ref"], "institution_student_id": d["institution_student_id"], "name": name,
                      "status": d["status"], "bucket_type": d["bucket_type"], "programme_code": d["programme_code"],
                      "programme_name": d["programme_name"], "claimed_at": d["claimed_at"], "admission_date": d["admission_date"],
                      "overall_state": d["overall_state"], "lock2": d["lock2_status"], "lock3": d["lock3_status"],
                      "identity_status": d["identity_status"], "channel_trust": d["channel_trust"], "deadline_at": d["deadline_at"],
                      "fix_deadline_at": d["fix_deadline_at"], "concurrency_class": d["concurrency_class"]})
    return {"items": items, "next_cursor": _cursor(data[-1]["claimed_at"].isoformat(), data[-1]["id"]) if has_more else None, "total": total}


@router.get("/enrolments/{enrolment_id}")
def enrolment_detail(enrolment_id: str, p: Principal = Depends(secured("students:read"))):
    inst = _require_institution(p)
    enrolment_id = ensure_uuid(enrolment_id, "enrolment")
    with tx(p.db_ctx()) as conn:
        e = row(conn, "SELECT e.*, pr.programme_code, pr.name AS programme_name, pr.level, pr.mode FROM core.enrolments e "
                      "JOIN ref.programmes pr ON pr.id=e.programme_id WHERE e.id=CAST(:e AS uuid) AND e.institution_id=CAST(:i AS uuid)",
                e=enrolment_id, i=inst)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        v = row(conn, "SELECT * FROM core.enrolment_verification WHERE enrolment_id=:e", e=e["id"])
        events = rows(conn, "SELECT seq, event_type, reason_code, reason_text, payload, occurred_at, effective_date FROM core.enrolment_events "
                            "WHERE enrolment_id=:e ORDER BY seq", e=e["id"])
        findings = rows(conn, "SELECT id, rule_code, severity, status, message, suggested_fix, created_at FROM rules.findings WHERE enrolment_id=:e "
                              "ORDER BY created_at", e=e["id"])
        diags = rows(conn, "SELECT status, field, message_code, message, suggested_fix, deadline_at, resolved_at FROM identity.diagnoses WHERE enrolment_id=:e", e=e["id"])
        checks = rows(conn, "SELECT source, status, name_score, dob_match, verdict, latency_ms, checked_at FROM identity.registry_checks "
                            "WHERE enrolment_id=:e ORDER BY checked_at DESC LIMIT 5", e=e["id"])
        cr = row(conn, "SELECT channel, target_masked, attempts, resend_count, status, otp_expires_at, for_guardian FROM core.confirmation_requests "
                       "WHERE enrolment_id=:e ORDER BY created_at DESC LIMIT 1", e=e["id"])
        pii = verification._vault(conn, str(e["student_id"]), inst)
        prov = row(conn, "SELECT token, type, compliance_deadline, status FROM core.provisional_ids WHERE student_id=:s", s=e["student_id"])
        audit.log(conn, "student.record_viewed", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role,
                  institution_id=inst, request_id=p.request_id)
    for f in findings:
        f["id"] = str(f["id"])
    student = {"name": pii.get("name"), "dob": pii.get("dob"), "parent_name": pii.get("parent_name"), "email": pii.get("email"),
               "district": pii.get("district"), "mobile_masked": identity.mask_mobile(pii["mobile"]) if pii.get("mobile") else None,
               "gender": e["gender"], "category": e["category"], "is_minor": e["is_minor"]}
    return {"id": str(e["id"]), "enrolment_ref": e["enrolment_ref"], "status": e["status"], "academic_year": e["academic_year"],
            "bucket_type": e["bucket_type"], "concurrency_class": e["concurrency_class"], "claimed_at": e["claimed_at"],
            "admission_date": e["admission_date"], "institution_student_id": e["institution_student_id"],
            "programme": {"code": e["programme_code"], "name": e["programme_name"], "level": e["level"], "mode": e["mode"]},
            "student": student, "verification": v and {k: (str(x) if k == "enrolment_id" else x) for k, x in v.items()},
            "events": events, "findings": findings, "diagnoses": diags, "registry_checks": checks, "confirmation": cr,
            "provisional": prov, "source_label": "SIMULATED registry and SMS"}


class AdmitIn(BaseModel):
    admission_date: date | None = None


class WithdrawIn(BaseModel):
    reason_code: str
    effective_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


class AssistedIn(BaseModel):
    reason_code: str
    witness_note: str = Field(min_length=5, max_length=500)


@router.post("/enrolments/{enrolment_id}/admit")
def admit(enrolment_id: str, body: AdmitIn, p: Principal = Depends(secured("enrolments:manage"))):
    return claims.admit(p, ensure_uuid(enrolment_id, "enrolment"), body.admission_date)


@router.post("/enrolments/{enrolment_id}/withdraw")
def withdraw(enrolment_id: str, body: WithdrawIn, p: Principal = Depends(secured("enrolments:manage"))):
    return claims.withdraw(p, ensure_uuid(enrolment_id, "enrolment"), body.reason_code, body.effective_date, body.note)


@router.post("/enrolments/{enrolment_id}/confirmation/resend")
def resend_otp(enrolment_id: str, p: Principal = Depends(secured("confirmation:resend"))):
    return verification.resend(p, None, ensure_uuid(enrolment_id, "enrolment"))


@router.post("/enrolments/{enrolment_id}/confirmation/assisted")
def assisted(enrolment_id: str, body: AssistedIn, p: Principal = Depends(secured("confirmation:assist"))):
    return verification.assisted_confirm(p, ensure_uuid(enrolment_id, "enrolment"), body.reason_code, body.witness_note)


class CorrectIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    dob: date | None = None
    apaar_id: str | None = Field(default=None, max_length=40)
    reason: str = Field(min_length=5, max_length=300)


@router.post("/enrolments/{enrolment_id}/identity/correct")
def correct_identity(enrolment_id: str, body: CorrectIn, p: Principal = Depends(secured("enrolments:manage"))):
    """'Edit & revalidate' from the fix-it list: new vault version, CORRECT event, then a registry revalidation."""
    inst = _require_institution(p)
    enrolment_id = ensure_uuid(enrolment_id, "enrolment")
    dk_id, _, dkey = keys.data_key(inst)
    with tx(p.db_ctx()) as conn:
        e = row(conn, "SELECT id, student_id, academic_year, status, seat_bucket_id FROM core.enrolments WHERE id=CAST(:e AS uuid) AND institution_id=CAST(:i AS uuid)",
                e=enrolment_id, i=inst)
        if not e or e["status"] not in ("CLAIMED", "ADMITTED"):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such live enrolment.")
        pii = verification._vault(conn, str(e["student_id"]), inst)
        if body.apaar_id is not None:
            hyg = identity.clean_apaar(body.apaar_id)
            if hyg.issues:
                raise ApiError(422, "IDENTITY_INVALID", "The APAAR ID is not valid", "See the fix.",
                               errors=[{"field": i["field"], "code": i["code"], "message": i["message"], "fix_suggestion": i["fix"]} for i in hyg.issues])
            pii["apaar_id"] = hyg.value
        if body.name:
            pii["name"] = body.name
        if body.dob:
            pii["dob"] = body.dob.isoformat()
        latest = scalar(conn, "SELECT max(version) FROM pii.pii_records WHERE student_id=:s AND institution_id=CAST(:i AS uuid)", s=e["student_id"], i=inst)
        nonce, ct = crypto.encrypt(dkey, pii, f"{e['student_id']}|{inst}".encode())
        conn.execute(text("INSERT INTO pii.pii_records (id, student_id, institution_id, data_key_id, version, nonce, ciphertext) VALUES "
                          "(:id,:s,CAST(:i AS uuid),CAST(:k AS uuid),:v,:n,:c)"),
                     {"id": uuid7(), "s": e["student_id"], "i": inst, "k": dk_id, "v": latest + 1, "n": nonce, "c": ct})
        if body.name:
            conn.execute(text("UPDATE core.enrolments SET name_tokens=:t, row_version=row_version+1 WHERE id=:e"), {"t": crypto.name_tokens(body.name), "e": e["id"]})
        claims._event(conn, e["id"], inst, claims.next_seq(conn, e["id"]), "CORRECT", p, None, {"fields": [k for k, v in
                      (("name", body.name), ("dob", body.dob), ("apaar_id", body.apaar_id)) if v]}, reason_text=body.reason)
        audit.log(conn, "enrolment.identity_corrected", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role, institution_id=inst,
                  justification=body.reason, request_id=p.request_id)
    out = ops.revalidate_enrolment(enrolment_id, source="REVALIDATION")
    return {"revalidation": out}


# ------------------------------------------------------------------ programmes / seat ledger
@router.get("/programmes")
def list_programmes(p: Principal = Depends(secured("dashboards:institution")), year: str | None = None, institution_id: str | None = None):
    inst = _scope_institution(p, institution_id)
    year = year or claims.current_academic_year()
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT pr.id AS programme_id, pr.programme_code, pr.name, pr.level, pr.mode, pr.lateral_entry, b.bucket_type, b.cap, b.approved_extra, "
                          "b.occupied, b.cumulative_admitted FROM ref.programmes pr LEFT JOIN core.seat_buckets b ON b.programme_id=pr.id AND b.academic_year=:y "
                          "WHERE pr.institution_id=CAST(:i AS uuid) AND pr.status='ACTIVE' ORDER BY pr.programme_code, b.bucket_type", i=inst, y=year)
    out: dict[str, dict] = {}
    for d in data:
        pg = out.setdefault(str(d["programme_id"]), {"id": str(d["programme_id"]), "code": d["programme_code"], "name": d["name"], "level": d["level"],
                                                       "mode": d["mode"], "lateral_entry": d["lateral_entry"], "buckets": []})
        if d["bucket_type"]:
            pct = round(100 * d["occupied"] / max(d["cap"] + d["approved_extra"], 1), 1)
            pg["buckets"].append({"bucket_type": d["bucket_type"], "cap": d["cap"], "approved_extra": d["approved_extra"], "occupied": d["occupied"],
                                  "cumulative_admitted": d["cumulative_admitted"], "pct": pct})
    return {"academic_year": year, "items": list(out.values()),
            "definitions": {"occupied": "Seats with a CLAIMED or ADMITTED enrolment", "cumulative_admitted": "Enrolments ever ADMITTED (never reduced by withdrawals)"}}


def _scope_institution(p: Principal, institution_id: str | None) -> str:
    """Institution users see only their own; AICTE reviewers may name an institution within their regional scope."""
    if p.institution_id:
        if institution_id and institution_id != p.institution_id:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such institution.")
        return p.institution_id
    if not institution_id:
        raise ApiError(422, "VALIDATION_ERROR", "institution_id required", "Name the institution to view.")
    institution_id = ensure_uuid(institution_id, "institution")
    with tx(SYSTEM) as conn:
        i = row(conn, "SELECT state, region FROM ref.institutions WHERE id=CAST(:i AS uuid)", i=institution_id)
    if not i or not _in_scope(p, i):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such institution.")
    return institution_id


def _in_scope(p: Principal, inst: dict) -> bool:
    if p.scope_type == "NATIONAL":
        return True
    if p.scope_type == "REGION":
        return inst["region"] == p.scope_value
    if p.scope_type == "STATE":
        return inst["state"] == p.scope_value
    return False


@router.get("/programmes/{programme_id}/ledger")
def programme_ledger(programme_id: str, p: Principal = Depends(secured("dashboards:institution")), year: str | None = None,
                     limit: int = Query(default=50, ge=1, le=500)):
    programme_id = ensure_uuid(programme_id, "programme")
    if p.institution_id:
        inst = p.institution_id
    else:  # AICTE reviewers may read the ledger of programmes inside their regional scope (no student data in a ledger)
        with tx(SYSTEM) as conn:
            owner = row(conn, "SELECT i.id, i.state, i.region FROM ref.programmes pr JOIN ref.institutions i ON i.id=pr.institution_id "
                              "WHERE pr.id=CAST(:p AS uuid)", p=programme_id)
        if not owner or not _in_scope(p, owner):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such programme.")
        inst = str(owner["id"])
    year = year or claims.current_academic_year()
    with tx(p.db_ctx()) as conn:
        entries = rows(conn, "SELECT l.id, b.bucket_type, l.entry_type, l.delta, l.occupied_after, l.reason, l.effective_date, l.created_at, "
                             "e.enrolment_ref FROM core.seat_ledger_entries l JOIN core.seat_buckets b ON b.id=l.seat_bucket_id "
                             "LEFT JOIN core.enrolments e ON e.id=l.enrolment_id WHERE b.programme_id=CAST(:p AS uuid) AND b.academic_year=:y "
                             "AND b.institution_id=CAST(:i AS uuid) ORDER BY l.id DESC LIMIT :n", p=programme_id, y=year, i=inst, n=limit)
        inv = rows(conn, "SELECT b.bucket_type, b.occupied, (SELECT coalesce(sum(CASE WHEN entry_type='DEBIT' THEN 1 WHEN entry_type='RELEASE' THEN -1 ELSE 0 END),0) "
                         "FROM core.seat_ledger_entries l WHERE l.seat_bucket_id=b.id) AS ledger_sum FROM core.seat_buckets b "
                         "WHERE b.programme_id=CAST(:p AS uuid) AND b.academic_year=:y AND b.institution_id=CAST(:i AS uuid)", p=programme_id, y=year, i=inst)
    return {"entries": entries, "invariant": [{"bucket_type": r["bucket_type"], "occupied": r["occupied"], "ledger_sum": int(r["ledger_sum"]),
                                                 "ok": r["occupied"] == int(r["ledger_sum"])} for r in inv]}


# ------------------------------------------------------------------ seat exceptions
class ExceptionIn(BaseModel):
    programme_code: str
    bucket_type: str = "REGULAR"
    academic_year: str | None = None
    extra_seats: int = Field(gt=0, le=500)
    justification: str = Field(min_length=20, max_length=1000)
    evidence_note: str | None = Field(default=None, max_length=500)


class DecideIn(BaseModel):
    approve: bool
    approved_seats: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=5, max_length=500)


@router.post("/seat-exceptions")
def request_exception(body: ExceptionIn, p: Principal = Depends(secured("seat_exceptions:request"))):
    _require_institution(p)
    return claims.request_exception(p, body.programme_code, body.bucket_type, body.extra_seats, body.justification, body.evidence_note, body.academic_year)


@router.get("/seat-exceptions")
def list_exceptions(p: Principal = Depends(secured("dashboards:institution")), status: str | None = None):
    with tx(p.db_ctx()) as conn:
        sql = ("SELECT x.id, x.status, x.extra_seats, x.approved_seats, x.justification, x.decision_reason, x.created_at, x.decided_at, b.bucket_type, "
               "pr.programme_code, pr.name AS programme_name, i.name AS institution_name, i.state, i.region FROM core.seat_exceptions x "
               "JOIN core.seat_buckets b ON b.id=x.seat_bucket_id JOIN ref.programmes pr ON pr.id=b.programme_id JOIN ref.institutions i ON i.id=x.institution_id "
               + ("WHERE x.status=:s " if status else "") + "ORDER BY x.created_at DESC LIMIT 200")
        data = rows(conn, sql, s=status) if status else rows(conn, sql)
    if not p.institution_id:
        data = [d for d in data if _in_scope(p, d)]
    return {"items": [{**d, "id": str(d["id"])} for d in data]}


@router.post("/seat-exceptions/{exception_id}/decide")
def decide_exception(exception_id: str, body: DecideIn, p: Principal = Depends(secured("seat_exceptions:decide"))):
    exception_id = ensure_uuid(exception_id, "request")
    with tx(SYSTEM) as conn:
        i = row(conn, "SELECT i.state, i.region FROM core.seat_exceptions x JOIN ref.institutions i ON i.id=x.institution_id WHERE x.id=CAST(:x AS uuid)", x=exception_id)
    if not i or not _in_scope(p, i):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such request.")
    return claims.decide_exception(p, exception_id, body.approve, body.approved_seats, body.reason)


# ------------------------------------------------------------------ findings
@router.get("/findings")
def list_findings(p: Principal = Depends(secured("findings:read")), status: str | None = None, severity: str | None = None,
                  year: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    inst = _require_institution(p)
    where, params = ["f.institution_id=CAST(:i AS uuid)", "f.academic_year=:y"], {"i": inst, "y": year or claims.current_academic_year(), "n": limit}
    if status:
        where.append("f.status=:s"); params["s"] = status
    if severity:
        where.append("f.severity=:v"); params["v"] = severity
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT f.id, f.rule_code, f.severity, f.status, f.message, f.suggested_fix, f.created_at, e.enrolment_ref, e.id AS enrolment_id "
                          f"FROM rules.findings f LEFT JOIN core.enrolments e ON e.id=f.enrolment_id WHERE {' AND '.join(where)} "
                          "ORDER BY CASE f.severity WHEN 'Blocker' THEN 0 WHEN 'Error' THEN 1 WHEN 'Warning' THEN 2 ELSE 3 END, f.created_at DESC LIMIT :n", **params)
    return {"items": [{**d, "id": str(d["id"]), "enrolment_id": str(d["enrolment_id"]) if d["enrolment_id"] else None} for d in data]}


@router.post("/findings/{finding_id}/acknowledge")
def ack_finding(finding_id: str, p: Principal = Depends(secured("findings:ack"))):
    finding_id = ensure_uuid(finding_id, "finding")
    with tx(p.db_ctx()) as conn:
        n = conn.execute(text("UPDATE rules.findings SET status='ACKNOWLEDGED', acknowledged_by=CAST(:u AS uuid) WHERE id=CAST(:f AS uuid) "
                              "AND institution_id=CAST(:i AS uuid) AND status='OPEN' AND severity IN ('Warning','Info')"),
                        {"u": p.id, "f": finding_id, "i": p.institution_id}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No open warning with that id.")
        audit.log(conn, "finding.acknowledged", entity_type="finding", entity_id=finding_id, actor_id=p.id, actor_role=p.role,
                  institution_id=p.institution_id, request_id=p.request_id)
    return {"id": finding_id, "status": "ACKNOWLEDGED"}


@router.get("/fixes")
def fix_list(p: Principal = Depends(secured("enrolments:read")), year: str | None = None):
    """PENDING_FIX / PENDING_VERIFICATION items with deadlines (REQ-IDN-07)."""
    inst = _require_institution(p)
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT e.id, e.enrolment_ref, e.institution_student_id, v.identity_status, v.fix_deadline_at, v.overall_state, "
                          "(SELECT json_agg(json_build_object('field', d.field, 'code', d.message_code, 'message', d.message, 'fix', d.suggested_fix->>'fix')) "
                          " FROM identity.diagnoses d WHERE d.enrolment_id=e.id AND d.resolved_at IS NULL) AS fixes "
                          "FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.institution_id=CAST(:i AS uuid) "
                          "AND e.academic_year=:y AND e.status IN ('CLAIMED','ADMITTED') AND (v.overall_state='PENDING_FIX' OR v.identity_status='PENDING_VERIFICATION') "
                          "ORDER BY v.fix_deadline_at NULLS LAST LIMIT 200", i=inst, y=year or claims.current_academic_year())
    return {"items": [{**d, "id": str(d["id"])} for d in data]}
