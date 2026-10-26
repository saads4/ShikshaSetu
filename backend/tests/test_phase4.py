"""Phase 4: Trust Index, anomaly layers, cases/appeals, elevation grants, seat release, reports."""
import hashlib
import io
import uuid
import zipfile
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from app import audit, cases, intel, matching, ops, reports
from app.core import now
from app.db import owner_tx, rows, scalar
from conftest import auth, claim_body, login_token, make_people, post_claim
from helpers import bucket_state, make_programme
from test_phase3 import step_up
from test_triple_lock_audit import _student_user


def drain():
    while ops.work_once("t4"):
        pass


# ------------------------------------------------------------------ Trust Index
def _inputs(**kw):
    base = {"window": {"opens_at": now() - timedelta(days=80), "closes_at": now() + timedelta(days=10)}, "conf": {}, "alerts": {}, "attempts": {}, "exceptions": {}, "subs": {}, "extend": {}, "corr": {}}
    base.update(kw)
    return base


def test_trust_formula_matches_the_prd_components():
    i = "inst"
    score, comps = intel.trust_for(i, _inputs())
    assert score == 100.0 and {c["key"]: c["max"] for c in comps} == {"confirmation": 30, "alerts": 20, "conflicts": 15, "timeliness": 15, "gate": 10, "corrections": 10}
    # confirmation: 30 x verified / (verified + unconfirmed + disputed + pending > 14 d)
    s, c = intel.trust_for(i, _inputs(conf={i: {"total": 100, "ver": 60, "unc": 20, "dis": 10, "pend_old": 10}}))
    assert next(x for x in c if x["key"] == "confirmation")["points"] == 18.0
    # alerts: 20 x (1 - min(1, sum w / 5)); HIGH=1.0, MEDIUM=0.5, LOW=0.2
    s, c = intel.trust_for(i, _inputs(alerts={i: [{"severity": "HIGH"}, {"severity": "MEDIUM"}, {"severity": "LOW"}]}))
    assert next(x for x in c if x["key"] == "alerts")["points"] == pytest.approx(20 * (1 - 1.7 / 5), abs=0.06)
    # conflicts: 50 per 1,000 attempts -> 0 points
    s, c = intel.trust_for(i, _inputs(attempts={i: {"total": 1000, "conflicts": 50, "seat_limit": 0}}))
    assert next(x for x in c if x["key"] == "conflicts")["points"] == 0.0
    # gate: (seat-limit attempts + exceptions) / 10
    s, c = intel.trust_for(i, _inputs(attempts={i: {"total": 10, "conflicts": 0, "seat_limit": 3}}, exceptions={i: 2}))
    assert next(x for x in c if x["key"] == "gate")["points"] == 5.0
    # timeliness: late 7.5; closed with none 0
    s, c = intel.trust_for(i, _inputs(subs={i: {"status": "SUBMITTED", "late_flag": True}}))
    assert next(x for x in c if x["key"] == "timeliness")["points"] == 7.5
    closed = {"opens_at": now() - timedelta(days=90), "closes_at": now() - timedelta(days=1)}
    s, c = intel.trust_for(i, _inputs(window=closed))
    assert next(x for x in c if x["key"] == "timeliness")["points"] == 0.0
    # corrections: 15% of enrolments -> 0
    s, c = intel.trust_for(i, _inputs(conf={i: {"total": 100, "ver": 100, "unc": 0, "dis": 0, "pend_old": 0}}, corr={i: 15}))
    assert next(x for x in c if x["key"] == "corrections")["points"] == 0.0
    assert [intel.band(x) for x in (95, 80, 79.9, 60, 59.9)] == ["LOW_PRIORITY", "LOW_PRIORITY", "WATCH", "WATCH", "REVIEW_PRIORITY"]


