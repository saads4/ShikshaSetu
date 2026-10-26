"""Purpose-bound, privacy-preserving verification (REQ-VER). Used by the separate verify-api process.

Answers are signed minimal YES/NO messages. Unknown references, missing consent and similar cases all return the identical NEUTRAL body.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta

from sqlalchemy import text

from . import audit, credentials, crypto, keys
from .core import ApiError, canonical_json, dumps, now, uuid7
from .db import SYSTEM, row, rows, scalar, tx

ANSWER_TTL = 300
NEUTRAL = {"result": "NEUTRAL", "message": "No verifiable answer is available for this request."}
PURPOSES = ["EMPLOYMENT_VERIFICATION", "LOAN_ELIGIBILITY", "VISA_SUPPORT", "ADMISSION_TRANSFER", "REGULATORY_CHECK"]
QUESTIONS = ["IS_ENROLLED", "PROGRAMME_MATCH", "ENROLLED_IN_YEAR"]
MIN_RESPONSE_S = 0.08                         # pads fast and slow paths so timing does not reveal whether a reference exists


def hash_token(token: str) -> str:
    return hashlib.sha256(("share:" + token).encode()).hexdigest()


def _pad(t0: float) -> None:
    rest = MIN_RESPONSE_S - (time.monotonic() - t0)
    if rest > 0:
        time.sleep(rest)


def _answer_jwt(aud: str, ref: str, question: dict, answer: str, fields: dict, nonce: str) -> tuple[str, int]:
    kid, _, pem = keys.signing_key("VERIFIER_ANSWER")
    t = int(now().timestamp())
    payload = {"iss": "shikshasetu-verify", "aud": aud, "jti": str(uuid7()), "nonce": nonce, "iat": t, "exp": t + ANSWER_TTL, "credential_ref": ref, "question": question, "answer": answer,
               "fields": fields, "status_list_checked_at": t}
    return credentials._jws({"alg": "ES256", "kid": kid, "typ": "verifier-answer+jwt"}, payload, pem), t + ANSWER_TTL


def _evaluate(conn, c: dict, question: dict) -> tuple[bool, dict | None]:
    """Live checks (REQ-VER-04). Returns (credential live?, enrolment facts)."""
    e = row(conn, "SELECT e.status, e.academic_year, v.overall_state, pr.programme_code, pr.name AS programme_name, i.name AS institution_name FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id "
                  "JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=e.institution_id WHERE e.id=:e", e=c["enrolment_id"])
    sl = row(conn, "SELECT bitstring FROM cred.status_lists WHERE id=:i", i=c["status_list_id"])
    revoked_bit = credentials.bit_is_set(bytes(sl["bitstring"]), c["status_list_index"]) if sl else False
    live = c["status"] == "VALID" and not revoked_bit and c["valid_until"] > now() and e["status"] == "ADMITTED" and e["overall_state"] == "VERIFIED"
    return live, e


def _yes_no(live: bool, e: dict, question: dict) -> bool:
    if not live:
        return False
    t = question.get("type")
    if t == "IS_ENROLLED":
        return True
    if t == "PROGRAMME_MATCH":
        return str(question.get("programme_code", "")).strip().lower() == str(e["programme_code"]).lower()
    if t == "ENROLLED_IN_YEAR":
        return str(question.get("academic_year", "")).strip() == e["academic_year"]
    return False


def _facts(e: dict, allowed: list[str]) -> dict:
    full = {"institution_name": e["institution_name"], "programme_name": e["programme_name"], "programme_code": e["programme_code"], "academic_year": e["academic_year"],
            "enrolment_status": e["status"], "approved_seat": True}
    return {k: v for k, v in full.items() if k in allowed}


def verify(org: dict, *, ref: str | None = None, token: str | None = None, purpose: str, question: dict, nonce: str | None, ip: str) -> dict:
    """org = {id, user_id, name, status}. Raises ApiError for errors that must be explicit (suspended, not allowed, rate limited)."""
    t0 = time.monotonic()
    nonce = nonce or secrets.token_urlsafe(12)
    if purpose not in PURPOSES or question.get("type") not in QUESTIONS:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid request", "Name a known purpose and question type.",
                       errors=[{"field": "purpose" if purpose not in PURPOSES else "question.type", "code": "ENUM", "message": "Not allowed.", "fix_suggestion": f"Use one of {PURPOSES if purpose not in PURPOSES else QUESTIONS}."}])
    with tx(SYSTEM) as conn:
        o = row(conn, "SELECT status FROM iam.verifier_orgs WHERE id=CAST(:o AS uuid)", o=org["id"])
        if not o or o["status"] != "ACTIVE":
            raise ApiError(403, "ORG_SUSPENDED", "Organisation suspended", "This verifier organisation is not active.")
        scope = row(conn, "SELECT allowed_fields, allowed_questions, hourly_limit FROM iam.verifier_scopes WHERE verifier_org_id=CAST(:o AS uuid) AND purpose_code=:p", o=org["id"], p=purpose)
        if not scope or question["type"] not in scope["allowed_questions"]:
            raise ApiError(403, "PURPOSE_NOT_ALLOWED", "Not allowed for this organisation", "Your organisation is not approved for this purpose or question.")
        used = scalar(conn, "SELECT count(*) FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) AND created_at > :t", o=org["id"], t=now() - timedelta(hours=1))
        if used >= scope["hourly_limit"]:
            raise ApiError(429, "RATE_LIMITED", "Hourly limit reached", "Your organisation's hourly verification limit is used up.", retryable=True, headers={"Retry-After": "3600"})
        result, answer_jwt, exp, fields_out, cred_id, student_id, unknown = _run(conn, org, ref, token, purpose, question, nonce, scope, "SHARE_LINK" if token else "CREDENTIAL_REF")
        conn.execute(text("INSERT INTO cred.verification_requests (id, verifier_org_id, verifier_user_id, credential_id, student_id, via, purpose_code, question, outcome, fields_disclosed, nonce, expires_at, ip_hash, unknown_ref) "
                          "VALUES (:id, CAST(:o AS uuid), CAST(:u AS uuid), :c, :s, :via, :p, CAST(:q AS jsonb), :out, :f, :n, :e, :ip, :un)"),
                     {"id": uuid7(), "o": org["id"], "u": org.get("user_id"), "c": cred_id, "s": student_id, "via": "SHARE_LINK" if token else "CREDENTIAL_REF", "p": purpose, "q": dumps(question), "out": result,
                      "f": list(fields_out), "n": nonce, "e": now() + timedelta(seconds=ANSWER_TTL) if answer_jwt else None, "ip": ip, "un": unknown})
        _abuse_check(conn, org, unknown)
    _pad(t0)
    if result == "NEUTRAL":
        return dict(NEUTRAL)
    return {"result": result, "answer_jwt": answer_jwt, "fields": fields_out, "disclosure": {"shared": len(fields_out), "of": len(credentials.FIELD_CATALOGUE), "fields": sorted(fields_out)},
            "nonce": nonce, "expires_at": exp, "boundary": credentials.BOUNDARY}


def _run(conn, org, ref, token, purpose, question, nonce, scope, via):
    link = None
    if token:
        link = row(conn, "SELECT id, credential_id, student_id, purpose_code, fields, expires_at, revoked_at FROM cred.share_links WHERE token_hash=:h", h=hash_token(token))
        if not link or link["revoked_at"] or link["expires_at"] < now() or link["purpose_code"] != purpose:
            return "NEUTRAL", None, None, {}, None, None, True
        c = row(conn, "SELECT * FROM cred.credentials WHERE id=:c", c=link["credential_id"])
    else:
        c = row(conn, "SELECT * FROM cred.credentials WHERE credential_ref=:r", r=ref)
    if not c:
        return "NEUTRAL", None, None, {}, None, None, True
    live, e = _evaluate(conn, c, question)
    consent_fields: list[str] | None = None
    if link:
        consent_fields = list(link["fields"])
        conn.execute(text("UPDATE cred.share_links SET use_count=use_count+1 WHERE id=:i"), {"i": link["id"]})
    else:
        cs = row(conn, "SELECT scope FROM consent.consents WHERE student_id=:s AND kind='SHARING' AND status='GRANTED' AND verifier_org_id=CAST(:o AS uuid) AND purpose_code=:p AND (expires_at IS NULL OR expires_at > :t) "
                       "ORDER BY granted_at DESC LIMIT 1", s=c["student_id"], o=org["id"], p=purpose, t=now())
        if cs:
            consent_fields = list(cs["scope"].get("fields", []))
        else:   # silently create a consent request in the student's wallet (REQ-VER-05)
            conn.execute(text("INSERT INTO consent.consent_requests (id, student_id, verifier_org_id, purpose_code, question) VALUES (:id,:s,CAST(:o AS uuid),:p,CAST(:q AS jsonb)) ON CONFLICT DO NOTHING"),
                         {"id": uuid7(), "s": c["student_id"], "o": org["id"], "p": purpose, "q": dumps(question)})
            return "NEUTRAL", None, None, {}, c["id"], c["student_id"], False
    if c["status"] != "VALID" and not live:
        pass
    ok = _yes_no(live, e, question)
    allowed = [f for f in scope["allowed_fields"] if f in (consent_fields or []) and f in credentials.DISCLOSABLE]
    facts = _facts(e, allowed) if ok else {}
    jwt, exp = _answer_jwt(str(org["id"]), c["credential_ref"], question, "YES" if ok else "NO", facts, nonce)
    return ("YES" if ok else "NO"), jwt, exp, facts, c["id"], c["student_id"], False


def _abuse_check(conn, org, unknown: bool) -> None:
    hour = scalar(conn, "SELECT count(*) FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) AND created_at > :t", o=org["id"], t=now() - timedelta(hours=1))
    week = scalar(conn, "SELECT count(*) FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) AND created_at > :t", o=org["id"], t=now() - timedelta(days=7)) or 0
    unk = scalar(conn, "SELECT count(*) FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) AND unknown_ref AND created_at > :t", o=org["id"], t=now() - timedelta(minutes=10))
    reason = "burst over 3x the 7-day hourly average" if hour >= 20 and hour > 3 * (week / 168) else "more than 30 distinct unknown references in 10 minutes" if unk > 30 else None
    if reason and not scalar(conn, "SELECT count(*) FROM audit.audit_log WHERE action='verifier.abuse_alert' AND entity_id=:o AND occurred_at > :t", o=str(org["id"]), t=now() - timedelta(minutes=10)):
        audit.log(conn, "verifier.abuse_alert", entity_type="verifier_org", entity_id=org["id"], payload={"reason": reason, "last_hour": hour, "unknown_10min": unk})


def public_verify(token: str, question: dict, ip: str) -> dict:
    """POST /public/verify (REQ-VER-10): anyone holding a student-issued share token gets the same signed minimal answer, limited to the link's fields."""
    t0 = time.monotonic()
    if question.get("type") not in QUESTIONS:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid question", "Use IS_ENROLLED, PROGRAMME_MATCH or ENROLLED_IN_YEAR.")
    with tx(SYSTEM) as conn:
        link = row(conn, "SELECT id, credential_id, student_id, purpose_code, fields, expires_at, revoked_at FROM cred.share_links WHERE token_hash=:h", h=hash_token(token))
        if not link or link["revoked_at"] or link["expires_at"] < now():
            conn.execute(text("INSERT INTO cred.verification_requests (id, via, question, outcome, ip_hash, unknown_ref) VALUES (:id,'PUBLIC',CAST(:q AS jsonb),'NEUTRAL',:ip,true)"), {"id": uuid7(), "q": dumps(question), "ip": ip})
            result = None
        else:
            c = row(conn, "SELECT * FROM cred.credentials WHERE id=:c", c=link["credential_id"])
            live, e = _evaluate(conn, c, question)
            ok = _yes_no(live, e, question)
            facts = _facts(e, [f for f in link["fields"] if f in credentials.DISCLOSABLE]) if ok else {}
            nonce = secrets.token_urlsafe(12)
            jwt, exp = _answer_jwt("public", c["credential_ref"], question, "YES" if ok else "NO", facts, nonce)
            conn.execute(text("UPDATE cred.share_links SET use_count=use_count+1 WHERE id=:i"), {"i": link["id"]})
            conn.execute(text("INSERT INTO cred.verification_requests (id, credential_id, student_id, via, purpose_code, question, outcome, fields_disclosed, nonce, expires_at, ip_hash) "
                              "VALUES (:id,:c,:s,'SHARE_LINK',:p,CAST(:q AS jsonb),:o,:f,:n,:e,:ip)"),
                         {"id": uuid7(), "c": c["id"], "s": c["student_id"], "p": link["purpose_code"], "q": dumps(question), "o": "YES" if ok else "NO", "f": list(facts), "n": nonce, "e": now() + timedelta(seconds=ANSWER_TTL), "ip": ip})
            result = {"result": "YES" if ok else "NO", "answer_jwt": jwt, "fields": facts, "disclosure": {"shared": len(facts), "of": len(credentials.FIELD_CATALOGUE), "fields": sorted(facts)},
                      "nonce": nonce, "expires_at": exp, "boundary": credentials.BOUNDARY}
    _pad(t0)
    return result or dict(NEUTRAL)


def verify_answer(jwt_token: str, jwks: dict, *, expected_nonce: str | None = None, now_ts: int | None = None) -> dict:
    """What a verifier must do with an answer: check the signature, expiry and (if they supplied one) the nonce."""
    h, b, s = jwt_token.split(".")
    import json
    header, payload = json.loads(crypto.b64u_dec(h)), json.loads(crypto.b64u_dec(b))
    ok = crypto.verify_es256(jwks[header["kid"]], f"{h}.{b}".encode(), s)
    t = now_ts or int(now().timestamp())
    return {"signature_valid": ok, "expired": t > payload["exp"], "nonce_ok": expected_nonce is None or payload["nonce"] == expected_nonce, "payload": payload}
