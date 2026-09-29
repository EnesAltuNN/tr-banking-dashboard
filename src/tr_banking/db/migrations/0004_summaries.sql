-- Weekly AI-written summaries shown on the dashboard. One row per data week (the Friday of the
-- newest weekly data). The numbers come from Python; `input` keeps the exact JSON the model
-- was given, so every sentence can be checked against it. Same model as db/schema.sql.

CREATE TABLE summaries (
    data_date  DATE        PRIMARY KEY,  -- newest weekly data the summary covers
    created_at TIMESTAMPTZ NOT NULL,
    model      TEXT        NOT NULL,     -- the model that wrote it (a fallback may differ)
    input      TEXT        NOT NULL,     -- JSON facts computed in Python
    text_tr    TEXT        NOT NULL,
    text_en    TEXT        NOT NULL
);

-- Same protection as the other tables (0001): RLS on, Supabase's API roles get nothing.
ALTER TABLE summaries ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON summaries FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;

-- The scheduled job writes it (upsert: SELECT for ON CONFLICT, INSERT, UPDATE; no DELETE).
GRANT SELECT, INSERT, UPDATE ON summaries TO fetch_writer;
CREATE POLICY fetch_writer_select ON summaries FOR SELECT TO fetch_writer USING (true);
CREATE POLICY fetch_writer_insert ON summaries FOR INSERT TO fetch_writer WITH CHECK (true);
CREATE POLICY fetch_writer_update ON summaries FOR UPDATE TO fetch_writer
    USING (true) WITH CHECK (true);

-- The dashboard only reads it.
GRANT SELECT ON summaries TO dashboard_reader;
CREATE POLICY dashboard_reader_select ON summaries
    FOR SELECT TO dashboard_reader USING (true);
