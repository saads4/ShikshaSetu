"""Test harness: a throwaway PostgreSQL (TEST_DATABASE_URL, never the hosted project), migrated and minimal-seeded once."""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        env = (BACKEND.parent / ".env")
        if env.exists():
            m = re.search(r"^TEST_DATABASE_URL=(.+)$", env.read_text(encoding="utf-8"), re.M)
            url = m.group(1).strip() if m else None
    if not url:
        pytest.exit("TEST_DATABASE_URL is not set (see .env.example)", returncode=2)
    return url


TEST_URL = _test_url()
os.environ["DATABASE_URL"] = TEST_URL                 # must be set before the app imports its settings
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-" + "x" * 40)
os.environ.setdefault("MASTER_KEY", "test-master-key-" + "y" * 40)
os.environ.setdefault("IDENTITY_HMAC_KEY", "test-identity-key-" + "z" * 40)
os.environ["MOCK_SERVICES_URL"] = "http://127.0.0.1:18002"
os.environ["SEED_PROFILE"] = "minimal"
os.environ["SMS_FIXED_OTP"] = ""
os.environ["WORM_DIR"] = str(BACKEND / "data" / "test-worm")
os.environ["DEMO_MODE"] = "true"
os.environ["APP_ENV"] = "local"
os.environ["SEED_WRITE_DOCS"] = "0"


@pytest.fixture(scope="session", autouse=True)
def database():
    from sqlalchemy import create_engine, text
    eng = create_engine(TEST_URL)
    with eng.begin() as c:
        for s in ("seed", "mockext", "agg", "ops", "cred", "audit", "rules", "identity", "core", "pii", "iam", "ref", "ctx", "intel", "consent", "ingest", "matching", "public"):
            c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    worm = Path(os.environ["WORM_DIR"])
    if worm.exists():
        for f in worm.glob("*"):
            f.unlink()
    env = {**os.environ}
    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env)
    from app import seed_main
    seed_main.run("minimal")
    yield


@pytest.fixture(scope="session", autouse=True)
def mock_server(database):
    import uvicorn
    from mock_services.main import app as mock_app
    server = uvicorn.Server(uvicorn.Config(mock_app, host="127.0.0.1", port=18002, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield
    server.should_exit = True


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def login_token(client, username: str, password: str = "1234") -> str:
    from app import routes_auth
    routes_auth._hits.clear()          # tests log in far more often than a person would; the limiter has its own test
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def tokens(client):
    return {u: login_token(client, u) for u in ("ia.kit", "ia2.kit", "io.kit", "ia.bvec", "ar.national", "ar.national2", "ar.south", "as.admin", "st.aarav", "st.diya", "gd.meera", "vu.employer")}


def auth(tok: str, **extra) -> dict:
    return {"Authorization": f"Bearer {tok}", **extra}


# ---- helpers shared by tests -------------------------------------------------------------
def make_people(n: int, *, dob: date = date(2007, 3, 15)):
    """Insert n synthetic registry people; returns [(apaar, name, dob)]."""
    from app.db import owner_tx
    from app.seed import make_apaar, make_mobile
    import random
    from sqlalchemy import text
    rng = random.Random(secrets.randbits(32))
    out = []
    with owner_tx() as conn:
        for i in range(n):
            a = make_apaar(rng)
            name = f"Test Person{i} Iyer"
            conn.execute(text("INSERT INTO mockext.registry_people (apaar_id, name, dob, mobile) VALUES (:a,:n,:d,:m) ON CONFLICT DO NOTHING"),
                         {"a": a, "n": name, "d": dob, "m": make_mobile(rng)})
            out.append((a, name, dob))
    return out


def claim_body(person, programme_code="CSE", sid=None, **extra):
    a, name, dob = person
    return {"apaar_id": a, "name": name, "dob": str(dob), "institution_student_id": sid or ("T" + uuid.uuid4().hex[:8]),
            "programme_code": programme_code, "gender": "M", "parent_name": "Test Parent", **extra}


def post_claim(client, token, body, key=None):
    return client.post("/api/v1/claims", headers=auth(token, **{"Idempotency-Key": key or uuid.uuid4().hex}), json=body)
