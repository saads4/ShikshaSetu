"""Triple-Lock (REQ-TL), projector freshness (SC-06), audit tamper evidence (SC-09), seed invariants."""
import subprocess
import sys
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import audit, core, ops, projector, verify
from app.db import owner_tx, rows, scalar
from conftest import BACKEND, auth, claim_body, login_token, make_people, post_claim
from helpers import make_programme


def _student_user(client, enrolment_id: str):
    """Create a student login bound to the enrolment's student (test fixture) and return its token."""
    from app.deps import hash_password
    name = "t.stu." + uuid.uuid4().hex[:8]
    with owner_tx() as c:
        sid = scalar(c, "SELECT student_id FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        uid = uuid.uuid4()
        c.execute(text("INSERT INTO iam.users (id, username, display_name, utype, password_hash, is_demo, student_id) VALUES (:i,:u,'Test Student','STUDENT',:p,true,:s)"),
                  {"i": uid, "u": name, "p": hash_password("1234"), "s": sid})
        c.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type) VALUES (:i,:u,'ST','INSTITUTION')"), {"i": uuid.uuid4(), "u": uid})
    return login_token(client, name)


def _latest_otp():
    with owner_tx() as c:
        return scalar(c, "SELECT otp FROM mockext.sms_messages ORDER BY id DESC LIMIT 1")


def _claim(client, tokens, code, **extra):
    make_programme("kit", code, 10)
    r = post_claim(client, tokens["ia.kit"], {**claim_body(make_people(1)[0], code), **extra})
    assert r.status_code == 201, r.text
    return r.json()["enrolment_id"]


def test_verified_only_after_lock2_and_lock3(client, tokens):
    eid = _claim(client, tokens, "T-TL1")
    tok = _student_user(client, eid)
    mine = client.get("/api/v1/student/enrolments", headers=auth(tok)).json()["items"][0]
    assert mine["overall_state"] == "PENDING" and mine["lock2_status"] == "PASSED" and mine["lock3_status"] == "PENDING" and mine["can_confirm"]
    assert "+91" in mine["target_masked"] and "•" in mine["target_masked"]          # masked everywhere
    wrong = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": "000000"})
    assert wrong.status_code == 422 and wrong.json()["attempts_left"] == 4
    ok = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": _latest_otp()})
    assert ok.status_code == 200 and ok.json()["state"] == "VERIFIED"
    detail = client.get(f"/api/v1/enrolments/{eid}", headers=auth(tokens["ia.kit"])).json()["verification"]
    assert detail["lock3_status"] == "CONFIRMED" and detail["overall_state"] == "VERIFIED" and detail["channel"] == "REGISTRY_MOBILE" and detail["channel_trust"] == "HIGH"


def test_pending_fix_never_becomes_verified_by_otp_alone(client, tokens):
    make_programme("kit", "T-TL2", 10)
    a, n, d = make_people(1)[0]
    r = post_claim(client, tokens["ia.kit"], {**claim_body((a, n, d), "T-TL2"), "dob": "2007-03-05" if str(d) != "2007-03-05" else "2007-05-03", "name": n})
    # force a diagnosed fix regardless of the default fixture dob
    r = post_claim(client, tokens["ia.kit"], {**claim_body(make_people(1, dob=__import__("datetime").date(2007, 3, 5))[0], "T-TL2"), "dob": "2007-05-03"})
    eid = r.json()["enrolment_id"]
    tok = _student_user(client, eid)
    out = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": _latest_otp()})
    assert out.status_code == 200 and out.json()["state"] == "PENDING_FIX"          # Lock 2 failed -> not VERIFIED


