"""REQ-AUTH / REQ-ACL: login, lockout, token hardening, session revoke, route x role matrix, tenant isolation."""
import re
import uuid

import jwt
import pytest

from app import deps
from app.permissions import PERMISSIONS, STEP_UP
from conftest import auth, login_token


def test_login_success_and_generic_failure(client):
    ok = client.post("/api/v1/auth/login", json={"username": "ia.kit", "password": "1234"})
    assert ok.status_code == 200 and ok.json()["user"]["roles"] == ["IA"]
    bad_user = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
    bad_pw = client.post("/api/v1/auth/login", json={"username": "ia.kit", "password": "x"})
    assert bad_user.status_code == bad_pw.status_code == 401
    assert bad_user.json()["code"] == bad_pw.json()["code"] == "INVALID_CREDENTIALS"      # no hint whether the user exists
    assert bad_user.json()["detail"] == bad_pw.json()["detail"]


def test_lockout_after_five_failures_then_success_resets(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "io.bvec", "password": "wrong"})
    locked = client.post("/api/v1/auth/login", json={"username": "io.bvec", "password": "1234"})
    assert locked.status_code == 423 and locked.json()["code"] == "ACCOUNT_LOCKED" and locked.json()["retry_after"] > 0
    from app.db import owner_tx
    from sqlalchemy import text
    with owner_tx() as c:
        c.execute(text("UPDATE iam.users SET locked_until=NULL, failed_attempts=0 WHERE username='io.bvec'"))
    assert client.post("/api/v1/auth/login", json={"username": "io.bvec", "password": "1234"}).status_code == 200


def test_failed_login_is_audited(client):
    from app.db import owner_tx, scalar
    client.post("/api/v1/auth/login", json={"username": "ia.mpb", "password": "nope"})
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='auth.login_failed'") >= 1


def test_only_hs256_accepted_alg_none_rejected(client):
    forged = jwt.encode({"sub": "x", "sid": str(uuid.uuid4()), "iss": "shikshasetu", "aud": "core-api"}, key="", algorithm="none")
    r = client.get("/api/v1/me", headers=auth(forged))
    assert r.status_code == 401 and r.json()["code"] == "INVALID_TOKEN"
    wrong_secret = jwt.encode({"sub": "x", "sid": str(uuid.uuid4()), "iss": "shikshasetu", "aud": "core-api"}, key="k" * 40, algorithm="HS256")
    assert client.get("/api/v1/me", headers=auth(wrong_secret)).status_code == 401


def test_logout_revokes_session_immediately(client):
    tok = login_token(client, "ia2.kit")
    assert client.get("/api/v1/me", headers=auth(tok)).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=auth(tok)).status_code == 200
    r = client.get("/api/v1/me", headers=auth(tok))
    assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED"


def test_passwords_are_argon2id_and_never_returned(client):
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM iam.users WHERE password_hash NOT LIKE '$argon2id$%'") == 0
    body = client.get("/api/v1/me", headers=auth(login_token(client, "ia.kit"))).text
    assert "password" not in body.lower().replace("permissions", "")


def test_demo_accounts_listed_without_passwords(client):
    r = client.get("/api/v1/auth/demo-accounts")
    assert r.status_code == 200
    names = {i["username"] for i in r.json()["items"]}
    assert {"ia.kit", "ia2.kit", "io.kit", "ar.national", "ar.national2", "ar.south", "as.admin", "st.aarav", "gd.meera"} <= names
    assert "password_hash" not in r.text and r.json()["password_hint"] == "1234"


def test_step_up_requires_password(client):
    tok = login_token(client, "ia.kit")
    assert client.post("/api/v1/auth/step-up", headers=auth(tok), json={"password": "wrong"}).status_code == 401
    ok = client.post("/api/v1/auth/step-up", headers=auth(tok), json={"password": "1234"})
    assert ok.status_code == 200 and ok.json()["expires_in"] == 300


def test_client_credentials_token_carries_only_granted_scopes(client):
    r = client.post("/api/v1/auth/token", json={"client_id": "erp-kit", "client_secret": "1234", "scope": "claims:write feeds:write"})
    assert r.status_code == 200 and set(r.json()["scope"].split()) == {"claims:write"}      # feeds:write was not granted to this client
    assert client.post("/api/v1/auth/token", json={"client_id": "erp-kit", "client_secret": "bad"}).status_code == 401


