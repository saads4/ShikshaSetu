"""Volume data for the seed: people, enrolments, ledgers, events, verification, vault, attempts, audit, notifications.

Enrolments are generated first (PRD 11.1 #5); bucket caps are derived afterwards from a target utilisation band so
every ledger is consistent by construction. The identity diagnosis uses the real identity.diagnose() so seeded
verification states match what the live system would have produced.
"""
from __future__ import annotations

import json
import math
import random
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from . import claims, crypto, identity, keys, rules_engine, verification
from .core import dumps
from .db import owner_tx
from .seed import bulk, did, make_apaar, make_mobile
from .seed_data import EMAIL_DOMAIN, FIRST_F, FIRST_M, SURNAMES

UTIL_BANDS = [(0.20, 0.35, 0.60), (0.45, 0.60, 0.85), (0.25, 0.85, 0.95), (0.10, 0.95, 1.00)]
WITHDRAW_REASONS = ["STUDENT_WITHDREW", "ADMISSION_CANCELLED", "FEE_NOT_PAID", "DATA_ERROR", "OTHER"]


def pick_util(rng: random.Random) -> float:
    r, acc = rng.random(), 0.0
    for w, lo, hi in UTIL_BANDS:
        acc += w
        if r <= acc:
            return rng.uniform(lo, hi)
    return 0.8


class Person:
    __slots__ = ("name", "dob", "mobile", "apaar", "gender", "parent", "region", "district")


def new_person(rng: random.Random, region: str, district: str, level: str, ref_year: int) -> Person:
    p = Person()
    p.gender = rng.choice("MF") if rng.random() < 0.98 else "O"
    first = rng.choice(FIRST_M if p.gender == "M" else FIRST_F if p.gender == "F" else FIRST_M + FIRST_F)
    p.name = f"{first} {rng.choice(SURNAMES[region])}"
    base = {"UG": 18.2, "DIPLOMA": 16.4, "PG": 22.5, "PGDM": 23.5}[level]
    age_days = int((base + rng.uniform(-1.3, 1.6)) * 365.25)
    p.dob = date(ref_year, 9, 1) - timedelta(days=age_days)
    p.mobile = make_mobile(rng)
    p.apaar = make_apaar(rng)
    p.parent = f"{rng.choice(FIRST_M)} {p.name.split()[-1]}"
    p.region, p.district = region, district
    return p


def variant(rng: random.Random, p: Person) -> tuple[str, date, str]:
    """An institution-side record that differs from the registry in a recognisable way (ground truth label returned)."""
    kind = rng.choice(["INITIAL", "TYPO", "DAY_MONTH", "YEAR_TYPO", "VARIANT_SPELLING"])
    first, *rest = p.name.split()
    if kind == "INITIAL":
        return f"{first[0]} {' '.join(rest)}", p.dob, kind
    if kind == "TYPO" and len(first) > 3:
        i = rng.randint(1, len(first) - 2)
        return f"{first[:i]}{first[i + 1]}{first[i]}{first[i + 2:]} {' '.join(rest)}", p.dob, kind
    if kind == "DAY_MONTH" and p.dob.day <= 12 and p.dob.day != p.dob.month:
        return p.name, date(p.dob.year, p.dob.day, p.dob.month), kind
    if kind == "YEAR_TYPO":
        return p.name, date(p.dob.year + 1, p.dob.month, min(p.dob.day, 28)), kind
    return p.name.replace("Mohammed", "Mohd").replace("Kumar", "Kr").replace("Krishnan", "Krishnn"), p.dob, "VARIANT_SPELLING"


