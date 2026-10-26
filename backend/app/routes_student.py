"""Student and guardian endpoints (PRD 8.8): own enrolments, Lock 3 confirmation, dispute, guardian mode."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from . import identity, verification
from .core import ApiError
from .db import SYSTEM, DbContext, rows, tx
from .deps import Principal, ensure_uuid, secured

router = APIRouter(prefix="/api/v1/student", tags=["student"])


class ConfirmIn(BaseModel):
    otp: str = Field(pattern=r"^\d{6}$")


class DisputeIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("/enrolments")
def my_enrolments(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    sid, is_guardian = verification.resolve_student(p, x_act_as_student)
    with tx(DbContext(student_id=sid)) as conn:
        data = rows(conn, "SELECT e.id, e.enrolment_ref, e.status, e.academic_year, e.claimed_at, e.bucket_type, e.is_minor, pr.name AS programme_name, "
                          "pr.level, i.name AS institution_name, i.state, v.lock2_status, v.lock3_status, v.overall_state, v.identity_status, v.channel, "
                          "v.channel_trust, v.deadline_at, v.fix_deadline_at, c.target_masked, c.attempts, c.status AS otp_status, c.otp_expires_at, "
                          "c.for_guardian FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=e.institution_id "
                          "JOIN core.enrolment_verification v ON v.enrolment_id=e.id LEFT JOIN LATERAL (SELECT * FROM core.confirmation_requests r "
                          "WHERE r.enrolment_id=e.id ORDER BY created_at DESC LIMIT 1) c ON true "
                          "WHERE e.student_id=CAST(:s AS uuid) ORDER BY e.claimed_at DESC", s=sid)
    items = []
    for d in data:
        can_confirm = d["status"] in ("CLAIMED", "ADMITTED") and d["lock3_status"] in ("PENDING", "EXPIRED") and (not d["for_guardian"] or is_guardian)
        items.append({**d, "id": str(d["id"]), "can_confirm": can_confirm, "needs_guardian": bool(d["for_guardian"]) and not is_guardian,
                      "attempts_left": (verification.MAX_ATTEMPTS - d["attempts"]) if d["attempts"] is not None else None})
    return {"acting_for_minor": is_guardian, "items": items,
            "notice": "APAAR is never mandatory for being counted. Confirming here attests your enrolment; it is not consent to share your data."}


@router.post("/enrolments/{enrolment_id}/confirm")
def confirm(enrolment_id: str, body: ConfirmIn, p: Principal = Depends(secured("student:confirm")),
            x_act_as_student: str | None = Header(default=None)):
    return verification.confirm(p, x_act_as_student, ensure_uuid(enrolment_id, "enrolment"), body.otp)


@router.post("/enrolments/{enrolment_id}/dispute")
def dispute(enrolment_id: str, body: DisputeIn, p: Principal = Depends(secured("student:confirm")),
            x_act_as_student: str | None = Header(default=None)):
    return verification.dispute(p, x_act_as_student, ensure_uuid(enrolment_id, "enrolment"), body.note)


@router.post("/enrolments/{enrolment_id}/resend")
def resend(enrolment_id: str, p: Principal = Depends(secured("student:confirm")), x_act_as_student: str | None = Header(default=None)):
    return verification.resend(p, x_act_as_student, ensure_uuid(enrolment_id, "enrolment"), p.locale)


@router.get("/guardian-links")
def guardian_links(p: Principal = Depends(secured("student:wallet"))):
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT g.student_id, g.relationship, g.status, s.student_ref FROM consent.guardian_links g JOIN core.students s ON s.id=g.student_id "
                          "WHERE g.guardian_user_id=CAST(:u AS uuid)", u=p.id)
    return {"items": [{**d, "student_id": str(d["student_id"])} for d in data]}


@router.get("/enrolments/{enrolment_id}/proof")
def my_inclusion_proof(enrolment_id: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    """Merkle inclusion proof for my enrolment in a countersigned cohort commitment (REQ-STU-08). Verifiable in the browser."""
    from . import submissions as sub
    sid, _ = verification.resolve_student(p, x_act_as_student)
    e = verification._own_enrolment(sid, ensure_uuid(enrolment_id, "enrolment"))
    with tx(DbContext(institution_id=str(e["institution_id"]), scope="INSTITUTION", student_id=sid)) as conn:
        proof = sub.inclusion_proof(conn, str(e["id"]))
        pub = {r["kid"]: r["public_jwk"] for r in rows(conn, "SELECT kid, public_jwk FROM cred.signing_keys")}
        c = rows(conn, "SELECT institution_kid, aicte_kid FROM audit.cohort_commitments WHERE merkle_root=:r LIMIT 1", r=proof["root"])
    return {**proof, "signing_keys": {k: pub.get(k) for k in (c[0]["institution_kid"], c[0]["aicte_kid"])} if c else {},
            "note": "The root is signed by your institution and countersigned by AICTE. The proof shows your enrolment was part of the cohort at the cut-off."}
