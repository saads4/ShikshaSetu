"""Onboarding, user and API-client administration, webhooks with retries, liveness feed, retention (REQ-ONB, REQ-INTG, REQ-OPS)."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import text

from . import audit, crypto, http, identity, ops
from .config import get_settings
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, owner_tx, row, rows, scalar, tx
from .deps import Principal, hash_password

EVENTS = ["job.completed", "claim.created", "claim.pending_verification_resolved", "finding.created", "confirmation.completed", "credential.revoked", "case.updated"]
TOPIC_EVENT = {"claim.created": "claim.created", "credential.revoked": "credential.revoked", "case.opened": "case.updated", "upload.completed": "job.completed", "finding.created": "finding.created"}
BACKOFF = [60, 300, 1800, 7200, 21600]
CLIENT_SCOPES = {"claims:write", "claims:validate", "uploads:write", "feeds:write"}


def _initial_password() -> tuple[str, bool]:
    s = get_settings()
    return (s.seed_demo_password, False) if s.demo_mode else (secrets.token_urlsafe(12), True)


# ------------------------------------------------------------------ onboarding (identity check is SIMULATED)
def submit_onboarding(aicte_id: str, officer: str, designation: str, email: str, phone: str | None) -> dict:
    with tx(SYSTEM) as conn:
        i = row(conn, "SELECT id, status, nodal_officer_email, name FROM ref.institutions WHERE aicte_id=:a", a=aicte_id.strip())
        if not i or not (i["status"] == "PENDING_APPROVAL" or (i["status"] == "ACTIVE" and not scalar(conn, "SELECT count(*) FROM iam.users u JOIN iam.user_roles r ON r.user_id=u.id WHERE u.institution_id=:i AND r.role_code='IA'", i=i["id"]))):
            raise ApiError(404, "NOT_FOUND", "Institution not eligible", "That AICTE ID is unknown, or the institution already has an administrator.",
                           errors=[{"field": "aicte_id", "code": "UNKNOWN", "message": "Not eligible for onboarding.", "fix_suggestion": "Check the AICTE ID printed on the approval letter."}])
        if scalar(conn, "SELECT count(*) FROM iam.onboarding_requests WHERE institution_id=:i AND status='PENDING'", i=i["id"]):
            raise ApiError(409, "ALREADY_PENDING", "Already submitted", "An application for this institution is already pending.")
        dom = lambda e: e.split("@")[-1].lower()
        match = bool(i["nodal_officer_email"]) and dom(email) == dom(i["nodal_officer_email"])
        token = secrets.token_urlsafe(16)
        rid = uuid7()
        conn.execute(text("INSERT INTO iam.onboarding_requests (id, aicte_id, institution_id, officer_name, designation, email, phone, identity_check, identity_note, status_token) VALUES (:id,:a,:i,:o,:d,:e,:p,:c,:n,:t)"),
                     {"id": rid, "a": aicte_id.strip(), "i": i["id"], "o": officer, "d": designation, "e": email, "p": phone, "c": "MATCH" if match else "MISMATCH",
                      "n": "Simulated check: official email domain compared with the master record. Production uses verified officer attestation.", "t": token})
        audit.log(conn, "onboarding.submitted", entity_type="onboarding", entity_id=rid, institution_id=i["id"], payload={"identity_check": "MATCH" if match else "MISMATCH"})
    return {"status_token": token, "institution": i["name"], "identity_check": "MATCH" if match else "MISMATCH", "label": "SIMULATED check — production uses verified officer attestation"}


def onboarding_status(token: str) -> dict:
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT r.status, r.reason, r.created_at, r.decided_at, i.name AS institution FROM iam.onboarding_requests r LEFT JOIN ref.institutions i ON i.id=r.institution_id WHERE r.status_token=:t", t=token)
    if not r:
        raise ApiError(404, "NOT_FOUND", "Not found", "No application with that token.")
    return r


def list_onboarding(p: Principal) -> list[dict]:
    from .cases import in_scope
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT r.id, r.aicte_id, r.officer_name, r.designation, r.email, r.phone, r.identity_check, r.identity_note, r.status, r.reason, r.created_at, i.name AS institution_name, i.state, i.region "
                       "FROM iam.onboarding_requests r JOIN ref.institutions i ON i.id=r.institution_id ORDER BY (r.status='PENDING') DESC, r.created_at DESC LIMIT 200")
    return [{**x, "id": str(x["id"])} for x in d if in_scope(p, x)]


def decide_onboarding(p: Principal, oid: str, approve: bool, reason: str) -> dict:
    from .cases import in_scope
    pw_shown = None
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT r.*, i.slug, i.state, i.region, i.name AS institution_name FROM iam.onboarding_requests r JOIN ref.institutions i ON i.id=r.institution_id WHERE r.id=CAST(:o AS uuid) FOR UPDATE OF r", o=oid)
        if not r or not in_scope(p, r):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such application.")
        if r["status"] != "PENDING":
            raise ApiError(409, "INVALID_STATE", "Already decided", f"This application is {r['status']}.")
        username = None
        if approve:
            username = f"ia.{r['slug']}"
            if scalar(conn, "SELECT count(*) FROM iam.users WHERE lower(username)=lower(:u)", u=username):
                raise ApiError(409, "USER_EXISTS", "Administrator exists", f"{username} already exists.")
            pw, shown = _initial_password()
            uid = uuid7()
            conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, utype, password_hash, is_demo, institution_id) VALUES (:id,:u,:d,:e,'INSTITUTION',:pw,:demo,:i)"),
                         {"id": uid, "u": username, "d": r["officer_name"], "e": r["email"], "pw": hash_password(pw), "demo": get_settings().demo_mode, "i": r["institution_id"]})
            conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type, scope_value) VALUES (:id,:u,'IA','INSTITUTION',:i)"), {"id": uuid7(), "u": uid, "i": str(r["institution_id"])})
            conn.execute(text("UPDATE ref.institutions SET status='ACTIVE', nodal_officer_name=:o, nodal_officer_email=:e, updated_at=now() WHERE id=:i"), {"o": r["officer_name"], "e": r["email"], "i": r["institution_id"]})
            pw_shown = pw if shown else None
        conn.execute(text("UPDATE iam.onboarding_requests SET status=:s, decided_by=CAST(:u AS uuid), decided_at=:t, reason=:r WHERE id=:o"), {"s": "APPROVED" if approve else "REJECTED", "u": p.id, "t": now(), "r": reason, "o": r["id"]})
        audit.log(conn, "onboarding.decided", entity_type="onboarding", entity_id=r["id"], actor_id=p.id, actor_role=p.role, institution_id=r["institution_id"], justification=reason, request_id=p.request_id, payload={"approved": approve})
    out = {"status": "APPROVED" if approve else "REJECTED"}
    if approve:
        out.update(admin_username=username, initial_password=pw_shown, note="The administrator signs in with this username. In demo mode the password is the demo password." if not pw_shown else "Give this one-time password to the officer; it is not shown again.")
    return out


# ------------------------------------------------------------------ users
def list_users(p: Principal) -> list[dict]:
    with tx(SYSTEM) as conn:
        if p.institution_id:
            d = rows(conn, "SELECT u.id, u.username, u.display_name, u.status, u.last_login_at, r.role_code, r.scope_type FROM iam.users u JOIN iam.user_roles r ON r.user_id=u.id WHERE u.institution_id=CAST(:i AS uuid) ORDER BY u.username", i=p.institution_id)
        else:
            d = rows(conn, "SELECT u.id, u.username, u.display_name, u.status, u.last_login_at, r.role_code, r.scope_type, r.scope_value FROM iam.users u JOIN iam.user_roles r ON r.user_id=u.id WHERE u.utype='AICTE' ORDER BY u.username")
    return [{**x, "id": str(x["id"])} for x in d]


def create_user(p: Principal, username: str, display_name: str, role: str, scope_type: str | None, scope_value: str | None) -> dict:
    inst = p.institution_id
    if inst:
        if role not in ("IO", "IA"):
            raise ApiError(403, "FORBIDDEN", "Not permitted", "An institution admin can create only operator (IO) or admin (IA) users, and never AICTE roles.")
        utype, st, sv = "INSTITUTION", "INSTITUTION", inst
    else:
        if role not in ("AR", "AS"):
            raise ApiError(403, "FORBIDDEN", "Not permitted", "AICTE administrators create AICTE reviewer or system-admin users.")
        utype, st, sv = "AICTE", scope_type or "NATIONAL", scope_value
        if st not in ("NATIONAL", "REGION", "STATE") or (st != "NATIONAL" and not sv):
            raise ApiError(422, "VALIDATION_ERROR", "Invalid scope", "Choose NATIONAL, or REGION/STATE with a value.", errors=[{"field": "scope_value", "code": "REQUIRED", "message": "Scope value is required.", "fix_suggestion": "e.g. SOUTH"}])
    pw, shown = _initial_password()
    with tx(SYSTEM) as conn:
        if scalar(conn, "SELECT count(*) FROM iam.users WHERE lower(username)=lower(:u)", u=username):
            raise ApiError(409, "USER_EXISTS", "Username taken", "Choose a different username.", errors=[{"field": "username", "code": "UNIQUE", "message": "Already in use.", "fix_suggestion": "Add a suffix."}])
        uid = uuid7()
        conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, utype, password_hash, is_demo, institution_id) VALUES (:id,:u,:d,:e,:t,:pw,:demo,CAST(:i AS uuid))"),
                     {"id": uid, "u": username, "d": display_name, "e": f"{username}@example.test", "t": utype, "pw": hash_password(pw), "demo": get_settings().demo_mode, "i": inst})
        conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type, scope_value) VALUES (:id,:u,:r,:st,:sv)"), {"id": uuid7(), "u": uid, "r": role, "st": st, "sv": sv})
        audit.log(conn, "user.created", entity_type="user", entity_id=uid, actor_id=p.id, actor_role=p.role, institution_id=inst, request_id=p.request_id, payload={"role": role})
    return {"id": str(uid), "username": username, "role": role, "initial_password": pw if shown else None,
            "note": "In demo mode the password is the demo password (1234)." if not shown else "Shown once. Ask the user to change it after signing in."}


def deactivate_user(p: Principal, uid: str) -> dict:
    with tx(SYSTEM) as conn:
        u = row(conn, "SELECT id, institution_id, utype, username FROM iam.users WHERE id=CAST(:u AS uuid)", u=uid)
        if not u or (p.institution_id and str(u["institution_id"]) != p.institution_id) or (not p.institution_id and u["utype"] != "AICTE"):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such user.")
        if str(u["id"]) == p.id:
            raise ApiError(409, "INVALID_STATE", "Not yourself", "You cannot deactivate your own account.")
        conn.execute(text("UPDATE iam.users SET status='DISABLED', updated_at=:t WHERE id=:u"), {"t": now(), "u": u["id"]})
        n = conn.execute(text("UPDATE iam.sessions SET revoked_at=:t WHERE user_id=:u AND revoked_at IS NULL"), {"t": now(), "u": u["id"]}).rowcount     # immediate effect (REQ-ONB-06)
        audit.log(conn, "user.deactivated", entity_type="user", entity_id=u["id"], actor_id=p.id, actor_role=p.role, institution_id=u["institution_id"], request_id=p.request_id, payload={"sessions_revoked": n})
    return {"status": "DISABLED", "sessions_revoked": n}


# ------------------------------------------------------------------ API clients
def list_clients(p: Principal) -> list[dict]:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT id, name, client_id, scopes, status, last_used_at, created_at FROM iam.api_clients WHERE " + ("institution_id=CAST(:i AS uuid)" if p.institution_id else "institution_id IS NULL") + " ORDER BY created_at", **({"i": p.institution_id} if p.institution_id else {}))
    return [{**x, "id": str(x["id"])} for x in d]


def create_client(p: Principal, name: str, scopes: list[str]) -> dict:
    bad = [s for s in scopes if s not in CLIENT_SCOPES or (not p.institution_id and s != "feeds:write") or (p.institution_id and s == "feeds:write")]
    if bad or not scopes:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid scopes", f"Allowed here: {sorted(s for s in CLIENT_SCOPES if (s == 'feeds:write') == (not p.institution_id))}.", errors=[{"field": "scopes", "code": "ENUM", "message": "Scope not allowed for your account.", "fix_suggestion": "Choose from the list."}])
    secret = secrets.token_urlsafe(24)
    cid = uuid7()
    with tx(SYSTEM) as conn:
        conn.execute(text("INSERT INTO iam.api_clients (id, institution_id, name, client_id, secret_hash, scopes, is_demo) VALUES (:id,CAST(:i AS uuid),:n,:c,:h,:s,false)"),
                     {"id": cid, "i": p.institution_id, "n": name, "c": "cl-" + secrets.token_hex(5), "h": hash_password(secret), "s": scopes})
        client_id = scalar(conn, "SELECT client_id FROM iam.api_clients WHERE id=:i", i=cid)
        audit.log(conn, "client.created", entity_type="client", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"scopes": scopes})
    return {"id": str(cid), "client_id": client_id, "client_secret": secret, "note": "The secret is shown once and stored only as a hash."}


def rotate_client(p: Principal, cid: str) -> dict:
    secret = secrets.token_urlsafe(24)
    with tx(SYSTEM) as conn:
        n = conn.execute(text("UPDATE iam.api_clients SET secret_hash=:h WHERE id=CAST(:c AS uuid) AND " + ("institution_id=CAST(:i AS uuid)" if p.institution_id else "institution_id IS NULL")),
                         {"h": hash_password(secret), "c": cid, **({"i": p.institution_id} if p.institution_id else {})}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such client.")
        audit.log(conn, "client.rotated", entity_type="client", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"client_secret": secret, "note": "The previous secret no longer works. Shown once."}


def disable_client(p: Principal, cid: str) -> dict:
    with tx(SYSTEM) as conn:
        n = conn.execute(text("UPDATE iam.api_clients SET status='DISABLED' WHERE id=CAST(:c AS uuid) AND " + ("institution_id=CAST(:i AS uuid)" if p.institution_id else "institution_id IS NULL")), {"c": cid, **({"i": p.institution_id} if p.institution_id else {})}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such client.")
        audit.log(conn, "client.disabled", entity_type="client", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"status": "DISABLED"}


# ------------------------------------------------------------------ webhooks
def check_url(url: str) -> None:
    """SSRF guard (REQ-INTG-07): block loopback/private/link-local targets unless ALLOW_PRIVATE_WEBHOOK_URLS=true (the local demo sink)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid URL", "Use an http(s) URL.", errors=[{"field": "url", "code": "URL", "message": "Not a valid http(s) URL.", "fix_suggestion": "https://erp.example.org/hooks"}])
    if os.environ.get("ALLOW_PRIVATE_WEBHOOK_URLS", "true" if get_settings().demo_mode else "false").lower() == "true":
        return
    try:
        addrs = {ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(u.hostname, u.port or 443)}
    except Exception:
        raise ApiError(422, "VALIDATION_ERROR", "Host not resolvable", "The webhook host could not be resolved.", errors=[{"field": "url", "code": "DNS", "message": "Unresolvable host.", "fix_suggestion": "Check the host name."}])
    if any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast for a in addrs):
        raise ApiError(422, "VALIDATION_ERROR", "Private address blocked", "Webhook targets on private or loopback networks are not allowed.", errors=[{"field": "url", "code": "SSRF", "message": "Blocked address range.", "fix_suggestion": "Use a public HTTPS endpoint."}])


