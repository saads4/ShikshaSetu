"""Session, identity and token endpoints (PRD 8.3)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import audit
from .config import get_settings
from .core import ApiError, now, uuid7
from .deps import (Principal, hash_password, ip_hash, make_token, public, secured, verify_password)
from .db import SYSTEM, row, rows, tx

router = APIRouter(prefix="/api/v1", tags=["auth"])

MAX_FAILS, LOCK_MINUTES = 5, 5

# In-process token bucket for login/public endpoints (DEC-28; Redis limiter deferred behind this seam).
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, per_seconds: int) -> None:
    q, t = _hits[key], time.monotonic()
    while q and t - q[0] > per_seconds:
        q.popleft()
    if len(q) >= limit:
        raise ApiError(429, "RATE_LIMITED", "Too many requests", "Slow down and retry shortly.", retryable=True,
                       headers={"Retry-After": str(per_seconds)})
    q.append(t)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class StepUpIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ClientTokenIn(BaseModel):
    grant_type: str = "client_credentials"
    client_id: str
    client_secret: str
    scope: str = ""


def profile(user_id: str, session_id: str | None = None) -> dict:
    with tx(SYSTEM) as conn:
        u = row(conn, "SELECT u.id, u.username, u.display_name, u.utype, u.locale, u.institution_id, u.student_id, u.is_demo, "
                      "i.name AS institution_name, i.state AS institution_state, i.region AS institution_region, i.slug AS institution_slug "
                      "FROM iam.users u LEFT JOIN ref.institutions i ON i.id=u.institution_id WHERE u.id=CAST(:u AS uuid)", u=user_id)
        roles = rows(conn, "SELECT role_code, scope_type, scope_value FROM iam.user_roles WHERE user_id=CAST(:u AS uuid)", u=user_id)
    from .permissions import PERMISSIONS
    perms = sorted({p for r in roles for p in PERMISSIONS.get(r["role_code"], set())})
    return {"id": str(u["id"]), "username": u["username"], "display_name": u["display_name"], "utype": u["utype"],
            "locale": u["locale"], "roles": [r["role_code"] for r in roles],
            "scope": {"type": roles[0]["scope_type"], "value": roles[0]["scope_value"]} if roles else None,
            "institution": {"id": str(u["institution_id"]), "name": u["institution_name"], "state": u["institution_state"],
                            "region": u["institution_region"], "slug": u["institution_slug"]} if u["institution_id"] else None,
            "student_id": str(u["student_id"]) if u["student_id"] else None, "permissions": perms,
            "session_id": session_id, "demo": u["is_demo"]}


@router.post("/auth/login", dependencies=[Depends(public())])
def login(body: LoginIn, request: Request):
    s = get_settings()
    rid = getattr(request.state, "request_id", None)
    rate_limit(f"login:{ip_hash(request)}", 30, 60)
    err: ApiError | None = None  # raised only after the transaction commits, so counters/audit persist
    with tx(SYSTEM) as conn:
        u = row(conn, "SELECT id, password_hash, status, failed_attempts, locked_until, institution_id FROM iam.users "
                      "WHERE lower(username)=lower(:n)", n=body.username)
        t = now()
        if u and u["locked_until"] and u["locked_until"] > t:
            audit.log(conn, "auth.login_locked", entity_type="user", entity_id=u["id"], request_id=rid)
            err = ApiError(423, "ACCOUNT_LOCKED", "Account locked", "Too many failed attempts.", retryable=True,
                           extra={"retry_after": int((u["locked_until"] - t).total_seconds())})
        elif not u or u["status"] != "ACTIVE" or not verify_password(u["password_hash"], body.password):
            if u:
                fails = u["failed_attempts"] + 1
                lock = t + timedelta(minutes=LOCK_MINUTES) if fails >= MAX_FAILS else None
                conn.execute(text("UPDATE iam.users SET failed_attempts=:f, locked_until=:l WHERE id=:id"),
                             {"f": 0 if lock else fails, "l": lock, "id": u["id"]})
            audit.log(conn, "auth.login_failed", entity_type="user", entity_id=u["id"] if u else None,
                      institution_id=u["institution_id"] if u else None, request_id=rid, ip_hash=ip_hash(request))
            err = ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials", "Check your username and password.")
    if err:
        raise err
    with tx(SYSTEM) as conn:
        t = now()
        sid = uuid7()
        conn.execute(text("UPDATE iam.users SET failed_attempts=0, locked_until=NULL, last_login_at=:t WHERE id=:id"),
                     {"t": t, "id": u["id"]})
        conn.execute(text("INSERT INTO iam.sessions (id, user_id, issued_at, last_seen_at, expires_at, user_agent, ip_hash) "
                          "VALUES (:id,:u,:t,:t,:e,:ua,:ip)"),
                     {"id": sid, "u": u["id"], "t": t, "e": t + timedelta(minutes=s.jwt_ttl_minutes),
                      "ua": (request.headers.get("user-agent") or "")[:200], "ip": ip_hash(request)})
        audit.log(conn, "auth.login", entity_type="session", entity_id=sid, actor_id=u["id"], institution_id=u["institution_id"],
                  request_id=rid, ip_hash=ip_hash(request))
    token = make_token({"sub": str(u["id"]), "sid": str(sid), "utype": "USER"}, s.jwt_ttl_minutes)
    prof = profile(str(u["id"]), str(sid))
    out = {"access_token": token, "token_type": "bearer", "expires_in": s.jwt_ttl_minutes * 60, "user": prof}
    if prof["utype"] == "VERIFIER":      # the verifier portal talks to the separate verify-api, which accepts only this audience
        out["verify_token"] = make_token({"sub": str(u["id"]), "sid": str(sid), "utype": "USER"}, s.jwt_ttl_minutes, aud="verify-api")
    return out


@router.post("/auth/logout")
def logout(p: Principal = Depends(secured("auth:self"))):
    with tx(p.db_ctx()) as conn:
        conn.execute(text("UPDATE iam.sessions SET revoked_at=:t WHERE id=CAST(:s AS uuid)"), {"t": now(), "s": p.session_id})
        audit.log(conn, "auth.logout", entity_type="session", entity_id=p.session_id, actor_id=p.id, actor_role=p.role,
                  institution_id=p.institution_id, request_id=p.request_id)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(secured("auth:self"))):
    return profile(p.id, p.session_id)


@router.get("/me/sessions")
def my_sessions(p: Principal = Depends(secured("auth:self"))):
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT id, issued_at, last_seen_at, expires_at, revoked_at, user_agent FROM iam.sessions "
                          "WHERE user_id=CAST(:u AS uuid) ORDER BY issued_at DESC LIMIT 20", u=p.id)
    return {"items": [{**d, "id": str(d["id"]), "current": str(d["id"]) == p.session_id} for d in data]}


@router.delete("/me/sessions/{session_id}")
def revoke_session(session_id: str, p: Principal = Depends(secured("auth:self"))):
    with tx(SYSTEM) as conn:
        n = conn.execute(text("UPDATE iam.sessions SET revoked_at=:t WHERE id=CAST(:s AS uuid) AND user_id=CAST(:u AS uuid) "
                              "AND revoked_at IS NULL"), {"t": now(), "s": session_id, "u": p.id}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such session.")
        audit.log(conn, "auth.session_revoked", entity_type="session", entity_id=session_id, actor_id=p.id, actor_role=p.role,
                  request_id=p.request_id)
    return {"ok": True}


@router.post("/auth/step-up")
def step_up(body: StepUpIn, p: Principal = Depends(secured("auth:self"))):
    """REQ-AUTH-05 (SIMULATED MFA step-up): re-entering the password yields a 5-minute signing token."""
    with tx(SYSTEM) as conn:
        u = row(conn, "SELECT password_hash FROM iam.users WHERE id=CAST(:u AS uuid)", u=p.id)
        ok = bool(u) and verify_password(u["password_hash"], body.password)
        audit.log(conn, "auth.step_up" if ok else "auth.step_up_failed", entity_type="session", entity_id=p.session_id,
                  actor_id=p.id, actor_role=p.role, request_id=p.request_id)
    if not ok:
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials", "Password incorrect.")
    import jwt as pyjwt
    s = get_settings()
    t = now()
    token = pyjwt.encode({"iss": "shikshasetu", "aud": "step-up", "sub": p.id, "sid": p.session_id, "iat": int(t.timestamp()),
                          "exp": int((t + timedelta(minutes=5)).timestamp())}, s.jwt_secret, algorithm="HS256")
    return {"step_up_token": token, "expires_in": 300}


@router.post("/auth/token", dependencies=[Depends(public())])
def client_token(body: ClientTokenIn, request: Request):
    rate_limit(f"token:{ip_hash(request)}", 30, 60)
    if body.grant_type != "client_credentials":
        raise ApiError(400, "UNSUPPORTED_GRANT", "Unsupported grant type", "Use client_credentials.")
    with tx(SYSTEM) as conn:
        c = row(conn, "SELECT id, secret_hash, scopes, status, institution_id FROM iam.api_clients WHERE client_id=:c", c=body.client_id)
        bad = not c or c["status"] != "ACTIVE" or not verify_password(c["secret_hash"], body.client_secret)
        if bad:
            audit.log(conn, "auth.client_token_failed", entity_type="client", entity_id=body.client_id,
                      request_id=getattr(request.state, "request_id", None))
        else:
            wanted = set(body.scope.split()) or set(c["scopes"])
            scopes = sorted(wanted & set(c["scopes"]))
            conn.execute(text("UPDATE iam.api_clients SET last_used_at=:t WHERE id=:id"), {"t": now(), "id": c["id"]})
    if bad:
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials", "Client credentials rejected.")
    token = make_token({"sub": str(c["id"]), "utype": "CLIENT", "scopes": scopes}, 15)
    return {"access_token": token, "token_type": "bearer", "expires_in": 900, "scope": " ".join(scopes)}


@router.get("/auth/demo-accounts", dependencies=[Depends(public())])
def demo_accounts():
    if not get_settings().demo_mode:
        raise ApiError(404, "DEMO_ONLY", "Not found", "Demo accounts are not available.")
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT u.username, u.display_name, u.description, u.utype, r.role_code, r.scope_type, r.scope_value, "
                          "i.name AS institution, i.slug AS institution_slug, i.state, "
                          "(u.username ~ '^(ia|io|ar|as|st|gd|vu)\\.' AND u.description IS NOT NULL) AS featured "
                          "FROM iam.users u JOIN iam.user_roles r ON r.user_id=u.id LEFT JOIN ref.institutions i ON i.id=u.institution_id "
                          "WHERE u.is_demo AND u.status='ACTIVE' ORDER BY (u.description IS NULL), r.role_code, u.username")
    return {"password_hint": get_settings().seed_demo_password, "notice": "Demo credentials are for local demonstration only.",
            "items": data}
