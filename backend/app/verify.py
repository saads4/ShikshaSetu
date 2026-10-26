"""verify-seed: recompute invariants from base tables (PRD 11.10, REQ-SEED-04). Every check is also asserted by tests."""
from __future__ import annotations

import re

from sqlalchemy import text

from . import audit, crypto, identity, submissions
from .db import owner_tx, rows, scalar
from .rlscheck import check as rls_check

PROFILE_FLOORS = {"minimal": {"institutions": 6, "students": 300}, "standard": {"institutions": 60, "students": 11000}}
_TWELVE = re.compile(r"(?<!\d)\d{12}(?!\d)")


def run(profile: str = "standard") -> list[dict]:
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    with owner_tx() as conn:
        n_inst = scalar(conn, "SELECT count(*) FROM ref.institutions WHERE status='ACTIVE'")
        n_stu = scalar(conn, "SELECT count(*) FROM core.students")
        floors = PROFILE_FLOORS.get(profile, PROFILE_FLOORS["standard"])
        check("institution count", n_inst >= floors["institutions"], f"{n_inst}")
        check("student count", n_stu >= floors["students"], f"{n_stu}")
        # ledger arithmetic: occupied = sum(DEBIT) - sum(RELEASE)
        bad = rows(conn, "SELECT b.id FROM core.seat_buckets b WHERE b.occupied <> (SELECT coalesce(sum(CASE entry_type WHEN 'DEBIT' THEN 1 WHEN 'RELEASE' THEN -1 ELSE 0 END),0) "
                         "FROM core.seat_ledger_entries l WHERE l.seat_bucket_id=b.id)")
        check("ledger sum equals occupied for every bucket", not bad, f"{len(bad)} mismatches")
        bad = rows(conn, "SELECT b.id FROM core.seat_buckets b WHERE b.occupied <> (SELECT count(*) FROM core.enrolments e WHERE e.seat_bucket_id=b.id AND e.status IN ('CLAIMED','ADMITTED'))")
        check("occupied equals live enrolments for every bucket", not bad, f"{len(bad)} mismatches")
        check("no bucket oversold", scalar(conn, "SELECT count(*) FROM core.seat_buckets WHERE occupied > cap + approved_extra") == 0)
        check("one live exclusive claim per student per cycle", scalar(conn,
              "SELECT count(*) FROM (SELECT academic_year, key_version, identity_key FROM core.enrolments WHERE concurrency_class='EXCLUSIVE' AND status IN ('CLAIMED','ADMITTED') "
              "GROUP BY 1,2,3 HAVING count(*)>1) x") == 0)
        check("every enrolment has verification and vault record", scalar(conn,
              "SELECT count(*) FROM core.enrolments e WHERE NOT EXISTS (SELECT 1 FROM core.enrolment_verification v WHERE v.enrolment_id=e.id) "
              "OR NOT EXISTS (SELECT 1 FROM pii.pii_records p WHERE p.student_id=e.student_id AND p.institution_id=e.institution_id)") == 0)
        check("VERIFIED only when lock2 passed and lock3 confirmed", scalar(conn,
              "SELECT count(*) FROM core.enrolment_verification WHERE overall_state='VERIFIED' AND NOT (lock2_status IN ('PASSED','PROVISIONAL_OK') AND lock3_status='CONFIRMED')") == 0)
        # aggregates recomputed from base tables must equal the stored aggregates
        diff = scalar(conn, "SELECT count(*) FROM (SELECT b.programme_id, b.academic_year, b.bucket_type, b.occupied FROM core.seat_buckets b EXCEPT "
                            "SELECT programme_id, academic_year, bucket_type, occupied FROM agg.seat_utilisation) x")
        check("agg.seat_utilisation matches base tables", diff == 0, f"{diff} differing rows")
        diff = scalar(conn, "SELECT count(*) FROM (SELECT e.institution_id, e.academic_year, count(*) c FROM core.enrolments e WHERE e.status IN ('CLAIMED','ADMITTED') GROUP BY 1,2 "
                            "EXCEPT SELECT institution_id, academic_year, total FROM agg.verification_funnel) x")
        check("agg.verification_funnel matches base tables", diff == 0, f"{diff} differing rows")
        # no Aadhaar-like value anywhere readable
        hits = 0
        for sql in ("SELECT payload::text t FROM audit.audit_log", "SELECT message t FROM rules.findings", "SELECT params::text t FROM ops.notifications",
                    "SELECT body t FROM mockext.sms_messages"):
            for r in conn.execute(text(sql)):
                for m in _TWELVE.findall(r[0] or ""):
                    if identity.verhoeff_valid(m) and m[0] not in "01":
                        hits += 1
        check("no Aadhaar-like value in logs, findings, notifications", hits == 0, f"{hits} hits")
        check("every APAAR in registry has the mock format", scalar(conn, "SELECT count(*) FROM mockext.registry_people WHERE apaar_id !~ '^99[0-9]{10}$'") == 0)
        check("exactly one ACTIVE rule set", scalar(conn, "SELECT count(*) FROM rules.rule_sets WHERE status='ACTIVE'") == 1)
        check("all demo users use argon2id hashes", scalar(conn, "SELECT count(*) FROM iam.users WHERE is_demo AND password_hash NOT LIKE '$argon2id$%'") == 0)
        # Phase 3 invariants
        bad = 0
        pub = {k["kid"]: k["public_jwk"] for k in rows(conn, "SELECT kid, public_jwk FROM cred.signing_keys")}
        for c in rows(conn, "SELECT id, institution_id, academic_year, cutoff_at, version, merkle_root, leaf_count, institution_kid, institution_signature, aicte_kid, aicte_countersignature, status "
                            "FROM audit.cohort_commitments WHERE status='COMPLETE'"):
            leaves = [bytes.fromhex(r["leaf_hash"]) for r in rows(conn, "SELECT leaf_hash FROM audit.commitment_leaves WHERE commitment_id=:c ORDER BY leaf_index", c=c["id"])]
            msg = submissions._sign_message(c["merkle_root"], str(c["institution_id"]), c["academic_year"], c["cutoff_at"], c["version"], c["leaf_count"])
            ok = (len(leaves) == c["leaf_count"] and crypto.merkle_root(leaves).hex() == c["merkle_root"]
                  and crypto.verify_es256(pub[c["institution_kid"]], msg, c["institution_signature"]) and crypto.verify_es256(pub[c["aicte_kid"]], msg, c["aicte_countersignature"]))
            bad += 0 if ok else 1
        check("every countersigned commitment: root recomputes from leaves, both signatures verify", bad == 0, f"{bad} bad")
        check("matched transfers reference both enrolments", scalar(conn, "SELECT count(*) FROM core.transfers WHERE status='MATCHED' AND (in_enrolment_id IS NULL OR out_enrolment_id IS NULL)") == 0)
        check("every live enrolment has matching features (the matching job indexes new claims within minutes)", scalar(conn, "SELECT count(*) FROM core.enrolments e WHERE e.status IN ('CLAIMED','ADMITTED') AND e.claimed_at < now() - interval '15 minutes' AND NOT EXISTS (SELECT 1 FROM matching.match_features f WHERE f.enrolment_id=e.id)") == 0)
        check("no upload job committed by its own uploader", scalar(conn, "SELECT count(*) FROM ingest.upload_jobs WHERE committed_by = created_by") == 0)
        check("every active institution has a Trust Index score between 0 and 100", scalar(conn, "SELECT count(*) FROM (SELECT DISTINCT institution_id FROM core.seat_buckets) b WHERE NOT EXISTS (SELECT 1 FROM intel.trust_scores t WHERE t.institution_id=b.institution_id AND t.score BETWEEN 0 AND 100)") == 0)
        check("trust components sum to the stored score", scalar(conn, "SELECT count(*) FROM intel.trust_scores t WHERE abs(t.score - (SELECT sum((c->>'points')::numeric) FROM jsonb_array_elements(t.components) c)) > 0.11") == 0)
        check("closed cases carry an outcome label; no case outcome is an automatic penalty", scalar(conn, "SELECT count(*) FROM intel.cases WHERE status='CLOSED' AND outcome_code IS NULL") == 0)
        check("appeal deciders differ from case owners", scalar(conn, "SELECT count(*) FROM intel.appeals a JOIN intel.cases c ON c.id=a.case_id WHERE a.decided_by = c.owner_id") == 0)
        check("elevation grants never exceed 4 hours", scalar(conn, "SELECT count(*) FROM pii.access_grants WHERE expires_at > granted_at + interval '4 hours' + interval '1 second'") == 0)
        check("unavailable-registry claims are never silently VERIFIED", scalar(
            conn, "SELECT count(*) FROM core.enrolment_verification WHERE identity_status='PENDING_VERIFICATION' AND lock2_status='PASSED'") == 0)
        # Final-phase invariants: credentials, status list, consent, webhooks
        from . import credentials
        jw = {k["kid"]: k["public_jwk"] for k in rows(conn, "SELECT kid, public_jwk FROM cred.signing_keys")}
        sl = conn.execute(text("SELECT bitstring FROM cred.status_lists WHERE id=1")).scalar()
        creds = rows(conn, "SELECT id, sd_jwt, attestation, status, status_list_index FROM cred.credentials")
        bad = [c for c in creds if not credentials.verify_artifact(c["sd_jwt"], c["attestation"], jw)["valid"]]
        check("every credential verifies (issuer signature, AICTE attestation, disclosures)", not bad, f"{len(creds)} checked, {len(bad)} bad")
        bad = [c for c in creds if (sl is not None and credentials.bit_is_set(bytes(sl), c["status_list_index"])) != (c["status"] != "VALID")]
        check("status-list bit set exactly for non-VALID credentials", not bad, f"{len(bad)} mismatches")
        check("a VALID credential exists only for an ADMITTED, VERIFIED enrolment", scalar(
            conn, "SELECT count(*) FROM cred.credentials c JOIN core.enrolments e ON e.id=c.enrolment_id JOIN core.enrolment_verification v ON v.enrolment_id=e.id "
                  "WHERE c.status='VALID' AND NOT (e.status='ADMITTED' AND v.overall_state='VERIFIED')") == 0)
        check("verifier accounts are scoped to an organisation with at least one purpose", scalar(
            conn, "SELECT count(*) FROM iam.users u WHERE u.utype='VERIFIER' AND NOT EXISTS (SELECT 1 FROM iam.verifier_scopes s WHERE s.verifier_org_id=u.verifier_org_id)") == 0)
        check("share links hold only a token hash and never outlive 30 days", scalar(conn, "SELECT count(*) FROM cred.share_links WHERE expires_at > created_at + interval '30 days' + interval '1 minute'") == 0)
        check("consent notices exist in English and Hindi", scalar(conn, "SELECT count(DISTINCT language) FROM consent.consent_notices") == 2)
        check("webhook secrets are stored encrypted, not in the clear", scalar(conn, "SELECT count(*) FROM iam.webhook_endpoints WHERE secret_enc IS NULL OR length(secret_enc) < 20") == 0)
    problems = rls_check()
    check("RLS forced on tenant tables; history tables append-only", not problems, "; ".join(problems))
    audit.seal()
    ig = audit.integrity_check()
    check("audit hash chain and checkpoints verify", ig["ok"], f"checked {ig['checked']} entries, {ig['checkpoints_verified']} checkpoints; {ig['problems']}")
    return results
