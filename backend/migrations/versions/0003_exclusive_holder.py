"""cross-tenant exclusive-claim lookup (SECURITY DEFINER, identifiers only)

Revision ID: 0003
"""
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0003_exclusive_holder.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS core.exclusive_holder(text, int, bytea)")