def create_webhook(p: Principal, url: str, events: list[str]) -> dict:
    bad = [e for e in events if e not in EVENTS]
    if bad or not events:
        raise ApiError(422, "VALIDATION_ERROR", "Unknown events", f"Events: {EVENTS}", errors=[{"field": "events", "code": "ENUM", "message": "Unknown event.", "fix_suggestion": "Pick from the list."}])
    check_url(url)
    secret = "whsec_" + secrets.token_urlsafe(24)
    wid = uuid7()
    with tx(SYSTEM) as conn:
        conn.execute(text("INSERT INTO iam.webhook_endpoints (id, institution_id, url, secret_enc, events, created_by) VALUES (:id,CAST(:i AS uuid),:u,:s,:e,CAST(:c AS uuid))"),
                     {"id": wid, "i": p.institution_id, "u": url, "s": crypto.wrap(secret.encode()), "e": events, "c": p.id})
        audit.log(conn, "webhook.created", entity_type="webhook", entity_id=wid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"events": events})
    return {"id": str(wid), "secret": secret, "note": "The signing secret is shown once. Deliveries carry X-Sentinel-Signature: t=<unix>,v1=<HMAC-SHA256(secret, t.body)> and identifiers only (no personal data)."}


def list_webhooks(p: Principal) -> list[dict]:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT e.id, e.url, e.events, e.status, e.created_at, (SELECT count(*) FROM ops.webhook_deliveries d WHERE d.endpoint_id=e.id AND d.status='DELIVERED') AS delivered, "
                       "(SELECT count(*) FROM ops.webhook_deliveries d WHERE d.endpoint_id=e.id AND d.status IN ('RETRYING','PENDING')) AS pending, "
                       "(SELECT count(*) FROM ops.webhook_deliveries d WHERE d.endpoint_id=e.id AND d.status='DEAD_LETTERED') AS dead FROM iam.webhook_endpoints e WHERE e.institution_id " + ("= CAST(:i AS uuid)" if p.institution_id else "IS NULL") + " ORDER BY e.created_at", **({"i": p.institution_id} if p.institution_id else {}))
    return [{**x, "id": str(x["id"])} for x in d]


