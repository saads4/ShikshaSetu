"""MOCK external services (SIMULATED): APAAR registry, SMS gateway, admin controls.

Runs as its own process on :8002 and owns schema `mockext`. Every response carries a banner so nothing here can be
mistaken for a real national system (PRD honesty rule 0.3).
"""
from __future__ import annotations

import hashlib
import re
import time
import random

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import engine

app = FastAPI(title="ShikshaSetu MOCK services (SIMULATED)", docs_url="/docs")
BANNER = "MOCK_SERVICE: simulated data, stands in for the real system. No real person or number."


def _conn():
    c = engine().begin()
    return c


def _control(conn):
    return conn.execute(text("SELECT mode, latency_ms, error_rate FROM mockext.registry_control WHERE id=1")).mappings().first()


@app.get("/healthz")
def healthz():
    return {"status": "ok", "banner": BANNER}


@app.get("/registry/people/{apaar_id}")
def registry_person(apaar_id: str):
    with engine().begin() as conn:
        ctl = _control(conn)
        if ctl["mode"] == "DOWN" or (ctl["error_rate"] and random.random() < float(ctl["error_rate"])):
            raise HTTPException(503, "Registry unavailable (mock outage)")
        if ctl["mode"] == "SLOW":
            time.sleep(max(ctl["latency_ms"], 2500) / 1000)  # exceeds the client's 2 s timeout -> exercises PENDING_VERIFICATION
        elif ctl["latency_ms"]:
            time.sleep(ctl["latency_ms"] / 1000)
        p = conn.execute(text("SELECT apaar_id, status, name, name_native, dob, mobile FROM mockext.registry_people WHERE apaar_id=:a"),
                         {"a": apaar_id}).mappings().first()
    if not p:
        return JSONResponse({"detail": "not found", "source": "MOCK_APAAR_REGISTRY", "banner": BANNER}, status_code=404)
    return {"apaar_id": p["apaar_id"], "status": p["status"], "name": p["name"], "dob": p["dob"].isoformat(), "mobile": p["mobile"],
            "source": "MOCK_APAAR_REGISTRY", "banner": BANNER}


class SmsIn(BaseModel):
    mobile: str = Field(min_length=6, max_length=20)
    template_code: str
    language: str = "en"
    body: str
    otp: str | None = None


@app.post("/sms/send")
def sms_send(body: SmsIn):
    digits = re.sub(r"\D", "", body.mobile)
    masked = f"+91 ••••• ••{digits[-3:]}"
    with engine().begin() as conn:
        mid = conn.execute(text("INSERT INTO mockext.sms_messages (recipient_masked, recipient_hash, template_code, language, body, otp) "
                                "VALUES (:m,:h,:t,:l,:b,:o) RETURNING id"),
                           {"m": masked, "h": hashlib.sha256(digits.encode()).hexdigest()[:16], "t": body.template_code, "l": body.language,
                            "b": body.body, "o": body.otp}).scalar()
    return {"message_id": mid, "status": "ACCEPTED", "source": "MOCK_SMS_GATEWAY", "banner": BANNER}


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(UP|DOWN|SLOW)$")
    latency_ms: int | None = Field(default=None, ge=0, le=10000)


@app.get("/admin/registry-mode")
def get_mode():
    with engine().begin() as conn:
        c = _control(conn)
    return dict(c)


@app.post("/admin/registry-mode")
def set_mode(body: ModeIn):
    with engine().begin() as conn:
        conn.execute(text("UPDATE mockext.registry_control SET mode=:m, latency_ms=coalesce(:l, latency_ms) WHERE id=1"),
                     {"m": body.mode, "l": body.latency_ms})
        c = _control(conn)
    return dict(c)
