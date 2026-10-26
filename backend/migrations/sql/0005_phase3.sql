-- Phase 3: bulk upload (ingest), submissions + Merkle commitments, duplicate/transfer engine (matching), window extensions.
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS matching;

-- ============================ ingest ============================
CREATE TABLE ingest.upload_jobs (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL,
  created_by uuid NOT NULL REFERENCES iam.users(id), filename text NOT NULL, storage_key text, size_bytes bigint NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'UPLOADING' CHECK (status IN ('UPLOADING','SCANNING','QUEUED','PARSING','VALIDATING','READY_TO_COMMIT','COMMITTING','COMPLETED','PARTIAL','FAILED','CANCELLED')),
  scan_result jsonb NOT NULL DEFAULT '{}', total_rows int NOT NULL DEFAULT 0, accepted int NOT NULL DEFAULT 0, held int NOT NULL DEFAULT 0,
  rejected int NOT NULL DEFAULT 0, noop int NOT NULL DEFAULT 0, committed int NOT NULL DEFAULT 0, failed int NOT NULL DEFAULT 0,
  committed_by uuid REFERENCES iam.users(id), committed_at timestamptz, failure_reason text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_commit_not_uploader CHECK (committed_by IS NULL OR committed_by <> created_by)
);
CREATE INDEX ix_upload_inst ON ingest.upload_jobs (institution_id, created_at DESC);
CREATE TABLE ingest.upload_chunks (
  id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES ingest.upload_jobs(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  phase text NOT NULL CHECK (phase IN ('VALIDATE','COMMIT')), chunk_no int NOT NULL, row_start int NOT NULL, row_end int NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','DONE','FAILED')), attempts int NOT NULL DEFAULT 0,
  UNIQUE (job_id, phase, chunk_no)
);
CREATE TABLE ingest.staged_rows (
  id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES ingest.upload_jobs(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  row_no int NOT NULL, row_key text NOT NULL,
  outcome text NOT NULL DEFAULT 'PENDING' CHECK (outcome IN ('PENDING','ACCEPTED','HELD','REJECTED','NOOP_DUPLICATE','COMMITTED','COMMIT_FAILED')),
  payload_nonce bytea NOT NULL, payload_ct bytea NOT NULL, errors jsonb NOT NULL DEFAULT '[]', diff jsonb NOT NULL DEFAULT '{}',
  claim_outcome text, enrolment_id uuid, UNIQUE (job_id, row_no)
);
CREATE INDEX ix_staged_outcome ON ingest.staged_rows (job_id, outcome);

ALTER TABLE rules.findings ADD COLUMN IF NOT EXISTS submission_id uuid;
ALTER TABLE rules.findings ADD COLUMN IF NOT EXISTS waiver_note text;

CREATE TABLE ref.window_extensions (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), window_id uuid NOT NULL REFERENCES ref.submission_windows(id),
  requested_by uuid NOT NULL REFERENCES iam.users(id), new_closes_at timestamptz NOT NULL, reason text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED')), decided_by uuid REFERENCES iam.users(id),
  decided_at timestamptz, decision_reason text, created_at timestamptz NOT NULL DEFAULT now(), row_version int NOT NULL DEFAULT 1
);

CREATE TABLE ingest.submissions (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL,
  window_id uuid NOT NULL REFERENCES ref.submission_windows(id),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','VALIDATED','SUBMITTED','ACCEPTED','UNDER_REVIEW','CORRECTED','LOCKED')),
  declared jsonb NOT NULL DEFAULT '{}', submitted_by uuid REFERENCES iam.users(id), submitted_at timestamptz,
  approved_by uuid REFERENCES iam.users(id), approved_at timestamptz, late_flag boolean NOT NULL DEFAULT false,
  rule_set_id uuid REFERENCES rules.rule_sets(id), review_notes text, locked_at timestamptz, created_by uuid REFERENCES iam.users(id),
  created_at timestamptz NOT NULL DEFAULT now(), row_version int NOT NULL DEFAULT 1,
  UNIQUE (institution_id, academic_year, window_id),
  CONSTRAINT ck_approver_not_submitter CHECK (approved_by IS NULL OR approved_by <> submitted_by)
);
CREATE TABLE ingest.submission_history (
  id bigserial PRIMARY KEY, submission_id uuid NOT NULL REFERENCES ingest.submissions(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  from_status text, to_status text NOT NULL, actor_id uuid, note text, at timestamptz NOT NULL DEFAULT now()
);

-- ============================ audit: cohort commitments ============================
CREATE TABLE audit.cohort_commitments (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL,
  submission_id uuid NOT NULL REFERENCES ingest.submissions(id), cutoff_at timestamptz NOT NULL, version int NOT NULL,
  merkle_root text NOT NULL, leaf_count int NOT NULL, serialisation_version int NOT NULL DEFAULT 1,
  institution_signature text NOT NULL, institution_kid text NOT NULL, aicte_countersignature text, aicte_kid text,
  status text NOT NULL DEFAULT 'PENDING_COUNTERSIGN' CHECK (status IN ('PENDING_COUNTERSIGN','COMPLETE','SUPERSEDED')),
  created_by uuid REFERENCES iam.users(id), countersigned_by uuid REFERENCES iam.users(id), created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (institution_id, academic_year, version)
);
CREATE TABLE audit.commitment_leaves (
  commitment_id uuid NOT NULL REFERENCES audit.cohort_commitments(id), leaf_index int NOT NULL, institution_id uuid NOT NULL,
  enrolment_id uuid NOT NULL, leaf_json jsonb NOT NULL, leaf_hash text NOT NULL, PRIMARY KEY (commitment_id, leaf_index)
);
CREATE INDEX ix_leaves_enrol ON audit.commitment_leaves (enrolment_id);

-- ============================ matching ============================
CREATE TABLE matching.match_features (
  enrolment_id uuid PRIMARY KEY REFERENCES core.enrolments(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL,
  k1 bytea NOT NULL, k2 bytea, k3 bytea, bloom_name bytea NOT NULL, bloom_dob bytea NOT NULL, bloom_parent bytea, contact_hashes jsonb NOT NULL DEFAULT '{}',
  built_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_mf_k1 ON matching.match_features (k1); CREATE INDEX ix_mf_k2 ON matching.match_features (k2); CREATE INDEX ix_mf_k3 ON matching.match_features (k3);
CREATE TABLE matching.match_candidates (
  id uuid PRIMARY KEY, enrolment_a uuid NOT NULL REFERENCES core.enrolments(id), enrolment_b uuid NOT NULL REFERENCES core.enrolments(id),
  institution_a uuid NOT NULL, institution_b uuid NOT NULL, score numeric(4,3) NOT NULL, field_scores jsonb NOT NULL,
  timeline_class text NOT NULL CHECK (timeline_class IN ('OVERLAP_FT','SEQUENTIAL','FT_PLUS_ODL','EXCEPTION','SAME_INST_READMIT','TRANSFER')),
  status text NOT NULL CHECK (status IN ('AUTO_LINKED','REVIEW','DISMISSED','CONFIRMED_DUP')), flags text[] NOT NULL DEFAULT '{}',
  decided_by uuid REFERENCES iam.users(id), decided_at timestamptz, decision_reason text, case_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (enrolment_a < enrolment_b)
);
CREATE UNIQUE INDEX ux_candidate_pair ON matching.match_candidates (enrolment_a, enrolment_b);
CREATE INDEX ix_candidate_status ON matching.match_candidates (status, score DESC);

-- ============================ core.transfers ============================
CREATE TABLE core.transfers (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id), out_enrolment_id uuid REFERENCES core.enrolments(id),
  in_enrolment_id uuid REFERENCES core.enrolments(id), out_institution_id uuid REFERENCES ref.institutions(id), in_institution_id uuid REFERENCES ref.institutions(id),
  effective_date_out date, status text NOT NULL CHECK (status IN ('OUT_RECORDED','MATCHED','ORPHAN_LEAD','DISMISSED')),
  created_at timestamptz NOT NULL DEFAULT now(), orphan_after timestamptz
);
CREATE INDEX ix_transfers_out ON core.transfers (out_institution_id, status);
CREATE INDEX ix_transfers_in ON core.transfers (in_institution_id);

-- ============================ RLS for tenant tables ============================
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ingest.upload_jobs','ingest.upload_chunks','ingest.staged_rows','ingest.submissions','ingest.submission_history','ref.window_extensions',
                           'audit.cohort_commitments','audit.commitment_leaves','matching.match_features'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s USING (ctx.visible(institution_id)) WITH CHECK (ctx.visible(institution_id))', t);
  END LOOP;
END $$;
-- transfers and candidates span two institutions: visible to either party (or system scope)
ALTER TABLE core.transfers ENABLE ROW LEVEL SECURITY; ALTER TABLE core.transfers FORCE ROW LEVEL SECURITY;
CREATE POLICY parties ON core.transfers USING (ctx.visible(out_institution_id) OR ctx.visible(in_institution_id));
ALTER TABLE matching.match_candidates ENABLE ROW LEVEL SECURITY; ALTER TABLE matching.match_candidates FORCE ROW LEVEL SECURITY;
CREATE POLICY system_only ON matching.match_candidates USING (current_setting('app.scope', true) IN ('NATIONAL','REGION','STATE','SYSTEM'));

GRANT USAGE ON SCHEMA ingest, matching TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ingest, matching, ref, audit, core TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ingest, audit, core TO app_runtime;
REVOKE UPDATE, DELETE ON audit.commitment_leaves FROM app_runtime;
REVOKE UPDATE, DELETE ON ingest.submission_history FROM app_runtime;
REVOKE UPDATE, DELETE ON core.enrolment_events, core.seat_ledger_entries, audit.audit_checkpoints FROM app_runtime;
REVOKE DELETE ON audit.audit_log FROM app_runtime;
REVOKE ALL ON cred.signing_keys FROM app_runtime;
GRANT SELECT (id, kid, purpose, institution_id, alg, public_jwk, status, created_at) ON cred.signing_keys TO app_runtime;

-- The scope is switched inside the body (and restored before returning) instead of with a function-level SET clause:
-- hosted Supabase (supautils) refuses custom parameters in function SET clauses for non-superusers.
-- Auto-match an incoming claim to an open transfer-out of the same student at ANOTHER institution (cross-tenant, identifiers only).
CREATE OR REPLACE FUNCTION core.match_transfer(p_student uuid, p_in_enrolment uuid, p_in_inst uuid) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, ref, pg_temp AS $$
DECLARE prev text := coalesce(current_setting('app.scope', true), ''); n int;
BEGIN
  PERFORM set_config('app.scope', 'SYSTEM', true);
  WITH u AS (
    UPDATE core.transfers SET in_enrolment_id = p_in_enrolment, in_institution_id = p_in_inst, status = 'MATCHED'
    WHERE student_id = p_student AND status IN ('OUT_RECORDED','ORPHAN_LEAD') AND out_institution_id <> p_in_inst RETURNING 1)
  SELECT count(*)::int INTO n FROM u;
  PERFORM set_config('app.scope', prev, true);
  RETURN n;
END
$$;
REVOKE ALL ON FUNCTION core.match_transfer(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.match_transfer(uuid, uuid, uuid) TO app_runtime;

ALTER TABLE seed.ground_truth DROP CONSTRAINT IF EXISTS ground_truth_kind_check;
ALTER TABLE seed.ground_truth ADD CONSTRAINT ground_truth_kind_check CHECK (kind IN
  ('DUPLICATE_TRUE','TRANSFER','FT_PLUS_ODL','EXCEPTION','SEQUENTIAL','ORPHAN','MISMATCH_TYPE','GHOST_RING','OVER_ADMISSION','STOLEN_IDENTITY'));
