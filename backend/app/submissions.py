"""Submission lifecycle, readiness checks, window extensions and Merkle cohort commitments (REQ-SUB, REQ-AUD-05/06)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import text

from . import audit, crypto, keys, rules_engine
from .claims import _enqueue_outbox, current_academic_year
from .core import ApiError, canonical_json, dumps, now, uuid7
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal

SERIALISATION_VERSION = 1


def _hist(conn, sub: dict, to: str, p: Principal | None, note: str | None = None) -> None:
    conn.execute(text("INSERT INTO ingest.submission_history (submission_id, institution_id, from_status, to_status, actor_id, note) "
                      "VALUES (:s, :i, :f, :t, CAST(:a AS uuid), :n)"),
                 {"s": sub["id"], "i": sub["institution_id"], "f": sub["status"], "t": to, "a": p.id if p else None, "n": note})


def window_for(conn, year: str) -> dict:
    w = row(conn, "SELECT id, academic_year, label, opens_at, closes_at, cutoff_at FROM ref.submission_windows WHERE academic_year=:y ORDER BY opens_at DESC LIMIT 1", y=year)
    if not w:
        raise ApiError(404, "NOT_FOUND", "No submission window", f"No window is configured for {year}.")
    return w


def effective_close(conn, institution_id: str, window: dict) -> datetime:
    ext = scalar(conn, "SELECT max(new_closes_at) FROM ref.window_extensions WHERE institution_id=CAST(:i AS uuid) AND window_id=:w AND status='APPROVED'",
                 i=institution_id, w=window["id"])
    return max(window["closes_at"], ext) if ext else window["closes_at"]


def get_submission(conn, sid: str, p: Principal) -> dict:
    s = row(conn, "SELECT s.*, i.name AS institution_name, i.state, i.region FROM ingest.submissions s JOIN ref.institutions i ON i.id=s.institution_id WHERE s.id=CAST(:s AS uuid)", s=sid)
    if not s:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such submission.")
    if p.institution_id and str(s["institution_id"]) != p.institution_id:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such submission.")
    if not p.institution_id and not _in_scope(p, s):
        raise ApiError(404, "NOT_FOUND", "Not found", "No such submission.")
    return s


def _in_scope(p: Principal, inst: dict) -> bool:
    if p.scope_type == "NATIONAL":
        return True
    return (inst["region"] == p.scope_value) if p.scope_type == "REGION" else (inst["state"] == p.scope_value) if p.scope_type == "STATE" else False


def create(p: Principal, year: str | None) -> dict:
    year = year or current_academic_year()
    with tx(p.db_ctx()) as conn:
        w = window_for(conn, year)
        ex = row(conn, "SELECT id FROM ingest.submissions WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y AND window_id=:w", i=p.institution_id, y=year, w=w["id"])
        if ex:
            raise ApiError(409, "INVALID_STATE", "Already exists", "A submission for this window already exists.", extra={"submission_id": str(ex["id"])})
        sid = uuid7()
        conn.execute(text("INSERT INTO ingest.submissions (id, institution_id, academic_year, window_id, created_by, declared) VALUES (:id, CAST(:i AS uuid), :y, :w, CAST(:u AS uuid), '{}')"),
                     {"id": sid, "i": p.institution_id, "y": year, "w": w["id"], "u": p.id})
        _hist(conn, {"id": sid, "institution_id": p.institution_id, "status": None}, "DRAFT", p)
        audit.log(conn, "submission.created", entity_type="submission", entity_id=sid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"id": str(sid), "status": "DRAFT"}


def set_declared(p: Principal, sid: str, declared: dict[str, int]) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] not in ("DRAFT", "CORRECTED", "VALIDATED"):
            raise ApiError(409, "INVALID_STATE", "Cannot edit", f"Declared totals cannot change while the submission is {s['status']}.")
        codes = {r["programme_code"] for r in rows(conn, "SELECT programme_code FROM ref.programmes WHERE institution_id=CAST(:i AS uuid)", i=p.institution_id)}
        bad = [c for c in declared if c not in codes]
        if bad:
            raise ApiError(422, "VALIDATION_ERROR", "Unknown programme", "Declared totals name an unknown programme.",
                           errors=[{"field": c, "code": "UNKNOWN_PROGRAMME", "message": "Not one of your programmes.", "fix_suggestion": "Remove it."} for c in bad])
        conn.execute(text("UPDATE ingest.submissions SET declared=CAST(:d AS jsonb), status=CASE WHEN status='VALIDATED' THEN 'DRAFT' ELSE status END, row_version=row_version+1 WHERE id=:s"),
                     {"d": dumps(declared), "s": s["id"]})
        audit.log(conn, "submission.declared", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"declared": declared}


def readiness(conn, s: dict) -> dict:
    """Submission-site rules (SUM_RECONCILES, REQ-RUL-08): declared == live rows per programme; total within approved intake."""
    inst, year = str(s["institution_id"]), s["academic_year"]
    declared = s["declared"] or {}
    progs = rows(conn, "SELECT pr.programme_code, pr.name, coalesce(sum(b.occupied),0)::int AS live, coalesce(sum(b.cap + b.approved_extra),0)::int AS approved "
                       "FROM ref.programmes pr LEFT JOIN core.seat_buckets b ON b.programme_id=pr.id AND b.academic_year=:y "
                       "WHERE pr.institution_id=CAST(:i AS uuid) AND pr.status='ACTIVE' GROUP BY pr.programme_code, pr.name ORDER BY 1", i=inst, y=year)
    findings, table = [], []
    for pg in progs:
        d = declared.get(pg["programme_code"])
        ok = d is not None and int(d) == pg["live"]
        table.append({**pg, "declared": d, "ok": ok})
        if pg["live"] > pg["approved"]:
            findings.append(("AGG-002", "Blocker", f"{pg['name']}: {pg['live']} occupied exceeds {pg['approved']} approved seats.", {"programme_code": pg["programme_code"]}))
        if not ok and (pg["live"] > 0 or d):
            findings.append(("AGG-001", "Error", f"{pg['name']}: declared {d if d is not None else 'nothing'} but {pg['live']} students are on record.",
                             {"programme_code": pg["programme_code"], "declared": d, "rows": pg["live"]}))
    return {"programmes": table, "agg_findings": findings, "total_live": sum(x["live"] for x in progs), "total_approved": sum(x["approved"] for x in progs)}


def validate(p: Principal, sid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] not in ("DRAFT", "CORRECTED", "VALIDATED"):
            raise ApiError(409, "INVALID_STATE", "Cannot validate", f"A {s['status']} submission cannot be re-validated.")
        rs = rules_engine.active_rule_set(conn, fresh=True)
        rules = {r["rule_code"]: r for r in rs["rules"] if r["kind"] == "SUM_RECONCILES"}
        rep = readiness(conn, s)
        conn.execute(text("UPDATE rules.findings SET status='RESOLVED', resolved_at=:t WHERE submission_id=:s AND status='OPEN'"), {"t": now(), "s": s["id"]})
        for code, sev, msg, ev in rep["agg_findings"]:
            r = rules.get(code)
            conn.execute(text("INSERT INTO rules.findings (id, institution_id, academic_year, rule_id, rule_code, rule_set_id, submission_id, severity, message, evidence, suggested_fix) "
                              "VALUES (:id, CAST(:i AS uuid), :y, CAST(:r AS uuid), :c, CAST(:rs AS uuid), :s, :sev, :m, CAST(:ev AS jsonb), CAST(:fx AS jsonb))"),
                         {"id": uuid7(), "i": str(s["institution_id"]), "y": s["academic_year"], "r": str(r["id"]) if r else None, "c": code, "rs": rs["id"], "s": s["id"],
                          "sev": (r["severity"] if r else sev), "m": msg, "ev": dumps(ev), "fx": dumps({"fix": "Correct the declared total or the student records, then validate again."})})
        open_f = rows(conn, "SELECT id, rule_code, severity, message, enrolment_id FROM rules.findings WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y AND status='OPEN' "
                            "AND severity IN ('Blocker','Error') ORDER BY severity, created_at LIMIT 200", i=str(s["institution_id"]), y=s["academic_year"])
        warn_open = scalar(conn, "SELECT count(*) FROM rules.findings WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y AND status='OPEN' AND severity='Warning'",
                           i=str(s["institution_id"]), y=s["academic_year"])
        ok = not open_f
        new_status = "VALIDATED" if ok else s["status"]
        if ok and s["status"] != "VALIDATED":
            _hist(conn, s, "VALIDATED", p)
        conn.execute(text("UPDATE ingest.submissions SET status=:st, rule_set_id=CAST(:rs AS uuid) WHERE id=:s"), {"st": new_status, "rs": rs["id"], "s": s["id"]})
        audit.log(conn, "submission.validated", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=s["institution_id"],
                  request_id=p.request_id, payload={"ok": ok, "blocking": len(open_f), "warnings_open": warn_open})
    return {"ok": ok, "status": new_status, "blocking_findings": [{**f, "id": str(f["id"]), "enrolment_id": str(f["enrolment_id"]) if f["enrolment_id"] else None} for f in open_f],
            "warnings_open": warn_open, "programmes": rep["programmes"], "total_live": rep["total_live"], "total_approved": rep["total_approved"]}


def submit(p: Principal, sid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] != "VALIDATED":
            raise ApiError(409, "INVALID_STATE", "Validate first", f"Only a VALIDATED submission can be submitted (this one is {s['status']}).")
        warn = scalar(conn, "SELECT count(*) FROM rules.findings WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y AND status='OPEN' AND severity='Warning'",
                      i=p.institution_id, y=s["academic_year"])
        if warn:
            raise ApiError(409, "WARNINGS_NOT_ACKNOWLEDGED", "Acknowledge warnings", f"{warn} warnings are still open. Acknowledge them before submitting.", extra={"warnings_open": warn})
        w = window_for(conn, s["academic_year"])
        t = now()
        close = effective_close(conn, p.institution_id, w)
        if t < w["opens_at"]:
            raise ApiError(409, "WINDOW_NOT_OPEN", "Window not open", "The submission window has not opened yet.")
        if t > close:
            raise ApiError(409, "WINDOW_CLOSED", "Window closed", "The window has closed. Ask AICTE for an extension.", extra={"closed_at": close.isoformat()})
        late = t > w["closes_at"]   # inside an approved extension
        _hist(conn, s, "SUBMITTED", p)
        conn.execute(text("UPDATE ingest.submissions SET status='SUBMITTED', submitted_by=CAST(:u AS uuid), submitted_at=:t, late_flag=:l, row_version=row_version+1 WHERE id=:s"),
                     {"u": p.id, "t": t, "l": late, "s": s["id"]})
        audit.log(conn, "submission.submitted", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=p.institution_id,
                  request_id=p.request_id, payload={"late_flag": late})
        _enqueue_outbox(conn, "submission.submitted", "submission", s["id"], p.institution_id, {"academic_year": s["academic_year"]})
    return {"status": "SUBMITTED", "late_flag": late}


def to_corrected(p: Principal, sid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] != "UNDER_REVIEW":
            raise ApiError(409, "INVALID_STATE", "Not under review", "Only a submission under review can be marked corrected.")
        _hist(conn, s, "CORRECTED", p)
        conn.execute(text("UPDATE ingest.submissions SET status='CORRECTED', row_version=row_version+1 WHERE id=:s"), {"s": s["id"]})
        audit.log(conn, "submission.corrected", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"status": "CORRECTED"}


def decide(p: Principal, sid: str, decision: str, notes: str | None) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] != "SUBMITTED":
            raise ApiError(409, "INVALID_STATE", "Not submitted", f"Only a SUBMITTED submission can be decided (this one is {s['status']}).")
        if decision == "REQUEST_REVIEW":
            if not notes or len(notes) < 5:
                raise ApiError(422, "VALIDATION_ERROR", "Notes required", "Say what the institution must correct.", errors=[{"field": "notes", "code": "REQUIRED", "message": "Notes are mandatory.", "fix_suggestion": "Describe the issue."}])
            new = "UNDER_REVIEW"
        else:
            new = "ACCEPTED"
        _hist(conn, s, new, p, notes)
        conn.execute(text("UPDATE ingest.submissions SET status=:n, approved_by=CAST(:u AS uuid), approved_at=:t, review_notes=:r, row_version=row_version+1 WHERE id=:s"),
                     {"n": new, "u": p.id, "t": now(), "r": notes, "s": s["id"]})
        audit.log(conn, "submission.decided", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, institution_id=s["institution_id"],
                  justification=notes, request_id=p.request_id, payload={"decision": decision})
    return {"status": new}


# ------------------------------------------------------------------ Merkle cohort commitments
def _leaves(conn, institution_id: str, year: str) -> list[dict]:
    data = rows(conn, "SELECT e.id, pr.programme_code, e.bucket_type, e.status, e.admission_date, v.overall_state, e.identity_key, e.rule_set_id "
                      "FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id JOIN core.enrolment_verification v ON v.enrolment_id=e.id "
                      "WHERE e.institution_id=CAST(:i AS uuid) AND e.academic_year=:y ORDER BY e.id", i=institution_id, y=year)
    return [{"enrolment_id": str(d["id"]), "programme_code": d["programme_code"], "bucket_type": d["bucket_type"], "status": d["status"],
             "admission_date": d["admission_date"].isoformat() if d["admission_date"] else None, "verification_state": d["overall_state"],
             "identity_key_hash": bytes(d["identity_key"]).hex(), "rule_set_id": str(d["rule_set_id"]) if d["rule_set_id"] else None} for d in data]


def leaf_hash(leaf: dict) -> bytes:
    return crypto.merkle_leaf(canonical_json(leaf))


def _sign_message(root_hex: str, inst: str, year: str, cutoff, version: int, count: int) -> bytes:
    return hashlib.sha256(canonical_json({"root": root_hex, "institution_id": inst, "academic_year": year, "cutoff_at": cutoff.isoformat() if hasattr(cutoff, "isoformat") else cutoff,
                                          "version": version, "leaf_count": count, "serialisation_version": SERIALISATION_VERSION})).digest()


def sign_commitment(p: Principal, sid: str) -> dict:
    inst = p.institution_id
    kid, _, pem = keys.signing_key("INSTITUTION_CLAIM", inst)
    with tx(p.db_ctx(), statement_timeout_ms=20000) as conn:
        s = get_submission(conn, sid, p)
        if s["status"] != "ACCEPTED":
            raise ApiError(409, "INVALID_STATE", "Not accepted", "AICTE must accept the submission before the cut-off commitment is signed.")
        w = window_for(conn, s["academic_year"])
        leaves = _leaves(conn, inst, s["academic_year"])
        hashes = [leaf_hash(l) for l in leaves]
        root = crypto.merkle_root(hashes).hex()
        version = (scalar(conn, "SELECT coalesce(max(version),0) FROM audit.cohort_commitments WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y", i=inst, y=s["academic_year"]) or 0) + 1
        sig = crypto.sign_es256(pem, _sign_message(root, inst, s["academic_year"], w["cutoff_at"], version, len(leaves)))
        cid = uuid7()
        conn.execute(text("INSERT INTO audit.cohort_commitments (id, institution_id, academic_year, submission_id, cutoff_at, version, merkle_root, leaf_count, serialisation_version, "
                          "institution_signature, institution_kid, created_by) VALUES (:id, CAST(:i AS uuid), :y, :s, :c, :v, :r, :n, :sv, :sig, :kid, CAST(:u AS uuid))"),
                     {"id": cid, "i": inst, "y": s["academic_year"], "s": s["id"], "c": w["cutoff_at"], "v": version, "r": root, "n": len(leaves), "sv": SERIALISATION_VERSION, "sig": sig, "kid": kid, "u": p.id})
        conn.execute(text("INSERT INTO audit.commitment_leaves (commitment_id, leaf_index, institution_id, enrolment_id, leaf_json, leaf_hash) VALUES (:c, :ix, CAST(:i AS uuid), CAST(:e AS uuid), CAST(:lj AS jsonb), :h)"),
                     [{"c": cid, "ix": ix, "i": inst, "e": l["enrolment_id"], "lj": dumps(l), "h": h.hex()} for ix, (l, h) in enumerate(zip(leaves, hashes))])
        audit.log(conn, "commitment.signed", entity_type="commitment", entity_id=cid, actor_id=p.id, actor_role=p.role, institution_id=inst, request_id=p.request_id,
                  payload={"root": root, "leaves": len(leaves), "version": version})
    return {"commitment_id": str(cid), "merkle_root": root, "leaf_count": len(leaves), "version": version, "status": "PENDING_COUNTERSIGN"}


def countersign(p: Principal, sid: str) -> dict:
    kid, jwk, pem = keys.signing_key("AICTE_COMMIT")
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        c = row(conn, "SELECT * FROM audit.cohort_commitments WHERE submission_id=:s AND status='PENDING_COUNTERSIGN' ORDER BY version DESC LIMIT 1", s=s["id"])
        if not c or s["status"] != "ACCEPTED":
            raise ApiError(409, "INVALID_STATE", "Nothing to countersign", "The institution must sign the commitment first.")
        pub = scalar(conn, "SELECT public_jwk FROM cred.signing_keys WHERE kid=:k", k=c["institution_kid"])
        msg = _sign_message(c["merkle_root"], str(c["institution_id"]), c["academic_year"], c["cutoff_at"], c["version"], c["leaf_count"])
        if not crypto.verify_es256(pub, msg, c["institution_signature"]):
            raise ApiError(409, "SIGNATURE_INVALID", "Institution signature invalid", "The institution's signature does not verify; the commitment is refused.")
        sig = crypto.sign_es256(pem, msg)
        conn.execute(text("UPDATE audit.cohort_commitments SET aicte_countersignature=:sig, aicte_kid=:k, status='COMPLETE', countersigned_by=CAST(:u AS uuid) WHERE id=:c"),
                     {"sig": sig, "k": kid, "u": p.id, "c": c["id"]})
        _hist(conn, s, "LOCKED", p, "Signed cut-off commitment countersigned")
        conn.execute(text("UPDATE ingest.submissions SET status='LOCKED', locked_at=:t, row_version=row_version+1 WHERE id=:s"), {"t": now(), "s": s["id"]})
        audit.log(conn, "commitment.countersigned", entity_type="commitment", entity_id=c["id"], actor_id=p.id, actor_role=p.role, institution_id=s["institution_id"], request_id=p.request_id)
    return {"status": "LOCKED", "merkle_root": c["merkle_root"]}


def get_commitment(p: Principal, sid: str) -> dict:
    with tx(p.db_ctx()) as conn:
        s = get_submission(conn, sid, p)
        c = row(conn, "SELECT id, version, merkle_root, leaf_count, status, cutoff_at, institution_kid, aicte_kid, created_at FROM audit.cohort_commitments WHERE submission_id=:s ORDER BY version DESC LIMIT 1", s=s["id"])
    return {"commitment": ({**c, "id": str(c["id"])} if c else None), "signatures": "ES256 institution signature and AICTE countersignature over the root (software keys, SIMULATED KMS)"}


def verify_commitment(p: Principal, sid: str) -> dict:
    """Recompute the Merkle root from live data and compare with the signed one; names the enrolments that changed after the cut-off."""
    with tx(p.db_ctx(), statement_timeout_ms=20000) as conn:
        s = get_submission(conn, sid, p)
        c = row(conn, "SELECT id, merkle_root, leaf_count, institution_kid, institution_signature, aicte_kid, aicte_countersignature, cutoff_at, version FROM audit.cohort_commitments "
                      "WHERE submission_id=:s ORDER BY version DESC LIMIT 1", s=s["id"])
        if not c:
            raise ApiError(404, "NOT_FOUND", "No commitment", "This submission has no commitment yet.")
        stored = {r["enrolment_id"]: r["leaf_hash"] for r in rows(conn, "SELECT enrolment_id::text AS enrolment_id, leaf_hash FROM audit.commitment_leaves WHERE commitment_id=:c", c=c["id"])}
        live = _leaves(conn, str(s["institution_id"]), s["academic_year"])
        root = crypto.merkle_root([leaf_hash(l) for l in live]).hex()
        changed = [l["enrolment_id"] for l in live if stored.get(l["enrolment_id"]) != leaf_hash(l).hex()]
        removed = [e for e in stored if e not in {l["enrolment_id"] for l in live}]
        pub = {k["kid"]: k["public_jwk"] for k in rows(conn, "SELECT kid, public_jwk FROM cred.signing_keys")}
        msg = _sign_message(c["merkle_root"], str(s["institution_id"]), s["academic_year"], c["cutoff_at"], c["version"], c["leaf_count"])
        inst_ok = crypto.verify_es256(pub.get(c["institution_kid"], {"x": "AA", "y": "AA"}), msg, c["institution_signature"])
        aicte_ok = bool(c["aicte_countersignature"]) and crypto.verify_es256(pub.get(c["aicte_kid"], {"x": "AA", "y": "AA"}), msg, c["aicte_countersignature"])
        audit.log(conn, "commitment.verified", entity_type="submission", entity_id=s["id"], actor_id=p.id, actor_role=p.role, request_id=p.request_id,
                  payload={"matches": root == c["merkle_root"], "changed": len(changed)})
    return {"matches": root == c["merkle_root"] and not removed, "signed_root": c["merkle_root"], "recomputed_root": root, "changed_enrolments": changed[:50],
            "changed_count": len(changed), "removed_count": len(removed), "institution_signature_valid": inst_ok, "aicte_countersignature_valid": aicte_ok}


def inclusion_proof(conn, enrolment_id: str) -> dict:
    leaf = row(conn, "SELECT l.commitment_id, l.leaf_index, l.leaf_json, l.leaf_hash, c.merkle_root, c.leaf_count, c.version, c.institution_kid, c.aicte_kid, c.status "
                     "FROM audit.commitment_leaves l JOIN audit.cohort_commitments c ON c.id=l.commitment_id WHERE l.enrolment_id=CAST(:e AS uuid) AND c.status='COMPLETE' "
                     "ORDER BY c.version DESC LIMIT 1", e=enrolment_id)
    if not leaf:
        raise ApiError(404, "NOT_FOUND", "No proof yet", "This enrolment is not part of a countersigned cohort commitment yet.")
    all_ = rows(conn, "SELECT leaf_hash FROM audit.commitment_leaves WHERE commitment_id=:c ORDER BY leaf_index", c=leaf["commitment_id"])
    hashes = [bytes.fromhex(r["leaf_hash"]) for r in all_]
    path = crypto.merkle_path(hashes, leaf["leaf_index"])
    return {"leaf": leaf["leaf_json"], "leaf_hash": leaf["leaf_hash"], "leaf_index": leaf["leaf_index"], "leaf_count": leaf["leaf_count"], "root": leaf["merkle_root"],
            "path": [{"side": s, "hash": h.hex()} for s, h in path], "commitment_version": leaf["version"], "hashing": "RFC 6962 (0x00 leaf prefix, 0x01 node prefix, SHA-256)"}


# ------------------------------------------------------------------ window extensions
def request_extension(p: Principal, new_closes_at: datetime, reason: str, year: str | None) -> dict:
    with tx(p.db_ctx()) as conn:
        w = window_for(conn, year or current_academic_year())
        if new_closes_at <= effective_close(conn, p.institution_id, w):
            raise ApiError(422, "VALIDATION_ERROR", "Date too early", "The new closing time must be later than the current one.",
                           errors=[{"field": "new_closes_at", "code": "TOO_EARLY", "message": "Must be after the current close.", "fix_suggestion": "Pick a later date."}])
        xid = uuid7()
        conn.execute(text("INSERT INTO ref.window_extensions (id, institution_id, window_id, requested_by, new_closes_at, reason) VALUES (:id, CAST(:i AS uuid), :w, CAST(:u AS uuid), :n, :r)"),
                     {"id": xid, "i": p.institution_id, "w": w["id"], "u": p.id, "n": new_closes_at, "r": reason})
        audit.log(conn, "extension.requested", entity_type="window_extension", entity_id=xid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, justification=reason, request_id=p.request_id)
    return {"id": str(xid), "status": "PENDING"}


def decide_extension(p: Principal, xid: str, approve: bool, reason: str) -> dict:
    with tx(p.db_ctx()) as conn:
        x = row(conn, "SELECT x.*, i.state, i.region FROM ref.window_extensions x JOIN ref.institutions i ON i.id=x.institution_id WHERE x.id=CAST(:x AS uuid) FOR UPDATE OF x", x=xid)
        if not x or not _in_scope(p, x):
            raise ApiError(404, "NOT_FOUND", "Not found", "No such request.")
        if x["status"] != "PENDING":
            raise ApiError(409, "INVALID_STATE", "Already decided", f"This request is {x['status']}.")
        conn.execute(text("UPDATE ref.window_extensions SET status=:s, decided_by=CAST(:u AS uuid), decided_at=:t, decision_reason=:r, row_version=row_version+1 WHERE id=:x"),
                     {"s": "APPROVED" if approve else "REJECTED", "u": p.id, "t": now(), "r": reason, "x": x["id"]})
        audit.log(conn, "extension.decided", entity_type="window_extension", entity_id=x["id"], actor_id=p.id, actor_role=p.role, institution_id=x["institution_id"], justification=reason, request_id=p.request_id,
                  payload={"approved": approve})
    return {"id": xid, "status": "APPROVED" if approve else "REJECTED"}
