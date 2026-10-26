"""Seed for the final feature set: verifier orgs, credentials, consents, verification history, corrections, DSRs, webhooks,
draft rule set, onboarding fixture, retention policies. Story records go through the real services."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from . import admin, credentials, crypto, keys, rulesets, seed, verifier, wallet
from .config import get_settings
from .core import dumps, now, uuid7
from .db import owner_tx, row, rows, scalar
from .deps import hash_password
from .seed import bulk, did
from .seed_main import _perms, principal_for

ORGS = [("vu.employer", "Meridian Software Services Pvt Ltd", "EMPLOYER", "EMPLOYMENT_VERIFICATION", ["institution_name", "programme_name", "academic_year", "enrolment_status"], ["IS_ENROLLED", "PROGRAMME_MATCH", "ENROLLED_IN_YEAR"], 60,
         "Verifier flow: purpose-bound enrolment check"),
        ("vu.bank", "Sagarmatha Co-operative Bank", "BANK", "LOAN_ELIGIBILITY", ["institution_name", "enrolment_status", "approved_seat"], ["IS_ENROLLED", "ENROLLED_IN_YEAR"], 40, "Different purpose scope and fewer fields"),
        ("vu.embassy", "Visa Section, Republic of Ardenia (fictional)", "EMBASSY", "VISA_SUPPORT", ["institution_name", "academic_year"], ["IS_ENROLLED"], 20, "Strict field scope")]
NOTICE = {"en": "Consent notice v1: you agree that the named organisation may ask whether you are enrolled, for the stated purpose only, and receive only the fields you tick. You can withdraw at any time. Your enrolment is recorded under statutory authority whether or not you consent.",
          "hi": "सहमति सूचना v1: आप सहमत हैं कि नामित संस्था केवल बताए गए उद्देश्य के लिए यह पूछ सकती है कि आप नामांकित हैं या नहीं, और केवल वही जानकारी पाएगी जिसे आप चुनेंगे। आप कभी भी सहमति वापस ले सकते हैं। आपका नामांकन सहमति के बिना भी वैधानिक अधिकार के अंतर्गत दर्ज होता है।"}


def run(ctx: dict, story: dict, rng: random.Random, profile: dict, year: str) -> dict:
    s = get_settings()
    pw = hash_password(s.seed_demo_password)
    stats: dict = {}
    # ---- verifier organisations, scopes, accounts, notices, retention policies
    with owner_tx() as conn:
        for uname, name, tier, purpose, fields, questions, limit, desc in ORGS:
            oid = did("vorg", uname)
            conn.execute(text("INSERT INTO iam.verifier_orgs (id, name, tier, contact_email) VALUES (:id,:n,:t,:e)"), {"id": oid, "n": name, "t": tier, "e": f"verify@{uname.split('.')[1]}.example.test"})
            conn.execute(text("INSERT INTO iam.verifier_scopes (id, verifier_org_id, purpose_code, allowed_fields, allowed_questions, hourly_limit) VALUES (:id,:o,:p,:f,:q,:h)"),
                         {"id": did("vscope", uname), "o": oid, "p": purpose, "f": fields, "q": questions, "h": limit})
            uid = did("user", uname)
            ctx["users"][uname] = uid
            conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, description, utype, password_hash, is_demo, verifier_org_id) VALUES (:id,:u,:d,:e,:ds,'VERIFIER',:pw,true,:o)"),
                         {"id": uid, "u": uname, "d": f"Verifier, {name}", "e": f"{uname}@example.test", "ds": desc, "pw": pw, "o": oid})
            conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type) VALUES (:id,:u,'VU','NATIONAL')"), {"id": did("ur", uname), "u": uid})
        for lang, body in NOTICE.items():
            conn.execute(text("INSERT INTO consent.consent_notices (id, code, version, language, body) VALUES (:id,'SHARING',1,:l,:b)"), {"id": did("notice", lang), "l": lang, "b": body})
        bulk(conn, "INSERT INTO ops.retention_policies (id, data_class, retention_days, action) VALUES (:id,:c,:d,:a)", [{"id": did("ret", c), "c": c, "d": d, "a": a} for c, d, a in admin.RETENTION])
        # an expired RETIRED sample key so the key-status view has something to show (REQ-CRD-07)
        pem, jwk = crypto.new_es256()
        jwk["kid"] = "issuer-retired-2025"
        conn.execute(text("INSERT INTO cred.signing_keys (id, kid, purpose, public_jwk, private_wrapped, status) VALUES (:id,'issuer-retired-2025','ISSUER',CAST(:j AS jsonb),:p,'RETIRED')"), {"id": did("key", "retired"), "j": json.dumps(jwk), "p": crypto.wrap(pem)})
    # ---- draft rule set v2 for the simulator demo (illustrative: stricter age rules)
    ia_as = _perms(principal_for(ctx, "as.admin", "AS"))
    d = rulesets.create_draft(ia_as, "Draft v2: stricter age checks (simulator demo)", False)
    rulesets.update_rule(ia_as, d["id"], "AGE-001", {"left": {"field": "age_years"}, "op": ">=", "right": {"value": 16}}, None, "Student is below the proposed minimum age of 16.", None)
    rulesets.update_rule(ia_as, d["id"], "AGE-002", {"left": {"field": "age_years"}, "op": "<=", "right": {"value": 30}}, "Error", "Student age is above the proposed maximum of 30.", None)
    stats["draft_rule_set"] = d["version_no"]
    # ---- credentials for a quarter of the VERIFIED + ADMITTED current-cycle enrolments (plus the story student)
    with owner_tx() as conn:
        ids = [str(r["id"]) for r in rows(conn, "SELECT e.id FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.academic_year=:y AND e.status='ADMITTED' AND v.overall_state='VERIFIED' "
                                                  "AND abs(hashtextextended(e.id::text, 7)) % 100 < 23", y=year)]
        aarav = story["ids"].get("st.aarav", (None, None))[1]
        priya = story["ids"].get("st.priya", (None, None))[1]
        for extra in (aarav, priya):
            if extra and extra not in ids:
                ids.append(str(extra))
        n = 0
        for eid in ids:
            try:
                credentials.issue(conn, eid, None, None, None)
                n += 1
            except Exception:  # noqa: BLE001 - not eligible (e.g. story enrolment not verified) is fine
                pass
        stats["credentials"] = n
        creds = rows(conn, "SELECT c.id, c.student_id, c.credential_ref, c.status_list_index, c.enrolment_id FROM cred.credentials c")
    # ---- a few revocations/supersessions so the status list is exercised
    with owner_tx() as conn:
        for c in creds[3:9:2]:
            credentials.revoke(conn, str(c["id"]), "Seeded example: enrolment ended", status="REVOKED")
        for c in creds[4:10:3]:
            try:
                credentials.revoke(conn, str(c["id"]), "Seeded example: record corrected", status="SUPERSEDED")
            except Exception:  # noqa: BLE001
                pass
    # ---- consents, share links, verification history (volume), then real story verifications
    with owner_tx() as conn:
        orgs = rows(conn, "SELECT o.id, o.name, s.purpose_code, s.allowed_fields, s.allowed_questions FROM iam.verifier_orgs o JOIN iam.verifier_scopes s ON s.verifier_org_id=o.id")
        live = rows(conn, "SELECT c.id, c.student_id, c.credential_ref FROM cred.credentials c WHERE c.status='VALID'")
        consents, links, reqs = [], [], []
        for i, c in enumerate(live):
            if rng.random() < 0.55:
                o = rng.choice(orgs)
                consents.append({"id": did("consent", c["id"]), "s": c["student_id"], "p": o["purpose_code"], "o": o["id"], "sc": dumps({"fields": o["allowed_fields"][: rng.randint(1, len(o["allowed_fields"]))]}),
                                 "t": now() - timedelta(days=rng.randint(1, 80)), "e": now() + timedelta(days=rng.randint(30, 300))})
            if i % 9 == 0:
                links.append({"id": did("link", c["id"]), "c": c["id"], "s": c["student_id"], "h": crypto.sha256(str(c["id"]).encode()).hex(), "p": "EMPLOYMENT_VERIFICATION", "f": ["institution_name", "programme_name"],
                              "e": now() + timedelta(days=rng.randint(2, 28)), "u": rng.randint(0, 4)})
        bulk(conn, "INSERT INTO consent.consents (id, student_id, kind, purpose_code, verifier_org_id, scope, status, method, granted_at, expires_at) VALUES (:id,:s,'SHARING',:p,:o,CAST(:sc AS jsonb),'GRANTED','PORTAL',:t,:e)", consents)
        bulk(conn, "INSERT INTO cred.share_links (id, credential_id, student_id, token_hash, purpose_code, fields, expires_at, use_count) VALUES (:id,:c,:s,:h,:p,:f,:e,:u)", links)
        vr = []
        for k in range(1800 if profile["institutions"] > 10 else 60):
            o = rng.choice(orgs)
            c = rng.choice(live) if live and rng.random() < 0.85 else None
            outcome = rng.choices(["YES", "NO", "NEUTRAL"], [70, 10, 20])[0] if c else "NEUTRAL"
            vr.append({"id": did("vreq", k), "o": o["id"], "c": c["id"] if c else None, "s": c["student_id"] if c else None, "p": o["purpose_code"], "q": dumps({"type": rng.choice(o["allowed_questions"])}),
                       "out": outcome, "f": o["allowed_fields"][:2] if outcome != "NEUTRAL" else [], "t": now() - timedelta(days=rng.random() * 90, hours=rng.randint(0, 23)), "un": c is None})
        bulk(conn, "INSERT INTO cred.verification_requests (id, verifier_org_id, credential_id, student_id, via, purpose_code, question, outcome, fields_disclosed, created_at, unknown_ref) VALUES "
                   "(:id,:o,:c,:s,'CREDENTIAL_REF',:p,CAST(:q AS jsonb),:out,:f,:t,:un)", vr)
        stats["verification_requests"], stats["consents"], stats["share_links"] = len(vr), len(consents), len(links)
        # story: st.aarav consents to the employer (full demo), nothing for the bank, so the bank's request creates a consent request in the wallet
        a = row(conn, "SELECT c.student_id, c.credential_ref FROM cred.credentials c WHERE c.enrolment_id=CAST(:e AS uuid) AND c.status='VALID'", e=aarav) if aarav else None
        if a:
            emp = next(o for o in orgs if o["purpose_code"] == "EMPLOYMENT_VERIFICATION")
            conn.execute(text("DELETE FROM consent.consents WHERE student_id=:s"), {"s": a["student_id"]})
            conn.execute(text("INSERT INTO consent.consents (id, student_id, kind, purpose_code, verifier_org_id, scope, status, granted_at, expires_at) VALUES (:id,:s,'SHARING',:p,:o,CAST(:sc AS jsonb),'GRANTED',:t,:e)"),
                         {"id": did("consent", "aarav-employer"), "s": a["student_id"], "p": emp["purpose_code"], "o": emp["id"], "sc": dumps({"fields": ["institution_name", "programme_name", "academic_year"]}), "t": now() - timedelta(days=2), "e": now() + timedelta(days=180)})
            stats["aarav_credential_ref"] = a["credential_ref"]
    if stats.get("aarav_credential_ref"):
        bank = {"id": str(did("vorg", "vu.bank")), "user_id": str(did("user", "vu.bank")), "name": "Sagarmatha Co-operative Bank"}
        verifier.verify(bank, ref=stats["aarav_credential_ref"], purpose="LOAN_ELIGIBILITY", question={"type": "IS_ENROLLED"}, nonce=None, ip="seed")     # NEUTRAL, and a consent request appears for the student
    # ---- wallet stories through the real services
    with owner_tx() as conn:
        sid = {u: scalar(conn, "SELECT student_id FROM iam.users WHERE username=:u", u=u) for u in ("st.aarav", "st.diya", "st.vikram", "st.fatima")}
    def stu(u):
        p = principal_for(ctx, u, "ST", student_id=str(sid[u]))
        return _perms(p)
    try:
        with owner_tx() as conn:
            eid = scalar(conn, "SELECT id FROM core.enrolments WHERE student_id=:s ORDER BY claimed_at DESC LIMIT 1", s=sid["st.diya"])
        wallet.request_correction(stu("st.diya"), str(sid["st.diya"]), str(eid), "parent_name", "Seema Nair", "Spelling of my mother's name")
        wallet.create_dsr(stu("st.aarav"), str(sid["st.aarav"]), "ACCESS", "I would like a copy of my record")
        wallet.create_dsr(stu("st.vikram"), str(sid["st.vikram"]), "ERASURE", "I withdrew and want my data erased")
        wallet.create_dsr(stu("st.fatima"), str(sid["st.fatima"]), "GRIEVANCE", "I was never asked to confirm an admission at GCE")
    except Exception as exc:  # noqa: BLE001
        stats["wallet_story_error"] = str(exc)
    # ---- webhook endpoint for KIT pointing at the local demo sink, and one dead-lettered delivery to demonstrate replay
    kit = _perms(principal_for(ctx, "ia.kit", "IA", "kit"))
    wh = admin.create_webhook(kit, "http://127.0.0.1:8000/api/v1/demo/webhook-sink/00000000-0000-0000-0000-000000000000", admin.EVENTS)
    with owner_tx() as conn:
        conn.execute(text("UPDATE iam.webhook_endpoints SET url=:u WHERE id=CAST(:i AS uuid)"), {"u": f"http://127.0.0.1:8000/api/v1/demo/webhook-sink/{wh['id']}", "i": wh["id"]})
        conn.execute(text("INSERT INTO ops.webhook_deliveries (id, endpoint_id, institution_id, event_type, payload, status, attempts, last_error, first_attempt_at, created_at) VALUES "
                          "(:id,CAST(:e AS uuid),:i,'claim.created',CAST(:p AS jsonb),'DEAD_LETTERED',9,'ConnectError',:t,:t)"),
                     {"id": did("whdead", 1), "e": wh["id"], "i": ctx["inst"]["kit"]["id"], "p": dumps({"event": "claim.created", "note": "seeded example that failed for 24 hours"}), "t": now() - timedelta(days=2)})
    # ---- Layer D [SIMULATED] liveness feed: most long-admitted students show activity; two institutions have a conspicuous gap
    with owner_tx() as conn:
        cut = (now() - timedelta(days=360)).date()
        pool = rows(conn, "SELECT e.identity_key, e.institution_id FROM core.enrolments e WHERE e.status='ADMITTED' AND e.admission_date <= :c AND e.concurrency_class='EXCLUSIVE'", c=cut)
        insts = sorted({str(r["institution_id"]) for r in pool})
        gap = set(insts[len(insts) // 3: len(insts) // 3 + 2]) if len(insts) > 3 else set()
        feed = []
        for r in pool:
            if rng.random() < 0.12:
                continue                                    # no data from the feed for this student: never counted
            ghost = str(r["institution_id"]) in gap and rng.random() < 0.45
            feed.append({"k": r["identity_key"], "abc": not ghost and rng.random() < 0.9, "ex": not ghost and rng.random() < 0.95, "t": now() - timedelta(days=rng.randint(1, 120))})
        bulk(conn, "INSERT INTO intel.liveness_feed (identity_key, has_abc_credit, has_exam_registration, last_activity_at) VALUES (:k,:abc,:ex,:t) ON CONFLICT DO NOTHING", feed)
        stats["liveness_feed"] = len(feed)
    # ---- onboarding fixture: Sahyadri Institute of Technology, Belagavi (public application awaiting approval)
    out = admin.submit_onboarding("1-199999", "Dr. Kavita Patil", "Principal", "principal@sahyadri-belagavi.example.test", "0000100200")
    stats["onboarding_status_token"] = out["status_token"]
    return stats
