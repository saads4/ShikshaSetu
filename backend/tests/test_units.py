"""Pure-function tests: checksums, Aadhaar guard, hygiene, names, dates, diagnosis, rules engine, crypto, merkle."""
from datetime import date

import pytest

from app import crypto, identity, rules_engine
from app.core import canonical_json


def test_mock_apaar_format_never_looks_like_aadhaar():
    from app.seed import make_apaar
    import random
    rng = random.Random(1)
    for _ in range(200):
        a = make_apaar(rng)
        assert identity.mock_apaar_valid(a) and not identity.verhoeff_valid(a) and a.startswith("99")


def test_aadhaar_guard_rejects_and_never_echoes_value():
    # build a Verhoeff-valid 12-digit number (Aadhaar-like) without using any real one
    base = "23456789012"
    for d in range(10):
        cand = base + str(d)
        if identity.verhoeff_valid(cand):
            h = identity.clean_apaar(cand)
            assert h.value is None and h.issues[0]["code"] == "AADHAAR_SUSPECTED"
            assert cand not in str(h.issues)
            return
    pytest.fail("no verhoeff-valid candidate")


def test_hygiene_variants():
    assert identity.clean_apaar("9.9E+11").issues[0]["code"] == "SCIENTIFIC_NOTATION"
    assert identity.clean_apaar("12345").issues[0]["code"] == "APAAR_FORMAT"
    assert identity.clean_apaar(None).issues[0]["code"] == "APAAR_MISSING"
    from app.seed import make_apaar
    import random
    a = make_apaar(random.Random(7))
    spaced = f"{a[:4]} {a[4:8]}-{a[8:]}"
    assert identity.clean_apaar(spaced).value == a


@pytest.mark.parametrize("given,reg,low,high", [
    ("Rahul Kumar", "Rahul Kumar", 1.0, 1.0), ("R Kumar", "Rahul Kumar", 0.92, 1.0), ("Mohd Imran", "Mohammed Imran", 0.92, 1.0),
    ("Priya Krishnan", "Priya Krishnn", 0.80, 0.99), ("Arjun Singh", "Sneha Patil", 0.0, 0.5)])
def test_name_similarity(given, reg, low, high):
    assert low <= identity.name_similarity(given, reg) <= high


def test_dob_comparison():
    assert identity.dob_compare(date(2007, 3, 5), date(2007, 3, 5)) == "EXACT"
    assert identity.dob_compare(date(2007, 5, 3), date(2007, 3, 5)) == "DAY_MONTH_SWAP"
    assert identity.dob_compare(date(2008, 3, 5), date(2007, 3, 5)) == "YEAR_TYPO"
    assert identity.dob_compare(date(2001, 1, 1), date(2007, 3, 5)) == "MISMATCH"


def test_diagnosis_taxonomy():
    reg = lambda: identity.RegistryResult("FOUND", "Rahul Kumar", date(2007, 3, 5), "0012345678", "ACTIVE")
    assert identity.diagnose(reg(), "Rahul Kumar", date(2007, 3, 5)).status == "VERIFIED"
    d = identity.diagnose(reg(), "Rahul Kumar", date(2007, 5, 3))
    assert d.status == "PENDING_FIX" and d.messages[0]["code"] == "DOB_DAY_MONTH_SWAP" and "Swap" in d.messages[0]["fix"]
    assert identity.diagnose(identity.RegistryResult("UNAVAILABLE"), "x", date(2000, 1, 1)).status == "PENDING_VERIFICATION"
    assert identity.diagnose(identity.RegistryResult("NOT_FOUND"), "x", date(2000, 1, 1)).status == "INVALID"


def test_circuit_breaker_opens_and_half_opens():
    b = identity.CircuitBreaker(threshold=3, reset_s=0)
    for _ in range(3):
        b.record(False)
    assert b.opened_at is not None
    assert b.state == "HALF_OPEN"          # reset_s=0 -> immediately half-open
    b.record(True)
    assert b.state == "CLOSED"


def test_rules_engine_kinds():
    rules = [
        {"id": "1", "rule_code": "R1", "category": "c", "kind": "REQUIRED_FIELD", "params": {"field": "name"}, "severity": "Blocker", "sites": ["C"], "message_template": "m", "fix_template": "f"},
        {"id": "2", "rule_code": "R2", "category": "c", "kind": "IMPLIES", "params": {"if": {"field": "bucket_type", "value": "SN_EWS"}, "then": {"field": "category", "value": "EWS"}},
         "severity": "Error", "sites": ["C"], "message_template": "m", "fix_template": "f"},
        {"id": "3", "rule_code": "R3", "category": "c", "kind": "BUCKET_CAP", "params": {"warn_pct": 90}, "severity": "Info", "sites": ["C"], "message_template": "m", "fix_template": "f"},
        {"id": "4", "rule_code": "R4", "category": "c", "kind": "COMPARE", "params": {"left": {"field": "age"}, "op": ">=", "right": {"value": 14}}, "severity": "Blocker",
         "sites": ["B"], "message_template": "m", "fix_template": "f"},
    ]
    rec = {"name": "", "bucket_type": "SN_EWS", "category": "GEN", "age": 10, "_bucket": {"cap": 10, "approved_extra": 0, "occupied": 9}}
    codes = [f["rule_code"] for f in rules_engine.evaluate(rules, rec, "C")]
    assert codes == ["R1", "R2", "R3"]                      # R4 is bulk-only (site B); ordered by severity
    assert [f["rule_code"] for f in rules_engine.evaluate(rules, rec, "B")] == ["R4"]


def test_findings_carry_no_pii_values():
    rules = [{"id": "1", "rule_code": "R1", "category": "c", "kind": "REGEX_FORMAT", "params": {"field": "name", "pattern": "[A-Z]+"}, "severity": "Error", "sites": ["C"],
              "message_template": "Name format is wrong.", "fix_template": "Use capitals."}]
    f = rules_engine.evaluate(rules, {"name": "secret-name"}, "C")[0]
    assert "secret-name" not in str(f)


def test_vault_roundtrip_and_tamper_detection():
    key, _ = crypto.new_data_key()
    nonce, ct = crypto.encrypt(key, {"name": "A"}, b"aad")
    assert crypto.decrypt(key, nonce, ct, b"aad") == {"name": "A"}
    with pytest.raises(Exception):
        crypto.decrypt(key, nonce, ct, b"other-aad")


def test_es256_sign_verify_and_tamper():
    pem, jwk = crypto.new_es256()
    sig = crypto.sign_es256(pem, b"message")
    assert crypto.verify_es256(jwk, b"message", sig)
    assert not crypto.verify_es256(jwk, b"messagE", sig)


def test_keyed_hash_is_deterministic_and_domain_separated():
    assert crypto.identity_key("X") == crypto.identity_key("X")
    assert crypto.identity_key("X") != crypto.keyed_hash("other", "X")


def test_canonical_json_is_stable():
    assert canonical_json({"b": 1, "a": [2, 1]}) == canonical_json({"a": [2, 1], "b": 1})
