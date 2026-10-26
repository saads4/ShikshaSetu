"""Credentials, verifier orgs, student wallet, corrections/DSR, rule sets and simulator, onboarding, admin, webhooks, feeds."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import admin, audit, credentials, rulesets, verification, wallet
from .cases import in_scope
from .core import ApiError, now
from .db import SYSTEM, DbContext, row, rows, scalar, tx
from .deps import Principal, ensure_uuid, ip_hash, public, secured
from .routes_auth import rate_limit
from .routes_claims import _require_institution

router = APIRouter(prefix="/api/v1", tags=["final"])


def _student(p: Principal, act_as: str | None) -> str:
    return verification.resolve_student(p, act_as)[0]


# =============================================================== credentials (institution / AICTE)
class IssueIn(BaseModel):
    enrolment_id: str


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/credentials")
def issue(body: IssueIn, p: Principal = Depends(secured("credentials:issue"))):
    _require_institution(p)
    with tx(p.db_ctx()) as conn:
        return credentials.issue(conn, ensure_uuid(body.enrolment_id, "enrolment"), p.id, p.role, p.request_id)


@router.post("/credentials/issue-all-verified")
def issue_all(p: Principal = Depends(secured("credentials:issue"))):
    _require_institution(p)
    return credentials.issue_all_verified(p)


@router.get("/credentials")
def list_credentials(p: Principal = Depends(secured("enrolments:read")), status: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    inst = _require_institution(p)
    with tx(p.db_ctx()) as conn:
        d = rows(conn, "SELECT c.id, c.credential_ref, c.status, c.issued_at, c.valid_until, c.revoked_reason, e.enrolment_ref, e.institution_student_id, pr.name AS programme_name FROM cred.credentials c "
                       "JOIN core.enrolments e ON e.id=c.enrolment_id JOIN ref.programmes pr ON pr.id=e.programme_id WHERE c.institution_id=CAST(:i AS uuid)" + (" AND c.status=:s" if status else "") +
                       " ORDER BY c.issued_at DESC LIMIT :n OFFSET :o", i=inst, n=limit, o=offset, **({"s": status} if status else {}))
        total = scalar(conn, "SELECT count(*) FROM cred.credentials WHERE institution_id=CAST(:i AS uuid)", i=inst)
        eligible = scalar(conn, "SELECT count(*) FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.institution_id=CAST(:i AS uuid) AND e.status='ADMITTED' AND v.overall_state='VERIFIED' "
                                "AND NOT EXISTS (SELECT 1 FROM cred.credentials c WHERE c.enrolment_id=e.id AND c.status='VALID')", i=inst)
    return {"items": [{**x, "id": str(x["id"])} for x in d], "total": total, "eligible_to_issue": eligible}


@router.get("/credentials/{cid}/certificate.pdf")
def institution_certificate(cid: str, p: Principal = Depends(secured("enrolments:read"))):
    cid = ensure_uuid(cid, "credential")
    inst = _require_institution(p)
    with tx(p.db_ctx()) as conn:
        if not row(conn, "SELECT 1 AS x FROM cred.credentials WHERE id=CAST(:c AS uuid) AND institution_id=CAST(:i AS uuid)", c=cid, i=inst):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such credential.")
        audit.log(conn, "credential.certificate_viewed", entity_type="credential", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=inst, request_id=p.request_id)
    with tx(SYSTEM) as conn:
        pdf, name = credentials.certificate_pdf(conn, cid)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={name}"})


@router.post("/credentials/{cid}/supersede")
def supersede(cid: str, body: ReasonIn, p: Principal = Depends(secured("credentials:revoke"))):
    cid = ensure_uuid(cid, "credential")
    inst = _require_institution(p) if p.institution_id else None
    with tx(p.db_ctx()) as conn:
        if inst and not row(conn, "SELECT 1 AS x FROM cred.credentials WHERE id=CAST(:c AS uuid) AND institution_id=CAST(:i AS uuid)", c=cid, i=inst):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such credential.")
        return credentials.revoke(conn, cid, body.reason, actor_id=p.id, actor_role=p.role, request_id=p.request_id, status="SUPERSEDED")


@router.get("/aicte/credentials/{ref}")
def aicte_lookup(ref: str, p: Principal = Depends(secured("credentials:revoke"))):
    """Lookup by credential reference. Returns the credential's own (non-personal) facts only."""
    if p.institution_id:
        raise ApiError(403, "FORBIDDEN", "Not permitted", "AICTE lookup is for reviewers.")
    with tx(SYSTEM) as conn:
        c = row(conn, "SELECT c.id, c.credential_ref, c.status, c.issued_at, c.valid_until, c.revoked_reason, c.issuer_kid, e.enrolment_ref, e.academic_year, pr.name AS programme_name, i.name AS institution_name, i.state, i.region "
                      "FROM cred.credentials c JOIN core.enrolments e ON e.id=c.enrolment_id JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=c.institution_id WHERE c.credential_ref=:r", r=ref)
    if not c or not in_scope(p, c):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such credential in your scope.")
    return {**c, "id": str(c["id"])}


