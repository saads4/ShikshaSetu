"""verify-api: a SEPARATE deployable (own process, port 8001) with a narrow surface: public status/keys, share-token verification
and purpose-bound verification for verifier users. No staff routes (REQ-VER-09)."""
from __future__ import annotations

import time
from datetime import timedelta

import jwt
from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import credentials, verifier
from app.config import get_settings
from app.core import ApiError, now, problem_body
from app.db import SYSTEM, row, rows, tx
from app.deps import ISS, ip_hash
from app.routes_auth import rate_limit

app = FastAPI(title="ShikshaSetu verify-api", docs_url="/docs" if get_settings().demo_mode else None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ApiError)
async def api_error(request: Request, exc: ApiError):
    return JSONResponse(problem_body(exc, request.headers.get("x-request-id", "")), status_code=exc.status, media_type="application/problem+json", headers=exc.headers)


@app.middleware("http")
async def headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


def verifier_org(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """Verifier users only: token audience must be verify-api; the session must be live."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required", "Sign in as a verifier user.")
    try:
        c = jwt.decode(authorization.split(" ", 1)[1], get_settings().jwt_secret, algorithms=["HS256"], audience="verify-api", issuer=ISS)
    except jwt.PyJWTError:
        raise ApiError(401, "INVALID_TOKEN", "Invalid token", "The access token could not be verified.")
    with tx(SYSTEM) as conn:
        s = row(conn, "SELECT s.last_seen_at, s.revoked_at, s.expires_at, u.id AS uid, u.status, u.utype, u.verifier_org_id, o.name AS org_name FROM iam.sessions s JOIN iam.users u ON u.id=s.user_id "
                      "LEFT JOIN iam.verifier_orgs o ON o.id=u.verifier_org_id WHERE s.id=CAST(:s AS uuid) AND u.id=CAST(:u AS uuid)", s=c.get("sid"), u=c.get("sub"))
        t = now()
        if not s or s["revoked_at"] or s["expires_at"] < t or t - s["last_seen_at"] > timedelta(minutes=get_settings().session_idle_minutes) or s["status"] != "ACTIVE":
            raise ApiError(401, "SESSION_EXPIRED", "Session expired", "Sign in again.")
        if s["utype"] != "VERIFIER" or not s["verifier_org_id"]:
            raise ApiError(403, "FORBIDDEN", "Not permitted", "Only verifier users can use this endpoint.")
        conn.execute(text("UPDATE iam.sessions SET last_seen_at=:t WHERE id=CAST(:s AS uuid)"), {"t": t, "s": c["sid"]})
    return {"id": str(s["verifier_org_id"]), "user_id": str(s["uid"]), "name": s["org_name"]}


class VerifyIn(BaseModel):
    credential_ref: str | None = Field(default=None, max_length=64)
    share_token: str | None = Field(default=None, max_length=128)
    purpose: str
    question: dict
    nonce: str | None = Field(default=None, max_length=64)


class PublicVerifyIn(BaseModel):
    token: str = Field(max_length=128)
    question: dict


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "verify-api"}


@app.get("/public/jwks")
def jwks():
    with tx(SYSTEM) as conn:
        return credentials.jwks(conn)


@app.get("/public/key-status")
def key_status():
    with tx(SYSTEM) as conn:
        return {"keys": credentials.jwks(conn)["status"]}


@app.get("/public/status-list/{list_id}")
def status_list(list_id: int):
    with tx(SYSTEM) as conn:
        return {"jwt": credentials.status_list_jwt(conn)}


@app.get("/v/{ref}")
def status_page(ref: str, request: Request):
    rate_limit(f"v:{ip_hash(request)}", 120, 60)
    with tx(SYSTEM) as conn:
        return credentials.public_status(conn, ref[:64])


@app.get("/qr/{ref}")
def qr(ref: str, request: Request):
    """QR code (SVG) that opens the public status page for a credential reference. The reference is unguessable and carries no personal data."""
    import io
    import os
    import segno
    from fastapi.responses import Response
    rate_limit(f"qr:{ip_hash(request)}", 120, 60)
    web = os.environ.get("PUBLIC_WEB_URL") or get_settings().cors_origins.split(",")[0].strip()
    buf = io.BytesIO()
    segno.make(f"{web}/v/{ref[:64]}", error="m").save(buf, kind="svg", scale=4, border=2, dark="#0f4d3a")
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.post("/public/verify")
def public_verify(body: PublicVerifyIn, request: Request):
    rate_limit(f"pv:{ip_hash(request)}", 30, 60)
    return verifier.public_verify(body.token, body.question, ip_hash(request))


@app.post("/verify")
def verify(body: VerifyIn, request: Request, org: dict = Depends(verifier_org)):
    if bool(body.credential_ref) == bool(body.share_token):
        raise ApiError(422, "VALIDATION_ERROR", "One identifier needed", "Send either credential_ref or share_token.",
                       errors=[{"field": "credential_ref", "code": "ONE_OF", "message": "Provide exactly one of credential_ref / share_token.", "fix_suggestion": "Scan the QR or paste the share token."}])
    return verifier.verify(org, ref=body.credential_ref, token=body.share_token, purpose=body.purpose, question=body.question, nonce=body.nonce, ip=ip_hash(request))


@app.get("/verify/usage")
def usage(org: dict = Depends(verifier_org)):
    with tx(SYSTEM) as conn:
        scopes = rows(conn, "SELECT purpose_code, allowed_fields, allowed_questions, hourly_limit FROM iam.verifier_scopes WHERE verifier_org_id=CAST(:o AS uuid) ORDER BY purpose_code", o=org["id"])
        used = row(conn, "SELECT count(*) AS n FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) AND created_at > :t", o=org["id"], t=now() - timedelta(hours=1))["n"]
        hist = rows(conn, "SELECT created_at, purpose_code, question, outcome, via, fields_disclosed FROM cred.verification_requests WHERE verifier_org_id=CAST(:o AS uuid) ORDER BY created_at DESC LIMIT 50", o=org["id"])
        st = row(conn, "SELECT status, tier FROM iam.verifier_orgs WHERE id=CAST(:o AS uuid)", o=org["id"])
    return {"organisation": org["name"], "status": st["status"], "tier": st["tier"], "used_this_hour": used, "scopes": scopes, "history": hist, "purposes": verifier.PURPOSES, "boundary": credentials.BOUNDARY}
