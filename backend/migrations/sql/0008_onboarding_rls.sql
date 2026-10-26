-- Onboarding requests carry an institution_id, so they follow the tenant rule: the applicant path and AICTE run in NATIONAL/SYSTEM scope,
-- an institution could only ever see its own row.
ALTER TABLE iam.onboarding_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam.onboarding_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_or_national ON iam.onboarding_requests
  USING (current_setting('app.scope', true) IN ('NATIONAL','SYSTEM') OR ctx.visible(institution_id))
  WITH CHECK (current_setting('app.scope', true) IN ('NATIONAL','SYSTEM') OR ctx.visible(institution_id));
