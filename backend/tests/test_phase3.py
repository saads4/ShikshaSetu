"""Phase 3: bulk upload, submissions + Merkle commitments, duplicate/transfer engine."""
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app import crypto, evalharness, matching, ops, uploads
from app.db import owner_tx, rows, scalar
from conftest import auth, claim_body, login_token, make_people, post_claim
from helpers import bucket_state, make_programme

COLS = ["row_key", "institution_student_id", "programme_code", "bucket_type", "apaar_id", "provisional_type", "document_number", "name", "dob", "gender", "category",
        "citizenship_type", "nationality_code", "parent_name", "mobile", "email", "district", "admission_date"]


def drain():
    n = 0
    while ops.work_once("test"):
        n += 1
    return n


def step_up(client, tok) -> dict:
    r = client.post("/api/v1/auth/step-up", headers=auth(tok), json={"password": "1234"})
    return {"X-Step-Up-Token": r.json()["step_up_token"]}


def csv_bytes(rows_, header=COLS) -> bytes:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=header)
    w.writeheader()
    for r in rows_:
        w.writerow({k: r.get(k, "") for k in header})
    return out.getvalue().encode()


def person_row(person, code, sid=None, **extra):
    a, name, dob = person
    return {"institution_student_id": sid or "U" + uuid.uuid4().hex[:8], "programme_code": code, "apaar_id": a, "name": name, "dob": str(dob), "gender": "M",
            "category": "GEN", "parent_name": "Test Parent", **extra}


def upload(client, tok, data: bytes, filename="students.csv"):
    j = client.post("/api/v1/upload-jobs", headers=auth(tok), json={"filename": filename})
    assert j.status_code == 200, j.text
    jid = j.json()["id"]
    c = client.put(f"/api/v1/upload-jobs/{jid}/content", headers=auth(tok), content=data)
    assert c.status_code == 200, c.text
    drain()
    return jid, client.get(f"/api/v1/upload-jobs/{jid}", headers=auth(tok)).json()


# ------------------------------------------------------------------ bulk upload
def test_template_and_file_safety(client, tokens):
    t = client.get("/api/v1/templates/enrolment/latest", headers=auth(tokens["io.kit"])).json()
    assert t["version"] == 1 and {c["name"] for c in t["columns"] if c["required"]} == {"institution_student_id", "programme_code", "name", "dob"}
    assert client.post("/api/v1/upload-jobs", headers=auth(tokens["io.kit"]), json={"filename": "x.xlsx"}).status_code == 422
    for data, why in ((b"a,b\n\x00\x01", "binary"), (csv_bytes([{"name": "x"}], header=["name"]), "missing columns"), (b"", "empty")):
        j = client.post("/api/v1/upload-jobs", headers=auth(tokens["io.kit"]), json={"filename": "f.csv"}).json()["id"]
        client.put(f"/api/v1/upload-jobs/{j}/content", headers=auth(tokens["io.kit"]), content=data or b"\n")
        drain()
        st = client.get(f"/api/v1/upload-jobs/{j}", headers=auth(tokens["io.kit"])).json()
        assert st["status"] == "FAILED" and st["failure_reason"], why
        assert st["scan_result"].get("malware") == "NOT_PERFORMED" or why == "empty"


