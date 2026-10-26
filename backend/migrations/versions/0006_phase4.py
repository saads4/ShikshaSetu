"""phase 4: trust index, alerts, appeals, elevation grants, report jobs

Revision ID: 0006
"""
from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0006_phase4.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.report_jobs, pii.access_grants, intel.appeals, intel.graph_clusters, intel.anomaly_alerts, intel.trust_scores CASCADE")