def test_trust_scores_are_computed_idempotently_and_explainable(client, tokens):
    r1 = intel.run_trust()
    with owner_tx() as c:
        n1 = scalar(c, "SELECT count(*) FROM intel.trust_scores")
    intel.run_trust()
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM intel.trust_scores") == n1                                  # idempotent per (institution, year, date)
    inst = client.get("/api/v1/me", headers=auth(tokens["ia.kit"])).json()["institution"]["id"]
    d = client.get(f"/api/v1/trust/{inst}", headers=auth(tokens["ia.kit"])).json()
    assert 0 <= d["score"] <= 100 and len(d["components"]) == 6 and abs(sum(c["points"] for c in d["components"]) - d["score"]) < 0.11
    assert all(c["formula"] and c["inputs"] is not None for c in d["components"]) and "no penalty" in d["notice"]
    assert d["open_alerts"] == []                                                                          # institutions never see anomaly alerts
    # access: operator has no trust:read; another institution's score is a 404; regional reviewer sees only their region
    assert client.get(f"/api/v1/trust/{inst}", headers=auth(tokens["io.kit"])).status_code == 403
    assert client.get(f"/api/v1/trust/{inst}", headers=auth(tokens["ia.bvec"])).status_code == 404
    south = client.get("/api/v1/trust?limit=200", headers=auth(tokens["ar.south"])).json()
    assert south["items"] and all(i["region"] == "SOUTH" for i in south["items"]) and "triggers no penalty" in south["notice"]
    assert client.post("/api/v1/intel/run-now", headers=auth(tokens["ar.national"])).status_code == 200


# ------------------------------------------------------------------ anomaly layers
def test_robust_z_flags_the_outlier_not_the_crowd():
    x = pd.Series([0.10, 0.11, 0.09, 0.10, 0.12, 0.10, 0.11, 0.95])
    z = intel._robust_z(x, pd.Series(["g"] * len(x)))
    assert z.iloc[-1] >= 3.5 and (z.iloc[:-1].abs() < 3.5).all()
    assert intel._robust_z(x.iloc[:4], pd.Series(["g"] * 4)).isna().all()                                    # fewer than 5 peers -> no score


def test_shared_phone_cluster_is_a_weighted_signal_not_an_automatic_flag(client, tokens):
    make_programme("kit", "T-GR", 40)
    phone = "0098" + uuid.uuid4().hex[:6].translate({i: None for i in range(97, 123)}).ljust(6, "7")
    ids = []
    for p in make_people(10):
        r = post_claim(client, tokens["ia.kit"], {**claim_body(p, "T-GR"), "mobile": phone})
        assert r.status_code == 201
        ids.append(r.json()["enrolment_id"])
    matching.process_all()
    with owner_tx() as c:
        cases_before = scalar(c, "SELECT count(*) FROM intel.cases")
    out = intel.run_anomalies()
    assert out["graph_clusters"] >= 1
    with owner_tx() as c:
        a = rows(c, "SELECT a.severity, a.status, a.explanation FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.type='SHARED_CONTACT_CLUSTER' AND i.slug='kit'")
        assert a and "weighted review signal" in a[0]["explanation"]["description"]
        assert scalar(c, "SELECT count(*) FROM core.enrolment_verification WHERE enrolment_id = ANY(CAST(:ids AS uuid[])) AND overall_state <> 'PENDING'", ids=ids) == 0   # nothing was flagged on the students
        assert scalar(c, "SELECT count(*) FROM intel.cases") == cases_before                                              # the pipeline opens no case by itself
    assert out["isolation_forest"].startswith("skipped")                                                       # minimal profile has fewer than 30 institutions


