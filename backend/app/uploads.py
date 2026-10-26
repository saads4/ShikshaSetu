"""Bulk upload pipeline (REQ-ING): scan -> parse & stage (encrypted) -> dry-run validate in chunks -> commit by a different admin.

CSV only in this delivery (XLSX/macro checks need a spreadsheet library that is not in the approved stack).
Malware scanning is NOT performed and is recorded as such (DEC-18). The same claim logic (claims.process_claim) runs per row.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import time
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import text

from . import audit, claims, crypto, keys, notify, ops, verification
from .config import ROOT, get_settings
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, DbContext, owner_tx, row, rows, scalar, tx
from .deps import Principal
from .permissions import PERMISSIONS

CHUNK = 500
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_ROWS = int(os.environ.get("MAX_UPLOAD_ROWS", "20000"))
TEMPLATE_VERSION = 1
COLUMNS = [  # (name, required, description)
    ("row_key", False, "Stable key for idempotent re-uploads (defaults to a hash of student ID + programme + year)"),
    ("institution_student_id", True, "Your internal student or roll number"), ("programme_code", True, "Approved programme code"),
    ("bucket_type", False, "REGULAR (default), LATERAL, SN_FN, SN_OCI, SN_TFW, SN_EWS"), ("apaar_id", False, "12-digit APAAR ID (leave empty for a provisional ID)"),
    ("provisional_type", False, "NOGEN, REFUSED or FOREIGN when there is no APAAR ID"), ("document_number", False, "Passport/OCI number for foreign nationals"),
    ("name", True, "Full name as on the certificate"), ("dob", True, "Date of birth: YYYY-MM-DD or DD/MM/YYYY"), ("gender", False, "M, F or O"),
    ("category", False, "GEN, OBC, SC, ST or EWS"), ("citizenship_type", False, "INDIAN (default), FOREIGN, OCI, PIO"), ("nationality_code", False, "ISO country code, IN default"),
    ("parent_name", False, "Parent or guardian name"), ("mobile", False, "Mobile number (low-trust channel if the registry has none)"), ("email", False, "Email"),
    ("district", False, "District"), ("admission_date", False, "YYYY-MM-DD; if given the student is admitted at commit"),
]
NAMES = [c[0] for c in COLUMNS]
REQUIRED = [c[0] for c in COLUMNS if c[1]]


def template() -> dict:
    return {"version": TEMPLATE_VERSION, "columns": [{"name": n, "required": r, "description": d} for n, r, d in COLUMNS], "csv_header": ",".join(NAMES),
            "limits": {"max_mb": MAX_BYTES // 1048576, "max_rows": MAX_ROWS, "formats": ["csv"]}}


def storage_path(job_id) -> Path:
    d = Path(get_settings().storage_dir)
    d = (d if d.is_absolute() else ROOT / d) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.csv"


def principal_for_user(user_id: str) -> Principal:
    with tx(SYSTEM) as conn:
        u = row(conn, "SELECT id, username, display_name, utype, institution_id, student_id, locale FROM iam.users WHERE id=CAST(:u AS uuid)", u=user_id)
        roles = rows(conn, "SELECT role_code, scope_type, scope_value FROM iam.user_roles WHERE user_id=CAST(:u AS uuid)", u=user_id)
    perms: set[str] = set()
    for r in roles:
        perms |= PERMISSIONS.get(r["role_code"], set())
    inst = str(u["institution_id"]) if u["institution_id"] else None
    return Principal("user", str(u["id"]), u["username"], u["display_name"], u["utype"], [r["role_code"] for r in roles], roles[0]["scope_type"], roles[0]["scope_value"], inst, None, None,
                     u["locale"], perms, "bulk-job", "worker")


def _safe(v: str) -> str:
    return "'" + v if v and v[0] in "=+-@" else v


# ------------------------------------------------------------------ API-side operations
def create_job(p: Principal, filename: str, year: str | None) -> dict:
    if not filename.lower().endswith(".csv"):
        raise ApiError(422, "VALIDATION_ERROR", "CSV only", "Only .csv files are accepted in this prototype.",
                       errors=[{"field": "filename", "code": "FORMAT", "message": "Not a .csv file.", "fix_suggestion": "Export the sheet as CSV (UTF-8)."}])
    if p.kind != "user":
        raise ApiError(400, "USER_REQUIRED", "Use a user account", "Bulk upload jobs are created by signed-in users in this prototype; API clients use POST /claims.")
    jid = uuid7()
    with tx(p.db_ctx()) as conn:
        conn.execute(text("INSERT INTO ingest.upload_jobs (id, institution_id, academic_year, created_by, filename) VALUES (:id, CAST(:i AS uuid), :y, CAST(:u AS uuid), :f)"),
                     {"id": jid, "i": p.institution_id, "y": year or claims.current_academic_year(), "u": p.id, "f": filename[:200]})
        audit.log(conn, "upload.created", entity_type="upload_job", entity_id=jid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"id": str(jid), "status": "UPLOADING"}


def store_content(p: Principal, job_id: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "File too large", f"The limit is {MAX_BYTES // 1048576} MB.")
    with tx(p.db_ctx()) as conn:
        j = row(conn, "SELECT id, status, created_by FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid) AND institution_id=CAST(:i AS uuid)", j=job_id, i=p.institution_id)
        if not j:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such upload job.")
        if j["status"] != "UPLOADING":
            raise ApiError(409, "INVALID_STATE", "Already uploaded", "This job already has content.")
        storage_path(job_id).write_bytes(data)
        conn.execute(text("UPDATE ingest.upload_jobs SET size_bytes=:n, storage_key=:k, status='SCANNING', updated_at=:t WHERE id=:j"), {"n": len(data), "k": f"uploads/{job_id}.csv", "t": now(), "j": j["id"]})
    ops.enqueue("ingest", "ingest.scan", {"job_id": job_id}, dedupe_key=f"scan:{job_id}", institution_id=p.institution_id)
    return {"id": job_id, "status": "SCANNING", "size_bytes": len(data)}


def job_view(p: Principal, job_id: str) -> dict:
    with tx(p.db_ctx()) as conn:
        j = row(conn, "SELECT j.*, u.username AS created_by_name, c.username AS committed_by_name FROM ingest.upload_jobs j JOIN iam.users u ON u.id=j.created_by "
                      "LEFT JOIN iam.users c ON c.id=j.committed_by WHERE j.id=CAST(:j AS uuid) AND j.institution_id=CAST(:i AS uuid)", j=job_id, i=p.institution_id)
    if not j:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such upload job.")
    return {**{k: (str(v) if k in ("id", "institution_id", "created_by", "committed_by") and v else v) for k, v in j.items()},
            "malware_scan": "Not performed in this prototype (simulated gap, DEC-18)"}


def commit(p: Principal, job_id: str) -> dict:
    with tx(p.db_ctx()) as conn:
        j = row(conn, "SELECT * FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid) AND institution_id=CAST(:i AS uuid) FOR UPDATE", j=job_id, i=p.institution_id)
        if not j:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such upload job.")
        if str(j["created_by"]) == p.id:
            raise ApiError(403, "SEPARATION_OF_DUTIES", "Another admin must commit", "The person who uploaded a file cannot commit it. Ask a different institution admin.")
        if j["status"] != "READY_TO_COMMIT":
            raise ApiError(409, "INVALID_STATE", "Not ready", f"Only READY_TO_COMMIT jobs can be committed (this one is {j['status']}).")
        busy = scalar(conn, "SELECT count(*) FROM ingest.upload_jobs WHERE institution_id=CAST(:i AS uuid) AND status IN ('VALIDATING','COMMITTING')", i=p.institution_id)
        if busy >= 2:
            raise ApiError(429, "JOB_QUOTA", "Too many jobs running", "Your institution already has 2 jobs running.", retryable=True)
        n = scalar(conn, "SELECT count(*) FROM ingest.staged_rows WHERE job_id=:j AND outcome='ACCEPTED'", j=j["id"])
        if not n:
            raise ApiError(409, "NOTHING_TO_COMMIT", "No accepted rows", "No rows are ACCEPTED. Fix the file and upload it again.")
        chunks = _make_chunks(conn, j["id"], str(j["institution_id"]), "COMMIT", "ACCEPTED")
        conn.execute(text("UPDATE ingest.upload_jobs SET status='COMMITTING', committed_by=CAST(:u AS uuid), committed_at=:t, updated_at=:t WHERE id=:j"), {"u": p.id, "t": now(), "j": j["id"]})
        audit.log(conn, "upload.commit_started", entity_type="upload_job", entity_id=j["id"], actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id,
                  payload={"rows": n, "uploaded_by": str(j["created_by"])})
    for c in chunks:
        ops.enqueue("bulk_claim", "ingest.commit_chunk", {"job_id": job_id, "chunk_id": str(c)}, institution_id=p.institution_id)
    return {"status": "COMMITTING", "rows": n}


def cancel(p: Principal, job_id: str) -> dict:
    with tx(p.db_ctx()) as conn:
        n = conn.execute(text("UPDATE ingest.upload_jobs SET status='CANCELLED', updated_at=:t WHERE id=CAST(:j AS uuid) AND institution_id=CAST(:i AS uuid) "
                              "AND status IN ('UPLOADING','SCANNING','VALIDATING','READY_TO_COMMIT')"), {"t": now(), "j": job_id, "i": p.institution_id}).rowcount
        if not n:
            raise ApiError(409, "INVALID_STATE", "Cannot cancel", "Only jobs before commit can be cancelled.")
        audit.log(conn, "upload.cancelled", entity_type="upload_job", entity_id=job_id, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id)
    return {"status": "CANCELLED"}


def _make_chunks(conn, job_id, inst, phase: str, outcome: str | None) -> list:
    where = "AND outcome='ACCEPTED'" if outcome else ""
    rn = [r["row_no"] for r in rows(conn, f"SELECT row_no FROM ingest.staged_rows WHERE job_id=:j {where} ORDER BY row_no", j=job_id)]
    ids = []
    for k in range(0, len(rn), CHUNK):
        cid = uuid7()
        conn.execute(text("INSERT INTO ingest.upload_chunks (id, job_id, institution_id, phase, chunk_no, row_start, row_end) VALUES (:id,:j,CAST(:i AS uuid),:p,:n,:a,:b)"),
                     {"id": cid, "j": job_id, "i": inst, "p": phase, "n": k // CHUNK, "a": rn[k], "b": rn[min(k + CHUNK, len(rn)) - 1]})
        ids.append(cid)
    return ids


# ------------------------------------------------------------------ worker handlers
def _fail(job_id: str, reason: str, scan: dict | None = None) -> dict:
    with owner_tx() as conn:
        conn.execute(text("UPDATE ingest.upload_jobs SET status='FAILED', failure_reason=:r, scan_result=CAST(:s AS jsonb), updated_at=:t WHERE id=CAST(:j AS uuid)"),
                     {"r": reason, "s": dumps(scan or {}), "t": now(), "j": job_id})
    return {"failed": reason}


@ops.handler("ingest.scan")
def scan_and_stage(payload: dict) -> dict:
    job_id = payload["job_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT id, institution_id, academic_year, status, created_by FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid)", j=job_id)
    if not j or j["status"] != "SCANNING":
        return {"skipped": True}
    inst = str(j["institution_id"])
    raw = storage_path(job_id).read_bytes()
    scan = {"extension": "csv", "size_bytes": len(raw), "malware": "NOT_PERFORMED", "formula_cells_neutralised": 0}
    if b"\x00" in raw:
        return _fail(job_id, "The file contains binary data and is not a text CSV.", scan)
    try:
        text_ = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _fail(job_id, "The file is not UTF-8 text. Export as CSV (UTF-8).", scan)
    rdr = csv.DictReader(io.StringIO(text_))
    header = [h.strip() for h in (rdr.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        return _fail(job_id, f"Missing required columns: {', '.join(missing)}. Download the template.", scan)
    unknown = [h for h in header if h not in NAMES]
    data = list(rdr)
    if len(data) > MAX_ROWS:
        return _fail(job_id, f"The file has {len(data)} rows; the limit is {MAX_ROWS}.", {**scan, "rows": len(data)})
    if not data:
        return _fail(job_id, "The file has no data rows.", scan)
    scan.update(rows=len(data), unknown_columns=unknown)
    dk_id, _, dkey = keys.data_key(inst)
    staged, seen_formula = [], 0
    for i, rowd in enumerate(data, start=2):          # row_no = line number in the file (header is line 1)
        clean = {}
        for k in NAMES:
            v = (rowd.get(k) or "").strip()
            if v and v[0] in "=+-@" and k not in ("dob", "admission_date", "mobile"):
                v, seen_formula = _safe(v), seen_formula + 1       # CSV-formula neutralisation (REQ-ING-04)
            clean[k] = v or None
        rk = clean["row_key"] or hashlib.sha256(f"{clean['institution_student_id']}|{clean['programme_code']}|{j['academic_year']}".encode()).hexdigest()[:32]
        clean["row_key"] = rk
        nonce, ct = crypto.encrypt(dkey, clean, f"{job_id}|{i}".encode())
        staged.append({"id": uuid7(), "j": job_id, "i": inst, "n": i, "rk": rk, "nonce": nonce, "ct": ct})
    scan["formula_cells_neutralised"] = seen_formula
    with owner_tx() as conn:
        conn.execute(text("UPDATE ingest.upload_jobs SET status='PARSING', scan_result=CAST(:s AS jsonb), total_rows=:n, updated_at=:t WHERE id=CAST(:j AS uuid)"),
                     {"s": dumps(scan), "n": len(staged), "t": now(), "j": job_id})
        for k in range(0, len(staged), 1000):
            conn.execute(text("INSERT INTO ingest.staged_rows (id, job_id, institution_id, row_no, row_key, payload_nonce, payload_ct) VALUES (:id, CAST(:j AS uuid), CAST(:i AS uuid), :n, :rk, :nonce, :ct)"), staged[k:k + 1000])
        chunks = _make_chunks(conn, job_id, inst, "VALIDATE", None)
        conn.execute(text("UPDATE ingest.upload_jobs SET status='VALIDATING', updated_at=:t WHERE id=CAST(:j AS uuid)"), {"t": now(), "j": job_id})
    for c in chunks:
        ops.enqueue("ingest", "ingest.validate_chunk", {"job_id": job_id, "chunk_id": str(c)}, institution_id=inst)
    return {"rows": len(staged), "chunks": len(chunks)}


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'{v}' is not a date")


def to_claim(d: dict, year: str, *, admit: bool = True) -> claims.ClaimIn:
    kw = dict(programme_code=d["programme_code"] or "", bucket_type=(d["bucket_type"] or "REGULAR").upper(), academic_year=year, institution_student_id=d["institution_student_id"] or "",
              name=d["name"] or "", dob=_parse_date(d["dob"]), gender=(d["gender"] or "O").upper()[:1], category=(d["category"] or "GEN").upper(), citizenship_type=(d["citizenship_type"] or "INDIAN").upper(),
              nationality_code=(d["nationality_code"] or "IN").upper(), parent_name=d["parent_name"], mobile=d["mobile"], email=d["email"], district=d["district"], row_key=d["row_key"])
    if d["provisional_type"] and not d["apaar_id"]:
        kw["provisional"] = {"type": d["provisional_type"].upper(), "document_number": d["document_number"]}
    else:
        kw["apaar_id"] = d["apaar_id"]
    ad = _parse_date(d["admission_date"])
    if ad and admit:
        kw.update(admit_now=True, admission_date=ad)
    return claims.ClaimIn(**kw)


def _errs_from_validation(e: ValidationError) -> list[dict]:
    return [{"field": ".".join(str(x) for x in er["loc"]), "code": er["type"], "message": er["msg"], "fix": "Correct this value."} for er in e.errors()]


def _decrypt_rows(job: dict, dkey: bytes, rws: list[dict]) -> list[dict]:
    return [{**r, "data": crypto.decrypt(dkey, r["payload_nonce"], r["payload_ct"], f"{job['id']}|{r['row_no']}".encode())} for r in rws]


@ops.handler("ingest.validate_chunk")
def validate_chunk(payload: dict) -> dict:
    job_id, chunk_id = payload["job_id"], payload["chunk_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT * FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid)", j=job_id)
        c = row(conn, "SELECT * FROM ingest.upload_chunks WHERE id=CAST(:c AS uuid)", c=chunk_id)
        if not j or j["status"] != "VALIDATING" or c["status"] == "DONE":
            return {"skipped": True}
        rws = rows(conn, "SELECT id, row_no, row_key, payload_nonce, payload_ct FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) AND row_no BETWEEN :a AND :b ORDER BY row_no", j=job_id, a=c["row_start"], b=c["row_end"])
    p = principal_for_user(str(j["created_by"]))
    _, _, dkey = keys.data_key(str(j["institution_id"]))
    results = []
    for r in _decrypt_rows(j, dkey, rws):
        outcome, errors, diff, claim_outcome, enr = _validate_row(p, r["data"], j["academic_year"])
        results.append({"id": r["id"], "o": outcome, "e": dumps(errors), "d": dumps(diff), "co": claim_outcome, "en": enr})
        time.sleep(0.02)       # registry diagnosis is rate-limited (~20/s per worker thread)
    with owner_tx() as conn:
        conn.execute(text("UPDATE ingest.staged_rows SET outcome=:o, errors=CAST(:e AS jsonb), diff=CAST(:d AS jsonb), claim_outcome=:co, enrolment_id=CAST(:en AS uuid) WHERE id=:id"), results)
        conn.execute(text("UPDATE ingest.upload_chunks SET status='DONE', attempts=attempts+1 WHERE id=CAST(:c AS uuid)"), {"c": chunk_id})
        left = scalar(conn, "SELECT count(*) FROM ingest.upload_chunks WHERE job_id=CAST(:j AS uuid) AND phase='VALIDATE' AND status<>'DONE'", j=job_id)
    if not left:
        ops.enqueue("ingest", "ingest.finalize_validation", {"job_id": job_id}, dedupe_key=f"finval:{job_id}", institution_id=str(j["institution_id"]))
    return {"rows": len(results)}


def _validate_row(p: Principal, d: dict, year: str):
    """Dry-run one row with the live claim logic. Returns (outcome, errors, diff, claim_outcome, enrolment_id)."""
    try:
        body = to_claim(d, year)
    except (ValidationError, ValueError) as e:
        errs = _errs_from_validation(e) if isinstance(e, ValidationError) else [{"field": "dob", "code": "DATE", "message": str(e), "fix": "Use YYYY-MM-DD or DD/MM/YYYY."}]
        return "REJECTED", errs, {}, "INVALID_ROW", None
    status, out = claims.process_claim(p, body, dry_run=True, source="BULK")
    if status == 200 and out.get("outcome") == "DUPLICATE_SAME_INSTITUTION":
        return "NOOP_DUPLICATE", [], _diff(p, out.get("enrolment_id"), d), "DUPLICATE_SAME_INSTITUTION", out.get("enrolment_id")
    if status == 200:
        errs = [{"field": "", "code": f["rule_code"], "message": f["message"], "fix": "", "severity": f["severity"]} for f in out.get("findings", [])]
        errs += [{"field": f["field"], "code": f["code"], "message": f["message"], "fix": f["fix"], "severity": "Warning"} for f in out.get("fix_list", [])]
        held = any(e.get("severity") == "Error" for e in errs)
        return ("HELD" if held else "ACCEPTED"), errs, {"status": "NEW"}, out.get("outcome"), None
    code = out.get("code", "ERROR")
    errs = out.get("errors") or [{"field": "", "code": code, "message": out.get("title", ""), "fix_suggestion": out.get("detail", "")}]
    errs = [{"field": e.get("field", ""), "code": e.get("code", code), "message": e.get("message", ""), "fix": e.get("fix_suggestion") or e.get("fix", "")} for e in errs]
    if code in ("SEAT_LIMIT", "SERVICE_BUSY"):
        return "HELD", errs + [{"field": "bucket_type", "code": code, "message": "Seat limit reached; request an exception.", "fix": "Request a seat exception, then re-upload."}], {}, code, None
    return "REJECTED", errs, {}, code, None


def _diff(p: Principal, enrolment_id: str | None, d: dict) -> dict:
    """Field-level before/after against the existing claim (visible only to the tenant; REQ-ING-08)."""
    if not enrolment_id:
        return {"status": "UNCHANGED"}
    with tx(p.db_ctx()) as conn:
        e = row(conn, "SELECT student_id, gender, category FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        pii = verification._vault(conn, str(e["student_id"]), p.institution_id) if e else {}
    changes = {}
    for k, before in (("name", pii.get("name")), ("dob", pii.get("dob")), ("parent_name", pii.get("parent_name")), ("gender", e and e["gender"]), ("category", e and e["category"])):
        after = d.get(k)
        if k == "dob" and after:
            try:
                after = _parse_date(after).isoformat()
            except ValueError:
                pass
        if after and before and str(after).strip().lower() != str(before).strip().lower():
            changes[k] = {"before": before, "after": after}
    return {"status": "CHANGED" if changes else "UNCHANGED", "fields": changes}


@ops.handler("ingest.finalize_validation")
def finalize_validation(payload: dict) -> dict:
    job_id = payload["job_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT * FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid)", j=job_id)
        if j["status"] != "VALIDATING":
            return {"skipped": True}
        accepted = rows(conn, "SELECT s.id, s.row_no, s.row_key, s.payload_nonce, s.payload_ct, s.errors FROM ingest.staged_rows s WHERE s.job_id=CAST(:j AS uuid) AND s.outcome='ACCEPTED' ORDER BY s.row_no", j=job_id)
        buckets = {(r["code"], r["bucket"]): dict(r) for r in rows(conn, "SELECT pr.programme_code AS code, b.bucket_type AS bucket, b.cap + b.approved_extra - b.occupied AS room FROM core.seat_buckets b JOIN ref.programmes pr ON pr.id=b.programme_id "
                                                                         "WHERE b.institution_id=CAST(:i AS uuid) AND b.academic_year=:y", i=str(j["institution_id"]), y=j["academic_year"])}
    _, _, dkey = keys.data_key(str(j["institution_id"]))
    seen_keys, seen_ids, upd = set(), set(), []
    for r in _decrypt_rows(j, dkey, accepted):                         # in-file duplicates and seat-headroom projection (advisory)
        d = r["data"]
        ident = (d.get("apaar_id") or "").replace(" ", "")
        errs = None
        if r["row_key"] in seen_keys or (ident and ident in seen_ids):
            upd.append({"id": r["id"], "o": "REJECTED", "e": dumps([{"field": "apaar_id", "code": "DUPLICATE_IN_FILE", "message": "This student appears earlier in the same file.", "fix": "Remove the duplicate row."}])})
            continue
        seen_keys.add(r["row_key"]); ident and seen_ids.add(ident)
        b = buckets.get((d["programme_code"], (d.get("bucket_type") or "REGULAR").upper()))
        if b is not None:
            if b["room"] <= 0:
                upd.append({"id": r["id"], "o": "HELD", "e": dumps([{"field": "bucket_type", "code": "SEAT_LIMIT_PROJECTED", "message": "Not enough seats left in this bucket for all rows in this file.", "fix": "Request a seat exception, then re-upload."}])})
                continue
            b["room"] -= 1
    with owner_tx() as conn:
        if upd:
            conn.execute(text("UPDATE ingest.staged_rows SET outcome=:o, errors=CAST(:e AS jsonb) WHERE id=:id"), upd)
        c = {r["outcome"]: r["n"] for r in conn.execute(text("SELECT outcome, count(*) n FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) GROUP BY outcome"), {"j": job_id}).mappings()}
        conn.execute(text("UPDATE ingest.upload_jobs SET status='READY_TO_COMMIT', accepted=:a, held=:h, rejected=:r, noop=:n, updated_at=:t WHERE id=CAST(:j AS uuid)"),
                     {"a": c.get("ACCEPTED", 0), "h": c.get("HELD", 0), "r": c.get("REJECTED", 0), "n": c.get("NOOP_DUPLICATE", 0), "t": now(), "j": job_id})
        audit.log(conn, "upload.validated", entity_type="upload_job", entity_id=job_id, institution_id=j["institution_id"], payload={"accepted": c.get("ACCEPTED", 0), "held": c.get("HELD", 0), "rejected": c.get("REJECTED", 0)})
    notify.record("INAPP", "UPLOAD_READY", "SENT", institution_id=str(j["institution_id"]), user_id=str(j["created_by"]), kind="USER", params={"job": job_id})
    return {"counts": c}


@ops.handler("ingest.commit_chunk")
def commit_chunk(payload: dict) -> dict:
    job_id, chunk_id = payload["job_id"], payload["chunk_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT * FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid)", j=job_id)
        c = row(conn, "SELECT * FROM ingest.upload_chunks WHERE id=CAST(:c AS uuid)", c=chunk_id)
        if not j or j["status"] != "COMMITTING" or c["status"] == "DONE":
            return {"skipped": True}
        rws = rows(conn, "SELECT id, row_no, row_key, payload_nonce, payload_ct FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) AND outcome='ACCEPTED' AND row_no BETWEEN :a AND :b ORDER BY row_no",
                   j=job_id, a=c["row_start"], b=c["row_end"])
    p = principal_for_user(str(j["committed_by"]))
    _, _, dkey = keys.data_key(str(j["institution_id"]))
    results = []
    for r in _decrypt_rows(j, dkey, rws):
        try:
            body = to_claim(r["data"], j["academic_year"])
            status, out = claims.process_claim(p, body, dry_run=False, source="BULK")
        except Exception as exc:  # noqa: BLE001
            status, out = 500, {"code": type(exc).__name__}
        if status == 201:
            results.append({"id": r["id"], "o": "COMMITTED", "co": out["outcome"], "en": out["enrolment_id"], "e": "[]"})
        elif status == 200:
            results.append({"id": r["id"], "o": "NOOP_DUPLICATE", "co": "DUPLICATE_SAME_INSTITUTION", "en": out.get("enrolment_id"), "e": "[]"})
        else:
            code = out.get("code", "ERROR")
            results.append({"id": r["id"], "o": "COMMIT_FAILED", "co": code, "en": None,
                            "e": dumps([{"field": "", "code": code, "message": out.get("title", "Commit failed"), "fix": out.get("detail", "")}])})
    with owner_tx() as conn:
        conn.execute(text("UPDATE ingest.staged_rows SET outcome=:o, claim_outcome=:co, enrolment_id=CAST(:en AS uuid), errors=CAST(:e AS jsonb) WHERE id=:id"), results)
        conn.execute(text("UPDATE ingest.upload_chunks SET status='DONE', attempts=attempts+1 WHERE id=CAST(:c AS uuid)"), {"c": chunk_id})
        left = scalar(conn, "SELECT count(*) FROM ingest.upload_chunks WHERE job_id=CAST(:j AS uuid) AND phase='COMMIT' AND status<>'DONE'", j=job_id)
    if not left:
        ops.enqueue("bulk_claim", "ingest.finalize_commit", {"job_id": job_id}, dedupe_key=f"fincom:{job_id}", institution_id=str(j["institution_id"]))
    return {"rows": len(results)}


@ops.handler("ingest.finalize_commit")
def finalize_commit(payload: dict) -> dict:
    job_id = payload["job_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT * FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid)", j=job_id)
        if j["status"] != "COMMITTING":
            return {"skipped": True}
        c = {r["outcome"]: r["n"] for r in conn.execute(text("SELECT outcome, count(*) n FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) GROUP BY outcome"), {"j": job_id}).mappings()}
        failed = c.get("COMMIT_FAILED", 0)
        status = "COMPLETED" if not failed and not c.get("HELD") and not c.get("REJECTED") else "PARTIAL"
        conn.execute(text("UPDATE ingest.upload_jobs SET status=:s, committed=:c, failed=:f, noop=:n, updated_at=:t WHERE id=CAST(:j AS uuid)"),
                     {"s": status, "c": c.get("COMMITTED", 0), "f": failed, "n": c.get("NOOP_DUPLICATE", 0), "t": now(), "j": job_id})
        audit.log(conn, "upload.completed", entity_type="upload_job", entity_id=job_id, institution_id=j["institution_id"], payload={"status": status, "committed": c.get("COMMITTED", 0), "failed": failed})
        claims._enqueue_outbox(conn, "upload.completed", "upload_job", job_id, j["institution_id"], {"academic_year": j["academic_year"], "status": status, "committed": c.get("COMMITTED", 0), "failed": failed})
    for u in {str(j["created_by"]), str(j["committed_by"])}:
        notify.record("INAPP", "UPLOAD_DONE", "SENT", institution_id=str(j["institution_id"]), user_id=u, kind="USER", params={"job": job_id, "status": status})
    return {"status": status}


def errors_csv(p: Principal, job_id: str) -> str:
    j = job_view(p, job_id)
    with owner_tx() as conn:
        rws = rows(conn, "SELECT row_no, outcome, errors FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) AND outcome IN ('REJECTED','HELD','COMMIT_FAILED') ORDER BY row_no", j=job_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["row_no", "outcome", "field", "code", "message", "suggested_fix"])
    for r in rws:
        for e in (r["errors"] or [{"field": "", "code": "", "message": "", "fix": ""}]):
            w.writerow([_safe(str(x)) for x in (r["row_no"], r["outcome"], e.get("field", ""), e.get("code", ""), e.get("message", ""), e.get("fix") or e.get("fix_suggestion", ""))])
    return out.getvalue()


def rows_view(p: Principal, job_id: str, outcome: str | None, limit: int, offset: int) -> dict:
    job_view(p, job_id)
    with owner_tx() as conn:
        j = row(conn, "SELECT id FROM ingest.upload_jobs WHERE id=CAST(:j AS uuid) AND institution_id=CAST(:i AS uuid)", j=job_id, i=p.institution_id)
        if not j:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such upload job.")
        flt = " AND outcome=:oc" if outcome else ""
        params = {"j": job_id, "n": limit, "off": offset, **({"oc": outcome} if outcome else {})}
        rws = rows(conn, "SELECT id, row_no, row_key, outcome, errors, diff, claim_outcome, enrolment_id, payload_nonce, payload_ct FROM ingest.staged_rows "
                         "WHERE job_id=CAST(:j AS uuid)" + flt + " ORDER BY row_no LIMIT :n OFFSET :off", **params)
        total = scalar(conn, "SELECT count(*) FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid)" + flt, **{k: v for k, v in params.items() if k in ("j", "oc")})
    _, _, dkey = keys.data_key(p.institution_id)
    out = []
    for r in rws:
        d = crypto.decrypt(dkey, r["payload_nonce"], r["payload_ct"], f"{job_id}|{r['row_no']}".encode())
        out.append({"row_no": r["row_no"], "outcome": r["outcome"], "institution_student_id": d["institution_student_id"], "name": d["name"], "programme_code": d["programme_code"],
                    "bucket_type": d["bucket_type"] or "REGULAR", "errors": r["errors"], "diff": r["diff"], "claim_outcome": r["claim_outcome"],
                    "enrolment_id": str(r["enrolment_id"]) if r["enrolment_id"] else None})
    return {"items": out, "total": total}