@router.post("/aicte/credentials/{cid}/revoke")
def aicte_revoke(cid: str, body: ReasonIn, p: Principal = Depends(secured("credentials:revoke"))):
    cid = ensure_uuid(cid, "credential")
    if p.institution_id:
        raise ApiError(403, "FORBIDDEN", "Not permitted", "Only AICTE reviewers revoke credentials.")
    with tx(SYSTEM) as conn:
        c = row(conn, "SELECT i.state, i.region FROM cred.credentials c JOIN ref.institutions i ON i.id=c.institution_id WHERE c.id=CAST(:c AS uuid)", c=cid)
    if not c or not in_scope(p, c):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such credential in your scope.")
    with tx(p.db_ctx()) as conn:
        return credentials.revoke(conn, cid, body.reason, actor_id=p.id, actor_role=p.role, request_id=p.request_id)


# =============================================================== verifier orgs
class OrgIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    tier: str = Field(pattern="^(REGULATOR|INSTITUTION|UNIVERSITY|EMPLOYER|BANK|EMBASSY)$")
    contact_email: str | None = None


class ScopeIn(BaseModel):
    purpose_code: str
    allowed_fields: list[str]
    allowed_questions: list[str]
    hourly_limit: int = Field(default=60, ge=1, le=10000)


@router.get("/verifier-orgs")
def list_orgs(p: Principal = Depends(secured("verifier_orgs:read"))):
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT o.id, o.name, o.tier, o.status, o.contact_email, o.created_at, "
                       "(SELECT count(*) FROM cred.verification_requests r WHERE r.verifier_org_id=o.id AND r.created_at > :t7) AS requests_7d, "
                       "(SELECT count(*) FROM cred.verification_requests r WHERE r.verifier_org_id=o.id AND r.created_at > :t1) AS requests_1h, "
                       "(SELECT jsonb_agg(jsonb_build_object('purpose', s.purpose_code, 'fields', s.allowed_fields, 'questions', s.allowed_questions, 'hourly_limit', s.hourly_limit)) FROM iam.verifier_scopes s WHERE s.verifier_org_id=o.id) AS scopes "
                       "FROM iam.verifier_orgs o ORDER BY o.name", t7=now().replace(microsecond=0) - __import__("datetime").timedelta(days=7), t1=now() - __import__("datetime").timedelta(hours=1))
    return {"items": [{**x, "id": str(x["id"])} for x in d]}


