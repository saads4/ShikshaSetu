"""Deterministic seed generator (REQ-SEED, PRD 11).

Volume data is written with validated bulk loaders; the named demo "story" records go through the real domain
service (claims.process_claim) with the clock back-dated. Everything is synthetic; mobiles use an invalid "00" prefix
so they can never reach a real number. Dashboards are NOT seeded: aggregates are recomputed from base tables.
"""
from __future__ import annotations

import hashlib
import random
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from . import audit, claims, core, crypto, identity, keys, notify, ops, projector, rules_engine, verification
from .config import ROOT, get_settings
from .db import SYSTEM, DbContext, engine, owner_tx, row, rows, scalar
from .deps import Principal, hash_password
from .seed_data import *  # noqa: F403

NS = uuid.UUID("5a5a5a5a-1234-4abc-9def-0123456789ab")
PROFILES = {  # institutions, avg current enrolments per programme, extra featured-only scale
    "minimal": {"institutions": 6, "per_programme": 14, "prior_ratio": 0.6},
    "standard": {"institutions": 60, "per_programme": 40, "prior_ratio": 0.65},
}
BUCKETS = [("REGULAR", "Regular seats", False, "Approved intake under the regular quota"),
           ("LATERAL", "Lateral entry", False, "Direct second-year entry"),
           ("SN_FN", "Supernumerary - foreign nationals", True, "Illustrative cap"),
           ("SN_OCI", "Supernumerary - OCI / PIO", True, "Illustrative cap"),
           ("SN_TFW", "Supernumerary - tuition fee waiver", True, "Illustrative cap"),
           ("SN_EWS", "Supernumerary - EWS", True, "Illustrative cap")]
DATA_TABLES = ("audit.commitment_leaves, audit.cohort_commitments, ingest.staged_rows, ingest.upload_chunks, ingest.upload_jobs, ingest.submission_history, ingest.submissions, matching.match_candidates, matching.match_features, core.transfers, ref.window_extensions, audit.audit_checkpoints, audit.audit_log, intel.case_events, intel.cases, consent.guardian_links, ops.notifications, ops.jobs, "
               "ops.schedules, ops.outbox_events, ops.system_config, agg.seat_utilisation, agg.verification_funnel, agg.findings_summary, agg.claim_daily, "
               "agg.projector_state, rules.findings, identity.diagnoses, identity.registry_checks, core.confirmation_requests, core.enrolment_verification, "
               "core.claim_attempts, core.seat_ledger_entries, core.enrolment_events, core.enrolments, core.seat_exceptions, core.seat_buckets, "
               "core.provisional_ids, core.student_identity_keys, pii.pii_records, pii.data_keys, "
               "cred.verification_requests, cred.share_links, cred.credentials, cred.status_lists, consent.consents, consent.consent_requests, consent.data_subject_requests, "
               "consent.consent_notices, ingest.correction_requests, rules.simulation_runs, intel.liveness_feed, ops.webhook_deliveries, ops.webhook_sink, ops.retention_policies, "
               "iam.webhook_endpoints, iam.onboarding_requests, iam.verifier_scopes, iam.verifier_orgs, iam.users CASCADE")


def did(kind: str, *parts) -> uuid.UUID:
    return uuid.uuid5(NS, ":".join([kind, *map(str, parts)]))