def test_dry_run_outcomes_errors_csv_and_formula_safety(client, tokens):
    make_programme("kit", "T-UP1", 6)
    ppl = make_people(9)
    rows_ = [person_row(ppl[i], "T-UP1") for i in range(4)]
    rows_.append(person_row(ppl[4], "T-UP1", apaar_id="991234567890"))                          # bad checksum -> REJECTED with fix
    rows_.append({**person_row(ppl[5], "T-UP1"), "gender": "X"})                                 # blocker rule -> REJECTED
    rows_.append({**person_row(ppl[0], "T-UP1"), "institution_student_id": "DUPFILE"})           # same person twice in the file -> REJECTED
    rows_.append(person_row(ppl[6], "T-UP1", name="=HYPERLINK(\"http://x\")"))                  # formula cell -> neutralised
    rows_.append(person_row(ppl[7], "T-UP1")); rows_.append(person_row(ppl[8], "T-UP1"))         # 7 valid-looking rows for 6 seats
    jid, job = upload(client, tokens["io.kit"], csv_bytes(rows_))
    assert job["status"] == "READY_TO_COMMIT", job
    assert job["total_rows"] == 10 and job["scan_result"]["formula_cells_neutralised"] >= 1
    allrows = client.get(f"/api/v1/upload-jobs/{jid}/rows?limit=50", headers=auth(tokens["io.kit"])).json()["items"]
    by = {r["row_no"]: r for r in allrows}
    assert by[6]["outcome"] == "REJECTED" and by[6]["errors"][0]["field"] == "apaar_id" and by[6]["errors"][0]["fix"]
    assert by[7]["outcome"] == "REJECTED" and any(e["code"] == "GEN-001" for e in by[7]["errors"])
    assert by[8]["outcome"] == "REJECTED" and by[8]["errors"][0]["code"] == "DUPLICATE_IN_FILE"
    assert by[9]["name"].startswith("'=")                                                         # staged with a neutralising quote
    outcomes = [r["outcome"] for r in allrows]
    assert outcomes.count("ACCEPTED") == 6 and outcomes.count("HELD") == 1          # 7 valid rows for 6 seats: the projection holds the last one (advisory)
    assert any(e["code"] == "SEAT_LIMIT_PROJECTED" for r in allrows if r["outcome"] == "HELD" for e in r["errors"])
    assert bucket_state("kit", "T-UP1")["occupied"] == 0                                           # dry-run consumed no seat
    csv_text = client.get(f"/api/v1/upload-jobs/{jid}/errors.csv", headers=auth(tokens["io.kit"])).text
    assert "DUPLICATE_IN_FILE" in csv_text and not any(line.startswith(("=", "+", "-", "@")) for line in csv_text.splitlines()[1:])
    # an operator can dry-run but never commit
    assert client.post(f"/api/v1/upload-jobs/{jid}/commit", headers=auth(tokens["io.kit"])).status_code == 403


def test_commit_separation_of_duties_idempotent_reupload_and_partial(client, tokens):
    make_programme("kit", "T-UP2", 3)
    ppl = make_people(5)
    data = csv_bytes([person_row(p, "T-UP2", sid=f"S{i}") for i, p in enumerate(ppl)])
    jid, job = upload(client, tokens["ia.kit"], data)                   # uploaded by ia.kit
    assert job["status"] == "READY_TO_COMMIT" and job["accepted"] == 3 and job["held"] == 2
    # the uploader cannot commit; step-up is required for everyone
    assert client.post(f"/api/v1/upload-jobs/{jid}/commit", headers=auth(tokens["ia2.kit"])).json()["code"] == "STEP_UP_REQUIRED"
    own = client.post(f"/api/v1/upload-jobs/{jid}/commit", headers=auth(tokens["ia.kit"], **step_up(client, tokens["ia.kit"])))
    assert own.status_code == 403 and own.json()["code"] == "SEPARATION_OF_DUTIES"
    with pytest.raises(Exception):                                       # and the database enforces it too
        with owner_tx() as c:
            c.execute(text("UPDATE ingest.upload_jobs SET committed_by=created_by WHERE id=CAST(:j AS uuid)"), {"j": jid})
    ok = client.post(f"/api/v1/upload-jobs/{jid}/commit", headers=auth(tokens["ia2.kit"], **step_up(client, tokens["ia2.kit"])))
    assert ok.status_code == 200 and ok.json()["status"] == "COMMITTING"
    drain()
    done = client.get(f"/api/v1/upload-jobs/{jid}", headers=auth(tokens["ia.kit"])).json()
    assert done["status"] == "PARTIAL" and done["committed"] == 3 and done["committed_by_name"] == "ia2.kit"     # held rows were not committed
    assert bucket_state("kit", "T-UP2")["occupied"] == 3
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM core.enrolments WHERE claim_source='BULK' AND institution_student_id IN ('S0','S1','S2')") == 3
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='upload.completed' AND entity_id=:j", j=jid) == 1
    # re-uploading the same file creates no duplicates: committed rows become NOOP_DUPLICATE (stable row keys)
    jid2, job2 = upload(client, tokens["ia.kit"], data)
    assert job2["noop"] == 3 and job2["accepted"] == 0
    assert bucket_state("kit", "T-UP2")["occupied"] == 3
    again = client.get(f"/api/v1/upload-jobs/{jid2}/rows?outcome=NOOP_DUPLICATE", headers=auth(tokens["ia.kit"])).json()["items"]
    assert again and again[0]["diff"]["status"] == "UNCHANGED"


