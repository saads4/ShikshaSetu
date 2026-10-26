"""student read policies for verification rows

Revision ID: 0004
"""
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0004_student_policies.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS student_own ON core.enrolment_verification; DROP POLICY IF EXISTS student_own ON core.confirmation_requests")
