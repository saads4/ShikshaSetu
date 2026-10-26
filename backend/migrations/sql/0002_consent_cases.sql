-- Guardian links (consent schema) and cases (intel schema) needed by Triple-Lock (minor path, student dispute).
CREATE SCHEMA IF NOT EXISTS consent;
CREATE SCHEMA IF NOT EXISTS intel;

CREATE TABLE consent.guardian_links (
  id uuid PRIMARY KEY, student_id uuid NOT NULL REFERENCES core.students(id),
  guardian_user_id uuid NOT NULL REFERENCES iam.users(id), relationship text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','VERIFIED','REVOKED')), verified_at timestamptz,
  UNIQUE (student_id, guardian_user_id)
);

CREATE SEQUENCE intel.case_no_seq START 1;
CREATE TABLE intel.cases (
  id uuid PRIMARY KEY, case_no text UNIQUE NOT NULL, institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  type text NOT NULL CHECK (type IN ('ANOMALY','DUPLICATE','STUDENT_DISPUTE','UNCONFIRMED_SHARE','SEAT_RELEASE','OTHER')),
  title text NOT NULL, owner_id uuid REFERENCES iam.users(id),
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','AWAITING_RESPONSE','UNDER_REVIEW','CLOSED','APPEALED')),
  response_due_at timestamptz, outcome_code text CHECK (outcome_code IN ('CONFIRMED_VIOLATION','NOT_SUBSTANTIATED','DATA_CORRECTED','REFERRED_FOR_INSPECTION','FALSE_POSITIVE')),
  outcome_notes text, evidence_pack jsonb NOT NULL DEFAULT '{}', evidence_sha256 text, opened_by uuid REFERENCES iam.users(id),
  enrolment_id uuid REFERENCES core.enrolments(id), created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
  row_version int NOT NULL DEFAULT 1
);
CREATE TABLE intel.case_events (
  id bigserial PRIMARY KEY, case_id uuid NOT NULL REFERENCES intel.cases(id), institution_id uuid NOT NULL REFERENCES ref.institutions(id),
  actor_id uuid, event_type text NOT NULL, body text, created_at timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['intel.cases','intel.case_events'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s USING (ctx.visible(institution_id)) WITH CHECK (ctx.visible(institution_id))', t);
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA consent, intel TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA consent, intel TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel, consent TO app_runtime;
REVOKE UPDATE, DELETE ON intel.case_events FROM app_runtime;
