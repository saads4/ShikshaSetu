"""Signed credentials (REQ-CRD): SD-JWT VC (ES256) with selective disclosure, AICTE attestation, Bitstring Status List, certificate PDF.

No student name or ID is inside a credential. "A signature proves integrity and key control, not that the underlying fact is true."
Keys are software keys (SIMULATED KMS/HSM). Only the Enrolment Proof type is issued (DEC-30).
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import os
from datetime import timedelta

import segno
from sqlalchemy import text

from . import audit, crypto, keys, verification
from .claims import _enqueue_outbox
from .core import ApiError, canonical_json, dumps, now, uuid7
from .db import SYSTEM, row, rows, scalar, tx
from .deps import Principal

LIST_ID, LIST_BITS = 1, 131072
BOUNDARY = "A signature proves integrity and key control, not that the underlying academic fact is true."
# Field catalogue behind the disclosure meter (REQ-VER-06): "n of ~40 record fields shared".
FIELD_CATALOGUE = ["student_name", "date_of_birth", "gender", "category", "citizenship", "nationality", "parent_name", "mobile", "email", "district", "apaar_id", "institution_student_id",
                   "enrolment_reference", "institution_name", "institution_aicte_id", "institution_state", "programme_name", "programme_code", "programme_level", "programme_mode", "academic_year",
                   "admission_date", "enrolment_status", "seat_bucket", "approved_seat", "verification_state", "lock1_status", "lock2_status", "lock3_status", "confirmation_channel",
                   "identity_status", "provisional_id", "guardian_name", "withdrawal_reason", "transfer_status", "claimed_at", "credential_ref", "credential_valid_until", "findings", "case_references"]
DISCLOSABLE = ["institution_name", "programme_name", "programme_code", "academic_year", "enrolment_status", "approved_seat"]
BASE_URL = lambda: os.environ.get("PUBLIC_VERIFY_URL", "http://localhost:8001")


def _jws(header: dict, payload: dict, pem: bytes) -> str:
    h, b = crypto.b64u(canonical_json(header)), crypto.b64u(canonical_json(payload))
    return f"{h}.{b}.{crypto.sign_es256(pem, f'{h}.{b}'.encode())}"


def jws_payload(token: str) -> dict:
    return json.loads(crypto.b64u_dec(token.split(".")[1]))


# ------------------------------------------------------------------ status list
def _ensure_list(conn) -> dict:
    r = row(conn, "SELECT * FROM cred.status_lists WHERE id=:i FOR UPDATE", i=LIST_ID)
    if not r:
        conn.execute(text("INSERT INTO cred.status_lists (id, bitstring) VALUES (:i, :b) ON CONFLICT DO NOTHING"), {"i": LIST_ID, "b": bytes(LIST_BITS // 8)})
        r = row(conn, "SELECT * FROM cred.status_lists WHERE id=:i FOR UPDATE", i=LIST_ID)
    return r


def _sign_list(bits: bytes, version: int) -> str:
    kid, _, pem = keys.signing_key("STATUS_LIST")
    payload = {"iss": "shikshasetu-aicte", "sub": f"{BASE_URL()}/public/status-list/{LIST_ID}", "iat": int(now().timestamp()), "version": version,
               "credentialSubject": {"type": "BitstringStatusList", "statusPurpose": "revocation", "encodedList": crypto.b64u(gzip.compress(bits))}}
    return _jws({"alg": "ES256", "kid": kid, "typ": "statuslist+jwt"}, payload, pem)


def _set_bit(conn, index: int) -> None:
    sl = _ensure_list(conn)
    bits = bytearray(sl["bitstring"])
    bits[index // 8] |= 0x80 >> (index % 8)            # bit order per the W3C Bitstring Status List (leftmost bit = index 0)
    v = sl["version"] + 1
    conn.execute(text("UPDATE cred.status_lists SET bitstring=:b, version=:v, signed_jwt=:j, updated_at=:t WHERE id=:i"), {"b": bytes(bits), "v": v, "j": _sign_list(bytes(bits), v), "t": now(), "i": LIST_ID})


def bit_is_set(bits: bytes, index: int) -> bool:
    return bool(bits[index // 8] & (0x80 >> (index % 8)))


def status_list_jwt(conn) -> str:
    sl = _ensure_list(conn)
    if not sl["signed_jwt"]:
        j = _sign_list(bytes(sl["bitstring"]), sl["version"])
        conn.execute(text("UPDATE cred.status_lists SET signed_jwt=:j WHERE id=:i"), {"j": j, "i": LIST_ID})
        return j
    return sl["signed_jwt"]


# ------------------------------------------------------------------ issue
def _build(conn, enrolment: dict, issuer: dict, idx: int) -> tuple[str, str, str, str]:
    kid, _, pem = keys.signing_key("ISSUER", str(enrolment["institution_id"]))
    akid, _, apem = keys.signing_key("AICTE_ATTEST")
    ref = crypto.b64u(os.urandom(16))                                   # 128-bit random, non-sequential (REQ-CRD-03)
    claims = [("institution", {"name": enrolment["institution_name"], "aicte_id": enrolment["aicte_id"]}), ("programme", {"code": enrolment["programme_code"], "name": enrolment["programme_name"]}),
              ("academic_year", enrolment["academic_year"]), ("approved_seat", True), ("status", "ADMITTED")]
    discl, digests = [], []
    for name, val in claims:
        d = crypto.b64u(json.dumps([crypto.b64u(os.urandom(16)), name, val], separators=(",", ":")).encode())
        discl.append(d)
        digests.append(crypto.b64u(crypto.sha256(d.encode())))
    t = now()
    valid_until = t + timedelta(days=365)
    payload = {"iss": f"{BASE_URL()}/issuers/{issuer['slug']}", "iat": int(t.timestamp()), "exp": int(valid_until.timestamp()), "vct": "https://shikshasetu.example/vct/enrolment-proof",
               "credential_ref": ref, "status": {"status_list": {"idx": idx, "uri": f"{BASE_URL()}/public/status-list/{LIST_ID}"}}, "_sd": sorted(digests), "_sd_alg": "sha-256"}
    jwt = _jws({"alg": "ES256", "kid": kid, "typ": "vc+sd-jwt"}, payload, pem)
    digest = crypto.sha256(jwt.encode())
    att = _jws({"alg": "ES256", "kid": akid, "typ": "attestation+jwt"}, {"iss": "shikshasetu-aicte", "sub_digest": crypto.b64u(digest), "credential_ref": ref, "iat": int(t.timestamp())}, apem)
    return ref, jwt + "~" + "~".join(discl) + "~", att, digest.hex(), kid, akid, valid_until


def issue(conn, enrolment_id: str, actor_id: str | None, actor_role: str | None = None, request_id: str | None = None) -> dict:
    e = row(conn, "SELECT e.id, e.student_id, e.institution_id, e.academic_year, e.status, v.overall_state, pr.programme_code, pr.name AS programme_name, i.name AS institution_name, i.aicte_id, i.slug "
                  "FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=e.institution_id "
                  "WHERE e.id=CAST(:e AS uuid)", e=enrolment_id)
    if not e:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such enrolment.")
    if e["overall_state"] != "VERIFIED" or e["status"] != "ADMITTED":
        raise ApiError(409, "NOT_ELIGIBLE", "Not eligible", "A credential is issued only for an ADMITTED enrolment whose three locks all agree (VERIFIED).")
    if scalar(conn, "SELECT count(*) FROM cred.credentials WHERE enrolment_id=:e AND status='VALID'", e=e["id"]):
        raise ApiError(409, "ALREADY_ISSUED", "Already issued", "This enrolment already has a valid credential.")
    sl = _ensure_list(conn)
    idx = sl["next_index"]
    if idx >= LIST_BITS:
        raise ApiError(507, "STATUS_LIST_FULL", "Status list full", "A new status list is needed (not supported in this prototype).")
    conn.execute(text("UPDATE cred.status_lists SET next_index=next_index+1 WHERE id=:i"), {"i": LIST_ID})
    ref, sd_jwt, att, digest, kid, akid, valid_until = _build(conn, e, e, idx)
    cid = uuid7()
    conn.execute(text("INSERT INTO cred.credentials (id, credential_ref, enrolment_id, student_id, institution_id, issuer_kid, aicte_kid, status_list_id, status_list_index, payload_digest, sd_jwt, attestation, "
                      "issued_by, issued_at, valid_until) VALUES (:id,:r,:e,:s,:i,:k,:ak,:l,:ix,:d,:sd,:at,CAST(:u AS uuid),:t,:v)"),
                 {"id": cid, "r": ref, "e": e["id"], "s": e["student_id"], "i": e["institution_id"], "k": kid, "ak": akid, "l": LIST_ID, "ix": idx, "d": digest, "sd": sd_jwt, "at": att, "u": actor_id, "t": now(), "v": valid_until})
    audit.log(conn, "credential.issued", entity_type="credential", entity_id=cid, actor_id=actor_id, actor_role=actor_role, institution_id=e["institution_id"], request_id=request_id, payload={"credential_ref": ref})
    return {"id": str(cid), "credential_ref": ref, "valid_until": valid_until}


def issue_all_verified(p: Principal, limit: int = 500) -> dict:
    with tx(p.db_ctx(), statement_timeout_ms=120000) as conn:
        ids = [str(r["id"]) for r in rows(conn, "SELECT e.id FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.institution_id=CAST(:i AS uuid) AND e.status='ADMITTED' "
                                                "AND v.overall_state='VERIFIED' AND NOT EXISTS (SELECT 1 FROM cred.credentials c WHERE c.enrolment_id=e.id AND c.status='VALID') ORDER BY e.claimed_at LIMIT :n", i=p.institution_id, n=limit)]
        for eid in ids:
            issue(conn, eid, p.id, p.role, p.request_id)
    return {"issued": len(ids)}


def revoke(conn, credential_id: str, reason: str, *, actor_id=None, actor_role=None, request_id=None, status: str = "REVOKED", superseded_by=None) -> dict:
    c = row(conn, "SELECT id, institution_id, status, status_list_index, credential_ref, student_id FROM cred.credentials WHERE id=CAST(:c AS uuid) FOR UPDATE", c=credential_id)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such credential.")
    if c["status"] != "VALID":
        raise ApiError(409, "INVALID_STATE", "Already ended", f"This credential is already {c['status']}.")
    conn.execute(text("UPDATE cred.credentials SET status=:s, revoked_reason=:r, revoked_at=:t, superseded_by=:sb WHERE id=:c"), {"s": status, "r": reason, "t": now(), "sb": superseded_by, "c": c["id"]})
    _set_bit(conn, c["status_list_index"])
    audit.log(conn, "credential.revoked" if status == "REVOKED" else "credential.superseded", entity_type="credential", entity_id=c["id"], actor_id=actor_id, actor_role=actor_role,
              institution_id=c["institution_id"], request_id=request_id, justification=reason, payload={"credential_ref": c["credential_ref"], "status": status})
    _enqueue_outbox(conn, "credential.revoked", "credential", c["id"], c["institution_id"], {"credential_ref": c["credential_ref"], "reason": reason, "academic_year": None})
    return {"status": status}


def revoke_for_enrolment(conn, enrolment_id, reason: str, **kw) -> None:
    """Auto-revocation when an enrolment ends (REQ-CRD-06)."""
    for c in rows(conn, "SELECT id FROM cred.credentials WHERE enrolment_id=:e AND status='VALID'", e=enrolment_id):
        revoke(conn, str(c["id"]), reason, **kw)


def supersede_for_student(conn, student_id, institution_id, reason: str, **kw) -> int:
    n = 0
    for c in rows(conn, "SELECT id FROM cred.credentials WHERE student_id=:s AND institution_id=:i AND status='VALID'", s=student_id, i=institution_id):
        revoke(conn, str(c["id"]), reason, status="SUPERSEDED", **kw)
        n += 1
    return n


# ------------------------------------------------------------------ verification of a credential artefact (used by tests and the public check)
def verify_artifact(sd_jwt: str, attestation: str, jwks: dict[str, dict]) -> dict:
    jwt, *disclosures = sd_jwt.rstrip("~").split("~")
    h, b, s = jwt.split(".")
    header, payload = json.loads(crypto.b64u_dec(h)), json.loads(crypto.b64u_dec(b))
    ok_sig = crypto.verify_es256(jwks[header["kid"]], f"{h}.{b}".encode(), s)
    digests = set(payload["_sd"])
    revealed = {}
    for d in disclosures:
        if crypto.b64u(crypto.sha256(d.encode())) not in digests:
            return {"valid": False, "reason": "disclosure does not match a signed digest"}
        _, name, val = json.loads(crypto.b64u_dec(d))
        revealed[name] = val
    ah, ab, asg = attestation.split(".")
    att = json.loads(crypto.b64u_dec(ab))
    ok_att = crypto.verify_es256(jwks[json.loads(crypto.b64u_dec(ah))["kid"]], f"{ah}.{ab}".encode(), asg) and att["sub_digest"] == crypto.b64u(crypto.sha256(jwt.encode()))
    return {"valid": ok_sig and ok_att, "issuer_signature": ok_sig, "aicte_attestation": ok_att, "claims": revealed, "credential_ref": payload["credential_ref"], "expires": payload["exp"]}


def jwks(conn) -> dict:
    ks = rows(conn, "SELECT kid, purpose, status, public_jwk, institution_id FROM cred.signing_keys WHERE purpose IN ('ISSUER','AICTE_ATTEST','STATUS_LIST','VERIFIER_ANSWER','INSTITUTION_CLAIM','AICTE_COMMIT','SEALER') ORDER BY created_at")
    return {"keys": [{**k["public_jwk"], "kid": k["kid"], "use": "sig"} for k in ks], "status": {k["kid"]: {"purpose": k["purpose"], "status": k["status"]} for k in ks}}


def public_status(conn, ref: str) -> dict:
    c = row(conn, "SELECT c.status, c.issuer_kid, c.valid_until, c.issued_at, i.name AS issuer FROM cred.credentials c JOIN ref.institutions i ON i.id=c.institution_id WHERE c.credential_ref=:r", r=ref)
    if not c:
        return {"result": "NEUTRAL", "message": "No verifiable status is available for this reference.", "boundary": BOUNDARY, "checked_at": now()}
    expired = c["valid_until"] < now()
    return {"result": "EXPIRED" if (c["status"] == "VALID" and expired) else c["status"], "issuer": c["issuer"], "kid": c["issuer_kid"], "issued_at": c["issued_at"], "valid_until": c["valid_until"],
            "checked_at": now(), "boundary": BOUNDARY}


# ------------------------------------------------------------------ certificate (platform template)
def certificate_pdf(conn, credential_id: str) -> tuple[bytes, str]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    c = row(conn, "SELECT c.*, e.institution_student_id, e.academic_year, e.enrolment_ref, pr.name AS programme_name, i.name AS institution_name FROM cred.credentials c JOIN core.enrolments e ON e.id=c.enrolment_id "
                  "JOIN ref.programmes pr ON pr.id=e.programme_id JOIN ref.institutions i ON i.id=c.institution_id WHERE c.id=CAST(:c AS uuid)", c=credential_id)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such credential.")
    pii = verification._vault(conn, str(c["student_id"]), str(c["institution_id"]))
    sig_digest = c["sd_jwt"].split("~")[0].split(".")[2][:22]
    qr_payload = json.dumps({"ref": c["credential_ref"], "iss": c["issuer_kid"], "kid": c["issuer_kid"], "sig": sig_digest, "url": f"{BASE_URL()}/v/{c['credential_ref']}"}, separators=(",", ":"))
    qr = io.BytesIO(); segno.make(qr_payload, error="m").save(qr, kind="png", scale=5); qr.seek(0)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm, title="Enrolment certificate")
    st = getSampleStyleSheet()
    rowsx = [["Name", pii.get("name", "")], ["APAAR ID (synthetic)", pii.get("apaar_id") or "Provisional ID holder"], ["Institution student ID", c["institution_student_id"]], ["Programme", c["programme_name"]],
             ["Institution", c["institution_name"]], ["Academic year", c["academic_year"]], ["Credential reference", c["credential_ref"]], ["Valid until", c["valid_until"].date().isoformat()],
             ["Status at issue", "Enrolment verified (institution · registry · student)"]]
    t = Table(rowsx, colWidths=[55 * mm, 110 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E3F4EC")), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    doc.build([Paragraph("<b>AICTE · APAAR ShikshaSetu</b>", st["Title"]), Paragraph("Certificate of verified enrolment (synthetic prototype data)", st["Heading2"]), Spacer(1, 6 * mm), t, Spacer(1, 8 * mm),
               Image(qr, 38 * mm, 38 * mm), Paragraph("Scan to check the live status. The QR holds only the credential reference, issuer key id, signature digest and a short URL; it contains no personal data.", st["BodyText"]),
               Spacer(1, 4 * mm), Paragraph(BOUNDARY + " APAAR is never mandatory for being counted. Aadhaar is never stored or printed.", st["BodyText"])])
    return buf.getvalue(), f"certificate-{c['enrolment_ref']}.pdf"
