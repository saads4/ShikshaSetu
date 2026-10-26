"""guardian links and cases

Revision ID: 0002
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0002_consent_cases.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS intel CASCADE; DROP SCHEMA IF EXISTS consent CASCADE")
