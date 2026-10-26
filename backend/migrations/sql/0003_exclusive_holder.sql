-- The scope is switched inside the body (and restored before returning) instead of with a function-level SET clause:
-- hosted Supabase (supautils) refuses custom parameters in function SET clauses for non-superusers.
-- Cross-tenant exclusivity lookup. Tenant RLS hides other institutions' enrolments, but the gate must know whether
-- another institution holds a live exclusive claim. This SECURITY DEFINER function returns identifiers only (no PII).
CREATE OR REPLACE FUNCTION core.exclusive_holder(p_year text, p_key_version int, p_key bytea)
RETURNS TABLE (enrolment_id uuid, institution_id uuid, enrolment_ref text, level text, mode text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, ref, pg_temp AS $$
DECLARE prev text := coalesce(current_setting('app.scope', true), '');
BEGIN
  PERFORM set_config('app.scope', 'SYSTEM', true);
  RETURN QUERY
    SELECT e.id, e.institution_id, e.enrolment_ref, pr.level, pr.mode
    FROM core.enrolments e JOIN ref.programmes pr ON pr.id = e.programme_id
    WHERE e.academic_year = p_year AND e.key_version = p_key_version AND e.identity_key = p_key
      AND e.concurrency_class = 'EXCLUSIVE' AND e.status IN ('CLAIMED','ADMITTED')
    LIMIT 1;
  PERFORM set_config('app.scope', prev, true);
END
$$;
REVOKE ALL ON FUNCTION core.exclusive_holder(text, int, bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.exclusive_holder(text, int, bytea) TO app_runtime;
