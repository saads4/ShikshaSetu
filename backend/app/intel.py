"""Intelligence (REQ-INT): Trust Index, anomaly layers A/B/C, graph clusters.

Everything here produces LEADS for reviewers. The score only prioritises reviews; it triggers no penalty (C-17).
Layer D (academic liveness from a mock feed) is not part of this delivery.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import audit, claims, ops
from .core import dumps, now, uuid7
from .db import owner_tx, row, rows, scalar

log = logging.getLogger("shikshasetu.intel")
CONFIG_VERSION = "trust-v1"
MODEL_VERSION = "robust-z+isolation-forest-v1"
WEIGHTS = {"confirmation": 30, "alerts": 20, "conflicts": 15, "timeliness": 15, "gate": 10, "corrections": 10}
ALERT_W = {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2}


def band(score: float) -> str:
    return "LOW_PRIORITY" if score >= 80 else "WATCH" if score >= 60 else "REVIEW_PRIORITY"


# =================================================================== Trust Index
def _inputs(conn, year: str) -> dict:
    w = row(conn, "SELECT opens_at, closes_at FROM ref.submission_windows WHERE academic_year=:y ORDER BY opens_at DESC LIMIT 1", y=year)
    d: dict = {"window": w}
    d["conf"] = {r["institution_id"]: r for r in rows(conn,
        "SELECT e.institution_id, count(*) AS total, count(*) FILTER (WHERE v.overall_state='VERIFIED') AS ver, count(*) FILTER (WHERE v.overall_state='UNCONFIRMED') AS unc, "
        "count(*) FILTER (WHERE v.overall_state='DISPUTED') AS dis, count(*) FILTER (WHERE v.overall_state='PENDING' AND e.claimed_at < :t - interval '14 days') AS pend_old "
        "FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id WHERE e.academic_year=:y AND e.status IN ('CLAIMED','ADMITTED') GROUP BY 1", y=year, t=now())}
    d["alerts"] = defaultdict(list)
    for r in rows(conn, "SELECT institution_id, severity, id FROM intel.anomaly_alerts WHERE status IN ('OPEN','IN_CASE') AND academic_year=:y", y=year):
        d["alerts"][r["institution_id"]].append(r)
    d["attempts"] = {r["institution_id"]: r for r in rows(conn,
        "SELECT institution_id, count(*) AS total, count(*) FILTER (WHERE outcome='CONFLICT') AS conflicts, count(*) FILTER (WHERE outcome='SEAT_LIMIT') AS seat_limit "
        "FROM core.claim_attempts WHERE created_at >= :o GROUP BY 1", o=w["opens_at"] if w else now())}
    d["exceptions"] = {r["institution_id"]: r["n"] for r in rows(conn, "SELECT x.institution_id, count(*) AS n FROM core.seat_exceptions x JOIN core.seat_buckets b ON b.id=x.seat_bucket_id WHERE b.academic_year=:y GROUP BY 1", y=year)}
    d["subs"] = {r["institution_id"]: r for r in rows(conn, "SELECT institution_id, status, late_flag FROM ingest.submissions WHERE academic_year=:y", y=year)}
    d["extend"] = {r["institution_id"]: r["m"] for r in rows(conn, "SELECT institution_id, max(new_closes_at) AS m FROM ref.window_extensions WHERE status='APPROVED' GROUP BY 1")}
    d["corr"] = {r["institution_id"]: r["n"] for r in rows(conn, "SELECT e.institution_id, count(*) AS n FROM core.enrolment_events ev JOIN core.enrolments e ON e.id=ev.enrolment_id WHERE ev.event_type='CORRECT' AND e.academic_year=:y GROUP BY 1", y=year)}
    return d


def trust_for(inst, d: dict) -> tuple[float, list[dict]]:
    c = d["conf"].get(inst, {"total": 0, "ver": 0, "unc": 0, "dis": 0, "pend_old": 0})
    denom = c["ver"] + c["unc"] + c["dis"] + c["pend_old"]
    p1 = WEIGHTS["confirmation"] * (c["ver"] / denom if denom else 1)
    al = d["alerts"].get(inst, [])
    sw = sum(ALERT_W[a["severity"]] for a in al)
    p2 = WEIGHTS["alerts"] * (1 - min(1, sw / 5))
    at = d["attempts"].get(inst, {"total": 0, "conflicts": 0, "seat_limit": 0})
    rate = 1000 * at["conflicts"] / at["total"] if at["total"] else 0
    p3 = WEIGHTS["conflicts"] * (1 - min(1, rate / 50))
    sub, w = d["subs"].get(inst), d["window"]
    t = now()
    close = max([x for x in (w and w["closes_at"], d["extend"].get(inst)) if x]) if w else None
    if sub and sub["status"] in ("SUBMITTED", "UNDER_REVIEW", "CORRECTED", "ACCEPTED", "LOCKED"):
        p4, note = (WEIGHTS["timeliness"] / 2, "submitted late (extension)") if sub["late_flag"] else (WEIGHTS["timeliness"], "submitted on time")
    elif close and t > close:
        p4, note = 0.0, "window closed with no submission"
    else:
        p4, note = float(WEIGHTS["timeliness"]), "window still open"
    ex = d["exceptions"].get(inst, 0)
    p5 = WEIGHTS["gate"] * (1 - min(1, (at["seat_limit"] + ex) / 10))
    corr = d["corr"].get(inst, 0)
    p6 = WEIGHTS["corrections"] * (1 - min(1, (corr / c["total"] / 0.15) if c["total"] else 0))
    comps = [
        {"key": "confirmation", "label": "Triple-Lock confirmation", "max": 30, "points": round(p1, 1), "formula": "30 × VERIFIED / (VERIFIED + UNCONFIRMED + DISPUTED + PENDING older than 14 days)",
         "inputs": {"verified": c["ver"], "unconfirmed": c["unc"], "disputed": c["dis"], "pending_over_14d": c["pend_old"]}, "evidence": "/aicte/trust"},
        {"key": "alerts", "label": "Open anomaly alerts", "max": 20, "points": round(p2, 1), "formula": "20 × (1 − min(1, Σw / 5)); w = 1.0 HIGH, 0.5 MEDIUM, 0.2 LOW",
         "inputs": {"open_alerts": len(al), "weighted_sum": round(sw, 2)}, "evidence": "/aicte/alerts"},
        {"key": "conflicts", "label": "Claim conflicts", "max": 15, "points": round(p3, 1), "formula": "15 × (1 − min(1, conflicts per 1,000 attempts ÷ 50))",
         "inputs": {"conflicts": at["conflicts"], "attempts": at["total"], "per_1000": round(rate, 1)}, "evidence": "claim_attempts"},
        {"key": "timeliness", "label": "Submission timeliness", "max": 15, "points": round(p4, 1), "formula": "on time 15 · late 7.5 · closed with none 0 · window open 15", "inputs": {"state": note}, "evidence": "/aicte/submissions"},
        {"key": "gate", "label": "Intake gate hits and exceptions", "max": 10, "points": round(p5, 1), "formula": "10 × (1 − min(1, (SEAT_LIMIT attempts + exceptions requested) ÷ 10))",
         "inputs": {"seat_limit_attempts": at["seat_limit"], "exceptions_requested": ex}, "evidence": "/aicte/reviews"},
        {"key": "corrections", "label": "Correction rate", "max": 10, "points": round(p6, 1), "formula": "10 × (1 − min(1, corrections ÷ enrolments ÷ 0.15))", "inputs": {"corrections": corr, "enrolments": c["total"]}, "evidence": "enrolment_events"},
    ]
    return round(sum(x["points"] for x in comps), 1), comps


def run_trust(year: str | None = None) -> dict:
    year = year or claims.current_academic_year()
    with owner_tx() as conn:
        d = _inputs(conn, year)
        insts = [r["institution_id"] for r in rows(conn, "SELECT DISTINCT institution_id FROM core.seat_buckets WHERE academic_year=:y", y=year)]
        out = []
        for i in insts:
            score, comps = trust_for(i, d)
            out.append({"id": uuid7(), "i": i, "y": year, "s": score, "b": band(score), "c": dumps(comps), "v": CONFIG_VERSION})
        conn.execute(text("INSERT INTO intel.trust_scores (id, institution_id, academic_year, score, band, components, config_version) VALUES (:id,:i,:y,:s,:b,CAST(:c AS jsonb),:v) "
                          "ON CONFLICT (institution_id, academic_year, computed_date) DO UPDATE SET score=EXCLUDED.score, band=EXCLUDED.band, components=EXCLUDED.components, computed_at=now()"), out)
    return {"institutions": len(out), "bands": dict(Counter(o["b"] for o in out))}


# =================================================================== anomaly layers
FEATURES = {
    "growth": ("GROWTH_SPIKE", "Enrolment growth versus the institution's own prior cycle is far above its peers.", "Ask the institution to explain the intake growth and review the claim sources."),
    "late_upload": ("LATE_BURST", "An unusual share of claims arrived in the last 48 hours of the window.", "Review the timing of claims and the sources (bulk or API)."),
    "nogen_pending": ("IDENTITY_GAPS", "An unusually large share of records have no verified identity (provisional, pending or needing a fix).", "Check provisional-ID use and the fix-it backlog."),
    "shared_contact": ("SHARED_CONTACTS", "Many students at the institution share one institution-supplied phone number (a weighted review signal, never proof).", "Check whether the phone numbers belong to students or guardians."),
    "foreign_share": ("FOREIGN_SHARE_SPIKE", "The share of foreign-national and OCI students is far above peers.", "Check supernumerary bucket use."),
    "category_drift": ("CATEGORY_MIX_DRIFT", "The category mix moved sharply compared with the prior cycle.", "Compare the category mix with counselling records."),
    "zero_attrition": ("ZERO_ATTRITION", "No withdrawals at all despite a large intake.", "Check whether withdrawals are being recorded."),
    "unconfirmed_share": ("LOW_CONFIRMATION", "An unusually large share of students never confirmed their enrolment.", "Check contact details and consider assisted confirmation or a review of the claims."),
}


def _features(conn, year: str) -> pd.DataFrame:
    y0 = int(year[:4]); prev = f"{y0 - 1}-{str(y0)[2:]}"
    w = row(conn, "SELECT closes_at FROM ref.submission_windows WHERE academic_year=:y ORDER BY opens_at DESC LIMIT 1", y=year)
    end = min(w["closes_at"], now()) if w else now()
    base = pd.DataFrame(rows(conn,
        "SELECT i.id AS inst, i.state, i.type, "
        "count(*) FILTER (WHERE e.academic_year=:y) AS cur, count(*) FILTER (WHERE e.academic_year=:p) AS prior, "
        "count(*) FILTER (WHERE e.academic_year=:y AND e.status IN ('WITHDRAWN','TRANSFERRED_OUT')) AS withdrawn, "
        "count(*) FILTER (WHERE e.academic_year=:y AND e.citizenship_type <> 'INDIAN') AS foreign_n, "
        "count(*) FILTER (WHERE e.academic_year=:y AND e.claimed_at >= :end - interval '48 hours' AND e.claimed_at <= :end) AS last48 "
        "FROM ref.institutions i JOIN core.enrolments e ON e.institution_id=i.id WHERE i.status='ACTIVE' GROUP BY 1,2,3", y=year, p=prev, end=end))
    if base.empty:
        return base
    f = pd.DataFrame(rows(conn, "SELECT institution_id AS inst, total, pending_fix, pending_registry, provisional, unconfirmed FROM agg.verification_funnel WHERE academic_year=:y", y=year))
    base = base.merge(f, on="inst", how="left").fillna({"total": 0, "pending_fix": 0, "pending_registry": 0, "provisional": 0, "unconfirmed": 0})
    sc = pd.DataFrame(rows(conn, "SELECT institution_id AS inst, max(n) AS shared_contact FROM (SELECT e.institution_id, f.contact_hashes->>'mobile' AS h, count(*) AS n FROM matching.match_features f "
                                 "JOIN core.enrolments e ON e.id=f.enrolment_id GROUP BY 1,2) t WHERE h IS NOT NULL GROUP BY 1"))
    if sc.empty:
        sc = pd.DataFrame({"inst": [], "shared_contact": []})
    base = base.merge(sc, on="inst", how="left").fillna({"shared_contact": 0})
    cat = pd.DataFrame(rows(conn, "SELECT institution_id AS inst, academic_year AS y, category, count(*) AS n FROM core.enrolments WHERE academic_year IN (:y,:p) GROUP BY 1,2,3", y=year, p=prev))
    drift = {}
    for inst, g in cat.groupby("inst"):
        a = g[g.y == year].set_index("category").n; b = g[g.y == prev].set_index("category").n
        if a.sum() and b.sum():
            cats = set(a.index) | set(b.index)
            drift[inst] = 0.5 * sum(abs(a.get(c, 0) / a.sum() - b.get(c, 0) / b.sum()) for c in cats)
    base["category_drift"] = base.inst.map(drift)
    base["growth"] = np.where(base.prior >= 10, base.cur / base.prior.clip(lower=1), np.nan)
    base["late_upload"] = base.last48 / base.cur.clip(lower=1)
    base["nogen_pending"] = (base.provisional + base.pending_registry + base.pending_fix) / base.total.clip(lower=1)
    base["foreign_share"] = base.foreign_n / base.cur.clip(lower=1)
    base["zero_attrition"] = ((base.withdrawn == 0) & (base.cur >= 50)).astype(float)
    base["unconfirmed_share"] = base.unconfirmed / base.total.clip(lower=1)
    return base


def _robust_z(x: pd.Series, groups: pd.Series) -> pd.Series:
    """Robust z = 0.6745 (x - median) / MAD within the peer group (REQ-INT-05). If MAD is 0 the mean absolute deviation is used instead."""
    z = pd.Series(np.nan, index=x.index)
    for _, idx in groups.groupby(groups).groups.items():
        v = x.loc[idx].dropna()
        if len(v) < 5:
            continue
        med = v.median(); dev = (v - med).abs(); mad = dev.median()
        if mad > 0:
            z.loc[v.index] = 0.6745 * (v - med) / mad
        elif dev.mean() > 0:
            z.loc[v.index] = (v - med) / (1.253314 * dev.mean())
    return z


def _sev_by_z(z: float) -> str:
    return "HIGH" if abs(z) >= 6 else "MEDIUM" if abs(z) >= 4.5 else "LOW"


def _peer_groups(df: pd.DataFrame) -> pd.Series:
    """(state, type) when it has >= 5 peers, else the type nationally."""
    key = df.state + "|" + df.type
    sizes = key.map(key.value_counts())
    return pd.Series(np.where(sizes >= 5, key, "NATIONAL|" + df.type), index=df.index)


def run_anomalies(year: str | None = None) -> dict:
    year = year or claims.current_academic_year()
    created = Counter()
    with owner_tx() as conn:
        alerts: list[dict] = []
        # ---- Layer A: deterministic findings above a per-institution threshold
        for r in rows(conn, "SELECT institution_id, count(*) AS n, jsonb_object_agg(rc, c) AS by_rule FROM (SELECT institution_id, rule_code AS rc, count(*) AS c FROM rules.findings "
                            "WHERE status='OPEN' AND severity IN ('Blocker','Error') AND academic_year=:y GROUP BY 1,2) t GROUP BY 1", y=year):
            tot = sum(r["by_rule"].values())
            if tot >= 8:
                alerts.append({"i": r["institution_id"], "layer": "A", "type": "OPEN_BLOCKING_FINDINGS", "sev": "MEDIUM" if tot >= 15 else "LOW", "base": {"threshold": 8}, "dev": tot,
                               "ev": {"open_blocking_findings": tot, "by_rule": r["by_rule"]}, "ex": {"contributions": [{"feature": k, "value": v} for k, v in r["by_rule"].items()]},
                               "act": "Ask the institution to fix or justify the listed finding types before the cut-off."})
        # ---- Layer B: cross-institution duplicates
        for r in rows(conn, "SELECT inst, count(*) AS n, max(score) AS top FROM (SELECT institution_a AS inst, score FROM matching.match_candidates WHERE status IN ('REVIEW','CONFIRMED_DUP') AND timeline_class='OVERLAP_FT' "
                            "UNION ALL SELECT institution_b, score FROM matching.match_candidates WHERE status IN ('REVIEW','CONFIRMED_DUP') AND timeline_class='OVERLAP_FT') t GROUP BY 1"):
            if r["n"] >= 2:
                alerts.append({"i": r["inst"], "layer": "B", "type": "CROSS_INSTITUTION_DUPLICATES", "sev": "MEDIUM" if r["n"] >= 4 else "LOW", "base": {"threshold": 2}, "dev": r["n"],
                               "ev": {"candidate_pairs": r["n"], "top_score": float(r["top"])}, "ex": {"contributions": [{"feature": "overlapping pairs", "value": r["n"]}]},
                               "act": "Work through the duplicate queue entries for this institution."})
        # ---- Layer C: robust z-scores against peers, then Isolation Forest
        df = _features(conn, year)
        contrib_tbl: dict = {}
        if not df.empty:
            groups = _peer_groups(df)
            Z = pd.DataFrame({f: _robust_z(df[f], groups) for f in FEATURES})
            for f, (typ, msg, act) in FEATURES.items():
                for ix in Z.index[Z[f].abs() >= 3.5]:
                    if f in ("late_upload", "nogen_pending", "foreign_share", "unconfirmed_share", "shared_contact", "zero_attrition") and Z.loc[ix, f] < 0:
                        continue
                    r = df.loc[ix]
                    peer = df.loc[groups[groups == groups[ix]].index, f].dropna()
                    alerts.append({"i": r["inst"], "layer": "C", "type": typ, "sev": _sev_by_z(Z.loc[ix, f]), "base": {"peer_median": round(float(peer.median()), 3), "peers": int(len(peer)), "peer_group": groups[ix]},
                                   "dev": round(float(Z.loc[ix, f]), 2), "ev": {"value": round(float(r[f]), 3), "feature": f, "z": round(float(Z.loc[ix, f]), 2)},
                                   "ex": {"description": msg, "contributions": [{"feature": k, "robust_z": round(float(Z.loc[ix, k]), 2) if not np.isnan(Z.loc[ix, k]) else None} for k in FEATURES]}, "act": act})
            with_prior = int((df.prior > 0).sum())
            if with_prior >= 30:
                from sklearn.ensemble import IsolationForest
                X = df[list(FEATURES)].copy(); X = X.fillna(X.median()).fillna(0); X = (X - X.mean()) / X.std(ddof=0).replace(0, 1)
                model = IsolationForest(n_estimators=200, random_state=42).fit(X)
                score = -model.score_samples(X)
                cut = np.percentile(score, 97)
                for ix in np.where(score >= cut)[0]:
                    r = df.iloc[ix]
                    contrib = sorted(({"feature": k, "robust_z": None if np.isnan(Z.iloc[ix][k]) else round(float(Z.iloc[ix][k]), 2)} for k in FEATURES), key=lambda c: -abs(c["robust_z"] or 0))
                    alerts.append({"i": r["inst"], "layer": "C", "type": "MULTI_FEATURE_OUTLIER", "sev": "MEDIUM", "base": {"percentile_cut": 97, "institutions": len(df)}, "dev": round(float(score[ix]), 3),
                                   "ev": {"isolation_score": round(float(score[ix]), 3)}, "ex": {"description": "Unusual combination of features compared with all institutions (Isolation Forest).", "contributions": contrib[:8]},
                                   "act": "Review the top contributing features together."})
            else:
                created["isolation_forest_skipped"] = 1
        # ---- Layer D [SIMULATED]: academic liveness from the mock ABC / exam-registration feed
        cut = (now() - timedelta(days=360)).date()
        for r in rows(conn, "SELECT e.institution_id, count(*) AS eligible, count(f.identity_key) AS with_data, count(*) FILTER (WHERE f.identity_key IS NOT NULL AND NOT f.has_abc_credit AND NOT f.has_exam_registration) AS ghost "
                            "FROM core.enrolments e LEFT JOIN intel.liveness_feed f ON f.identity_key=e.identity_key WHERE e.status='ADMITTED' AND e.admission_date <= :c AND e.concurrency_class='EXCLUSIVE' GROUP BY 1", c=cut):
            if r["with_data"] >= 10 and r["ghost"] >= 5 and r["ghost"] / r["with_data"] >= 0.2:
                share = r["ghost"] / r["with_data"]
                alerts.append({"i": r["institution_id"], "layer": "D", "type": "LIVENESS_GAP", "sev": "HIGH" if share >= 0.4 else "MEDIUM", "base": {"min_share": 0.2, "min_candidates": 5}, "dev": round(share, 3),
                               "ev": {"eligible": r["eligible"], "with_feed_data": r["with_data"], "ghost_candidates": r["ghost"], "share": round(share, 3), "source": "MOCK_FEED (SIMULATED)"},
                               "ex": {"description": "Admitted students older than two semesters with no credit and no exam registration in the simulated feed. A lead only: the value claim is withheld and enrolments with no feed data are not counted.",
                                      "contributions": [{"feature": "no activity share", "value": round(share, 3)}, {"feature": "candidates", "value": r["ghost"]}]},
                               "act": "Check academic activity directly with the institution; the feed here is simulated."})
        # ---- graph clusters (Louvain): students sharing an institution-supplied phone
        clusters = _graph_clusters(conn, alerts)
        for a in alerts:
            conn.execute(text("INSERT INTO intel.anomaly_alerts (id, institution_id, layer, type, severity, academic_year, baseline, deviation, evidence, explanation, suggested_action, model_version, dedupe_key) "
                              "VALUES (:id,:i,:l,:t,:s,:y,CAST(:b AS jsonb),:d,CAST(:e AS jsonb),CAST(:x AS jsonb),:a,:m,:k) ON CONFLICT (dedupe_key) WHERE status IN ('OPEN','IN_CASE') DO NOTHING"),
                         {"id": uuid7(), "i": a["i"], "l": a["layer"], "t": a["type"], "s": a["sev"], "y": year, "b": dumps(a["base"]), "d": a["dev"], "e": dumps(a["ev"]), "x": dumps(a["ex"]), "a": a["act"],
                          "m": MODEL_VERSION, "k": f"{a['layer']}:{a['type']}:{a['i']}:{year}"})
            created[a["layer"]] += 1
    return {"alerts_considered": sum(v for k, v in created.items() if k in "ABCDG"), "by_layer": {k: v for k, v in created.items() if k in "ABCDG"}, "graph_clusters": clusters,
            "isolation_forest": "skipped (fewer than 30 institutions with a prior cycle)" if created.get("isolation_forest_skipped") else "scored"}


def _graph_clusters(conn, alerts: list) -> int:
    import networkx as nx
    data = rows(conn, "SELECT e.id, e.enrolment_ref, e.institution_id, f.contact_hashes->>'mobile' AS h FROM matching.match_features f JOIN core.enrolments e ON e.id=f.enrolment_id "
                      "WHERE f.contact_hashes ? 'mobile' AND e.status IN ('CLAIMED','ADMITTED')")
    cnt = Counter(d["h"] for d in data)
    shared = [d for d in data if cnt[d["h"]] >= 2]
    G = nx.Graph()
    for d in shared:
        G.add_edge(f"e:{d['id']}", f"m:{d['h']}")
    inst_of = {f"e:{d['id']}": d["institution_id"] for d in shared}
    ref_of = {f"e:{d['id']}": d["enrolment_ref"] for d in shared}
    conn.execute(text("DELETE FROM intel.graph_clusters WHERE run_date=current_date"))
    n = 0
    if G.number_of_nodes():
        for k, comm in enumerate(nx.community.louvain_communities(G, seed=42)):
            studs = [x for x in comm if x.startswith("e:")]
            contacts = [x for x in comm if x.startswith("m:")]
            if len(studs) < 8 or not contacts:
                continue
            insts = Counter(inst_of[s] for s in studs)
            top = insts.most_common(1)[0][0]
            score = round(len(studs) / max(len(contacts), 1), 2)
            conn.execute(text("INSERT INTO intel.graph_clusters (id, run_date, cluster_key, institution_ids, student_count, shared_contact_types, score, evidence) VALUES (:id, current_date, :k, :i, :n, ARRAY['mobile'], :s, CAST(:e AS jsonb))"),
                         {"id": uuid7(), "k": f"c{k}", "i": list(insts), "n": len(studs), "s": score, "e": dumps({"contact_nodes": len(contacts), "students": len(studs), "students_per_contact": score})})
            alerts.append({"i": top, "layer": "G", "type": "SHARED_CONTACT_CLUSTER", "sev": "MEDIUM" if len(studs) >= 15 else "LOW", "base": {"min_students": 8}, "dev": len(studs),
                           "ev": {"students": len(studs), "contact_nodes": len(contacts), "institutions": len(insts), "students_per_contact": score, "sample_enrolments": sorted(ref_of[x] for x in studs if inst_of[x] == top)[:5]},
                           "ex": {"description": "A cluster of students linked by shared institution-supplied phone numbers. Guardians legitimately share phones, so this is a weighted review signal only.",
                                  "contributions": [{"feature": "students in cluster", "value": len(studs)}, {"feature": "shared phone numbers", "value": len(contacts)}]},
                           "act": "Check whether the shared numbers are guardians' or a single party's; compare with confirmation outcomes."})
            n += 1
    return n


def run_all(year: str | None = None) -> dict:
    """Nightly pipeline, idempotent per (institution, year, date): alerts first (they feed the score), then the Trust Index."""
    a = run_anomalies(year)
    t = run_trust(year)
    with owner_tx() as conn:
        audit.log(conn, "intel.pipeline_run", entity_type="pipeline", payload={"alerts": a["by_layer"], "trust": t["bands"]})
    return {"anomalies": a, "trust": t}


@ops.handler("intel.pipeline")
def _handler(payload: dict) -> dict:
    return run_all(payload.get("year"))
