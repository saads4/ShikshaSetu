"""Seed orchestration: reset -> reference -> accounts -> volume -> story fixtures -> aggregates -> audit seal."""
from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from . import audit, cases, claims, core, crypto, identity, intel, matching, notify, ops, projector, seed, seed_volume, submissions, verification
from .config import ROOT, get_settings
from .db import SYSTEM, owner_tx, row, rows, scalar
from .deps import Principal, hash_password
from .seed_data import EMAIL_DOMAIN, FICTIONAL_DEMO_STUDENTS

CONFIG_DEFAULTS = {"seat_warn_pct": 90, "seat_stop_pct": 100, "confirm_deadline_days": 30, "fix_deadline_days": 14, "orphan_lead_days": 30,
                   "small_cell_min": 5, "otp_ttl_minutes": 10, "stale_claim_days": 45, "late_submission_flag": True}


def _direct_registry(apaar: str) -> identity.RegistryResult:
    """In-process stand-in for the registry call while seeding (reads the same mockext table the mock service serves)."""
    with owner_tx() as conn:
        p = row(conn, "SELECT name, dob, mobile, status FROM mockext.registry_people WHERE apaar_id=:a", a=apaar)
    if not p:
        return identity.RegistryResult("NOT_FOUND")
    return identity.RegistryResult("FOUND", p["name"], p["dob"], p["mobile"], p["status"], 40, "CLOSED")


def _direct_sms(mobile: str, template_code: str, language: str, otp: str | None = None, **params):
    import re
    body = notify.render(template_code, language, otp=otp or "", **params)
    digits = re.sub(r"\D", "", mobile)
    with owner_tx() as conn:
        mid = conn.execute(text("INSERT INTO mockext.sms_messages (recipient_masked, recipient_hash, template_code, language, body, otp) "
                                "VALUES (:m,'seed',:t,:l,:b,:o) RETURNING id"),
                           {"m": f"+91 ••••• ••{digits[-3:]}", "t": template_code, "l": language, "b": body, "o": otp}).scalar()
    return True, str(mid)


def principal_for(ctx: dict, username: str, role: str, inst_slug: str | None = None, student_id: str | None = None) -> Principal:
    inst = str(ctx["inst"][inst_slug]["id"]) if inst_slug else None
    utype = "INSTITUTION" if inst else ("STUDENT" if role == "ST" else "AICTE")
    return Principal("user", str(ctx["users"][username]), username, username, utype, [role], "INSTITUTION" if inst else "NATIONAL", inst, inst,
                     student_id, None, "en", set(), "seed-" + username, "seed")


def _perms(p: Principal) -> Principal:
    from .permissions import PERMISSIONS
    for r in p.roles:
        p.perms |= PERMISSIONS[r]
    return p


def _claim(ctx, p: Principal, days_ago: int, **kw):
    core.set_clock_offset(-days_ago * 86400)
    try:
        body = claims.ClaimIn(**kw)
        return claims.process_claim(p, body, dry_run=False, source="PORTAL")
    finally:
        core.set_clock_offset(0)


def _latest_otp() -> str | None:
    with owner_tx() as conn:
        return scalar(conn, "SELECT otp FROM mockext.sms_messages ORDER BY id DESC LIMIT 1")


