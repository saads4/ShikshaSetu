"""Triple-Lock verification: Lock 3 confirmation by OTP, guardian, assisted channel, dispute, resend (REQ-TL)."""
from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from sqlalchemy import text

from . import audit, crypto, identity, keys, notify
from .claims import _enqueue_outbox, _event, _hash_otp, _otp, next_seq
from .core import ApiError, now, uuid7
from .db import DbContext, SYSTEM, row, rows, scalar, tx
from .deps import Principal

MAX_ATTEMPTS, OTP_MINUTES, MAX_SENDS_PER_HOUR = 5, 10, 3
ASSIST_REASONS = {"NO_PHONE", "PHONE_LOST", "ACCESSIBILITY", "OTHER"}


def overall_state(lock2: str, lock3: str) -> str:
    if lock3 == "DISPUTED":
        return "DISPUTED"
    if lock2 == "FAILED":
        return "PENDING_FIX"
    if lock2 in ("PASSED", "PROVISIONAL_OK") and lock3 == "CONFIRMED":
        return "VERIFIED"
    if lock3 == "EXPIRED":
        return "UNCONFIRMED"
    return "PENDING"


def resolve_student(p: Principal, act_as: str | None) -> tuple[str, bool]:
    """Own student record, or a linked minor when acting as a verified guardian (DEC-31). Returns (student_id, is_guardian)."""
    if act_as and act_as != (p.student_id or ""):
        with tx(SYSTEM) as conn:
            ok = row(conn, "SELECT 1 AS x FROM consent.guardian_links WHERE guardian_user_id=CAST(:u AS uuid) AND student_id=CAST(:s AS uuid) "
                           "AND status='VERIFIED'", u=p.id, s=act_as)
        if not ok:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such student.")
        return act_as, True
    if not p.student_id:
        raise ApiError(404, "NOT_FOUND", "Not found", "This account has no student record.")
    return p.student_id, False


def _own_enrolment(student_id: str, enrolment_id: str) -> dict:
    with tx(DbContext(scope="", student_id=student_id)) as conn:
        e = row(conn, "SELECT id, institution_id, status, is_minor, academic_year FROM core.enrolments WHERE id=CAST(:e AS uuid) "
                      "AND student_id=CAST(:s AS uuid)", e=enrolment_id, s=student_id)
    if not e:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
    return e


def _set_lock3(conn, e: dict, p: Principal, lock3: str, *, channel: str | None = None, trust: str | None = None, reason: str | None = None) -> str:
    v = row(conn, "SELECT lock2_status, overall_state FROM core.enrolment_verification WHERE enrolment_id=:e FOR UPDATE", e=e["id"])
    new_overall = overall_state(v["lock2_status"], lock3)
    conn.execute(text(
        "UPDATE core.enrolment_verification SET lock3_status=:l3, overall_state=:ov, confirmed_at=CASE WHEN :l3='CONFIRMED' THEN :t ELSE confirmed_at END, "
        "channel=coalesce(:ch, channel), channel_trust=coalesce(:tr, channel_trust), updated_at=:t WHERE enrolment_id=:e"),
        {"l3": lock3, "ov": new_overall, "t": now(), "ch": channel, "tr": trust, "e": e["id"]})
    _event(conn, e["id"], e["institution_id"], next_seq(conn, e["id"]), "VERIFICATION_CHANGE", p, None,
           {"lock3": lock3, "overall": new_overall, "channel": channel}, reason_code=reason)
    _enqueue_outbox(conn, "verification.changed", "enrolment", e["id"], e["institution_id"],
                    {"academic_year": e["academic_year"], "overall": new_overall, "lock3": lock3})
    return new_overall


