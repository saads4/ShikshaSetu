-- ShikshaSetu foundation schema (PRD section 7). Enumerations are text + CHECK.
-- The runtime connects as the owner and executes each request inside
-- SET LOCAL ROLE app_runtime, so RLS and the history-table grants apply to application code.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    CREATE ROLE app_runtime NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
GRANT app_runtime TO CURRENT_USER;

CREATE SCHEMA IF NOT EXISTS ctx;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS pii;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS rules;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS cred;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS agg;
CREATE SCHEMA IF NOT EXISTS mockext;
CREATE SCHEMA IF NOT EXISTS seed;

-- Tenant visibility predicate used by every RLS policy (PRD 7.1).
CREATE OR REPLACE FUNCTION ctx.visible(inst uuid, stu uuid DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT current_setting('app.scope', true) IN ('NATIONAL','REGION','STATE','SYSTEM')
      OR (inst IS NOT NULL AND inst = nullif(current_setting('app.institution_id', true), '')::uuid)
      OR (stu IS NOT NULL AND stu = nullif(current_setting('app.student_id', true), '')::uuid)
$$;

-- ============================ ref ============================
CREATE TABLE ref.institutions (
  id uuid PRIMARY KEY, aicte_id text UNIQUE NOT NULL, name text NOT NULL, slug text UNIQUE NOT NULL,
  type text NOT NULL CHECK (type IN ('ENGINEERING','POLYTECHNIC','MANAGEMENT','PHARMACY','UNIVERSITY_DEPT')),
  state text NOT NULL, district text NOT NULL,
  region text NOT NULL CHECK (region IN ('NORTH','SOUTH','EAST','WEST','CENTRAL','NORTH_EAST')),
  nodal_officer_name text, nodal_officer_email text,
  public_contact_name text, public_contact_email text, public_contact_phone text,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','PENDING_APPROVAL','SUSPENDED','CLOSED')),
  merged_into_id uuid REFERENCES ref.institutions(id), former_names jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ref.programmes (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  programme_code text NOT NULL, name text NOT NULL,
  level text NOT NULL CHECK (level IN ('UG','PG','DIPLOMA','PGDM')), discipline text NOT NULL,
  mode text NOT NULL DEFAULT 'FULL_TIME' CHECK (mode IN ('FULL_TIME','PART_TIME','ODL','ONLINE')),
  lateral_entry boolean NOT NULL DEFAULT false, duration_years int NOT NULL DEFAULT 4,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISCONTINUED')),
  successor_programme_id uuid REFERENCES ref.programmes(id),
  UNIQUE (institution_id, programme_code)
);
CREATE TABLE ref.seat_bucket_types (
  code text PRIMARY KEY, label text NOT NULL, is_supernumerary boolean NOT NULL DEFAULT false, description text
);
CREATE TABLE ref.intake_versions (
  id uuid PRIMARY KEY, programme_id uuid NOT NULL REFERENCES ref.programmes(id), academic_year text NOT NULL,
  version_no int NOT NULL DEFAULT 1, approved_intake int NOT NULL CHECK (approved_intake >= 0),
  effective_from date NOT NULL, effective_to date, source text NOT NULL DEFAULT 'MOCK_AICTE_MASTER',
  UNIQUE (programme_id, academic_year, version_no)
);
CREATE TABLE ref.intake_bucket_caps (
  id uuid PRIMARY KEY, intake_version_id uuid NOT NULL REFERENCES ref.intake_versions(id),
  bucket_type text NOT NULL REFERENCES ref.seat_bucket_types(code), cap int NOT NULL CHECK (cap >= 0),
  UNIQUE (intake_version_id, bucket_type)
);
CREATE TABLE ref.submission_windows (
  id uuid PRIMARY KEY, academic_year text NOT NULL, label text NOT NULL,
  opens_at timestamptz NOT NULL, closes_at timestamptz NOT NULL, cutoff_at timestamptz NOT NULL,
  CHECK (opens_at < closes_at)
);
CREATE TABLE ref.enrolment_exception_rules (
  id uuid PRIMARY KEY, name text NOT NULL, description text, level_a text NOT NULL, level_b text NOT NULL,
  mode_a text, mode_b text, active boolean NOT NULL DEFAULT true
);

-- ============================ iam ============================
CREATE TABLE iam.users (
  id uuid PRIMARY KEY, username text NOT NULL, display_name text NOT NULL, email text,
  description text, utype text NOT NULL CHECK (utype IN ('INSTITUTION','AICTE','STUDENT','VERIFIER')),
  password_hash text NOT NULL, is_demo boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED')),
  institution_id uuid REFERENCES ref.institutions(id), student_id uuid, verifier_org_id uuid,
  locale text NOT NULL DEFAULT 'en' CHECK (locale IN ('en','hi')),
  failed_attempts int NOT NULL DEFAULT 0, locked_until timestamptz, last_login_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_users_username ON iam.users (lower(username));
CREATE TABLE iam.roles (code text PRIMARY KEY CHECK (code IN ('IA','IO','AR','AS','ST','VU','CL')), label text NOT NULL);
CREATE TABLE iam.role_permissions (
  role_code text NOT NULL REFERENCES iam.roles(code), permission text NOT NULL, PRIMARY KEY (role_code, permission)
);
CREATE TABLE iam.user_roles (
  id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES iam.users(id), role_code text NOT NULL REFERENCES iam.roles(code),
  scope_type text NOT NULL CHECK (scope_type IN ('NATIONAL','REGION','STATE','INSTITUTION')), scope_value text,
  UNIQUE (user_id, role_code, scope_type, scope_value)
);
CREATE TABLE iam.sessions (
  id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES iam.users(id), issued_at timestamptz NOT NULL,
  last_seen_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz,
  user_agent text, ip_hash text
);
CREATE INDEX ix_sessions_user ON iam.sessions (user_id);
CREATE TABLE iam.api_clients (
  id uuid PRIMARY KEY, institution_id uuid REFERENCES ref.institutions(id), name text NOT NULL,
  client_id text UNIQUE NOT NULL, secret_hash text NOT NULL, scopes text[] NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED')), last_used_at timestamptz,
  is_demo boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE iam.idempotency_keys (
  actor_id uuid NOT NULL, key text NOT NULL, route text NOT NULL, request_hash text NOT NULL,
  state text NOT NULL CHECK (state IN ('IN_PROGRESS','DONE')), response_status int, response_body jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, PRIMARY KEY (actor_id, key)
);
CREATE TABLE iam.step_up_tokens (
  id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES iam.sessions(id), issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL
);

-- ============================ cred (keys only in this phase) ============================
CREATE TABLE cred.signing_keys (
  id uuid PRIMARY KEY, kid text UNIQUE NOT NULL,
  purpose text NOT NULL CHECK (purpose IN ('ISSUER','AICTE_ATTEST','AICTE_COMMIT','SEALER','VERIFIER_ANSWER','STATUS_LIST','INSTITUTION_CLAIM')),
  institution_id uuid REFERENCES ref.institutions(id), alg text NOT NULL DEFAULT 'ES256',
  public_jwk jsonb NOT NULL, private_wrapped bytea NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ROTATING','RETIRED','COMPROMISED')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ============================ pii ============================
CREATE TABLE pii.data_keys (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), version int NOT NULL DEFAULT 1,
  wrapped_key bytea NOT NULL, status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','RETIRED','DESTROYED')),
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (institution_id, version)
);

-- ============================ rules ============================
CREATE TABLE rules.rule_sets (
  id uuid PRIMARY KEY, name text NOT NULL, version_no int UNIQUE NOT NULL,
  status text NOT NULL CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
  drafted_by uuid REFERENCES iam.users(id), published_by uuid REFERENCES iam.users(id), published_at timestamptz,
  effective_from timestamptz, retroactive boolean NOT NULL DEFAULT false, notes text,
  CHECK (published_by IS NULL OR published_by <> drafted_by)
);
CREATE UNIQUE INDEX ux_one_active_ruleset ON rules.rule_sets ((status)) WHERE status = 'ACTIVE';
CREATE TABLE rules.rules (
  id uuid PRIMARY KEY, rule_set_id uuid NOT NULL REFERENCES rules.rule_sets(id), rule_code text NOT NULL,
  category text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('REQUIRED_FIELD','REGEX_FORMAT','COMPARE','IMPLIES','MUTUALLY_EXCLUSIVE','DATE_WITHIN','LOOKUP_IN_REFERENCE','BUCKET_CAP','SUM_RECONCILES')),
  params jsonb NOT NULL DEFAULT '{}', severity text NOT NULL CHECK (severity IN ('Blocker','Error','Warning','Info')),
  sites text[] NOT NULL DEFAULT '{C,B,S}', regulatory_ref text, owner text NOT NULL DEFAULT 'AICTE',
  message_template text NOT NULL, fix_template text, UNIQUE (rule_set_id, rule_code)
);

-- ============================ core ============================
CREATE TABLE core.students (
  id uuid PRIMARY KEY, student_ref text UNIQUE NOT NULL,
  identity_type text NOT NULL CHECK (identity_type IN ('APAAR','PROVISIONAL')),
  identity_status text NOT NULL CHECK (identity_status IN ('VERIFIED','PROBABLE_MATCH','PENDING_FIX','PENDING_VERIFICATION','NOGEN','CONSENT_REFUSED','INVALID')),
  registry_status text, merged_into_id uuid REFERENCES core.students(id), created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE iam.users ADD CONSTRAINT fk_users_student FOREIGN KEY (student_id) REFERENCES core.students(id);
CREATE TABLE core.student_identity_keys (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), key_version int NOT NULL,
  identity_key bytea NOT NULL, kind text NOT NULL CHECK (kind IN ('APAAR','PROVISIONAL')),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')), UNIQUE (key_version, identity_key)
);
CREATE TABLE core.provisional_ids (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), token text UNIQUE NOT NULL,
  type text NOT NULL CHECK (type IN ('NOGEN','REFUSED','FOREIGN')), document_hash bytea, document_last4 text,
  expires_at timestamptz NOT NULL, compliance_deadline timestamptz, upgraded_key_id uuid REFERENCES core.student_identity_keys(id),
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','UPGRADED','EXPIRED'))
);
CREATE TABLE core.seat_buckets (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  programme_id uuid NOT NULL REFERENCES ref.programmes(id), academic_year text NOT NULL,
  bucket_type text NOT NULL REFERENCES ref.seat_bucket_types(code), cap int NOT NULL,
  approved_extra int NOT NULL DEFAULT 0, occupied int NOT NULL DEFAULT 0, cumulative_admitted int NOT NULL DEFAULT 0,
  intake_version_id uuid REFERENCES ref.intake_versions(id), warn_alerted boolean NOT NULL DEFAULT false,
  UNIQUE (programme_id, academic_year, bucket_type),
  CONSTRAINT ck_occupied_within_cap CHECK (occupied BETWEEN 0 AND cap + approved_extra),
  CHECK (cumulative_admitted >= 0)
);
CREATE TABLE core.seat_exceptions (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  seat_bucket_id uuid NOT NULL REFERENCES core.seat_buckets(id), requested_by uuid NOT NULL REFERENCES iam.users(id),
  extra_seats int NOT NULL CHECK (extra_seats > 0), justification text NOT NULL, evidence_note text,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
  approved_seats int, decided_by uuid REFERENCES iam.users(id), decided_at timestamptz, decision_reason text,
  expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), row_version int NOT NULL DEFAULT 1,
  CHECK (decided_by IS NULL OR decided_by <> requested_by)
);
CREATE TABLE core.enrolments (
  id uuid PRIMARY KEY, enrolment_ref text UNIQUE NOT NULL, student_id uuid NOT NULL REFERENCES core.students(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id), programme_id uuid NOT NULL REFERENCES ref.programmes(id),
  academic_year text NOT NULL, seat_bucket_id uuid NOT NULL REFERENCES core.seat_buckets(id),
  bucket_type text NOT NULL REFERENCES ref.seat_bucket_types(code),
  concurrency_class text NOT NULL CHECK (concurrency_class IN ('EXCLUSIVE','CONCURRENT_OK','EXCEPTION_APPROVED','TRANSFER_PENDING')),
  status text NOT NULL CHECK (status IN ('CLAIMED','ADMITTED','WITHDRAWN','TRANSFERRED_OUT')),
  institution_student_id text NOT NULL, admission_date date, claimed_at timestamptz NOT NULL,
  claimed_by uuid REFERENCES iam.users(id), claim_source text NOT NULL CHECK (claim_source IN ('PORTAL','BULK','API','SEED')),
  row_key text NOT NULL, rule_set_id uuid REFERENCES rules.rule_sets(id),
  identity_key bytea NOT NULL, key_version int NOT NULL,
  gender text, category text, citizenship_type text NOT NULL DEFAULT 'INDIAN', nationality_code text NOT NULL DEFAULT 'IN',
  birth_year int, is_minor boolean NOT NULL DEFAULT false, exception_id uuid REFERENCES core.seat_exceptions(id),
  name_tokens bytea[] NOT NULL DEFAULT '{}', withdrawn_reason text, withdrawn_at timestamptz,
  row_version int NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (institution_id, academic_year, row_key)
);
CREATE UNIQUE INDEX ux_enrol_exclusive ON core.enrolments (academic_year, key_version, identity_key)
  WHERE concurrency_class = 'EXCLUSIVE' AND status IN ('CLAIMED','ADMITTED');
CREATE UNIQUE INDEX ux_enrol_programme ON core.enrolments (student_id, programme_id, academic_year)
  WHERE status IN ('CLAIMED','ADMITTED');
CREATE INDEX ix_enrol_inst_status ON core.enrolments (institution_id, academic_year, status);
CREATE INDEX ix_enrol_programme ON core.enrolments (programme_id, academic_year);
CREATE INDEX ix_enrol_student ON core.enrolments (student_id);
CREATE INDEX ix_enrol_name_tokens ON core.enrolments USING gin (name_tokens);
CREATE INDEX ix_enrol_keyset ON core.enrolments (institution_id, id);
CREATE TABLE core.enrolment_events (
  id bigserial PRIMARY KEY, enrolment_id uuid NOT NULL REFERENCES core.enrolments(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id), seq int NOT NULL,
  event_type text NOT NULL CHECK (event_type IN ('CLAIM','ADMIT','WITHDRAW','TRANSFER_OUT','TRANSFER_IN','CORRECT','SEAT_RELEASE','IDENTITY_UPGRADE','VERIFICATION_CHANGE')),
  reason_code text, reason_text text, payload jsonb NOT NULL DEFAULT '{}', actor_id uuid, rule_set_id uuid,
  occurred_at timestamptz NOT NULL, effective_date date, UNIQUE (enrolment_id, seq)
);
CREATE TABLE core.seat_ledger_entries (
  id bigserial PRIMARY KEY, seat_bucket_id uuid NOT NULL REFERENCES core.seat_buckets(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  entry_type text NOT NULL CHECK (entry_type IN ('DEBIT','RELEASE','EXCEPTION_GRANT','CAP_CHANGE')),
  delta int NOT NULL, occupied_after int NOT NULL, enrolment_id uuid, exception_id uuid, reason text, actor_id uuid,
  effective_date date NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ledger_bucket ON core.seat_ledger_entries (seat_bucket_id, id);
CREATE TABLE core.enrolment_verification (
  enrolment_id uuid PRIMARY KEY REFERENCES core.enrolments(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  lock1_status text NOT NULL DEFAULT 'SIGNED' CHECK (lock1_status IN ('SIGNED')), lock1_signature text, lock1_kid text,
  lock2_status text NOT NULL CHECK (lock2_status IN ('PENDING','PASSED','FAILED','PROVISIONAL_OK')),
  lock3_status text NOT NULL DEFAULT 'PENDING' CHECK (lock3_status IN ('PENDING','CONFIRMED','EXPIRED','DISPUTED')),
  overall_state text NOT NULL CHECK (overall_state IN ('PENDING','VERIFIED','UNCONFIRMED','DISPUTED','PENDING_FIX')),
  identity_status text NOT NULL, channel text CHECK (channel IN ('REGISTRY_MOBILE','INSTITUTION_MOBILE','GUARDIAN','ASSISTED','DIGILOCKER')),
  channel_trust text CHECK (channel_trust IN ('HIGH','LOW')), deadline_at timestamptz NOT NULL, fix_deadline_at timestamptz,
  confirmed_at timestamptz, reminders_sent int NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_verif_state ON core.enrolment_verification (institution_id, overall_state);
CREATE TABLE core.confirmation_requests (
  id uuid PRIMARY KEY, enrolment_id uuid NOT NULL REFERENCES core.enrolments(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id), channel text NOT NULL, target_masked text NOT NULL,
  otp_hash text NOT NULL, otp_salt text NOT NULL, otp_expires_at timestamptz NOT NULL, attempts int NOT NULL DEFAULT 0,
  resend_count int NOT NULL DEFAULT 1, last_sent_at timestamptz NOT NULL, send_window_start timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'SENT' CHECK (status IN ('SENT','CONFIRMED','EXPIRED','FAILED')),
  for_guardian boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_confreq_enrol ON core.confirmation_requests (enrolment_id);
CREATE TABLE core.claim_attempts (
  id bigserial PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), programme_id uuid, bucket_type text,
  source text NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('CLAIMED','CLAIMED_PENDING_VERIFICATION','CONFLICT','SEAT_LIMIT','DUPLICATE_SAME_INSTITUTION','INVALID','RULE_BLOCKED','CONCURRENT_OK','EXCEPTION_APPROVED')),
  http_status int NOT NULL, conflict_holder_institution_id uuid, identity_key_hash text, request_id text,
  created_at timestamptz NOT NULL
);
CREATE INDEX ix_attempts_inst ON core.claim_attempts (institution_id, created_at);

-- ============================ identity ============================
CREATE TABLE identity.registry_checks (
  id uuid PRIMARY KEY, enrolment_id uuid, institution_id uuid,
  source text NOT NULL CHECK (source IN ('CLAIM','REVALIDATION','CHECK_ONLY','UPLOAD_DRYRUN')),
  status text NOT NULL CHECK (status IN ('FOUND','NOT_FOUND','UNAVAILABLE')),
  name_score numeric(4,3), dob_match text CHECK (dob_match IN ('EXACT','DAY_MONTH_SWAP','YEAR_TYPO','MISMATCH')),
  verdict text NOT NULL, latency_ms int, breaker_state text, checked_at timestamptz NOT NULL
);
CREATE INDEX ix_regcheck_enrol ON identity.registry_checks (enrolment_id);
CREATE TABLE identity.diagnoses (
  id uuid PRIMARY KEY, registry_check_id uuid REFERENCES identity.registry_checks(id), enrolment_id uuid,
  institution_id uuid, status text NOT NULL, field text, message_code text NOT NULL, message text, suggested_fix jsonb NOT NULL DEFAULT '{}',
  deadline_at timestamptz, resolved_at timestamptz
);
CREATE INDEX ix_diag_enrol ON identity.diagnoses (enrolment_id);

-- ============================ pii (needs enrolments' institution) ============================
CREATE TABLE pii.pii_records (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id),
  institution_id uuid NOT NULL REFERENCES ref.institutions(id), data_key_id uuid NOT NULL REFERENCES pii.data_keys(id),
  version int NOT NULL DEFAULT 1, nonce bytea, ciphertext bytea, erased_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (student_id, institution_id, version),
  CHECK ((ciphertext IS NULL) = (erased_at IS NOT NULL))
);

-- ============================ rules.findings ============================
CREATE TABLE rules.findings (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL,
  rule_id uuid REFERENCES rules.rules(id), rule_code text NOT NULL, rule_set_id uuid REFERENCES rules.rule_sets(id),
  enrolment_id uuid REFERENCES core.enrolments(id), severity text NOT NULL CHECK (severity IN ('Blocker','Error','Warning','Info')),
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED','WAIVED')),
  message text NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', suggested_fix jsonb NOT NULL DEFAULT '{}',
  acknowledged_by uuid, waived_by uuid, waiver_reason_code text, created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE INDEX ix_findings_inst ON rules.findings (institution_id, academic_year, status);

-- ============================ audit ============================
CREATE TABLE audit.audit_log (
  id bigserial PRIMARY KEY, stream_id text NOT NULL DEFAULT 'main', seq bigint, occurred_at timestamptz NOT NULL,
  actor_id uuid, actor_role text, action text NOT NULL, entity_type text, entity_id text, institution_id uuid,
  case_id uuid, justification text, request_id text, ip_hash text, payload jsonb NOT NULL DEFAULT '{}',
  prev_hash text, entry_hash text, checkpoint_id uuid, UNIQUE (stream_id, seq)
);
CREATE INDEX ix_audit_unsealed ON audit.audit_log (id) WHERE seq IS NULL;
CREATE INDEX ix_audit_inst ON audit.audit_log (institution_id, id);
CREATE TABLE audit.audit_checkpoints (
  id uuid PRIMARY KEY, stream_id text NOT NULL, from_seq bigint NOT NULL, to_seq bigint NOT NULL, root_hash text NOT NULL,
  signed_jwt text NOT NULL, kid text NOT NULL, worm_path text, created_at timestamptz NOT NULL DEFAULT now()
);
-- A sealed audit row is immutable; an unsealed one may only receive seq/prev_hash/entry_hash.
CREATE OR REPLACE FUNCTION audit.guard_audit_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.seq IS NOT NULL THEN RAISE EXCEPTION 'audit_log row % is sealed and immutable', OLD.id; END IF;
  IF (NEW.occurred_at, NEW.actor_id, NEW.actor_role, NEW.action, NEW.entity_type, NEW.entity_id, NEW.institution_id,
      NEW.case_id, NEW.justification, NEW.request_id, NEW.ip_hash, NEW.payload, NEW.stream_id)
     IS DISTINCT FROM
     (OLD.occurred_at, OLD.actor_id, OLD.actor_role, OLD.action, OLD.entity_type, OLD.entity_id, OLD.institution_id,
      OLD.case_id, OLD.justification, OLD.request_id, OLD.ip_hash, OLD.payload, OLD.stream_id)
  THEN RAISE EXCEPTION 'audit_log content columns are immutable'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_audit_guard BEFORE UPDATE ON audit.audit_log FOR EACH ROW EXECUTE FUNCTION audit.guard_audit_update();

-- ============================ ops ============================
CREATE TABLE ops.outbox_events (
  id bigserial PRIMARY KEY, topic text NOT NULL, aggregate_type text NOT NULL, aggregate_id text NOT NULL,
  institution_id uuid, payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz, attempts int NOT NULL DEFAULT 0
);
CREATE INDEX ix_outbox_pending ON ops.outbox_events (id) WHERE published_at IS NULL;
CREATE TABLE ops.jobs (
  id uuid PRIMARY KEY, queue text NOT NULL, type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','DEAD')),
  priority int NOT NULL DEFAULT 5, run_at timestamptz NOT NULL DEFAULT now(), attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 5, lease_expires_at timestamptz, locked_by text, result jsonb, last_error text,
  institution_id uuid, dedupe_key text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX ix_jobs_pick ON ops.jobs (queue, status, run_at);
CREATE UNIQUE INDEX ux_jobs_dedupe ON ops.jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('QUEUED','RUNNING');
CREATE TABLE ops.schedules (
  name text PRIMARY KEY, queue text NOT NULL, job_type text NOT NULL, interval_seconds int NOT NULL,
  next_run_at timestamptz NOT NULL, last_run_at timestamptz, enabled boolean NOT NULL DEFAULT true, last_status text
);
CREATE TABLE ops.notifications (
  id uuid PRIMARY KEY, recipient_user_id uuid, recipient_kind text NOT NULL,
  channel text NOT NULL CHECK (channel IN ('SMS','EMAIL','INAPP')), template_code text NOT NULL, language text NOT NULL DEFAULT 'en',
  params jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','SENT','FAILED','FALLBACK')),
  attempts int NOT NULL DEFAULT 0, provider_ref text, institution_id uuid, enrolment_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz, read_at timestamptz
);
CREATE INDEX ix_notif_user ON ops.notifications (recipient_user_id, created_at);
CREATE INDEX ix_notif_inst ON ops.notifications (institution_id, created_at);
CREATE TABLE ops.system_config (key text PRIMARY KEY, value jsonb NOT NULL, updated_by uuid, updated_at timestamptz NOT NULL DEFAULT now());

-- ============================ agg ============================
CREATE TABLE agg.seat_utilisation (
  programme_id uuid NOT NULL, academic_year text NOT NULL, bucket_type text NOT NULL, institution_id uuid NOT NULL,
  state text, district text, region text, institution_type text, level text, programme_name text, institution_name text,
  cap int NOT NULL, approved_extra int NOT NULL, occupied int NOT NULL, cumulative_admitted int NOT NULL,
  pct numeric(6,2) NOT NULL, as_of timestamptz NOT NULL, PRIMARY KEY (programme_id, academic_year, bucket_type)
);
CREATE INDEX ix_agg_util_inst ON agg.seat_utilisation (institution_id, academic_year);
CREATE TABLE agg.verification_funnel (
  institution_id uuid NOT NULL, academic_year text NOT NULL, total int NOT NULL DEFAULT 0, pending int NOT NULL DEFAULT 0,
  verified int NOT NULL DEFAULT 0, unconfirmed int NOT NULL DEFAULT 0, disputed int NOT NULL DEFAULT 0, pending_fix int NOT NULL DEFAULT 0,
  pending_registry int NOT NULL DEFAULT 0, provisional int NOT NULL DEFAULT 0, low_trust_channel int NOT NULL DEFAULT 0,
  as_of timestamptz NOT NULL, PRIMARY KEY (institution_id, academic_year)
);
CREATE TABLE agg.findings_summary (
  institution_id uuid NOT NULL, academic_year text NOT NULL, severity text NOT NULL, status text NOT NULL, count int NOT NULL,
  PRIMARY KEY (institution_id, academic_year, severity, status)
);
CREATE TABLE agg.claim_daily (
  day date NOT NULL, institution_id uuid NOT NULL, claims int NOT NULL DEFAULT 0, conflicts int NOT NULL DEFAULT 0,
  seat_limit int NOT NULL DEFAULT 0, invalid int NOT NULL DEFAULT 0, duplicates_same_inst int NOT NULL DEFAULT 0,
  PRIMARY KEY (day, institution_id)
);
CREATE TABLE agg.projector_state (name text PRIMARY KEY, last_outbox_id bigint NOT NULL DEFAULT 0, as_of timestamptz);

-- ============================ mockext (mock-services only) ============================
CREATE TABLE mockext.registry_people (
  apaar_id text PRIMARY KEY, status text NOT NULL DEFAULT 'ACTIVE', name text NOT NULL, name_native text, dob date NOT NULL,
  mobile text NOT NULL, is_spare boolean NOT NULL DEFAULT false
);
CREATE TABLE mockext.registry_control (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1), mode text NOT NULL DEFAULT 'UP' CHECK (mode IN ('UP','DOWN','SLOW')),
  latency_ms int NOT NULL DEFAULT 40, error_rate numeric NOT NULL DEFAULT 0
);
INSERT INTO mockext.registry_control DEFAULT VALUES;
CREATE TABLE mockext.sms_messages (
  id bigserial PRIMARY KEY, recipient_masked text NOT NULL, recipient_hash text, template_code text NOT NULL,
  language text NOT NULL DEFAULT 'en', body text NOT NULL, otp text, created_at timestamptz NOT NULL DEFAULT now()
);

-- ============================ seed (ground truth, owner only) ============================
CREATE TABLE seed.ground_truth (
  id bigserial PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('DUPLICATE_TRUE','TRANSFER','FT_PLUS_ODL','EXCEPTION','MISMATCH_TYPE','GHOST_RING','OVER_ADMISSION','STOLEN_IDENTITY')),
  ref_a text NOT NULL, ref_b text, details jsonb NOT NULL DEFAULT '{}'
);

-- ============================ RLS (tables carrying institution_id) ============================
DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT table_schema AS s, table_name AS n FROM information_schema.columns
    WHERE column_name = 'institution_id' AND table_schema IN ('iam','pii','core','identity','rules','ops','audit')
      AND table_name IN (SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', t.s, t.n);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', t.s, t.n);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I USING (ctx.visible(institution_id)) WITH CHECK (ctx.visible(institution_id))', t.s, t.n);
  END LOOP;
END $$;
-- Students may read their own enrolment/verification rows across institutions.
CREATE POLICY student_own ON core.enrolments FOR SELECT USING (ctx.visible(NULL, student_id));
CREATE POLICY student_own_pii ON pii.pii_records FOR SELECT USING (ctx.visible(NULL, student_id));
-- Tables with a nullable institution_id (users, api_clients, jobs, notifications...) must also allow NULL-tenant rows.
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT * FROM (VALUES ('iam','users'),('iam','api_clients'),('ops','jobs'),('ops','notifications'),('ops','outbox_events'),
                                  ('audit','audit_log'),('identity','registry_checks'),('identity','diagnoses')) AS v(s, n)
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', t.s, t.n);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I USING (institution_id IS NULL OR ctx.visible(institution_id)) WITH CHECK (institution_id IS NULL OR ctx.visible(institution_id))', t.s, t.n);
  END LOOP;
END $$;
-- iam.users: a user can always read their own row (login/session resolution runs under SYSTEM scope).
CREATE POLICY self_row ON iam.users USING (id = nullif(current_setting('app.actor_id', true), '')::uuid);

-- ============================ grants ============================
GRANT USAGE ON SCHEMA ctx, ref, iam, pii, core, identity, rules, audit, cred, ops, agg TO app_runtime;
GRANT EXECUTE ON FUNCTION ctx.visible(uuid, uuid) TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ref, iam, pii, core, identity, rules, audit, cred, ops, agg TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, audit, ops TO app_runtime;
-- History tables: insert/select only (REQ-AUD-07). audit_log additionally may UPDATE (seal columns, guarded by trigger).
REVOKE UPDATE, DELETE ON core.enrolment_events, core.seat_ledger_entries, audit.audit_checkpoints FROM app_runtime;
REVOKE DELETE ON audit.audit_log FROM app_runtime;
-- Private signing keys and ground truth are not reachable from ordinary request code paths.
REVOKE ALL ON cred.signing_keys FROM app_runtime;
GRANT SELECT (id, kid, purpose, institution_id, alg, public_jwk, status, created_at) ON cred.signing_keys TO app_runtime;
-- mockext and seed stay owner-only.
