"""`sentinel eval` (PRD 14.6): duplicate-engine precision/recall against labelled seed ground truth, plus a check that legitimate pairs are not flagged."""
from __future__ import annotations

from .db import owner_tx, rows


def run() -> dict:
    with owner_tx() as conn:
        truth = rows(conn, "SELECT kind, ref_a, ref_b FROM seed.ground_truth WHERE kind IN ('DUPLICATE_TRUE','TRANSFER','FT_PLUS_ODL','EXCEPTION','SEQUENTIAL') AND ref_b IS NOT NULL")
        cands = rows(conn, "SELECT ea.enrolment_ref AS a, eb.enrolment_ref AS b, c.status, c.timeline_class, c.score FROM matching.match_candidates c "
                           "JOIN core.enrolments ea ON ea.id=c.enrolment_a JOIN core.enrolments eb ON eb.id=c.enrolment_b "
                           "WHERE ea.claim_source='SEED' AND eb.claim_source='SEED'")   # only the labelled, seeded universe
    key = lambda a, b: frozenset((a, b))
    by_pair = {key(c["a"], c["b"]): c for c in cands}
    dup_truth = {key(t["ref_a"], t["ref_b"]) for t in truth if t["kind"] == "DUPLICATE_TRUE"}
    flagged = {key(c["a"], c["b"]) for c in cands if c["status"] in ("REVIEW", "CONFIRMED_DUP") and c["timeline_class"] in ("OVERLAP_FT", "SAME_INST_READMIT")}
    tp = len(dup_truth & flagged)
    precision = tp / len(flagged) if flagged else 1.0
    recall = tp / len(dup_truth) if dup_truth else 1.0
    legit = {}
    for kind in ("TRANSFER", "FT_PLUS_ODL", "EXCEPTION", "SEQUENTIAL"):
        pairs = [key(t["ref_a"], t["ref_b"]) for t in truth if t["kind"] == kind]
        found = [by_pair.get(p) for p in pairs]
        wrongly_reviewed = sum(1 for c in found if c and c["status"] == "REVIEW")
        legit[kind] = {"labelled": len(pairs), "linked": sum(1 for c in found if c and c["status"] == "AUTO_LINKED"), "wrongly_sent_to_review": wrongly_reviewed}
    return {"duplicates": {"labelled": len(dup_truth), "flagged": len(flagged), "true_positives": tp, "precision": round(precision, 3), "recall": round(recall, 3)},
            "legitimate_pairs": legit, "notes": "Static weights on Bloom-filter Dice scores (DEC-12); thresholds: review >= 0.80, auto-link sequential >= 0.97."}