def test_production_guard_refuses_demo_mode(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    prod = s.model_copy(update={"app_env": "production", "demo_mode": True, "jwt_secret": "CHANGE_ME" + "x" * 40})
    problems = prod.production_problems()
    assert any("DEMO_MODE" in p for p in problems) and any("JWT_SECRET" in p for p in problems)


# ----------------------------------------------------------------------------- route x role matrix (SC-11)
ROLE_USERS = {"IA": "ia.kit", "IO": "io.kit", "AR": "ar.national", "AS": "as.admin", "ST": "st.aarav", "VU": "vu.employer"}


def _routes(client):
    from app.main import api_routes
    out = []
    for r in api_routes(client.app):
        path = r.path
        perm = deps.route_permission(r)
        for m in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
            out.append((m, path, perm))
    return out


def test_every_route_declares_a_permission(client):
    missing = [(m, p) for m, p, perm in _routes(client) if perm is None]
    assert not missing, missing


def test_route_by_role_matrix(client, tokens):
    """Every denied role/route combination returns 403 (and is audited); every permitted one is NOT 401/403."""
    zero = str(uuid.UUID(int=0))
    checked = denied = 0
    for method, path, perm in _routes(client):
        if perm == "public" or path == "/api/v1/auth/logout":     # logout would revoke the shared token; it has its own test
            continue
        url = re.sub(r"\{[^}]+\}", zero, path)
        for role, user in ROLE_USERS.items():
            r = client.request(method, url, headers=auth(tokens[user]), json={} if method in ("POST", "PUT", "PATCH") else None)
            allowed = perm in PERMISSIONS[role]
            checked += 1
            if allowed:
                step_up_ok = perm in STEP_UP and r.status_code == 401 and r.json().get("code") == "STEP_UP_REQUIRED"   # reached the route, needs a password step-up
                assert step_up_ok or r.status_code not in (401, 403), f"{role} should reach {method} {path}: got {r.status_code} {r.text[:120]}"
            else:
                assert r.status_code == 403, f"{role} must be denied {method} {path} ({perm}): got {r.status_code}"
                denied += 1
    assert checked > 150 and denied > 100
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='authz.denied'") >= denied


def test_unauthenticated_requests_get_401(client):
    for method, path, perm in _routes(client):
        if perm == "public":
            continue
        r = client.request(method, re.sub(r"\{[^}]+\}", str(uuid.UUID(int=0)), path), json={} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)


def test_cross_tenant_enrolment_returns_404_not_403(client, tokens):
    """BVEC admin asks for a KIT enrolment id: the existence of the record is not revealed."""
    kit_list = client.get("/api/v1/enrolments?limit=1", headers=auth(tokens["ia.kit"])).json()["items"]
    r = client.get(f"/api/v1/enrolments/{kit_list[0]['id']}", headers=auth(tokens["ia.bvec"]))
    assert r.status_code == 404


def test_institution_lists_only_show_own_tenant(client, tokens):
    items = client.get("/api/v1/enrolments?limit=500", headers=auth(tokens["ia.bvec"])).json()["items"]
    kit_ids = {i["id"] for i in client.get("/api/v1/enrolments?limit=500", headers=auth(tokens["ia.kit"])).json()["items"]}
    assert items and not ({i["id"] for i in items} & kit_ids)


def test_regional_reviewer_sees_only_their_region(client, tokens):
    ov = client.get("/api/v1/dashboards/aicte/overview", headers=auth(tokens["ar.south"])).json()
    nat = client.get("/api/v1/dashboards/aicte/overview", headers=auth(tokens["ar.national"])).json()
    south_states = {"Tamil Nadu", "Karnataka", "Kerala", "Telangana", "Andhra Pradesh"}
    assert {s["state"] for s in ov["states"]} <= south_states
    assert nat["kpis"]["institutions"] > ov["kpis"]["institutions"]
    kit = client.get("/api/v1/ref/institutions", headers=auth(tokens["ar.south"])).json()["items"]
    assert all(i["region"] == "SOUTH" for i in kit)


def test_aicte_cannot_open_student_level_institution_dashboard_lists(client, tokens):
    inst = next(i for i in client.get("/api/v1/ref/institutions", headers=auth(tokens["ar.national"])).json()["items"] if i["name"].startswith("Kaveri"))
    d = client.get(f"/api/v1/dashboards/institution?institution_id={inst['id']}", headers=auth(tokens["ar.national"])).json()
    assert d["aggregate_only"] is True and d["awaiting_confirmation"] == []
    assert client.get("/api/v1/enrolments", headers=auth(tokens["ar.national"])).status_code == 403