@router.post("/verifier-orgs")
def create_org(body: OrgIn, p: Principal = Depends(secured("verifier_orgs:manage"))):
    from .core import uuid7
    with tx(SYSTEM) as conn:
        oid = uuid7()
        conn.execute(text("INSERT INTO iam.verifier_orgs (id, name, tier, contact_email) VALUES (:id,:n,:t,:c)"), {"id": oid, "n": body.name, "t": body.tier, "c": body.contact_email})
        audit.log(conn, "verifier_org.created", entity_type="verifier_org", entity_id=oid, actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    return {"id": str(oid)}


@router.put("/verifier-orgs/{oid}/scopes")
def set_scope(oid: str, body: ScopeIn, p: Principal = Depends(secured("verifier_orgs:manage"))):
    from .core import uuid7
    from .verifier import PURPOSES, QUESTIONS
    if body.purpose_code not in PURPOSES or any(q not in QUESTIONS for q in body.allowed_questions) or any(f not in credentials.DISCLOSABLE for f in body.allowed_fields):
        raise ApiError(422, "VALIDATION_ERROR", "Invalid scope", "Unknown purpose, question or field.")
    with tx(SYSTEM) as conn:
        conn.execute(text("INSERT INTO iam.verifier_scopes (id, verifier_org_id, purpose_code, allowed_fields, allowed_questions, hourly_limit) VALUES (:id,CAST(:o AS uuid),:p,:f,:q,:h) "
                          "ON CONFLICT (verifier_org_id, purpose_code) DO UPDATE SET allowed_fields=:f, allowed_questions=:q, hourly_limit=:h"), {"id": uuid7(), "o": ensure_uuid(oid, "organisation"), "p": body.purpose_code, "f": body.allowed_fields, "q": body.allowed_questions, "h": body.hourly_limit})
        audit.log(conn, "verifier_org.scope_set", entity_type="verifier_org", entity_id=oid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"purpose": body.purpose_code})
    return {"ok": True}


