"""foundation schema: ref, iam, pii, core, identity, rules, audit, ops, agg, mockext, seed

Revision ID: 0001
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0001_foundation.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for schema in ("seed", "mockext", "agg", "ops", "cred", "audit", "rules", "identity", "core", "pii", "iam", "ref", "ctx"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
