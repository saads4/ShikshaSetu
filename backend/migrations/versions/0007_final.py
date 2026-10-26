"""final phase: credentials, verifier, consent, DSR, onboarding, webhooks, simulation, liveness

Revision ID: 0007
"""
from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"

SQL = Path(__file__).resolve().parents[1] / "sql" / "0007_final.sql"


def upgrade() -> None:
    op.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intel.liveness_feed, rules.simulation_runs, ingest.correction_requests, consent.data_subject_requests, consent.consent_requests, consent.consents, "
               "consent.consent_notices, cred.verification_requests, cred.share_links, cred.credentials, cred.status_lists, ops.retention_policies, ops.webhook_sink, ops.webhook_deliveries, "
               "iam.webhook_endpoints, iam.onboarding_requests, iam.verifier_scopes, iam.verifier_orgs CASCADE")