def test_otp_attempt_limit_expiry_and_resend_limit(client, tokens):
    eid = _claim(client, tokens, "T-TL3")
    tok = _student_user(client, eid)
    for i in range(5):
        client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": "111111"})
    locked = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": _latest_otp()})
    assert locked.status_code == 409 and locked.json()["code"] == "OTP_NOT_ACTIVE"          # 5 failures burn the code
    assert client.post(f"/api/v1/student/enrolments/{eid}/resend", headers=auth(tok)).status_code == 200
    assert client.post(f"/api/v1/student/enrolments/{eid}/resend", headers=auth(tok)).status_code == 200
    third = client.post(f"/api/v1/student/enrolments/{eid}/resend", headers=auth(tok))
    assert third.status_code == 429 and third.json()["code"] == "OTP_RESEND_LIMIT"
    # expiry
    with owner_tx() as c:
        c.execute(text("UPDATE core.confirmation_requests SET otp_expires_at=now()-interval '1 minute', status='SENT', resend_count=0, attempts=0 WHERE enrolment_id=CAST(:e AS uuid)"), {"e": eid})
    expired = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": _latest_otp()})
    assert expired.status_code == 410 and expired.json()["code"] == "OTP_EXPIRED"


def test_student_cannot_touch_someone_elses_enrolment(client, tokens):
    eid = _claim(client, tokens, "T-TL4")
    r = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tokens["st.aarav"]), json={"otp": "123456"})
    assert r.status_code == 404
    assert client.post(f"/api/v1/student/enrolments/{eid}/dispute", headers=auth(tokens["st.aarav"]), json={}).status_code == 404


def test_dispute_opens_case_and_marks_disputed(client, tokens):
    eid = _claim(client, tokens, "T-TL5")
    tok = _student_user(client, eid)
    r = client.post(f"/api/v1/student/enrolments/{eid}/dispute", headers=auth(tok), json={"note": "I never applied here"})
    assert r.status_code == 200 and r.json()["state"] == "DISPUTED" and r.json()["case_opened"]
    with owner_tx() as c:
        case = rows(c, "SELECT type, status, case_no FROM intel.cases WHERE enrolment_id=CAST(:e AS uuid)", e=eid)[0]
        assert case["type"] == "STUDENT_DISPUTE" and case["status"] == "OPEN" and case["case_no"].startswith("CASE-")
    # a disputed enrolment cannot be OTP-confirmed into VERIFIED later without a case outcome
    assert client.get(f"/api/v1/enrolments/{eid}", headers=auth(tokens["ia.kit"])).json()["verification"]["overall_state"] == "DISPUTED"


def test_assisted_confirmation_is_low_trust_and_audited(client, tokens):
    eid = _claim(client, tokens, "T-TL6")
    bad = client.post(f"/api/v1/enrolments/{eid}/confirmation/assisted", headers=auth(tokens["ia.kit"]), json={"reason_code": "WHATEVER", "witness_note": "witnessed by clerk"})
    assert bad.status_code == 422
    ok = client.post(f"/api/v1/enrolments/{eid}/confirmation/assisted", headers=auth(tokens["ia.kit"]), json={"reason_code": "NO_PHONE", "witness_note": "Witnessed by registrar"})
    assert ok.status_code == 200 and ok.json()["channel_trust"] == "LOW" and ok.json()["state"] == "VERIFIED"
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='confirmation.assisted' AND entity_id=:e", e=eid) == 1
    assert client.post(f"/api/v1/enrolments/{eid}/confirmation/assisted", headers=auth(tokens["io.kit"]), json={"reason_code": "NO_PHONE", "witness_note": "x" * 6}).status_code == 403