@router.post("/verifier-orgs/{oid}/status")
def set_org_status(oid: str, body: ReasonIn, status: str = Query(pattern="^(ACTIVE|SUSPENDED)$"), p: Principal = Depends(secured("verifier_orgs:manage"))):
    with tx(SYSTEM) as conn:
        n = conn.execute(text("UPDATE iam.verifier_orgs SET status=:s WHERE id=CAST(:o AS uuid)"), {"s": status, "o": ensure_uuid(oid, "organisation")}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such organisation.")
        audit.log(conn, "verifier_org.status", entity_type="verifier_org", entity_id=oid, actor_id=p.id, actor_role=p.role, justification=body.reason, request_id=p.request_id, payload={"status": status})
    return {"status": status}


# =============================================================== student wallet
class ShareIn(BaseModel):
    credential_id: str
    purpose_code: str
    fields: list[str]
    expires_days: int = Field(default=7, ge=1, le=30)


class ConsentIn(BaseModel):
    verifier_org_id: str
    purpose_code: str
    fields: list[str]
    expires_days: int | None = Field(default=None, ge=1, le=365)


class ApaarConsentIn(BaseModel):
    granted: bool


class CorrectionIn(BaseModel):
    enrolment_id: str
    field: str
    value: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class DsrIn(BaseModel):
    type: str
    details: str | None = Field(default=None, max_length=1000)


class GuardianIn(BaseModel):
    student_ref: str = Field(min_length=4, max_length=20)
    relationship: str = Field(default="PARENT", max_length=30)


class GuardianVerifyIn(BaseModel):
    student_ref: str
    otp: str = Field(pattern=r"^\d{6}$")


class LocaleIn(BaseModel):
    locale: str = Field(pattern="^(en|hi)$")


@router.get("/student/credentials")
def my_credentials(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return {"items": wallet.my_credentials(_student(p, x_act_as_student)), "boundary": credentials.BOUNDARY}


@router.get("/student/credentials/{cid}/certificate.pdf")
def my_certificate(cid: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    pdf, name = wallet.credential_pdf(_student(p, x_act_as_student), ensure_uuid(cid, "credential"))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={name}"})


@router.get("/student/share-links")
def my_links(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return {"items": wallet.list_share_links(_student(p, x_act_as_student)), "fields": credentials.DISCLOSABLE, "catalogue_size": len(credentials.FIELD_CATALOGUE)}


@router.post("/student/share-links")
def create_link(body: ShareIn, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.create_share_link(p, _student(p, x_act_as_student), ensure_uuid(body.credential_id, "credential"), body.purpose_code, body.fields, body.expires_days)


@router.delete("/student/share-links/{lid}")
def revoke_link(lid: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.revoke_share_link(p, _student(p, x_act_as_student), ensure_uuid(lid, "link"))


@router.get("/student/verifier-orgs")
def student_orgs(p: Principal = Depends(secured("student:wallet"))):
    return {"items": wallet.verifier_orgs(), "fields": credentials.DISCLOSABLE}


@router.get("/student/consents")
def my_consents(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.my_consents(_student(p, x_act_as_student))


@router.post("/student/consents")
def grant(body: ConsentIn, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.grant_consent(p, _student(p, x_act_as_student), ensure_uuid(body.verifier_org_id, "organisation"), body.purpose_code, body.fields, body.expires_days)


@router.delete("/student/consents/{cid}")
def withdraw(cid: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.withdraw_consent(p, _student(p, x_act_as_student), ensure_uuid(cid, "consent"))


@router.post("/student/consent-requests/{rid}/refuse")
def refuse(rid: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.refuse_request(p, _student(p, x_act_as_student), ensure_uuid(rid, "request"))


@router.post("/student/consents/apaar")
def apaar_consent(body: ApaarConsentIn, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.set_apaar_consent(p, _student(p, x_act_as_student), body.granted)


@router.get("/student/access-log")
def my_access_log(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.access_log(_student(p, x_act_as_student))


@router.get("/student/corrections")
def my_corrections(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return {"items": wallet.my_corrections(_student(p, x_act_as_student)), "note": "You cannot edit official fields directly. A reviewer decides each request."}


@router.post("/student/corrections")
def request_correction(body: CorrectionIn, p: Principal = Depends(secured("corrections:request")), x_act_as_student: str | None = Header(default=None)):
    return wallet.request_correction(p, _student(p, x_act_as_student), ensure_uuid(body.enrolment_id, "enrolment"), body.field, body.value, body.note)


@router.get("/student/dsr")
def my_dsrs(p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return {"items": wallet.my_dsrs(_student(p, x_act_as_student)), "grievance_officer": "Grievance officer (placeholder role holder in this prototype): grievance@aicte.example.test"}


@router.post("/student/dsr")
def create_dsr(body: DsrIn, p: Principal = Depends(secured("dsr:request")), x_act_as_student: str | None = Header(default=None)):
    return wallet.create_dsr(p, _student(p, x_act_as_student), body.type, body.details)


@router.get("/student/dsr/{did}/export")
def dsr_export(did: str, p: Principal = Depends(secured("student:wallet")), x_act_as_student: str | None = Header(default=None)):
    return wallet.dsr_export(_student(p, x_act_as_student), ensure_uuid(did, "request"))


@router.post("/student/guardian-links")
def guardian_request(body: GuardianIn, p: Principal = Depends(secured("student:wallet"))):
    rate_limit(f"guardian:{p.id}", 10, 3600)
    return wallet.request_guardian_link(p, body.student_ref, body.relationship)


@router.post("/student/guardian-links/verify")
def guardian_verify(body: GuardianVerifyIn, p: Principal = Depends(secured("student:wallet"))):
    return wallet.verify_guardian_link(p, body.student_ref, body.otp)


@router.patch("/me/locale")
def set_locale(body: LocaleIn, p: Principal = Depends(secured("auth:self"))):
    with tx(SYSTEM) as conn:
        conn.execute(text("UPDATE iam.users SET locale=:l WHERE id=CAST(:u AS uuid)"), {"l": body.locale, "u": p.id})
    return {"locale": body.locale}


# =============================================================== AICTE: corrections, DSR
class DecideIn(BaseModel):
    approve: bool
    reason: str = Field(min_length=5, max_length=500)


class DsrDecideIn(BaseModel):
    decision: str = Field(pattern="^(FULFIL|REJECT)$")
    note: str = Field(min_length=5, max_length=500)


@router.get("/aicte/corrections")
def aicte_corrections(p: Principal = Depends(secured("corrections:decide")), status: str | None = None):
    return {"items": wallet.list_corrections(p, status), "privacy": "Proposed values stay encrypted; reviewers see the field and the student's note, not the values."}


@router.post("/aicte/corrections/{cid}/decide")
def aicte_decide_correction(cid: str, body: DecideIn, p: Principal = Depends(secured("corrections:decide"))):
    return wallet.decide_correction(p, ensure_uuid(cid, "request"), body.approve, body.reason)


@router.get("/aicte/dsr")
def aicte_dsrs(p: Principal = Depends(secured("dsr:decide")), status: str | None = None):
    return {"items": wallet.list_dsrs(p, status)}


@router.post("/aicte/dsr/{did}/decide")
def aicte_decide_dsr(did: str, body: DsrDecideIn, p: Principal = Depends(secured("dsr:decide"))):
    return wallet.decide_dsr(p, ensure_uuid(did, "request"), body.decision, body.note)


# =============================================================== rule sets and simulator
class DraftIn(BaseModel):
    notes: str | None = Field(default=None, max_length=300)
    retroactive: bool = False


class RuleUpdateIn(BaseModel):
    params: dict | None = None
    severity: str | None = None
    message: str | None = Field(default=None, max_length=300)
    sites: list[str] | None = None


class SimIn(BaseModel):
    cycles: list[str] = Field(min_length=1, max_length=3)


@router.get("/rulesets")
def list_rulesets(p: Principal = Depends(secured("rulesets:read"))):
    with tx(SYSTEM) as conn:
        items = rulesets.list_sets(conn)
    if p.institution_id:
        items = [i for i in items if i["status"] == "ACTIVE"]
    return {"items": items}


@router.get("/rulesets/{sid}")
def get_ruleset(sid: str, p: Principal = Depends(secured("rulesets:read"))):
    with tx(SYSTEM) as conn:
        s = rulesets.get_set(conn, ensure_uuid(sid, "rule set"))
    if p.institution_id and s["status"] != "ACTIVE":
        raise ApiError(404, "NOT_FOUND", "Not found", "No such rule set.")
    return s


@router.post("/rulesets/draft")
def draft(body: DraftIn, p: Principal = Depends(secured("rulesets:draft"))):
    return rulesets.create_draft(p, body.notes, body.retroactive)


@router.put("/rulesets/{sid}/rules/{code}")
def update_rule(sid: str, code: str, body: RuleUpdateIn, p: Principal = Depends(secured("rulesets:draft"))):
    return rulesets.update_rule(p, ensure_uuid(sid, "rule set"), code, body.params, body.severity, body.message, body.sites)


@router.post("/rulesets/{sid}/publish")
def publish(sid: str, p: Principal = Depends(secured("rulesets:publish"))):
    return rulesets.publish(p, ensure_uuid(sid, "rule set"))


@router.post("/rulesets/{sid}/rollback")
def rollback(sid: str, p: Principal = Depends(secured("rulesets:publish"))):
    return rulesets.rollback(p, ensure_uuid(sid, "rule set"))


@router.post("/rulesets/{sid}/simulate")
def simulate(sid: str, body: SimIn, p: Principal = Depends(secured("simulations:run"))):
    return rulesets.request_simulation(p, ensure_uuid(sid, "rule set"), body.cycles)


@router.get("/simulations")
def simulations(p: Principal = Depends(secured("simulations:run")), draft_id: str | None = None):
    return {"items": rulesets.list_simulations(draft_id)}


@router.get("/simulations/{sid}")
def simulation(sid: str, p: Principal = Depends(secured("simulations:run"))):
    return rulesets.get_simulation(ensure_uuid(sid, "simulation"))


# =============================================================== onboarding
class OnboardIn(BaseModel):
    aicte_id: str = Field(min_length=4, max_length=20)
    officer_name: str = Field(min_length=3, max_length=120)
    designation: str = Field(min_length=2, max_length=80)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    phone: str | None = Field(default=None, max_length=20)


@router.post("/onboarding/institutions", dependencies=[Depends(public())])
def onboard(body: OnboardIn, request: Request):
    rate_limit(f"onboard:{ip_hash(request)}", 10, 3600)
    return admin.submit_onboarding(body.aicte_id, body.officer_name, body.designation, body.email, body.phone)


@router.get("/onboarding/status/{token}", dependencies=[Depends(public())])
def onboard_status(token: str, request: Request):
    rate_limit(f"onboard-s:{ip_hash(request)}", 60, 60)
    return admin.onboarding_status(token[:64])


@router.get("/aicte/onboarding")
def onboarding_queue(p: Principal = Depends(secured("onboarding:decide"))):
    return {"items": admin.list_onboarding(p), "label": "The nodal-officer identity check is SIMULATED"}


@router.post("/aicte/onboarding/{oid}/decide")
def onboarding_decide(oid: str, body: DecideIn, p: Principal = Depends(secured("onboarding:decide"))):
    return admin.decide_onboarding(p, ensure_uuid(oid, "application"), body.approve, body.reason)


# =============================================================== administration
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=2, max_length=120)
    role: str = Field(pattern="^(IO|IA|AR|AS)$")
    scope_type: str | None = None
    scope_value: str | None = None


class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    scopes: list[str]


class WebhookIn(BaseModel):
    url: str = Field(max_length=500)
    events: list[str]


@router.get("/admin/users")
def users(p: Principal = Depends(secured("users:manage"))):
    return {"items": admin.list_users(p)}


@router.post("/admin/users")
def new_user(body: UserIn, p: Principal = Depends(secured("users:manage"))):
    return admin.create_user(p, body.username, body.display_name, body.role, body.scope_type, body.scope_value)


@router.post("/admin/users/{uid}/deactivate")
def deactivate(uid: str, p: Principal = Depends(secured("users:manage"))):
    return admin.deactivate_user(p, ensure_uuid(uid, "user"))


@router.get("/admin/clients")
def clients(p: Principal = Depends(secured("clients:manage"))):
    return {"items": admin.list_clients(p), "scopes": sorted(s for s in admin.CLIENT_SCOPES if (s == "feeds:write") == (not p.institution_id))}


@router.post("/admin/clients")
def new_client(body: ClientIn, p: Principal = Depends(secured("clients:manage"))):
    return admin.create_client(p, body.name, body.scopes)


@router.post("/admin/clients/{cid}/rotate")
def rotate(cid: str, p: Principal = Depends(secured("clients:manage"))):
    return admin.rotate_client(p, ensure_uuid(cid, "client"))


@router.post("/admin/clients/{cid}/disable")
def disable(cid: str, p: Principal = Depends(secured("clients:manage"))):
    return admin.disable_client(p, ensure_uuid(cid, "client"))


@router.get("/admin/webhooks")
def webhooks(p: Principal = Depends(secured("clients:manage"))):
    return {"items": admin.list_webhooks(p), "events": admin.EVENTS}


@router.post("/admin/webhooks")
def new_webhook(body: WebhookIn, p: Principal = Depends(secured("clients:manage"))):
    return admin.create_webhook(p, body.url, body.events)


@router.post("/admin/webhooks/{wid}/status")
def webhook_status(wid: str, status: str = Query(pattern="^(ACTIVE|PAUSED|DISABLED)$"), p: Principal = Depends(secured("clients:manage"))):
    return admin.set_webhook_status(p, ensure_uuid(wid, "webhook"), status)


@router.get("/admin/webhook-deliveries")
def webhook_deliveries(p: Principal = Depends(secured("clients:manage")), webhook_id: str | None = None, status: str | None = None):
    return {"items": admin.deliveries(p, webhook_id, status)}


@router.post("/admin/webhook-deliveries/{did}/replay")
def webhook_replay(did: str, p: Principal = Depends(secured("clients:manage"))):
    return admin.replay(p, ensure_uuid(did, "delivery"))


@router.post("/demo/webhook-sink/{endpoint_id}", dependencies=[Depends(public())])
async def webhook_sink(endpoint_id: str, request: Request):
    """Local demo sink: shows that a delivery arrived and whether its signature verified. Present only in DEMO_MODE."""
    from .config import get_settings
    if not get_settings().demo_mode:
        raise ApiError(404, "DEMO_ONLY", "Not found", "Not available.")
    body = (await request.body()).decode("utf-8", "replace")
    return admin.sink_record(ensure_uuid(endpoint_id, "endpoint"), {k.lower(): v for k, v in request.headers.items()}, body)


@router.get("/demo/webhook-sink")
def sink_list(p: Principal = Depends(secured("demo:tools"))):
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT id, endpoint_id, headers, body, signature_valid, received_at FROM ops.webhook_sink ORDER BY id DESC LIMIT 50")
    return {"items": [{**x, "endpoint_id": str(x["endpoint_id"]) if x["endpoint_id"] else None} for x in d]}


# =============================================================== liveness feed [SIMULATED]
class FeedItem(BaseModel):
    apaar_id: str
    has_abc_credit: bool = False
    has_exam_registration: bool = False
    last_activity_at: str | None = None


class FeedIn(BaseModel):
    items: list[FeedItem] = Field(max_length=5000)


@router.post("/feeds/liveness")
def feeds(body: FeedIn, p: Principal = Depends(secured("feeds:write"))):
    return admin.ingest_liveness([i.model_dump() for i in body.items])
