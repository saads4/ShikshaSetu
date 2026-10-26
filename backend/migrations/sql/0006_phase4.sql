-- Phase 4: Trust Index, anomaly alerts, graph clusters, case workflow additions, appeals, elevation grants, report jobs.
CREATE TABLE intel.trust_scores (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), academic_year text NOT NULL, score numeric(4,1) NOT NULL,
  band text NOT NULL CHECK (band IN ('LOW_PRIORITY','WATCH','REVIEW_PRIORITY')), components jsonb NOT NULL, config_version text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(), computed_date date NOT NULL DEFAULT current_date,
  UNIQUE (institution_id, academic_year, computed_date)
);
CREATE TABLE intel.anomaly_alerts (
  id uuid PRIMARY KEY, institution_id uuid NOT NULL REFERENCES ref.institutions(id), layer text NOT NULL CHECK (layer IN ('A','B','C','D','G')),
  type text NOT NULL, severity text NOT NULL CHECK (severity IN ('HIGH','MEDIUM','LOW')),
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_CASE','DISMISSED','CLOSED')), academic_year text NOT NULL,
  baseline jsonb NOT NULL DEFAULT '{}', deviation numeric, evidence jsonb NOT NULL DEFAULT '{}', explanation jsonb NOT NULL DEFAULT '{}',
  suggested_action text NOT NULL, model_version text NOT NULL, case_id uuid REFERENCES intel.cases(id), dedupe_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_alert_open ON intel.anomaly_alerts (dedupe_key) WHERE status IN ('OPEN','IN_CASE');
CREATE INDEX ix_alert_inst ON intel.anomaly_alerts (institution_id, status);
CREATE TABLE intel.graph_clusters (
  id uuid PRIMARY KEY, run_date date NOT NULL, cluster_key text NOT NULL, institution_ids uuid[] NOT NULL, student_count int NOT NULL,
  shared_contact_types text[] NOT NULL, score numeric NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE intel.cases ADD COLUMN IF NOT EXISTS response_text text;
ALTER TABLE intel.cases ADD COLUMN IF NOT EXISTS responded_at timestamptz;
ALTER TABLE intel.cases ADD COLUMN IF NOT EXISTS alert_id uuid;
ALTER TABLE intel.cases ADD COLUMN IF NOT EXISTS release_enrolment_id uuid;
CREATE TABLE intel.appeals (
  id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES intel.cases(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  filed_by uuid NOT NULL REFERENCES iam.users(id), grounds text NOT NULL, status text NOT NULL DEFAULT 'FILED' CHECK (status IN ('FILED','UPHELD','OVERTURNED')),
  decided_by uuid REFERENCES iam.users(id), decided_at timestamptz, decision_notes text, created_at timestamptz NOT NULL DEFAULT now()
);
-- The appeal decider must not be the case owner (REQ-INT-11).
CREATE OR REPLACE FUNCTION intel.guard_appeal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.decided_by IS NOT NULL AND NEW.decided_by = (SELECT owner_id FROM intel.cases WHERE id = NEW.case_id) THEN
    RAISE EXCEPTION 'appeal decider must differ from the case owner'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_appeal_guard BEFORE INSERT OR UPDATE ON intel.appeals FOR EACH ROW EXECUTE FUNCTION intel.guard_appeal();

CREATE TABLE pii.access_grants (
  id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES iam.users(id), case_id uuid NOT NULL REFERENCES intel.cases(id), justification text NOT NULL,
  scope_type text NOT NULL DEFAULT 'CASE' CHECK (scope_type IN ('CASE','INSTITUTION')), granted_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  revoked_at timestamptz, records_revealed int NOT NULL DEFAULT 0,
  CHECK (expires_at > granted_at AND expires_at <= granted_at + interval '4 hours' + interval '1 second')
);
CREATE INDEX ix_grants_user ON pii.access_grants (user_id, expires_at);

CREATE TABLE ops.report_jobs (
  id uuid PRIMARY KEY, requested_by uuid NOT NULL REFERENCES iam.users(id), institution_id uuid REFERENCES ref.institutions(id), type text NOT NULL, params jsonb NOT NULL DEFAULT '{}',
  format text NOT NULL CHECK (format IN ('CSV','XLSX','PDF')), status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED')),
  ref text UNIQUE NOT NULL, title text, file_key text, sha256 text, rule_set_version int, error text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['intel.trust_scores','intel.anomaly_alerts','intel.appeals'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s USING (ctx.visible(institution_id)) WITH CHECK (ctx.visible(institution_id))', t);
  END LOOP;
  ALTER TABLE ops.report_jobs ENABLE ROW LEVEL SECURITY; ALTER TABLE ops.report_jobs FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON ops.report_jobs USING (institution_id IS NULL OR ctx.visible(institution_id)) WITH CHECK (institution_id IS NULL OR ctx.visible(institution_id));
END $$;

-- The scope is switched inside the body (and restored before returning) instead of with a function-level SET clause:
-- hosted Supabase (supautils) refuses custom parameters in function SET clauses for non-superusers.
-- Public report verification (title, time, hash, rule version only) without exposing the table to anonymous sessions.
CREATE OR REPLACE FUNCTION ops.report_public(p_ref text) RETURNS TABLE (title text, generated_at timestamptz, sha256 text, rule_set_version int, format text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE prev text := coalesce(current_setting('app.scope', true), '');
BEGIN
  PERFORM set_config('app.scope', 'SYSTEM', true);
  RETURN QUERY SELECT r.title, r.finished_at, r.sha256, r.rule_set_version, r.format FROM ops.report_jobs r WHERE r.ref = p_ref AND r.status = 'DONE';
  PERFORM set_config('app.scope', prev, true);
END
$$;
REVOKE ALL ON FUNCTION ops.report_public(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.report_public(text) TO app_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intel, pii, ops TO app_runtime;
REVOKE UPDATE, DELETE ON intel.case_events FROM app_runtime;
REVOKE UPDATE, DELETE ON core.enrolment_events, core.seat_ledger_entries, audit.audit_checkpoints FROM app_runtime;
REVOKE DELETE ON audit.audit_log FROM app_runtime;
REVOKE UPDATE, DELETE ON audit.commitment_leaves, ingest.submission_history FROM app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel, ops TO app_runtime;
