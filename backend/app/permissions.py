"""Route permission registry (PRD 4.3). Role -> permission codes; scope qualifiers are enforced in services."""
from __future__ import annotations

ROLE_LABELS = {
    "IA": "Institution admin", "IO": "Institution data operator", "AR": "AICTE reviewer",
    "AS": "AICTE system admin", "ST": "Student or guardian", "VU": "Verifier user", "CL": "System client",
}

# Permissions the PRD matrix grants, by role. Qualifiers (own / grant / step / reg) are applied at the service layer.
PERMISSIONS: dict[str, set[str]] = {
    "IA": {
        "auth:self", "claims:write", "claims:validate", "enrolments:read", "enrolments:manage", "uploads:write",
        "uploads:commit", "submissions:validate", "submissions:submit", "commitments:sign",
        "seat_exceptions:request", "extensions:request", "findings:read", "findings:ack", "rulesets:read",
        "dashboards:institution", "trust:read", "cases:respond", "appeals:file", "students:read", "transfers:write",
        "reports:request", "credentials:issue", "credentials:revoke", "users:manage", "clients:manage", "confirmation:resend",
        "confirmation:assist",
    },
    "IO": {
        "auth:self", "claims:validate", "enrolments:read", "uploads:write", "submissions:validate", "findings:read",
        "rulesets:read", "dashboards:institution", "students:read",
    },
    "AR": {
        "auth:self", "seats:release", "submissions:decide", "commitments:countersign", "seat_exceptions:decide",
        "extensions:decide", "findings:waive", "rulesets:read", "rulesets:publish", "simulations:run",
        "dashboards:aicte", "dashboards:institution", "trust:read", "duplicates:review", "cases:manage",
        "appeals:decide", "elevations:request", "students:reveal", "reports:request", "credentials:revoke",
        "corrections:decide", "dsr:decide", "verifier_orgs:read", "onboarding:decide", "audit:read", "ops:read",
    },
    "AS": {
        "auth:self", "rulesets:draft", "rulesets:read", "simulations:run", "verifier_orgs:manage", "users:manage",
        "clients:manage", "audit:read", "ops:read", "demo:tools", "verifier_orgs:read",
    },
    "ST": {
        "auth:self", "student:wallet", "student:confirm", "corrections:request",
        "dsr:request",
    },
    "VU": {"auth:self", "verifier:request"},
    "CL": {"claims:write", "claims:validate", "uploads:write", "feeds:write"},
}

# Permissions requiring a fresh step-up token (REQ-AUTH-05).
STEP_UP: set[str] = {
    "uploads:commit", "commitments:sign", "commitments:countersign", "rulesets:publish", "credentials:issue",
    "credentials:revoke", "seats:release",
}

PUBLIC = "public"
