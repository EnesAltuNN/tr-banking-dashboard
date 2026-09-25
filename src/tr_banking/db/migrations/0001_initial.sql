-- Tables shared by all modules. Same model as db/schema.sql (SQLite), with native Postgres types.

CREATE TABLE series (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source    TEXT NOT NULL,  -- evds | bddk | bkm | bank_site (validated in config.py)
    code      TEXT NOT NULL,  -- source-specific code, e.g. TP.HPBITABLO6.3
    name_tr   TEXT NOT NULL,
    name_en   TEXT NOT NULL,
    unit      TEXT NOT NULL,  -- e.g. 'thousand TRY', 'million TRY', '%'
    frequency TEXT NOT NULL,  -- daily | weekly | monthly
    module    TEXT NOT NULL,  -- credit | cards | rates
    UNIQUE (source, code)
);

CREATE TABLE observations (
    series_id  BIGINT           NOT NULL REFERENCES series (id),
    date       DATE             NOT NULL,  -- end of the period
    value      DOUBLE PRECISION NOT NULL,
    fetched_at TIMESTAMPTZ      NOT NULL,  -- when the fetch that wrote this row ran
    PRIMARY KEY (series_id, date)
);

-- Supabase serves the public schema over its REST API (PostgREST) as the `anon` and
-- `authenticated` roles. Row Level Security with no policy for them returns no rows, and
-- revoking their privileges refuses the request outright. The table owner (the fetch job)
-- bypasses RLS. The roles only exist on Supabase, hence the check.
ALTER TABLE series ENABLE ROW LEVEL SECURITY;
ALTER TABLE observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'REVOKE ALL ON series, observations, schema_migrations FROM %I', api_role
            );
        END IF;
    END LOOP;
END
$$;