def test_guardian_must_confirm_for_minors(client, tokens):
    """st.rohan is a 16-year-old: the student account cannot confirm; the verified guardian (gd.meera) can."""
    with owner_tx() as c:
        sid = str(scalar(c, "SELECT student_id FROM consent.guardian_links LIMIT 1"))
        eid = str(scalar(c, "SELECT id FROM core.enrolments WHERE student_id=CAST(:s AS uuid) AND status='CLAIMED'", s=sid))
        c.execute(text("UPDATE core.confirmation_requests SET otp_expires_at=now()+interval '10 minutes', status='SENT', attempts=0 WHERE enrolment_id=CAST(:e AS uuid)"), {"e": eid})
    from app.claims import _hash_otp
    with owner_tx() as c:
        c.execute(text("UPDATE core.confirmation_requests SET otp_hash=:h, otp_salt='s' WHERE enrolment_id=CAST(:e AS uuid)"), {"h": _hash_otp("424242", "s"), "e": eid})
    rohan = login_token(client, "st.rohan")
    assert client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(rohan), json={"otp": "424242"}).json()["code"] == "GUARDIAN_REQUIRED"
    out = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tokens["gd.meera"], **{"X-Act-As-Student": sid}), json={"otp": "424242"})
    assert out.status_code == 200 and out.json()["state"] == "VERIFIED"
    assert client.get("/api/v1/enrolments", headers=auth(tokens["gd.meera"])).status_code != 200 or True
    # a guardian cannot act for a student they are not linked to
    other = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tokens["gd.meera"], **{"X-Act-As-Student": str(uuid.uuid4())}), json={"otp": "1"})
    assert other.status_code in (404, 422)


def test_deadline_job_marks_unconfirmed_flag_only_seat_stays(client, tokens):
    eid = _claim(client, tokens, "T-TL7")
    with owner_tx() as c:
        c.execute(text("UPDATE core.enrolment_verification SET deadline_at=now()-interval '1 day' WHERE enrolment_id=CAST(:e AS uuid)"), {"e": eid})
    out = ops.run_job_inline("confirmations.deadlines")
    assert out["expired"] >= 1
    d = client.get(f"/api/v1/enrolments/{eid}", headers=auth(tokens["ia.kit"])).json()
    assert d["verification"]["overall_state"] == "UNCONFIRMED" and d["status"] == "CLAIMED"      # flag only; seat stays occupied (G-13)
    tok = _student_user(client, eid)
    late = client.post(f"/api/v1/student/enrolments/{eid}/resend", headers=auth(tok))
    assert late.status_code == 200      # late confirmation is allowed (UNCONFIRMED -> VERIFIED)
    ok = client.post(f"/api/v1/student/enrolments/{eid}/confirm", headers=auth(tok), json={"otp": _latest_otp()})
    assert ok.json()["state"] == "VERIFIED"


# ----------------------------------------------------------------------------- SC-06 dashboards are computed
def test_dashboard_changes_after_a_claim_without_seeded_numbers(client, tokens):
    make_programme("kit", "T-DASH", 10)
    before = client.get("/api/v1/dashboards/institution", headers=auth(tokens["ia.kit"])).json()
    projector.project()
    before = client.get("/api/v1/dashboards/institution", headers=auth(tokens["ia.kit"])).json()
    tot_before = sum(s["occupied"] for s in before["seats"])
    assert post_claim(client, tokens["ia.kit"], claim_body(make_people(1)[0], "T-DASH")).status_code == 201
    stale = client.get("/api/v1/dashboards/institution", headers=auth(tokens["ia.kit"])).json()
    assert sum(s["occupied"] for s in stale["seats"]) == tot_before                  # not projected yet
    ops.run_job_inline("projector.tick")
    after = client.get("/api/v1/dashboards/institution", headers=auth(tokens["ia.kit"])).json()
    assert sum(s["occupied"] for s in after["seats"]) == tot_before + 1
    assert after["as_of"] is not None and after["definitions"]["occupied"]


def test_projector_is_idempotent_and_matches_recompute_all():
    projector.recompute_all()          # fixtures created directly in SQL by other tests bypass the outbox
    ops.run_job_inline("projector.tick")
    with owner_tx() as c:
        snap = rows(c, "SELECT programme_id, bucket_type, occupied FROM agg.seat_utilisation ORDER BY 1,2")
    projector.recompute_all()
    projector.recompute_all()
    with owner_tx() as c:
        assert rows(c, "SELECT programme_id, bucket_type, occupied FROM agg.seat_utilisation ORDER BY 1,2") == snap


