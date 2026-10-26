-- Students may read the verification and confirmation rows of their own enrolments (never other tenants' rows).
CREATE POLICY student_own ON core.enrolment_verification FOR SELECT
  USING (EXISTS (SELECT 1 FROM core.enrolments e WHERE e.id = enrolment_id AND ctx.visible(NULL, e.student_id)));
CREATE POLICY student_own ON core.confirmation_requests FOR SELECT
  USING (EXISTS (SELECT 1 FROM core.enrolments e WHERE e.id = enrolment_id AND ctx.visible(NULL, e.student_id)));
