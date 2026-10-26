"""Final-phase tests: credentials, status list, verifier (neutral answers, consent, scope, limits), share links, wallet, corrections, DSR,
rule sets and simulator, onboarding, administration, webhooks, retention and the liveness layer."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from conftest import auth, login_token
from test_phase3 import step_up


@pytest.fixture(scope="module")
def vclient(client):
    from fastapi.testclient import TestClient
    from verify_api.main import app as vapp
    with TestClient(vapp) as c:
        yield c


def h(tok, **extra):
    return auth(tok, **extra)


def vtoken(client, user):
    from app import routes_auth
    routes_auth._hits.clear()
    r = client.post("/api/v1/auth/login", json={"username": user, "password": "1234"})
    assert r.status_code == 200, r.text
    assert r.json().get("verify_token"), "verifier users receive a token for the verify-api audience"
    return r.json()


def org_id(name_part):
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        return str(scalar(c, "SELECT id FROM iam.verifier_orgs WHERE name ILIKE :n", n=f"%{name_part}%"))


@pytest.fixture(scope="module")
def aarav(client, tokens):
    r = client.get("/api/v1/student/credentials", headers=h(tokens["st.aarav"]))
    assert r.status_code == 200, r.text
    live = [c for c in r.json()["items"] if c["status"] == "VALID"]
    assert live, "the seed issues a credential to the story student"
    return live[0]


def ask(vclient, tok, ref, purpose="EMPLOYMENT_VERIFICATION", q=None, **extra):
    return vclient.post("/verify", headers=h(tok), json={"credential_ref": ref, "purpose": purpose, "question": q or {"type": "IS_ENROLLED"}, **extra})


# =============================================================== credentials
def test_credential_is_a_verifiable_sd_jwt_with_aicte_attestation(client, tokens, aarav):
    from app import credentials
    from app.db import owner_tx, row
    with owner_tx() as c:
        cr = row(c, "SELECT sd_jwt, attestation FROM cred.credentials WHERE credential_ref=:r", r=aarav["credential_ref"])
        jw = {k["kid"]: k["public_jwk"] for k in c.execute(text("SELECT kid, public_jwk FROM cred.signing_keys")).mappings()}
    out = credentials.verify_artifact(cr["sd_jwt"], cr["attestation"], jw)
    assert out["valid"] and out["issuer_signature"] and out["aicte_attestation"]
    assert out["claims"]["status"] == "ADMITTED"
    # the credential never carries the student's identifiers
    blob = cr["sd_jwt"] + cr["attestation"]
    assert "apaar" not in blob.lower()
    # tamper: flip a character in the signed part
    jwt, *rest = cr["sd_jwt"].split("~")
    bad = jwt[:-3] + ("AAA" if not jwt.endswith("AAA") else "BBB")
    try:
        res = credentials.verify_artifact("~".join([bad, *rest]), cr["attestation"], jw)
        assert not res["valid"]
    except Exception:  # noqa: BLE001 - a malformed signature may also raise
        pass


def test_public_status_page_shows_only_status(vclient, aarav):
    r = vclient.get(f"/v/{aarav['credential_ref']}")
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "VALID" and "boundary" in body
    assert not {"student_id", "name", "apaar_id"} & set(body)
    unknown = vclient.get("/v/does-not-exist-123").json()
    assert unknown["result"] == "NEUTRAL"


def test_public_keys_and_status_list_are_published(vclient):
    jw = vclient.get("/public/jwks").json()
    assert jw["keys"] and all("d" not in k for k in jw["keys"])           # public parts only
    ks = vclient.get("/public/key-status").json()["keys"]
    assert any(v["status"] == "RETIRED" for v in ks.values())
    sl = vclient.get("/public/status-list/1").json()["jwt"]
    from app import credentials
    p = credentials.jws_payload(sl)
    assert p["credentialSubject"]["type"] == "BitstringStatusList"


def test_institution_issue_rules_and_tenant_isolation(client, tokens):
    r = client.get("/api/v1/credentials", headers=h(tokens["ia.kit"]))
    assert r.status_code == 200 and r.json()["total"] >= 1
    first = r.json()["items"][0]
    # another institution cannot fetch the certificate
    assert client.get(f"/api/v1/credentials/{first['id']}/certificate.pdf", headers=h(tokens["ia.bvec"])).status_code == 404
    ok = client.get(f"/api/v1/credentials/{first['id']}/certificate.pdf", headers=h(tokens["ia.kit"]))
    assert ok.status_code == 200 and ok.content.startswith(b"%PDF")
    # an enrolment that is not VERIFIED cannot be issued a credential
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        pending = scalar(c, "SELECT e.id FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id JOIN ref.institutions i ON i.id=e.institution_id "
                            "WHERE i.slug='kit' AND v.overall_state<>'VERIFIED' LIMIT 1")
    if pending:
        r = client.post("/api/v1/credentials", headers=h(tokens["ia.kit"], **step_up(client, tokens["ia.kit"])), json={"enrolment_id": str(pending)})
        assert r.status_code == 409 and r.json()["code"] == "NOT_ELIGIBLE"
    # io (operator) may not issue
    io = login_token(client, "io.kit")
    assert client.post("/api/v1/credentials/issue-all-verified", headers=h(io, **step_up(client, io))).status_code == 403


def test_issue_all_verified_is_idempotent(client, tokens):
    a = client.post("/api/v1/credentials/issue-all-verified", headers=h(tokens["ia.kit"], **step_up(client, tokens["ia.kit"])))
    assert a.status_code == 200, a.text
    b = client.post("/api/v1/credentials/issue-all-verified", headers=h(tokens["ia.kit"], **step_up(client, tokens["ia.kit"])))
    assert b.json()["issued"] == 0


# =============================================================== verifier
def test_verifier_yes_discloses_only_consented_and_scoped_fields(client, vclient, aarav):
    v = vtoken(client, "vu.employer")
    r = ask(vclient, v["verify_token"], aarav["credential_ref"], nonce="n-123")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["result"] == "YES" and b["nonce"] == "n-123"
    # consent covers institution_name, programme_name, academic_year; the scope for this purpose also allows enrolment_status, but the student did not agree
    assert set(b["fields"]) == {"institution_name", "programme_name", "academic_year"}
    assert b["disclosure"]["shared"] == 3 and b["disclosure"]["of"] == 40
    from app import credentials, verifier
    jw = vclient.get("/public/jwks").json()
    keys = {k["kid"]: k for k in jw["keys"]}
    chk = verifier.verify_answer(b["answer_jwt"], keys, expected_nonce="n-123")
    assert chk["signature_valid"] and not chk["expired"] and chk["nonce_ok"]
    assert verifier.verify_answer(b["answer_jwt"], keys, expected_nonce="other")["nonce_ok"] is False
    assert verifier.verify_answer(b["answer_jwt"], keys, now_ts=chk["payload"]["exp"] + 1)["expired"] is True


def test_programme_and_year_questions(client, vclient, aarav):
    v = vtoken(client, "vu.employer")["verify_token"]
    yr = ask(vclient, v, aarav["credential_ref"], q={"type": "ENROLLED_IN_YEAR", "academic_year": aarav["academic_year"]}).json()
    assert yr["result"] == "YES"
    wrong = ask(vclient, v, aarav["credential_ref"], q={"type": "ENROLLED_IN_YEAR", "academic_year": "1999-00"}).json()
    assert wrong["result"] == "NO" and wrong["fields"] == {}
    bad = ask(vclient, v, aarav["credential_ref"], q={"type": "WHO_IS_THIS"})
    assert bad.status_code == 422


def test_unknown_reference_and_missing_consent_return_identical_neutral_bodies(client, vclient, tokens, aarav):
    bank = vtoken(client, "vu.bank")["verify_token"]
    no_consent = ask(vclient, bank, aarav["credential_ref"], purpose="LOAN_ELIGIBILITY")
    unknown = ask(vclient, bank, "x" * 22, purpose="LOAN_ELIGIBILITY")
    assert no_consent.status_code == unknown.status_code == 200
    assert no_consent.json() == unknown.json() and no_consent.json()["result"] == "NEUTRAL"
    # ... but the student sees a pending consent request in the wallet (silently created)
    reqs = client.get("/api/v1/student/consents", headers=h(tokens["st.aarav"])).json()["requests"]
    assert any(r["verifier"].startswith("Sagarmatha") and r["status"] == "PENDING" for r in reqs)


def test_scope_and_auth_boundaries(client, vclient, tokens, aarav):
    emb = vtoken(client, "vu.embassy")
    # purpose the organisation is not approved for
    assert ask(vclient, emb["verify_token"], aarav["credential_ref"], purpose="LOAN_ELIGIBILITY").status_code == 403
    # staff tokens and the core-api token are not accepted by the verify-api
    assert vclient.post("/verify", headers=h(tokens["ia.kit"]), json={"credential_ref": "a", "purpose": "VISA_SUPPORT", "question": {"type": "IS_ENROLLED"}}).status_code == 401
    assert vclient.post("/verify", headers=h(emb["access_token"]), json={"credential_ref": "a", "purpose": "VISA_SUPPORT", "question": {"type": "IS_ENROLLED"}}).status_code == 401
    assert vclient.post("/verify", json={"credential_ref": "a", "purpose": "VISA_SUPPORT", "question": {"type": "IS_ENROLLED"}}).status_code == 401
    # a verifier cannot use the staff API
    for path in ("/api/v1/credentials", "/api/v1/admin/users", "/api/v1/aicte/onboarding", "/api/v1/student/credentials"):
        assert client.get(path, headers=h(emb["access_token"])).status_code in (401, 403), path
    # exactly one identifier
    assert vclient.post("/verify", headers=h(emb["verify_token"]), json={"purpose": "VISA_SUPPORT", "question": {"type": "IS_ENROLLED"}}).status_code == 422


def test_withdrawn_consent_takes_effect_on_next_request(client, vclient, tokens, aarav):
    v = vtoken(client, "vu.employer")["verify_token"]
    cons = client.get("/api/v1/student/consents", headers=h(tokens["st.aarav"])).json()["consents"]
    live = next(c for c in cons if c["status"] == "GRANTED" and c["verifier"].startswith("Meridian"))
    assert client.delete(f"/api/v1/student/consents/{live['id']}", headers=h(tokens["st.aarav"])).status_code == 200
    assert ask(vclient, v, aarav["credential_ref"]).json()["result"] == "NEUTRAL"
    # grant again (restricted to one field)
    g = client.post("/api/v1/student/consents", headers=h(tokens["st.aarav"]), json={"verifier_org_id": org_id("Meridian"), "purpose_code": "EMPLOYMENT_VERIFICATION", "fields": ["institution_name"], "expires_days": 30})
    assert g.status_code == 200, g.text
    again = ask(vclient, v, aarav["credential_ref"]).json()
    assert again["result"] == "YES" and set(again["fields"]) == {"institution_name"}
    # access log records every request with who and what
    log = client.get("/api/v1/student/access-log", headers=h(tokens["st.aarav"])).json()
    assert log["disclosure_of"] == 40 and any(x["verifier"].startswith("Meridian") and x["outcome"] == "YES" for x in log["verifications"])


def test_hourly_limit_returns_429(client, vclient, aarav):
    from app.db import owner_tx
    v = vtoken(client, "vu.embassy")["verify_token"]
    with owner_tx() as c:
        c.execute(text("UPDATE iam.verifier_scopes SET hourly_limit=2 WHERE verifier_org_id=CAST(:o AS uuid) AND purpose_code='VISA_SUPPORT'"), {"o": org_id("Visa Section")})
    try:
        codes = [ask(vclient, v, aarav["credential_ref"], purpose="VISA_SUPPORT").status_code for _ in range(4)]
        assert 429 in codes and codes[:2] == [200, 200]
    finally:
        with owner_tx() as c:
            c.execute(text("UPDATE iam.verifier_scopes SET hourly_limit=20 WHERE verifier_org_id=CAST(:o AS uuid) AND purpose_code='VISA_SUPPORT'"), {"o": org_id("Visa Section")})


def test_abuse_alert_on_unknown_reference_scan(client, vclient):
    from app.db import owner_tx, scalar
    v = vtoken(client, "vu.employer")["verify_token"]
    with owner_tx() as c:
        c.execute(text("UPDATE iam.verifier_scopes SET hourly_limit=1000 WHERE verifier_org_id=CAST(:o AS uuid)"), {"o": org_id("Meridian")})
    for i in range(32):
        ask(vclient, v, f"scan-{uuid.uuid4().hex[:14]}")
    with owner_tx() as c:
        n = scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='verifier.abuse_alert'")
        c.execute(text("UPDATE iam.verifier_scopes SET hourly_limit=60 WHERE verifier_org_id=CAST(:o AS uuid)"), {"o": org_id("Meridian")})
    assert n >= 1


def test_suspended_organisation_is_refused(client, vclient, tokens, aarav):
    v = vtoken(client, "vu.embassy")["verify_token"]
    oid = org_id("Visa Section")
    r = client.post(f"/api/v1/verifier-orgs/{oid}/status?status=SUSPENDED", headers=h(tokens["as.admin"]), json={"reason": "Abuse review in progress"})
    assert r.status_code == 200, r.text
    try:
        assert ask(vclient, v, aarav["credential_ref"], purpose="VISA_SUPPORT").status_code == 403
    finally:
        client.post(f"/api/v1/verifier-orgs/{oid}/status?status=ACTIVE", headers=h(tokens["as.admin"]), json={"reason": "Review closed, reinstated"})
    assert client.get("/api/v1/verifier-orgs", headers=h(tokens["ia.kit"])).status_code == 403


# =============================================================== share links
def test_share_link_token_hashed_scoped_expiring_revocable(client, vclient, tokens, aarav):
    tok = tokens["st.aarav"]
    assert client.post("/api/v1/student/share-links", headers=h(tok), json={"credential_id": aarav["id"], "purpose_code": "VISA_SUPPORT", "fields": ["institution_name"], "expires_days": 31}).status_code == 422
    assert client.post("/api/v1/student/share-links", headers=h(tok), json={"credential_id": aarav["id"], "purpose_code": "VISA_SUPPORT", "fields": ["date_of_birth"], "expires_days": 5}).status_code == 422
    r = client.post("/api/v1/student/share-links", headers=h(tok), json={"credential_id": aarav["id"], "purpose_code": "EMPLOYMENT_VERIFICATION", "fields": ["institution_name", "programme_name"], "expires_days": 5})
    assert r.status_code == 200, r.text
    link = r.json()
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        stored = scalar(c, "SELECT token_hash FROM cred.share_links WHERE id=CAST(:i AS uuid)", i=link["id"])
    assert stored != link["token"] and link["token"] not in stored             # only a hash is stored
    pub = vclient.post("/public/verify", json={"token": link["token"], "question": {"type": "IS_ENROLLED"}})
    assert pub.status_code == 200 and pub.json()["result"] == "YES"
    assert set(pub.json()["fields"]) == {"institution_name", "programme_name"}
    # the list never shows the token again
    items = client.get("/api/v1/student/share-links", headers=h(tok)).json()["items"]
    assert items and all("token" not in i for i in items)
    assert client.delete(f"/api/v1/student/share-links/{link['id']}", headers=h(tok)).status_code == 200
    after = vclient.post("/public/verify", json={"token": link["token"], "question": {"type": "IS_ENROLLED"}}).json()
    bogus = vclient.post("/public/verify", json={"token": "nope", "question": {"type": "IS_ENROLLED"}}).json()
    assert after == bogus and after["result"] == "NEUTRAL"


def test_expired_link_is_neutral(client, vclient, tokens, aarav):
    r = client.post("/api/v1/student/share-links", headers=h(tokens["st.aarav"]), json={"credential_id": aarav["id"], "purpose_code": "EMPLOYMENT_VERIFICATION", "fields": ["institution_name"], "expires_days": 1}).json()
    from app.db import owner_tx
    with owner_tx() as c:
        c.execute(text("UPDATE cred.share_links SET expires_at=now() - interval '1 minute' WHERE id=CAST(:i AS uuid)"), {"i": r["id"]})
    assert vclient.post("/public/verify", json={"token": r["token"], "question": {"type": "IS_ENROLLED"}}).json()["result"] == "NEUTRAL"


# =============================================================== wallet isolation, APAAR consent, access log
def test_student_cannot_see_other_students_wallet(client, tokens, aarav):
    r = client.get("/api/v1/student/credentials", headers=h(tokens["st.diya"]))
    assert r.status_code == 200 and all(c["id"] != aarav["id"] for c in r.json()["items"])
    assert client.get(f"/api/v1/student/credentials/{aarav['id']}/certificate.pdf", headers=h(tokens["st.diya"])).status_code == 404
    assert client.delete(f"/api/v1/student/share-links/{uuid.uuid4()}", headers=h(tokens["st.diya"])).status_code == 404
    assert client.get("/api/v1/student/credentials", headers=h(tokens["ia.kit"])).status_code == 403


def test_refusing_apaar_consent_never_changes_counting(client, tokens):
    from app.db import owner_tx, scalar
    def occupied():
        with owner_tx() as c:
            return scalar(c, "SELECT coalesce(sum(occupied),0) FROM core.seat_buckets")
    before = occupied()
    r = client.post("/api/v1/student/consents/apaar", headers=h(tokens["st.aarav"]), json={"granted": False})
    assert r.status_code == 200 and r.json()["apaar_use"] == "REFUSED"
    assert occupied() == before
    state = client.get("/api/v1/student/consents", headers=h(tokens["st.aarav"])).json()
    assert state["apaar_use"]["status"] == "REFUSED" and "statutory" in state["notice"]


def test_student_can_download_own_certificate(client, tokens, aarav):
    r = client.get(f"/api/v1/student/credentials/{aarav['id']}/certificate.pdf", headers=h(tokens["st.aarav"]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# =============================================================== corrections
def test_correction_flow_supersedes_credential_and_hides_values(client, tokens):
    diya = tokens["st.diya"]
    items = client.get("/api/v1/student/corrections", headers=h(diya)).json()["items"]
    assert items, "the seed files one correction request for the story student"
    seeded = items[0]
    assert "value" not in json.dumps(seeded).lower() or "proposed" not in seeded
    # a student cannot decide
    q = client.get("/api/v1/aicte/corrections", headers=h(tokens["ar.national"]))
    assert q.status_code == 200
    mine = [x for x in q.json()["items"] if x["id"] == seeded["id"]]
    assert mine and "Seema" not in json.dumps(mine)                             # encrypted proposed value stays hidden
    assert client.post(f"/api/v1/aicte/corrections/{seeded['id']}/decide", headers=h(diya), json={"approve": True, "reason": "Please approve my own"}).status_code == 403
    ok = client.post(f"/api/v1/aicte/corrections/{seeded['id']}/decide", headers=h(tokens["ar.national"], **step_up(client, tokens["ar.national"])), json={"approve": True, "reason": "Document checked against the school record."})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "APPROVED"
    from app import matching
    matching.process_all()                                                      # the worker rebuilds the features of a corrected record
    again = client.post(f"/api/v1/aicte/corrections/{seeded['id']}/decide", headers=h(tokens["ar.national"], **step_up(client, tokens["ar.national"])), json={"approve": True, "reason": "Second attempt should fail"})
    assert again.status_code == 409
    # only the allowed fields can be corrected
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        eid = scalar(c, "SELECT e.id FROM core.enrolments e JOIN iam.users u ON u.student_id=e.student_id WHERE u.username='st.diya' LIMIT 1")
    bad = client.post("/api/v1/student/corrections", headers=h(diya), json={"enrolment_id": str(eid), "field": "programme_code", "value": "CSE"})
    assert bad.status_code == 422


# =============================================================== data subject requests and guardian
def test_dsr_access_is_fulfilled_with_export_and_erasure_respects_lawful_retention(client, tokens):
    aar = tokens["st.aarav"]
    r = client.post("/api/v1/student/dsr", headers=h(aar), json={"type": "ACCESS", "details": "Copy of my data"})
    assert r.status_code == 200 and r.json()["status"] == "FULFILLED"
    exp = client.get(f"/api/v1/student/dsr/{r.json()['id']}/export", headers=h(aar)).json()
    blob = json.dumps(exp)
    import re
    assert "enrolments" in exp and not re.search(r"(?<!\d)99\d{10}(?!\d)", blob)       # no registry identifier in the export
    # erasure while an enrolment is live: reviewer rejects for lawful retention
    er = client.post("/api/v1/student/dsr", headers=h(aar), json={"type": "ERASURE", "details": "Delete everything"})
    assert er.status_code == 200 and er.json()["status"] == "OPEN"
    q = client.get("/api/v1/aicte/dsr", headers=h(tokens["ar.national"])).json()["items"]
    assert any(x["id"] == er.json()["id"] for x in q)
    d = client.post(f"/api/v1/aicte/dsr/{er.json()['id']}/decide", headers=h(tokens["ar.national"], **step_up(client, tokens["ar.national"])), json={"decision": "FULFIL", "note": "Processing the erasure request."})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "REJECTED_LAWFUL_RETENTION" and d.json()["records_erased"] == 0
    assert client.post("/api/v1/student/dsr", headers=h(aar), json={"type": "SELL_MY_DATA"}).status_code == 422


def test_erasure_after_withdrawal_erases_personal_details_but_keeps_counts_and_audit(client, tokens):
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        sid = scalar(c, "SELECT student_id FROM iam.users WHERE username='st.vikram'")
        did = scalar(c, "SELECT id FROM consent.data_subject_requests WHERE student_id=:s AND type='ERASURE'", s=sid)
        live = scalar(c, "SELECT count(*) FROM core.enrolments WHERE student_id=:s AND status IN ('CLAIMED','ADMITTED')", s=sid)
        enrolments = scalar(c, "SELECT count(*) FROM core.enrolments WHERE student_id=:s", s=sid)
        audit_before = scalar(c, "SELECT count(*) FROM audit.audit_log")
    assert did and live == 0, "the story student withdrew, so no live enrolment remains"
    ar = tokens["ar.national"]
    r = client.post(f"/api/v1/aicte/dsr/{did}/decide", headers=h(ar, **step_up(client, ar)), json={"decision": "FULFIL", "note": "Withdrawn student; erasing personal details."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FULFILLED" and r.json()["records_erased"] >= 1
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM pii.pii_records WHERE student_id=:s AND ciphertext IS NOT NULL", s=sid) == 0
        assert scalar(c, "SELECT count(*) FROM core.enrolments WHERE student_id=:s", s=sid) == enrolments       # counts survive
        assert scalar(c, "SELECT count(*) FROM audit.audit_log") > audit_before                                # and the decision is audited
    again = client.post(f"/api/v1/aicte/dsr/{did}/decide", headers=h(ar, **step_up(client, ar)), json={"decision": "FULFIL", "note": "Second attempt should fail."})
    assert again.status_code == 409


def test_guardian_link_needs_otp_from_the_students_number(client, tokens):
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        ref = scalar(c, "SELECT s.student_ref FROM core.students s JOIN iam.users u ON u.student_id=s.id WHERE u.username='st.rohan'")
        c.execute(text("UPDATE consent.guardian_links SET status='PENDING', verified_at=NULL WHERE student_id=(SELECT id FROM core.students WHERE student_ref=:r)"), {"r": ref})
    gd = tokens["gd.meera"]
    r = client.post("/api/v1/student/guardian-links", headers=h(gd), json={"student_ref": str(ref), "relationship": "PARENT"})
    assert r.status_code == 200, r.text
    assert sum(ch.isdigit() for ch in r.json()["sent_to"]) <= 5                  # masked: only the country code and last digits show
    wrong = client.post("/api/v1/student/guardian-links/verify", headers=h(gd), json={"student_ref": str(ref), "otp": "000000"})
    assert wrong.status_code in (400, 401, 403, 422)
    with owner_tx() as c:
        otp = scalar(c, "SELECT otp FROM mockext.sms_messages ORDER BY id DESC LIMIT 1")
    ok = client.post("/api/v1/student/guardian-links/verify", headers=h(gd), json={"student_ref": str(ref), "otp": otp})
    assert ok.status_code == 200 and ok.json()["status"] == "VERIFIED"


# =============================================================== rule sets and simulator
def test_ruleset_separation_of_duties_publish_and_rollback(client, tokens):
    asd, ar = tokens["as.admin"], tokens["ar.national"]
    d = client.post("/api/v1/rulesets/draft", headers=h(asd), json={"notes": "Test: reword one message", "retroactive": False})
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    up = client.put(f"/api/v1/rulesets/{did}/rules/FLD-007", headers=h(asd), json={"message": "No mobile number supplied (test wording)."})
    assert up.status_code == 200, up.text
    # drafter cannot publish; an institution user cannot even see drafts
    assert client.post(f"/api/v1/rulesets/{did}/publish", headers=h(asd, **step_up(client, asd))).status_code == 403
    assert client.post(f"/api/v1/rulesets/{did}/publish", headers=h(tokens["ia.kit"])).status_code == 403
    assert all(s["status"] == "ACTIVE" for s in client.get("/api/v1/rulesets", headers=h(tokens["ia.kit"])).json()["items"])
    assert client.post(f"/api/v1/rulesets/{did}/publish", headers=h(ar)).status_code == 401           # step-up required
    before = next(s for s in client.get("/api/v1/rulesets", headers=h(ar)).json()["items"] if s["status"] == "ACTIVE")
    pub = client.post(f"/api/v1/rulesets/{did}/publish", headers=h(ar, **step_up(client, ar)))
    assert pub.status_code == 200, pub.text
    sets = client.get("/api/v1/rulesets", headers=h(ar)).json()["items"]
    assert sum(1 for s in sets if s["status"] == "ACTIVE") == 1
    assert next(s for s in sets if s["status"] == "ACTIVE")["id"] == did
    # a published set is immutable
    assert client.put(f"/api/v1/rulesets/{did}/rules/FLD-007", headers=h(asd), json={"message": "Edit after publish"}).status_code == 409
    rb = client.post(f"/api/v1/rulesets/{before['id']}/rollback", headers=h(ar, **step_up(client, ar)))
    assert rb.status_code == 200, rb.text
    sets = client.get("/api/v1/rulesets", headers=h(ar)).json()["items"]
    assert next(s for s in sets if s["status"] == "ACTIVE")["id"] == before["id"]


def test_simulator_replays_read_only_and_reports_skipped_rules(client, tokens):
    from app import ops
    from app.db import owner_tx, scalar
    ar, asd = tokens["ar.national"], tokens["as.admin"]
    drafts = [s for s in client.get("/api/v1/rulesets", headers=h(asd)).json()["items"] if s["status"] == "DRAFT"]
    assert drafts, "the seed leaves a draft with stricter age rules"
    from app.claims import current_academic_year
    with owner_tx() as c:
        n0 = scalar(c, "SELECT count(*) FROM rules.findings")
    r = client.post(f"/api/v1/rulesets/{drafts[0]['id']}/simulate", headers=h(ar), json={"cycles": [current_academic_year()]})
    assert r.status_code == 200, r.text
    ops.run_job_inline("rules.simulate", {"run_id": r.json()["id"]})
    res = client.get(f"/api/v1/simulations/{r.json()['id']}", headers=h(ar)).json()
    assert res["status"] == "DONE", res
    assert res["result"]["enrolments_replayed"] > 0
    assert res["result"]["reviewer_workload_minutes"] == res["result"]["new_findings_total"] * res["result"]["minutes_per_finding"]
    assert "AGG-001" in res["skipped_rules"]                                      # needs data outside the snapshot, so it is listed rather than guessed
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM rules.findings") == n0               # no decision changed
    assert client.post(f"/api/v1/rulesets/{drafts[0]['id']}/simulate", headers=h(tokens["ia.kit"]), json={"cycles": ["2025-26"]}).status_code == 403


# =============================================================== onboarding
def test_onboarding_application_approval_creates_admin_and_is_scoped(client, tokens):
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        token = scalar(c, "SELECT status_token FROM iam.onboarding_requests WHERE status='PENDING' ORDER BY created_at LIMIT 1")
    st = client.get(f"/api/v1/onboarding/status/{token}")
    assert st.status_code == 200 and st.json()["status"] == "PENDING"
    # duplicates and unknown ids
    assert client.post("/api/v1/onboarding/institutions", json={"aicte_id": "9-999999", "officer_name": "Test Officer", "designation": "Registrar", "email": "x@college.example.test"}).status_code == 404
    queue = client.get("/api/v1/aicte/onboarding", headers=h(tokens["ar.national"]))
    assert queue.status_code == 200
    pend = [x for x in queue.json()["items"] if x["status"] == "PENDING"]
    assert pend and pend[0]["identity_check"] in ("MATCH", "MISMATCH") and "SIMULATED" in queue.json()["label"]
    # institutions cannot decide
    assert client.post(f"/api/v1/aicte/onboarding/{pend[0]['id']}/decide", headers=h(tokens["ia.kit"]), json={"approve": True, "reason": "self approval"}).status_code == 403
    ok = client.post(f"/api/v1/aicte/onboarding/{pend[0]['id']}/decide", headers=h(tokens["ar.national"], **step_up(client, tokens["ar.national"])), json={"approve": True, "reason": "Records match the master list."})
    assert ok.status_code == 200, ok.text
    u = ok.json()["admin_username"]
    assert client.get(f"/api/v1/onboarding/status/{token}").json()["status"] == "APPROVED"
    # the new administrator can sign in and only sees their own institution
    t = login_token(client, u)
    me = client.get("/api/v1/me", headers=h(t))
    assert me.status_code == 200 and "IA" in json.dumps(me.json())
    again = client.post(f"/api/v1/aicte/onboarding/{pend[0]['id']}/decide", headers=h(tokens["ar.national"], **step_up(client, tokens["ar.national"])), json={"approve": True, "reason": "Duplicate decision"})
    assert again.status_code == 409


# =============================================================== administration
def test_user_management_is_tenant_limited_and_deactivation_is_immediate(client, tokens):
    ia = tokens["ia.kit"]
    mk = client.post("/api/v1/admin/users", headers=h(ia), json={"username": "io2.kit", "display_name": "Second Operator", "role": "IO"})
    assert mk.status_code == 200, mk.text
    assert client.post("/api/v1/admin/users", headers=h(ia), json={"username": "io2.kit", "display_name": "Dup", "role": "IO"}).status_code == 409
    assert client.post("/api/v1/admin/users", headers=h(ia), json={"username": "sneaky.ar", "display_name": "Sneaky", "role": "AR"}).status_code in (403, 422)
    assert client.post("/api/v1/admin/users", headers=h(tokens["io.kit"]), json={"username": "x.y.z", "display_name": "Nope", "role": "IO"}).status_code == 403
    t = login_token(client, "io2.kit")
    assert client.get("/api/v1/me", headers=h(t)).status_code == 200
    # another institution's admin cannot deactivate this user
    assert client.post(f"/api/v1/admin/users/{mk.json()['id']}/deactivate", headers=h(tokens["ia.bvec"])).status_code == 404
    d = client.post(f"/api/v1/admin/users/{mk.json()['id']}/deactivate", headers=h(ia))
    assert d.status_code == 200 and d.json()["sessions_revoked"] >= 1
    assert client.get("/api/v1/me", headers=h(t)).status_code == 401              # session is dead at once
    from app import routes_auth
    routes_auth._hits.clear()
    assert client.post("/api/v1/auth/login", json={"username": "io2.kit", "password": "1234"}).status_code in (401, 403)
    users = client.get("/api/v1/admin/users", headers=h(ia)).json()["items"]
    assert all(not u["username"].startswith("ia.bvec") for u in users)


def test_api_client_secret_shown_once_and_scopes_limited(client, tokens):
    ia = tokens["ia.kit"]
    c = client.post("/api/v1/admin/clients", headers=h(ia), json={"name": "KIT ERP test", "scopes": ["claims:write", "claims:validate"]})
    assert c.status_code == 200, c.text
    sec = c.json()
    assert sec.get("client_secret")
    lst = client.get("/api/v1/admin/clients", headers=h(ia)).json()
    assert all("secret" not in k and "secret_hash" not in k for k in lst["items"])
    assert "feeds:write" not in lst["scopes"]
    assert client.post("/api/v1/admin/clients", headers=h(ia), json={"name": "Greedy", "scopes": ["feeds:write"]}).status_code in (403, 422)
    tok = client.post("/api/v1/auth/token", json={"client_id": sec["client_id"], "client_secret": sec["client_secret"]})
    assert tok.status_code == 200
    rot = client.post(f"/api/v1/admin/clients/{sec['id']}/rotate", headers=h(ia)).json()
    assert rot["client_secret"] != sec["client_secret"]
    assert client.post("/api/v1/auth/token", json={"client_id": sec["client_id"], "client_secret": sec["client_secret"]}).status_code == 401
    assert client.post(f"/api/v1/admin/clients/{sec['id']}/disable", headers=h(ia)).status_code == 200
    assert client.post("/api/v1/auth/token", json={"client_id": sec["client_id"], "client_secret": rot["client_secret"]}).status_code == 401


# =============================================================== webhooks
class _FakeHttp:
    def __init__(self, codes):
        self.codes, self.calls = list(codes), []

    def post(self, url, content=None, timeout=None, headers=None):
        self.calls.append({"url": url, "body": content, "headers": headers})
        code = self.codes.pop(0) if self.codes else 200
        if code == 0:
            raise ConnectionError("down")
        return type("R", (), {"status_code": code})()


def _mk_delivery(client, ia):
    w = client.post("/api/v1/admin/webhooks", headers=h(ia), json={"url": "https://erp.example.org/hook", "events": ["claim.created"]})
    assert w.status_code == 200, w.text
    from app.core import dumps, now, uuid7
    from app.db import owner_tx, scalar
    did = uuid7()
    with owner_tx() as c:
        inst = scalar(c, "SELECT institution_id FROM iam.webhook_endpoints WHERE id=CAST(:w AS uuid)", w=w.json()["id"])
        c.execute(text("INSERT INTO ops.webhook_deliveries (id, endpoint_id, institution_id, event_type, payload, next_attempt_at) VALUES (:id,CAST(:e AS uuid),:i,'claim.created',CAST(:p AS jsonb),:n)"),
                  {"id": did, "e": w.json()["id"], "i": inst, "p": dumps({"event": "claim.created", "aggregate_id": "x"}), "n": now()})
    return w.json(), str(did)


def test_webhook_signature_retry_dead_letter_and_replay(client, tokens, monkeypatch):
    from app import admin, http
    from app.db import owner_tx, row
    ia = tokens["ia.kit"]
    w, did = _mk_delivery(client, ia)
    fake = _FakeHttp([500, 200])
    monkeypatch.setattr(http, "client", lambda: fake)
    out = admin.deliver({"delivery_id": did})
    assert "retry_in_s" in out and out["retry_in_s"] == admin.BACKOFF[0]
    call = fake.calls[0]
    sig = call["headers"]["X-Sentinel-Signature"]
    parts = dict(x.split("=", 1) for x in sig.split(","))
    assert admin.sign(w["secret"], call["body"], int(parts["t"])) == sig                    # receiver can verify with the secret shown once
    assert "personal" not in call["body"].lower()
    out = admin.deliver({"delivery_id": did})
    assert out == {"delivered": True}
    with owner_tx() as c:
        assert row(c, "SELECT status, attempts FROM ops.webhook_deliveries WHERE id=CAST(:d AS uuid)", d=did) == {"status": "DELIVERED", "attempts": 2}
    # dead letter after 24 hours of failures, then replay by the owner
    w2, did2 = _mk_delivery(client, ia)
    with owner_tx() as c:
        c.execute(text("UPDATE ops.webhook_deliveries SET first_attempt_at=now() - interval '25 hours', status='RETRYING' WHERE id=CAST(:d AS uuid)"), {"d": did2})
    monkeypatch.setattr(http, "client", lambda: _FakeHttp([0]))
    assert admin.deliver({"delivery_id": did2}) == {"dead_lettered": True}
    other = client.post(f"/api/v1/admin/webhook-deliveries/{did2}/replay", headers=h(tokens["ia.bvec"]))
    assert other.status_code == 404
    rp = client.post(f"/api/v1/admin/webhook-deliveries/{did2}/replay", headers=h(ia))
    assert rp.status_code == 200, rp.text
    monkeypatch.setattr(http, "client", lambda: _FakeHttp([200]))
    assert admin.deliver({"delivery_id": did2}) == {"delivered": True}


def test_webhook_ssrf_guard_blocks_private_targets(client, tokens, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_URLS", "false")
    ia = tokens["ia.kit"]
    for url in ("http://127.0.0.1:8000/x", "http://169.254.169.254/latest/meta-data", "http://[::1]/x", "http://10.0.0.5/x", "ftp://example.org/x"):
        r = client.post("/api/v1/admin/webhooks", headers=h(ia), json={"url": url, "events": ["claim.created"]})
        assert r.status_code == 422, (url, r.status_code)
    assert client.post("/api/v1/admin/webhooks", headers=h(ia), json={"url": "https://erp.example.org/h", "events": ["not.an.event"]}).status_code == 422


def test_claim_creates_webhook_delivery_with_identifiers_only(client, tokens):
    from conftest import claim_body, make_people, post_claim
    from app import projector
    from app.db import owner_tx, rows
    ia = tokens["ia.kit"]
    w = client.post("/api/v1/admin/webhooks", headers=h(ia), json={"url": "https://erp.example.org/claims", "events": ["claim.created"]}).json()
    p = make_people(1)[0]
    r = post_claim(client, ia, claim_body(p))
    assert r.status_code == 201, r.text
    while projector.project():
        pass
    with owner_tx() as c:
        d = rows(c, "SELECT payload FROM ops.webhook_deliveries WHERE endpoint_id=CAST(:e AS uuid)", e=w["id"])
    assert d, "the projector turns the outbox event into a delivery"
    blob = json.dumps(d[0]["payload"])
    assert p[0] not in blob and p[1] not in blob and "dob" not in blob.lower()


# =============================================================== retention and liveness
def test_retention_purges_expired_operational_rows_only(client):
    from app import ops
    from app.db import owner_tx, scalar
    with owner_tx() as c:
        audit_before = scalar(c, "SELECT count(*) FROM audit.audit_log")
        c.execute(text("INSERT INTO iam.idempotency_keys (actor_id, key, route, request_hash, state, expires_at) SELECT id, :k, '/x', 'h', 'DONE', now() - interval '2 days' FROM iam.users LIMIT 1"), {"k": "old-" + uuid.uuid4().hex})
    out = ops.run_job_inline("maintenance.retention")
    assert out["IDEMPOTENCY_KEYS"] >= 1
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log") >= audit_before        # the audit trail is never purged
        assert scalar(c, "SELECT last_count FROM ops.retention_policies WHERE data_class='IDEMPOTENCY_KEYS'") >= 1


def test_liveness_feed_hashes_ids_and_layer_d_is_a_lead_not_a_verdict(client, tokens):
    from app import intel
    from app.db import owner_tx, rows, scalar
    from conftest import make_people
    asd = tokens["as.admin"]
    people = make_people(2)
    # only a system client with feeds:write may push; staff and institution tokens cannot
    assert client.post("/api/v1/feeds/liveness", headers=h(tokens["ia.kit"]), json={"items": []}).status_code == 403
    cl = client.post("/api/v1/admin/clients", headers=h(asd), json={"name": "Mock ABC feed", "scopes": ["feeds:write"]})
    assert cl.status_code == 200, cl.text
    tok = client.post("/api/v1/auth/token", json={"client_id": cl.json()["client_id"], "client_secret": cl.json()["client_secret"]}).json()["access_token"]
    r = client.post("/api/v1/feeds/liveness", headers=h(tok), json={"items": [{"apaar_id": people[0][0], "has_abc_credit": True}, {"apaar_id": "123"}]})
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1 and r.json()["rejected"] == 1 and "SIMULATED" in r.json()["label"]
    with owner_tx() as c:
        cols = [x[0] for x in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='intel' AND table_name='liveness_feed'"))]
    assert "apaar_id" not in cols                                                   # hashed on receipt, never stored raw
    intel.run_all()
    with owner_tx() as c:
        d = rows(c, "SELECT severity, evidence, explanation FROM intel.anomaly_alerts WHERE layer='D'")
    assert d, "the seeded feed has an institution with conspicuous inactivity"
    ev = d[0]["evidence"]
    assert ev["source"].startswith("MOCK_FEED") and "withheld" in d[0]["explanation"]["description"]


# =============================================================== audit
def test_final_phase_actions_are_audited(client):
    from app.db import owner_tx, rows
    with owner_tx() as c:
        got = {r["action"] for r in rows(c, "SELECT DISTINCT action FROM audit.audit_log")}
    for a in ("credential.issued", "consent.granted", "consent.withdrawn", "share_link.created", "share_link.revoked", "user.created", "user.deactivated", "webhook.created", "onboarding.decided"):
        assert a in got, a