def set_webhook_status(p: Principal, wid: str, status: str) -> dict:
    with tx(SYSTEM) as conn:
        n = conn.execute(text("UPDATE iam.webhook_endpoints SET status=:s WHERE id=CAST(:w AS uuid) AND institution_id IS NOT DISTINCT FROM CAST(:i AS uuid)"), {"s": status, "w": wid, "i": p.institution_id}).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such webhook.")
    return {"status": status}


def deliveries(p: Principal, wid: str | None = None, status: str | None = None) -> list[dict]:
    where, params = ["e.institution_id IS NOT DISTINCT FROM CAST(:i AS uuid)"], {"i": p.institution_id}
    if wid:
        where.append("d.endpoint_id=CAST(:w AS uuid)"); params["w"] = wid
    if status:
        where.append("d.status=:s"); params["s"] = status
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT d.id, d.event_type, d.status, d.attempts, d.next_attempt_at, d.last_status_code, d.last_error, d.created_at, d.delivered_at, e.url FROM ops.webhook_deliveries d JOIN iam.webhook_endpoints e ON e.id=d.endpoint_id WHERE "
                       + " AND ".join(where) + " ORDER BY d.created_at DESC LIMIT 100", **params)
    return [{**x, "id": str(x["id"])} for x in d]


def replay(p: Principal, did: str) -> dict:
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT d.id, d.status FROM ops.webhook_deliveries d JOIN iam.webhook_endpoints e ON e.id=d.endpoint_id WHERE d.id=CAST(:d AS uuid) AND e.institution_id IS NOT DISTINCT FROM CAST(:i AS uuid)", d=did, i=p.institution_id)
        if not r or r["status"] != "DEAD_LETTERED":
            raise ApiError(404, "NOT_FOUND", "Not found", "No dead-lettered delivery with that id.")
        conn.execute(text("UPDATE ops.webhook_deliveries SET status='PENDING', attempts=0, first_attempt_at=NULL, next_attempt_at=:t WHERE id=:d"), {"t": now(), "d": r["id"]})
    ops.enqueue("webhook", "webhook.deliver", {"delivery_id": did})
    return {"status": "PENDING"}