def story_fixtures(ctx: dict, counts: dict, rng: random.Random, anchor: date, spare_people: list) -> dict:
    """PRD 11.3 / 11.7: demo accounts' records, created through the real domain rules (clock back-dated)."""
    orig_reg, orig_sms = identity.registry_lookup, notify.send_sms
    identity.registry_lookup, notify.send_sms = _direct_registry, _direct_sms
    out = {"ids": {}}
    try:
        ia_kit, ia_bvec, ia_mpb, ia_gce = (_perms(principal_for(ctx, f"ia.{s}", "IA", s)) for s in ("kit", "bvec", "mpb", "gce"))
        kit_codes = ctx["inst"]["kit"]["programmes"]
        sp = spare_people  # (apaar, name, dob, gender) tuples
        base = dict(institution_student_id="", programme_code="CSE", bucket_type="REGULAR")

        def person(i):
            return {"apaar_id": sp[i][0], "name": sp[i][1], "dob": date.fromisoformat(sp[i][2]), "gender": sp[i][3]}

        def claim(p, idx, days, sid, prog, **extra):
            kw = {**person(idx), "institution_student_id": sid, "programme_code": prog, "parent_name": f"Parent of {sp[idx][1].split()[0]}",
                  "mobile": None, "category": "GEN", **extra}
            return _claim(ctx, p, days, **kw)

        # st.aarav - verified, admitted
        st, res = claim(ia_kit, 0, 40, "KIT26S9001", "CSE", admit_now=True)
        out["ids"]["st.aarav"] = (sp[0][0], res.get("enrolment_id"))
        _confirm_story(ctx, "st.aarav", sp[0][0], res.get("enrolment_id"))
        # st.diya - pending OTP, claimed now
        st, res = claim(ia_kit, 1, 0, "KIT26S9002", "CSE")
        out["ids"]["st.diya"] = (sp[1][0], res.get("enrolment_id"))
        # st.priya - verified at KIT; BVEC will hit CONFLICT
        st, res = claim(ia_kit, 2, 10, "KIT26S9003", "CSE", admit_now=True)
        out["ids"]["st.priya"] = (sp[2][0], res.get("enrolment_id"))
        _confirm_story(ctx, "st.priya", sp[2][0], res.get("enrolment_id"))
        # st.vikram - withdrawn
        st, res = claim(ia_kit, 3, 20, "KIT26S9004", "ECE" if "ECE" in kit_codes else kit_codes[0])
        out["ids"]["st.vikram"] = (sp[3][0], res.get("enrolment_id"))
        if res.get("enrolment_id"):
            core.set_clock_offset(-12 * 86400)
            claims.withdraw(ia_kit, res["enrolment_id"], "STUDENT_WITHDREW", None, "Student moved to another course")
            core.set_clock_offset(0)
        # BVEC double-claim of Priya's APAAR -> CONFLICT (recorded attempt)
        st, res_conf = claim(ia_bvec, 2, 8, "BVEC26X0007", "CSE")
        out["conflict_status"] = st
        # MPB: minor with guardian, and a NOGEN provisional student
        mpb_codes = ctx["inst"]["mpb"]["programmes"]
        minor = seed_volume.new_person(rng, "EAST", "Bhubaneswar", "DIPLOMA", anchor.year)
        minor.dob = date(anchor.year - 16, 5, 14)
        with owner_tx() as conn:
            conn.execute(text("INSERT INTO mockext.registry_people (apaar_id, name, dob, mobile, is_spare) VALUES (:a,:n,:d,:m,false) ON CONFLICT DO NOTHING"),
                         {"a": minor.apaar, "n": "Rohan Mohanty", "d": minor.dob, "m": minor.mobile})
        st, res = _claim_dict(ctx, ia_mpb, 5, {"apaar_id": minor.apaar, "name": "Rohan Mohanty", "dob": minor.dob, "gender": "M", "institution_student_id": "MPB26S9101",
                                              "programme_code": mpb_codes[0], "parent_name": "Meera Mohanty"})
        out["ids"]["st.rohan"] = (minor.apaar, res.get("enrolment_id"))
        st, res = _claim_dict(ctx, ia_mpb, 6, {"provisional": {"type": "NOGEN"}, "name": "Ishaan Behera", "dob": date(anchor.year - 18, 2, 3), "gender": "M",
                                              "institution_student_id": "MPB26S9102", "programme_code": mpb_codes[0], "mobile": "0011223344",
                                              "parent_name": "Suresh Behera"})
        out["ids"]["st.ishaan"] = (res.get("provisional_token"), res.get("enrolment_id"))
        # GCE ghost claim on a real student's APAAR (st.fatima disputes it)
        gce_code = next(c for c in ctx["inst"]["gce"]["programmes"] if ctx["prog"][("gce", c)]["mode"] == "FULL_TIME" and ctx["prog"][("gce", c)]["level"] == "UG")
        st, res = _claim_dict(ctx, ia_gce, 14, {**person(4), "institution_student_id": "GCE26G0042", "programme_code": gce_code, "parent_name": "Unknown",
                                               "mobile": "0099887766", "category": "GEN"})
        out["ids"]["st.fatima"] = (sp[4][0], res.get("enrolment_id"), st)
        st, res_f = _claim_dict(ctx, ia_bvec, 12, {**person(4), "institution_student_id": "BVEC26F0011", "programme_code": "CSE", "parent_name": "Fatima's father"})
        out["fatima_bvec_status"] = st
        # a pending seat-exception request from KIT
        try:
            claims.request_exception(ia_kit, "CSE", "REGULAR", 6, "Strong demand for CSE after a counselling round; the bucket is at its approved limit.",
                                     "Counselling list attached (synthetic)", None)
        except Exception as exc:  # noqa: BLE001
            out["exception_request_error"] = str(exc)
    finally:
        identity.registry_lookup, notify.send_sms = orig_reg, orig_sms
        core.set_clock_offset(0)
    return out


