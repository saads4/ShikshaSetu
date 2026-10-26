"""phase 3: ingest, submissions, commitments, matching, transfers

Revision ID: 0005
"""
from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0005_phase3.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ingest CASCADE; DROP SCHEMA IF EXISTS matching CASCADE; DROP TABLE IF EXISTS core.transfers, "
               "ref.window_extensions, audit.commitment_leaves, audit.cohort_commitments CASCADE")
