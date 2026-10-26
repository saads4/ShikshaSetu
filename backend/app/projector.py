"""Outbox projector (REQ-DASH-01): turns business events into aggregate tables, recomputed from base tables.

Recomputing the affected (institution, year) slice from base rows (rather than incrementing counters) keeps the
projection idempotent: replaying an event can never double count.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .core import now
from .db import SYSTEM, owner_tx, rows, scalar, tx

BATCH = 500


def recompute(conn: Connection, institution_id: str, year: str, as_of) -> None:
    p = {"i": institution_id, "y": year, "t": as_of}
    conn.execute(text("DELETE FROM agg.seat_utilisation WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y"), p)
    conn.execute(text(
        "INSERT INTO agg.seat_utilisation (programme_id, academic_year, bucket_type, institution_id, state, district, region, institution_type, level, "
        "programme_name, institution_name, cap, approved_extra, occupied, cumulative_admitted, pct, as_of) "
        "SELECT b.programme_id, b.academic_year, b.bucket_type, b.institution_id, i.state, i.district, i.region, i.type, pr.level, pr.name, i.name, "
        "b.cap, b.approved_extra, b.occupied, b.cumulative_admitted, "
        "round(100.0 * b.occupied / greatest(b.cap + b.approved_extra, 1), 2), :t "
        "FROM core.seat_buckets b JOIN ref.programmes pr ON pr.id=b.programme_id JOIN ref.institutions i ON i.id=b.institution_id "
        "WHERE b.institution_id=CAST(:i AS uuid) AND b.academic_year=:y"), p)
    conn.execute(text("DELETE FROM agg.verification_funnel WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y"), p)
    conn.execute(text(
        "INSERT INTO agg.verification_funnel (institution_id, academic_year, total, pending, verified, unconfirmed, disputed, pending_fix, "
        "pending_registry, provisional, low_trust_channel, as_of) "
        "SELECT e.institution_id, e.academic_year, count(*), "
        "count(*) FILTER (WHERE v.overall_state='PENDING'), count(*) FILTER (WHERE v.overall_state='VERIFIED'), "
        "count(*) FILTER (WHERE v.overall_state='UNCONFIRMED'), count(*) FILTER (WHERE v.overall_state='DISPUTED'), "
        "count(*) FILTER (WHERE v.overall_state='PENDING_FIX'), count(*) FILTER (WHERE v.identity_status='PENDING_VERIFICATION'), "
        "count(*) FILTER (WHERE v.identity_status IN ('NOGEN','CONSENT_REFUSED')), count(*) FILTER (WHERE v.channel_trust='LOW'), :t "
        "FROM core.enrolments e JOIN core.enrolment_verification v ON v.enrolment_id=e.id "
        "WHERE e.institution_id=CAST(:i AS uuid) AND e.academic_year=:y AND e.status IN ('CLAIMED','ADMITTED') "
        "GROUP BY e.institution_id, e.academic_year"), p)
    conn.execute(text("DELETE FROM agg.findings_summary WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y"), p)
    conn.execute(text("INSERT INTO agg.findings_summary (institution_id, academic_year, severity, status, count) "
                      "SELECT institution_id, academic_year, severity, status, count(*) FROM rules.findings "
                      "WHERE institution_id=CAST(:i AS uuid) AND academic_year=:y GROUP BY 1,2,3,4"), p)
    conn.execute(text("DELETE FROM agg.claim_daily WHERE institution_id=CAST(:i AS uuid)"), p)
    conn.execute(text(
        "INSERT INTO agg.claim_daily (day, institution_id, claims, conflicts, seat_limit, invalid, duplicates_same_inst) "
        "SELECT (created_at AT TIME ZONE 'UTC')::date, institution_id, "
        "count(*) FILTER (WHERE outcome IN ('CLAIMED','CLAIMED_PENDING_VERIFICATION','CONCURRENT_OK','EXCEPTION_APPROVED')), "
        "count(*) FILTER (WHERE outcome='CONFLICT'), count(*) FILTER (WHERE outcome='SEAT_LIMIT'), "
        "count(*) FILTER (WHERE outcome IN ('INVALID','RULE_BLOCKED')), count(*) FILTER (WHERE outcome='DUPLICATE_SAME_INSTITUTION') "
        "FROM core.claim_attempts WHERE institution_id=CAST(:i AS uuid) GROUP BY 1,2"), p)


def project(limit: int = BATCH) -> int:
    """Process one batch of unpublished outbox events. Returns the number of events handled."""
    with owner_tx("SYSTEM") as conn:
        events = rows(conn, "SELECT id, institution_id, payload, created_at, topic, aggregate_id FROM ops.outbox_events WHERE published_at IS NULL "
                            "ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED", n=limit)
        if not events:
            conn.execute(text("INSERT INTO agg.projector_state (name, last_outbox_id, as_of) VALUES ('main', 0, :t) "
                              "ON CONFLICT (name) DO UPDATE SET as_of=:t"), {"t": now()})
            return 0
        touched = {(str(e["institution_id"]), e["payload"].get("academic_year")) for e in events if e["institution_id"]}
        t = now()
        for inst, year in touched:
            if year:
                recompute(conn, inst, year, t)
        from . import admin
        for e in events:                                   # webhook deliveries carry identifiers only (REQ-INTG-07)
            ev = None
            if e["topic"] == "verification.changed":
                ev = "confirmation.completed" if e["payload"].get("lock3") == "CONFIRMED" else "claim.pending_verification_resolved" if e["payload"].get("resolved") else None
            admin.emit(conn, e["institution_id"], e["topic"], e["aggregate_id"], e["payload"], ev)
        ids = [e["id"] for e in events]
        conn.execute(text("UPDATE ops.outbox_events SET published_at=:t, attempts=attempts+1 WHERE id = ANY(:ids)"), {"t": t, "ids": ids})
        conn.execute(text("INSERT INTO agg.projector_state (name, last_outbox_id, as_of) VALUES ('main', :l, :t) "
                          "ON CONFLICT (name) DO UPDATE SET last_outbox_id=:l, as_of=:t"), {"l": max(ids), "t": t})
    return len(events)


def recompute_all() -> None:
    """Rebuild every aggregate from base tables (used after seeding and by verify-seed)."""
    with owner_tx("SYSTEM") as conn:
        pairs = rows(conn, "SELECT DISTINCT institution_id, academic_year FROM core.seat_buckets")
        t = now()
        for r in pairs:
            recompute(conn, str(r["institution_id"]), r["academic_year"], t)
        conn.execute(text("UPDATE ops.outbox_events SET published_at=:t WHERE published_at IS NULL"), {"t": t})
        conn.execute(text("INSERT INTO agg.projector_state (name, last_outbox_id, as_of) VALUES ('main', "
                          "(SELECT coalesce(max(id),0) FROM ops.outbox_events), :t) ON CONFLICT (name) DO UPDATE SET "
                          "last_outbox_id=EXCLUDED.last_outbox_id, as_of=:t"), {"t": t})