def _claim_dict(ctx, p, days, kw):
    core.set_clock_offset(-days * 86400)
    try:
        return claims.process_claim(p, claims.ClaimIn(**kw), dry_run=False, source="PORTAL")
    finally:
        core.set_clock_offset(0)


def _confirm_story(ctx, username, apaar, enrolment_id):
    otp = _latest_otp()
    if not otp or not enrolment_id:
        return
    with owner_tx() as conn:
        sid = scalar(conn, "SELECT student_id FROM core.enrolments WHERE id=CAST(:e AS uuid)", e=enrolment_id)
        # student user may not exist yet: confirm using a transient principal (the student acts for themselves)
    p = Principal("user", str(ctx["users"].get(username, "00000000-0000-0000-0000-000000000000")), username, username, "STUDENT", ["ST"], "", None, None,
                  str(sid), None, "en", set(), "seed-confirm", "seed")
    # the OTP was minted "back then"; push expiry forward so the seeded confirmation succeeds
    with owner_tx() as conn:
        conn.execute(text("UPDATE core.confirmation_requests SET otp_expires_at=now()+interval '5 minutes' WHERE enrolment_id=CAST(:e AS uuid)"), {"e": enrolment_id})
    core.set_clock_offset(0)
    verification.confirm(p, None, enrolment_id, otp)


