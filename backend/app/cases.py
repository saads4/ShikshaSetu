"""Cases, appeals, anomaly-alert handling, elevation grants and seat release (REQ-INT-09..11, REQ-ACL-06, REQ-GATE-09)."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from sqlalchemy import text

from . import audit, claims, crypto, identity, keys, notify, verification
from .core import ApiError, canonical_json, dumps, now, uuid7
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal

RESPONSE_DAYS, APPEAL_DAYS = 14, 15
OUTCOMES = {"CONFIRMED_VIOLATION", "NOT_SUBSTANTIATED", "DATA_CORRECTED", "REFERRED_FOR_INSPECTION", "FALSE_POSITIVE"}
VOLUME_LIMIT = 200


def in_scope(p: Principal, inst: dict) -> bool:
    if p.institution_id:
        return str(inst.get("institution_id", inst.get("id"))) == p.institution_id
    if p.scope_type == "NATIONAL":
        return True
    return (inst["region"] == p.scope_value) if p.scope_type == "REGION" else (inst["state"] == p.scope_value) if p.scope_type == "STATE" else False


def _load(conn, cid: str, p: Principal, lock: bool = False) -> dict:
    c = row(conn, "SELECT c.*, i.name AS institution_name, i.state, i.region, u.username AS owner_name, e.enrolment_ref FROM intel.cases c JOIN ref.institutions i ON i.id=c.institution_id "
                  "LEFT JOIN iam.users u ON u.id=c.owner_id LEFT JOIN core.enrolments e ON e.id=c.enrolment_id WHERE c.id=CAST(:c AS uuid)" + (" FOR UPDATE OF c" if lock else ""), c=cid)
    if not c or not in_scope(p, c):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such case.")
    return c


def _event(conn, c: dict, p: Principal | None, etype: str, body: str | None = None) -> None:
    conn.execute(text("INSERT INTO intel.case_events (case_id, institution_id, actor_id, event_type, body) VALUES (:c, :i, CAST(:a AS uuid), :t, :b)"),
                 {"c": c["id"], "i": c["institution_id"], "a": p.id if p else None, "t": etype, "b": body})


def _pack_hash(pack: dict) -> str:
    return hashlib.sha256(canonical_json(pack)).hexdigest()


def open_case(conn, p: Principal, *, institution_id, type_: str, title: str, alert_id=None, enrolment_id=None, evidence: dict | None = None) -> str:
    n = scalar(conn, "SELECT nextval('intel.case_no_seq')")
    cid = uuid7()
    pack = {"opened_at": now().isoformat(), "type": type_, "alert_id": str(alert_id) if alert_id else None, **(evidence or {})}
    conn.execute(text("INSERT INTO intel.cases (id, case_no, institution_id, type, title, owner_id, status, evidence_pack, evidence_sha256, opened_by, enrolment_id, alert_id) VALUES "
                      "(:id,:no,:i,:t,:ti,CAST(:u AS uuid),'OPEN',CAST(:ev AS jsonb),:h,CAST(:u AS uuid),:e,:a)"),
                 {"id": cid, "no": f"CASE-{now().year}-{n:04d}", "i": institution_id, "t": type_, "ti": title, "u": p.id, "ev": dumps(pack), "h": _pack_hash(pack), "e": enrolment_id, "a": alert_id})
    conn.execute(text("INSERT INTO intel.case_events (case_id, institution_id, actor_id, event_type, body) VALUES (:c,:i,CAST(:a AS uuid),'OPENED',:b)"),
                 {"c": cid, "i": institution_id, "a": p.id, "b": f"Opened: {title}"})
    audit.log(conn, "case.opened", entity_type="case", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=institution_id, case_id=cid, request_id=p.request_id, payload={"type": type_})
    return str(cid)


# ------------------------------------------------------------------ alerts
def list_alerts(p: Principal, status: str | None, layer: str | None, severity: str | None, limit: int, offset: int) -> dict:
    where, params = ["true"], {}
    for col, val in (("a.status", status), ("a.layer", layer), ("a.severity", severity)):
        if val:
            where.append(f"{col}=:{col.split('.')[1]}"); params[col.split(".")[1]] = val
    with tx(SYSTEM) as conn:
        data = rows(conn, "SELECT a.id, a.layer, a.type, a.severity, a.status, a.deviation, a.suggested_action, a.created_at, a.case_id, a.evidence, i.name AS institution_name, i.state, i.region, a.institution_id "
                          "FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE " + " AND ".join(where) +
                    " ORDER BY CASE a.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, a.created_at DESC LIMIT 2000", **params)
    data = [d for d in data if in_scope(p, d)]
    return {"total": len(data), "items": [{**d, "id": str(d["id"]), "institution_id": str(d["institution_id"]), "case_id": str(d["case_id"]) if d["case_id"] else None,
                                           "deviation": float(d["deviation"]) if d["deviation"] is not None else None} for d in data[offset:offset + limit]]}


def alert_detail(p: Principal, aid: str) -> dict:
    with tx(SYSTEM) as conn:
        a = row(conn, "SELECT a.*, i.name AS institution_name, i.state, i.region FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.id=CAST(:a AS uuid)", a=aid)
    if not a or not in_scope(p, a):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such alert.")
    a["id"], a["institution_id"], a["case_id"] = str(a["id"]), str(a["institution_id"]), str(a["case_id"]) if a["case_id"] else None
    a["deviation"] = float(a["deviation"]) if a["deviation"] is not None else None
    return a


def dismiss_alert(p: Principal, aid: str, reason: str) -> dict:
    with tx(p.db_ctx()) as conn:
        a = row(conn, "SELECT a.id, a.institution_id, a.status, i.state, i.region FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.id=CAST(:a AS uuid) FOR UPDATE OF a", a=aid)
        if not a or not in_scope(p, a):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such alert.")
        if a["status"] != "OPEN":
            raise ApiError(409, "INVALID_STATE", "Not open", f"This alert is {a['status']}.")
        conn.execute(text("UPDATE intel.anomaly_alerts SET status='DISMISSED' WHERE id=:a"), {"a": a["id"]})
        audit.log(conn, "alert.dismissed", entity_type="alert", entity_id=a["id"], actor_id=p.id, actor_role=p.role, institution_id=a["institution_id"], justification=reason, request_id=p.request_id)
    return {"status": "DISMISSED"}


def case_from_alert(p: Principal, aid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        a = row(conn, "SELECT a.*, i.state, i.region FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.id=CAST(:a AS uuid) FOR UPDATE OF a", a=aid)
        if not a or not in_scope(p, a):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such alert.")
        if a["status"] != "OPEN":
            raise ApiError(409, "INVALID_STATE", "Not open", f"This alert is {a['status']}.")
        cid = open_case(conn, p, institution_id=a["institution_id"], type_="ANOMALY", title=f"{a['type'].replace('_', ' ').title()} ({a['layer']})", alert_id=a["id"],
                        evidence={"enrolments": (a["evidence"] or {}).get("sample_enrolments", []), "alert": {"type": a["type"], "layer": a["layer"], "severity": a["severity"], "baseline": a["baseline"], "evidence": a["evidence"], "explanation": a["explanation"], "model_version": a["model_version"]}})
        conn.execute(text("UPDATE intel.anomaly_alerts SET status='IN_CASE', case_id=:c WHERE id=:a"), {"c": cid, "a": a["id"]})
    return {"case_id": cid}


# ------------------------------------------------------------------ cases
def list_cases(p: Principal, status: str | None, type_: str | None, q: str | None, limit: int, offset: int) -> dict:
    where, params = ["true"], {}
    if status:
        where.append("c.status=:st"); params["st"] = status
    if type_:
        where.append("c.type=:ty"); params["ty"] = type_
    if q:
        where.append("(c.case_no ILIKE :q OR c.title ILIKE :q OR i.name ILIKE :q)"); params["q"] = f"%{q}%"
    with tx(p.db_ctx()) as conn:
        data = rows(conn, "SELECT c.id, c.case_no, c.type, c.title, c.status, c.response_due_at, c.outcome_code, c.created_at, c.closed_at, i.name AS institution_name, i.state, i.region, c.institution_id, u.username AS owner_name "
                          "FROM intel.cases c JOIN ref.institutions i ON i.id=c.institution_id LEFT JOIN iam.users u ON u.id=c.owner_id WHERE " + " AND ".join(where) + " ORDER BY c.created_at DESC LIMIT 2000", **params)
    data = [d for d in data if in_scope(p, d)]
    return {"total": len(data), "items": [{**d, "id": str(d["id"]), "institution_id": str(d["institution_id"])} for d in data[offset:offset + limit]]}


def detail(p: Principal, cid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p)
        ev = rows(conn, "SELECT e.id, e.event_type, e.body, e.created_at, u.username AS actor FROM intel.case_events e LEFT JOIN iam.users u ON u.id=e.actor_id WHERE e.case_id=:c ORDER BY e.id", c=c["id"])
        ap = rows(conn, "SELECT a.id, a.grounds, a.status, a.decided_at, a.decision_notes, a.created_at, u.username AS filed_by, d.username AS decided_by FROM intel.appeals a JOIN iam.users u ON u.id=a.filed_by LEFT JOIN iam.users d ON d.id=a.decided_by WHERE a.case_id=:c ORDER BY a.created_at", c=c["id"])
        alert = None
        if c["alert_id"] and not p.institution_id:
            alert = row(conn, "SELECT id, layer, type, severity, baseline, evidence, explanation, suggested_action, model_version FROM intel.anomaly_alerts WHERE id=:a", a=c["alert_id"])
            if alert:
                alert["id"] = str(alert["id"])
    t = now()
    appealable = c["status"] == "CLOSED" and c["closed_at"] and t - c["closed_at"] <= timedelta(days=APPEAL_DAYS)
    out = {"id": str(c["id"]), "case_no": c["case_no"], "type": c["type"], "title": c["title"], "status": c["status"], "institution_id": str(c["institution_id"]), "institution_name": c["institution_name"],
           "owner": c["owner_name"], "owner_id": str(c["owner_id"]) if c["owner_id"] else None, "response_due_at": c["response_due_at"], "response_text": c["response_text"], "responded_at": c["responded_at"],
           "outcome_code": c["outcome_code"], "outcome_notes": c["outcome_notes"], "enrolment_ref": c["enrolment_ref"], "enrolment_id": str(c["enrolment_id"]) if c["enrolment_id"] else None,
           "created_at": c["created_at"], "closed_at": c["closed_at"], "appealable": bool(appealable), "appeal_deadline": c["closed_at"] + timedelta(days=APPEAL_DAYS) if c["closed_at"] else None,
           "events": [{**e, "id": e["id"]} for e in ev], "appeals": [{**a, "id": str(a["id"])} for a in ap], "release_enrolment_id": str(c["release_enrolment_id"]) if c["release_enrolment_id"] else None,
           "overdue": c["status"] == "AWAITING_RESPONSE" and c["response_due_at"] and c["response_due_at"] < t, "statement": "Outcomes are labels for review quality. No outcome triggers an automatic penalty."}
    if not p.institution_id:
        out.update(evidence_pack=c["evidence_pack"], evidence_sha256=c["evidence_sha256"], alert=alert)
    return out


def request_response(p: Principal, cid: str, message: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if c["status"] not in ("OPEN", "UNDER_REVIEW"):
            raise ApiError(409, "INVALID_STATE", "Cannot request a response", f"The case is {c['status']}.")
        due = now() + timedelta(days=RESPONSE_DAYS)
        conn.execute(text("UPDATE intel.cases SET status='AWAITING_RESPONSE', response_due_at=:d, row_version=row_version+1 WHERE id=:c"), {"d": due, "c": c["id"]})
        _event(conn, c, p, "RESPONSE_REQUESTED", message)
        audit.log(conn, "case.response_requested", entity_type="case", entity_id=c["id"], actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], justification=message, request_id=p.request_id)
    notify.record("INAPP", "CASE_RESPONSE_REQUESTED", "SENT", institution_id=str(c["institution_id"]), kind="INSTITUTION", params={"case_no": c["case_no"], "due": due.date().isoformat()})
    return {"status": "AWAITING_RESPONSE", "response_due_at": due}


def respond(p: Principal, cid: str, response: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if c["status"] != "AWAITING_RESPONSE":
            raise ApiError(409, "INVALID_STATE", "No response requested", f"The case is {c['status']}.")
        conn.execute(text("UPDATE intel.cases SET status='UNDER_REVIEW', response_text=:r, responded_at=:t, row_version=row_version+1 WHERE id=:c"), {"r": response, "t": now(), "c": c["id"]})
        _event(conn, c, p, "RESPONSE_RECEIVED", response)
        audit.log(conn, "case.responded", entity_type="case", entity_id=c["id"], actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], request_id=p.request_id)
    return {"status": "UNDER_REVIEW"}


def start_review(p: Principal, cid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        overdue = c["response_due_at"] and c["response_due_at"] < now()
        if c["status"] == "AWAITING_RESPONSE" and not overdue:
            raise ApiError(409, "INVALID_STATE", "Still waiting", "The response window is still open.")
        if c["status"] not in ("OPEN", "AWAITING_RESPONSE"):
            raise ApiError(409, "INVALID_STATE", "Cannot start review", f"The case is {c['status']}.")
        conn.execute(text("UPDATE intel.cases SET status='UNDER_REVIEW', row_version=row_version+1 WHERE id=:c"), {"c": c["id"]})
        _event(conn, c, p, "REVIEW_STARTED", "Overdue response; proceeding to review." if overdue else None)
    return {"status": "UNDER_REVIEW"}


def close(p: Principal, cid: str, outcome: str, notes: str) -> dict:
    if outcome not in OUTCOMES:
        raise ApiError(422, "VALIDATION_ERROR", "Unknown outcome", f"Use one of {sorted(OUTCOMES)}.", errors=[{"field": "outcome_code", "code": "ENUM", "message": "Not a valid outcome.", "fix_suggestion": "Pick a listed outcome."}])
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if c["status"] != "UNDER_REVIEW":
            raise ApiError(409, "INVALID_STATE", "Not under review", f"Only a case under review can be closed (this one is {c['status']}).")
        pack = dict(c["evidence_pack"]); pack["closed"] = {"outcome": outcome, "at": now().isoformat()}
        conn.execute(text("UPDATE intel.cases SET status='CLOSED', outcome_code=:o, outcome_notes=:n, closed_at=:t, evidence_pack=CAST(:p AS jsonb), evidence_sha256=:h, row_version=row_version+1 WHERE id=:c"),
                     {"o": outcome, "n": notes, "t": now(), "p": dumps(pack), "h": _pack_hash(pack), "c": c["id"]})
        if c["alert_id"]:
            conn.execute(text("UPDATE intel.anomaly_alerts SET status='CLOSED' WHERE id=:a"), {"a": c["alert_id"]})
        if c["type"] == "STUDENT_DISPUTE" and c["enrolment_id"] and outcome in ("NOT_SUBSTANTIATED", "FALSE_POSITIVE"):
            # a disputed enrolment leaves DISPUTED only through a case outcome decided by AICTE (state machine 5.4.1)
            v = row(conn, "SELECT lock2_status FROM core.enrolment_verification WHERE enrolment_id=:e", e=c["enrolment_id"])
            conn.execute(text("UPDATE core.enrolment_verification SET lock3_status='PENDING', overall_state=:o, updated_at=:t WHERE enrolment_id=:e"),
                         {"o": verification.overall_state(v["lock2_status"], "PENDING"), "t": now(), "e": c["enrolment_id"]})
        _event(conn, c, p, "CLOSED", f"{outcome}: {notes}")
        audit.log(conn, "case.closed", entity_type="case", entity_id=c["id"], actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], justification=notes,
                  request_id=p.request_id, payload={"outcome": outcome, "label_stored": True, "automatic_penalty": False})
    notify.record("INAPP", "CASE_CLOSED", "SENT", institution_id=str(c["institution_id"]), kind="INSTITUTION", params={"case_no": c["case_no"], "outcome": outcome})
    return {"status": "CLOSED", "outcome_code": outcome, "appeal_window_days": APPEAL_DAYS}


def release_seat(p: Principal, cid: str, reason: str) -> dict:
    """REQ-GATE-09 (gate-abuse control): AICTE releases a seat tied to a case. Needs step-up; the institution is notified."""
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if not c["enrolment_id"]:
            raise ApiError(409, "INVALID_STATE", "No enrolment on this case", "Only cases linked to one enrolment can release a seat.")
    out = claims.withdraw(p, str(c["enrolment_id"]), "AICTE_RELEASE", None, reason, aicte_release=True)
    with tx(p.db_ctx()) as conn:
        conn.execute(text("UPDATE intel.cases SET release_enrolment_id=enrolment_id WHERE id=:c"), {"c": c["id"]})
        _event(conn, c, p, "SEAT_RELEASED", reason)
    notify.record("INAPP", "SEAT_RELEASED", "SENT", institution_id=str(c["institution_id"]), kind="INSTITUTION", params={"case_no": c["case_no"]})
    return out


# ------------------------------------------------------------------ appeals
def appeal(p: Principal, cid: str, grounds: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if c["status"] != "CLOSED":
            raise ApiError(409, "INVALID_STATE", "Not closed", "Only a closed case can be appealed.")
        if now() - c["closed_at"] > timedelta(days=APPEAL_DAYS):
            raise ApiError(409, "APPEAL_WINDOW_PASSED", "Appeal window passed", f"Appeals must be filed within {APPEAL_DAYS} days of closure.")
        aid = uuid7()
        conn.execute(text("INSERT INTO intel.appeals (id, case_id, institution_id, filed_by, grounds) VALUES (:id,:c,:i,CAST(:u AS uuid),:g)"), {"id": aid, "c": c["id"], "i": c["institution_id"], "u": p.id, "g": grounds})
        conn.execute(text("UPDATE intel.cases SET status='APPEALED', row_version=row_version+1 WHERE id=:c"), {"c": c["id"]})
        _event(conn, c, p, "APPEAL_FILED", grounds)
        audit.log(conn, "appeal.filed", entity_type="appeal", entity_id=aid, actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], justification=grounds, request_id=p.request_id)
    return {"status": "APPEALED", "appeal_id": str(aid)}


def decide_appeal(p: Principal, cid: str, decision: str, notes: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, cid, p, lock=True)
        if c["status"] != "APPEALED":
            raise ApiError(409, "INVALID_STATE", "No appeal pending", f"The case is {c['status']}.")
        if str(c["owner_id"]) == p.id:
            raise ApiError(403, "SEPARATION_OF_DUTIES", "A different reviewer must decide", "The case owner cannot decide the appeal.")
        a = row(conn, "SELECT id FROM intel.appeals WHERE case_id=:c AND status='FILED' ORDER BY created_at DESC LIMIT 1", c=c["id"])
        conn.execute(text("UPDATE intel.appeals SET status=:s, decided_by=CAST(:u AS uuid), decided_at=:t, decision_notes=:n WHERE id=:a"), {"s": decision, "u": p.id, "t": now(), "n": notes, "a": a["id"]})
        label = "NOT_SUBSTANTIATED" if decision == "OVERTURNED" else c["outcome_code"]
        conn.execute(text("UPDATE intel.cases SET status='CLOSED', outcome_code=:o, closed_at=:t, row_version=row_version+1 WHERE id=:c"), {"o": label, "t": now(), "c": c["id"]})
        _event(conn, c, p, "APPEAL_DECIDED", f"{decision}: {notes}")
        audit.log(conn, "appeal.decided", entity_type="appeal", entity_id=a["id"], actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], justification=notes,
                  request_id=p.request_id, payload={"decision": decision, "label_updated": label})
    return {"status": "CLOSED", "decision": decision, "outcome_code": label}


# ------------------------------------------------------------------ elevation grants (case-gated student-level access)
def request_elevation(p: Principal, case_id: str, justification: str, minutes: int) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, case_id, p)
        t = now()
        gid = uuid7()
        conn.execute(text("INSERT INTO pii.access_grants (id, user_id, case_id, justification, scope_type, granted_at, expires_at) VALUES (:id,CAST(:u AS uuid),:c,:j,'CASE',:g,:e)"),
                     {"id": gid, "u": p.id, "c": c["id"], "j": justification, "g": t, "e": t + timedelta(minutes=minutes)})
        audit.log(conn, "elevation.granted", entity_type="grant", entity_id=gid, actor_id=p.id, actor_role=p.role, institution_id=c["institution_id"], case_id=c["id"], justification=justification,
                  request_id=p.request_id, payload={"minutes": minutes})
    return {"grant_id": str(gid), "case_no": c["case_no"], "expires_at": t + timedelta(minutes=minutes)}


def my_grants(p: Principal) -> dict:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT g.id, g.expires_at, g.records_revealed, g.justification, c.case_no, c.id AS case_id FROM pii.access_grants g JOIN intel.cases c ON c.id=g.case_id "
                       "WHERE g.user_id=CAST(:u AS uuid) AND g.expires_at > :t AND g.revoked_at IS NULL ORDER BY g.expires_at DESC", u=p.id, t=now())
    return {"items": [{**x, "id": str(x["id"]), "case_id": str(x["case_id"])} for x in d]}


def _grant(conn, p: Principal, case_id: str | None = None, institutions: tuple | None = None) -> dict:
    sql = ("SELECT g.id, g.case_id, g.expires_at, g.justification, c.case_no, c.institution_id FROM pii.access_grants g JOIN intel.cases c ON c.id=g.case_id WHERE g.user_id=CAST(:u AS uuid) "
           "AND g.expires_at > :t AND g.revoked_at IS NULL")
    params: dict = {"u": p.id, "t": now()}
    if case_id:
        sql += " AND g.case_id=CAST(:c AS uuid)"; params["c"] = case_id
    g = [x for x in rows(conn, sql + " ORDER BY g.expires_at DESC", **params) if not institutions or str(x["institution_id"]) in institutions]
    if not g:
        raise ApiError(403, "ELEVATION_REQUIRED", "A case-linked elevation grant is required", "Request a time-limited elevation for the relevant case, with a justification of at least 30 characters.")
    return g[0]


def _reveal_enrolments(conn, p: Principal, grant: dict, eids: list[str]) -> list[dict]:
    out = []
    for eid in eids:
        e = row(conn, "SELECT e.id, e.enrolment_ref, e.student_id, e.institution_id, e.status, e.academic_year, i.name AS institution_name, pr.name AS programme_name FROM core.enrolments e "
                      "JOIN ref.institutions i ON i.id=e.institution_id JOIN ref.programmes pr ON pr.id=e.programme_id WHERE e.id=CAST(:e AS uuid)", e=eid)
        if not e or not in_scope(p, {"region": scalar(conn, "SELECT region FROM ref.institutions WHERE id=:i", i=e["institution_id"]) if e else "", "state": scalar(conn, "SELECT state FROM ref.institutions WHERE id=:i", i=e["institution_id"]) if e else ""}):
            continue
        pii = verification._vault(conn, str(e["student_id"]), str(e["institution_id"]))
        out.append({"enrolment_ref": e["enrolment_ref"], "institution": e["institution_name"], "programme": e["programme_name"], "year": e["academic_year"], "status": e["status"],
                    "name": pii.get("name"), "dob": pii.get("dob"), "parent_name": pii.get("parent_name"), "district": pii.get("district"),
                    "mobile_masked": identity.mask_mobile(pii["mobile"]) if pii.get("mobile") else None})
        audit.log(conn, "access.student_revealed", entity_type="enrolment", entity_id=e["id"], actor_id=p.id, actor_role=p.role, institution_id=e["institution_id"], case_id=grant["case_id"],
                  justification=grant["justification"], request_id=p.request_id)
    conn.execute(text("UPDATE pii.access_grants SET records_revealed = records_revealed + :n WHERE id=:g"), {"n": len(out), "g": grant["id"]})
    last_hour = scalar(conn, "SELECT count(*) FROM audit.audit_log WHERE action='access.student_revealed' AND actor_id=CAST(:u AS uuid) AND occurred_at > :t", u=p.id, t=now() - timedelta(hours=1))
    if last_hour > VOLUME_LIMIT:
        audit.log(conn, "access.volume_alert", entity_type="user", entity_id=p.id, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"revealed_last_hour": last_hour})
    return out


def reveal_case(p: Principal, case_id: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = _load(conn, case_id, p)
        g = _grant(conn, p, case_id=case_id)
        eids = [str(c["enrolment_id"])] if c["enrolment_id"] else []
        refs = (c["evidence_pack"] or {}).get("enrolments") or []
        for r in refs:
            e = scalar(conn, "SELECT id FROM core.enrolments WHERE enrolment_ref=:r", r=r)
            if e and str(e) not in eids:
                eids.append(str(e))
        people = _reveal_enrolments(conn, p, g, eids)
    return {"grant_expires_at": g["expires_at"], "case_no": g["case_no"], "people": people, "notice": "Every reveal is audited with the case and your justification, and is visible to the student in their access log."}


def reveal_duplicate(p: Principal, cand_id: str) -> dict:
    with tx(p.db_ctx()) as conn:
        c = row(conn, "SELECT enrolment_a, enrolment_b, institution_a, institution_b FROM matching.match_candidates WHERE id=CAST(:c AS uuid)", c=cand_id)
        if not c:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such candidate.")
        g = _grant(conn, p, institutions=(str(c["institution_a"]), str(c["institution_b"])))
        people = _reveal_enrolments(conn, p, g, [str(c["enrolment_a"]), str(c["enrolment_b"])])
    return {"grant_expires_at": g["expires_at"], "case_no": g["case_no"], "people": people}
