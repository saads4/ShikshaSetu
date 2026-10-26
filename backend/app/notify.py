"""Notifications (REQ-OPS-09). SMS goes to the MOCK SMS gateway (SIMULATED); nothing real is ever sent."""
from __future__ import annotations

import logging

from sqlalchemy import text

from . import http
from .config import get_settings
from .core import dumps, now, uuid7
from .db import SYSTEM, tx
from .identity import mask_mobile

log = logging.getLogger("shikshasetu.notify")

TEMPLATES = {
    "OTP_CONFIRM": {
        "en": "ShikshaSetu: your confirmation code is {otp}. It is valid for 10 minutes. Do not share it with anyone.",
        "hi": "शिक्षासेतु: आपका पुष्टि कोड {otp} है। यह 10 मिनट के लिए मान्य है। इसे किसी से साझा न करें।",
    },
    "REMINDER": {
        "en": "ShikshaSetu reminder: please confirm your admission before {date}. Sign in to your student account.",
        "hi": "शिक्षासेतु अनुस्मारक: कृपया {date} से पहले अपने प्रवेश की पुष्टि करें।",
    },
}


def render(code: str, language: str, **params) -> str:
    return TEMPLATES[code].get(language, TEMPLATES[code]["en"]).format(**params)


def send_sms(mobile: str, template_code: str, language: str, otp: str | None = None, **params) -> tuple[bool, str | None]:
    s = get_settings()
    body = render(template_code, language, otp=otp or "", **params)
    try:
        r = http.client().post(f"{s.mock_services_url}/sms/send", timeout=3,
                       json={"mobile": mobile, "template_code": template_code, "language": language, "body": body, "otp": otp})
        r.raise_for_status()
        return True, str(r.json().get("message_id"))
    except Exception as exc:  # noqa: BLE001 - delivery failures are recorded, not raised
        log.warning("sms delivery failed: %s", type(exc).__name__)
        return False, None


def record(channel: str, template_code: str, status: str, *, institution_id=None, enrolment_id=None, user_id=None, kind="STUDENT",
           language="en", params: dict | None = None, provider_ref: str | None = None) -> None:
    with tx(SYSTEM) as conn:
        conn.execute(text(
            "INSERT INTO ops.notifications (id, recipient_user_id, recipient_kind, channel, template_code, language, params, status, attempts, "
            "provider_ref, institution_id, enrolment_id, sent_at) VALUES (:id, CAST(:u AS uuid), :k, :ch, :tc, :l, CAST(:p AS jsonb), :st, 1, "
            ":pr, CAST(:i AS uuid), CAST(:e AS uuid), :t)"),
            {"id": uuid7(), "u": user_id, "k": kind, "ch": channel, "tc": template_code, "l": language, "p": dumps(params or {}), "st": status,
             "pr": provider_ref, "i": str(institution_id) if institution_id else None, "e": enrolment_id,
             "t": now() if status == "SENT" else None})


def send_otp(institution_id: str, enrolment_id: str, mobile: str, otp: str, channel: str, language: str = "en") -> bool:
    ok, ref = send_sms(mobile, "OTP_CONFIRM", language, otp=otp)
    record("SMS", "OTP_CONFIRM", "SENT" if ok else "FAILED", institution_id=institution_id, enrolment_id=enrolment_id, language=language,
           params={"to": mask_mobile(mobile), "channel": channel}, provider_ref=ref)
    return ok