def create_student_accounts(ctx: dict, story: dict) -> None:
    pw = hash_password(get_settings().seed_demo_password)
    with owner_tx() as conn:
        for uname, (first, sur, g) in FICTIONAL_DEMO_STUDENTS.items():
            key = story["ids"].get(uname)
            if not key:
                continue
            ident = key[0]
            ik = crypto.identity_key(ident if uname != "st.ishaan" else "PROV:" + ident)
            sid = scalar(conn, "SELECT student_id FROM core.student_identity_keys WHERE identity_key=:k", k=ik)
            if not sid:
                continue
            uid = seed.did("user", uname)
            ctx["users"][uname] = uid
            desc = {"st.aarav": "VERIFIED enrolment (credential and verifier demo arrive in a later phase)", "st.diya": "Lock 3 PENDING - OTP confirmation",
                    "st.priya": "Her APAAR already held at KIT: a second claim at BVEC is a CONFLICT", "st.rohan": "Minor (16) at Mahanadi Polytechnic - guardian confirmation",
                    "st.ishaan": "NOGEN provisional ID (counted and checked; upgrade path arrives in a later phase)", "st.fatima": "Real student whose APAAR was claimed at GCE: dispute the enrolment",
                    "st.vikram": "WITHDRAWN enrolment only (erasure request arrives in a later phase)"}[uname]
            conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, description, utype, password_hash, is_demo, student_id) VALUES "
                              "(:id,:u,:d,:e,:ds,'STUDENT',:pw,true,:s)"),
                         {"id": uid, "u": uname, "d": f"{first} {sur}", "e": f"{uname}@{EMAIL_DOMAIN}", "ds": desc, "pw": pw, "s": sid})
            conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type) VALUES (:id,:u,'ST','INSTITUTION')"),
                         {"id": seed.did("ur", uname), "u": uid})
        gid = seed.did("user", "gd.meera")
        ctx["users"]["gd.meera"] = gid
        conn.execute(text("INSERT INTO iam.users (id, username, display_name, email, description, utype, password_hash, is_demo) VALUES "
                          "(:id,'gd.meera','Meera Mohanty (guardian)',:e,'Guardian of st.rohan: confirms for the minor in guardian mode','STUDENT',:pw,true)"),
                     {"id": gid, "e": f"gd.meera@{EMAIL_DOMAIN}", "pw": pw})
        conn.execute(text("INSERT INTO iam.user_roles (id, user_id, role_code, scope_type) VALUES (:id,:u,'ST','INSTITUTION')"), {"id": seed.did("ur", "gd.meera"), "u": gid})
        rohan = story["ids"].get("st.rohan")
        if rohan:
            sid = scalar(conn, "SELECT student_id FROM core.student_identity_keys WHERE identity_key=:k", k=crypto.identity_key(rohan[0]))
            if sid:
                conn.execute(text("INSERT INTO consent.guardian_links (id, student_id, guardian_user_id, relationship, status, verified_at) VALUES "
                                  "(:id,:s,:g,'MOTHER','VERIFIED',now())"), {"id": seed.did("guardian", "rohan"), "s": sid, "g": gid})