def emit(conn, institution_id, topic: str, aggregate_id, payload: dict, extra_event: str | None = None) -> int:
    """Create deliveries for every active endpoint subscribed to the event (called by the outbox projector; identifiers only)."""
    event = extra_event or TOPIC_EVENT.get(topic)
    if not event or not institution_id:
        return 0
    eps = rows(conn, "SELECT id FROM iam.webhook_endpoints WHERE institution_id=:i AND status='ACTIVE' AND :e = ANY(events)", i=institution_id, e=event)
    for e in eps:
        did = uuid7()
        body = {"event": event, "delivery_id": str(did), "created_at": now().isoformat(), "institution_id": str(institution_id), "aggregate_id": str(aggregate_id), "data": {k: v for k, v in payload.items() if k != "academic_year" or True}}
        conn.execute(text("INSERT INTO ops.webhook_deliveries (id, endpoint_id, institution_id, event_type, payload, next_attempt_at) VALUES (:id,:e,:i,:t,CAST(:p AS jsonb),:n)"),
                     {"id": did, "e": e["id"], "i": institution_id, "t": event, "p": dumps(body), "n": now()})
        conn.execute(text("INSERT INTO ops.jobs (id, queue, type, payload, run_at, institution_id) VALUES (:id,'webhook','webhook.deliver',CAST(:p AS jsonb),:r,:i)"),
                     {"id": uuid7(), "p": dumps({"delivery_id": str(did)}), "r": now() + timedelta(seconds=2), "i": institution_id})
    return len(eps)