def test_cluster_alert_carries_sample_enrolments_so_a_granted_reviewer_can_inspect(client, tokens):
    with owner_tx() as c:
        a = rows(c, "SELECT a.id, a.evidence FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.type='SHARED_CONTACT_CLUSTER' AND a.status='OPEN' AND i.slug='kit' LIMIT 1")
    assert a and a[0]["evidence"]["sample_enrolments"]
    cid = client.post(f"/api/v1/alerts/{a[0]['id']}/open-case", headers=auth(tokens["ar.national"])).json()["case_id"]
    assert client.post(f"/api/v1/cases/{cid}/reveal", headers=auth(tokens["ar.national"])).status_code == 403
    client.post("/api/v1/me/elevations", headers=auth(tokens["ar.national"]), json={"case_id": cid, "justification": "Inspecting members of a shared-phone cluster in this case.", "duration_minutes": 15})
    people = client.post(f"/api/v1/cases/{cid}/reveal", headers=auth(tokens["ar.national"])).json()["people"]
    assert 1 <= len(people) <= 5 and all(p["name"] for p in people)


def test_isolation_forest_scores_when_enough_institutions(monkeypatch):
    rng = np.random.default_rng(1)
    df = pd.DataFrame({f: rng.normal(size=40) for f in intel.FEATURES}); df.loc[0, :] = 8
    from sklearn.ensemble import IsolationForest
    s = -IsolationForest(n_estimators=100, random_state=42).fit(df).score_samples(df)
    assert s.argmax() == 0 and s[0] >= np.percentile(s, 97)


# ------------------------------------------------------------------ alerts -> cases -> appeals
def _alert_for(client, tokens, slug_name="Kaveri"):
    intel.run_anomalies()
    items = client.get("/api/v1/alerts?status=OPEN&limit=200", headers=auth(tokens["ar.national"])).json()["items"]
    return items


def _fresh_alert(inst_slug="kit", typ=None):
    typ = typ or "T4_" + uuid.uuid4().hex[:6]
    with owner_tx() as c:
        iid = scalar(c, "SELECT id FROM ref.institutions WHERE slug=:s", s=inst_slug)
        aid = uuid.uuid4()
        c.execute(text("INSERT INTO intel.anomaly_alerts (id, institution_id, layer, type, severity, academic_year, baseline, deviation, evidence, explanation, suggested_action, model_version, dedupe_key) "
                       "VALUES (:id,:i,'C',:t,'MEDIUM','2026-27','{}',4.2,CAST(:ev AS jsonb),CAST(:ex AS jsonb),'Review it',:m,:k)"),
                  {"id": aid, "i": iid, "t": typ, "m": intel.MODEL_VERSION, "k": f"C:{typ}:{iid}:2026-27", "ev": '{"value": 0.4}', "ex": '{"contributions": []}'})
    return str(aid)


