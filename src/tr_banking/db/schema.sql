-- Generic time-series storage shared by all modules (EVDS, BDDK, BKM, bank sites).
-- Allowed values for source/frequency/module are validated in Python (config.py), not here,
-- so adding a new source never requires a migration.

CREATE TABLE IF NOT EXISTS series (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,  -- evds | bddk | bkm | bank_site
    code      TEXT NOT NULL,  -- source-specific code, e.g. TP.HPBITABLO6.3
    name_tr   TEXT NOT NULL,
    name_en   TEXT NOT NULL,
    unit      TEXT NOT NULL,  -- e.g. 'thousand TRY', '%'
    frequency TEXT NOT NULL,  -- daily | weekly | monthly
    module    TEXT NOT NULL,  -- credit | cards | rates
    UNIQUE (source, code)
);

CREATE TABLE IF NOT EXISTS observations (
    series_id  INTEGER NOT NULL REFERENCES series (id),
    date       TEXT    NOT NULL,  -- ISO 8601 YYYY-MM-DD, end of the period
    value      REAL    NOT NULL,
    fetched_at TEXT    NOT NULL,  -- ISO 8601 UTC timestamp of the fetch that wrote this row
    -- Composite primary key = UNIQUE (series_id, date) + an index for per-series queries.
    PRIMARY KEY (series_id, date)
);