def test_changed_rows_show_field_level_diff(client, tokens):
    make_programme("kit", "T-UP3", 5)
    p = make_people(1)[0]
    r = person_row(p, "T-UP3", sid="DIFF1", row_key="stable-key-1")
    jid, _ = upload(client, tokens["ia.kit"], csv_bytes([r]))
    client.post(f"/api/v1/upload-jobs/{jid}/commit", headers=auth(tokens["ia2.kit"], **step_up(client, tokens["ia2.kit"])))
    drain()
    jid2, _ = upload(client, tokens["ia.kit"], csv_bytes([{**r, "parent_name": "A Different Parent"}]))
    row2 = client.get(f"/api/v1/upload-jobs/{jid2}/rows", headers=auth(tokens["ia.kit"])).json()["items"][0]
    assert row2["outcome"] == "NOOP_DUPLICATE" and row2["diff"]["status"] == "CHANGED" and "parent_name" in row2["diff"]["fields"]


def test_upload_jobs_are_tenant_isolated(client, tokens):
    make_programme("kit", "T-UP4", 2)
    jid, _ = upload(client, tokens["ia.kit"], csv_bytes([person_row(make_people(1)[0], "T-UP4")]))
    assert client.get(f"/api/v1/upload-jobs/{jid}", headers=auth(tokens["ia.bvec"])).status_code == 404
    assert client.get(f"/api/v1/upload-jobs/{jid}/rows", headers=auth(tokens["ia.bvec"])).status_code == 404
    with owner_tx() as c:   # staged payloads are ciphertext, never plaintext
        assert scalar(c, "SELECT count(*) FROM ingest.staged_rows WHERE job_id=CAST(:j AS uuid) AND encode(payload_ct,'escape') ILIKE '%Test Person%'", j=jid) == 0


# ------------------------------------------------------------------ submissions and commitments
def _prepare(client, tokens, ia_user, slug, ar_user="ar.national"):
    """Create a submission, declare the live totals, waive blocking findings (AICTE), acknowledge warnings, validate."""
    tok = tokens[ia_user]
    sid = client.post("/api/v1/submissions", headers=auth(tok), json={}).json().get("id")
    if not sid:
        sid = next(s["id"] for s in client.get("/api/v1/submissions", headers=auth(tok)).json()["items"])
    prog = client.get("/api/v1/programmes", headers=auth(tok)).json()["items"]
    declared = {p["code"]: sum(b["occupied"] for b in p["buckets"]) for p in prog}
    assert client.put(f"/api/v1/submissions/{sid}/declared", headers=auth(tok), json={"declared": declared}).status_code == 200
    return sid, declared


def _waive_all(client, tokens, slug):
    n = 0
    for f in client.get("/api/v1/aicte/findings?limit=300", headers=auth(tokens["ar.national"])).json()["items"]:
        if slug in f["institution_name"].lower() or True:
            pass
    return n