def confirm(p: Principal, act_as: str | None, enrolment_id: str, otp: str) -> dict:
    student_id, is_guardian = resolve_student(p, act_as)
    e = _own_enrolment(student_id, enrolment_id)
    ctx = DbContext(institution_id=str(e["institution_id"]), scope="INSTITUTION", actor_id=p.id, student_id=student_id)
    err: ApiError | None = None
    out: dict = {}
    with tx(ctx, lock_timeout_ms=2000) as conn:
        v = row(conn, "SELECT lock3_status, overall_state FROM core.enrolment_verification WHERE enrolment_id=:e", e=e["id"])
        if v["lock3_status"] == "CONFIRMED":
            return {"state": "VERIFIED" if v["overall_state"] == "VERIFIED" else v["overall_state"], "already_confirmed": True}
        r = row(conn, "SELECT id, otp_hash, otp_salt, otp_expires_at, attempts, status, for_guardian FROM core.confirmation_requests "
                      "WHERE enrolment_id=:e ORDER BY created_at DESC LIMIT 1 FOR UPDATE", e=e["id"])
        t = now()
        if not r:
            err = ApiError(409, "NO_OTP", "No code was sent", "Ask your institution to resend the confirmation code.")
        elif r["for_guardian"] and not is_guardian:
            err = ApiError(403, "GUARDIAN_REQUIRED", "A guardian must confirm", "This student is a minor. A verified guardian confirms on their behalf.")
        elif r["status"] != "SENT":
            err = ApiError(409, "OTP_NOT_ACTIVE", "Code no longer valid", "Request a new code.")
        elif r["otp_expires_at"] < t:
            conn.execute(text("UPDATE core.confirmation_requests SET status='EXPIRED' WHERE id=:i"), {"i": r["id"]})
            err = ApiError(410, "OTP_EXPIRED", "Code expired", "Request a new code.")
        elif not hmac.compare_digest(_hash_otp(otp, r["otp_salt"]), r["otp_hash"]):
            attempts = r["attempts"] + 1
            conn.execute(text("UPDATE core.confirmation_requests SET attempts=:a, status=CASE WHEN :a>=:m THEN 'FAILED' ELSE status END WHERE id=:i"),
                         {"a": attempts, "m": MAX_ATTEMPTS, "i": r["id"]})
            audit.log(conn, "confirmation.otp_failed", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, institution_id=e["institution_id"],
                      request_id=p.request_id, payload={"attempts": attempts})
            err = ApiError(422, "OTP_INVALID", "That code is not correct", f"{max(MAX_ATTEMPTS - attempts, 0)} attempts left.",
                           extra={"attempts_left": max(MAX_ATTEMPTS - attempts, 0)})
        else:
            conn.execute(text("UPDATE core.confirmation_requests SET status='CONFIRMED' WHERE id=:i"), {"i": r["id"]})
            channel = "GUARDIAN" if is_guardian else None
            overall = _set_lock3(conn, e, p, "CONFIRMED", channel=channel)
            audit.log(conn, "confirmation.confirmed", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role,
                      institution_id=e["institution_id"], request_id=p.request_id, payload={"guardian": is_guardian, "overall": overall})
            out = {"state": overall, "lock3": "CONFIRMED"}
    if err:
        raise err
    return out


def dispute(p: Principal, act_as: str | None, enrolment_id: str, note: str | None) -> dict:
    student_id, _ = resolve_student(p, act_as)
    e = _own_enrolment(student_id, enrolment_id)
    ctx = DbContext(institution_id=str(e["institution_id"]), scope="INSTITUTION", actor_id=p.id, student_id=student_id)
    with tx(ctx) as conn:
        overall = _set_lock3(conn, e, p, "DISPUTED", reason="STUDENT_DISPUTE")
        year = e["academic_year"]
        case_id = uuid7()
        n = scalar(conn, "SELECT nextval('intel.case_no_seq')")
        conn.execute(text("INSERT INTO intel.cases (id, case_no, institution_id, type, title, status, enrolment_id, evidence_pack) "
                          "VALUES (:id, :no, CAST(:i AS uuid), 'STUDENT_DISPUTE', 'Student disputes enrolment', 'OPEN', :e, CAST(:ev AS jsonb))"),
                     {"id": case_id, "no": f"CASE-{now().year}-{n:04d}", "i": str(e["institution_id"]), "e": e["id"],
                      "ev": '{"source": "student_dispute"}'})
        conn.execute(text("INSERT INTO intel.case_events (case_id, institution_id, actor_id, event_type, body) VALUES (:c, CAST(:i AS uuid), "
                          "CAST(:a AS uuid), 'OPENED', 'Opened automatically from a student dispute.')"),
                     {"c": case_id, "i": str(e["institution_id"]), "a": p.id})
        audit.log(conn, "confirmation.disputed", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role,
                  institution_id=e["institution_id"], case_id=case_id, justification=note, request_id=p.request_id)
        _enqueue_outbox(conn, "case.opened", "case", case_id, e["institution_id"], {"academic_year": year, "type": "STUDENT_DISPUTE"})
    return {"state": overall, "lock3": "DISPUTED", "case_opened": True}