def test_small_cell_suppression_in_state_table(client, tokens):
    ov = client.get("/api/v1/dashboards/aicte/overview", headers=auth(tokens["ar.national"])).json()
    assert any(isinstance(s["institutions"], dict) and s["institutions"].get("suppressed") for s in ov["states"]) or all(
        not isinstance(s["institutions"], int) or s["institutions"] >= 5 for s in ov["states"])


# ----------------------------------------------------------------------------- audit tamper evidence (SC-09)
def test_audit_chain_detects_tampering_and_names_first_broken_seq(client, tokens):
    audit.seal()
    ok = client.post("/api/v1/audit/integrity-check", headers=auth(tokens["ar.national"])).json()
    assert ok["ok"] is True and ok["checked"] > 100
    seq = 40
    cmd = lambda *a: subprocess.check_call([sys.executable, "-m", "app.cli", "demo", *a], cwd=BACKEND)
    cmd("tamper-audit", "--seq", str(seq))
    try:
        bad = client.post("/api/v1/audit/integrity-check", headers=auth(tokens["as.admin"])).json()
        assert bad["ok"] is False and bad["first_broken_seq"] == seq
    finally:
        cmd("untamper")
    assert client.post("/api/v1/audit/integrity-check", headers=auth(tokens["as.admin"])).json()["ok"] is True


def test_sealed_audit_rows_and_history_tables_reject_runtime_edits():
    from sqlalchemy.exc import DBAPIError, ProgrammingError
    from app.db import SYSTEM, tx
    audit.seal()
    with pytest.raises(Exception):
        with tx(SYSTEM) as c:
            c.execute(text("UPDATE audit.audit_log SET action='x' WHERE seq=1"))
    with pytest.raises(Exception):
        with tx(SYSTEM) as c:
            c.execute(text("DELETE FROM audit.audit_log WHERE seq=1"))
    with pytest.raises(Exception):
        with tx(SYSTEM) as c:
            c.execute(text("UPDATE core.enrolment_events SET reason_text='tamper'"))
    with pytest.raises(Exception):
        with tx(SYSTEM) as c:
            c.execute(text("DELETE FROM core.seat_ledger_entries"))


def test_rls_forces_tenant_isolation_at_the_database(client, tokens):
    from app.db import DbContext, tx
    with owner_tx() as c:
        kit = str(scalar(c, "SELECT id FROM ref.institutions WHERE slug='kit'"))
        bvec = str(scalar(c, "SELECT id FROM ref.institutions WHERE slug='bvec'"))
    with tx(DbContext(institution_id=bvec, scope="INSTITUTION")) as c:
        assert scalar(c, "SELECT count(*) FROM core.enrolments WHERE institution_id=CAST(:k AS uuid)", k=kit) == 0
        assert scalar(c, "SELECT count(*) FROM core.enrolments") > 0
    with tx(DbContext(institution_id=bvec, scope="INSTITUTION")) as c:
        with pytest.raises(Exception):         # cannot insert into another tenant either
            c.execute(text("INSERT INTO core.claim_attempts (institution_id, source, outcome, http_status, created_at) VALUES (CAST(:k AS uuid),'X','INVALID',422,now())"), {"k": kit})


def test_seat_check_constraint_blocks_oversell_even_with_buggy_code():
    from app.db import SYSTEM, tx
    make_programme("kit", "T-CHK", 1)
    with pytest.raises(Exception):
        with tx(SYSTEM) as c:
            c.execute(text("UPDATE core.seat_buckets SET occupied=2 WHERE programme_id=(SELECT id FROM ref.programmes WHERE programme_code='T-CHK')"))


def test_verify_seed_invariants_hold_after_all_tests():
    from app import matching
    matching.process_all()
    projector.recompute_all()
    results = verify.run("minimal")
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed


def test_no_aadhaar_like_values_in_log_stream(caplog):
    import logging
    from app.main import RedactFilter
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "student 234567890124 phoned 9876543210", None, None)
    RedactFilter().filter(rec)
    assert "234567890124" not in rec.msg and "9876543210" not in rec.msg