def generate(ctx: dict, profile: dict, rng: random.Random, anchor: date) -> dict:
    from .config import get_settings
    s = get_settings()
    year_cur = claims.current_academic_year(anchor)
    y0 = int(year_cur[:4])
    year_prev = f"{y0 - 1}-{str(y0)[2:]}"
    now_dt = datetime.combine(anchor, datetime.min.time(), timezone.utc) + timedelta(hours=12)
    ruleset = rules_engine.active_rule_set  # noqa: F841  (rules loaded from DB below)
    with owner_tx() as conn:
        rule_rows = [dict(r._mapping) for r in conn.execute(text("SELECT * FROM rules.rules WHERE rule_set_id=:i"), {"i": ctx["rule_set"]})]
    out_counts = {"students": 0, "enrolments": 0}

    buckets_acc: dict = {}            # (slug, code, year, bucket) -> list of enrolment dicts
    students, skeys, enrols, events, verifs, confs, piis, regppl, regchecks, diags = [], [], [], [], [], [], [], [], [], []
    findings, attempts, audits, notifs, gtruth, provs = [], [], [], [], [], []
    reg_seen: set[str] = set()
    n_enrol = 0
    ghost_phones = [make_mobile(rng) for _ in range(6)]

    student_by_apaar: dict = {}

    def add_enrolment(slug: str, code: str, year: str, bucket: str, cur: bool, ghost: bool = False, person_in=None, force: dict | None = None):
        nonlocal n_enrol
        info, prog = ctx["inst"][slug], ctx["prog"][(slug, code)]
        n_enrol += 1
        eid = did("enrol", slug, code, year, bucket, n_enrol)
        sid = did("student", slug, code, year, n_enrol)
        person = person_in or new_person(rng, info["region"], info["district"], prog["level"], y0 if cur else y0 - 1)
        reuse = student_by_apaar.get(person.apaar) if (force and person_in is not None) else None
        if reuse:
            sid = reuse
        days_ago = force["days_ago"] if force and "days_ago" in force else (int(85 * rng.betavariate(1.7, 1.0)) if cur else 365 + int(85 * rng.betavariate(1.7, 1.0)))
        claimed_at = now_dt - timedelta(days=days_ago, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
        minor = (claimed_at.date() - person.dob).days < 18 * 365 + 4
        # --- identity path
        provisional = None
        r = rng.random()
        if (not ghost) and not force and r < 0.05:
            provisional = rng.choice(["NOGEN", "NOGEN", "REFUSED", "FOREIGN"])
        mismatch = None
        inst_name, inst_dob = person.name, person.dob
        if not provisional and not ghost and not force and rng.random() < 0.09:
            inst_name, inst_dob, mismatch = variant(rng, person)
        unavailable = (not provisional) and not force and rng.random() < 0.015
        if provisional:
            token = "PRV-" + secrets.token_hex(6).upper()
            ikey = crypto.identity_key("PROV:" + token)
            id_status, lock2, apaar = {"NOGEN": "NOGEN", "REFUSED": "CONSENT_REFUSED", "FOREIGN": "NOGEN"}[provisional], "PROVISIONAL_OK", None
            ident_type, reg_obj = "PROVISIONAL", None
        else:
            ikey = crypto.identity_key(person.apaar)
            ident_type, apaar = "APAAR", person.apaar
            if person.apaar not in reg_seen:
                reg_seen.add(person.apaar)
                regppl.append({"a": person.apaar, "n": person.name, "d": person.dob, "m": person.mobile})
            reg_obj = identity.RegistryResult("UNAVAILABLE") if unavailable else identity.RegistryResult(
                "FOUND", person.name, person.dob, person.mobile, "ACTIVE", 60, "CLOSED")
            diag = identity.diagnose(reg_obj, inst_name, inst_dob)
            id_status = diag.status
            lock2 = {"VERIFIED": "PASSED", "PROBABLE_MATCH": "PENDING", "PENDING_FIX": "FAILED", "PENDING_VERIFICATION": "PENDING"}[diag.status]
        # --- lock 3
        if cur and days_ago <= 30:
            confirm_p = 0.55 if not ghost else 0.08
        else:
            confirm_p = 0.82 if not ghost else 0.10
        confirmed = (not unavailable) and lock2 != "FAILED" and rng.random() < confirm_p
        assisted = confirmed and rng.random() < 0.04
        if confirmed:
            lock3 = "CONFIRMED"
        elif claimed_at + timedelta(days=30) < now_dt:
            lock3 = "EXPIRED"
        else:
            lock3 = "PENDING"
        disputed = cur and not confirmed and not ghost and rng.random() < 0.012
        if disputed:
            lock3 = "DISPUTED"
        overall = verification.overall_state(lock2, lock3)
        if id_status == "PENDING_VERIFICATION":
            overall = "PENDING"
        # --- status
        withdrawn = rng.random() < (0.06 if cur else 0.04) and not ghost and not force
        admitted = rng.random() < (0.78 if cur else 0.97)
        status = "WITHDRAWN" if withdrawn else ("ADMITTED" if admitted else "CLAIMED")
        if force and force.get("status"):
            status = force["status"]
            withdrawn = status in ("WITHDRAWN", "TRANSFERRED_OUT")
            admitted = True
        elif force:
            admitted = True
            status = "ADMITTED"
        admit_at = claimed_at + timedelta(days=rng.randint(0, 5), hours=3)
        if admitted and admit_at > now_dt:
            admit_at = now_dt - timedelta(hours=1)
        withdrawn_at = min(claimed_at + timedelta(days=rng.randint(1, 40)), now_dt - timedelta(hours=1)) if withdrawn else None
        # trust / channel
        if provisional or not apaar:
            channel, trust, phone = "INSTITUTION_MOBILE", "LOW", person.mobile
        elif ghost:
            channel, trust, phone = "REGISTRY_MOBILE", "HIGH", person.mobile
        else:
            channel, trust, phone = "REGISTRY_MOBILE", "HIGH", person.mobile
        if assisted:
            channel, trust = "ASSISTED", "LOW"
        elif confirmed and minor:
            channel = "GUARDIAN"
        elif confirmed and rng.random() < 0.06 and not provisional:
            channel, trust = "INSTITUTION_MOBILE", "LOW"
        inst_mobile = rng.choice(ghost_phones) if ghost else (person.mobile if rng.random() > 0.08 else None)
        ik_hex = ikey.hex()
        sig_blob = {"claim_id": str(eid), "institution_id": str(info["id"]), "programme_id": str(prog["id"]), "bucket": bucket,
                    "identity_key_hash": ik_hex, "claimed_at": claimed_at.isoformat(), "actor_id": str(ctx["users"][f"ia.{slug}"])}
        rec = {"institution_student_id": f"{slug.upper()[:4]}{year[2:4]}{n_enrol:05d}", "name": inst_name, "dob": inst_dob, "gender": person.gender,
               "category": "GEN", "citizenship_type": "FOREIGN" if provisional == "FOREIGN" else "INDIAN", "nationality_code": "IN",
               "admission_date": admit_at.date() if admitted else None, "bucket_type": bucket, "programme_level": prog["level"],
               "programme_mode": prog["mode"], "lateral_entry": prog["lateral"], "parent_name": person.parent if rng.random() > 0.12 else None,
               "mobile": inst_mobile, "today": anchor, "age_years": (claimed_at.date() - inst_dob).days // 365,
               "window_opens": (anchor - timedelta(days=86)) if cur else (anchor - timedelta(days=451))}
        # category assignment (+ injected inconsistencies that become findings)
        rec["category"] = rng.choices(["GEN", "OBC", "SC", "ST", "EWS"], [45, 30, 14, 5, 6])[0]
        if bucket == "SN_EWS":
            rec["category"] = "EWS" if rng.random() > 0.15 else "GEN"
        if bucket == "SN_FN":
            rec["citizenship_type"], rec["nationality_code"] = "FOREIGN", rng.choice(["NP", "BT", "LK", "BD", "AF"])
        if bucket == "SN_OCI":
            rec["citizenship_type"], rec["nationality_code"] = rng.choice(["OCI", "PIO"]), rng.choice(["US", "GB", "CA", "AE"])
        f_list = rules_engine.evaluate(rule_rows, rec, "C")
        if any(f["severity"] == "Blocker" for f in f_list):
            n_enrol -= 1
            return
        kid, _, pem = keys.signing_key("INSTITUTION_CLAIM", str(info["id"]))
        sig = crypto.sign_es256(pem, crypto.sha256(crypto.canonical_json(sig_blob)))
        dk_id, _, dkey = keys.data_key(str(info["id"]))
        nonce, ct = crypto.encrypt(dkey, {"name": inst_name, "dob": inst_dob.isoformat(), "parent_name": rec["parent_name"], "mobile": inst_mobile,
                                          "email": f"{inst_name.split()[0].lower()}.{n_enrol}@{EMAIL_DOMAIN}", "district": person.district,
                                          "apaar_id": apaar, "registry_mobile": person.mobile if apaar else None},
                                   f"{sid}|{info['id']}".encode())
        if not reuse:
            students.append({"id": sid, "ref": "STU-" + did("sref", sid).hex[:10].upper(), "t": ident_type, "st": id_status, "rs": "ACTIVE" if apaar else None})
            skeys.append({"id": did("key", sid), "s": sid, "v": s.identity_hmac_key_version, "k": ikey, "kind": ident_type})
        if apaar:
            student_by_apaar[apaar] = sid
        if provisional:
            provs.append({"id": did("prov", sid), "s": sid, "t": token, "ty": provisional, "e": now_dt + timedelta(days=365),
                          "c": now_dt + timedelta(days=120)})
        ia_id = ctx["users"][f"ia.{slug}"]
        enrols.append({"id": eid, "ref": "ENR-" + did("eref", eid).hex[:10].upper(), "st": sid, "inst": info["id"], "pg": prog["id"], "y": year,
                       "bt": bucket, "cc": (force or {}).get("cc") or ("EXCLUSIVE" if prog["mode"] == "FULL_TIME" else "CONCURRENT_OK"), "status": status,
                       "isid": rec["institution_student_id"], "ad": admit_at.date() if admitted else None, "ca": claimed_at, "cb": ia_id,
                       "src": "SEED", "rk": did("rowkey", eid).hex[:32], "rs": ctx["rule_set"], "ik": ikey, "kv": s.identity_hmac_key_version,
                       "g": person.gender, "c": rec["category"], "ct": rec["citizenship_type"], "nc": rec["nationality_code"], "by": inst_dob.year,
                       "mn": minor, "nt": crypto.name_tokens(inst_name), "wr": ("TRANSFER_OUT" if status == "TRANSFERRED_OUT" else rng.choice(WITHDRAW_REASONS)) if withdrawn else None, "_person": person, "sid": sid, "wa": withdrawn_at,
                       "slug": slug, "code": code, "ever_admitted": admitted or (withdrawn and rng.random() < 0.6), "admit_at": admit_at if admitted else None})
        buckets_acc.setdefault((slug, code, year, bucket), []).append(enrols[-1])
        ev_seq = 1
        events.append({"e": eid, "i": info["id"], "q": ev_seq, "t": "CLAIM", "p": dumps({"bucket": bucket, "source": "SEED"}), "a": ia_id, "at": claimed_at})
        if admitted:
            ev_seq += 1
            events.append({"e": eid, "i": info["id"], "q": ev_seq, "t": "ADMIT", "p": dumps({}), "a": ia_id, "at": admit_at})
        confirmed_at = None
        if lock3 in ("CONFIRMED", "EXPIRED", "DISPUTED"):
            confirmed_at = min(claimed_at + timedelta(days=rng.randint(0, 20), hours=2), now_dt - timedelta(minutes=30)) if lock3 == "CONFIRMED" \
                else claimed_at + timedelta(days=30)
            ev_seq += 1
            events.append({"e": eid, "i": info["id"], "q": ev_seq, "t": "VERIFICATION_CHANGE", "p": dumps({"lock3": lock3, "overall": overall}),
                           "a": None, "at": confirmed_at})
        if withdrawn:
            ev_seq += 1
            events.append({"e": eid, "i": info["id"], "q": ev_seq, "t": "TRANSFER_OUT" if status == "TRANSFERRED_OUT" else "WITHDRAW", "p": dumps({"reason": enrols[-1]["wr"]}), "a": ia_id, "at": withdrawn_at})
        verifs.append({"e": eid, "i": info["id"], "sig": sig, "kid": kid, "l2": lock2, "l3": lock3, "ov": overall, "ids": id_status, "ch": channel,
                       "tr": trust, "dl": claimed_at + timedelta(days=30), "fd": (claimed_at + timedelta(days=14)) if lock2 == "FAILED" else None,
                       "ca": confirmed_at if lock3 == "CONFIRMED" else None, "rem": 2 if (cur and days_ago >= 21) else (1 if (cur and days_ago >= 7) else 0)})
        if channel and phone and lock3 != "CONFIRMED" or lock3 == "CONFIRMED":
            confs.append({"id": did("conf", eid), "e": eid, "i": info["id"], "ch": channel if channel != "ASSISTED" else "INSTITUTION_MOBILE",
                          "tm": identity.mask_mobile(phone or "0000000000"), "h": "seeded", "salt": "seeded", "ex": claimed_at + timedelta(minutes=10),
                          "st": "CONFIRMED" if lock3 == "CONFIRMED" else "EXPIRED", "ca": claimed_at, "g": minor})
        piis.append({"id": did("pii", eid), "s": sid, "i": info["id"], "k": dk_id, "n": nonce, "c": ct, "ca": claimed_at})
        if reg_obj is not None:
            rc = did("rc", eid)
            regchecks.append({"id": rc, "e": eid, "i": info["id"], "st": reg_obj.status, "ns": diag.name_score, "dm": diag.dob_match, "v": diag.status,
                              "l": 60 + rng.randint(0, 80), "t": claimed_at})
            for m in diag.messages:
                diags.append({"id": did("diag", eid, m["code"]), "rc": rc, "e": eid, "i": info["id"], "st": diag.status, "f": m["field"], "c": m["code"],
                              "m": m["message"], "fx": dumps({"fix": m["fix"]}), "d": claimed_at + timedelta(days=14) if lock2 == "FAILED" else None,
                              "res": None})
        if mismatch:
            gtruth.append({"k": "MISMATCH_TYPE", "a": enrols[-1]["ref"], "b": None, "d": dumps({"type": mismatch, "diagnosis": id_status})})
        for f in f_list:
            findings.append({"id": did("find", eid, f["rule_code"]), "i": info["id"], "y": year, "rid": did("rule", f["rule_code"]), "c": f["rule_code"],
                             "rs": ctx["rule_set"], "e": eid, "s": f["severity"], "st": "OPEN" if rng.random() > 0.25 else "ACKNOWLEDGED", "m": f["message"],
                             "ev": dumps(f["evidence"]), "fx": dumps({"fix": f["fix"]}), "ca": claimed_at})
        if id_status == "PROBABLE_MATCH":
            findings.append({"id": did("find", eid, "IDN-PROBABLE"), "i": info["id"], "y": year, "rid": None, "c": "IDN-PROBABLE", "rs": ctx["rule_set"],
                             "e": eid, "s": "Warning", "st": "OPEN", "m": "Registry returned a probable (not certain) identity match.",
                             "ev": "{}", "fx": dumps({"fix": "A reviewer may confirm; correcting the spelling avoids delay."}), "ca": claimed_at})
        attempts.append({"i": info["id"], "p": prog["id"], "b": bucket, "s": "SEED", "o": "CLAIMED_PENDING_VERIFICATION" if unavailable else "CLAIMED",
                         "h": 201, "ik": ik_hex[:16], "t": claimed_at})
        audits.append({"at": claimed_at, "a": ia_id, "act": "claim.created", "et": "enrolment", "eid": str(eid), "i": info["id"],
                       "p": dumps({"programme_code": code, "bucket": bucket, "identity_status": id_status, "source": "SEED"})})
        if admitted:
            audits.append({"at": admit_at, "a": ia_id, "act": "enrolment.admit", "et": "enrolment", "eid": str(eid), "i": info["id"], "p": "{}"})
        if lock3 == "CONFIRMED":
            audits.append({"at": confirmed_at, "a": None, "act": "confirmation.confirmed", "et": "enrolment", "eid": str(eid), "i": info["id"],
                           "p": dumps({"channel": channel})})
        if withdrawn:
            audits.append({"at": withdrawn_at, "a": ia_id, "act": "enrolment.withdraw", "et": "enrolment", "eid": str(eid), "i": info["id"],
                           "p": dumps({"reason_code": enrols[-1]["wr"]})})
        if phone:
            notifs.append({"id": did("notif", eid), "k": "STUDENT", "ch": "SMS", "tc": "OTP_CONFIRM", "p": dumps({"to": identity.mask_mobile(phone), "channel": channel}),
                           "st": "SENT" if rng.random() > 0.01 else "FAILED", "i": info["id"], "e": eid, "ca": claimed_at})
        if cur and not confirmed and days_ago >= 7 and lock3 == "PENDING":
            notifs.append({"id": did("notif", eid, "rem"), "k": "STUDENT", "ch": "INAPP", "tc": "REMINDER", "p": dumps({}), "st": "SENT", "i": info["id"],
                           "e": eid, "ca": claimed_at + timedelta(days=7)})

    # ------------------------------------------------------------------ plan and generate
    per = profile["per_programme"]
    live_by_bucket: dict = {}
    for slug, info in ctx["inst"].items():
        ghost_inst = slug == "gce"
        for code in info["programmes"]:
            prog = ctx["prog"][(slug, code)]
            n_cur = max(6, int(rng.gauss(per, per * 0.35)))
            if ghost_inst:
                n_cur = max(n_cur, 36 if profile["institutions"] > 10 else 12)
            n_prev = int(n_cur * profile["prior_ratio"] * rng.uniform(0.8, 1.15))
            mix = [("REGULAR", 1.0)]
            if prog["lateral"]:
                mix = [("REGULAR", 0.9), ("LATERAL", 0.1)]
            if prog["level"] == "UG" and info["type"] in ("ENGINEERING", "PHARMACY") and profile["institutions"] > 10:
                mix = [("REGULAR", 0.86)] + ([("LATERAL", 0.07)] if prog["lateral"] else []) + [("SN_FN", 0.02), ("SN_OCI", 0.02), ("SN_TFW", 0.015), ("SN_EWS", 0.035)]
            for year, n_total, cur in ((year_cur, n_cur, True), (year_prev, n_prev, False)):
                for bucket, share in mix:
                    for _ in range(max(1 if share >= 0.015 and n_total > 20 else 0, int(round(n_total * share)))):
                        # ghost ring members: borrowed identities at GCE in the current cycle
                        add_enrolment(slug, code, year, bucket, cur, ghost=ghost_inst and cur and rng.random() < 0.6)

    # ------------------------------------------------------------------ labelled pairs for the duplicate/transfer engine (PRD 11.6)
    scale = 1.0 if profile["institutions"] > 10 else 0.12
    cnt = lambda n: max(2, int(n * scale))
    ok_inst = lambda sl: sl != "gce"
    fts = [(sl, c) for sl, info in ctx["inst"].items() for c in info["programmes"] if ctx["prog"][(sl, c)]["mode"] == "FULL_TIME" and ctx["prog"][(sl, c)]["level"] in ("UG", "DIPLOMA") and ok_inst(sl)]
    pgs = [(sl, c) for sl, info in ctx["inst"].items() for c in info["programmes"] if ctx["prog"][(sl, c)]["mode"] == "FULL_TIME" and ctx["prog"][(sl, c)]["level"] == "PG" and ok_inst(sl)]
    odl = [(sl, c) for sl, info in ctx["inst"].items() for c in info["programmes"] if ctx["prog"][(sl, c)]["mode"] in ("ONLINE", "ODL", "PART_TIME") and ok_inst(sl)]
    transfers_rows = []

    def other(pairs, not_slug):
        pool = [x for x in pairs if x[0] != not_slug]
        return rng.choice(pool) if pool else None

    def person_like(slug, code):
        info = ctx["inst"][slug]
        return new_person(rng, info["region"], info["district"], ctx["prog"][(slug, code)]["level"], y0)

    # DUPLICATE_TRUE: one person under two APAAR IDs at two institutions, overlapping this cycle (spelling variants in the second record)
    for _ in range(cnt(60)):
        a = rng.choice(fts); b = other(fts, a[0])
        if not b:
            continue
        n0 = len(enrols); add_enrolment(a[0], a[1], year_cur, "REGULAR", True, force={"days_ago": int(70 * rng.random()) + 10})
        if len(enrols) == n0:
            continue
        ea = enrols[-1]; base = ea["_person"]
        dup = Person(); dup.gender, dup.dob, dup.region, dup.district = base.gender, base.dob, base.region, base.district
        dup.parent, dup.mobile = base.parent, (base.mobile if rng.random() < 0.3 else make_mobile(rng)); dup.apaar = make_apaar(rng)
        first, *rest = base.name.split()
        dup.name = rng.choice([base.name, f"{first[0]} {' '.join(rest)}", base.name.replace("a", "aa", 1), base.name.upper()])
        n1 = len(enrols); add_enrolment(b[0], b[1], year_cur, "REGULAR", True, person_in=dup, force={"days_ago": int(60 * rng.random())})
        if len(enrols) > n1:
            gtruth.append({"k": "DUPLICATE_TRUE", "a": ea["ref"], "b": enrols[-1]["ref"], "d": dumps({"variant": dup.name != base.name})})
    # TRANSFER: out at A (TRANSFERRED_OUT), in at B with the same APAAR ID
    for _ in range(cnt(40)):
        a = rng.choice(fts); b = other(fts, a[0])
        if not b:
            continue
        person = person_like(*a)
        n0 = len(enrols); add_enrolment(a[0], a[1], year_cur, "REGULAR", True, person_in=person, force={"status": "TRANSFERRED_OUT", "days_ago": 45})
        if len(enrols) == n0:
            continue
        ea = enrols[-1]
        n1 = len(enrols); add_enrolment(b[0], b[1], year_cur, "REGULAR", True, person_in=person, force={"days_ago": 25})
        if len(enrols) > n1:
            eb = enrols[-1]
            transfers_rows.append({"id": did("transfer", ea["id"]), "s": ea["sid"], "oe": ea["id"], "ie": eb["id"], "oi": ea["inst"], "ii": eb["inst"], "d": (ea["wa"] or now_dt).date(), "st": "MATCHED"})
            gtruth.append({"k": "TRANSFER", "a": ea["ref"], "b": eb["ref"], "d": "{}"})
    # orphan transfer-out leads (no counterpart)
    for _ in range(cnt(6)):
        a = rng.choice(fts)
        n0 = len(enrols); add_enrolment(a[0], a[1], year_cur, "REGULAR", True, person_in=person_like(*a), force={"status": "TRANSFERRED_OUT", "days_ago": 75})
        if len(enrols) > n0:
            ea = enrols[-1]
            transfers_rows.append({"id": did("transfer", ea["id"]), "s": ea["sid"], "oe": ea["id"], "ie": None, "oi": ea["inst"], "ii": None, "d": (ea["wa"] or now_dt).date(), "st": "ORPHAN_LEAD"})
            gtruth.append({"k": "ORPHAN", "a": ea["ref"], "b": None, "d": "{}"})
    # FT_PLUS_ODL: full-time at A plus an online/part-time programme at B with the same APAAR (legitimate)
    for _ in range(cnt(30) if odl else 0):
        a = rng.choice(fts); b = rng.choice([x for x in odl if x[0] != a[0]] or odl)
        n0 = len(enrols); add_enrolment(a[0], a[1], year_cur, "REGULAR", True, force={"days_ago": 50})
        if len(enrols) == n0:
            continue
        ea = enrols[-1]; n1 = len(enrols)
        add_enrolment(b[0], b[1], year_cur, "REGULAR", True, person_in=ea["_person"], force={"days_ago": 30})
        if len(enrols) > n1 and enrols[-1]["inst"] != ea["inst"]:
            gtruth.append({"k": "FT_PLUS_ODL", "a": ea["ref"], "b": enrols[-1]["ref"], "d": "{}"})
    # EXCEPTION: UG full-time + PG full-time (approved dual-programme pair)
    ugs = [x for x in fts if ctx["prog"][x]["level"] == "UG"]
    for _ in range(cnt(10) if pgs and ugs else 0):
        a = rng.choice(ugs); b = other(pgs, a[0])
        if not b:
            continue
        n0 = len(enrols); add_enrolment(a[0], a[1], year_cur, "REGULAR", True, force={"days_ago": 55})
        if len(enrols) == n0:
            continue
        ea = enrols[-1]; n1 = len(enrols)
        add_enrolment(b[0], b[1], year_cur, "REGULAR", True, person_in=ea["_person"], force={"days_ago": 35, "cc": "EXCEPTION_APPROVED"})
        if len(enrols) > n1:
            gtruth.append({"k": "EXCEPTION", "a": ea["ref"], "b": enrols[-1]["ref"], "d": "{}"})
    # SEQUENTIAL: diploma last cycle, degree this cycle (legitimate progression, same APAAR)
    dips = [x for x in fts if ctx["prog"][x]["level"] == "DIPLOMA"]
    for _ in range(cnt(40) if dips and ugs else 0):
        a = rng.choice(dips); b = other(ugs, a[0])
        if not b:
            continue
        n0 = len(enrols); add_enrolment(a[0], a[1], year_prev, "REGULAR", False, force={"days_ago": 400})
        if len(enrols) == n0:
            continue
        ea = enrols[-1]; n1 = len(enrols)
        add_enrolment(b[0], b[1], year_cur, "REGULAR", True, person_in=ea["_person"], force={"days_ago": 40})
        if len(enrols) > n1:
            gtruth.append({"k": "SEQUENTIAL", "a": ea["ref"], "b": enrols[-1]["ref"], "d": "{}"})
    # spare registry people for demos (never enrolled)
    spare = []
    for k in range(300 if profile["institutions"] > 10 else 40):
        reg = rng.choice(list(SURNAMES))
        p = new_person(rng, reg, "x", "UG", y0)
        p.dob = date(y0 - rng.randint(19, 21), rng.randint(1, 12), rng.randint(13, 28))  # always adults (stories rely on it)
        spare.append(p)
        regppl.append({"a": p.apaar, "n": p.name, "d": p.dob, "m": p.mobile})

    # ------------------------------------------------------------------ write (in dependency order)
    with owner_tx() as conn:
        bulk(conn, "INSERT INTO mockext.registry_people (apaar_id, name, dob, mobile) VALUES (:a,:n,:d,:m) ON CONFLICT DO NOTHING", regppl)
        bulk(conn, "UPDATE mockext.registry_people SET is_spare=true WHERE apaar_id=:a", [{"a": p.apaar} for p in spare])
        # seat buckets with caps derived from target utilisation
        sb_rows, ledger = [], []
        sb_id: dict = {}
        for (slug, code, year, bucket), lst in buckets_acc.items():
            live = sum(1 for e in lst if e["status"] in ("CLAIMED", "ADMITTED"))
            util = pick_util(rng)
            cap = max(live, math.ceil(live / max(util, 0.2)))
            if slug in ("kit", "bvec", "mpb", "asm"):
                cap = max(cap, live + 8)         # demo institutions keep room for the story claims and for new claims in a demo
            if slug == "bvec" and code == "MECH" and bucket == "REGULAR" and year == year_cur:
                cap = live                       # fixture: 100% full (SEAT_LIMIT demo)
            elif slug == "kit" and code == "CSE" and bucket == "REGULAR" and year == year_cur:
                cap = live + 6                   # fixture: >90% once the story claims land (warning band)
            elif slug == "gce" and year == year_cur:
                cap = live + rng.randint(2, 3)   # over-admission institution sits near the limit (room for the ghost fixture)
            elif bucket.startswith("SN_"):
                cap = max(cap, live + 1)
            bid = did("bucket", slug, code, year, bucket)
            sb_id[(slug, code, year, bucket)] = bid
            ever = sum(1 for e in lst if e["ever_admitted"])
            sb_rows.append({"id": bid, "i": ctx["inst"][slug]["id"], "p": ctx["prog"][(slug, code)]["id"], "y": year, "bt": bucket, "cap": cap,
                            "occ": live, "cum": ever})
            events_b = sorted([(e["ca"], 1, e["id"]) for e in lst] + [(e["wa"], -1, e["id"]) for e in lst if e["wa"]])
            running = 0
            for t, d, eid in events_b:
                running += d
                ledger.append({"b": bid, "i": ctx["inst"][slug]["id"], "et": "DEBIT" if d == 1 else "RELEASE", "d": d, "oa": running, "e": eid,
                               "r": None if d == 1 else "seeded withdrawal", "ed": t.date(), "ca": t})
            for e in lst:
                e["bucket_id"] = bid
        bulk(conn, "INSERT INTO core.seat_buckets (id, institution_id, programme_id, academic_year, bucket_type, cap, occupied, cumulative_admitted) "
                   "VALUES (:id,:i,:p,:y,:bt,:cap,:occ,:cum)", sb_rows)
        bulk(conn, "INSERT INTO core.students (id, student_ref, identity_type, identity_status, registry_status) VALUES (:id,:ref,:t,:st,:rs)", students)
        bulk(conn, "INSERT INTO core.student_identity_keys (id, student_id, key_version, identity_key, kind) VALUES (:id,:s,:v,:k,:kind)", skeys)
        bulk(conn, "INSERT INTO core.provisional_ids (id, student_id, token, type, expires_at, compliance_deadline) VALUES (:id,:s,:t,:ty,:e,:c)", provs)
        bulk(conn, "INSERT INTO core.enrolments (id, enrolment_ref, student_id, institution_id, programme_id, academic_year, seat_bucket_id, bucket_type, "
                   "concurrency_class, status, institution_student_id, admission_date, claimed_at, claimed_by, claim_source, row_key, rule_set_id, "
                   "identity_key, key_version, gender, category, citizenship_type, nationality_code, birth_year, is_minor, name_tokens, withdrawn_reason, "
                   "withdrawn_at) VALUES (:id,:ref,:st,:inst,:pg,:y,:bid,:bt,:cc,:status,:isid,:ad,:ca,:cb,:src,:rk,:rs,:ik,:kv,:g,:c,:ct,:nc,:by,:mn,:nt,:wr,:wa)",
             [{**e, "bid": e["bucket_id"]} for e in enrols])
        bulk(conn, "INSERT INTO core.seat_ledger_entries (seat_bucket_id, institution_id, entry_type, delta, occupied_after, enrolment_id, reason, effective_date, created_at) "
                   "VALUES (:b,:i,:et,:d,:oa,:e,:r,:ed,:ca)", ledger)
        bulk(conn, "INSERT INTO core.enrolment_events (enrolment_id, institution_id, seq, event_type, payload, actor_id, occurred_at) "
                   "VALUES (:e,:i,:q,:t,CAST(:p AS jsonb),:a,:at)", events)
        bulk(conn, "INSERT INTO core.enrolment_verification (enrolment_id, institution_id, lock1_signature, lock1_kid, lock2_status, lock3_status, overall_state, "
                   "identity_status, channel, channel_trust, deadline_at, fix_deadline_at, confirmed_at, reminders_sent) VALUES (:e,:i,:sig,:kid,:l2,:l3,:ov,:ids,"
                   ":ch,:tr,:dl,:fd,:ca,:rem)", verifs)
        bulk(conn, "INSERT INTO core.confirmation_requests (id, enrolment_id, institution_id, channel, target_masked, otp_hash, otp_salt, otp_expires_at, status, "
                   "last_sent_at, send_window_start, for_guardian, created_at) VALUES (:id,:e,:i,:ch,:tm,:h,:salt,:ex,:st,:ca,:ca,:g,:ca)", confs)
        bulk(conn, "INSERT INTO pii.pii_records (id, student_id, institution_id, data_key_id, version, nonce, ciphertext, created_at) VALUES "
                   "(:id,:s,:i,:k,1,:n,:c,:ca)", piis)
        bulk(conn, "INSERT INTO identity.registry_checks (id, enrolment_id, institution_id, source, status, name_score, dob_match, verdict, latency_ms, breaker_state, "
                   "checked_at) VALUES (:id,:e,:i,'CLAIM',:st,:ns,:dm,:v,:l,'CLOSED',:t)", regchecks)
        bulk(conn, "INSERT INTO identity.diagnoses (id, registry_check_id, enrolment_id, institution_id, status, field, message_code, message, suggested_fix, deadline_at) "
                   "VALUES (:id,:rc,:e,:i,:st,:f,:c,:m,CAST(:fx AS jsonb),:d)", diags)
        bulk(conn, "INSERT INTO rules.findings (id, institution_id, academic_year, rule_id, rule_code, rule_set_id, enrolment_id, severity, status, message, evidence, "
                   "suggested_fix, created_at) VALUES (:id,:i,:y,:rid,:c,:rs,:e,:s,:st,:m,CAST(:ev AS jsonb),CAST(:fx AS jsonb),:ca)", findings)
        # extra claim attempts (conflicts, seat limit, invalid, blocked, duplicates) - realistic noise around the successes
        extra = []
        for slug, info in ctx["inst"].items():
            n_succ = sum(len(v) for (sl, _c, y, _b), v in buckets_acc.items() if sl == slug and y == year_cur)
            ghosty = slug == "gce"
            for outcome, status, share in (("CONFLICT", 409, 0.035 if not ghosty else 0.12), ("SEAT_LIMIT", 409, 0.02), ("INVALID", 422, 0.07),
                                            ("RULE_BLOCKED", 422, 0.035), ("DUPLICATE_SAME_INSTITUTION", 200, 0.014)):
                for _ in range(int(n_succ * share)):
                    code = rng.choice(info["programmes"])
                    extra.append({"i": info["id"], "p": ctx["prog"][(slug, code)]["id"], "b": "REGULAR", "s": rng.choice(["PORTAL", "PORTAL", "BULK", "API"]),
                                  "o": outcome, "h": status, "ik": secrets.token_hex(8), "t": now_dt - timedelta(days=int(85 * rng.betavariate(1.7, 1.0)),
                                                                                                                  hours=rng.randint(0, 20))})
        bulk(conn, "INSERT INTO core.claim_attempts (institution_id, programme_id, bucket_type, source, outcome, http_status, identity_key_hash, created_at) "
                   "VALUES (:i,:p,:b,:s,:o,:h,:ik,:t)", attempts + extra)
        bulk(conn, "INSERT INTO audit.audit_log (occurred_at, actor_id, actor_role, action, entity_type, entity_id, institution_id, payload) "
                   "VALUES (:at,:a,'IA',:act,:et,:eid,:i,CAST(:p AS jsonb))", sorted(audits, key=lambda a: a["at"]))
        bulk(conn, "INSERT INTO ops.notifications (id, recipient_kind, channel, template_code, params, status, attempts, institution_id, enrolment_id, created_at, sent_at) "
                   "VALUES (:id,:k,:ch,:tc,CAST(:p AS jsonb),:st,1,:i,:e,:ca,CASE WHEN :st='SENT' THEN :ca END)", notifs)
        if transfers_rows:
            bulk(conn, "INSERT INTO core.transfers (id, student_id, out_enrolment_id, in_enrolment_id, out_institution_id, in_institution_id, effective_date_out, status, orphan_after) "
                       "VALUES (:id,:s,:oe,:ie,:oi,:ii,:d,:st,now() + interval '1 day')", transfers_rows)
        # ground truth: ghost ring + over-admission institution
        ghosts = [e for e in enrols if e["slug"] == "gce" and e["y"] == year_cur]
        for e in ghosts:
            gtruth.append({"k": "GHOST_RING", "a": e["ref"], "b": None, "d": dumps({"institution": "gce"})})
        gtruth.append({"k": "OVER_ADMISSION", "a": "gce", "b": None, "d": dumps({"note": "buckets at 100% with low confirmation"})})
        bulk(conn, "INSERT INTO seed.ground_truth (kind, ref_a, ref_b, details) VALUES (:k,:a,:b,CAST(:d AS jsonb))", gtruth)
    out_counts.update(students=len(students), enrolments=len(enrols), findings=len(findings), attempts=len(attempts) + len(extra), audit=len(audits),
                      notifications=len(notifs), spare=[(p.apaar, p.name, p.dob.isoformat(), p.gender) for p in spare[:16]],
                      year_cur=year_cur, year_prev=year_prev)
    return out_counts
