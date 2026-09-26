-- Least-privilege role for the scheduled fetch job: read and upsert data, nothing else.
-- No DELETE or TRUNCATE, no DDL. Schema changes are applied by hand with
-- `tr-banking db migrate` as the table owner; the job only checks that none is pending.
-- Created without a password (NOLOGIN). Enable it once in the Supabase SQL editor with
-- sql/enable_fetch_writer.sql, so the password never enters this repository.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fetch_writer') THEN
        CREATE ROLE fetch_writer NOLOGIN;
    END IF;
END
$$;

-- A runaway statement cannot hang the job (the workflow itself times out after 20 minutes).
ALTER ROLE fetch_writer SET statement_timeout = '60s';

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO fetch_writer', current_schema());
END
$$;

-- Upserts need INSERT + UPDATE; SELECT is needed for ON CONFLICT and for reading series ids.
GRANT SELECT, INSERT, UPDATE ON series, observations TO fetch_writer;
-- Read-only, so the job can fail loudly when a migration is pending.
GRANT SELECT ON schema_migrations TO fetch_writer;

-- RLS is on for all three tables, so every allowed command needs a policy for this role.
CREATE POLICY fetch_writer_select ON series FOR SELECT TO fetch_writer USING (true);
CREATE POLICY fetch_writer_insert ON series FOR INSERT TO fetch_writer WITH CHECK (true);
CREATE POLICY fetch_writer_update ON series FOR UPDATE TO fetch_writer
    USING (true) WITH CHECK (true);

CREATE POLICY fetch_writer_select ON observations FOR SELECT TO fetch_writer USING (true);
CREATE POLICY fetch_writer_insert ON observations FOR INSERT TO fetch_writer WITH CHECK (true);
CREATE POLICY fetch_writer_update ON observations FOR UPDATE TO fetch_writer
    USING (true) WITH CHECK (true);

CREATE POLICY fetch_writer_select ON schema_migrations FOR SELECT TO fetch_writer USING (true);