def test_case_state_machine_response_close_label_and_evidence_hash(client, tokens):
    h = lambda u, **x: auth(tokens[u], **x)
    aid = _fresh_alert()
    detail = client.get(f"/api/v1/alerts/{aid}", headers=h("ar.national")).json()
    assert detail["explanation"] is not None and detail["suggested_action"] and detail["model_version"]
    cid = client.post(f"/api/v1/alerts/{aid}/open-case", headers=h("ar.national")).json()["case_id"]
    assert client.post(f"/api/v1/alerts/{aid}/open-case", headers=h("ar.national")).status_code == 409            # alert is now IN_CASE
    c0 = client.get(f"/api/v1/cases/{cid}", headers=h("ar.national")).json()
    assert c0["status"] == "OPEN" and c0["evidence_sha256"] and c0["case_no"].startswith("CASE-") and "No outcome triggers an automatic penalty" in c0["statement"]
    # cannot close before review; institution cannot respond before a request
    assert client.post(f"/api/v1/cases/{cid}/close", headers=h("ar.national"), json={"outcome_code": "FALSE_POSITIVE", "notes": "too early"}).status_code == 409
    assert client.post(f"/api/v1/institution/cases/{cid}/respond", headers=h("ia.kit"), json={"message": "Nothing requested yet"}).status_code == 409
    rq = client.post(f"/api/v1/cases/{cid}/request-response", headers=h("ar.national"), json={"message": "Please explain this pattern."})
    assert rq.status_code == 200 and rq.json()["status"] == "AWAITING_RESPONSE"
    due = pd.Timestamp(rq.json()["response_due_at"]); assert 13 <= (due - pd.Timestamp.now(tz="UTC")).days <= 14                     # 14-day response window
    # tenant isolation: the other institution cannot see or answer it; the institution view hides the evidence pack
    assert client.get(f"/api/v1/institution/cases/{cid}", headers=h("ia.bvec")).status_code == 404
    inst_view = client.get(f"/api/v1/institution/cases/{cid}", headers=h("ia.kit")).json()
    assert "evidence_pack" not in inst_view and "alert" not in inst_view and inst_view["status"] == "AWAITING_RESPONSE"
    assert client.post(f"/api/v1/institution/cases/{cid}/respond", headers=h("io.kit"), json={"message": "operator reply"}).status_code == 403
    assert client.post(f"/api/v1/institution/cases/{cid}/respond", headers=h("ia.kit"), json={"message": "We reviewed the records and attach the counselling list."}).json()["status"] == "UNDER_REVIEW"
    bad = client.post(f"/api/v1/cases/{cid}/close", headers=h("ar.national"), json={"outcome_code": "FINE", "notes": "not an outcome"})
    assert bad.status_code == 422
    closed = client.post(f"/api/v1/cases/{cid}/close", headers=h("ar.national"), json={"outcome_code": "CONFIRMED_VIOLATION", "notes": "Evidence reviewed and response considered."})
    assert closed.status_code == 200 and closed.json()["appeal_window_days"] == 15
    c1 = client.get(f"/api/v1/cases/{cid}", headers=h("ar.national")).json()
    assert c1["evidence_sha256"] != c0["evidence_sha256"] and c1["outcome_code"] == "CONFIRMED_VIOLATION" and c1["alert"]["type"].startswith("T4_")
    with owner_tx() as c:
        assert scalar(c, "SELECT status FROM intel.anomaly_alerts WHERE id=CAST(:a AS uuid)", a=aid) == "CLOSED"
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='case.closed' AND (payload->>'automatic_penalty')='false' AND entity_id=:c", c=cid) == 1
        assert scalar(c, "SELECT status FROM ref.institutions WHERE slug='kit'") == "ACTIVE"                              # no automatic consequence for the institution
    # ---- appeal: IA files within 15 days; a DIFFERENT reviewer decides; the label is updated
    assert client.post(f"/api/v1/institution/cases/{cid}/appeal", headers=h("ia.kit"), json={"grounds": "too short"}).status_code == 422
    assert client.post(f"/api/v1/institution/cases/{cid}/appeal", headers=h("io.kit"), json={"grounds": "G" * 30}).status_code == 403
    assert client.post(f"/api/v1/institution/cases/{cid}/appeal", headers=h("ia.kit"), json={"grounds": "The shared numbers belong to guardians and records support this."}).json()["status"] == "APPEALED"
    own = client.post(f"/api/v1/cases/{cid}/appeal/decide", headers=h("ar.national"), json={"decision": "OVERTURNED", "notes": "owner deciding"})
    assert own.status_code == 403 and own.json()["code"] == "SEPARATION_OF_DUTIES"
    done = client.post(f"/api/v1/cases/{cid}/appeal/decide", headers=h("ar.national2"), json={"decision": "OVERTURNED", "notes": "Guardian phones verified."})
    assert done.status_code == 200 and done.json()["outcome_code"] == "NOT_SUBSTANTIATED"                                 # label updated
    with pytest.raises(Exception):                                                                                       # the database enforces it as well
        with owner_tx() as c:
            c.execute(text("UPDATE intel.appeals SET decided_by=(SELECT owner_id FROM intel.cases WHERE id=CAST(:c AS uuid)) WHERE case_id=CAST(:c AS uuid)"), {"c": cid})


