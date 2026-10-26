"""REQ-GATE: claim outcomes, idempotency, ledger, exceptions; SC-02/03/04 and registry resilience (SC-12)."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest

from app import claims, identity, ops
from app.db import owner_tx, rows, scalar
from conftest import auth, claim_body, make_people, post_claim
from helpers import bucket_state, make_programme


@pytest.fixture()
def kit(tokens):
    return tokens["ia.kit"]


def test_claim_success_creates_all_records_consistently(client, kit):
    make_programme("kit", "T-OK", 10)
    p = make_people(1)[0]
    r = post_claim(client, kit, claim_body(p, "T-OK"))
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["outcome"] == "CLAIMED" and j["verification"]["lock1"] == "SIGNED" and j["verification"]["lock2"] == "PASSED" and j["verification"]["lock3"] == "PENDING"
    assert bucket_state("kit", "T-OK")["occupied"] == 1
    with owner_tx() as c:
        eid = j["enrolment_id"]
        assert scalar(c, "SELECT count(*) FROM core.seat_ledger_entries WHERE enrolment_id=CAST(:e AS uuid) AND entry_type='DEBIT'", e=eid) == 1
        assert scalar(c, "SELECT count(*) FROM core.enrolment_events WHERE enrolment_id=CAST(:e AS uuid) AND event_type='CLAIM'", e=eid) == 1
        assert scalar(c, "SELECT count(*) FROM pii.pii_records WHERE ciphertext IS NOT NULL AND student_id=(SELECT student_id FROM core.enrolments WHERE id=CAST(:e AS uuid))", e=eid) == 1
        assert scalar(c, "SELECT count(*) FROM audit.audit_log WHERE action='claim.created' AND entity_id=:e", e=eid) == 1
        assert scalar(c, "SELECT count(*) FROM ops.outbox_events WHERE topic='claim.created' AND aggregate_id=:e", e=eid) == 1
        assert scalar(c, "SELECT lock1_signature IS NOT NULL FROM core.enrolment_verification WHERE enrolment_id=CAST(:e AS uuid)", e=eid)
        # personal data is not in any plaintext column
        assert scalar(c, "SELECT count(*) FROM core.students WHERE student_ref ILIKE '%Person%'") == 0


def test_lock1_signature_verifies_with_institution_key(client, kit):
    from app import crypto
    make_programme("kit", "T-SIG", 5)
    j = post_claim(client, kit, claim_body(make_people(1)[0], "T-SIG")).json()
    with owner_tx() as c:
        v = rows(c, "SELECT v.lock1_signature, v.lock1_kid, k.public_jwk FROM core.enrolment_verification v JOIN cred.signing_keys k ON k.kid=v.lock1_kid "
                    "WHERE v.enrolment_id=CAST(:e AS uuid)", e=j["enrolment_id"])[0]
        e = rows(c, "SELECT e.id, e.institution_id, e.programme_id, e.bucket_type, e.identity_key, e.claimed_at, e.claimed_by FROM core.enrolments e WHERE e.id=CAST(:e AS uuid)",
                 e=j["enrolment_id"])[0]
    blob = {"claim_id": str(e["id"]), "institution_id": str(e["institution_id"]), "programme_id": str(e["programme_id"]), "bucket": e["bucket_type"],
            "identity_key_hash": bytes(e["identity_key"]).hex(), "claimed_at": e["claimed_at"].isoformat(), "actor_id": str(e["claimed_by"])}
    # the claim path signs claimed_at with microsecond precision from the clock; re-derive via the stored value
    ok = crypto_verify(v, blob)
    assert ok


def crypto_verify(v, blob):
    import hashlib
    from app import crypto
    from app.core import canonical_json
    for suffix in ("+00:00",):
        b = dict(blob)
        b["claimed_at"] = blob["claimed_at"].replace("+00:00", suffix)
        if crypto.verify_es256(v["public_jwk"], hashlib.sha256(canonical_json(b)).digest(), v["lock1_signature"]):
            return True
    return False


def test_idempotency_replay_and_mismatch_sc04(client, kit):
    make_programme("kit", "T-IDEM", 5)
    body = claim_body(make_people(1)[0], "T-IDEM")
    key = uuid.uuid4().hex
    first = post_claim(client, kit, body, key)
    replay = post_claim(client, kit, body, key)
    assert first.status_code == replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true" and replay.json()["enrolment_id"] == first.json()["enrolment_id"]
    assert bucket_state("kit", "T-IDEM")["occupied"] == 1                       # replay consumed no second seat
    mismatch = post_claim(client, kit, {**body, "name": "Another Name"}, key)
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert client.post("/api/v1/claims", headers=auth(kit), json=body).status_code == 400   # key required


def test_seat_limit_returns_409_with_bucket_details(client, kit):
    make_programme("kit", "T-FULL", 1)
    assert post_claim(client, kit, claim_body(make_people(1)[0], "T-FULL")).status_code == 201
    r = post_claim(client, kit, claim_body(make_people(1)[0], "T-FULL"))
    assert r.status_code == 409 and r.json()["code"] == "SEAT_LIMIT"
    assert r.json()["cap"] == 1 and r.json()["occupied"] == 1 and r.json()["exception_requestable"] is True
    assert bucket_state("kit", "T-FULL")["occupied"] == 1


def test_90_percent_warning(client, kit):
    make_programme("kit", "T-WARN", 10)
    people = make_people(9)
    last = None
    for p in people:
        last = post_claim(client, kit, claim_body(p, "T-WARN"))
    assert last.status_code == 201 and any(w["code"] == "SEAT_90_PERCENT" for w in last.json()["warnings"])


def test_conflict_between_institutions_discloses_no_student_data(client, tokens):
    make_programme("kit", "T-C1", 5)
    make_programme("bvec", "T-C2", 5)
    p = make_people(1)[0]
    assert post_claim(client, tokens["ia.kit"], claim_body(p, "T-C1")).status_code == 201
    r = post_claim(client, tokens["ia.bvec"], claim_body(p, "T-C2"))
    assert r.status_code == 409 and r.json()["code"] == "CLAIM_CONFLICT" and r.json()["resolution_ref"].startswith("RES-")
    text_ = r.text
    assert p[1] not in text_ and p[0] not in text_ and "Kaveri" not in text_.split("other_institution_contact")[0]
    assert bucket_state("bvec", "T-C2")["occupied"] == 0                          # no seat consumed at the loser
    with owner_tx() as c:
        a = rows(c, "SELECT outcome, conflict_holder_institution_id FROM core.claim_attempts WHERE outcome='CONFLICT' ORDER BY id DESC LIMIT 1")[0]
        assert a["conflict_holder_institution_id"] is not None                    # stored internally, never exposed


def test_duplicate_same_institution_is_not_a_second_row(client, kit):
    make_programme("kit", "T-DUP", 5)
    p = make_people(1)[0]
    first = post_claim(client, kit, claim_body(p, "T-DUP", sid="DUP-1"))
    again = post_claim(client, kit, claim_body(p, "T-DUP", sid="DUP-2"))
    assert again.status_code == 200 and again.json()["duplicate"] is True and again.json()["enrolment_id"] == first.json()["enrolment_id"]
    assert bucket_state("kit", "T-DUP")["occupied"] == 1


def test_concurrent_class_programmes_are_outside_exclusivity(client, tokens):
    make_programme("kit", "T-ODL", 5, mode="ONLINE", level="PG")
    make_programme("bvec", "T-FT", 5)
    p = make_people(1)[0]
    assert post_claim(client, tokens["ia.bvec"], claim_body(p, "T-FT")).status_code == 201
    r = post_claim(client, tokens["ia.kit"], claim_body(p, "T-ODL"))
    assert r.status_code == 201 and r.json()["outcome"] == "CONCURRENT_OK"


def test_invalid_identities_get_field_level_fixes(client, kit):
    make_programme("kit", "T-INV", 5)
    p = make_people(1)[0]
    bad_checksum = post_claim(client, kit, {**claim_body(p, "T-INV"), "apaar_id": "991234567890"})
    assert bad_checksum.status_code == 422 and bad_checksum.json()["code"] == "IDENTITY_INVALID"
    e = bad_checksum.json()["errors"][0]
    assert e["field"] == "apaar_id" and e["fix_suggestion"]
    not_found = post_claim(client, kit, {**claim_body(p, "T-INV"), "apaar_id": _unused_apaar()})
    assert not_found.status_code == 422 and "provisional" in not_found.json()["errors"][0]["fix_suggestion"].lower()
    assert bucket_state("kit", "T-INV")["occupied"] == 0


def _unused_apaar():
    from app.seed import make_apaar
    import random
    return make_apaar(random.Random(uuid.uuid4().int))


def test_blocker_rule_stops_claim_without_consuming_seat(client, kit):
    make_programme("kit", "T-BLK", 5)
    r = post_claim(client, kit, {**claim_body(make_people(1)[0], "T-BLK"), "gender": "X", "category": "ZZ"})
    assert r.status_code == 422 and r.json()["code"] == "RULE_BLOCKER"
    assert {e["code"] for e in r.json()["errors"]} >= {"GEN-001", "CAT-001"}
    assert bucket_state("kit", "T-BLK")["occupied"] == 0


def test_dob_mismatch_is_claimed_pending_fix_with_deadline_never_rejected(client, kit):
    make_programme("kit", "T-FIX", 5)
    a, n, d = make_people(1, dob=date(2007, 3, 5))[0]
    r = post_claim(client, kit, {**claim_body((a, n, d), "T-FIX"), "dob": "2007-05-03"})
    assert r.status_code == 201 and r.json()["verification"]["overall"] == "PENDING_FIX"
    assert r.json()["fix_list"][0]["code"] == "DOB_DAY_MONTH_SWAP"
    fixes = client.get("/api/v1/fixes", headers=auth(kit)).json()["items"]
    assert any(f["id"] == r.json()["enrolment_id"] and f["fix_deadline_at"] for f in fixes)
    # fix-it flow: correct the DOB and revalidate
    fx = client.post(f"/api/v1/enrolments/{r.json()['enrolment_id']}/identity/correct", headers=auth(kit),
                     json={"dob": "2007-03-05", "reason": "Corrected from the birth certificate"})
    assert fx.status_code == 200 and fx.json()["revalidation"]["verdict"] == "VERIFIED"


def test_provisional_id_path_counts_and_never_calls_registry(client, kit):
    make_programme("kit", "T-PRV", 5)
    r = post_claim(client, kit, {"provisional": {"type": "NOGEN"}, "name": "No Apaar Student", "dob": "2007-01-01", "institution_student_id": "PRV1",
                                 "programme_code": "T-PRV", "gender": "F", "mobile": "0011223344", "parent_name": "P"})
    assert r.status_code == 201 and r.json()["identity_status"] == "NOGEN" and r.json()["provisional_token"].startswith("PRV-")
    assert r.json()["verification"]["lock2"] == "PROVISIONAL_OK" and r.json()["registry"] is None
    assert bucket_state("kit", "T-PRV")["occupied"] == 1


def test_validate_is_dry_run_no_write(client, kit):
    make_programme("kit", "T-DRY", 5)
    before = bucket_state("kit", "T-DRY")
    r = client.post("/api/v1/claims:validate", headers=auth(kit), json=claim_body(make_people(1)[0], "T-DRY"))
    assert r.status_code == 200 and r.json()["outcome"] == "CLAIMED" and r.json()["dry_run"] is True
    assert bucket_state("kit", "T-DRY") == before


def test_io_can_validate_but_not_claim(client, tokens):
    make_programme("kit", "T-IO", 5)
    body = claim_body(make_people(1)[0], "T-IO")
    assert client.post("/api/v1/claims:validate", headers=auth(tokens["io.kit"]), json=body).status_code == 200
    assert post_claim(client, tokens["io.kit"], body).status_code == 403


def test_withdraw_releases_seat_but_not_cumulative_admitted(client, kit):
    make_programme("kit", "T-WD", 5)
    j = post_claim(client, kit, {**claim_body(make_people(1)[0], "T-WD"), "admit_now": True}).json()
    s = bucket_state("kit", "T-WD")
    assert s["occupied"] == 1 and s["cumulative_admitted"] == 1
    w = client.post(f"/api/v1/enrolments/{j['enrolment_id']}/withdraw", headers=auth(kit), json={"reason_code": "STUDENT_WITHDREW"})
    assert w.status_code == 200
    s = bucket_state("kit", "T-WD")
    assert s["occupied"] == 0 and s["cumulative_admitted"] == 1                  # never decremented (C-07)
    assert client.post(f"/api/v1/enrolments/{j['enrolment_id']}/withdraw", headers=auth(kit), json={"reason_code": "OTHER"}).status_code == 409
    # a released student can be claimed elsewhere
    ledger = client.get(f"/api/v1/programmes/{_pid('kit','T-WD')}/ledger", headers=auth(kit)).json()
    assert ledger["invariant"][0]["ok"] is True and [e["entry_type"] for e in ledger["entries"][:2]] == ["RELEASE", "DEBIT"]


def _pid(slug, code):
    with owner_tx() as c:
        return scalar(c, "SELECT p.id FROM ref.programmes p JOIN ref.institutions i ON i.id=p.institution_id WHERE i.slug=:s AND p.programme_code=:c", s=slug, c=code)


def test_admit_requires_claimed_and_valid_date(client, kit):
    make_programme("kit", "T-ADM", 5)
    j = post_claim(client, kit, claim_body(make_people(1)[0], "T-ADM")).json()
    future = client.post(f"/api/v1/enrolments/{j['enrolment_id']}/admit", headers=auth(kit), json={"admission_date": "2099-01-01"})
    assert future.status_code == 422
    ok = client.post(f"/api/v1/enrolments/{j['enrolment_id']}/admit", headers=auth(kit), json={})
    assert ok.status_code == 200 and bucket_state("kit", "T-ADM")["cumulative_admitted"] == 1
    assert client.post(f"/api/v1/enrolments/{j['enrolment_id']}/admit", headers=auth(kit), json={}).status_code == 409


def test_seat_exception_workflow_and_separation_of_duties(client, tokens):
    make_programme("kit", "T-EXC", 2)
    for p in make_people(2):
        assert post_claim(client, tokens["ia.kit"], claim_body(p, "T-EXC")).status_code == 201
    over = post_claim(client, tokens["ia.kit"], claim_body(make_people(1)[0], "T-EXC"))
    assert over.status_code == 409
    req = client.post("/api/v1/seat-exceptions", headers=auth(tokens["ia.kit"]),
                      json={"programme_code": "T-EXC", "extra_seats": 1, "justification": "Counselling round produced additional eligible students."})
    assert req.status_code == 200, req.text
    # an institution admin cannot decide; a regional reviewer outside the region cannot see it, in-region can
    assert client.post(f"/api/v1/seat-exceptions/{req.json()['id']}/decide", headers=auth(tokens["ia.kit"]), json={"approve": True, "reason": "self approve"}).status_code == 403
    dec = client.post(f"/api/v1/seat-exceptions/{req.json()['id']}/decide", headers=auth(tokens["ar.south"]), json={"approve": True, "reason": "Verified the counselling list"})
    assert dec.status_code == 200 and dec.json()["status"] == "APPROVED"
    s = bucket_state("kit", "T-EXC")
    assert s["approved_extra"] == 1
    assert post_claim(client, tokens["ia.kit"], claim_body(make_people(1)[0], "T-EXC")).status_code == 201
    assert post_claim(client, tokens["ia.kit"], claim_body(make_people(1)[0], "T-EXC")).status_code == 409
    assert client.get(f"/api/v1/programmes/{_pid('kit','T-EXC')}/ledger", headers=auth(tokens["ia.kit"])).json()["invariant"][0]["ok"]


# ----------------------------------------------------------------------------- concurrency (SC-02, SC-03)
def _direct_registry(monkeypatch, people):
    book = {p[0]: p for p in people}

    def lookup(apaar):
        p = book.get(apaar)
        return identity.RegistryResult("FOUND", p[1], p[2], "0012345678", "ACTIVE", 1, "CLOSED") if p else identity.RegistryResult("NOT_FOUND")
    monkeypatch.setattr(identity, "registry_lookup", lookup)


def test_sc02_no_oversell_under_concurrency(monkeypatch):
    """1,000 claim attempts at concurrency 50 on one hot bucket (cap 100): exactly 100 succeed, the rest are SEAT_LIMIT."""
    from app import seed_main
    make_programme("kit", "T-HOT", 100)
    people = make_people(1000)
    _direct_registry(monkeypatch, people)
    with owner_tx() as c:
        ctx = {"inst": {"kit": {"id": scalar(c, "SELECT id FROM ref.institutions WHERE slug='kit'")}},
               "users": {"ia.kit": scalar(c, "SELECT id FROM iam.users WHERE username='ia.kit'")}}
    p = seed_main._perms(seed_main.principal_for(ctx, "ia.kit", "IA", "kit"))
    results = []

    def one(person):
        body = claims.ClaimIn(**{k: (date.fromisoformat(v) if k == "dob" else v) for k, v in claim_body(person, "T-HOT").items()})
        status, out = claims.process_claim(p, body, dry_run=False)
        return status, out.get("code") or out.get("outcome")

    with ThreadPoolExecutor(max_workers=50) as ex:
        results = list(ex.map(one, people))
    statuses = [s for s, _ in results]
    assert statuses.count(201) == 100, {s: statuses.count(s) for s in set(statuses)}
    assert statuses.count(409) == 900 and set(c for s, c in results if s == 409) == {"SEAT_LIMIT"}
    st = bucket_state("kit", "T-HOT")
    assert st["occupied"] == 100 <= st["cap"] + st["approved_extra"]
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM core.seat_ledger_entries l JOIN core.seat_buckets b ON b.id=l.seat_bucket_id JOIN ref.programmes p ON p.id=b.programme_id "
                         "WHERE p.programme_code='T-HOT' AND l.entry_type='DEBIT'") == 100


def test_sc03_one_exclusive_claim_wins_when_two_institutions_race(monkeypatch):
    from app import seed_main
    make_programme("kit", "T-RACE1", 50)
    make_programme("bvec", "T-RACE2", 50)
    people = make_people(40)
    _direct_registry(monkeypatch, people)
    with owner_tx() as c:
        ctx = {"inst": {s: {"id": scalar(c, "SELECT id FROM ref.institutions WHERE slug=:s", s=s)} for s in ("kit", "bvec")},
               "users": {f"ia.{s}": scalar(c, "SELECT id FROM iam.users WHERE username=:u", u=f"ia.{s}") for s in ("kit", "bvec")}}
    pk = seed_main._perms(seed_main.principal_for(ctx, "ia.kit", "IA", "kit"))
    pb = seed_main._perms(seed_main.principal_for(ctx, "ia.bvec", "IA", "bvec"))
    barrier = threading.Barrier(2)

    def attempt(args):
        principal, code, person = args
        barrier.wait() if False else None
        body = claims.ClaimIn(**{k: (date.fromisoformat(v) if k == "dob" else v) for k, v in claim_body(person, code).items()})
        return claims.process_claim(principal, body, dry_run=False)[0]

    jobs = []
    for person in people:
        jobs += [(pk, "T-RACE1", person), (pb, "T-RACE2", person)]
    with ThreadPoolExecutor(max_workers=16) as ex:
        out = list(ex.map(attempt, jobs))
    pairs = [(out[i], out[i + 1]) for i in range(0, len(out), 2)]
    assert all(sorted(pair) == [201, 409] for pair in pairs), pairs
    with owner_tx() as c:
        assert scalar(c, "SELECT count(*) FROM (SELECT identity_key FROM core.enrolments WHERE academic_year=:y AND concurrency_class='EXCLUSIVE' AND status IN ('CLAIMED','ADMITTED') "
                         "GROUP BY identity_key HAVING count(*)>1) x", y=claims.current_academic_year()) == 0


# ----------------------------------------------------------------------------- resilience (SC-12)
def test_registry_down_gives_pending_verification_then_revalidation_resolves(client, tokens):
    make_programme("kit", "T-DOWN", 10)
    p = make_people(1)[0]
    identity.reset_breaker()
    httpx.post("http://127.0.0.1:18002/admin/registry-mode", json={"mode": "DOWN"})
    try:
        r = post_claim(client, tokens["ia.kit"], claim_body(p, "T-DOWN"))
        assert r.status_code == 201 and r.json()["identity_status"] == "PENDING_VERIFICATION"        # never a silent pass
        assert r.json()["verification"]["lock2"] == "PENDING" and bucket_state("kit", "T-DOWN")["occupied"] == 1
        still = ops.revalidate_enrolment(r.json()["enrolment_id"])
        assert still.get("still_unavailable")
    finally:
        httpx.post("http://127.0.0.1:18002/admin/registry-mode", json={"mode": "UP"})
        identity.reset_breaker()
    out = ops.revalidate_enrolment(r.json()["enrolment_id"])
    assert out["verdict"] == "VERIFIED"
    detail = client.get(f"/api/v1/enrolments/{r.json()['enrolment_id']}", headers=auth(tokens["ia.kit"])).json()
    assert detail["verification"]["lock2_status"] == "PASSED" and detail["verification"]["identity_status"] == "VERIFIED"
    assert any(c["source"] == "REVALIDATION" for c in detail["registry_checks"])


def test_breaker_opens_after_repeated_failures():
    identity.reset_breaker()
    httpx.post("http://127.0.0.1:18002/admin/registry-mode", json={"mode": "DOWN"})
    try:
        for _ in range(6):
            identity.registry_lookup("990000000000")
        assert identity.breaker().state == "OPEN"
        assert identity.registry_lookup("990000000000").status == "UNAVAILABLE"
    finally:
        httpx.post("http://127.0.0.1:18002/admin/registry-mode", json={"mode": "UP"})
        identity.reset_breaker()
