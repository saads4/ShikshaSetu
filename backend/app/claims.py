"""Admission Gate (REQ-GATE): claim, validate, admit, withdraw, seat exceptions.

Sequence follows PRD 5.1.1: rules -> identity -> registry call (outside any DB transaction) -> one bounded transaction
that locks the seat-bucket row, enforces exclusivity and cap, and writes ledger, events, verification, PII vault,
audit and outbox rows together.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import audit, crypto, identity, keys, notify, rules_engine
from .config import get_settings
from .core import ApiError, canonical_json, dumps, now, today, uuid7
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal

log = logging.getLogger("shikshasetu.claims")

CONFIRM_DEADLINE_DAYS = 30
FIX_DEADLINE_DAYS = 14
SEAT_WARN_PCT = 90


def current_academic_year(d: date | None = None) -> str:
    d = d or today()
    start = d.year if d.month >= 6 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


class Provisional(BaseModel):
    type: str = Field(pattern="^(NOGEN|REFUSED|FOREIGN)$")
    document_number: str | None = Field(default=None, max_length=40)


class ClaimIn(BaseModel):
    programme_code: str = Field(min_length=1, max_length=40)
    bucket_type: str = Field(default="REGULAR", max_length=20)
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    apaar_id: str | None = Field(default=None, max_length=40)
    provisional: Provisional | None = None
    institution_student_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    dob: date
    gender: str = Field(default="O", max_length=1)
    category: str = Field(default="GEN", max_length=4)
    citizenship_type: str = Field(default="INDIAN", max_length=10)
    nationality_code: str = Field(default="IN", max_length=3)
    parent_name: str | None = Field(default=None, max_length=120)
    mobile: str | None = Field(default=None, max_length=15)
    email: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=60)
    admit_now: bool = False
    admission_date: date | None = None
    row_key: str | None = Field(default=None, max_length=80)

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v):
        if v and not v.replace("+", "").replace(" ", "").isdigit():
            raise ValueError("mobile must contain digits only")
        return v


class _SeatRace(Exception):
    """Internal: the conditional seat UPDATE found the bucket full; the transaction is rolled back."""


def problem(status: int, code: str, title: str, detail: str = "", **extra) -> dict:
    return {"type": f"https://shikshasetu.example/problems/{code}", "title": title, "status": status, "code": code,
            "detail": detail, "retryable": False, "errors": extra.pop("errors", []), **extra}


# ---------------------------------------------------------------- idempotency (REQ-GATE-10)
def idem_begin(p: Principal, key: str | None, route: str, body: dict) -> tuple[str, dict | None]:
    """Returns ('new', None) | ('replay', stored). Raises 409/422 per the PRD."""
    if not key or not (16 <= len(key) <= 64):
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key required", "Send an Idempotency-Key header of 16-64 characters.")
    h = hashlib.sha256(canonical_json(body)).hexdigest()
    with tx(SYSTEM) as conn:
        conn.execute(text("DELETE FROM iam.idempotency_keys WHERE expires_at < :t"), {"t": now()})
        r = row(conn, "INSERT INTO iam.idempotency_keys (actor_id, key, route, request_hash, state, expires_at) "
                      "VALUES (CAST(:a AS uuid), :k, :r, :h, 'IN_PROGRESS', :e) ON CONFLICT DO NOTHING RETURNING key",
                a=p.id, k=key, r=route, h=h, e=now() + timedelta(days=7))
        if r:
            return "new", None
        ex = row(conn, "SELECT request_hash, state, response_status, response_body FROM iam.idempotency_keys "
                       "WHERE actor_id=CAST(:a AS uuid) AND key=:k", a=p.id, k=key)
    if ex["request_hash"] != h:
        raise ApiError(422, "IDEMPOTENCY_MISMATCH", "Idempotency key reused", "This key was used with a different request body.")
    if ex["state"] == "IN_PROGRESS":
        raise ApiError(409, "REQUEST_IN_PROGRESS", "Request in progress", "The original request is still running.", retryable=True)
    return "replay", {"status": ex["response_status"], "body": ex["response_body"]}


def idem_finish(p: Principal, key: str, status: int, body: dict) -> None:
    with tx(SYSTEM) as conn:
        if status >= 500:
            conn.execute(text("DELETE FROM iam.idempotency_keys WHERE actor_id=CAST(:a AS uuid) AND key=:k"), {"a": p.id, "k": key})
        else:
            conn.execute(text("UPDATE iam.idempotency_keys SET state='DONE', response_status=:s, response_body=CAST(:b AS jsonb) "
                              "WHERE actor_id=CAST(:a AS uuid) AND key=:k"), {"s": status, "b": dumps(body), "a": p.id, "k": key})


# ---------------------------------------------------------------- helpers
def _attempt(conn, p: Principal, *, outcome: str, status: int, programme_id=None, bucket=None, source="PORTAL", holder=None,
             ikey: bytes | None = None) -> None:
    conn.execute(text(
        "INSERT INTO core.claim_attempts (institution_id, programme_id, bucket_type, source, outcome, http_status, "
        "conflict_holder_institution_id, identity_key_hash, request_id, created_at) VALUES (CAST(:i AS uuid), :pg, :b, :s, :o, :h, "
        "CAST(:holder AS uuid), :ik, :rid, :t)"),
        {"i": p.institution_id, "pg": programme_id, "b": bucket, "s": source, "o": outcome, "h": status,
         "holder": str(holder) if holder else None, "ik": ikey[:8].hex() if ikey else None, "rid": p.request_id, "t": now()})


def _enqueue_outbox(conn, topic: str, agg_type: str, agg_id: Any, institution_id: Any, payload: dict) -> None:
    conn.execute(text("INSERT INTO ops.outbox_events (topic, aggregate_type, aggregate_id, institution_id, payload) "
                      "VALUES (:t, :a, :i, CAST(:inst AS uuid), CAST(:p AS jsonb))"),
                 {"t": topic, "a": agg_type, "i": str(agg_id), "inst": str(institution_id) if institution_id else None, "p": dumps(payload)})


def _otp() -> str:
    fixed = get_settings().sms_fixed_otp
    return fixed if fixed else f"{secrets.randbelow(900000) + 100000}"


def _hash_otp(otp: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{otp}".encode()).hexdigest()


def _is_minor(dob: date, on: date) -> bool:
    return (on - dob).days < 18 * 365 + 4


# ---------------------------------------------------------------- the gate
def process_claim(p: Principal, body: ClaimIn, *, dry_run: bool, source: str = "PORTAL") -> tuple[int, dict]:
    """Run the full claim. Returns (http_status, body). Business outcomes are returned, not raised, so the
    idempotency layer can store them and the attempt log records them."""
    inst_id = p.institution_id
    s = get_settings()
    year = body.academic_year or current_academic_year()
    t_now = now()

    # --- 3. reference lookups (local tables only)
    with tx(p.db_ctx()) as conn:
        inst = row(conn, "SELECT id, status, name FROM ref.institutions WHERE id=CAST(:i AS uuid)", i=inst_id)
        if not inst or inst["status"] != "ACTIVE":
            return 403, problem(403, "INSTITUTION_NOT_ACTIVE", "Institution not active", "Only active institutions can claim seats.")
        prog = row(conn, "SELECT id, programme_code, level, mode, lateral_entry, status FROM ref.programmes "
                         "WHERE institution_id=CAST(:i AS uuid) AND programme_code=:c", i=inst_id, c=body.programme_code)
        if not prog or prog["status"] != "ACTIVE":
            _attempt(conn, p, outcome="INVALID", status=422, source=source) if not dry_run else None
            return 422, problem(422, "PROGRAMME_NOT_APPROVED", "Programme not approved",
                                "This programme is not approved for the institution.",
                                errors=[{"field": "programme_code", "code": "PROGRAMME_NOT_APPROVED", "message": "Unknown or inactive programme.",
                                         "fix_suggestion": "Pick one of your approved programmes."}])
        bucket = row(conn, "SELECT id, cap, approved_extra, occupied FROM core.seat_buckets WHERE programme_id=:p AND academic_year=:y "
                           "AND bucket_type=:b", p=prog["id"], y=year, b=body.bucket_type)
        window = row(conn, "SELECT opens_at, closes_at FROM ref.submission_windows WHERE academic_year=:y ORDER BY opens_at DESC LIMIT 1", y=year)
        ruleset = rules_engine.active_rule_set(conn)
        exception_pairs = rows(conn, "SELECT level_a, level_b, mode_a, mode_b FROM ref.enrolment_exception_rules WHERE active")
    if not bucket:
        return 422, problem(422, "BUCKET_NOT_FOUND", "No such seat bucket", "This programme has no such bucket for the academic year.",
                            errors=[{"field": "bucket_type", "code": "BUCKET_NOT_FOUND", "message": "Unknown bucket.",
                                     "fix_suggestion": "Choose a bucket with an approved cap."}])

    # --- 4. claim-path rules
    adm_date = body.admission_date or (t_now.date() if body.admit_now else None)
    rec = {"institution_student_id": body.institution_student_id, "name": body.name, "dob": body.dob, "gender": body.gender,
           "category": body.category, "citizenship_type": body.citizenship_type, "nationality_code": body.nationality_code,
           "admission_date": adm_date, "bucket_type": body.bucket_type, "programme_level": prog["level"], "programme_mode": prog["mode"],
           "lateral_entry": prog["lateral_entry"], "parent_name": body.parent_name, "mobile": body.mobile, "today": t_now.date(),
           "age_years": (t_now.date() - body.dob).days // 365, "window_opens": window["opens_at"].date() if window else None,
           "_bucket": bucket}
    findings = rules_engine.evaluate(ruleset["rules"], rec, "C")
    blockers = [f for f in findings if f["severity"] == "Blocker"]
    if blockers:
        out = problem(422, "RULE_BLOCKER", "A rule blocks this claim", "Fix the listed findings and resubmit.",
                      errors=[{"field": (f["evidence"].get("fields") or [""])[0], "code": f["rule_code"], "message": f["message"],
                               "fix_suggestion": f["fix"]} for f in blockers])
        if not dry_run:
            with tx(p.db_ctx()) as conn:
                _attempt(conn, p, outcome="RULE_BLOCKED", status=422, programme_id=prog["id"], bucket=body.bucket_type, source=source)
        return 422, out

    # --- 5. identity hygiene / provisional path
    prov_token = None
    if body.provisional:
        ident_type = "PROVISIONAL"
        if body.provisional.document_number and body.provisional.type == "FOREIGN":
            ikey = crypto.identity_key("DOC:" + body.provisional.document_number.strip().upper())
        else:
            prov_token = "PRV-" + secrets.token_hex(6).upper()
            ikey = crypto.identity_key("PROV:" + prov_token)
        diag = identity.Diagnosis("VERIFIED")  # replaced below; provisional never contacts the registry
        diag.status = {"NOGEN": "NOGEN", "REFUSED": "CONSENT_REFUSED", "FOREIGN": "NOGEN"}[body.provisional.type]
        reg = None
    else:
        ident_type = "APAAR"
        hyg = identity.clean_apaar(body.apaar_id)
        if hyg.issues:
            out = problem(422, "IDENTITY_INVALID", "The APAAR ID is not valid", "See the field-level fixes.",
                          errors=[{"field": i["field"], "code": i["code"], "message": i["message"], "fix_suggestion": i["fix"]}
                                  for i in hyg.issues])
            if not dry_run:
                with tx(p.db_ctx()) as conn:
                    _attempt(conn, p, outcome="INVALID", status=422, programme_id=prog["id"], bucket=body.bucket_type, source=source)
            return 422, out
        ikey = crypto.identity_key(hyg.value)
        # --- 6. registry call: OUTSIDE any DB transaction
        reg = identity.registry_lookup(hyg.value)
        diag = identity.diagnose(reg, body.name, body.dob)
        if diag.status == "INVALID":
            out = problem(422, "IDENTITY_INVALID", "The APAAR ID was not found", "See the fix suggestions.",
                          errors=[{"field": m["field"], "code": m["code"], "message": m["message"], "fix_suggestion": m["fix"]}
                                  for m in diag.messages])
            if not dry_run:
                with tx(p.db_ctx()) as conn:
                    _attempt(conn, p, outcome="INVALID", status=422, programme_id=prog["id"], bucket=body.bucket_type, source=source,
                             ikey=ikey)
            return 422, out

    concurrency = "EXCLUSIVE" if prog["mode"] == "FULL_TIME" else "CONCURRENT_OK"
    enrolment_id = uuid7()
    claim_blob = {"claim_id": str(enrolment_id), "institution_id": inst_id, "programme_id": str(prog["id"]),
                  "bucket": body.bucket_type, "identity_key_hash": ikey.hex(), "claimed_at": t_now.isoformat(), "actor_id": p.id}
    # Lock 1: institution signature with its delegated software key (SIMULATED KMS). Key custody uses a separate connection.
    kid, _, pem = keys.signing_key("INSTITUTION_CLAIM", inst_id)
    sig = crypto.sign_es256(pem, hashlib.sha256(canonical_json(claim_blob)).digest())
    dkey_id, dkey_ver, dkey = keys.data_key(inst_id)

    minor = _is_minor(body.dob, t_now.date())
    key_version = s.identity_hmac_key_version
    row_key = body.row_key or hashlib.sha256(f"{body.institution_student_id}|{body.programme_code}|{year}".encode()).hexdigest()[:32]

    # --- 8. bounded transaction
    try:
        with tx(p.db_ctx(), statement_timeout_ms=3000, lock_timeout_ms=2000) as conn:
            # Non-locking read for the fast path and warnings. The authoritative cap check is the conditional UPDATE at the END of
            # the transaction, so the contended bucket row lock is held for one statement + commit (PRD 5.1.1 step 10, minimal hold).
            b = row(conn, "SELECT cap, approved_extra, occupied FROM core.seat_buckets WHERE id=:b", b=bucket["id"])
            # serialise creation of the same identity across concurrent claims (different buckets/institutions) without a table lock
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": ikey.hex()})
            student = row(conn, "SELECT s.id, s.student_ref FROM core.student_identity_keys k JOIN core.students s ON s.id=k.student_id "
                                "WHERE k.key_version=:v AND k.identity_key=:k", v=key_version, k=ikey)
            same_row = row(conn, "SELECT id, enrolment_ref FROM core.enrolments WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y "
                                 "AND row_key=:rk", i=inst_id, y=year, rk=row_key)
            holder = _holder(conn, year, key_version, ikey) if concurrency == "EXCLUSIVE" else None
            if same_row or (holder and str(holder["institution_id"]) == inst_id):
                ex = same_row or holder
                if not dry_run:
                    _attempt(conn, p, outcome="DUPLICATE_SAME_INSTITUTION", status=200, programme_id=prog["id"], bucket=body.bucket_type,
                             source=source, ikey=ikey)
                return 200, {"outcome": "DUPLICATE_SAME_INSTITUTION", "duplicate": True, "enrolment_id": str(ex["id"]),
                             "enrolment_ref": ex["enrolment_ref"], "dry_run": dry_run,
                             "message": "This student already has a live claim at your institution."}
            if holder:
                def _m(level, mode, lv, md):
                    return lv == level and (md is None or md == mode)
                pair = next((e for e in exception_pairs
                             if (_m(holder["level"], holder["mode"], e["level_a"], e["mode_a"]) and _m(prog["level"], prog["mode"], e["level_b"], e["mode_b"]))
                             or (_m(prog["level"], prog["mode"], e["level_a"], e["mode_a"]) and _m(holder["level"], holder["mode"], e["level_b"], e["mode_b"]))),
                            None)
                if pair:
                    concurrency = "EXCEPTION_APPROVED"  # approved dual-programme pair (REQ-REF-06)
                else:
                    ref = "RES-" + hashlib.sha256(f"{holder['id']}{inst_id}".encode()).hexdigest()[:10].upper()
                    if not dry_run:
                        _attempt(conn, p, outcome="CONFLICT", status=409, programme_id=prog["id"], bucket=body.bucket_type,
                                 source=source, holder=holder["institution_id"], ikey=ikey)
                    contact = row(conn, "SELECT name, public_contact_name, public_contact_email, public_contact_phone FROM ref.institutions "
                                        "WHERE id=:i", i=holder["institution_id"])
                    return 409, problem(409, "CLAIM_CONFLICT", "This student is already claimed elsewhere",
                                        "Another institution holds a live exclusive claim for this student this cycle. No student "
                                        "details are shared.", outcome="CONFLICT", resolution_ref=ref, dry_run=dry_run,
                                        other_institution_contact={"institution": contact["name"], "contact": contact["public_contact_name"],
                                                                   "email": contact["public_contact_email"],
                                                                   "phone": contact["public_contact_phone"]})
            cap_total = b["cap"] + b["approved_extra"]
            if b["occupied"] + 1 > cap_total:
                if not dry_run:
                    _attempt(conn, p, outcome="SEAT_LIMIT", status=409, programme_id=prog["id"], bucket=body.bucket_type, source=source,
                             ikey=ikey)
                return 409, problem(409, "SEAT_LIMIT", "No seats left in this bucket",
                                    "The bucket is at its approved limit. Request an exception from AICTE.", outcome="SEAT_LIMIT",
                                    bucket=body.bucket_type, cap=b["cap"], approved_extra=b["approved_extra"], occupied=b["occupied"],
                                    exception_requestable=True, dry_run=dry_run)
            pct_after = 100 * (b["occupied"] + 1) / cap_total
            warnings = []
            if pct_after >= SEAT_WARN_PCT:
                warnings.append({"code": "SEAT_90_PERCENT", "message": f"This bucket will be {pct_after:.0f}% full after this claim."})

            # lifecycle states from the diagnosis
            if body.provisional:
                lock2, overall, id_status, fix_deadline = "PROVISIONAL_OK", "PENDING", diag.status, None
            elif diag.status == "VERIFIED":
                lock2, overall, id_status, fix_deadline = "PASSED", "PENDING", "VERIFIED", None
            elif diag.status == "PROBABLE_MATCH":
                lock2, overall, id_status, fix_deadline = "PENDING", "PENDING", "PROBABLE_MATCH", None
            elif diag.status == "PENDING_FIX":
                lock2, overall, id_status = "FAILED", "PENDING_FIX", "PENDING_FIX"
                fix_deadline = t_now + timedelta(days=FIX_DEADLINE_DAYS)
            else:
                lock2, overall, id_status, fix_deadline = "PENDING", "PENDING", "PENDING_VERIFICATION", None
            outcome_code = "CLAIMED" if concurrency == "EXCLUSIVE" else concurrency
            attempt_outcome = "CLAIMED_PENDING_VERIFICATION" if id_status == "PENDING_VERIFICATION" else outcome_code

            result: dict[str, Any] = {
                "outcome": outcome_code, "enrolment_id": str(enrolment_id), "status": "ADMITTED" if body.admit_now else "CLAIMED",
                "concurrency_class": concurrency, "identity_status": id_status, "dry_run": dry_run, "warnings": warnings,
                "verification": {"lock1": "SIGNED", "lock2": lock2, "lock3": "PENDING", "overall": overall,
                                 "deadline_at": (t_now + timedelta(days=CONFIRM_DEADLINE_DAYS)).isoformat()},
                "seat": {"bucket_type": body.bucket_type, "cap": b["cap"], "approved_extra": b["approved_extra"],
                         "occupied_after": b["occupied"] + 1, "pct_after": round(pct_after, 1)},
                "fix_list": [{"field": m["field"], "code": m["code"], "message": m["message"], "fix": m["fix"]} for m in diag.messages]
                if not body.provisional else [],
                "findings": [{"rule_code": f["rule_code"], "severity": f["severity"], "message": f["message"]} for f in findings],
                "source": "MOCK_APAAR_REGISTRY" if not body.provisional else None,
                "registry": {"status": reg.status, "breaker": reg.breaker, "label": "SIMULATED registry"} if reg else None,
            }
            if body.provisional:
                result["provisional_token"] = prov_token
            if dry_run:
                return 200, result

            # --- 9..13 writes
            if student:
                student_id = student["id"]
            else:
                student_id = uuid7()
                conn.execute(text("INSERT INTO core.students (id, student_ref, identity_type, identity_status, registry_status) "
                                  "VALUES (:id,:ref,:t,:st,:rs)"),
                             {"id": student_id, "ref": "STU-" + secrets.token_hex(5).upper(), "t": ident_type,
                              "st": id_status if id_status != "PENDING_VERIFICATION" else "PENDING_VERIFICATION",
                              "rs": reg.registry_status if reg else None})
                conn.execute(text("INSERT INTO core.student_identity_keys (id, student_id, key_version, identity_key, kind) "
                                  "VALUES (:id,:s,:v,:k,:kind)"),
                             {"id": uuid7(), "s": student_id, "v": key_version, "k": ikey, "kind": ident_type})
                if body.provisional:
                    doc = body.provisional.document_number
                    conn.execute(text(
                        "INSERT INTO core.provisional_ids (id, student_id, token, type, document_hash, document_last4, expires_at, "
                        "compliance_deadline) VALUES (:id,:s,:t,:ty,:dh,:l4,:e,:c)"),
                        {"id": uuid7(), "s": student_id, "t": prov_token or ("PRV-" + secrets.token_hex(6).upper()),
                         "ty": body.provisional.type, "dh": crypto.keyed_hash("doc", doc.strip().upper()) if doc else None,
                         "l4": doc[-4:] if doc else None, "e": t_now + timedelta(days=365), "c": t_now + timedelta(days=120)})
            enrol_ref = "ENR-" + secrets.token_hex(5).upper()
            try:
                with conn.begin_nested():
                    conn.execute(text(
                        "INSERT INTO core.enrolments (id, enrolment_ref, student_id, institution_id, programme_id, academic_year, seat_bucket_id, "
                        "bucket_type, concurrency_class, status, institution_student_id, admission_date, claimed_at, claimed_by, claim_source, "
                        "row_key, rule_set_id, identity_key, key_version, gender, category, citizenship_type, nationality_code, birth_year, "
                        "is_minor, name_tokens) VALUES (:id,:ref,:st,CAST(:inst AS uuid),:pg,:y,:sb,:bt,:cc,:status,:isid,:ad,:ca,CAST(:cb AS uuid),:src,"
                        ":rk,CAST(:rs AS uuid),:ik,:kv,:g,:c,:ct,:nc,:by,:mn,:nt)"),
                        {"id": enrolment_id, "ref": enrol_ref, "st": student_id, "inst": inst_id, "pg": prog["id"], "y": year, "sb": bucket["id"],
                         "bt": body.bucket_type, "cc": concurrency, "status": "ADMITTED" if body.admit_now else "CLAIMED",
                         "isid": body.institution_student_id, "ad": adm_date, "ca": t_now, "cb": p.id if p.kind == "user" else None,
                         "src": source, "rk": row_key, "rs": ruleset["id"], "ik": ikey, "kv": key_version, "g": body.gender,
                         "c": body.category, "ct": body.citizenship_type, "nc": body.nationality_code, "by": body.dob.year, "mn": minor,
                         "nt": crypto.name_tokens(body.name)})
            except IntegrityError:
                # Lost a race on ux_enrol_exclusive / ux_enrol_programme / row_key: resolve to the holder.
                h = _holder(conn, year, key_version, ikey)
                if h and str(h["institution_id"]) != inst_id:
                    _attempt(conn, p, outcome="CONFLICT", status=409, programme_id=prog["id"], bucket=body.bucket_type, source=source,
                             holder=h["institution_id"], ikey=ikey)
                    ref = "RES-" + hashlib.sha256(f"{h['id']}{inst_id}".encode()).hexdigest()[:10].upper()
                    return 409, problem(409, "CLAIM_CONFLICT", "This student is already claimed elsewhere",
                                        "Another institution holds a live exclusive claim for this student this cycle.",
                                        outcome="CONFLICT", resolution_ref=ref)
                _attempt(conn, p, outcome="DUPLICATE_SAME_INSTITUTION", status=200, programme_id=prog["id"], bucket=body.bucket_type,
                         source=source, ikey=ikey)
                return 200, {"outcome": "DUPLICATE_SAME_INSTITUTION", "duplicate": True,
                             "enrolment_id": str(h["id"]) if h else None, "enrolment_ref": h["enrolment_ref"] if h else None}
            matched = scalar(conn, "SELECT core.match_transfer(:s, :e, CAST(:i AS uuid))", s=student_id, e=enrolment_id, i=inst_id)
            if matched:
                result["transfer_matched"] = True
            _event(conn, enrolment_id, inst_id, 1, "CLAIM", p, ruleset["id"], {"bucket": body.bucket_type, "source": source,
                                                                              "concurrency_class": concurrency})
            if body.admit_now:
                _event(conn, enrolment_id, inst_id, 2, "ADMIT", p, ruleset["id"], {"admission_date": str(adm_date)})
            # verification + confirmation target
            channel, trust, target, phone = _confirmation_target(reg, body, minor)
            conn.execute(text(
                "INSERT INTO core.enrolment_verification (enrolment_id, institution_id, lock1_signature, lock1_kid, lock2_status, lock3_status, "
                "overall_state, identity_status, channel, channel_trust, deadline_at, fix_deadline_at) VALUES (:e, CAST(:i AS uuid), :sig, :kid, "
                ":l2, 'PENDING', :ov, :ids, :ch, :tr, :dl, :fd)"),
                {"e": enrolment_id, "i": inst_id, "sig": sig, "kid": kid, "l2": lock2, "ov": overall, "ids": id_status, "ch": channel,
                 "tr": trust, "dl": t_now + timedelta(days=CONFIRM_DEADLINE_DAYS), "fd": fix_deadline})
            otp = None
            if channel and phone:
                otp = _otp()
                salt = secrets.token_hex(8)
                conn.execute(text(
                    "INSERT INTO core.confirmation_requests (id, enrolment_id, institution_id, channel, target_masked, otp_hash, otp_salt, "
                    "otp_expires_at, last_sent_at, send_window_start, for_guardian) VALUES (:id,:e,CAST(:i AS uuid),:ch,:tm,:h,:s,:ex,:t,:t,:g)"),
                    {"id": uuid7(), "e": enrolment_id, "i": inst_id, "ch": channel, "tm": target, "h": _hash_otp(otp, salt), "s": salt,
                     "ex": t_now + timedelta(minutes=10), "t": t_now, "g": minor})
            # PII vault (encrypted with the institution's data key)
            nonce, ct = crypto.encrypt(dkey, {"name": body.name, "dob": body.dob.isoformat(), "parent_name": body.parent_name,
                                              "mobile": body.mobile, "email": body.email, "district": body.district,
                                              "apaar_id": None if body.provisional else hyg.value,
                                              "registry_mobile": reg.mobile if reg else None},
                                       f"{student_id}|{inst_id}".encode())
            latest = scalar(conn, "SELECT coalesce(max(version),0) FROM pii.pii_records WHERE student_id=:s AND institution_id=CAST(:i AS uuid)",
                            s=student_id, i=inst_id)
            conn.execute(text("INSERT INTO pii.pii_records (id, student_id, institution_id, data_key_id, version, nonce, ciphertext) "
                              "VALUES (:id,:s,CAST(:i AS uuid),CAST(:k AS uuid),:v,:n,:c)"),
                         {"id": uuid7(), "s": student_id, "i": inst_id, "k": dkey_id, "v": latest + 1, "n": nonce, "c": ct})
            # registry check log + diagnosis (no PII)
            if reg:
                rc = uuid7()
                conn.execute(text("INSERT INTO identity.registry_checks (id, enrolment_id, institution_id, source, status, name_score, dob_match, "
                                  "verdict, latency_ms, breaker_state, checked_at) VALUES (:id,:e,CAST(:i AS uuid),'CLAIM',:st,:ns,:dm,:v,:l,:b,:t)"),
                             {"id": rc, "e": enrolment_id, "i": inst_id, "st": reg.status, "ns": diag.name_score, "dm": diag.dob_match,
                              "v": diag.status, "l": reg.latency_ms, "b": reg.breaker, "t": t_now})
                for m in diag.messages:
                    conn.execute(text("INSERT INTO identity.diagnoses (id, registry_check_id, enrolment_id, institution_id, status, field, "
                                      "message_code, message, suggested_fix, deadline_at) VALUES (:id,:rc,:e,CAST(:i AS uuid),:st,:f,:c,:m,"
                                      "CAST(:fx AS jsonb),:d)"),
                                 {"id": uuid7(), "rc": rc, "e": enrolment_id, "i": inst_id, "st": diag.status, "f": m["field"], "c": m["code"],
                                  "m": m["message"], "fx": dumps({"fix": m["fix"]}), "d": fix_deadline})
            # non-blocking findings (Error/Warning/Info) become findings rows (REQ-RUL-06)
            for f in findings:
                conn.execute(text("INSERT INTO rules.findings (id, institution_id, academic_year, rule_id, rule_code, rule_set_id, enrolment_id, "
                                  "severity, message, evidence, suggested_fix) VALUES (:id,CAST(:i AS uuid),:y,CAST(:r AS uuid),:c,CAST(:rs AS uuid),:e,:s,:m,"
                                  "CAST(:ev AS jsonb),CAST(:fx AS jsonb))"),
                             {"id": uuid7(), "i": inst_id, "y": year, "r": f["rule_id"], "c": f["rule_code"], "rs": ruleset["id"], "e": enrolment_id,
                              "s": f["severity"], "m": f["message"], "ev": dumps(f["evidence"]), "fx": dumps({"fix": f["fix"]})})
            if id_status == "PROBABLE_MATCH":
                conn.execute(text("INSERT INTO rules.findings (id, institution_id, academic_year, rule_code, rule_set_id, enrolment_id, severity, "
                                  "message, suggested_fix) VALUES (:id,CAST(:i AS uuid),:y,'IDN-PROBABLE',CAST(:rs AS uuid),:e,'Warning',:m,CAST(:fx AS jsonb))"),
                             {"id": uuid7(), "i": inst_id, "y": year, "rs": ruleset["id"], "e": enrolment_id,
                              "m": "Registry returned a probable (not certain) identity match.",
                              "fx": dumps({"fix": "A reviewer may confirm; correcting the spelling avoids delay."})})
            if findings:
                _enqueue_outbox(conn, "finding.created", "enrolment", enrolment_id, inst_id, {"academic_year": year, "count": len(findings)})
            _attempt(conn, p, outcome=attempt_outcome, status=201, programme_id=prog["id"], bucket=body.bucket_type, source=source, ikey=ikey)
            audit.log(conn, "claim.created", entity_type="enrolment", entity_id=enrolment_id, actor_id=p.id, actor_role=p.role,
                      institution_id=inst_id, request_id=p.request_id,
                      payload={"programme_code": body.programme_code, "bucket": body.bucket_type, "outcome": outcome_code,
                               "identity_status": id_status, "source": source})
            _enqueue_outbox(conn, "claim.created", "enrolment", enrolment_id, inst_id,
                            {"academic_year": year, "bucket_type": body.bucket_type, "programme_id": str(prog["id"]), "outcome": outcome_code,
                             "identity_status": id_status})
            # Authoritative seat check: one conditional UPDATE takes the bucket row lock; it fails (no row) when the bucket is full.
            upd = row(conn, "UPDATE core.seat_buckets SET occupied=occupied+1, cumulative_admitted=cumulative_admitted+:adm "
                            "WHERE id=:b AND occupied < cap + approved_extra RETURNING occupied", b=bucket["id"], adm=1 if body.admit_now else 0)
            if not upd:
                raise _SeatRace()
            conn.execute(text(
                "INSERT INTO core.seat_ledger_entries (seat_bucket_id, institution_id, entry_type, delta, occupied_after, enrolment_id, actor_id, "
                "effective_date) VALUES (:b, CAST(:i AS uuid), 'DEBIT', 1, :oa, :e, CAST(:a AS uuid), :d)"),
                {"b": bucket["id"], "i": inst_id, "oa": upd["occupied"], "e": enrolment_id, "a": p.id if p.kind == "user" else None,
                 "d": t_now.date()})
            result["seat"]["occupied_after"] = upd["occupied"]
            result["enrolment_ref"] = enrol_ref
            result["message"] = "Seat claimed. Waiting for the student's confirmation (Lock 3)." if overall != "PENDING_FIX" \
                else "Seat claimed with identity fixes required before the deadline."
    except ApiError:
        raise
    except _SeatRace:
        # Lost the race for the last seat: everything rolled back; record the attempt and report the live bucket state.
        with tx(p.db_ctx()) as conn:
            cur = row(conn, "SELECT cap, approved_extra, occupied FROM core.seat_buckets WHERE id=:b", b=bucket["id"])
            _attempt(conn, p, outcome="SEAT_LIMIT", status=409, programme_id=prog["id"], bucket=body.bucket_type, source=source, ikey=ikey)
        return 409, problem(409, "SEAT_LIMIT", "No seats left in this bucket", "The bucket is at its approved limit. Request an exception from AICTE.",
                            outcome="SEAT_LIMIT", bucket=body.bucket_type, cap=cur["cap"], approved_extra=cur["approved_extra"], occupied=cur["occupied"],
                            exception_requestable=True, dry_run=False)
    except Exception as exc:  # DB timeout / lock timeout / unexpected
        msg = str(exc).lower()
        if "lock timeout" in msg or "statement timeout" in msg or "canceling statement" in msg:
            return 503, problem(503, "SERVICE_BUSY", "The seat ledger is busy", "Retry in a moment.", retryable=True)
        raise
    # --- post-commit, best effort: OTP delivery through the (mock) SMS gateway
    if otp and phone:
        notify.send_otp(inst_id, str(enrolment_id), phone, otp, channel, language="en")
    return 201, result


def _event(conn, enrolment_id, inst_id, seq: int, etype: str, p: Principal, rule_set_id, payload: dict, *, reason_code=None,
           reason_text=None, effective_date=None) -> None:
    conn.execute(text(
        "INSERT INTO core.enrolment_events (enrolment_id, institution_id, seq, event_type, reason_code, reason_text, payload, actor_id, rule_set_id, "
        "occurred_at, effective_date) VALUES (:e, CAST(:i AS uuid), :seq, :t, :rc, :rt, CAST(:p AS jsonb), CAST(:a AS uuid), CAST(:rs AS uuid), :at, :ed)"),
        {"e": enrolment_id, "i": inst_id, "seq": seq, "t": etype, "rc": reason_code, "rt": reason_text, "p": dumps(payload),
         "a": p.id if p and p.kind == "user" else None, "rs": str(rule_set_id) if rule_set_id else None, "at": now(), "ed": effective_date})


def _holder(conn, year: str, key_version: int, ikey: bytes) -> dict | None:
    """Live exclusive claim for this identity, across ALL institutions (SECURITY DEFINER; identifiers only, REQ-GATE-12)."""
    r = row(conn, "SELECT enrolment_id AS id, institution_id, enrolment_ref, level, mode FROM core.exclusive_holder(:y, :v, :k)",
            y=year, v=key_version, k=ikey)
    return r


def _confirmation_target(reg, body: ClaimIn, minor: bool):
    """REQ-TL-03: registry-held mobile first (HIGH trust); institution-supplied number is LOW trust."""
    if reg is not None and reg.status == "FOUND" and reg.mobile:
        return "REGISTRY_MOBILE", "HIGH", identity.mask_mobile(reg.mobile), reg.mobile
    if body.mobile:
        return "INSTITUTION_MOBILE", "LOW", identity.mask_mobile(body.mobile), body.mobile
    return None, None, None, None


def next_seq(conn, enrolment_id) -> int:
    return scalar(conn, "SELECT coalesce(max(seq),0)+1 FROM core.enrolment_events WHERE enrolment_id=:e", e=enrolment_id)


# ---------------------------------------------------------------- admit / withdraw
def admit(p: Principal, enrolment_id: str, admission_date: date | None) -> dict:
    with tx(p.db_ctx(), statement_timeout_ms=3000, lock_timeout_ms=2000) as conn:
        e = row(conn, "SELECT id, status, seat_bucket_id, academic_year FROM core.enrolments WHERE id=CAST(:e AS uuid) FOR UPDATE", e=enrolment_id)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        if e["status"] != "CLAIMED":
            raise ApiError(409, "INVALID_STATE", "Cannot admit", f"Only CLAIMED enrolments can be admitted (this one is {e['status']}).")
        d = admission_date or today()
        if d > today():
            raise ApiError(422, "RULE_BLOCKER", "Admission date is in the future", "Use today's date or earlier.",
                           errors=[{"field": "admission_date", "code": "FLD-004", "message": "Admission date cannot be in the future.",
                                    "fix_suggestion": "Use today's date or earlier."}])
        conn.execute(text("UPDATE core.enrolments SET status='ADMITTED', admission_date=:d, row_version=row_version+1, updated_at=:t WHERE id=:e"),
                     {"d": d, "t": now(), "e": e["id"]})
        conn.execute(text("UPDATE core.seat_buckets SET cumulative_admitted=cumulative_admitted+1 WHERE id=:b"), {"b": e["seat_bucket_id"]})
        _event(conn, e["id"], p.institution_id, next_seq(conn, e["id"]), "ADMIT", p, None, {"admission_date": str(d)}, effective_date=d)
        audit.log(conn, "enrolment.admit", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role,
                  institution_id=p.institution_id, request_id=p.request_id)
        _enqueue_outbox(conn, "claim.admitted", "enrolment", e["id"], p.institution_id, {"academic_year": e["academic_year"]})
    return {"enrolment_id": enrolment_id, "status": "ADMITTED", "admission_date": str(d)}


WITHDRAW_REASONS = {"STUDENT_WITHDREW", "ADMISSION_CANCELLED", "FEE_NOT_PAID", "DATA_ERROR", "OTHER"}


def withdraw(p: Principal, enrolment_id: str, reason_code: str, effective_date: date | None, note: str | None,
             *, aicte_release: bool = False, transfer_out: bool = False) -> dict:
    if reason_code not in WITHDRAW_REASONS and not aicte_release and not transfer_out:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid reason", "Reason code is not allowed.",
                       errors=[{"field": "reason_code", "code": "ENUM", "message": f"Use one of {sorted(WITHDRAW_REASONS)}",
                                "fix_suggestion": "Pick a listed reason."}])
    with tx(p.db_ctx(), statement_timeout_ms=3000, lock_timeout_ms=2000) as conn:
        e = row(conn, "SELECT id, status, seat_bucket_id, institution_id, academic_year, claimed_at FROM core.enrolments "
                      "WHERE id=CAST(:e AS uuid) FOR UPDATE", e=enrolment_id)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
        if e["status"] not in ("CLAIMED", "ADMITTED"):
            raise ApiError(409, "INVALID_STATE", "Cannot withdraw", f"This enrolment is already {e['status']}.")
        if transfer_out and e["status"] != "ADMITTED":
            raise ApiError(409, "INVALID_STATE", "Cannot transfer out", "Only an ADMITTED student can be transferred out.")
        eff = effective_date or today()
        if eff < e["claimed_at"].date():
            raise ApiError(422, "RULE_BLOCKER", "Effective date too early", "The effective date cannot precede the claim date.",
                           errors=[{"field": "effective_date", "code": "WDR-001", "message": "Effective date precedes the claim date.",
                                    "fix_suggestion": "Use a date on or after the claim date."}])
        b = row(conn, "SELECT occupied FROM core.seat_buckets WHERE id=:b FOR UPDATE", b=e["seat_bucket_id"])
        conn.execute(text("UPDATE core.enrolments SET status=:st, withdrawn_reason=:r, withdrawn_at=:t, row_version=row_version+1, "
                          "updated_at=:t WHERE id=:e"), {"st": "TRANSFERRED_OUT" if transfer_out else "WITHDRAWN", "r": "TRANSFER_OUT" if transfer_out else "AICTE_RELEASE" if aicte_release else reason_code,
                                                         "t": now(), "e": e["id"]})
        conn.execute(text("UPDATE core.seat_buckets SET occupied=occupied-1 WHERE id=:b"), {"b": e["seat_bucket_id"]})
        conn.execute(text("INSERT INTO core.seat_ledger_entries (seat_bucket_id, institution_id, entry_type, delta, occupied_after, enrolment_id, "
                          "reason, actor_id, effective_date) VALUES (:b,:i,'RELEASE',-1,:oa,:e,:r,CAST(:a AS uuid),:d)"),
                     {"b": e["seat_bucket_id"], "i": e["institution_id"], "oa": b["occupied"] - 1, "e": e["id"], "r": reason_code,
                      "a": p.id, "d": eff})
        _event(conn, e["id"], e["institution_id"], next_seq(conn, e["id"]), "TRANSFER_OUT" if transfer_out else "SEAT_RELEASE" if aicte_release else "WITHDRAW", p, None,
               {"reason": reason_code}, reason_code=reason_code, reason_text=note, effective_date=eff)
        conn.execute(text("UPDATE core.confirmation_requests SET status='EXPIRED' WHERE enrolment_id=:e AND status='SENT'"), {"e": e["id"]})
        from . import credentials as _cred          # an ended enrolment auto-revokes its credential (REQ-CRD-06)
        _cred.revoke_for_enrolment(conn, e["id"], "ENROLMENT_ENDED", actor_id=p.id if p.kind == "user" else None, actor_role=p.role, request_id=p.request_id)
        audit.log(conn, "enrolment.seat_release" if aicte_release else "enrolment.withdraw", entity_type="enrolment", entity_id=e["id"],
                  actor_id=p.id, actor_role=p.role, institution_id=e["institution_id"], justification=note, request_id=p.request_id,
                  payload={"reason_code": reason_code})
        _enqueue_outbox(conn, "claim.withdrawn", "enrolment", e["id"], e["institution_id"], {"academic_year": e["academic_year"]})
        if transfer_out:
            sid = scalar(conn, "SELECT student_id FROM core.enrolments WHERE id=:e", e=e["id"])
            conn.execute(text("INSERT INTO core.transfers (id, student_id, out_enrolment_id, out_institution_id, effective_date_out, status, orphan_after) VALUES "
                              "(:id,:s,:e,:i,:d,'OUT_RECORDED',:o)"), {"id": uuid7(), "s": sid, "e": e["id"], "i": e["institution_id"], "d": eff, "o": now() + timedelta(days=30)})
    return {"enrolment_id": enrolment_id, "status": "TRANSFERRED_OUT" if transfer_out else "WITHDRAWN", "effective_date": str(eff)}


# ---------------------------------------------------------------- seat exceptions (REQ-GATE-07)
def request_exception(p: Principal, programme_code: str, bucket_type: str, extra_seats: int, justification: str,
                      evidence_note: str | None, academic_year: str | None) -> dict:
    year = academic_year or current_academic_year()
    with tx(p.db_ctx()) as conn:
        b = row(conn, "SELECT b.id, b.cap, b.approved_extra, b.occupied FROM core.seat_buckets b JOIN ref.programmes pr ON pr.id=b.programme_id "
                      "WHERE pr.institution_id=CAST(:i AS uuid) AND pr.programme_code=:c AND b.academic_year=:y AND b.bucket_type=:t",
                i=p.institution_id, c=programme_code, y=year, t=bucket_type)
        if not b:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such seat bucket.")
        if 100 * b["occupied"] / max(b["cap"] + b["approved_extra"], 1) < 90:
            raise ApiError(409, "INVALID_STATE", "Exception not needed yet", "Exceptions can be requested once a bucket is 90% full.")
        pending = scalar(conn, "SELECT count(*) FROM core.seat_exceptions WHERE seat_bucket_id=:b AND status='PENDING'", b=b["id"])
        if pending:
            raise ApiError(409, "INVALID_STATE", "Already requested", "A request for this bucket is already pending.")
        xid = uuid7()
        conn.execute(text("INSERT INTO core.seat_exceptions (id, institution_id, seat_bucket_id, requested_by, extra_seats, justification, evidence_note) "
                          "VALUES (:id, CAST(:i AS uuid), :b, CAST(:u AS uuid), :n, :j, :ev)"),
                     {"id": xid, "i": p.institution_id, "b": b["id"], "u": p.id, "n": extra_seats, "j": justification, "ev": evidence_note})
        audit.log(conn, "seat_exception.requested", entity_type="seat_exception", entity_id=xid, actor_id=p.id, actor_role=p.role,
                  institution_id=p.institution_id, request_id=p.request_id, justification=justification,
                  payload={"extra_seats": extra_seats, "bucket": bucket_type})
    return {"id": str(xid), "status": "PENDING"}


def decide_exception(p: Principal, exception_id: str, approve: bool, approved_seats: int | None, reason: str, expires_days: int = 90) -> dict:
    with tx(p.db_ctx(), statement_timeout_ms=3000, lock_timeout_ms=2000) as conn:
        x = row(conn, "SELECT * FROM core.seat_exceptions WHERE id=CAST(:x AS uuid) FOR UPDATE", x=exception_id)
        if not x:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such request.")
        if x["status"] != "PENDING":
            raise ApiError(409, "INVALID_STATE", "Already decided", f"This request is {x['status']}.")
        seats = min(approved_seats or x["extra_seats"], x["extra_seats"])
        if approve:
            b = row(conn, "SELECT occupied, cap, approved_extra FROM core.seat_buckets WHERE id=:b FOR UPDATE", b=x["seat_bucket_id"])
            conn.execute(text("UPDATE core.seat_buckets SET approved_extra=approved_extra+:n WHERE id=:b"), {"n": seats, "b": x["seat_bucket_id"]})
            conn.execute(text("INSERT INTO core.seat_ledger_entries (seat_bucket_id, institution_id, entry_type, delta, occupied_after, exception_id, "
                              "reason, actor_id, effective_date) VALUES (:b,:i,'EXCEPTION_GRANT',:n,:oa,:x,:r,CAST(:a AS uuid),:d)"),
                         {"b": x["seat_bucket_id"], "i": x["institution_id"], "n": seats, "oa": b["occupied"], "x": x["id"], "r": reason,
                          "a": p.id, "d": today()})
        conn.execute(text("UPDATE core.seat_exceptions SET status=:s, approved_seats=:n, decided_by=CAST(:u AS uuid), decided_at=:t, "
                          "decision_reason=:r, expires_at=:e, row_version=row_version+1 WHERE id=:x"),
                     {"s": "APPROVED" if approve else "REJECTED", "n": seats if approve else None, "u": p.id, "t": now(), "r": reason,
                      "e": now() + timedelta(days=expires_days) if approve else None, "x": x["id"]})
        audit.log(conn, "seat_exception.decided", entity_type="seat_exception", entity_id=x["id"], actor_id=p.id, actor_role=p.role,
                  institution_id=x["institution_id"], justification=reason, request_id=p.request_id,
                  payload={"approved": approve, "seats": seats if approve else 0})
        year = scalar(conn, "SELECT academic_year FROM core.seat_buckets WHERE id=:b", b=x["seat_bucket_id"])
        _enqueue_outbox(conn, "seat_exception.decided", "seat_exception", x["id"], x["institution_id"], {"approved": approve, "academic_year": year})
    return {"id": exception_id, "status": "APPROVED" if approve else "REJECTED", "approved_seats": seats if approve else None}