def test_appeal_window_expires_after_15_days(client, tokens):
    h = lambda u: auth(tokens[u])
    cid = client.post(f"/api/v1/alerts/{_fresh_alert()}/open-case", headers=h("ar.national")).json()["case_id"]
    client.post(f"/api/v1/cases/{cid}/start-review", headers=h("ar.national"))
    client.post(f"/api/v1/cases/{cid}/close", headers=h("ar.national"), json={"outcome_code": "DATA_CORRECTED", "notes": "Institution corrected the records."})
    with owner_tx() as c:
        c.execute(text("UPDATE intel.cases SET closed_at = now() - interval '16 days' WHERE id=CAST(:c AS uuid)"), {"c": cid})
    r = client.post(f"/api/v1/institution/cases/{cid}/appeal", headers=h("ia.kit"), json={"grounds": "A" * 40})
    assert r.status_code == 409 and r.json()["code"] == "APPEAL_WINDOW_PASSED"


def test_alert_dismissal_and_scope(client, tokens):
    aid = _fresh_alert("kit")
    assert client.post(f"/api/v1/alerts/{aid}/dismiss", headers=auth(tokens["ar.south"]), json={"reason": "Known seasonal pattern"}).json()["status"] == "DISMISSED"
    assert client.post(f"/api/v1/alerts/{aid}/dismiss", headers=auth(tokens["ar.south"]), json={"reason": "again"}).status_code == 409
    bvec_alert = _fresh_alert("bvec")                                                                                  # WEST: outside the South reviewer's scope
    assert client.get(f"/api/v1/alerts/{bvec_alert}", headers=auth(tokens["ar.south"])).status_code == 404
    assert client.get("/api/v1/alerts", headers=auth(tokens["ia.kit"])).status_code == 403


# ------------------------------------------------------------------ disputes, seat release, elevation
def _dispute_case(client, tokens, code):
    make_programme("kit", code, 10)
    eid = post_claim(client, tokens["ia.kit"], claim_body(make_people(1)[0], code)).json()["enrolment_id"]
    tok = _student_user(client, eid)
    assert client.post(f"/api/v1/student/enrolments/{eid}/dispute", headers=auth(tok), json={"note": "I did not apply"}).status_code == 200
    with owner_tx() as c:
        cid = str(scalar(c, "SELECT id FROM intel.cases WHERE enrolment_id=CAST(:e AS uuid)", e=eid))
    return eid, cid


def test_disputed_enrolment_leaves_disputed_only_through_a_case_outcome(client, tokens):
    eid, cid = _dispute_case(client, tokens, "T-DS1")
    h = lambda u, **x: auth(tokens[u], **x)
    assert client.get(f"/api/v1/enrolments/{eid}", headers=h("ia.kit")).json()["verification"]["overall_state"] == "DISPUTED"
    client.post(f"/api/v1/cases/{cid}/start-review", headers=h("ar.national"))
    assert client.post(f"/api/v1/cases/{cid}/close", headers=h("ar.national"), json={"outcome_code": "NOT_SUBSTANTIATED", "notes": "Student confirmed it was them."}).status_code == 200
    v = client.get(f"/api/v1/enrolments/{eid}", headers=h("ia.kit")).json()["verification"]
    assert v["overall_state"] == "PENDING" and v["lock3_status"] == "PENDING"                                           # back to waiting for the student, not auto-VERIFIED