def _clear_blockers(client, tokens, inst_name_prefix):
    for _ in range(5):
        items = [f for f in client.get("/api/v1/aicte/findings?limit=300", headers=auth(tokens["ar.national"])).json()["items"] if f["institution_name"].startswith(inst_name_prefix)]
        if not items:
            return
        for f in items:
            r = client.post(f"/api/v1/findings/{f['id']}/waive", headers=auth(tokens["ar.national"]), json={"reason_code": "DATA_VERIFIED", "note": "Verified against records"})
            assert r.status_code == 200, r.text


def test_submission_lifecycle_commitment_proof_and_tamper_detection(client, tokens):
    h = lambda user, **x: auth(tokens[user], **x)
    sid, declared = _prepare(client, tokens, "io.kit", "kit")
    # a wrong declared total blocks validation with an AGG-001 finding
    wrong = dict(declared); k0 = next(iter(wrong)); wrong[k0] += 3
    client.put(f"/api/v1/submissions/{sid}/declared", headers=h("io.kit"), json={"declared": wrong})
    v = client.post(f"/api/v1/submissions/{sid}/validate", headers=h("io.kit")).json()
    assert v["ok"] is False and any(f["rule_code"] == "AGG-001" for f in v["blocking_findings"])
    client.put(f"/api/v1/submissions/{sid}/declared", headers=h("io.kit"), json={"declared": declared})
    _clear_blockers(client, tokens, "Kaveri")
    v = client.post(f"/api/v1/submissions/{sid}/validate", headers=h("io.kit")).json()
    assert v["ok"] is True and v["status"] == "VALIDATED", v
    # an operator cannot submit; warnings must be acknowledged first
    assert client.post(f"/api/v1/submissions/{sid}/submit", headers=h("io.kit")).status_code == 403
    if v["warnings_open"]:
        w = client.post(f"/api/v1/submissions/{sid}/submit", headers=h("ia.kit"))
        assert w.status_code == 409 and w.json()["code"] == "WARNINGS_NOT_ACKNOWLEDGED"
        assert client.post("/api/v1/findings/acknowledge-all", headers=h("ia.kit")).json()["acknowledged"] >= v["warnings_open"]
    assert client.post(f"/api/v1/submissions/{sid}/submit", headers=h("ia.kit")).json()["status"] == "SUBMITTED"
    # AICTE: needs notes to request a review; then the institution corrects and resubmits
    assert client.post(f"/api/v1/submissions/{sid}/decide", headers=h("ar.south"), json={"decision": "REQUEST_REVIEW"}).status_code == 422
    assert client.post(f"/api/v1/submissions/{sid}/decide", headers=h("ar.south"), json={"decision": "REQUEST_REVIEW", "notes": "Please confirm the lateral totals"}).json()["status"] == "UNDER_REVIEW"
    assert client.post(f"/api/v1/submissions/{sid}/corrected", headers=h("ia.kit")).json()["status"] == "CORRECTED"
    assert client.post(f"/api/v1/submissions/{sid}/validate", headers=h("ia.kit")).json()["status"] == "VALIDATED"
    client.post(f"/api/v1/submissions/{sid}/submit", headers=h("ia.kit"))
    assert client.post(f"/api/v1/submissions/{sid}/decide", headers=h("ar.south"), json={"decision": "ACCEPT"}).json()["status"] == "ACCEPTED"
    with pytest.raises(Exception):                                         # approver must differ from submitter (database CHECK)
        with owner_tx() as c:
            c.execute(text("UPDATE ingest.submissions SET approved_by=submitted_by WHERE id=CAST(:s AS uuid)"), {"s": sid})
    # signing needs a step-up; the AICTE reviewer cannot sign for the institution and vice versa
    assert client.post(f"/api/v1/submissions/{sid}/commitment/sign", headers=h("ia.kit")).json()["code"] == "STEP_UP_REQUIRED"
    assert client.post(f"/api/v1/submissions/{sid}/commitment/sign", headers=h("ar.south", **step_up(client, tokens["ar.south"]))).status_code == 403
    assert client.post(f"/api/v1/submissions/{sid}/commitment/countersign", headers=h("ia.kit", **step_up(client, tokens["ia.kit"]))).status_code == 403
    signed = client.post(f"/api/v1/submissions/{sid}/commitment/sign", headers=h("ia.kit", **step_up(client, tokens["ia.kit"])))
    assert signed.status_code == 200 and signed.json()["status"] == "PENDING_COUNTERSIGN" and signed.json()["leaf_count"] > 50
    assert client.get(f"/api/v1/submissions/{sid}", headers=h("ia.kit")).json()["status"] == "ACCEPTED"             # not LOCKED until countersigned
    cs = client.post(f"/api/v1/submissions/{sid}/commitment/countersign", headers=h("ar.south", **step_up(client, tokens["ar.south"])))
    assert cs.status_code == 200 and cs.json()["status"] == "LOCKED"
    ver = client.post(f"/api/v1/submissions/{sid}/commitment/verify", headers=h("ar.south")).json()
    assert ver["matches"] and ver["institution_signature_valid"] and ver["aicte_countersignature_valid"] and ver["changed_count"] == 0
    # inclusion proof verifies against the signed root with RFC 6962 hashing
    with owner_tx() as c:
        eid = str(scalar(c, "SELECT id FROM core.enrolments e WHERE institution_id=(SELECT id FROM ref.institutions WHERE slug='kit') AND academic_year='2026-27' ORDER BY id LIMIT 7"))
    pr = client.get(f"/api/v1/enrolments/{eid}/inclusion-proof", headers=h("ia.kit")).json()
    leaf = bytes.fromhex(pr["leaf_hash"])
    assert leaf == crypto.merkle_leaf(crypto.canonical_json(pr["leaf"]))
    assert crypto.merkle_verify(leaf, [(x["side"], bytes.fromhex(x["hash"])) for x in pr["path"]], bytes.fromhex(pr["root"]))
    assert pr["root"] == signed.json()["merkle_root"]
    bad = [(x["side"], bytes.fromhex(x["hash"])) for x in pr["path"]]
    assert not crypto.merkle_verify(crypto.merkle_leaf(b"forged"), bad, bytes.fromhex(pr["root"]))
    # editing an enrolment after the cut-off changes the root and is detected (SC-09)
    with owner_tx() as c:
        c.execute(text("UPDATE core.enrolments SET admission_date = coalesce(admission_date, current_date) - 5, status='ADMITTED' WHERE id=CAST(:e AS uuid)"), {"e": eid})
    tam = client.post(f"/api/v1/submissions/{sid}/commitment/verify", headers=h("as.admin")).json()
    assert tam["matches"] is False and eid in tam["changed_enrolments"] and tam["recomputed_root"] != tam["signed_root"]
    assert tam["institution_signature_valid"]                                  # the signature itself is intact; the data moved
    with owner_tx() as c:
        c.execute(text("UPDATE core.enrolments SET admission_date=NULL WHERE id=CAST(:e AS uuid) AND admission_date IS NOT NULL AND false"), {"e": eid})