def luhn_digit(body: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return (10 - total % 10) % 10


def make_apaar(rng: random.Random) -> str:
    while True:
        body = "99" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        v = body + str(luhn_digit(body))
        if identity.mock_apaar_valid(v):
            return v


def make_mobile(rng: random.Random) -> str:
    return "00" + "".join(str(rng.randint(0, 9)) for _ in range(8))  # deliberately not a valid Indian mobile


def bulk(conn, sql: str, data: list[dict], chunk: int = 2000) -> None:
    for i in range(0, len(data), chunk):
        conn.execute(text(sql), data[i:i + chunk])


# =========================================================================== reset
def reset_data() -> None:
    with owner_tx() as conn:
        conn.execute(text(f"TRUNCATE {DATA_TABLES}"))
        conn.execute(text("TRUNCATE ref.enrolment_exception_rules, ref.submission_windows, ref.intake_bucket_caps, ref.intake_versions, ref.programmes, "
                          "ref.institutions, ref.seat_bucket_types, rules.rules, rules.rule_sets, iam.api_clients, iam.user_roles, iam.role_permissions, "
                          "iam.roles, iam.sessions, iam.idempotency_keys, core.students, cred.signing_keys, mockext.registry_people, mockext.sms_messages, "
                          "seed.ground_truth RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE mockext.registry_control SET mode='UP', latency_ms=40"))
    keys.clear_cache()
    rules_engine.invalidate_cache()
    identity.reset_breaker()


# =========================================================================== reference + users
RULES = [  # code, category, kind, params, severity, message, fix, sites
    ("FLD-001", "data_integrity", "REQUIRED_FIELD", {"field": "institution_student_id"}, "Blocker", "Institution student ID is required.", "Enter your internal student or roll number.", "{C,B}"),
    ("FLD-002", "data_integrity", "REQUIRED_FIELD", {"field": "name"}, "Blocker", "Student name is required.", "Enter the student's name as on the certificate.", "{C,B}"),
    ("FLD-003", "data_integrity", "REQUIRED_FIELD", {"field": "dob"}, "Blocker", "Date of birth is required.", "Enter the date of birth.", "{C,B}"),
    ("FLD-004", "data_integrity", "COMPARE", {"left": {"field": "admission_date"}, "op": "<=", "right": {"today": True}}, "Error", "Admission date cannot be in the future.", "Use today's date or earlier.", "{C,B,S}"),
    ("FLD-005", "cross_field", "COMPARE", {"left": {"field": "admission_date"}, "op": ">=", "right": {"field": "window_opens"}}, "Warning", "Admission date is before the admission window opened.", "Check the admission date.", "{C,B}"),
    ("GEN-001", "category", "LOOKUP_IN_REFERENCE", {"field": "gender", "lookup": "gender"}, "Blocker", "Gender must be M, F or O.", "Use M, F or O.", "{C,B}"),
    ("CAT-001", "category", "LOOKUP_IN_REFERENCE", {"field": "category", "lookup": "category"}, "Blocker", "Category must be GEN, OBC, SC, ST or EWS.", "Use one of the listed categories.", "{C,B}"),
    ("CAT-002", "category", "IMPLIES", {"if": {"field": "bucket_type", "value": "SN_EWS"}, "then": {"field": "category", "value": "EWS"}}, "Error", "An EWS supernumerary seat needs category EWS.", "Set category to EWS or pick the regular bucket.", "{C,B}"),
    ("CAT-003", "category", "IMPLIES", {"if": {"field": "bucket_type", "value": "SN_FN"}, "then": {"field": "citizenship_type", "value": "FOREIGN"}}, "Error", "A foreign-national seat needs citizenship FOREIGN.", "Set citizenship to FOREIGN or pick another bucket.", "{C,B}"),
    ("CAT-004", "category", "IMPLIES", {"if": {"field": "bucket_type", "value": "SN_OCI"}, "then": {"field": "citizenship_type", "op": "in", "right": {"value": ["OCI", "PIO"]}}}, "Error", "An OCI/PIO seat needs citizenship OCI or PIO.", "Set citizenship or pick another bucket.", "{C,B}"),
    ("PGM-001", "intake", "IMPLIES", {"if": {"field": "bucket_type", "value": "LATERAL"}, "then": {"field": "lateral_entry", "value": True}}, "Blocker", "This programme does not offer lateral entry.", "Pick the regular bucket.", "{C,B}"),
    ("AGE-001", "data_integrity", "COMPARE", {"left": {"field": "age_years"}, "op": ">=", "right": {"value": 14}}, "Blocker", "Student is too young for admission.", "Check the date of birth.", "{C,B}"),
    ("AGE-002", "data_integrity", "COMPARE", {"left": {"field": "age_years"}, "op": "<=", "right": {"value": 60}}, "Warning", "Student age is unusually high.", "Check the date of birth.", "{C,B}"),
    ("FLD-006", "data_integrity", "REQUIRED_FIELD", {"field": "parent_name"}, "Warning", "Parent or guardian name is missing.", "Add the parent or guardian name to the record.", "{C,B}"),
    ("FLD-007", "data_integrity", "REQUIRED_FIELD", {"field": "mobile"}, "Info", "No mobile number supplied; confirmation will need the registry-held number or assisted confirmation.", "Add a mobile number if the student has one.", "{C,B}"),
    ("AGG-001", "aggregate", "SUM_RECONCILES", {"check": "declared_equals_rows"}, "Error", "Declared enrolment does not match the student records for a programme.", "Correct the declared total or the student records.", "{S}"),
    ("AGG-002", "aggregate", "SUM_RECONCILES", {"check": "total_within_intake"}, "Blocker", "Occupied seats exceed the approved intake plus approved extras.", "Request a seat exception from AICTE.", "{S}"),
    ("INT-001", "intake", "BUCKET_CAP", {"warn_pct": 90}, "Info", "This claim takes the bucket past 90% of its approved limit.", "Plan an exception request if more seats are needed.", "{C}"),
    ("CON-001", "cross_field", "MUTUALLY_EXCLUSIVE", {"fields": ["_never_a", "_never_b"]}, "Info", "Placeholder exclusive rule (kept for engine coverage).", "-", "{S}"),
]


def seed_reference(rng: random.Random, profile: dict, anchor: date) -> dict:
    s = get_settings()
    ctx = {"inst": {}, "prog": {}, "users": {}, "ia": {}, "io": {}}
    pw = hash_password(s.seed_demo_password)  # same demo password for every account (REQ-AUTH-08)
    with owner_tx() as conn:
        bulk(conn, "INSERT INTO ref.seat_bucket_types (code, label, is_supernumerary, description) VALUES (:c,:l,:s,:d)",
             [{"c": c, "l": l, "s": sn, "d": d} for c, l, sn, d in BUCKETS])
        types = ["ENGINEERING"] * 28 + ["POLYTECHNIC"] * 14 + ["MANAGEMENT"] * 9 + ["PHARMACY"] * 5 + ["UNIVERSITY_DEPT"] * 4
        n = profile["institutions"]
        names = list(WATER_NAMES)
        rng.shuffle(names)
        insts = [(slug, *v) for slug, v in FEATURED.items()]
        pool_types = [t for t in types]
        states = list(GEOGRAPHY)
        i = 0
        while len(insts) < n:
            nm = names[i % len(names)]
            i += 1
            t = pool_types[(len(insts) * 7) % len(pool_types)]
            st = states[(len(insts) * 3) % len(states)]
            district = rng.choice(GEOGRAPHY[st][1])
            suffix = {"ENGINEERING": "Institute of Technology", "POLYTECHNIC": "Polytechnic", "MANAGEMENT": "School of Management",
                      "PHARMACY": "College of Pharmacy", "UNIVERSITY_DEPT": "University Department of Technology"}[t]
            insts.append((f"{nm.lower().replace(' ', '')}{len(insts):02d}", f"{nm} {suffix}", t, st, district, f"1-{100000 + len(insts) + 1}"))
        insts = insts[:n]
        rows_i = []
        for slug, name, t, st, dist, aid in insts:
            iid = did("inst", slug)
            ctx["inst"][slug] = {"id": iid, "name": name, "type": t, "state": st, "region": GEOGRAPHY[st][0], "district": dist}
            rows_i.append({"id": iid, "aid": aid, "n": name, "slug": slug, "t": t, "st": st, "d": dist, "r": GEOGRAPHY[st][0],
                           "off": f"Nodal officer, {name}", "em": f"nodal@{slug}.{EMAIL_DOMAIN}", "pcn": f"Admissions Office, {name}",
                           "pce": f"admissions@{slug}.{EMAIL_DOMAIN}", "pcp": "0000" + str(100000 + len(rows_i))})
        # a pending onboarding fixture institution (PRD S13)
        pend = did("inst", "sahyadri-belagavi")
        rows_i.append({"id": pend, "aid": "1-199999", "n": "Sahyadri Institute of Technology, Belagavi", "slug": "sahyadri-belagavi", "t": "ENGINEERING",
                       "st": "Karnataka", "d": "Belagavi", "r": "SOUTH", "off": "Pending nodal officer", "em": f"nodal@sahyadri-belagavi.{EMAIL_DOMAIN}",
                       "pcn": None, "pce": None, "pcp": None})
        bulk(conn, "INSERT INTO ref.institutions (id, aicte_id, name, slug, type, state, district, region, nodal_officer_name, nodal_officer_email, "
                   "public_contact_name, public_contact_email, public_contact_phone, status) VALUES (:id,:aid,:n,:slug,:t,:st,:d,:r,:off,:em,:pcn,:pce,:pcp,"
                   "CASE WHEN :slug='sahyadri-belagavi' THEN 'PENDING_APPROVAL' ELSE 'ACTIVE' END)", rows_i)
        # programmes + intake versions
        prog_rows, iv_rows = [], []
        catalog = {"ENGINEERING": ENGINEERING_PROGRAMMES, "POLYTECHNIC": POLY_PROGRAMMES, "MANAGEMENT": MGMT_PROGRAMMES,
                   "PHARMACY": PHARMA_PROGRAMMES, "UNIVERSITY_DEPT": UNI_PROGRAMMES}
        for slug, info in ctx["inst"].items():
            cat = catalog[info["type"]]
            k = {"ENGINEERING": 5, "POLYTECHNIC": 3, "MANAGEMENT": 2, "PHARMACY": 3, "UNIVERSITY_DEPT": 3}[info["type"]]
            if slug == "kit":
                chosen = [c for c in cat if c[1] in ("CSE", "ECE", "MECH", "AIDS", "MTCS")]
            elif slug == "bvec":
                chosen = [c for c in cat if c[1] in ("CSE", "MECH", "CIVIL", "IT")]
            else:
                chosen = rng.sample(cat, min(k, len(cat)))
            ctx["inst"][slug]["programmes"] = []
            for name, code, level, dur in chosen:
                mode = "PART_TIME" if code == "PGDMX" else "FULL_TIME"
                if info["type"] == "ENGINEERING" and code == "MTCS" and rng.random() < 0.3:
                    mode = "ONLINE"
                lateral = level in ("UG", "DIPLOMA") and rng.random() < 0.3 or (slug == "kit" and code == "CSE")
                pid = did("prog", slug, code)
                ctx["prog"][(slug, code)] = {"id": pid, "name": name, "level": level, "mode": mode, "lateral": lateral}
                ctx["inst"][slug]["programmes"].append(code)
                prog_rows.append({"id": pid, "i": info["id"], "c": code, "n": name, "l": level, "d": code, "m": mode, "lat": lateral, "dur": dur})
        bulk(conn, "INSERT INTO ref.programmes (id, institution_id, programme_code, name, level, discipline, mode, lateral_entry, duration_years) "
                   "VALUES (:id,:i,:c,:n,:l,:d,:m,:lat,:dur)", prog_rows)
        # windows
        bulk(conn, "INSERT INTO ref.submission_windows (id, academic_year, label, opens_at, closes_at, cutoff_at) VALUES (:id,:y,:l,:o,:c,:cu)", [
            {"id": did("win", "2026-27"), "y": "2026-27", "l": "Main window 2026-27", "o": datetime.combine(anchor - timedelta(days=86), datetime.min.time(), timezone.utc),
             "c": datetime.combine(anchor + timedelta(days=20), datetime.max.time().replace(microsecond=0), timezone.utc),
             "cu": datetime.combine(anchor + timedelta(days=25), datetime.max.time().replace(microsecond=0), timezone.utc)},
            {"id": did("win", "2025-26"), "y": "2025-26", "l": "Main window 2025-26", "o": datetime.combine(anchor - timedelta(days=451), datetime.min.time(), timezone.utc),
             "c": datetime.combine(anchor - timedelta(days=345), datetime.max.time().replace(microsecond=0), timezone.utc),
             "cu": datetime.combine(anchor - timedelta(days=340), datetime.max.time().replace(microsecond=0), timezone.utc)}])
        bulk(conn, "INSERT INTO ref.enrolment_exception_rules (id, name, description, level_a, level_b, mode_a, mode_b) VALUES (:id,:n,:d,:a,:b,:ma,:mb)", [
            {"id": did("exc", 1), "n": "Integrated dual degree (UG + PG)", "d": "Approved dual-programme pair (illustrative)", "a": "UG", "b": "PG", "ma": "FULL_TIME", "mb": "FULL_TIME"}])
        # AICTE users + roles (needed before the rule set)
        users = [("ar.national", "Anita Rao (AICTE reviewer)", "AR", "NATIONAL", None, "Reviewer: national dashboard, seat-exception decisions, audit trail (case ownership, countersigning and rule publishing arrive in a later phase)"),
                 ("ar.national2", "Rakesh Menon (AICTE reviewer)", "AR", "NATIONAL", None, "Second national reviewer (appeal decisions arrive in a later phase)"),
                 ("ar.south", "Lakshmi Iyer (Regional reviewer, South)", "AR", "REGION", "SOUTH", "Regional scope: sees only South-region institutions"),
                 ("as.admin", "Sandeep Verma (AICTE system admin)", "AS", "NATIONAL", None, "System admin: Demo tools, audit trail, operations (rule drafting and simulator arrive in a later phase)")]
        _add_users(conn, ctx, users, pw, "AICTE", None)
        conn.execute(text("INSERT INTO iam.roles (code, label) VALUES ('IA','Institution admin'),('IO','Institution data operator'),('AR','AICTE reviewer'),"
                          "('AS','AICTE system admin'),('ST','Student or guardian'),('VU','Verifier user'),('CL','System client') ON CONFLICT DO NOTHING"))
        from .permissions import PERMISSIONS
        bulk(conn, "INSERT INTO iam.role_permissions (role_code, permission) VALUES (:r,:p) ON CONFLICT DO NOTHING",
             [{"r": r, "p": p} for r, ps in PERMISSIONS.items() for p in ps])
    # roles table must exist before user_roles; re-run role inserts for users created above
    return ctx


def _add_users(conn, ctx, users, pw, utype, inst_id):
    conn.execute(text("INSERT INTO iam.roles (code, label) VALUES ('IA','Institution admin'),('IO','Institution data operator'),('AR','AICTE reviewer'),"
                      "('AS','AICTE system admin'),('ST','Student or guardian'),('VU','Verifier user'),('CL','System client') ON CONFLICT DO NOTHING"))
    for uname, display, role, scope_t, scope_v, desc in users:
        uid = did("user", uname)
        ctx["users"][uname] = uid
        conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, description, utype, password_hash, is_demo, institution_id) "
                          "VALUES (:id,:u,:d,:e,:ds,:t,:pw,true,CAST(:i AS uuid))"),
                     {"id": uid, "u": uname, "d": display, "e": f"{uname}@{EMAIL_DOMAIN}", "ds": desc, "t": utype, "pw": pw, "i": str(inst_id) if inst_id else None})
        if scope_t == "INSTITUTION":
            scope_v = str(ctx["inst"][scope_v]["id"])
        conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type, scope_value) VALUES (:id,:u,:r,:st,:sv)"),
                     {"id": did("ur", uname), "u": uid, "r": role, "st": scope_t, "sv": scope_v})