def assisted_confirm(p: Principal, enrolment_id: str, reason_code: str, witness_note: str) -> dict:
    """REQ-TL-05: institution admin records an audited confirmation for a student without a phone (LOW trust)."""
    if reason_code not in ASSIST_REASONS:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid reason", "Reason is not allowed.",
                       errors=[{"field": "reason_code", "code": "ENUM", "message": f"Use one of {sorted(ASSIST_REASONS)}", "fix_suggestion": "Pick a listed reason."}])
    with tx(p.db_ctx()) as conn:
        e = row(conn, "SELECT id, institution_id, academic_year, status FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        if e["status"] not in ("CLAIMED", "ADMITTED"):
            raise ApiError(409, "INVALID_STATE", "Cannot confirm", "Only live enrolments can be confirmed.")
        overall = _set_lock3(conn, e, p, "CONFIRMED", channel="ASSISTED", trust="LOW", reason=reason_code)
        audit.log(conn, "confirmation.assisted", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role,
                  institution_id=e["institution_id"], justification=witness_note, request_id=p.request_id,
                  payload={"reason_code": reason_code, "channel_trust": "LOW"})
    return {"state": overall, "lock3": "CONFIRMED", "channel": "ASSISTED", "channel_trust": "LOW"}


def resend(p: Principal, act_as: str | None, enrolment_id: str, language: str = "en") -> dict:
    """OTP resend by the student or the institution admin: max 3 sends per hour."""
    if p.utype == "STUDENT":
        student_id, _ = resolve_student(p, act_as)
        e = _own_enrolment(student_id, enrolment_id)
        ctx = DbContext(institution_id=str(e["institution_id"]), scope="INSTITUTION", actor_id=p.id, student_id=student_id)
    else:
        with tx(p.db_ctx()) as conn:
            e = row(conn, "SELECT id, institution_id, student_id, academic_year FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        ctx = p.db_ctx()
        student_id = str(e["student_id"])
    inst = str(e["institution_id"])
    err: ApiError | None = None
    with tx(ctx) as conn:
        enr = row(conn, "SELECT student_id, institution_id FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        r = row(conn, "SELECT id, channel, resend_count, send_window_start, for_guardian FROM core.confirmation_requests WHERE enrolment_id=:e "
                      "ORDER BY created_at DESC LIMIT 1 FOR UPDATE", e=enrolment_id)
        v = row(conn, "SELECT lock3_status FROM core.enrolment_verification WHERE enrolment_id=:e", e=enrolment_id)
        t = now()
        if v and v["lock3_status"] == "CONFIRMED":
            raise ApiError(409, "INVALID_STATE", "Already confirmed", "This enrolment is already confirmed.")
        if not r:
            raise ApiError(409, "NO_OTP_CHANNEL", "No phone on record", "No mobile is available. Use assisted confirmation.")
        window_start, count = r["send_window_start"], r["resend_count"]
        if t - window_start > timedelta(hours=1):
            window_start, count = t, 0
        if count >= MAX_SENDS_PER_HOUR:
            err = ApiError(429, "OTP_RESEND_LIMIT", "Too many codes requested", "You can request 3 codes per hour.", retryable=True,
                           headers={"Retry-After": "3600"})
        else:
            pii = _vault(conn, str(enr["student_id"]), inst)
            mobile = pii.get("registry_mobile") if r["channel"] == "REGISTRY_MOBILE" else pii.get("mobile")
            if not mobile:
                raise ApiError(409, "NO_OTP_CHANNEL", "No phone on record", "No mobile is available. Use assisted confirmation.")
            otp, salt = _otp(), hashlib.sha256(str(uuid7()).encode()).hexdigest()[:16]
            conn.execute(text("UPDATE core.confirmation_requests SET otp_hash=:h, otp_salt=:s, otp_expires_at=:e, attempts=0, status='SENT', "
                              "resend_count=:c, last_sent_at=:t, send_window_start=:w WHERE id=:i"),
                         {"h": _hash_otp(otp, salt), "s": salt, "e": t + timedelta(minutes=OTP_MINUTES), "c": count + 1, "t": t, "w": window_start,
                          "i": r["id"]})
            audit.log(conn, "confirmation.resend", entity_type="enrolment", entity_id=enrolment_id, actor_id=p.id, actor_role=p.role,
                      institution_id=inst, request_id=p.request_id)
    if err:
        raise err
    notify.send_otp(inst, enrolment_id, mobile, otp, r["channel"], language)
    return {"sent": True, "sends_left_this_hour": MAX_SENDS_PER_HOUR - (count + 1)}


def _vault(conn, student_id: str, institution_id: str) -> dict:
    r = row(conn, "SELECT data_key_id, nonce, ciphertext FROM pii.pii_records WHERE student_id=CAST(:s AS uuid) AND institution_id=CAST(:i AS uuid) "
                  "AND ciphertext IS NOT NULL ORDER BY version DESC LIMIT 1", s=student_id, i=institution_id)
    if not r:
        return {}
    return crypto.decrypt(keys.data_key_by_id(str(r["data_key_id"])), r["nonce"], r["ciphertext"], f"{student_id}|{institution_id}".encode())
