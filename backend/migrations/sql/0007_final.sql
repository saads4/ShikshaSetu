-- Final phase: verifier orgs, credentials, status list, share links, verification requests, consent wallet, corrections, DSR,
-- onboarding, webhooks, retention, rule-set simulation, liveness feed, guardian OTP.

-- ============================ iam ============================
CREATE TABLE iam.verifier_orgs (
  id uuid PRIMARY KEY, name text NOT NULL, tier text NOT NULL CHECK (tier IN ('REGULATOR','INSTITUTION','UNIVERSITY','EMPLOYER','BANK','EMBASSY')),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED')), contact_email text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE iam.verifier_scopes (
  id uuid PRIMARY KEY, verifier_org_id uuid NOT NULL REFERENCES iam.verifier_orgs(id),
  purpose_code text NOT NULL CHECK (purpose_code IN ('EMPLOYMENT_VERIFICATION','LOAN_ELIGIBILITY','VISA_SUPPORT','ADMISSION_TRANSFER','REGULATORY_CHECK')),
  allowed_fields text[] NOT NULL, allowed_questions text[] NOT NULL, hourly_limit int NOT NULL DEFAULT 60, UNIQUE (verifier_org_id, purpose_code)
);
ALTER TABLE iam.users ADD CONSTRAINT fk_users_vorg FOREIGN KEY (verifier_org_id) REFERENCES iam.verifier_orgs(id);
CREATE TABLE iam.onboarding_requests (
  id uuid PRIMARY KEY, aicte_id text NOT NULL, institution_id uuid REFERENCES ref.institutions(id), officer_name text NOT NULL, designation text NOT NULL, email text NOT NULL, phone text,
  identity_check text NOT NULL CHECK (identity_check IN ('MATCH','MISMATCH')), identity_note text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED')), status_token text UNIQUE NOT NULL,
  decided_by uuid REFERENCES iam.users(id), decided_at timestamptz, reason text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE iam.webhook_endpoints (
  id uuid PRIMARY KEY, institution_id uuid REFERENCES ref.institutions(id), url text NOT NULL, secret_enc bytea NOT NULL, events text[] NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','PAUSED','DISABLED')), created_by uuid REFERENCES iam.users(id), created_at timestamptz NOT NULL DEFAULT now()
);

-- ============================ ops ============================
CREATE TABLE ops.webhook_deliveries (
  id uuid PRIMARY KEY, endpoint_id uuid NOT NULL REFERENCES iam.webhook_endpoints(id), institution_id uuid, event_type text NOT NULL, payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','DELIVERED','RETRYING','DEAD_LETTERED')), attempts int NOT NULL DEFAULT 0, next_attempt_at timestamptz,
  last_status_code int, last_error text, first_attempt_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz
);
CREATE INDEX ix_whd_status ON ops.webhook_deliveries (status, next_attempt_at);
CREATE TABLE ops.webhook_sink (
  id bigserial PRIMARY KEY, endpoint_id uuid, headers jsonb NOT NULL, body text NOT NULL, signature_valid boolean NOT NULL, received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ops.retention_policies (
  id uuid PRIMARY KEY, data_class text UNIQUE NOT NULL, retention_days int NOT NULL, action text NOT NULL CHECK (action IN ('PURGE','ANONYMISE','MARK')), last_run_at timestamptz, last_count int
);

-- ============================ cred ============================
CREATE TABLE cred.status_lists (
  id int PRIMARY KEY, purpose text NOT NULL DEFAULT 'revocation', size int NOT NULL DEFAULT 131072, bitstring bytea NOT NULL, next_index int NOT NULL DEFAULT 0,
  version int NOT NULL DEFAULT 1, signed_jwt text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cred.credentials (
  id uuid PRIMARY KEY, credential_ref text UNIQUE NOT NULL, enrolment_id uuid NOT NULL REFERENCES core.enrolments(id), student_id uuid NOT NULL REFERENCES core.students(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id), type text NOT NULL DEFAULT 'ENROLMENT_PROOF' CHECK (type IN ('ENROLMENT_PROOF','YEAR_COMPLETION','DEGREE_DIPLOMA','TRANSCRIPT_SUMMARY')),
  issuer_kid text NOT NULL, aicte_kid text NOT NULL, status_list_id int NOT NULL REFERENCES cred.status_lists(id), status_list_index int NOT NULL, payload_digest text NOT NULL,
  sd_jwt text NOT NULL, attestation text NOT NULL, issued_by uuid REFERENCES iam.users(id), issued_at timestamptz NOT NULL, valid_until timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID','REVOKED','SUPERSEDED')), revoked_reason text, revoked_at timestamptz, superseded_by uuid
);
CREATE UNIQUE INDEX ux_cred_live ON cred.credentials (enrolment_id) WHERE status = 'VALID';
CREATE INDEX ix_cred_student ON cred.credentials (student_id);
CREATE TABLE cred.share_links (
  id uuid PRIMARY KEY, credential_id uuid NOT NULL REFERENCES cred.credentials(id), student_id uuid NOT NULL REFERENCES core.students(id), token_hash text UNIQUE NOT NULL,
  purpose_code text NOT NULL, fields text[] NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz, use_count int NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cred.verification_requests (
  id uuid PRIMARY KEY, verifier_org_id uuid REFERENCES iam.verifier_orgs(id), verifier_user_id uuid, credential_id uuid, student_id uuid,
  via text NOT NULL CHECK (via IN ('CREDENTIAL_REF','SHARE_LINK','PUBLIC')), purpose_code text, question jsonb NOT NULL DEFAULT '{}',
  outcome text NOT NULL CHECK (outcome IN ('YES','NO','NEUTRAL')), fields_disclosed text[] NOT NULL DEFAULT '{}', nonce text, expires_at timestamptz, ip_hash text,
  unknown_ref boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_vr_org ON cred.verification_requests (verifier_org_id, created_at);
CREATE INDEX ix_vr_student ON cred.verification_requests (student_id, created_at);

-- ============================ consent ============================
CREATE TABLE consent.consent_notices (id uuid PRIMARY KEY, code text NOT NULL, version int NOT NULL, language text NOT NULL CHECK (language IN ('en','hi')), body text NOT NULL, effective_from timestamptz NOT NULL DEFAULT now(), UNIQUE (code, version, language));
CREATE TABLE consent.consents (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), kind text NOT NULL DEFAULT 'SHARING' CHECK (kind IN ('SHARING','APAAR_USE')), purpose_code text, verifier_org_id uuid REFERENCES iam.verifier_orgs(id),
  scope jsonb NOT NULL DEFAULT '{}', notice_version int NOT NULL DEFAULT 1, status text NOT NULL CHECK (status IN ('GRANTED','REFUSED','WITHDRAWN','EXPIRED')),
  method text NOT NULL DEFAULT 'PORTAL' CHECK (method IN ('PORTAL','SHARE_LINK','GUARDIAN')), granted_at timestamptz NOT NULL, expires_at timestamptz, withdrawn_at timestamptz, given_by uuid
);
CREATE INDEX ix_consent_student ON consent.consents (student_id, status);
CREATE TABLE consent.consent_requests (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), verifier_org_id uuid NOT NULL REFERENCES iam.verifier_orgs(id), purpose_code text NOT NULL, question jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','GRANTED','REFUSED','EXPIRED')), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_consent_req_pending ON consent.consent_requests (student_id, verifier_org_id, purpose_code) WHERE status = 'PENDING';
CREATE TABLE consent.data_subject_requests (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), type text NOT NULL CHECK (type IN ('ACCESS','CORRECTION','ERASURE','GRIEVANCE')), details text,
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_REVIEW','FULFILLED','REJECTED_LAWFUL_RETENTION','REJECTED_OTHER')), decided_by uuid REFERENCES iam.users(id),
  decision_note text, created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, result jsonb
);
ALTER TABLE consent.guardian_links ADD COLUMN IF NOT EXISTS otp_hash text;
ALTER TABLE consent.guardian_links ADD COLUMN IF NOT EXISTS otp_salt text;
ALTER TABLE consent.guardian_links ADD COLUMN IF NOT EXISTS otp_expires_at timestamptz;
ALTER TABLE consent.guardian_links ADD COLUMN IF NOT EXISTS attempts int NOT NULL DEFAULT 0;

-- ============================ ingest / rules / intel ============================
CREATE TABLE ingest.correction_requests (
  id uuid PRIMARY KEY, enrolment_id uuid NOT NULL REFERENCES core.enrolments(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id), student_id uuid NOT NULL REFERENCES core.students(id),
  requested_by uuid NOT NULL REFERENCES iam.users(id), field text NOT NULL CHECK (field IN ('name','dob','parent_name','mobile','email')), proposed_nonce bytea NOT NULL, proposed_enc bytea NOT NULL,
  note text, status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED')), decided_by uuid REFERENCES iam.users(id), decided_at timestamptz, reason text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE rules.simulation_runs (
  id uuid PRIMARY KEY, draft_rule_set_id uuid NOT NULL REFERENCES rules.rule_sets(id), baseline_rule_set_id uuid NOT NULL REFERENCES rules.rule_sets(id), cycles text[] NOT NULL,
  status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED')), requested_by uuid NOT NULL REFERENCES iam.users(id), result jsonb, skipped_rules text[] NOT NULL DEFAULT '{}',
  error text, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE intel.liveness_feed (
  identity_key bytea PRIMARY KEY, has_abc_credit boolean NOT NULL DEFAULT false, has_exam_registration boolean NOT NULL DEFAULT false, last_activity_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL DEFAULT 'MOCK_FEED'
);

-- ============================ RLS ============================
-- Student-owned tables: visible to the student (app.student_id) or to system/aicte scope; credentials also to the issuing institution.
ALTER TABLE cred.credentials ENABLE ROW LEVEL SECURITY; ALTER TABLE cred.credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_or_student ON cred.credentials USING (ctx.visible(institution_id, student_id)) WITH CHECK (ctx.visible(institution_id, student_id));
ALTER TABLE ingest.correction_requests ENABLE ROW LEVEL SECURITY; ALTER TABLE ingest.correction_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_or_student ON ingest.correction_requests USING (ctx.visible(institution_id, student_id)) WITH CHECK (ctx.visible(institution_id, student_id));
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['cred.share_links','cred.verification_requests','consent.consents','consent.consent_requests','consent.data_subject_requests'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY student_or_system ON %s USING (ctx.visible(NULL, student_id)) WITH CHECK (ctx.visible(NULL, student_id))', t);
  END LOOP;
  ALTER TABLE ops.webhook_deliveries ENABLE ROW LEVEL SECURITY; ALTER TABLE ops.webhook_deliveries FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON ops.webhook_deliveries USING (institution_id IS NULL OR ctx.visible(institution_id)) WITH CHECK (institution_id IS NULL OR ctx.visible(institution_id));
  ALTER TABLE iam.webhook_endpoints ENABLE ROW LEVEL SECURITY; ALTER TABLE iam.webhook_endpoints FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON iam.webhook_endpoints USING (institution_id IS NULL OR ctx.visible(institution_id)) WITH CHECK (institution_id IS NULL OR ctx.visible(institution_id));
END $$;

-- Verification requests are written by the verifier path (system scope) with student_id NULL for unknown references.
DROP POLICY IF EXISTS student_or_system ON cred.verification_requests;
CREATE POLICY student_or_system ON cred.verification_requests USING (student_id IS NULL AND current_setting('app.scope', true) IN ('NATIONAL','REGION','STATE','SYSTEM') OR ctx.visible(NULL, student_id))
  WITH CHECK (student_id IS NULL OR ctx.visible(NULL, student_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA iam, ops, cred, consent, rules, intel, ingest TO app_runtime;
REVOKE ALL ON cred.signing_keys FROM app_runtime;
GRANT SELECT (id, kid, purpose, institution_id, alg, public_jwk, status, created_at) ON cred.signing_keys TO app_runtime;
REVOKE UPDATE, DELETE ON core.enrolment_events, core.seat_ledger_entries, audit.audit_checkpoints, audit.commitment_leaves, ingest.submission_history, intel.case_events FROM app_runtime;
REVOKE DELETE ON audit.audit_log FROM app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops, intel, cred, consent TO app_runtime;
