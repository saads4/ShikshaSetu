"""Authentication, principal resolution and permission enforcement (REQ-AUTH, REQ-ACL)."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, Header, Request
from sqlalchemy import text

from . import audit
from .config import get_settings
from .core import ApiError, now
from .db import SYSTEM, DbContext, row, rows, tx
from .permissions import PERMISSIONS, STEP_UP

ISS = "shikshasetu"
AUD = "core-api"
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)  # argon2id, modest cost for demo seeding


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    try:
        return _hasher.verify(hash_, pw)
    except VerificationError:
        return False
    except Exception:
        return False


def make_token(claims: dict, ttl_minutes: int, aud: str = AUD) -> str:
    s = get_settings()
    t = now()
    payload = {"iss": ISS, "aud": aud, "iat": int(t.timestamp()), "exp": int((t + timedelta(minutes=ttl_minutes)).timestamp()),
               **claims}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """HS256 only; 'none' and any other alg are rejected (REQ-AUTH-02)."""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "HS256":
            raise ApiError(401, "INVALID_TOKEN", "Invalid token", "Unsupported signing algorithm.")
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], audience=AUD, issuer=ISS)
    except ApiError:
        raise
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "SESSION_EXPIRED", "Session expired", "Sign in again.")
    except jwt.PyJWTError:
        raise ApiError(401, "INVALID_TOKEN", "Invalid token", "The access token could not be verified.")


def ip_hash(request: Request) -> str:
    ip = request.client.host if request.client else "?"
    return hashlib.sha256(f"ip:{ip}".encode()).hexdigest()[:16]


@dataclass
class Principal:
    kind: str                      # user | client
    id: str
    username: str
    display_name: str
    utype: str
    roles: list[str]
    scope_type: str
    scope_value: str | None
    institution_id: str | None
    student_id: str | None
    session_id: str | None
    locale: str = "en"
    perms: set[str] = field(default_factory=set)
    request_id: str = ""
    ip: str = ""
    step_up_ok: bool = False

    @property
    def role(self) -> str:
        return self.roles[0] if self.roles else ""

    def db_ctx(self) -> DbContext:
        scope = self.scope_type if self.utype == "AICTE" else ("INSTITUTION" if self.institution_id else "")
        return DbContext(institution_id=self.institution_id or "", scope=scope or "", actor_id=self.id,
                         student_id=self.student_id or "")

    def can(self, perm: str) -> bool:
        return perm in self.perms


def _load_principal(claims: dict, request: Request) -> Principal:
    s = get_settings()
    rid = getattr(request.state, "request_id", "")
    if claims.get("utype") == "CLIENT":
        with tx(SYSTEM) as conn:
            c = row(conn, "SELECT id, client_id, name, scopes, status, institution_id FROM iam.api_clients WHERE id=CAST(:i AS uuid)",
                    i=claims["sub"])
        if not c or c["status"] != "ACTIVE":
            raise ApiError(401, "INVALID_TOKEN", "Invalid token", "Client is not active.")
        perms = set(claims.get("scopes", [])) & set(c["scopes"])
        inst = str(c["institution_id"]) if c["institution_id"] else None
        return Principal("client", str(c["id"]), c["client_id"], c["name"], "CLIENT", ["CL"], "INSTITUTION" if inst else "NATIONAL",
                         inst, inst, None, None, "en", perms, rid, ip_hash(request))
    with tx(SYSTEM) as conn:
        sess = row(conn, "SELECT s.last_seen_at, s.revoked_at, s.expires_at, u.id AS uid, u.username, u.display_name, u.utype, "
                         "u.status, u.institution_id, u.student_id, u.locale FROM iam.sessions s "
                         "JOIN iam.users u ON u.id = s.user_id WHERE s.id = CAST(:sid AS uuid) AND u.id = CAST(:uid AS uuid)",
                   sid=claims.get("sid"), uid=claims.get("sub"))
        if not sess:
            raise ApiError(401, "SESSION_EXPIRED", "Session expired", "Sign in again.")
        t = now()
        idle = t - sess["last_seen_at"]
        if sess["revoked_at"] or sess["expires_at"] < t or idle > timedelta(minutes=s.session_idle_minutes) \
                or sess["status"] != "ACTIVE":
            raise ApiError(401, "SESSION_EXPIRED", "Session expired", "Your session has ended. Sign in again.")
        if idle > timedelta(seconds=60):
            conn.execute(text("UPDATE iam.sessions SET last_seen_at=:t WHERE id=CAST(:sid AS uuid)"), {"t": t, "sid": claims["sid"]})
        # Sensitive permissions are re-read from the database on every request (stale tokens cannot widen access).
        roles = rows(conn, "SELECT role_code, scope_type, scope_value FROM iam.user_roles WHERE user_id=CAST(:u AS uuid) "
                           "ORDER BY role_code", u=claims["sub"])
    if not roles:
        raise ApiError(403, "NO_ROLE", "No role assigned", "This account has no role.")
    codes = [r["role_code"] for r in roles]
    perms: set[str] = set()
    for c in codes:
        perms |= PERMISSIONS.get(c, set())
    return Principal("user", claims["sub"], sess["username"], sess["display_name"], sess["utype"], codes, roles[0]["scope_type"],
                     roles[0]["scope_value"], str(sess["institution_id"]) if sess["institution_id"] else None,
                     str(sess["student_id"]) if sess["student_id"] else None, claims["sid"], sess["locale"], perms, rid, ip_hash(request))


def _deny(principal: Principal | None, request: Request, perm: str, reason: str) -> ApiError:
    with tx(SYSTEM) as conn:
        audit.log(conn, "authz.denied", entity_type="route", entity_id=request.url.path, actor_id=principal.id if principal else None,
                  actor_role=principal.role if principal else None, institution_id=principal.institution_id if principal else None,
                  request_id=getattr(request.state, "request_id", None),
                  payload={"permission": perm, "method": request.method, "reason": reason})
    return ApiError(403, "FORBIDDEN", "Not permitted", f"Your role cannot perform this action ({perm}).")


def secured(permission: str, *, step_up: bool | None = None) -> Callable:
    """Dependency factory: every protected route declares exactly one permission (REQ-ACL-01)."""
    needs_step = (permission in STEP_UP) if step_up is None else step_up

    def dep(request: Request, authorization: str | None = Header(default=None),
            x_step_up_token: str | None = Header(default=None)) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "UNAUTHENTICATED", "Authentication required", "Provide a bearer token.")
        claims = decode_token(authorization.split(" ", 1)[1])
        p = _load_principal(claims, request)
        if permission not in p.perms:
            raise _deny(p, request, permission, "permission not granted to role")
        if needs_step:
            check_step_up(p, x_step_up_token)
            p.step_up_ok = True
        request.state.principal = p
        return p

    dep._perm = permission  # type: ignore[attr-defined]
    return dep


def public() -> Callable:
    def dep() -> None:
        return None
    dep._perm = "public"  # type: ignore[attr-defined]
    return dep


def check_step_up(p: Principal, token: str | None) -> None:
    if not token:
        raise ApiError(401, "STEP_UP_REQUIRED", "Re-enter your password", "This action needs a fresh password confirmation.")
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], audience="step-up", issuer=ISS)
    except jwt.PyJWTError:
        raise ApiError(401, "STEP_UP_REQUIRED", "Re-enter your password", "The step-up confirmation expired.")
    if claims.get("sid") != p.session_id or claims.get("sub") != p.id:
        raise ApiError(401, "STEP_UP_REQUIRED", "Re-enter your password", "The step-up confirmation does not belong to this session.")


def route_permission(route) -> str | None:
    dependant = getattr(route, "dependant", None)
    if not dependant:
        return None
    for d in dependant.dependencies:
        perm = getattr(d.call, "_perm", None)
        if perm:
            return perm
    return None


def ensure_uuid(value: str, what: str = "id") -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(404, "NOT_FOUND", "Not found", f"No such {what}.")