def seed_submissions(ctx: dict, year_prev: str) -> int:
    """Prior-cycle submissions, signed and countersigned through the real service so commitments and inclusion proofs exist."""
    import json
    ar = _perms(principal_for(ctx, "ar.national", "AR"))
    done = 0
    slugs = [s for s in ctx["inst"] if s not in ("kit", "gce", "asm")]
    for slug in slugs[: max(2, len(slugs) // 2)]:
        ia = _perms(principal_for(ctx, f"ia.{slug}", "IA", slug))
        with owner_tx() as conn:
            inst = ctx["inst"][slug]["id"]
            w = scalar(conn, "SELECT id FROM ref.submission_windows WHERE academic_year=:y", y=year_prev)
            declared = {r["programme_code"]: int(r["n"]) for r in rows(conn, "SELECT pr.programme_code, coalesce(sum(b.occupied),0) AS n FROM ref.programmes pr LEFT JOIN core.seat_buckets b ON "
                                                                             "b.programme_id=pr.id AND b.academic_year=:y WHERE pr.institution_id=:i GROUP BY 1", y=year_prev, i=inst)}
            sid = seed.did("submission", slug, year_prev)
            conn.execute(text("INSERT INTO ingest.submissions (id, institution_id, academic_year, window_id, status, declared, submitted_by, submitted_at, approved_by, approved_at, created_by) "
                              "VALUES (:id,:i,:y,:w,'ACCEPTED',CAST(:d AS jsonb),:u,now() - interval '340 days',:a,now() - interval '338 days',:u)"),
                         {"id": sid, "i": inst, "y": year_prev, "w": w, "d": json.dumps(declared), "u": ctx["users"][f"ia.{slug}"], "a": ctx["users"]["ar.national"]})
        submissions.sign_commitment(ia, str(sid))
        submissions.countersign(ar, str(sid))
        done += 1
    return done


def seed_cases(ctx: dict) -> dict:
    """Cases from the highest-priority alerts, advanced through the real case service so audit and labels are consistent."""
    ar, ar2 = _perms(principal_for(ctx, "ar.national", "AR")), _perms(principal_for(ctx, "ar.national2", "AR"))
    stats = {"opened": 0, "awaiting": 0, "responded": 0, "closed": 0, "appealed": 0}
    with owner_tx() as conn:
        alerts = rows(conn, "SELECT a.id, a.severity, a.layer, i.slug FROM intel.anomaly_alerts a JOIN ref.institutions i ON i.id=a.institution_id WHERE a.status='OPEN' "
                            "ORDER BY CASE a.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, (i.slug='gce') DESC, a.created_at LIMIT 30")
    outcomes = ["CONFIRMED_VIOLATION", "NOT_SUBSTANTIATED", "DATA_CORRECTED", "REFERRED_FOR_INSPECTION", "FALSE_POSITIVE"]
    for n, a in enumerate(alerts):
        cid = cases.case_from_alert(ar, str(a["id"]))["case_id"]
        stats["opened"] += 1
        ia = _perms(principal_for(ctx, f"ia.{a['slug']}", "IA", a["slug"])) if f"ia.{a['slug']}" in ctx["users"] else None
        if n % 3 == 2:
            continue                                                    # left OPEN
        cases.request_response(ar, cid, "Please explain the pattern shown in the evidence pack and attach supporting records.")
        stats["awaiting"] += 1
        if n % 3 == 1 or ia is None:
            continue                                                    # left AWAITING_RESPONSE (gce keeps one for the demo)
        if a["slug"] == "gce" and n == 0:
            continue
        cases.respond(ia, cid, "The records are being reviewed; we will share the counselling list and contact verification within the window.")
        stats["responded"] += 1
        if n % 6 == 0:
            cases.close(ar, cid, outcomes[(n // 6) % 5], "Reviewed the evidence pack and the institution's response.")
            stats["closed"] += 1
            if n % 12 == 0:
                cases.appeal(ia, cid, "We dispute the finding: the shared phone numbers belong to guardians and the unconfirmed students are being followed up.")
                cases.decide_appeal(ar2, cid, "OVERTURNED" if n % 24 == 0 else "UPHELD", "Second reviewer re-examined the evidence pack.")
                stats["appealed"] += 1
    return stats


def write_demo_script(story: dict, counts: dict) -> None:
    import os
    if os.environ.get("SEED_WRITE_DOCS", "1") == "0":      # tests must not overwrite the demo script of the real database
        return
    lines = ["# Demo script (generated by the seed - synthetic data only)", "",
             "All passwords are `1234`. Mock registry IDs below are synthetic and exist only in the mock registry.", "",
             "| Story | Account | Key identifier |", "|---|---|---|"]
    for uname, v in story["ids"].items():
        lines.append(f"| {uname} | `{uname}` | `{v[0]}` |")
    lines += ["", "## Spare registry people (never enrolled; use them to try new claims)", "", "| APAAR (mock) | Name | DOB | Gender |", "|---|---|---|---|"]
    for a, n, d, g in counts["spare"][5:]:   # the first five are used by the story fixtures
        lines.append(f"| `{a}` | {n} | {d} | {g} |")
    lines += ["", "## Walk-through", "",
              "1. Sign in as `ia.kit`, open **Claims**, **New claim**, claim a spare person; watch the live validation panel.",
              "2. Sign in as `ia.bvec` and claim the APAAR in the `st.priya` row: you get a CONFLICT with a resolution reference only.",
              "3. Sign in as `st.diya`, open **My enrolments**, read the OTP in **Dev SMS Inbox** (`/dev/sms-inbox`), confirm Lock 3.",
              "4. Sign in as `st.fatima` and dispute the enrolment at GCE: a case is opened automatically.",
              "5. Sign in as `as.admin` -> **Demo tools**: set the registry to DOWN, then claim as `ia.kit`: the claim is `PENDING_VERIFICATION`; set UP and run the revalidation job.",
              "6. Sign in as `ar.national` -> **Dashboard**: figures are computed from the database; claim something and refresh.",
              "7. Sign in as `st.aarav` -> **Credentials**: copy the credential reference, create a share link (shown once) and open it in a private window (`/s/<token>`); press *Verify signature in this browser*.",
              "8. Sign in as `vu.employer` -> **Verify**: paste the reference; the answer shows only the fields `st.aarav` consented to. Repeat as `vu.bank`: the reply is neutral and a consent request appears under `st.aarav` -> **Consents**.",
              "9. Sign in as `as.admin` -> **Rule sets and simulator**: open draft v2, run the simulation, then sign in as `ar.national` and publish it (the drafter cannot).",
              "10. Sign in as `ar.national` -> **Reviews** -> *Onboarding*: approve Sahyadri Institute of Technology; `ia.sahyadri-belagavi` can then sign in.",
              "11. Sign in as `ia.kit` -> **Admin** -> *Webhooks*: the seeded endpoint points at the local demo sink; claim a seat and watch a signed delivery arrive (the dead-lettered example can be replayed).",
              "12. Sign in as `st.diya` -> **Corrections**: a pending correction exists; `ar.national` decides it under *Reviews* -> *Corrections* (approval supersedes the credential).", ""]
    out = ROOT / "docs" / "demo-script.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def run(profile_name: str | None = None) -> dict:
    s = get_settings()
    profile_name = profile_name or s.seed_profile
    profile = seed.PROFILES[profile_name]
    anchor = date.fromisoformat(s.seed_anchor_date) if s.seed_anchor_date else datetime.now(timezone.utc).date()
    rng = random.Random(s.seed_rng)
    t0 = time.time()
    core.set_clock_offset(0)
    seed.reset_data()
    ctx = seed.seed_reference(rng, profile, anchor)
    seed.seed_rules_and_accounts(ctx, anchor)
    with owner_tx() as conn:
        for k, v in CONFIG_DEFAULTS.items():
            conn.execute(text("INSERT INTO ops.system_config (key, value) VALUES (:k, CAST(:v AS jsonb))"), {"k": k, "v": core.dumps(v)})
        conn.execute(text("INSERT INTO ops.system_config (key, value) VALUES ('demo_clock', '{\"offset_days\": 0}')"))
    counts = seed_volume.generate(ctx, profile, rng, anchor)
    story = story_fixtures(ctx, counts, rng, anchor, counts["spare"])
    create_student_accounts(ctx, story)
    matching_stats = matching.process_all()
    n_sub = seed_submissions(ctx, counts["year_prev"])
    try:
        asm = _perms(principal_for(ctx, "ia.asm", "IA", "asm"))
        submissions.request_extension(asm, datetime.now(timezone.utc) + timedelta(days=35), "Counselling round ran late; requesting a short extension.", None)
    except Exception:  # noqa: BLE001
        pass
    from . import seed_final
    final_stats = seed_final.run(ctx, story, rng, profile, counts["year_cur"])
    projector.recompute_all()
    intel_stats = intel.run_all()
    case_stats = seed_cases(ctx)
    ops.ensure_schedules()
    while audit.seal() > 0:
        pass
    audit.checkpoint_now()
    write_demo_script(story, counts)
    return {"profile": profile_name, "seconds": round(time.time() - t0, 1), "counts": {k: v for k, v in counts.items() if k != "spare"},
            "story": {k: v for k, v in story.items() if k in ("conflict_status", "fatima_bvec_status", "exception_request_error")}, "matching": matching_stats, "locked_prior_submissions": n_sub, "intelligence": {"alerts": intel_stats["anomalies"]["by_layer"], "trust_bands": intel_stats["trust"]["bands"]}, "cases": case_stats,
            "final": {k: v for k, v in final_stats.items() if k not in ("onboarding_status_token",)}}