def sign(secret: str, body: str, t: int) -> str:
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()


@ops.handler("webhook.deliver")
def deliver(payload: dict) -> dict:
    did = payload["delivery_id"]
    with owner_tx() as conn:
        d = row(conn, "SELECT d.*, e.url, e.secret_enc, e.status AS ep_status FROM ops.webhook_deliveries d JOIN iam.webhook_endpoints e ON e.id=d.endpoint_id WHERE d.id=CAST(:d AS uuid) FOR UPDATE OF d", d=did)
    if not d:
        raise RuntimeError("delivery row not visible yet")                                   # retried by the queue
    if d["status"] in ("DELIVERED", "DEAD_LETTERED") or d["ep_status"] != "ACTIVE":
        return {"skipped": True}
    body = json.dumps(d["payload"], separators=(",", ":"), sort_keys=True)
    t = int(now().timestamp())
    secret = crypto.unwrap(d["secret_enc"]).decode()
    code, err = None, None
    try:
        r = http.client().post(d["url"], content=body, timeout=5, headers={"Content-Type": "application/json", "X-Sentinel-Signature": sign(secret, body, t), "X-Sentinel-Event": d["event_type"], "X-Sentinel-Delivery": did})
        code = r.status_code
        ok = 200 <= code < 300
    except Exception as exc:  # noqa: BLE001
        ok, err = False, type(exc).__name__
    attempts = d["attempts"] + 1
    first = d["first_attempt_at"] or now()
    with owner_tx() as conn:
        if ok:
            conn.execute(text("UPDATE ops.webhook_deliveries SET status='DELIVERED', attempts=:a, last_status_code=:c, delivered_at=:t, first_attempt_at=:f, last_error=NULL WHERE id=CAST(:d AS uuid)"), {"a": attempts, "c": code, "t": now(), "f": first, "d": did})
            return {"delivered": True}
        if now() - first > timedelta(hours=24):
            conn.execute(text("UPDATE ops.webhook_deliveries SET status='DEAD_LETTERED', attempts=:a, last_status_code=:c, last_error=:e, first_attempt_at=:f WHERE id=CAST(:d AS uuid)"), {"a": attempts, "c": code, "e": err or f"HTTP {code}", "f": first, "d": did})
            return {"dead_lettered": True}
        delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        nxt = now() + timedelta(seconds=delay)
        conn.execute(text("UPDATE ops.webhook_deliveries SET status='RETRYING', attempts=:a, last_status_code=:c, last_error=:e, next_attempt_at=:n, first_attempt_at=:f WHERE id=CAST(:d AS uuid)"), {"a": attempts, "c": code, "e": err or f"HTTP {code}", "n": nxt, "f": first, "d": did})
        conn.execute(text("INSERT INTO ops.jobs (id, queue, type, payload, run_at) VALUES (:id,'webhook','webhook.deliver',CAST(:p AS jsonb),:r)"), {"id": uuid7(), "p": dumps({"delivery_id": did}), "r": nxt})
    return {"retry_in_s": delay}


