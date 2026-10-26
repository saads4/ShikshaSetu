"""REQ-ACL-04 / REQ-AUD-07 self-check: forced RLS on tenant tables, no UPDATE/DELETE on history tables."""
from __future__ import annotations

from sqlalchemy import text

from .db import engine

TENANT_SCHEMAS = ("iam", "pii", "core", "identity", "rules", "ops", "audit")
HISTORY = ("core.enrolment_events", "core.seat_ledger_entries", "audit.audit_checkpoints", "audit.audit_log")


def check() -> list[str]:
    problems: list[str] = []
    with engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT c.table_schema, c.table_name, cl.relrowsecurity, cl.relforcerowsecurity FROM information_schema.columns c "
            "JOIN pg_class cl ON cl.relname = c.table_name JOIN pg_namespace ns ON ns.oid = cl.relnamespace AND ns.nspname = c.table_schema "
            "JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name AND t.table_type='BASE TABLE' "
            "WHERE c.column_name='institution_id' AND c.table_schema = ANY(:s)"), {"s": list(TENANT_SCHEMAS)}).all()
        for schema, name, rls, forced in rows:
            if not (rls and forced):
                problems.append(f"{schema}.{name} lacks forced row level security")
        for table in HISTORY:
            for priv in ("UPDATE", "DELETE"):
                if table == "audit.audit_log" and priv == "UPDATE":
                    continue  # seal columns only; guarded by trigger
                ok = conn.execute(text("SELECT has_table_privilege('app_runtime', :t, :p)"), {"t": table, "p": priv}).scalar()
                if ok:
                    problems.append(f"app_runtime has {priv} on history table {table}")
    return problems