def seed_rules_and_accounts(ctx: dict, anchor: date) -> None:
    s = get_settings()
    pw = hash_password(s.seed_demo_password)
    with owner_tx() as conn:
        rs = did("ruleset", 1)
        conn.execute(text("INSERT INTO rules.rule_sets (id, name, version_no, status, drafted_by, published_by, published_at, notes) VALUES "
                          "(:id,'Admission rules v1',1,'ACTIVE',:d,:p,:t,'Illustrative synthetic rule set (not Approval Process Handbook values)')"),
                     {"id": rs, "d": ctx["users"]["as.admin"], "p": ctx["users"]["ar.national"], "t": datetime.now(timezone.utc) - timedelta(days=100)})
        import json
        bulk(conn, "INSERT INTO rules.rules (id, rule_set_id, rule_code, category, kind, params, severity, sites, message_template, fix_template) "
                   "VALUES (:id,:rs,:c,:cat,:k,CAST(:p AS jsonb),:s,CAST(:sites AS text[]),:m,:f)",
             [{"id": did("rule", c), "rs": rs, "c": c, "cat": cat, "k": k, "p": json.dumps(p), "s": sev, "sites": sites, "m": m, "f": f}
              for c, cat, k, p, sev, m, f, sites in RULES])
        ctx["rule_set"] = rs
        # institution admin/operator accounts
        feat_desc = {"kit": ("Main institution admin: claims, seat ledger, identity fixes, seat exceptions", "Operator: dry-runs and views, cannot commit"),
                     "bvec": ("Cross-institution conflict demo (bulk upload arrives in a later phase)", "Operator at BVEC"),
                     "mpb": ("Minors, provisional-ID (NOGEN) students and the guardian flow", "Operator at MPB"),
                     "gce": ("Ghost-ring institution: low confirmation rate, borrowed identities (case response arrives in a later phase)", None),
                     "asm": ("PGDM institution (late submission and window extension arrive in a later phase)", "Operator at ASM")}
        for slug, info in ctx["inst"].items():
            ia_desc, io_desc = feat_desc.get(slug, (None, None))
            users = [(f"ia.{slug}", f"Admin, {info['name']}", "IA", "INSTITUTION", slug, ia_desc)]
            if slug not in ("gce",):
                users.append((f"io.{slug}", f"Operator, {info['name']}", "IO", "INSTITUTION", slug, io_desc))
            if slug == "kit":
                users.insert(1, ("ia2.kit", "Second admin, Kaveri Institute of Technology", "IA", "INSTITUTION", "kit", "Second admin at KIT (used for upload commits in a later phase)"))
            _add_users(conn, ctx, users, pw, "INSTITUTION", info["id"])
        conn.execute(text("INSERT INTO iam.api_clients (id, institution_id, name, client_id, secret_hash, scopes, is_demo) VALUES "
                          "(:a,:i,'KIT ERP client','erp-kit',:pw,ARRAY['claims:write','claims:validate','upload:write'],true),"
                          "(:b,NULL,'Mock liveness feed','feeds-mock',:pw,ARRAY['feeds:write'],true)"),
                     {"a": did("client", "erp-kit"), "b": did("client", "feeds-mock"), "i": ctx["inst"]["kit"]["id"], "pw": pw})