def sink_record(endpoint_id: str, headers: dict, body: str) -> dict:
    """Local webhook sink for demos: records the delivery and checks the HMAC signature with the endpoint's secret."""
    with owner_tx() as conn:
        e = row(conn, "SELECT secret_enc FROM iam.webhook_endpoints WHERE id=CAST(:e AS uuid)", e=endpoint_id)
        if not e:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such endpoint.")
        secret = crypto.unwrap(e["secret_enc"]).decode()
        sig = headers.get("x-sentinel-signature", "")
        try:
            parts = dict(x.split("=", 1) for x in sig.split(","))
            valid = hmac.compare_digest(sign(secret, body, int(parts["t"])), sig) and abs(now().timestamp() - int(parts["t"])) < 300
        except Exception:
            valid = False
        conn.execute(text("INSERT INTO ops.webhook_sink (endpoint_id, headers, body, signature_valid) VALUES (CAST(:e AS uuid), CAST(:h AS jsonb), :b, :v)"), {"e": endpoint_id, "h": dumps({k: v for k, v in headers.items() if k.startswith("x-sentinel")}), "b": body[:4000], "v": valid})
    return {"received": True, "signature_valid": valid}


# ------------------------------------------------------------------ liveness feed [SIMULATED] and retention
def ingest_liveness(items: list[dict]) -> dict:
    """POST /feeds/liveness: the APAAR ID is hashed on receipt and never stored raw (REQ-INTG-09)."""
    ok, rejected = [], 0
    for it in items:
        h = identity.clean_apaar(it.get("apaar_id"))
        if h.issues:
            rejected += 1
            continue
        ok.append({"k": crypto.identity_key(h.value), "abc": bool(it.get("has_abc_credit")), "ex": bool(it.get("has_exam_registration")), "la": it.get("last_activity_at")})
    with owner_tx() as conn:
        for i in range(0, len(ok), 1000):
            conn.execute(text("INSERT INTO intel.liveness_feed (identity_key, has_abc_credit, has_exam_registration, last_activity_at) VALUES (:k,:abc,:ex,CAST(:la AS timestamptz)) "
                              "ON CONFLICT (identity_key) DO UPDATE SET has_abc_credit=EXCLUDED.has_abc_credit, has_exam_registration=EXCLUDED.has_exam_registration, last_activity_at=EXCLUDED.last_activity_at, received_at=now()"), ok[i:i + 1000])
    return {"accepted": len(ok), "rejected": rejected, "label": "SIMULATED feed (stands in for ABC / exam-registration systems)"}


