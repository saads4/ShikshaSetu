"""Duplicate and transfer engine (REQ-DUP): privacy-preserving features, blocking, Bloom-filter Dice scoring, timeline classes.

Raw names/DOBs never leave the tenant vault except in this worker's memory; the shared tables hold keyed hashes and Bloom encodings.
Static weights (DEC-12); Splink-style training is deferred. Bloom-filter privacy review is a documented pre-production requirement (R-08).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from sqlalchemy import text

from . import audit, crypto, identity, keys, ops, verification
from .core import ApiError, dumps, now, uuid7
from .db import owner_tx, row, rows, scalar

log = logging.getLogger("shikshasetu.matching")
BITS, HASHES = 256, 3
MAX_BLOCK = 50
W = {"name": 0.45, "dob": 0.30, "parent": 0.25}
REVIEW_MIN, AUTO_LINK_MIN = 0.80, 0.97


def _grams(s: str) -> list[str]:
    s = f" {s} "
    return [s[i:i + 2] for i in range(len(s) - 1)]


def bloom(purpose: str, text_: str) -> bytes:
    bits = 0
    for g in _grams(text_):
        for i in range(HASHES):
            h = crypto.keyed_hash(f"bloom:{purpose}", f"{i}|{g}")
            bits |= 1 << (int.from_bytes(h[:4], "big") % BITS)
    return bits.to_bytes(BITS // 8, "big")


def dice(a: bytes, b: bytes) -> float:
    x, y = int.from_bytes(bytes(a), "big"), int.from_bytes(bytes(b), "big")
    tot = x.bit_count() + y.bit_count()
    return round(2 * (x & y).bit_count() / tot, 3) if tot else 0.0


def features_for(pii: dict, identity_key: bytes) -> dict:
    name = identity.normalise_name(pii.get("name") or "")
    toks = name.split() or [""]
    first, last = identity.phonetic(toks[0]), identity.phonetic(toks[-1])
    dob = pii.get("dob") or ""
    parent = identity.normalise_name(pii.get("parent_name") or "")
    ptoks = parent.split()
    contacts = {}
    for k in ("mobile", "registry_mobile", "email"):
        if pii.get(k):
            contacts[k] = crypto.keyed_hash("contact", str(pii[k]).strip().lower()).hex()
    return {"k1": identity_key, "k2": crypto.keyed_hash("k2", f"{first}|{last}|{dob}"), "k3": crypto.keyed_hash("k3", f"{dob}|{identity.phonetic(ptoks[0])}") if ptoks else None,
            "bn": bloom("name", name), "bd": bloom("dob", dob), "bp": bloom("parent", parent) if parent else None, "contacts": contacts}


def build_features(ids: list[str]) -> int:
    """Decrypt vault records in memory, build features, store them (no raw values stored)."""
    if not ids:
        return 0
    with owner_tx() as conn:
        data = rows(conn, "SELECT e.id, e.student_id, e.institution_id, e.academic_year, e.identity_key FROM core.enrolments e WHERE e.id = ANY(CAST(:ids AS uuid[]))", ids=ids)
        vault = {}
        for d in data:
            vault[(str(d["student_id"]), str(d["institution_id"]))] = None
        pii_rows = rows(conn, "SELECT DISTINCT ON (student_id, institution_id) student_id, institution_id, data_key_id, nonce, ciphertext FROM pii.pii_records WHERE ciphertext IS NOT NULL AND "
                              "(student_id, institution_id) IN (SELECT student_id, institution_id FROM core.enrolments WHERE id = ANY(CAST(:ids AS uuid[]))) ORDER BY student_id, institution_id, version DESC", ids=ids)
    dkeys: dict[str, bytes] = {}
    for r in pii_rows:
        kid = str(r["data_key_id"])
        dkeys.setdefault(kid, keys.data_key_by_id(kid))
        vault[(str(r["student_id"]), str(r["institution_id"]))] = crypto.decrypt(dkeys[kid], r["nonce"], r["ciphertext"], f"{r['student_id']}|{r['institution_id']}".encode())
    out = []
    for d in data:
        pii = vault.get((str(d["student_id"]), str(d["institution_id"])))
        if not pii:
            continue
        f = features_for(pii, bytes(d["identity_key"]))
        out.append({"e": d["id"], "i": d["institution_id"], "y": d["academic_year"], "k1": f["k1"], "k2": f["k2"], "k3": f["k3"], "bn": f["bn"], "bd": f["bd"], "bp": f["bp"], "c": dumps(f["contacts"])})
    with owner_tx() as conn:
        for k in range(0, len(out), 1000):
            conn.execute(text("INSERT INTO matching.match_features (enrolment_id, institution_id, academic_year, k1, k2, k3, bloom_name, bloom_dob, bloom_parent, contact_hashes) "
                              "VALUES (:e,:i,:y,:k1,:k2,:k3,:bn,:bd,:bp,CAST(:c AS jsonb)) ON CONFLICT (enrolment_id) DO NOTHING"), out[k:k + 1000])
    return len(out)


def _timeline(a: dict, b: dict, exact_id: bool) -> str:
    if a["institution_id"] == b["institution_id"]:
        return "SAME_INST_READMIT"
    if "EXCEPTION_APPROVED" in (a["concurrency_class"], b["concurrency_class"]):
        return "EXCEPTION"
    if "CONCURRENT_OK" in (a["concurrency_class"], b["concurrency_class"]):
        return "FT_PLUS_ODL"
    live = ("CLAIMED", "ADMITTED")
    if a["academic_year"] == b["academic_year"]:
        if a["status"] in live and b["status"] in live:
            return "OVERLAP_FT"
        return "TRANSFER" if exact_id else "SEQUENTIAL"
    return "SEQUENTIAL"


def generate_candidates(ids: list[str]) -> dict:
    """Blocking on K1/K2/K3 -> pair scoring -> AUTO_LINKED / REVIEW. Blocks above MAX_BLOCK are routed to review, not compared."""
    if not ids:
        return {"pairs": 0}
    with owner_tx() as conn:
        pairs = rows(conn, "SELECT f.enrolment_id AS a, o.enrolment_id AS b FROM matching.match_features f JOIN matching.match_features o ON o.enrolment_id <> f.enrolment_id "
                           "AND (o.k1 = f.k1 OR o.k2 = f.k2 OR (f.k3 IS NOT NULL AND o.k3 = f.k3)) WHERE f.enrolment_id = ANY(CAST(:ids AS uuid[]))", ids=ids)
        per: dict = {}
        for p in pairs:
            per.setdefault(p["a"], set()).add(p["b"])
        skipped = [a for a, s in per.items() if len(s) > MAX_BLOCK]
        uniq = {(min(str(p["a"]), str(p["b"])), max(str(p["a"]), str(p["b"]))) for p in pairs if p["a"] not in skipped}
        if skipped:
            log.warning("matching: %d enrolments exceeded the block size limit and were routed to review", len(skipped))
        if not uniq:
            return {"pairs": 0, "skipped_blocks": len(skipped)}
        need = list({x for pr in uniq for x in pr})
        det = {str(r["id"]): r for r in rows(conn, "SELECT e.id, e.institution_id, e.academic_year, e.status, e.concurrency_class, e.identity_key, e.student_id, pr.level, pr.mode, "
                                                     "f.bloom_name, f.bloom_dob, f.bloom_parent, f.contact_hashes FROM core.enrolments e JOIN ref.programmes pr ON pr.id=e.programme_id "
                                                     "JOIN matching.match_features f ON f.enrolment_id=e.id WHERE e.id = ANY(CAST(:ids AS uuid[]))", ids=need)}
        pairs_done = 0
        out = []
        for a, b in sorted(uniq):
            A, B = det.get(a), det.get(b)
            if not A or not B:
                continue
            exact = bytes(A["identity_key"]) == bytes(B["identity_key"]) or str(A["student_id"]) == str(B["student_id"])
            sn, sd = dice(A["bloom_name"], B["bloom_name"]), dice(A["bloom_dob"], B["bloom_dob"])
            sp = dice(A["bloom_parent"], B["bloom_parent"]) if A["bloom_parent"] and B["bloom_parent"] else None
            w = {k: v for k, v in W.items() if k != "parent" or sp is not None}
            tot = sum(w.values())
            score = round((w["name"] * sn + w["dob"] * sd + (w.get("parent", 0) * (sp or 0))) / tot, 3)
            if exact:
                score = max(score, 0.99)
            if score < REVIEW_MIN:
                continue
            tl = _timeline(A, B, exact)
            shared = [k for k in ("mobile", "registry_mobile", "email") if A["contact_hashes"].get(k) and A["contact_hashes"].get(k) == B["contact_hashes"].get(k)]
            flags = (["EXACT_ID"] if exact else []) + (["SHARED_CONTACT"] if shared else []) + (["PARENT_MISSING"] if sp is None else [])
            if tl in ("EXCEPTION", "FT_PLUS_ODL") or (tl == "TRANSFER" and exact):
                status = "AUTO_LINKED"
            elif tl == "SEQUENTIAL" and score >= AUTO_LINK_MIN:
                status = "AUTO_LINKED"
            else:
                status = "REVIEW"
            out.append({"id": uuid7(), "a": a, "b": b, "ia": A["institution_id"], "ib": B["institution_id"], "s": score, "fs": dumps({"name": sn, "dob": sd, "parent": sp, "weights": w, "exact_identity": exact,
                        "levels": [A["level"], B["level"]], "years": [A["academic_year"], B["academic_year"]], "modes": [A["mode"], B["mode"]]}), "tl": tl, "st": status, "fl": flags})
        if out:
            conn.execute(text("INSERT INTO matching.match_candidates (id, enrolment_a, enrolment_b, institution_a, institution_b, score, field_scores, timeline_class, status, flags) "
                              "VALUES (:id, CAST(:a AS uuid), CAST(:b AS uuid), :ia, :ib, :s, CAST(:fs AS jsonb), :tl, :st, :fl) ON CONFLICT (enrolment_a, enrolment_b) DO NOTHING"), out)
        pairs_done = len(out)
    return {"pairs": pairs_done, "skipped_blocks": len(skipped)}


def process_pending(limit: int = 2000) -> dict:
    with owner_tx() as conn:
        ids = [str(r["id"]) for r in rows(conn, "SELECT e.id FROM core.enrolments e WHERE NOT EXISTS (SELECT 1 FROM matching.match_features f WHERE f.enrolment_id=e.id) "
                                                  "ORDER BY e.claimed_at LIMIT :n", n=limit)]
    built = build_features(ids)
    cand = generate_candidates(ids)
    return {"features": built, **cand}


def process_all() -> dict:
    total = {"features": 0, "pairs": 0}
    while True:
        r = process_pending(3000)
        total["features"] += r["features"]; total["pairs"] += r["pairs"]
        if r["features"] == 0:
            return total


@ops.handler("matching.process")
def _handler(_: dict) -> dict:
    return process_pending(500)


@ops.handler("transfers.orphan_scan")
def orphan_scan(_: dict) -> dict:
    """REQ-DUP-08: an out-record with no matching in-record after orphan_lead_days becomes a lead (not a violation)."""
    with owner_tx() as conn:
        n = conn.execute(text("UPDATE core.transfers SET status='ORPHAN_LEAD' WHERE status='OUT_RECORDED' AND orphan_after < :t"), {"t": now()}).rowcount
    return {"orphan_leads": n}


# ------------------------------------------------------------------ review decisions
def decide(conn, p, cid: str, decision: str, reason: str) -> dict:
    c = row(conn, "SELECT * FROM matching.match_candidates WHERE id=CAST(:c AS uuid) FOR UPDATE", c=cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Not found", "No such candidate.")
    if c["status"] != "REVIEW":
        raise ApiError(409, "INVALID_STATE", "Already decided", f"This pair is {c['status']}.")
    case_id = None
    if decision == "CONFIRM_DUP":
        a = row(conn, "SELECT institution_id, academic_year, enrolment_ref FROM core.enrolments WHERE id=:e", e=c["enrolment_a"])
        b = row(conn, "SELECT institution_id, academic_year, enrolment_ref FROM core.enrolments WHERE id=:e", e=c["enrolment_b"])
        n = scalar(conn, "SELECT nextval('intel.case_no_seq')")
        case_id = uuid7()
        conn.execute(text("INSERT INTO intel.cases (id, case_no, institution_id, type, title, owner_id, status, evidence_pack, opened_by) VALUES (:id, :no, :i, 'DUPLICATE', "
                          "'Confirmed cross-institution duplicate', CAST(:u AS uuid), 'OPEN', CAST(:ev AS jsonb), CAST(:u AS uuid))"),
                     {"id": case_id, "no": f"CASE-{now().year}-{n:04d}", "i": a["institution_id"], "u": p.id,
                      "ev": dumps({"pair": cid, "score": float(c["score"]), "timeline": c["timeline_class"], "enrolments": [a["enrolment_ref"], b["enrolment_ref"]]})})
        for e_id, x in ((c["enrolment_a"], a), (c["enrolment_b"], b)):
            conn.execute(text("INSERT INTO rules.findings (id, institution_id, academic_year, rule_code, enrolment_id, severity, message, evidence, suggested_fix) VALUES "
                              "(:id,:i,:y,'DUP-CONFIRMED',:e,'Error','AICTE confirmed this student holds two overlapping seats.',CAST(:ev AS jsonb),CAST(:fx AS jsonb))"),
                         {"id": uuid7(), "i": x["institution_id"], "y": x["academic_year"], "e": e_id, "ev": dumps({"case": str(case_id)}),
                          "fx": dumps({"fix": "Release the seat or document the exception; respond on the case."})})
    conn.execute(text("UPDATE matching.match_candidates SET status=:s, decided_by=CAST(:u AS uuid), decided_at=:t, decision_reason=:r, case_id=:c WHERE id=:id"),
                 {"s": "CONFIRMED_DUP" if decision == "CONFIRM_DUP" else "DISMISSED", "u": p.id, "t": now(), "r": reason, "c": case_id, "id": c["id"]})
    audit.log(conn, "duplicate.decided", entity_type="match_candidate", entity_id=c["id"], actor_id=p.id, actor_role=p.role, justification=reason, request_id=p.request_id,
              payload={"decision": decision, "score": float(c["score"]), "label_stored": True})
    return {"id": cid, "status": "CONFIRMED_DUP" if decision == "CONFIRM_DUP" else "DISMISSED", "case_id": str(case_id) if case_id else None}
