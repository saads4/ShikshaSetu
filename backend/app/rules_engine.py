"""Rules engine (REQ-RUL): rules are data; one engine serves the claim path (C), bulk dry-run (B) and submission (S).

Messages and evidence never contain PII values - only field names, codes and counts.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy.engine import Connection

from .db import row, rows

SEV_ORDER = {"Blocker": 0, "Error": 1, "Warning": 2, "Info": 3}
_cache: dict[str, Any] = {"at": 0.0, "set": None}


def active_rule_set(conn: Connection, *, fresh: bool = False) -> dict:
    if not fresh and _cache["set"] and time.monotonic() - _cache["at"] < 30:
        return _cache["set"]
    rs = row(conn, "SELECT id, name, version_no FROM rules.rule_sets WHERE status='ACTIVE'")
    rules = rows(conn, "SELECT * FROM rules.rules WHERE rule_set_id=:i ORDER BY rule_code", i=rs["id"]) if rs else []
    out = {"id": str(rs["id"]) if rs else None, "version_no": rs["version_no"] if rs else None, "rules": rules}
    _cache.update(at=time.monotonic(), set=out)
    return out


def invalidate_cache() -> None:
    _cache.update(at=0.0, set=None)


_LOOKUPS = {
    "gender": {"M", "F", "O"},
    "category": {"GEN", "OBC", "SC", "ST", "EWS"},
    "citizenship": {"INDIAN", "FOREIGN", "OCI", "PIO"},
    "level": {"UG", "PG", "DIPLOMA", "PGDM"},
}


def _resolve(spec: Any, rec: dict) -> Any:
    if isinstance(spec, dict):
        if "field" in spec:
            return rec.get(spec["field"])
        if "days_ago" in spec:
            return rec["today"] - timedelta(days=spec["days_ago"])
        if "today" in spec:
            return rec["today"]
        if "value" in spec:
            return spec["value"]
    return spec


def _cmp(left: Any, op: str, right: Any) -> bool:
    if left is None or right is None:
        return False
    return {">=": lambda: left >= right, "<=": lambda: left <= right, ">": lambda: left > right, "<": lambda: left < right,
            "==": lambda: left == right, "!=": lambda: left != right, "in": lambda: left in right,
            "not_in": lambda: left not in right}[op]()


def _truthy(v: Any) -> bool:
    return v not in (None, "", False, 0)


def _eval_one(rule: dict, rec: dict) -> dict | None:
    p, kind = rule["params"], rule["kind"]
    fail: dict | None = None
    if kind == "REQUIRED_FIELD":
        if not _truthy(rec.get(p["field"])):
            fail = {"fields": [p["field"]]}
    elif kind == "REGEX_FORMAT":
        v = rec.get(p["field"])
        if v is not None and not re.fullmatch(p["pattern"], str(v)):
            fail = {"fields": [p["field"]]}
    elif kind == "COMPARE":
        if not _cmp(_resolve(p["left"], rec), p["op"], _resolve(p["right"], rec)):
            if _resolve(p["left"], rec) is not None:  # absent values are REQUIRED_FIELD's concern
                fail = {"fields": [p["left"].get("field", "value")]}
    elif kind == "IMPLIES":
        cond = p["if"]
        if _cmp(rec.get(cond["field"]), cond.get("op", "=="), _resolve(cond.get("right", {"value": cond.get("value")}), rec)):
            then = p["then"]
            if not _cmp(rec.get(then["field"]), then.get("op", "=="), _resolve(then.get("right", {"value": then.get("value")}), rec)):
                fail = {"fields": [cond["field"], then["field"]]}
    elif kind == "MUTUALLY_EXCLUSIVE":
        if sum(1 for f in p["fields"] if _truthy(rec.get(f))) > 1:
            fail = {"fields": p["fields"]}
    elif kind == "DATE_WITHIN":
        v = rec.get(p["field"])
        if isinstance(v, date):
            lo, hi = _resolve(p.get("min"), rec) if p.get("min") else None, _resolve(p.get("max"), rec) if p.get("max") else None
            if (lo and v < lo) or (hi and v > hi):
                fail = {"fields": [p["field"]]}
    elif kind == "LOOKUP_IN_REFERENCE":
        v = rec.get(p["field"])
        if v is not None and str(v) not in _LOOKUPS[p["lookup"]]:
            fail = {"fields": [p["field"]]}
    elif kind == "BUCKET_CAP":
        b = rec.get("_bucket")
        if b and b["cap"] + b["approved_extra"] > 0:
            pct = 100 * (b["occupied"] + 1) / (b["cap"] + b["approved_extra"])
            if pct >= p.get("warn_pct", 90):
                fail = {"fields": ["bucket_type"], "occupied_after_pct": round(pct, 1)}
    # SUM_RECONCILES applies at the submission site (Phase 3); the claim/bulk sites skip it.
    if not fail:
        return None
    return {"rule_code": rule["rule_code"], "rule_id": str(rule["id"]), "severity": rule["severity"], "category": rule["category"],
            "message": rule["message_template"], "fix": rule["fix_template"], "evidence": fail}


def evaluate(rules: list[dict], rec: dict, site: str) -> list[dict]:
    """Return findings for one record. `site` is C (claim), B (bulk/dry-run) or S (submission)."""
    out = []
    for rule in rules:
        if site not in rule["sites"]:
            continue
        f = _eval_one(rule, rec)
        if f:
            out.append(f)
    return sorted(out, key=lambda f: (SEV_ORDER[f["severity"]], f["rule_code"]))