RETENTION = [("STAGED_ROWS", 7, "PURGE"), ("IDEMPOTENCY_KEYS", 7, "PURGE"), ("EXPIRED_SESSIONS", 30, "PURGE"), ("EXPIRED_OTP_HASHES", 1, "ANONYMISE"), ("EXPIRED_GRANTS", 0, "MARK")]


@ops.handler("maintenance.retention")
def retention(_: dict) -> dict:
    t = now()
    out = {}
    with owner_tx() as conn:
        out["STAGED_ROWS"] = conn.execute(text("DELETE FROM ingest.staged_rows WHERE job_id IN (SELECT id FROM ingest.upload_jobs WHERE status IN ('COMPLETED','PARTIAL','FAILED','CANCELLED') AND updated_at < :t)"), {"t": t - timedelta(days=7)}).rowcount
        out["IDEMPOTENCY_KEYS"] = conn.execute(text("DELETE FROM iam.idempotency_keys WHERE expires_at < :t"), {"t": t}).rowcount
        out["EXPIRED_SESSIONS"] = conn.execute(text("DELETE FROM iam.sessions WHERE expires_at < :t"), {"t": t - timedelta(days=30)}).rowcount
        out["EXPIRED_OTP_HASHES"] = conn.execute(text("UPDATE core.confirmation_requests SET otp_hash='', otp_salt='' WHERE otp_expires_at < :t AND otp_hash <> ''"), {"t": t - timedelta(days=1)}).rowcount
        out["EXPIRED_GRANTS"] = conn.execute(text("UPDATE pii.access_grants SET revoked_at=expires_at WHERE expires_at < :t AND revoked_at IS NULL"), {"t": t}).rowcount
        for k, v in out.items():
            conn.execute(text("UPDATE ops.retention_policies SET last_run_at=:t, last_count=:c WHERE data_class=:k"), {"t": t, "c": v, "k": k})
    return out
