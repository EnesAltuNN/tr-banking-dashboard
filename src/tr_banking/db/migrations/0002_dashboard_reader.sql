-- Read-only role for the dashboard: SELECT on the data tables and nothing else.
-- Created without a password (NOLOGIN). Enable it once in the Supabase SQL editor with
-- sql/enable_dashboard_reader.sql, so the password never enters this repository.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
        CREATE ROLE dashboard_reader NOLOGIN;
    END IF;
END
$$;

-- Defense in depth: every session of this role is read-only, even if a grant slips through.
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;

-- The schema holding the tables (public on Supabase; a throwaway schema in tests).
DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO dashboard_reader', current_schema());
END
$$;
GRANT SELECT ON series, observations TO dashboard_reader;

-- RLS is on (0001), so SELECT alone would return no rows. These policies let this role, and
-- only this role, read every row; anon and authenticated still get nothing.
CREATE POLICY dashboard_reader_select ON series
    FOR SELECT TO dashboard_reader USING (true);
CREATE POLICY dashboard_reader_select ON observations
    FOR SELECT TO dashboard_reader USING (true);
