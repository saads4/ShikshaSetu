"""forced row level security on onboarding requests

Revision ID: 0008
"""
from pathlib import Path

from alembic import op

revision = "0008"
down_revision = "0007"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0008_onboarding_rls.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_or_national ON iam.onboarding_requests; ALTER TABLE iam.onboarding_requests NO FORCE ROW LEVEL SECURITY; ALTER TABLE iam.onboarding_requests DISABLE ROW LEVEL SECURITY")