def test_release_seat_needs_step_up_reason_and_notifies_institution(client, tokens):
    eid, cid = _dispute_case(client, tokens, "T-DS2")
    h = lambda u, **x: auth(tokens[u], **x)
    assert bucket_state("kit", "T-DS2")["occupied"] == 1
    assert client.post(f"/api/v1/cases/{cid}/release-seat", headers=h("ar.national"), json={"reason": "Identity was borrowed"}).json()["code"] == "STEP_UP_REQUIRED"
    assert client.post(f"/api/v1/cases/{cid}/release-seat", headers=h("ia.kit", **step_up(client, tokens["ia.kit"])), json={"reason": "I want it"}).status_code == 403
    assert client.post(f"/api/v1/cases/{cid}/release-seat", headers=h("ar.national", **step_up(client, tokens["ar.national"])), json={"reason": "x"}).status_code == 422
    ok = client.post(f"/api/v1/cases/{cid}/release-seat", headers=h("ar.national", **step_up(client, tokens["ar.national"])), json={"reason": "Identity was borrowed; student disputes."})
    assert ok.status_code == 200 and ok.json()["status"] == "WITHDRAWN"
    assert bucket_state("kit", "T-DS2")["occupied"] == 0
    with owner_tx() as c:
        assert scalar(c, "SELECT withdrawn_reason FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=eid) == "AICTE_RELEASE"
        assert scalar(c, "SELECT count(*) FROM core.enrolment_events WHERE enrolment_id=CAST(:e AS uuid) AND event_type='SEAT_RELEASE'", e=eid) == 1
        assert scalar(c, "SELECT count(*) FROM ops.notifications WHERE template_code='SEAT_RELEASED' AND created_at > now() - interval '1 minute'") >= 1
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='enrolment.seat_release' AND entity_id=:e", e=eid) == 1


def test_elevation_grant_is_case_gated_time_limited_and_audited(client, tokens):
    eid, cid = _dispute_case(client, tokens, "T-EL1")
    h = lambda u: auth(tokens[u])
    # without a grant: no student-level data, ever
    r = client.post(f"/api/v1/cases/{cid}/reveal", headers=h("ar.national"))
    assert r.status_code == 403 and r.json()["code"] == "ELEVATION_REQUIRED"
    assert client.get(f"/api/v1/enrolments/{eid}", headers=h("ar.national")).status_code == 403
    body = {"case_id": cid, "justification": "Investigating a student dispute about a borrowed identity.", "duration_minutes": 30}
    assert client.post("/api/v1/me/elevations", headers=h("ar.national"), json={**body, "justification": "too short"}).status_code == 422
    assert client.post("/api/v1/me/elevations", headers=h("ar.national"), json={**body, "duration_minutes": 241}).status_code == 422
    assert client.post("/api/v1/me/elevations", headers=h("ia.kit"), json=body).status_code == 403
    assert client.post("/api/v1/me/elevations", headers=h("ar.south"), json={**body, "case_id": str(uuid.uuid4())}).status_code == 404
    g = client.post("/api/v1/me/elevations", headers=h("ar.national"), json=body)
    assert g.status_code == 200 and 0 < (pd.Timestamp(g.json()["expires_at"]) - pd.Timestamp.now(tz="UTC")).total_seconds() <= 1800 + 5
    rev = client.post(f"/api/v1/cases/{cid}/reveal", headers=h("ar.national")).json()
    assert rev["people"] and rev["people"][0]["name"] and "audited" in rev["notice"] and "•" in (rev["people"][0]["mobile_masked"] or "•")
    assert "aadhaar" not in str(rev).lower()
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='access.student_revealed' AND case_id=CAST(:c AS uuid) AND justification LIKE 'Investigating%'", c=cid) >= 1
        assert scalar(c, "SELECT records_revealed FROM pii.access_grants WHERE case_id=CAST(:c AS uuid)", c=cid) >= 1
        with pytest.raises(Exception):                                                                                   # grants are capped at 4 hours in the database
            c.execute(text("UPDATE pii.access_grants SET expires_at = granted_at + interval '5 hours' WHERE case_id=CAST(:c AS uuid)"), {"c": cid})
    assert client.get("/api/v1/me/elevations", headers=h("ar.national")).json()["items"]
    # regional reviewer cannot reuse someone else's grant; an expired grant stops working at once
    assert client.post(f"/api/v1/cases/{cid}/reveal", headers=h("ar.national2")).status_code == 403
    with owner_tx() as c:
        c.execute(text("UPDATE pii.access_grants SET granted_at = now() - interval '2 hours', expires_at = now() - interval '1 minute' WHERE case_id=CAST(:c AS uuid)"), {"c": cid})
    assert client.post(f"/api/v1/cases/{cid}/reveal", headers=h("ar.national")).json()["code"] == "ELEVATION_REQUIRED"


def test_duplicate_reveal_needs_a_grant_for_one_of_the_two_institutions(client, tokens):
    with owner_tx() as c:
        cand = rows(c, "SELECT c.id, i.slug FROM matching.match_candidates c JOIN ref.institutions i ON i.id=c.institution_a WHERE c.status='REVIEW' LIMIT 1")
    if not cand:
        pytest.skip("no review candidates in this seed")
    cid = str(cand[0]["id"])
    with owner_tx() as c:                                   # earlier tests may have left live grants for the same user
        c.execute(text("UPDATE pii.access_grants SET revoked_at = now() WHERE revoked_at IS NULL"))
    assert client.post(f"/api/v1/duplicates/{cid}/reveal", headers=auth(tokens["ar.national"])).json()["code"] == "ELEVATION_REQUIRED"


def test_access_volume_alert_over_200_reveals_per_hour(client, tokens):
    eid, cid = _dispute_case(client, tokens, "T-EL2")
    h = lambda u: auth(tokens[u])
    client.post("/api/v1/me/elevations", headers=h("ar.national"), json={"case_id": cid, "justification": "Bulk review of a ghost-ring case for the volume alert test.", "duration_minutes": 30})
    with owner_tx() as c:
        uid = scalar(c, "SELECT id FROM iam.users WHERE username='ar.national'")
        c.execute(text("INSERT INTO audit.audit_log (occurred_at, actor_id, action, entity_type, payload) SELECT now(), :u, 'access.student_revealed', 'enrolment', '{}' FROM generate_series(1,201)"), {"u": uid})
    client.post(f"/api/v1/cases/{cid}/reveal", headers=h("ar.national"))
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='access.volume_alert' AND actor_id=:u", u=uid) >= 1


# ------------------------------------------------------------------ reports
def _report(client, tok, rtype, fmt):
    j = client.post("/api/v1/reports", headers=auth(tok), json={"type": rtype, "format": fmt})
    assert j.status_code == 200, j.text
    drain()
    lst = client.get("/api/v1/reports", headers=auth(tok)).json()["items"]
    job = next(x for x in lst if x["id"] == j.json()["id"])
    assert job["status"] == "DONE", job
    d = client.get(f"/api/v1/reports/{job['id']}/download", headers=auth(tok))
    assert d.status_code == 200
    return job, d.content


def test_reports_in_all_formats_are_stamped_hashed_and_verifiable(client, tokens):
    job, csv_b = _report(client, tokens["ar.national"], "SEAT_UTILISATION", "CSV")
    txt = csv_b.decode("utf-8-sig")
    assert txt.startswith("# Seat utilisation") and "# Rule set version: v1" in txt and "# Cut-off:" in txt and "# Generated by: ar.national" in txt and "Occupied = CLAIMED or ADMITTED" in txt
    assert f"/api/v1/public/reports/{job['ref']}" in txt and hashlib.sha256(csv_b).hexdigest() == job["sha256"]
    pub = client.get(f"/api/v1/public/reports/{job['ref']}")
    assert pub.status_code == 200 and pub.json()["sha256"] == job["sha256"] and set(pub.json()) == {"title", "generated_at", "sha256", "rule_set_version", "format", "note"}
    assert client.get("/api/v1/public/reports/nonexistent-ref").status_code == 404
    _, x = _report(client, tokens["ar.national"], "TRUST_INDEX", "XLSX")
    assert zipfile.ZipFile(io.BytesIO(x)).testzip() is None and b"xl/worksheets/sheet1.xml" in b"".join(zipfile.ZipFile(io.BytesIO(x)).namelist()[i].encode() for i in range(len(zipfile.ZipFile(io.BytesIO(x)).namelist())))
    _, pdf = _report(client, tokens["ia.kit"], "VERIFICATION_STATUS", "PDF")
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000
    for t in ("ENROLMENT_SUMMARY", "FINDINGS", "COMPLIANCE", "CLAIM_ACTIVITY"):
        _report(client, tokens["ar.national"], t, "CSV")
    _, pack = _report(client, tokens["ia.kit"], "INSTITUTION_PACK", "CSV")
    assert b"Kaveri" in pack and b"Bharatiya" not in pack


def test_report_scope_privacy_and_formula_safety(client, tokens):
    assert reports.safe("=HYPERLINK(1)") == "'=HYPERLINK(1)" and reports.safe("-1+2") == "'-1+2" and reports.safe("Plain") == "Plain" and reports.safe(5) == 5
    assert reports.supp(3) == "<5" and reports.supp(0) == 0 and reports.supp(5) == 5 and reports.supp("x") == "x"
    _, south = _report(client, tokens["ar.south"], "VERIFICATION_STATUS", "CSV")
    assert b"Tamil Nadu" in south and b"Maharashtra" not in south and b"Odisha" not in south                               # regional scope applies to reports
    _, own = _report(client, tokens["ia.bvec"], "SEAT_UTILISATION", "CSV")
    assert b"Bharatiya" in own and b"Kaveri" not in own
    # AICTE cannot export student-level data or an institution pack; types are listed per role
    for t in ("ENROLMENT_EXPORT", "INSTITUTION_PACK"):
        assert client.post("/api/v1/reports", headers=auth(tokens["ar.national"]), json={"type": t, "format": "CSV"}).status_code == 403
    assert "ENROLMENT_EXPORT" not in [x["code"] for x in client.get("/api/v1/reports/types", headers=auth(tokens["ar.national"])).json()["items"]]
    assert client.post("/api/v1/reports", headers=auth(tokens["ia.kit"]), json={"type": "NOPE", "format": "CSV"}).status_code == 422
    assert client.post("/api/v1/reports", headers=auth(tokens["io.kit"]), json={"type": "FINDINGS", "format": "CSV"}).status_code == 403
    # a report belongs to its requester
    j = client.post("/api/v1/reports", headers=auth(tokens["ia.kit"]), json={"type": "FINDINGS", "format": "CSV"}).json()
    drain()
    assert client.get(f"/api/v1/reports/{j['id']}/download", headers=auth(tokens["ia.bvec"])).status_code == 404
    assert "aadhaar" not in _report(client, tokens["ar.national"], "VERIFICATION_STATUS", "CSV")[1].decode("utf-8-sig").lower().replace("never stored or printed", "")


def test_institution_enrolment_export_is_student_level_decrypted_and_audited(client, tokens):
    job, data = _report(client, tokens["ia.kit"], "ENROLMENT_EXPORT", "CSV")
    text_ = data.decode("utf-8-sig")
    assert "Name,Date of birth" in text_ and "Aarav" in text_ and "STU-" not in text_
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='report.enrolment_export' AND (payload->>'student_level')='true'") >= 1
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='report.downloaded' AND entity_id=:j", j=job["id"]) == 1
    assert client.post("/api/v1/reports", headers=auth(tokens["io.kit"]), json={"type": "ENROLMENT_EXPORT", "format": "CSV"}).status_code == 403


def test_seed_has_cases_alerts_and_labels(client, tokens):
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM intel.anomaly_alerts") >= 3 and scalar(c, "SELECT count(*) FROM intel.cases WHERE type='ANOMALY'") >= 3
        st = {r["status"] for r in rows(c, "SELECT DISTINCT status FROM intel.cases")}
    assert {"OPEN", "AWAITING_RESPONSE"} <= st
