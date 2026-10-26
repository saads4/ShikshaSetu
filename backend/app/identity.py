"""Identity validation (REQ-IDN): hygiene, Aadhaar guard, registry adapter with circuit breaker, diagnosis.

APAAR format is pluggable (G-01). The mock format (DEC-17): 12 digits, prefix 99, Luhn-valid and NOT Verhoeff-valid,
so a synthetic ID can never be mistaken for an Aadhaar number.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import httpx
from rapidfuzz import fuzz

from . import http
from .config import get_settings

# ---------------------------------------------------------------- checksums
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
        alt = not alt
    return total % 10 == 0


def mock_apaar_valid(v: str) -> bool:
    return bool(re.fullmatch(r"99\d{10}", v)) and luhn_valid(v) and not verhoeff_valid(v)


def looks_like_aadhaar(v: str) -> bool:
    return bool(re.fullmatch(r"\d{12}", v)) and verhoeff_valid(v) and v[0] not in "01"


# ---------------------------------------------------------------- hygiene
@dataclass
class Hygiene:
    value: str | None
    issues: list[dict] = field(default_factory=list)


def clean_apaar(raw: str | None) -> Hygiene:
    """REQ-IDN-01/02. Never logs or returns an Aadhaar-like value."""
    if raw is None or not str(raw).strip():
        return Hygiene(None, [{"code": "APAAR_MISSING", "field": "apaar_id", "message": "No APAAR ID supplied.",
                               "fix": "Enter the APAAR ID, or use a provisional ID if the student does not have one."}])
    s = re.sub(r"[\s\-]", "", str(raw)).upper()
    if re.fullmatch(r"\d(\.\d+)?E\+?\d+", s):
        return Hygiene(None, [{"code": "SCIENTIFIC_NOTATION", "field": "apaar_id",
                               "message": "The ID looks like it was converted to scientific notation by a spreadsheet.",
                               "fix": "Re-export the column as text so leading digits are kept."}])
    if looks_like_aadhaar(s):
        return Hygiene(None, [{"code": "AADHAAR_SUSPECTED", "field": "apaar_id",
                               "message": "This looks like an Aadhaar number. Aadhaar must never be entered here.",
                               "fix": "Remove it and enter the APAAR ID. Aadhaar is never stored."}])
    if not re.fullmatch(r"\d{12}", s):
        return Hygiene(None, [{"code": "APAAR_FORMAT", "field": "apaar_id", "message": "An APAAR ID has 12 digits.",
                               "fix": "Check for missing or extra characters."}])
    if not mock_apaar_valid(s):
        return Hygiene(None, [{"code": "APAAR_CHECKSUM", "field": "apaar_id", "message": "The ID fails its format check.",
                               "fix": "Re-type the ID carefully; a digit is probably wrong."}])
    return Hygiene(s)


# ---------------------------------------------------------------- names, dates
_VARIANTS = {"mohd": "mohammed", "md": "mohammed", "mohammad": "mohammed", "muhammad": "mohammed", "mohamed": "mohammed",
             "kum": "kumar", "kr": "kumar", "shri": "", "smt": "", "mr": "", "mrs": "", "ms": "", "dr": ""}


def normalise_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    toks = [_VARIANTS.get(t, t) for t in re.sub(r"[^a-z\s]", " ", s).split()]
    return " ".join(t for t in toks if t)


def phonetic(token: str) -> str:
    """Compact phonetic key (lightweight Soundex-like; a fuller Indic-aware encoder is a documented limitation)."""
    t = re.sub(r"[^a-z]", "", token.lower())
    if not t:
        return ""
    t = re.sub(r"(ph)", "f", t)
    t = re.sub(r"(ck|q)", "k", t)
    t = re.sub(r"(th|dh|bh|kh|gh|sh|ch)", lambda m: m.group(1)[0], t)
    t = t.replace("w", "v").replace("z", "j").replace("y", "i")
    first, rest = t[0], re.sub(r"[aeiouh]", "", t[1:])
    rest = re.sub(r"(.)\1+", r"\1", rest)
    return (first + rest)[:6]


def expand_initials(given: str, reference: str) -> str:
    """'R Kumar' vs registry 'Rahul Kumar' - expand single-letter tokens using the reference name."""
    ref = reference.split()
    out = []
    for tok in given.split():
        if len(tok) == 1:
            match = next((r for r in ref if r.startswith(tok)), tok)
            out.append(match)
        else:
            out.append(tok)
    return " ".join(out)


def name_similarity(a: str, b: str) -> float:
    na, nb = normalise_name(a), normalise_name(b)
    na = expand_initials(na, nb)
    nb_exp = expand_initials(nb, na)
    base = fuzz.token_set_ratio(na, nb_exp) / 100
    pa = " ".join(sorted(phonetic(t) for t in na.split()))
    pb = " ".join(sorted(phonetic(t) for t in nb_exp.split()))
    ph = fuzz.ratio(pa, pb) / 100
    return round(1.0 if base == 1.0 else base * 0.8 + ph * 0.2, 3)


def dob_compare(given: date, registry: date) -> str:
    if given == registry:
        return "EXACT"
    if given.year == registry.year and given.day == registry.month and given.month == registry.day:
        return "DAY_MONTH_SWAP"
    if given.month == registry.month and given.day == registry.day and abs(given.year - registry.year) in (1, 10, 100, 11, 9):
        return "YEAR_TYPO"
    if given.month == registry.month and given.day == registry.day and given.year % 100 == registry.year % 100:
        return "YEAR_TYPO"
    return "MISMATCH"


def mask_mobile(m: str) -> str:
    d = re.sub(r"\D", "", m)
    return f"+91 ••••• ••{d[-3:]}" if len(d) >= 3 else "••••"


# ---------------------------------------------------------------- registry adapter + breaker
class CircuitBreaker:
    def __init__(self, threshold: int, reset_s: int):
        self.threshold, self.reset_s = threshold, reset_s
        self.failures, self.opened_at = 0, None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "CLOSED"
        return "HALF_OPEN" if time.monotonic() - self.opened_at >= self.reset_s else "OPEN"

    def allow(self) -> bool:
        return self.state != "OPEN"

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.failures, self.opened_at = 0, None
            else:
                self.failures += 1
                if self.failures >= self.threshold or self.state == "HALF_OPEN":
                    self.opened_at = time.monotonic()


@dataclass
class RegistryResult:
    status: str                       # FOUND | NOT_FOUND | UNAVAILABLE
    name: str | None = None
    dob: date | None = None
    mobile: str | None = None
    registry_status: str | None = None
    latency_ms: int = 0
    breaker: str = "CLOSED"
    source: str = "MOCK_APAAR_REGISTRY"   # honesty rule: always labelled


_breaker: CircuitBreaker | None = None


def breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        s = get_settings()
        _breaker = CircuitBreaker(s.registry_breaker_threshold, s.registry_breaker_reset_s)
    return _breaker


def reset_breaker() -> None:
    global _breaker
    _breaker = None


def registry_lookup(apaar_id: str) -> RegistryResult:
    """Call the (mock) registry outside any DB transaction. Timeout/breaker -> UNAVAILABLE, never a silent pass."""
    s = get_settings()
    b = breaker()
    if not b.allow():
        return RegistryResult("UNAVAILABLE", breaker=b.state)
    t0 = time.monotonic()
    try:
        r = http.client().get(f"{s.mock_services_url}/registry/people/{apaar_id}", timeout=s.registry_timeout_ms / 1000)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 404:
            b.record(True)
            return RegistryResult("NOT_FOUND", latency_ms=ms, breaker=b.state)
        if r.status_code != 200:
            raise httpx.HTTPError(f"status {r.status_code}")
        d = r.json()
        b.record(True)
        return RegistryResult("FOUND", d["name"], date.fromisoformat(d["dob"]), d.get("mobile"), d.get("status"), ms, b.state)
    except Exception:
        b.record(False)
        return RegistryResult("UNAVAILABLE", latency_ms=int((time.monotonic() - t0) * 1000), breaker=b.state)


# ---------------------------------------------------------------- diagnosis
@dataclass
class Diagnosis:
    status: str                    # VERIFIED | PROBABLE_MATCH | PENDING_FIX | PENDING_VERIFICATION | INVALID
    name_score: float | None = None
    dob_match: str | None = None
    messages: list[dict] = field(default_factory=list)
    registry: RegistryResult | None = None


def diagnose(res: RegistryResult, given_name: str, given_dob: date) -> Diagnosis:
    """REQ-IDN-05/06/07: field comparison with a field-level message and suggested fix."""
    if res.status == "UNAVAILABLE":
        return Diagnosis("PENDING_VERIFICATION", registry=res, messages=[{
            "code": "REGISTRY_UNAVAILABLE", "field": "apaar_id",
            "message": "The APAAR registry could not be reached. The seat is held and the check will be retried.",
            "fix": "No action needed."}])
    if res.status == "NOT_FOUND":
        return Diagnosis("INVALID", registry=res, messages=[{
            "code": "APAAR_NOT_FOUND", "field": "apaar_id", "message": "This APAAR ID was not found in the registry.",
            "fix": "Check the ID. If the student was never issued one, use a provisional ID (no APAAR)."}])
    score = name_similarity(given_name, res.name or "")
    dob = dob_compare(given_dob, res.dob) if res.dob else "MISMATCH"
    msgs: list[dict] = []
    if dob == "DAY_MONTH_SWAP":
        msgs.append({"code": "DOB_DAY_MONTH_SWAP", "field": "dob", "message": "Date of birth appears to have day and month swapped.",
                     "fix": "Swap the day and month in the institution record."})
    elif dob == "YEAR_TYPO":
        msgs.append({"code": "DOB_YEAR_TYPO", "field": "dob", "message": "Date of birth differs only in the year (likely a typo).",
                     "fix": "Correct the birth year in the institution record."})
    elif dob == "MISMATCH":
        msgs.append({"code": "DOB_MISMATCH", "field": "dob", "message": "Date of birth does not match the registry.",
                     "fix": "Verify the date of birth against the student's certificate."})
    if score < 0.80:
        msgs.append({"code": "NAME_MISMATCH", "field": "name", "message": f"Name similarity {score:.2f} is below the match threshold.",
                     "fix": "Correct the name spelling to match the APAAR record."})
    elif score < 0.92:
        msgs.append({"code": "NAME_PROBABLE", "field": "name", "message": f"Name similarity {score:.2f}: probable match.",
                     "fix": "A reviewer may confirm; correcting spelling avoids delay."})
    if dob == "EXACT" and score >= 0.92:
        status = "VERIFIED"
    elif dob in ("EXACT",) and score >= 0.80:
        status = "PROBABLE_MATCH"
    else:
        status = "PENDING_FIX"
    return Diagnosis(status, score, dob, msgs, res)