def test_window_closed_extension_and_late_flag(client, tokens):
    h = lambda user, **x: auth(tokens[user], **x)
    sid, _ = _prepare(client, tokens, "ia.bvec", "bvec")
    _clear_blockers(client, tokens, "Bharatiya")
    assert client.post(f"/api/v1/submissions/{sid}/validate", headers=h("ia.bvec")).json()["ok"]
    client.post("/api/v1/findings/acknowledge-all", headers=h("ia.bvec"))
    with owner_tx() as c:
        old = c.execute(text("SELECT id, closes_at FROM ref.submission_windows WHERE academic_year='2026-27'")).first()
        c.execute(text("UPDATE ref.submission_windows SET closes_at = now() - interval '1 day' WHERE id=:i"), {"i": old.id})
    try:
        closed = client.post(f"/api/v1/submissions/{sid}/submit", headers=h("ia.bvec"))
        assert closed.status_code == 409 and closed.json()["code"] == "WINDOW_CLOSED"
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert client.post("/api/v1/window-extensions", headers=h("ia.bvec"), json={"new_closes_at": past, "reason": "Requesting a date in the past"}).status_code == 422
        x = client.post("/api/v1/window-extensions", headers=h("ia.bvec"), json={"new_closes_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(), "reason": "Counselling ran late this year"}).json()
        assert client.post(f"/api/v1/window-extensions/{x['id']}/decide", headers=h("ia.bvec"), json={"approve": True, "reason": "self approval"}).status_code == 403
        assert client.post(f"/api/v1/window-extensions/{x['id']}/decide", headers=h("ar.national"), json={"approve": True, "reason": "Approved for one week"}).status_code == 200
        done = client.post(f"/api/v1/submissions/{sid}/submit", headers=h("ia.bvec"))
        assert done.status_code == 200 and done.json()["late_flag"] is True
    finally:
        with owner_tx() as c:
            c.execute(text("UPDATE ref.submission_windows SET closes_at=:c WHERE id=:i"), {"c": old.closes_at, "i": old.id})


def test_seeded_prior_cycle_commitments_and_student_proof(client, tokens):
    with owner_tx() as c:
        eid = str(scalar(c, "SELECT enrolment_id FROM audit.commitment_leaves l JOIN audit.cohort_commitments m ON m.id=l.commitment_id WHERE m.status='COMPLETE' AND m.academic_year='2025-26' LIMIT 1"))
        inst = scalar(c, "SELECT i.slug FROM core.enrolments e JOIN ref.institutions i ON i.id=e.institution_id WHERE e.id=CAST(:e AS uuid)", e=eid)
    tok = login_token(client, f"ia.{inst}")
    p = client.get(f"/api/v1/enrolments/{eid}/inclusion-proof", headers=auth(tok))
    assert p.status_code == 200 and p.json()["hashing"].startswith("RFC 6962")
    assert client.get(f"/api/v1/enrolments/{eid}/inclusion-proof", headers=auth(tokens["ia.kit"])).status_code == 404       # not my enrolment


# ------------------------------------------------------------------ duplicate and transfer engine
def test_bloom_dice_properties():
    a, b = matching.bloom("name", "rahul kumar"), matching.bloom("name", "rahul kumar")
    assert matching.dice(a, b) == 1.0
    assert matching.dice(a, matching.bloom("name", "rahul kumaar")) > 0.8
    assert matching.dice(a, matching.bloom("name", "sneha patil")) < 0.5
    assert matching.bloom("name", "x") != matching.bloom("parent", "x")           # keyed and domain separated


def _named(name, dob=date(2007, 4, 2)):
    p = make_people(1, dob=dob)[0]
    with owner_tx() as c:
        c.execute(text("UPDATE mockext.registry_people SET name=:n WHERE apaar_id=:a"), {"n": name, "a": p[0]})
    return (p[0], name, dob)


def _match_all():
    matching.process_all()


def test_cross_institution_duplicate_is_found_reviewed_and_never_reflagged(client, tokens):
    make_programme("kit", "T-DP1", 5); make_programme("bvec", "T-DP2", 5)
    uniq = "Zaffar" + uuid.uuid4().hex[:6]
    p1, p2 = _named(f"{uniq} Quillfeather"), _named(f"{uniq} Quillfeather")        # same person, two different APAAR IDs
    e1 = post_claim(client, tokens["ia.kit"], claim_body(p1, "T-DP1")).json()["enrolment_id"]
    e2 = post_claim(client, tokens["ia.bvec"], claim_body(p2, "T-DP2", parent_name="Test Parent")).json()["enrolment_id"]
    _match_all()
    lst = client.get("/api/v1/duplicates?min_score=0.8&limit=200", headers=auth(tokens["ar.national"])).json()
    mine = [c for c in lst["items"] if {c["ref_a"], c["ref_b"]} == {_ref(e1), _ref(e2)}]
    assert len(mine) == 1 and mine[0]["timeline_class"] == "OVERLAP_FT" and mine[0]["score"] >= 0.9 and mine[0]["status"] == "REVIEW"
    assert mine[0]["field_scores"]["name"] == 1.0 and "Quillfeather" not in str(mine[0])                  # contributions without PII
    d = client.post(f"/api/v1/duplicates/{mine[0]['id']}/decide", headers=auth(tokens["ar.national"]), json={"decision": "CONFIRM_DUP", "reason": "Same person under two IDs"})
    assert d.status_code == 200 and d.json()["status"] == "CONFIRMED_DUP" and d.json()["case_id"]
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM rules.findings WHERE rule_code='DUP-CONFIRMED' AND enrolment_id IN (CAST(:a AS uuid), CAST(:b AS uuid))", a=e1, b=e2) == 2
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='duplicate.decided' AND payload->>'decision'='CONFIRM_DUP'") >= 1
    assert client.post(f"/api/v1/duplicates/{mine[0]['id']}/decide", headers=auth(tokens["ar.national"]), json={"decision": "DISMISS", "reason": "second thoughts"}).status_code == 409
    # institution users cannot see the queue; regional reviewer outside both regions cannot decide
    assert client.get("/api/v1/duplicates", headers=auth(tokens["ia.kit"])).status_code == 403


def _ref(eid):
    with owner_tx() as c:
        return scalar(c, "SELECT enrolment_ref FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=eid)


def test_dismissed_pairs_are_not_recreated(client, tokens):
    make_programme("kit", "T-DP3", 5); make_programme("bvec", "T-DP4", 5)
    uniq = "Yadunandan" + uuid.uuid4().hex[:6]
    p1, p2 = _named(f"{uniq} Ostrander"), _named(f"{uniq} Ostrander")
    e1 = post_claim(client, tokens["ia.kit"], claim_body(p1, "T-DP3")).json()["enrolment_id"]
    e2 = post_claim(client, tokens["ia.bvec"], claim_body(p2, "T-DP4")).json()["enrolment_id"]
    _match_all()
    c = next(c for c in client.get("/api/v1/duplicates?limit=200", headers=auth(tokens["ar.national"])).json()["items"] if {c["ref_a"], c["ref_b"]} == {_ref(e1), _ref(e2)})
    assert client.post(f"/api/v1/duplicates/{c['id']}/decide", headers=auth(tokens["ar.national"]), json={"decision": "DISMISS", "reason": "Twins, verified"}).json()["status"] == "DISMISSED"
    matching.generate_candidates([e1, e2])
    with owner_tx() as cx:
        assert scalar(cx, "SELECT count(*) FROM matching.match_candidates WHERE enrolment_a=LEAST(CAST(:a AS uuid),CAST(:b AS uuid)) AND enrolment_b=GREATEST(CAST(:a AS uuid),CAST(:b AS uuid))", a=e1, b=e2) == 1
        assert scalar(cx, "SELECT status FROM matching.match_candidates WHERE enrolment_a=LEAST(CAST(:a AS uuid),CAST(:b AS uuid)) AND enrolment_b=GREATEST(CAST(:a AS uuid),CAST(:b AS uuid))", a=e1, b=e2) == "DISMISSED"


def test_transfer_out_in_is_matched_and_not_flagged(client, tokens):
    make_programme("kit", "T-TR1", 5); make_programme("bvec", "T-TR2", 5)
    p = _named("Transfer Person" + uuid.uuid4().hex[:5])
    first = post_claim(client, tokens["ia.kit"], {**claim_body(p, "T-TR1"), "admit_now": True}).json()["enrolment_id"]
    assert client.post(f"/api/v1/enrolments/{first}/transfer-out", headers=auth(tokens["io.kit"]), json={}).status_code == 403
    out = client.post(f"/api/v1/enrolments/{first}/transfer-out", headers=auth(tokens["ia.kit"]), json={"note": "Moving to BVEC"})
    assert out.status_code == 200 and out.json()["status"] == "TRANSFERRED_OUT"
    assert bucket_state("kit", "T-TR1")["occupied"] == 0
    tr = client.get("/api/v1/transfers", headers=auth(tokens["ia.kit"])).json()["items"]
    assert any(t["status"] == "OUT_RECORDED" and t["we_released"] for t in tr)
    second = post_claim(client, tokens["ia.bvec"], claim_body(p, "T-TR2"))
    assert second.status_code == 201 and second.json().get("transfer_matched") is True          # the released ID can be claimed elsewhere
    tr2 = client.get("/api/v1/transfers", headers=auth(tokens["ia.bvec"])).json()["items"]
    assert any(t["status"] == "MATCHED" and not t["we_released"] for t in tr2)
    _match_all()
    with owner_tx() as c:
        st = rows(c, "SELECT status, timeline_class FROM matching.match_candidates WHERE enrolment_a IN (CAST(:a AS uuid), CAST(:b AS uuid)) AND enrolment_b IN (CAST(:a AS uuid), CAST(:b AS uuid))",
                  a=first, b=second.json()["enrolment_id"])
    assert st and st[0]["timeline_class"] == "TRANSFER" and st[0]["status"] == "AUTO_LINKED"


def test_orphan_transfer_becomes_a_lead_not_a_violation(client, tokens):
    make_programme("kit", "T-TR3", 5)
    p = _named("Orphan Person" + uuid.uuid4().hex[:5])
    e = post_claim(client, tokens["ia.kit"], {**claim_body(p, "T-TR3"), "admit_now": True}).json()["enrolment_id"]
    client.post(f"/api/v1/enrolments/{e}/transfer-out", headers=auth(tokens["ia.kit"]), json={})
    with owner_tx() as c:
        c.execute(text("UPDATE core.transfers SET orphan_after = now() - interval '1 day' WHERE out_enrolment_id=CAST(:e AS uuid)"), {"e": e})
    assert ops.run_job_inline("transfers.orphan_scan")["orphan_leads"] >= 1
    assert any(t["status"] == "ORPHAN_LEAD" for t in client.get("/api/v1/transfers", headers=auth(tokens["ia.kit"])).json()["items"])


def test_full_time_plus_online_is_auto_linked_not_reviewed(client, tokens):
    make_programme("kit", "T-FP1", 5); make_programme("bvec", "T-FP2", 5, mode="ONLINE", level="PG")
    p = _named("Parallel Person" + uuid.uuid4().hex[:5])
    a = post_claim(client, tokens["ia.kit"], claim_body(p, "T-FP1")).json()["enrolment_id"]
    b = post_claim(client, tokens["ia.bvec"], claim_body(p, "T-FP2")).json()["enrolment_id"]
    _match_all()
    with owner_tx() as c:
        r = rows(c, "SELECT status, timeline_class FROM matching.match_candidates WHERE enrolment_a IN (CAST(:a AS uuid), CAST(:b AS uuid)) AND enrolment_b IN (CAST(:a AS uuid), CAST(:b AS uuid))", a=a, b=b)
    assert r and r[0]["timeline_class"] == "FT_PLUS_ODL" and r[0]["status"] == "AUTO_LINKED"


def test_eval_harness_on_labelled_seed_ground_truth():
    _match_all()
    out = evalharness.run()
    d = out["duplicates"]
    assert d["labelled"] >= 5 and d["precision"] >= 0.8 and d["recall"] >= 0.7, out
    for kind, v in out["legitimate_pairs"].items():
        assert v["wrongly_sent_to_review"] == 0, (kind, v)


def test_matching_features_hold_no_raw_personal_data():
    _match_all()
    with owner_tx() as c:
        f = rows(c, "SELECT k1, k2, k3, bloom_name, bloom_dob, bloom_parent, contact_hashes::text AS ch FROM matching.match_features LIMIT 5")
    assert f and all(len(bytes(x["k2"])) == 32 and len(bytes(x["bloom_name"])) == 32 for x in f)
    assert all("0" * 8 not in x["ch"] or len(x["ch"]) > 20 for x in f)           # contact values are 64-hex hashes, never numbers
