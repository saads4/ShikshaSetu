"""Reports (REQ-RPT): CSV / XLSX (XlsxWriter) / PDF (reportlab), stamped, QR-verifiable, small-cell suppressed, formula-safe.

AICTE reports are aggregates built only from agg.*, reference and intel tables. Student-level data appears only in the
institution's own enrolment export (decrypted, audited). The QR carries a random reference only; no personal data.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
from datetime import timedelta
from pathlib import Path

import segno
import xlsxwriter
from sqlalchemy import text

from . import audit, claims, crypto, keys, ops, verification
from .config import ROOT, get_settings
from .core import ApiError, dumps, now, uuid7
from .db import SYSTEM, owner_tx, row, rows, scalar, tx
from .deps import Principal

SMALL_CELL = 5
DEFINITIONS = ("Occupied = CLAIMED or ADMITTED seats. Cumulative admitted = ever ADMITTED, never reduced by withdrawals. Verified = occupied with all three locks agreeing. "
               "Groups smaller than 5 are suppressed (<5).")
TYPES = {"SEAT_UTILISATION": "Seat utilisation", "ENROLMENT_SUMMARY": "Enrolment summary", "VERIFICATION_STATUS": "Verification status", "FINDINGS": "Findings summary",
         "COMPLIANCE": "Submission compliance", "TRUST_INDEX": "Trust Index (review aid)", "CLAIM_ACTIVITY": "Claim activity", "INSTITUTION_PACK": "Institution summary pack",
         "ENROLMENT_EXPORT": "Enrolment export (student-level, institution only)"}
FORMATS = ("CSV", "XLSX", "PDF")


def supp(n):
    return "<5" if isinstance(n, int) and 0 < n < SMALL_CELL else n


def safe(v):
    """Formula-safe cell (REQ-RPT-04): a leading = + - @ is neutralised with a quote."""
    if isinstance(v, str) and v and v[0] in "=+-@":
        return "'" + v
    return v


def _scope(p: Principal, alias: str) -> tuple[str, dict]:
    if p.institution_id:
        return f" AND {alias}.institution_id=CAST(:scope_inst AS uuid)", {"scope_inst": p.institution_id}
    if p.scope_type == "REGION":
        return f" AND i.region=:scope_value", {"scope_value": p.scope_value}
    if p.scope_type == "STATE":
        return f" AND i.state=:scope_value", {"scope_value": p.scope_value}
    return "", {}


# ------------------------------------------------------------------ data builders: (title, columns, rows)
def build(conn, p: Principal, rtype: str, year: str) -> tuple[str, list[str], list[list]]:
    sc, sp = _scope(p, "x")
    if rtype == "SEAT_UTILISATION":
        d = rows(conn, "SELECT i.name, i.state, x.programme_name, x.level, x.bucket_type, x.cap, x.approved_extra, x.occupied, x.cumulative_admitted, x.pct FROM agg.seat_utilisation x "
                       "JOIN ref.institutions i ON i.id=x.institution_id WHERE x.academic_year=:y" + sc + " ORDER BY i.name, x.programme_name, x.bucket_type", y=year, **sp)
        return "Seat utilisation", ["Institution", "State", "Programme", "Level", "Bucket", "Approved seats", "Approved extra", "Occupied", "Cumulative admitted", "Utilisation %"], \
            [[r["name"], r["state"], r["programme_name"], r["level"], r["bucket_type"], r["cap"], r["approved_extra"], r["occupied"], r["cumulative_admitted"], float(r["pct"])] for r in d]
    if rtype == "ENROLMENT_SUMMARY":
        d = rows(conn, "SELECT i.state, x.level, count(DISTINCT x.programme_id) AS programmes, sum(x.cap + x.approved_extra) AS seats, sum(x.occupied) AS occupied, sum(x.cumulative_admitted) AS admitted "
                       "FROM agg.seat_utilisation x JOIN ref.institutions i ON i.id=x.institution_id WHERE x.academic_year=:y" + sc + " GROUP BY i.state, x.level ORDER BY i.state, x.level", y=year, **sp)
        return "Enrolment summary by state and level", ["State", "Level", "Programmes", "Approved seats", "Occupied", "Cumulative admitted"], \
            [[r["state"], r["level"], r["programmes"], int(r["seats"]), supp(int(r["occupied"])), supp(int(r["admitted"]))] for r in d]
    if rtype == "VERIFICATION_STATUS":
        d = rows(conn, "SELECT i.name, i.state, x.total, x.verified, x.pending, x.unconfirmed, x.disputed, x.pending_fix, x.pending_registry, x.provisional, x.low_trust_channel FROM agg.verification_funnel x "
                       "JOIN ref.institutions i ON i.id=x.institution_id WHERE x.academic_year=:y" + sc + " ORDER BY i.name", y=year, **sp)
        return "Verification status (Triple-Lock)", ["Institution", "State", "Live enrolments", "Verified", "Pending", "Unconfirmed", "Disputed", "Pending fix", "Pending registry", "Provisional ID", "Low-trust confirmations"], \
            [[r["name"], r["state"], r["total"], supp(r["verified"]), supp(r["pending"]), supp(r["unconfirmed"]), supp(r["disputed"]), supp(r["pending_fix"]), supp(r["pending_registry"]), supp(r["provisional"]), supp(r["low_trust_channel"])] for r in d]
    if rtype == "FINDINGS":
        d = rows(conn, "SELECT i.name, x.severity, x.status, x.count FROM agg.findings_summary x JOIN ref.institutions i ON i.id=x.institution_id WHERE x.academic_year=:y" + sc + " ORDER BY i.name, x.severity, x.status", y=year, **sp)
        return "Findings summary", ["Institution", "Severity", "Status", "Count"], [[r["name"], r["severity"], r["status"], supp(r["count"])] for r in d]
    if rtype == "COMPLIANCE":
        d = rows(conn, "SELECT i.name, i.state, s.status, s.late_flag, s.submitted_at, s.locked_at FROM ingest.submissions s JOIN ref.institutions i ON i.id=s.institution_id WHERE s.academic_year=:y"
                       + sc.replace("x.", "s.") + " ORDER BY i.name", y=year, **sp)
        return "Submission compliance", ["Institution", "State", "Status", "Late (extension)", "Submitted", "Locked"], \
            [[r["name"], r["state"], r["status"], "Yes" if r["late_flag"] else "No", r["submitted_at"].date().isoformat() if r["submitted_at"] else "", r["locked_at"].date().isoformat() if r["locked_at"] else ""] for r in d]
    if rtype == "TRUST_INDEX":
        d = rows(conn, "SELECT DISTINCT ON (x.institution_id) i.name, i.state, x.score, x.band, x.config_version FROM intel.trust_scores x JOIN ref.institutions i ON i.id=x.institution_id WHERE x.academic_year=:y"
                       + sc + " ORDER BY x.institution_id, x.computed_date DESC", y=year, **sp)
        d.sort(key=lambda r: r["score"])
        return "Trust Index (review aid, not a verdict; triggers no penalty)", ["Institution", "State", "Score", "Band", "Config"], [[r["name"], r["state"], float(r["score"]), r["band"], r["config_version"]] for r in d]
    if rtype == "CLAIM_ACTIVITY":
        d = rows(conn, "SELECT x.day, sum(x.claims) AS claims, sum(x.conflicts) AS conflicts, sum(x.seat_limit) AS seat_limit, sum(x.invalid) AS invalid FROM agg.claim_daily x JOIN ref.institutions i ON i.id=x.institution_id "
                       "WHERE x.day >= (current_date - 90)" + sc + " GROUP BY x.day ORDER BY x.day", **sp)
        return "Claim activity (last 90 days)", ["Day", "Claims", "Conflicts", "Seat-limit hits", "Invalid"], [[r["day"].isoformat(), supp(int(r["claims"])), supp(int(r["conflicts"])), supp(int(r["seat_limit"])), supp(int(r["invalid"]))] for r in d]
    if rtype == "INSTITUTION_PACK":
        out = []
        for t in ("SEAT_UTILISATION", "VERIFICATION_STATUS", "FINDINGS", "TRUST_INDEX"):
            ti, cols, rr = build(conn, p, t, year)
            out.append([ti, "", ""])
            out += [[ti, cols[0], " · ".join(str(c) for c in r[1:])] if False else [r[0], cols[-1] if len(cols) < 3 else "", " | ".join(f"{c}: {v}" for c, v in zip(cols[1:], r[1:]))] for r in rr]
        return "Institution summary pack", ["Section / item", "", "Details"], out
    if rtype == "ENROLMENT_EXPORT":
        if not p.institution_id or "enrolments:manage" not in p.perms and "students:read" not in p.perms:
            raise ApiError(403, "FORBIDDEN", "Not permitted", "Only an institution can export its own student-level list.")
        d = rows(conn, "SELECT e.enrolment_ref, e.institution_student_id, pr.programme_code, e.bucket_type, e.status, v.overall_state, e.claimed_at, e.admission_date, e.student_id FROM core.enrolments e "
                       "JOIN ref.programmes pr ON pr.id=e.programme_id JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.institution_id=CAST(:i AS uuid) AND e.academic_year=:y ORDER BY e.claimed_at", i=p.institution_id, y=year)
        _, _, dkey = keys.data_key(p.institution_id)
        vault = {str(r["student_id"]): r for r in rows(conn, "SELECT DISTINCT ON (student_id) student_id, data_key_id, nonce, ciphertext FROM pii.pii_records WHERE institution_id=CAST(:i AS uuid) AND ciphertext IS NOT NULL ORDER BY student_id, version DESC", i=p.institution_id)}
        out = []
        for r in d:
            v = vault.get(str(r["student_id"]))
            pii = crypto.decrypt(dkey, v["nonce"], v["ciphertext"], f"{r['student_id']}|{p.institution_id}".encode()) if v else {}
            out.append([r["enrolment_ref"], r["institution_student_id"], pii.get("name", ""), pii.get("dob", ""), r["programme_code"], r["bucket_type"], r["status"], r["overall_state"],
                        r["claimed_at"].date().isoformat(), r["admission_date"].isoformat() if r["admission_date"] else ""])
        audit.log(conn, "report.enrolment_export", entity_type="report", actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"rows": len(out), "student_level": True})
        return "Enrolment export (student-level; handle as personal data)", ["Reference", "Student ID", "Name", "Date of birth", "Programme", "Bucket", "Status", "Verification", "Claimed", "Admitted"], out
    raise ApiError(422, "VALIDATION_ERROR", "Unknown report", f"Use one of {sorted(TYPES)}.")


# ------------------------------------------------------------------ renderers
def _storage(job_id, ext: str) -> Path:
    d = Path(get_settings().storage_dir)
    d = (d if d.is_absolute() else ROOT / d) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.{ext}"


def _verify_url(ref: str) -> str:
    return f"{os.environ.get('PUBLIC_API_URL', 'http://localhost:8000')}/api/v1/public/reports/{ref}"


def _stamp(meta: dict) -> list[str]:
    return [f"Rule set version: v{meta['rule_set_version']}", f"Cut-off: {meta['cutoff']}", f"Generated by: {meta['generator']}", f"Generated at: {meta['generated_at']}",
            f"Academic year: {meta['year']}", DEFINITIONS, f"Verify: {meta['verify_url']}"]


def render_csv(title, cols, data, meta) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    for line in [title] + _stamp(meta):
        w.writerow(["# " + str(line)])
    w.writerow(cols)
    for r in data:
        w.writerow([safe(v) for v in r])
    return ("﻿" + out.getvalue()).encode("utf-8")


def render_xlsx(title, cols, data, meta) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Report")
    b = wb.add_format({"bold": True}); hdr = wb.add_format({"bold": True, "bg_color": "#E3F4EC", "border": 1})
    ws.write_string(0, 0, title, b)
    for i, line in enumerate(_stamp(meta), start=1):
        ws.write_string(i, 0, str(line))
    r0 = len(_stamp(meta)) + 2
    for j, c in enumerate(cols):
        ws.write_string(r0, j, c, hdr)
    for i, r in enumerate(data, start=r0 + 1):
        for j, v in enumerate(r):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ws.write_number(i, j, v)
            else:
                ws.write_string(i, j, str(safe(v)) if v is not None else "")
    ws.set_column(0, len(cols) - 1, 22)
    ws.freeze_panes(r0 + 1, 0)
    qr = io.BytesIO(); segno.make(meta["verify_url"], error="m").save(qr, kind="png", scale=4)
    ws.insert_image(0, max(len(cols), 4) + 1, "qr.png", {"image_data": qr})
    wb.close()
    return buf.getvalue()


def render_pdf(title, cols, data, meta) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm, title=title)
    st = getSampleStyleSheet()
    qr = io.BytesIO(); segno.make(meta["verify_url"], error="m").save(qr, kind="png", scale=3); qr.seek(0)
    head = Table([[Paragraph(f"<b>{title}</b><br/><font size=8>AICTE · APAAR ShikshaSetu · synthetic prototype data</font>", st["Title"]), Image(qr, 24 * mm, 24 * mm)]], colWidths=[220 * mm, 30 * mm])
    story = [head, Paragraph("<br/>".join(str(x).replace("&", "&amp;").replace("<", "&lt;") for x in _stamp(meta)), st["BodyText"]), Spacer(1, 4 * mm)]
    cap = 1500
    cells = [[Paragraph(f"<b>{c}</b>", st["BodyText"]) for c in cols]] + [[Paragraph(str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;"), st["BodyText"]) for v in r] for r in data[:cap]]
    t = Table(cells, repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3F4EC")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)
    if len(data) > cap:
        story.append(Paragraph(f"Showing the first {cap} of {len(data)} rows. Use CSV or XLSX for the full list.", st["BodyText"]))
    story.append(Paragraph("A signature proves integrity, not truth. APAAR is never mandatory for being counted. Aadhaar is never stored or printed.", st["BodyText"]))
    doc.build(story)
    return buf.getvalue()


RENDER = {"CSV": (render_csv, "csv", "text/csv"), "XLSX": (render_xlsx, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), "PDF": (render_pdf, "pdf", "application/pdf")}


# ------------------------------------------------------------------ API operations
def request_report(p: Principal, rtype: str, fmt: str, year: str | None) -> dict:
    if rtype not in TYPES or fmt not in FORMATS:
        raise ApiError(422, "VALIDATION_ERROR", "Unknown report or format", f"Types: {sorted(TYPES)}; formats: {list(FORMATS)}.")
    if rtype == "ENROLMENT_EXPORT" and not p.institution_id:
        raise ApiError(403, "FORBIDDEN", "Not permitted", "AICTE cannot export student-level data; it is available only to the institution (or under a case-gated grant in the case workspace).")
    if rtype == "INSTITUTION_PACK" and not p.institution_id:
        raise ApiError(403, "FORBIDDEN", "Not permitted", "The summary pack is an institution report.")
    jid, ref = uuid7(), secrets.token_urlsafe(16)
    with tx(p.db_ctx()) as conn:
        conn.execute(text("INSERT INTO ops.report_jobs (id, requested_by, institution_id, type, params, format, ref, title) VALUES (:id,CAST(:u AS uuid),CAST(:i AS uuid),:t,CAST(:p AS jsonb),:f,:r,:ti)"),
                     {"id": jid, "u": p.id, "i": p.institution_id, "t": rtype, "p": dumps({"year": year or claims.current_academic_year()}), "f": fmt, "r": ref, "ti": TYPES[rtype]})
        audit.log(conn, "report.requested", entity_type="report", entity_id=jid, actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"type": rtype, "format": fmt})
    ops.enqueue("report", "report.generate", {"job_id": str(jid)}, dedupe_key=f"report:{jid}", institution_id=p.institution_id)
    return {"id": str(jid), "status": "QUEUED", "ref": ref}


@ops.handler("report.generate")
def generate(payload: dict) -> dict:
    jid = payload["job_id"]
    with owner_tx() as conn:
        j = row(conn, "SELECT * FROM ops.report_jobs WHERE id=CAST(:j AS uuid)", j=jid)
        if not j or j["status"] not in ("QUEUED", "FAILED"):
            return {"skipped": True}
        conn.execute(text("UPDATE ops.report_jobs SET status='RUNNING' WHERE id=:j"), {"j": j["id"]})
    from .uploads import principal_for_user
    p = principal_for_user(str(j["requested_by"]))
    year = j["params"]["year"]
    try:
        with tx(p.db_ctx(), statement_timeout_ms=60000) as conn:
            title, cols, data = build(conn, p, j["type"], year)
            rs = row(conn, "SELECT version_no FROM rules.rule_sets WHERE status='ACTIVE'")
            cut = scalar(conn, "SELECT cutoff_at FROM ref.submission_windows WHERE academic_year=:y ORDER BY opens_at DESC LIMIT 1", y=year)
            gen = scalar(conn, "SELECT username FROM iam.users WHERE id=:u", u=j["requested_by"])
        meta = {"rule_set_version": rs["version_no"] if rs else 0, "cutoff": cut.date().isoformat() if cut else "n/a", "generator": gen, "generated_at": now().strftime("%Y-%m-%d %H:%M UTC"), "year": year, "verify_url": _verify_url(j["ref"])}
        fn, ext, _ = RENDER[j["format"]]
        blob = fn(TYPES[j["type"]] if False else title, cols, data, meta)
        path = _storage(jid, ext); path.write_bytes(blob)
        with owner_tx() as conn:
            conn.execute(text("UPDATE ops.report_jobs SET status='DONE', file_key=:k, sha256=:h, rule_set_version=:v, finished_at=:t, title=:ti WHERE id=CAST(:j AS uuid)"),
                         {"k": str(path.name), "h": hashlib.sha256(blob).hexdigest(), "v": meta["rule_set_version"], "t": now(), "ti": title, "j": jid})
        return {"rows": len(data), "bytes": len(blob)}
    except Exception as exc:  # noqa: BLE001
        with owner_tx() as conn:
            conn.execute(text("UPDATE ops.report_jobs SET status='FAILED', error=:e, finished_at=:t WHERE id=CAST(:j AS uuid)"), {"e": f"{type(exc).__name__}: {exc}"[:300], "t": now(), "j": jid})
        raise


def list_jobs(p: Principal) -> list[dict]:
    with tx(p.db_ctx()) as conn:
        d = rows(conn, "SELECT id, type, format, status, title, sha256, ref, created_at, finished_at, error FROM ops.report_jobs WHERE requested_by=CAST(:u AS uuid) ORDER BY created_at DESC LIMIT 50", u=p.id)
    return [{**x, "id": str(x["id"])} for x in d]


def file_for(p: Principal, jid: str) -> tuple[Path, str, str]:
    with tx(p.db_ctx()) as conn:
        j = row(conn, "SELECT id, format, status, file_key, type FROM ops.report_jobs WHERE id=CAST(:j AS uuid) AND requested_by=CAST(:u AS uuid)", j=jid, u=p.id)
        if not j:
            raise ApiError(404, "NOT_FOUND", "Not found", "No such report.")
        if j["status"] != "DONE":
            raise ApiError(409, "NOT_READY", "Not ready", f"The report is {j['status']}.", retryable=True)
        audit.log(conn, "report.downloaded", entity_type="report", entity_id=j["id"], actor_id=p.id, actor_role=p.role, institution_id=p.institution_id, request_id=p.request_id, payload={"type": j["type"]})
    fn, ext, mime = RENDER[j["format"]]
    return _storage(jid, ext), mime, f"{j['type'].lower()}.{ext}"
