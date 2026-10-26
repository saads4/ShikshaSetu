"""Rule-set drafting, publishing, rollback and the policy simulator (REQ-RUL-07, REQ-RUL-09)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import text

from . import audit, ops, rules_engine
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, owner_tx, row, rows, scalar, tx
from .deps import Principal

KINDS = {"REQUIRED_FIELD", "REGEX_FORMAT", "COMPARE", "IMPLIES", "MUTUALLY_EXCLUSIVE", "DATE_WITHIN", "LOOKUP_IN_REFERENCE", "BUCKET_CAP", "SUM_RECONCILES"}
SEVERITIES = {"Blocker", "Error", "Warning", "Info"}
MINUTES_PER_FINDING = 6
# Inputs that exist in the non-PII enrolment snapshot (the simulator never decrypts anything).
SNAPSHOT_FIELDS = {"gender", "category", "citizenship_type", "nationality_code", "bucket_type", "programme_level", "programme_mode", "lateral_entry", "age_years", "admission_date", "today", "window_opens"}


def list_sets(conn) -> list[dict]:
    d = rows(conn, "SELECT s.id, s.name, s.version_no, s.status, s.published_at, s.retroactive, s.notes, du.username AS drafted_by, pu.username AS published_by, "
                   "(SELECT count(*) FROM rules.rules r WHERE r.rule_set_id=s.id) AS rule_count FROM rules.rule_sets s LEFT JOIN iam.users du ON du.id=s.drafted_by LEFT JOIN iam.users pu ON pu.id=s.published_by ORDER BY s.version_no DESC")
    return [{**x, "id": str(x["id"])} for x in d]


def get_set(conn, sid: str) -> dict:
    s = row(conn, "SELECT s.id, s.name, s.version_no, s.status, s.retroactive, s.notes, s.drafted_by, s.published_by, s.published_at FROM rules.rule_sets s WHERE s.id=CAST(:s AS uuid)", s=sid)
    if not s:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such rule set.")
    rl = rows(conn, "SELECT rule_code, category, kind, params, severity, sites, message_template, fix_template FROM rules.rules WHERE rule_set_id=:s ORDER BY rule_code", s=s["id"])
    return {**s, "id": str(s["id"]), "drafted_by": str(s["drafted_by"]) if s["drafted_by"] else None, "published_by": str(s["published_by"]) if s["published_by"] else None, "rules": rl}


def create_draft(p: Principal, notes: str | None, retroactive: bool) -> dict:
    with tx(p.db_ctx()) as conn:
        act = row(conn, "SELECT id, version_no FROM rules.rule_sets WHERE status='ACTIVE'")
        if scalar(conn, "SELECT count(*) FROM rules.rule_sets WHERE status='DRAFT' AND drafted_by=CAST(:u AS uuid)", u=p.id) >= 3:
            raise ApiError(409, "TOO_MANY_DRAFTS", "Too many drafts", "Publish or retire an existing draft first.")
        v = scalar(conn, "SELECT max(version_no) FROM rules.rule_sets") + 1
        nid = uuid7()
        conn.execute(text("INSERT INTO rules.rule_sets (id, name, version_no, status, drafted_by, retroactive, notes) VALUES (:id,:n,:v,'DRAFT',CAST(:u AS uuid),:r,:no)"),
                     {"id": nid, "n": f"Admission rules v{v} (draft)", "v": v, "u": p.id, "r": retroactive, "no": notes})
        conn.execute(text("INSERT INTO rules.rules (id, rule_set_id, rule_code, category, kind, params, severity, sites, regulatory_ref, owner, message_template, fix_template) "
                          "SELECT gen_random_uuid(), :n, rule_code, category, kind, params, severity, sites, regulatory_ref, owner, message_template, fix_template FROM rules.rules WHERE rule_set_id=:a"), {"n": nid, "a": act["id"]})
        audit.log(conn, "ruleset.drafted", entity_type="rule_set", entity_id=nid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"version": v, "cloned_from": act["version_no"]})
    return {"id": str(nid), "version_no": v, "status": "DRAFT"}


def update_rule(p: Principal, sid: str, code: str, params: dict | None, severity: str | None, message: str | None, enabled_sites: list[str] | None) -> dict:
    with tx(p.db_ctx()) as conn:
        s = row(conn, "SELECT id, status, drafted_by FROM rules.rule_sets WHERE id=CAST(:s AS uuid) FOR UPDATE", s=sid)
        if not s:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such rule set.")
        if s["status"] != "DRAFT":
            raise ApiError(409, "IMMUTABLE", "Published rule sets are immutable", "Create a new draft to change a rule.")
        r = row(conn, "SELECT id FROM rules.rules WHERE rule_set_id=:s AND rule_code=:c", s=s["id"], c=code)
        if not r:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such rule in this set.")
        if severity and severity not in SEVERITIES:
            raise ApiError(422, "VALIDATION_ERROR", "Unknown severity", f"Use one of {sorted(SEVERITIES)}.")
        conn.execute(text("UPDATE rules.rules SET params=coalesce(CAST(:p AS jsonb), params), severity=coalesce(:sv, severity), message_template=coalesce(:m, message_template), sites=coalesce(CAST(:st AS text[]), sites) WHERE id=:i"),
                     {"p": dumps(params) if params is not None else None, "sv": severity, "m": message, "st": "{" + ",".join(enabled_sites) + "}" if enabled_sites else None, "i": r["id"]})
        audit.log(conn, "ruleset.rule_updated", entity_type="rule_set", entity_id=s["id"], actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"rule": code, "severity": severity})
    return {"updated": code}


def publish(p: Principal, sid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        s = row(conn, "SELECT id, status, drafted_by, version_no FROM rules.rule_sets WHERE id=CAST(:s AS uuid) FOR UPDATE", s=sid)
        if not s:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such rule set.")
        if s["status"] != "DRAFT":
            raise ApiError(409, "INVALID_STATE", "Not a draft", f"Only a DRAFT can be published (this one is {s['status']}).")
        if str(s["drafted_by"]) == p.id:
            raise ApiError(403, "SEPARATION_OF_DUTIES", "A different reviewer must publish", "The person who drafted a rule set cannot publish it.")
        conn.execute(text("UPDATE rules.rule_sets SET status='RETIRED' WHERE status='ACTIVE'"))
        conn.execute(text("UPDATE rules.rule_sets SET status='ACTIVE', published_by=CAST(:u AS uuid), published_at=:t, effective_from=:t WHERE id=:s"), {"u": p.id, "t": now(), "s": s["id"]})
        audit.log(conn, "ruleset.published", entity_type="rule_set", entity_id=s["id"], actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"version": s["version_no"]})
    rules_engine.invalidate_cache()
    return {"status": "ACTIVE", "version_no": s["version_no"], "note": "Existing decisions keep the rule version that evaluated them unless the set was marked retroactive."}


def rollback(p: Principal, sid: str) -> dict:
    """Re-activate a RETIRED rule set (a new activation event). The currently ACTIVE set is retired."""
    with tx(p.db_ctx()) as conn:
        s = row(conn, "SELECT id, status, version_no FROM rules.rule_sets WHERE id=CAST(:s AS uuid) FOR UPDATE", s=sid)
        if not s or s["status"] != "RETIRED":
            raise ApiError(409, "INVALID_STATE", "Not a retired rule set", "Rollback re-activates a RETIRED rule set.")
        conn.execute(text("UPDATE rules.rule_sets SET status='RETIRED' WHERE status='ACTIVE'"))
        conn.execute(text("UPDATE rules.rule_sets SET status='ACTIVE', published_at=:t WHERE id=:s"), {"t": now(), "s": s["id"]})
        audit.log(conn, "ruleset.rolled_back", entity_type="rule_set", entity_id=s["id"], actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"version": s["version_no"]})
    rules_engine.invalidate_cache()
    return {"status": "ACTIVE", "version_no": s["version_no"]}


# ------------------------------------------------------------------ simulator (read-only replay, async)
def request_simulation(p: Principal, draft_id: str, cycles: list[str]) -> dict:
    with tx(p.db_ctx()) as conn:
        d = row(conn, "SELECT id, status FROM rules.rule_sets WHERE id=CAST(:d AS uuid)", d=draft_id)
        if not d or d["status"] != "DRAFT":
            raise ApiError(404, "NOT_FOUND", "Not found", "Simulations run on a DRAFT rule set.")
        base = scalar(conn, "SELECT id FROM rules.rule_sets WHERE status='ACTIVE'")
        sid = uuid7()
        conn.execute(text("INSERT INTO rules.simulation_runs (id, draft_rule_set_id, baseline_rule_set_id, cycles, requested_by) VALUES (:id,:d,:b,:c,CAST(:u AS uuid))"), {"id": sid, "d": d["id"], "b": base, "c": cycles, "u": p.id})
        audit.log(conn, "simulation.requested", entity_type="simulation", entity_id=sid, actor_id=p.id, actor_role=p.role, request_id=p.request_id, payload={"cycles": cycles})
    ops.enqueue("intel", "rules.simulate", {"run_id": str(sid)}, dedupe_key=f"sim:{sid}")
    return {"id": str(sid), "status": "QUEUED"}


def get_simulation(sid: str) -> dict:
    with tx(SYSTEM) as conn:
        r = row(conn, "SELECT r.id, r.status, r.cycles, r.result, r.skipped_rules, r.error, r.started_at, r.finished_at, d.version_no AS draft_version, b.version_no AS baseline_version "
                      "FROM rules.simulation_runs r JOIN rules.rule_sets d ON d.id=r.draft_rule_set_id JOIN rules.rule_sets b ON b.id=r.baseline_rule_set_id WHERE r.id=CAST(:s AS uuid)", s=sid)
    if not r:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such simulation.")
    return {**r, "id": str(r["id"])}


def list_simulations(draft_id: str | None) -> list[dict]:
    with tx(SYSTEM) as conn:
        d = rows(conn, "SELECT r.id, r.status, r.cycles, r.started_at, r.finished_at, d.version_no AS draft_version FROM rules.simulation_runs r JOIN rules.rule_sets d ON d.id=r.draft_rule_set_id "
                       + ("WHERE r.draft_rule_set_id=CAST(:d AS uuid) " if draft_id else "") + "ORDER BY r.started_at DESC LIMIT 30", **({"d": draft_id} if draft_id else {}))
    return [{**x, "id": str(x["id"])} for x in d]


def _simulable(rule: dict) -> bool:
    p = rule["params"]
    fields = set()
    def walk(x):
        if isinstance(x, dict):
            if "field" in x:
                fields.add(x["field"])
            for v in x.values():
                walk(v)
    walk(p)
    return fields <= SNAPSHOT_FIELDS and rule["kind"] not in ("SUM_RECONCILES", "BUCKET_CAP")


@ops.handler("rules.simulate")
def simulate(payload: dict) -> dict:
    rid = payload["run_id"]
    with owner_tx() as conn:
        r = row(conn, "SELECT * FROM rules.simulation_runs WHERE id=CAST(:r AS uuid)", r=rid)
        if not r or r["status"] not in ("QUEUED", "FAILED"):
            return {"skipped": True}
        conn.execute(text("UPDATE rules.simulation_runs SET status='RUNNING' WHERE id=:r"), {"r": r["id"]})
    try:
        with owner_tx() as conn:
            draft = [dict(x) for x in rows(conn, "SELECT * FROM rules.rules WHERE rule_set_id=:s", s=r["draft_rule_set_id"])]
            base = [dict(x) for x in rows(conn, "SELECT * FROM rules.rules WHERE rule_set_id=:s", s=r["baseline_rule_set_id"])]
            snaps = rows(conn, "SELECT e.id, e.institution_id, e.academic_year, e.gender, e.category, e.citizenship_type, e.nationality_code, e.bucket_type, e.birth_year, e.claimed_at, e.admission_date, "
                               "pr.level, pr.mode, pr.lateral_entry, pr.programme_code, i.name AS institution_name FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id "
                               "JOIN ref.institutions i ON i.id=e.institution_id WHERE e.academic_year = ANY(:y)", y=list(r["cycles"]))
        skipped = sorted({x["rule_code"] for x in draft + base if not _simulable(x)})
        sim_d = [x for x in draft if _simulable(x)]
        sim_b = [x for x in base if _simulable(x)]
        new_f, by_sev, by_inst, by_prog, gone = Counter(), Counter(), defaultdict(Counter), Counter(), 0
        for s in snaps:
            ref = s["claimed_at"].date()
            rec = {"gender": s["gender"], "category": s["category"], "citizenship_type": s["citizenship_type"], "nationality_code": s["nationality_code"], "bucket_type": s["bucket_type"],
                   "programme_level": s["level"], "programme_mode": s["mode"], "lateral_entry": s["lateral_entry"], "age_years": ref.year - s["birth_year"] if s["birth_year"] else None,   # age approximated from birth year
                   "admission_date": s["admission_date"], "today": ref, "window_opens": None}
            b = {(f["rule_code"], f["severity"]) for f in rules_engine.evaluate(sim_b, rec, "C")}
            d = {(f["rule_code"], f["severity"]) for f in rules_engine.evaluate(sim_d, rec, "C")}
            for code, sev in d - b:
                new_f[code] += 1; by_sev[sev] += 1; by_inst[s["institution_name"]][sev] += 1; by_prog[(s["programme_code"], s["level"])] += 1
            gone += len(b - d)
        flagged = [{"institution": i, "new_findings": sum(c.values()), "blocking": c["Blocker"] + c["Error"]} for i, c in by_inst.items()]
        flagged.sort(key=lambda x: -x["new_findings"])
        result = {"enrolments_replayed": len(snaps), "new_findings_total": sum(new_f.values()), "findings_no_longer_raised": gone, "by_severity": dict(by_sev), "by_rule": dict(new_f),
                  "institutions_that_would_be_flagged": sum(1 for f in flagged if f["blocking"] > 0), "institutions": flagged[:25], "programmes_affected": len(by_prog),
                  "reviewer_workload_minutes": sum(new_f.values()) * MINUTES_PER_FINDING, "minutes_per_finding": MINUTES_PER_FINDING,
                  "notes": ["Read-only replay over the non-PII enrolment snapshot; no decision was changed.", "Age is approximated as claim year minus birth year.", "Rules whose inputs are not in the snapshot are listed as not simulated."]}
        with owner_tx() as conn:
            conn.execute(text("UPDATE rules.simulation_runs SET status='DONE', result=CAST(:r AS jsonb), skipped_rules=:s, finished_at=:t WHERE id=:i"), {"r": dumps(result), "s": skipped, "t": now(), "i": r["id"]})
        return {"replayed": len(snaps)}
    except Exception as exc:  # noqa: BLE001
        with owner_tx() as conn:
            conn.execute(text("UPDATE rules.simulation_runs SET status='FAILED', error=:e, finished_at=:t WHERE id=CAST(:i AS uuid)"), {"e": f"{type(exc).__name__}: {exc}"[:300], "t": now(), "i": rid})
        raise
