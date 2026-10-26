"""Student wallet (REQ-STU): share links, consent ledger and requests, access log, corrections, data-subject requests, guardian link, locale."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import text

from . import audit, credentials, crypto, identity, keys, notify, ops, verification
from .claims import _enqueue_outbox, _event, next_seq
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, DbContext, row, rows, scalar, tx
from .deps import Principal
from .verifier import PURPOSES, hash_token

CORRECTION_FIELDS = {"name", "dob", "parent_name", "mobile", "email"}


def sctx(student_id: str) -> DbContext:
    return DbContext(student_id=student_id)


# ------------------------------------------------------------------ credentials and share links
def my_credentials(sid: str) -> list[dict]:
    with tx(sctx(sid)) as conn:
        d = rows(conn, "SELECT c.id, c.credential_ref, c.status, c.issued_at, c.valid_until, c.revoked_reason, e.academic_year, e.enrolment_ref, pr.name AS programme_name, i.name AS institution_name "
                       "FROM cred.credentials c JOIN core.enrolments e ON e.id=c.enrolment_id JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=c.institution_id "
                       "WHERE c.student_id=CAST(:s AS uuid) ORDER BY c.issued_at DESC", s=sid)
    return [{**x, "id": str(x["id"]), "verify_url": f"{credentials.BASE_URL()}/v/{x['credential_ref']}"} for x in d]


def credential_pdf(sid: str, cid: str):
    with tx(sctx(sid)) as conn:
        if not row(conn, "SELECT 1 AS x FROM cred.credentials WHERE id=CAST(:c AS uuid) AND student_id=CAST(:s AS uuid)", c=cid, s=sid):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such credential.")
    with tx(SYSTEM) as conn:
        return credentials.certificate_pdf(conn, cid)


def create_share_link(p: Principal, sid: str, credential_id: str, purpose: str, fields: list[str], days: int) -> dict:
    if purpose not in PURPOSES or not fields or any(f not in credentials.DISCLOSABLE for f in fields) or not (1 <= days <= 30):
        raise ApiError(422, "VALIDATION_ERROR", "Invalid share link", "Choose a purpose, at least one field, and an expiry of 1-30 days.",
                       errors=[{"field": "fields", "code": "ENUM", "message": f"Fields must be among {credentials.DISCLOSABLE}.", "fix_suggestion": "Pick the fields you are happy to share."}])
    token = secrets.token_urlsafe(24)
    with tx(sctx(sid)) as conn:
        c = row(conn, "SELECT id, status FROM cred.credentials WHERE id=CAST(:c AS uuid) AND student_id=CAST(:s AS uuid)", c=credential_id, s=sid)
        if not c or c["status"] != "VALID":
            raise ApiError(404, "NOT_FOUND", "Not found", "No valid credential with that id.")
        lid = uuid7()
        conn.execute(text("INSERT INTO cred.share_links (id, credential_id, student_id, token_hash, purpose_code, fields, expires_at) VALUES (:id,:c,CAST(:s AS uuid),:h,:p,:f,:e)"),
                     {"id": lid, "c": c["id"], "s": sid, "h": hash_token(token), "p": purpose, "f": fields, "e": now() + timedelta(days=days)})
        audit.log(conn, "share_link.created", entity_type="share_link", entity_id=lid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"purpose": purpose, "fields": fields, "days": days})
    return {"id": str(lid), "token": token, "expires_at": now() + timedelta(days=days), "note": "Shown once. Anyone holding this token can ask the verification service the allowed questions for this purpose until it expires or you revoke it."}


def list_share_links(sid: str) -> list[dict]:
    with tx(sctx(sid)) as conn:
        d = rows(conn, "SELECT l.id, l.purpose_code, l.fields, l.expires_at, l.revoked_at, l.use_count, l.created_at, c.credential_ref FROM cred.share_links l JOIN cred.credentials c ON c.id=l.credential_id "
                       "WHERE l.student_id=CAST(:s AS uuid) ORDER BY l.created_at DESC", s=sid)
    return [{**x, "id": str(x["id"])} for x in d]


def revoke_share_link(p: Principal, sid: str, lid: str) -> dict:
    with tx(sctx(sid)) as conn:
        n = conn.execute(text("UPDATE cred.share_links SET revoked_at=:t WHERE id=CAST(:l AS uuid) AND student_id=CAST(:s AS uuid) AND revoked_at IS NULL"), {"t": now(), "l": lid, "s": sid}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No active link with that id.")
        audit.log(conn, "share_link.revoked", entity_type="share_link", entity_id=lid, actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    return {"revoked": True}


# ------------------------------------------------------------------ consent ledger
def verifier_orgs() -> list[dict]:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT o.id, o.name, o.tier, (SELECT jsonb_object_agg(s.purpose_code, s.allowed_fields) FROM iam.verifier_scopes s WHERE s.verifier_org_id=o.id) AS purposes FROM iam.verifier_orgs o WHERE o.status='ACTIVE' ORDER BY o.name")
    return [{**x, "id": str(x["id"])} for x in d]


def my_consents(sid: str) -> dict:
    with tx(sctx(sid)) as conn:
        cons = rows(conn, "SELECT id, kind, purpose_code, verifier_org_id, scope, notice_version, status, method, granted_at, expires_at, withdrawn_at FROM consent.consents WHERE student_id=CAST(:s AS uuid) ORDER BY granted_at DESC", s=sid)
        reqs = rows(conn, "SELECT id, verifier_org_id, purpose_code, question, status, created_at FROM consent.consent_requests WHERE student_id=CAST(:s AS uuid) ORDER BY created_at DESC", s=sid)
    with tx(SYSTEM) as conn:
        orgs = {str(o["id"]): o["name"] for o in rows(conn, "SELECT id, name FROM iam.verifier_orgs")}
    for c in cons:
        c["id"], c["verifier"] = str(c["id"]), orgs.get(str(c["verifier_org_id"]))
        c["verifier_org_id"] = str(c["verifier_org_id"]) if c["verifier_org_id"] else None
    for r in reqs:
        r["id"], r["verifier"] = str(r["id"]), orgs.get(str(r["verifier_org_id"]))
        r["verifier_org_id"] = str(r["verifier_org_id"])
    apaar = next((c for c in cons if c["kind"] == "APAAR_USE"), None)
    return {"consents": [c for c in cons if c["kind"] == "SHARING"], "requests": reqs, "apaar_use": apaar,
            "notice": "Your enrolment is recorded under statutory authority whether or not you consent. Consent only governs sharing with verifier organisations. Refusing APAAR consent never affects being counted."}


def grant_consent(p: Principal, sid: str, org_id: str, purpose: str, fields: list[str], days: int | None, method: str = "PORTAL") -> dict:
    if purpose not in PURPOSES or any(f not in credentials.DISCLOSABLE for f in fields) or not fields:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid consent", "Choose a purpose and the fields you agree to share.",
                       errors=[{"field": "fields", "code": "ENUM", "message": f"Fields must be among {credentials.DISCLOSABLE}.", "fix_suggestion": "Pick fields to share."}])
    with tx(SYSTEM) as conn:
        o = row(conn, "SELECT id, status FROM iam.verifier_orgs WHERE id=CAST(:o AS uuid)", o=org_id)
        sc = row(conn, "SELECT allowed_fields FROM iam.verifier_scopes WHERE verifier_org_id=CAST(:o AS uuid) AND purpose_code=:p", o=org_id, p=purpose) if o else None
    if not o or o["status"] != "ACTIVE" or not sc:
        raise ApiError(404, "NOT_FOUND", "Not found", "That organisation is not available for this purpose.")
    with tx(sctx(sid)) as conn:
        cid = uuid7()
        conn.execute(text("UPDATE consent.consents SET status='WITHDRAWN', withdrawn_at=:t WHERE student_id=CAST(:s AS uuid) AND kind='SHARING' AND verifier_org_id=CAST(:o AS uuid) AND purpose_code=:p AND status='GRANTED'"),
                     {"t": now(), "s": sid, "o": org_id, "p": purpose})
        conn.execute(text("INSERT INTO consent.consents (id, student_id, kind, purpose_code, verifier_org_id, scope, status, method, granted_at, expires_at, given_by) VALUES "
                          "(:id,CAST(:s AS uuid),'SHARING',:p,CAST(:o AS uuid),CAST(:sc AS jsonb),'GRANTED',:m,:t,:e,CAST(:u AS uuid))"),
                     {"id": cid, "s": sid, "p": purpose, "o": org_id, "sc": dumps({"fields": fields}), "m": "GUARDIAN" if sid != p.student_id else method, "t": now(), "e": now() + timedelta(days=days) if days else None, "u": p.id})
        conn.execute(text("UPDATE consent.consent_requests SET status='GRANTED' WHERE student_id=CAST(:s AS uuid) AND verifier_org_id=CAST(:o AS uuid) AND purpose_code=:p AND status='PENDING'"), {"s": sid, "o": org_id, "p": purpose})
        audit.log(conn, "consent.granted", entity_type="consent", entity_id=cid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"purpose": purpose, "fields": fields})
    return {"id": str(cid), "status": "GRANTED"}


def withdraw_consent(p: Principal, sid: str, cid: str) -> dict:
    with tx(sctx(sid)) as conn:
        n = conn.execute(text("UPDATE consent.consents SET status='WITHDRAWN', withdrawn_at=:t WHERE id=CAST(:c AS uuid) AND student_id=CAST(:s AS uuid) AND status='GRANTED'"), {"t": now(), "c": cid, "s": sid}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No active consent with that id.")
        audit.log(conn, "consent.withdrawn", entity_type="consent", entity_id=cid, actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    return {"status": "WITHDRAWN", "note": "Takes effect on the next verification request. Past verifications stay in your access log and the audit trail."}


def refuse_request(p: Principal, sid: str, rid: str) -> dict:
    with tx(sctx(sid)) as conn:
        n = conn.execute(text("UPDATE consent.consent_requests SET status='REFUSED' WHERE id=CAST(:r AS uuid) AND student_id=CAST(:s AS uuid) AND status='PENDING'"), {"r": rid, "s": sid}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No pending request with that id.")
        audit.log(conn, "consent.request_refused", entity_type="consent_request", entity_id=rid, actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    return {"status": "REFUSED"}


def set_apaar_consent(p: Principal, sid: str, granted: bool) -> dict:
    with tx(sctx(sid)) as conn:
        conn.execute(text("UPDATE consent.consents SET status='WITHDRAWN', withdrawn_at=:t WHERE student_id=CAST(:s AS uuid) AND kind='APAAR_USE' AND status IN ('GRANTED','REFUSED')"), {"t": now(), "s": sid})
        conn.execute(text("INSERT INTO consent.consents (id, student_id, kind, status, granted_at, given_by) VALUES (:id,CAST(:s AS uuid),'APAAR_USE',:st,:t,CAST(:u AS uuid))"),
                     {"id": uuid7(), "s": sid, "st": "GRANTED" if granted else "REFUSED", "t": now(), "u": p.id})
        audit.log(conn, "consent.apaar_use", entity_type="student", entity_id=sid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"granted": granted})
    return {"apaar_use": "GRANTED" if granted else "REFUSED", "note": "Refusing never affects whether you are counted."}


# ------------------------------------------------------------------ access log
def access_log(sid: str) -> dict:
    with tx(sctx(sid)) as conn:
        v = rows(conn, "SELECT r.created_at, r.purpose_code, r.outcome, r.fields_disclosed, r.via, r.question, o.name AS verifier FROM cred.verification_requests r LEFT JOIN iam.verifier_orgs o ON o.id=r.verifier_org_id "
                       "WHERE r.student_id=CAST(:s AS uuid) ORDER BY r.created_at DESC LIMIT 100", s=sid)
    with tx(SYSTEM) as conn:
        staff = rows(conn, "SELECT a.occurred_at AS created_at, a.actor_role, c.case_no FROM audit.audit_log a LEFT JOIN intel.cases c ON c.id=a.case_id WHERE a.action='access.student_revealed' "
                           "AND a.entity_id IN (SELECT id::text FROM core.enrolments WHERE student_id=CAST(:s AS uuid)) ORDER BY a.occurred_at DESC LIMIT 50", s=sid)
    return {"verifications": [{**x, "verifier": x["verifier"] or "Anyone holding a share link"} for x in v],
            "staff_access": [{"at": s["created_at"], "label": "AICTE case access", "case_no": s["case_no"], "role": s["actor_role"]} for s in staff],
            "disclosure_of": len(credentials.FIELD_CATALOGUE)}


# ------------------------------------------------------------------ corrections
def request_correction(p: Principal, sid: str, enrolment_id: str, field: str, value: str, note: str | None) -> dict:
    if field not in CORRECTION_FIELDS:
        raise ApiError(422, "VALIDATION_ERROR", "Field not allowed", f"Use one of {sorted(CORRECTION_FIELDS)}.", errors=[{"field": "field", "code": "ENUM", "message": "Not a correctable field.", "fix_suggestion": "Pick a listed field."}])
    e = verification._own_enrolment(sid, enrolment_id)
    inst = str(e["institution_id"])
    _, _, dkey = keys.data_key(inst)
    nonce, ct = crypto.encrypt(dkey, {"value": value}, f"corr|{sid}|{inst}".encode())
    with tx(DbContext(institution_id=inst, scope="INSTITUTION", actor_id=p.id, student_id=sid)) as conn:
        cid = uuid7()
        conn.execute(text("INSERT INTO ingest.correction_requests (id, enrolment_id, institution_id, student_id, requested_by, field, proposed_nonce, proposed_enc, note) VALUES "
                          "(:id,CAST(:e AS uuid),CAST(:i AS uuid),CAST(:s AS uuid),CAST(:u AS uuid),:f,:n,:c,:no)"), {"id": cid, "e": enrolment_id, "i": inst, "s": sid, "u": p.id, "f": field, "n": nonce, "c": ct, "no": note})
        audit.log(conn, "correction.requested", entity_type="correction", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=inst, request_id=p.request_id, payload={"field": field})
    return {"id": str(cid), "status": "PENDING"}


def my_corrections(sid: str) -> list[dict]:
    with tx(sctx(sid)) as conn:
        d = rows(conn, "SELECT c.id, c.field, c.status, c.note, c.reason, c.created_at, c.decided_at, e.enrolment_ref FROM ingest.correction_requests c JOIN core.enrolments e ON e.id=c.enrolment_id WHERE c.student_id=CAST(:s AS uuid) ORDER BY c.created_at DESC", s=sid)
    return [{**x, "id": str(x["id"])} for x in d]


def list_corrections(p: Principal, status: str | None) -> list[dict]:
    from .cases import in_scope
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT c.id, c.field, c.status, c.note, c.reason, c.created_at, e.enrolment_ref, i.name AS institution_name, i.state, i.region FROM ingest.correction_requests c JOIN core.enrolments e ON e.id=c.enrolment_id "
                       "JOIN ref.institutions i ON i.id=c.institution_id" + (" WHERE c.status=:s" if status else "") + " ORDER BY c.created_at DESC LIMIT 300", **({"s": status} if status else {}))
    return [{**x, "id": str(x["id"])} for x in d if in_scope(p, x)]


def decide_correction(p: Principal, cid: str, approve: bool, reason: str) -> dict:
    from .cases import in_scope
    with tx(p.db_ctx()) as conn:
        c = row(conn, "SELECT c.*, i.state, i.region FROM ingest.correction_requests c JOIN ref.institutions i ON i.id=c.institution_id WHERE c.id=CAST(:c AS uuid) FOR UPDATE OF c", c=cid)
        if not c or not in_scope(p, c):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such request.")
        if c["status"] != "PENDING":
            raise ApiError(409, "INVALID_STATE", "Already decided", f"This request is {c['status']}.")
        conn.execute(text("UPDATE ingest.correction_requests SET status=:s, decided_by=CAST(:u AS uuid), decided_at=:t, reason=:r WHERE id=:c"), {"s": "APPROVED" if approve else "REJECTED", "u": p.id, "t": now(), "r": reason, "c": c["id"]})
        revalidate = False
        if approve:
            inst, sid = str(c["institution_id"]), str(c["student_id"])
            dk_id, _, dkey = keys.data_key(inst)
            new = crypto.decrypt(dkey, c["proposed_nonce"], c["proposed_enc"], f"corr|{sid}|{inst}".encode())["value"]
            pii = verification._vault(conn, sid, inst)
            pii[c["field"]] = new
            ver = scalar(conn, "SELECT max(version) FROM pii.pii_records WHERE student_id=:s AND institution_id=:i", s=c["student_id"], i=c["institution_id"])
            nonce, ct = crypto.encrypt(dkey, pii, f"{sid}|{inst}".encode())
            conn.execute(text("INSERT INTO pii.pii_records (id, student_id, institution_id, data_key_id, version, nonce, ciphertext) VALUES (:id,:s,:i,CAST(:k AS uuid),:v,:n,:c)"),
                         {"id": uuid7(), "s": c["student_id"], "i": c["institution_id"], "k": dk_id, "v": ver + 1, "n": nonce, "c": ct})
            if c["field"] == "name":
                conn.execute(text("UPDATE core.enrolments SET name_tokens=:t, row_version=row_version+1 WHERE id=:e"), {"t": crypto.name_tokens(new), "e": c["enrolment_id"]})
            conn.execute(text("DELETE FROM matching.match_features WHERE enrolment_id=:e"), {"e": c["enrolment_id"]})       # features rebuilt by the matching job
            _event(conn, c["enrolment_id"], c["institution_id"], next_seq(conn, c["enrolment_id"]), "CORRECT", p, None, {"field": c["field"], "source": "STUDENT_REQUEST"}, reason_text=reason)
            n_super = credentials.supersede_for_student(conn, c["student_id"], c["institution_id"], f"Record corrected ({c['field']})", actor_id=p.id, actor_role=p.role, request_id=p.request_id)
            revalidate = c["field"] in ("name", "dob")
        audit.log(conn, "correction.decided", entity_type="correction", entity_id=c["id"], actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], justification=reason, request_id=p.request_id,
                  payload={"approved": approve, "field": c["field"]})
    if revalidate:
        ops.revalidate_enrolment(str(c["enrolment_id"]))
    return {"status": "APPROVED" if approve else "REJECTED", "credentials_superseded": n_super if approve else 0}


# ------------------------------------------------------------------ data-subject requests
def _export(sid: str) -> dict:
    with tx(SYSTEM) as conn:
        s = row(conn, "SELECT student_ref, identity_type, identity_status, created_at FROM core.students WHERE id=CAST(:s AS uuid)", s=sid)
        enr = rows(conn, "SELECT e.id, e.enrolment_ref, e.institution_id, e.academic_year, e.status, e.claimed_at, e.admission_date, pr.name AS programme, i.name AS institution, v.overall_state FROM core.enrolments e "
                         "JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=e.institution_id JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.student_id=CAST(:s AS uuid) ORDER BY e.claimed_at", s=sid)
        out_enr = []
        for e in enr:
            pii = verification._vault(conn, sid, str(e["institution_id"]))
            out_enr.append({"reference": e["enrolment_ref"], "institution": e["institution"], "programme": e["programme"], "academic_year": e["academic_year"], "status": e["status"],
                            "verification": e["overall_state"], "claimed_at": e["claimed_at"].isoformat(), "record_held_by_institution": {k: pii.get(k) for k in ("name", "dob", "parent_name", "district", "email")} if pii else "erased"})
        creds = rows(conn, "SELECT credential_ref, status, issued_at, valid_until FROM cred.credentials WHERE student_id=CAST(:s AS uuid)", s=sid)
        cons = rows(conn, "SELECT kind, purpose_code, status, granted_at, expires_at FROM consent.consents WHERE student_id=CAST(:s AS uuid)", s=sid)
    log = access_log(sid)
    return {"generated_at": now().isoformat(), "student": {"reference": s["student_ref"], "identity_type": s["identity_type"], "identity_status": s["identity_status"]}, "enrolments": out_enr,
            "credentials": [{**c, "issued_at": c["issued_at"].isoformat(), "valid_until": c["valid_until"].isoformat()} for c in creds],
            "consents": [{**c, "granted_at": c["granted_at"].isoformat(), "expires_at": c["expires_at"].isoformat() if c["expires_at"] else None} for c in cons],
            "verification_requests": len(log["verifications"]), "staff_access_events": len(log["staff_access"]), "notes": "Aadhaar is never stored. This export contains only your own record."}


def create_dsr(p: Principal, sid: str, type_: str, details: str | None) -> dict:
    if type_ not in ("ACCESS", "CORRECTION", "ERASURE", "GRIEVANCE"):
        raise ApiError(422, "VALIDATION_ERROR", "Unknown request type", "Use ACCESS, CORRECTION, ERASURE or GRIEVANCE.")
    result = _export(sid) if type_ == "ACCESS" else None
    with tx(sctx(sid)) as conn:
        did = uuid7()
        conn.execute(text("INSERT INTO consent.data_subject_requests (id, student_id, type, details, status, result, resolved_at) VALUES (:id,CAST(:s AS uuid),:t,:d,:st,CAST(:r AS jsonb),:ra)"),
                     {"id": did, "s": sid, "t": type_, "d": details, "st": "FULFILLED" if type_ == "ACCESS" else "OPEN", "r": dumps(result) if result else None, "ra": now() if result else None})
        audit.log(conn, "dsr.created", entity_type="dsr", entity_id=did, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"type": type_})
    return {"id": str(did), "type": type_, "status": "FULFILLED" if type_ == "ACCESS" else "OPEN"}


def my_dsrs(sid: str) -> list[dict]:
    with tx(sctx(sid)) as conn:
        d = rows(conn, "SELECT id, type, details, status, decision_note, created_at, resolved_at, (result IS NOT NULL) AS has_export FROM consent.data_subject_requests WHERE student_id=CAST(:s AS uuid) ORDER BY created_at DESC", s=sid)
    return [{**x, "id": str(x["id"])} for x in d]


def dsr_export(sid: str, did: str) -> dict:
    with tx(sctx(sid)) as conn:
        r = row(conn, "SELECT result FROM consent.data_subject_requests WHERE id=CAST(:d AS uuid) AND student_id=CAST(:s AS uuid) AND result IS NOT NULL", d=did, s=sid)
    if not r:
        raise ApiError(404, "NOT_FOUND", "Not found", "No export for that request.")
    return r["result"]


def list_dsrs(p: Principal, status: str | None) -> list[dict]:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT r.id, r.type, r.details, r.status, r.decision_note, r.created_at, s.student_ref, (SELECT array_agg(DISTINCT e.institution_id) FROM core.enrolments e WHERE e.student_id=r.student_id) AS insts, "
                       "EXISTS (SELECT 1 FROM core.enrolments e WHERE e.student_id=r.student_id AND e.status IN ('CLAIMED','ADMITTED')) AS has_live_enrolment FROM consent.data_subject_requests r JOIN core.students s ON s.id=r.student_id "
                       "WHERE r.type<>'ACCESS'" + (" AND r.status=:s" if status else "") + " ORDER BY r.created_at DESC LIMIT 300", **({"s": status} if status else {}))
        inst = {r["id"]: r for r in rows(conn, "SELECT id, state, region FROM ref.institutions")}
    from .cases import in_scope
    out = []
    for x in d:
        if p.scope_type == "NATIONAL" or any(in_scope(p, inst[i]) for i in (x["insts"] or []) if i in inst):
            out.append({**{k: v for k, v in x.items() if k != "insts"}, "id": str(x["id"])})
    return out


def decide_dsr(p: Principal, did: str, decision: str, note: str) -> dict:
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT id, student_id, type, status FROM consent.data_subject_requests WHERE id=CAST(:d AS uuid) FOR UPDATE", d=did)
        if not r or r["type"] == "ACCESS":
            raise ApiError(404, "NOT_FOUND", "Not found", "No such request.")
        if r["status"] not in ("OPEN", "IN_REVIEW"):
            raise ApiError(409, "INVALID_STATE", "Already decided", f"This request is {r['status']}.")
        status, erased = "FULFILLED" if decision == "FULFIL" else "REJECTED_OTHER", 0
        if r["type"] == "ERASURE" and decision == "FULFIL":
            live = scalar(conn, "SELECT count(*) FROM core.enrolments WHERE student_id=:s AND status IN ('CLAIMED','ADMITTED')", s=r["student_id"])
            if live:
                status = "REJECTED_LAWFUL_RETENTION"       # statutory recording of an active enrolment; processing is restricted instead
            else:
                erased = conn.execute(text("UPDATE pii.pii_records SET ciphertext=NULL, nonce=NULL, erased_at=:t WHERE student_id=:s AND erased_at IS NULL"), {"t": now(), "s": r["student_id"]}).rowcount
                conn.execute(text("DELETE FROM matching.match_features WHERE enrolment_id IN (SELECT id FROM core.enrolments WHERE student_id=:s)"), {"s": r["student_id"]})
        conn.execute(text("UPDATE consent.data_subject_requests SET status=:st, decided_by=CAST(:u AS uuid), decision_note=:n, resolved_at=:t WHERE id=:d"), {"st": status, "u": p.id, "n": note, "t": now(), "d": r["id"]})
        audit.log(conn, "dsr.decided", entity_type="dsr", entity_id=r["id"], actor_id=p.id, actor_role=p.role, justification=note, request_id=p.request_id, payload={"type": r["type"], "status": status, "records_erased": erased})
    return {"status": status, "records_erased": erased}


# ------------------------------------------------------------------ guardian link (guardian OTP via the mock SMS gateway)
def request_guardian_link(p: Principal, student_ref: str, relationship: str) -> dict:
    with tx(SYSTEM) as conn:
        s = row(conn, "SELECT s.id FROM core.students s WHERE s.student_ref=:r", r=student_ref.strip().upper())
        e = row(conn, "SELECT e.id, e.institution_id, e.is_minor FROM core.enrolments e WHERE e.student_id=:s AND e.status IN ('CLAIMED','ADMITTED') ORDER BY e.claimed_at DESC LIMIT 1", s=s["id"]) if s else None
        if not s or not e or not e["is_minor"]:
            raise ApiError(404, "NOT_FOUND", "No such minor", "No live enrolment of a minor matches that student reference.")
        pii = verification._vault(conn, str(s["id"]), str(e["institution_id"]))
        mobile = pii.get("registry_mobile") or pii.get("mobile")
        if not mobile:
            raise ApiError(409, "NO_OTP_CHANNEL", "No phone on record", "No mobile is available to send the guardian code to.")
        otp = f"{secrets.randbelow(900000) + 100000}"
        salt = secrets.token_hex(8)
        lid = uuid7()
        conn.execute(text("INSERT INTO consent.guardian_links (id, student_id, guardian_user_id, relationship, status, otp_hash, otp_salt, otp_expires_at) VALUES (:id,:s,CAST(:g AS uuid),:r,'PENDING',:h,:sa,:e) "
                          "ON CONFLICT (student_id, guardian_user_id) DO UPDATE SET status=CASE WHEN consent.guardian_links.status='VERIFIED' THEN 'VERIFIED' ELSE 'PENDING' END, otp_hash=:h, otp_salt=:sa, otp_expires_at=:e, attempts=0"),
                     {"id": lid, "s": s["id"], "g": p.id, "r": relationship[:30], "h": hashlib.sha256(f"{salt}:{otp}".encode()).hexdigest(), "sa": salt, "e": now() + timedelta(minutes=10)})
        audit.log(conn, "guardian_link.requested", entity_type="student", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=e["institution_id"], request_id=p.request_id)
    notify.send_otp(str(e["institution_id"]), str(e["id"]), mobile, otp, "REGISTRY_MOBILE", "en")
    return {"student_ref": student_ref.strip().upper(), "sent_to": identity.mask_mobile(mobile), "note": "A code was sent to the number held for the student (mock SMS gateway, see the Dev SMS Inbox)."}


def verify_guardian_link(p: Principal, student_ref: str, otp: str) -> dict:
    err = None
    with tx(SYSTEM) as conn:
        s = scalar(conn, "SELECT id FROM core.students WHERE student_ref=:r", r=student_ref.strip().upper())
        g = row(conn, "SELECT id, otp_hash, otp_salt, otp_expires_at, attempts, status FROM consent.guardian_links WHERE student_id=:s AND guardian_user_id=CAST(:u AS uuid) FOR UPDATE", s=s, u=p.id) if s else None
        if not g:
            raise ApiError(404, "NOT_FOUND", "No pending link", "Request a link first.")
        if g["status"] == "VERIFIED":
            return {"status": "VERIFIED"}
        if g["attempts"] >= 5 or not g["otp_expires_at"] or g["otp_expires_at"] < now():
            err = ApiError(410, "OTP_EXPIRED", "Code expired", "Request a new code.")
        elif hashlib.sha256(f"{g['otp_salt']}:{otp}".encode()).hexdigest() != g["otp_hash"]:
            conn.execute(text("UPDATE consent.guardian_links SET attempts=attempts+1 WHERE id=:i"), {"i": g["id"]})
            err = ApiError(422, "OTP_INVALID", "That code is not correct", f"{4 - g['attempts']} attempts left.")
        else:
            conn.execute(text("UPDATE consent.guardian_links SET status='VERIFIED', verified_at=:t, otp_hash=NULL WHERE id=:i"), {"t": now(), "i": g["id"]})
            audit.log(conn, "guardian_link.verified", entity_type="student", entity_id=s, actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    if err:
        raise err
    return {"status": "VERIFIED"}
