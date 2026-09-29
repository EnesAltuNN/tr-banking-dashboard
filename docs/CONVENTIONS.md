# Conventions and technical notes

Read the section you need before changing code; `CLAUDE.md` links here. Update this file when
a convention changes.

## Deployment facts


- **Streamlit Community Cloud:**
  - It reads `uv.lock` first and installs with `uv sync` (supported since 2024-11), so no
    `requirements.txt` is needed.
  - `streamlit run` promotes root-level string secrets to `os.environ` at bootstrap, before
    the script runs.
  - Its secret is TOML with a quoted value:
    `DATABASE_URL = "postgresql://dashboard_reader.<ref>:<pw>@<pooler-host>:5432/postgres"`.
    GitHub Secrets take the bare value.
- **GitHub Actions secrets:**
  - `EVDS_API_KEY`.
  - `DATABASE_URL`, the **`fetch_writer`** session pooler string
    (`postgresql://fetch_writer.<ref>:<pw>@<pooler-host>:5432/postgres`). It never holds the
    owner string.
  - `ANTHROPIC_API_KEY` (optional): the Claude API key for the weekly summary step.
- **GitHub CLI:**
  - Installed at `C:\Program Files\GitHub CLI\gh.exe` and logged in as EnesAltuNN. It is not
    on the PATH of old terminals, so call it by full path.
  - Use it to trigger runs (`gh workflow run fetch.yml`) and to read logs and artifacts.
  - Never use it to read or set secret values.
- **Local `.env`:**
  - Holds `EVDS_API_KEY` and the **`fetch_writer`** `DATABASE_URL`. Daily work (`fetch`,
    `backfill`, `db check`, the local dashboard) runs as `fetch_writer`. Comment the line out to
    go back to SQLite.
  - To use SQLite for one command without editing `.env`, set `$env:DATABASE_URL = " "` (a
    space) and `$env:DB_PATH`. **Never `""`:** Windows PowerShell 5.1 deletes the variable on an
    empty assignment, `.env` then wins, and the command writes to Supabase.
- **Owner string (`postgres.<ref>`):** it lives only in the user's password manager, in no file
  at all. It is used one-off for migrations and disaster recovery:
  `$env:DATABASE_URL = Read-Host "postgres URL"; uv run tr-banking db migrate; Remove-Item Env:DATABASE_URL`
  - `db migrate` checks read-only first. With nothing pending it is a no-op, even as
    `fetch_writer`.
  - With a migration pending and a non-owner role it stops with a clear "needs the table owner"
    message.
  - `fetch_writer` can read `schema_migrations` (SELECT only) for this check.
- **Windows task:** it still runs locally on Thursdays at 15:00 and writes to Supabase via
  `.env`, i.e. as `fetch_writer`. That is harmless because upserts are idempotent. It gets
  removed per the Calendar.
- **Local Postgres tests:**
  - The machine has no Docker. A throwaway `pgserver` works: Python 3.12, and initdb with
    `--no-locale`, because the Turkish Windows locale crashes initdb.
  - Point `TEST_DATABASE_URL` at it.

## Architecture


```
config/series.yaml  ->  config.py (validated SeriesSpec)
                          |
sources/<source>.py -> DataFrame[code, date, value]  (OBSERVATION_COLUMNS, sources/__init__.py)
                          |
pipeline.py  ->  db/ (only place with SQL)  ->  SQLite data/tr_banking.db
                    open_repository(settings)   or Postgres/Supabase if DATABASE_URL is set
                                                              |
                     app/dashboard.py + app/metrics.py (pure) + app/i18n.py (pure)
```

- `settings.py`: pydantic-settings from env vars / `.env`. `EVDS_API_KEY` is optional there
  (the dashboard does not need it) and is checked by the fetch pipeline. `DATABASE_URL`
  (SecretStr) switches storage to Postgres; blank values count as unset.
- `db/`:
  - `repository.py` is the abstract `Repository`: validation, upsert SQL and queries written
    once with a `{p}` placeholder.
  - `sqlite.py` and `postgres.py` only add connections, transactions and schema setup.
  - Always open storage via `open_repository(settings)`.
- `cli.py`:
  - `tr-banking fetch [--weeks N] [--source evds|bddk|bkm]`
  - `tr-banking backfill --start YYYY-MM-DD [--end] [--source ...]`
  - `tr-banking db migrate` (idempotent; needs the owner role)
  - `tr-banking db migrate --check`: applies nothing, exit 1 if a migration is pending. It works
    for `fetch_writer`.
  - `tr-banking db check`: connection kind, migrations, RLS, row counts. It never logs the
    connection string.
  - `tr-banking check-freshness [--source]`: exit 1 if a configured series has no data or data
    older than `freshness.MAX_AGE_DAYS` for its frequency (weekly: 13 days), or than its own
    `max_age_days` in `series.yaml` (CPI 45, policy rate 14, BKM 100).
  - `tr-banking scan-raw`: exit 1 if any raw file (name or content) contains a configured
    secret. The secrets checked are the EVDS key, the full `DATABASE_URL` and its password.
  - `tr-banking clean-raw [--days 30]`: delete raw files older than N days (by modification
    time), for local housekeeping.
  - `tr-banking summarize [--dry-run] [--force]`: facts of the newest data week from
    `summary.build_brief`, text from Claude via `summary.write_summary`, stored once per data
    week in `summaries`. Needs `ANTHROPIC_API_KEY` (not for `--dry-run`).
- `pipeline.run_update` runs each source independently. A failing source is logged and the
  others still load; the CLI then exits 1.
- Fetch windows: weekly/daily requests start at `--weeks` back; monthly requests reach back
  at least `MONTHLY_LOOKBACK_MONTHS` before today's month (EVDS 3, BKM 6) to cover the
  publication lag. A new monthly source must be added there.
- Clients that read page by page expose `iter_observations` (BKM, 12 months per chunk); the
  pipeline upserts each chunk, so an interrupted backfill keeps what it has downloaded.
- `sources/common.py` holds the shared retry logic (transient 429/5xx and network errors only)
  and raw-response saving.
- Every client implements `ObservationClient.fetch_observations(codes, start, end)`.

## Data model


- `series(id, source, code, name_tr, name_en, unit, frequency, module)`, `UNIQUE(source, code)`.
- `observations(series_id, date, value, fetched_at)`, `PRIMARY KEY(series_id, date)`.
  - `date` is ISO `YYYY-MM-DD` (period end).
  - `fetched_at` is an ISO UTC timestamp.
- Upserts use `ON CONFLICT ... DO UPDATE`, so loads are idempotent and revisions overwrite old
  values.
- Allowed `source` / `frequency` / `module` values are `Literal`s in `config.py`, not SQL CHECKs,
  so adding one never needs a migration.
- Config-only fields in `series.yaml` (not stored in the database): `deflator: true` (the CPI
  used for real values) and `policy_rate: true` (its changes are the decision markers), at
  most one series each; `category` for colors, KPI tiles and ratios (the full list is in
  `config.py`; banking uses deposits, fx_deposits, npl, npl_consumer, npl_commercial,
  state_banks, private_banks, foreign_banks and others).
- Values are stored exactly as published. Display scaling (thousand TRY -> billion TRY) lives in
  `app/metrics.py`.
- `config/mpc_meetings.yaml` (config only, like `series.yaml`): CBRT MPC meeting dates,
  strictly increasing (`config.MpcCalendar`). Decisions are never typed in; they are read
  from the policy rate on the meeting day. Unlisted rate changes are still marked.
- `summaries(data_date PK, created_at, model, input, text_tr, text_en)` (migration 0004): one
  AI-written summary per data week, with the JSON facts it was written from.
- Non-numeric data such as bank campaigns (module 3) will need an additional table; that is an
  addition, not a rewrite.
- Postgres uses native types: `DATE`, `DOUBLE PRECISION`, `TIMESTAMPTZ`, identity ids.
  SQLite stores ISO text.
- **Postgres migrations** are numbered files in `db/migrations/`, applied in order and recorded
  in `schema_migrations`.
  - Never edit an applied migration; add a new file instead.
  - Every new table must enable RLS, revoke `anon`/`authenticated`, and, if the dashboard needs
    it, grant SELECT plus add a `dashboard_reader` policy.
  - Keep `db/schema.sql` (SQLite) in step with the migrations.
- **Supabase access model:**
  - **Owner `postgres`:** not stored in any file. It is used one-off, via `Read-Host`, for
    `db migrate` (DDL) and disaster recovery, and bypasses RLS as the table owner.
  - **`fetch_writer`** (GitHub Actions and the local `.env`):
    - SELECT, INSERT and UPDATE on `series` and `observations`, plus SELECT on
      `schema_migrations`.
    - No DELETE or TRUNCATE, no DDL. RLS policies exist per command.
    - `statement_timeout` is 60 s.
  - **`dashboard_reader`** (Streamlit): SELECT only; sessions are read-only.
  - Both non-owner roles are created NOLOGIN by migrations. Their passwords are set by hand
    with `sql/enable_*.sql` and never stored in the repo.
  - `Repository.ensure_ready()` creates the schema on SQLite but only *checks* it on Postgres.
    The fetch never runs DDL; a pending migration fails it with a clear message.
  - Every new table needs grants and policies for `fetch_writer` and `dashboard_reader` in its
    migration.
  - Use the **session pooler** (port 5432, user `<role>.<project-ref>`). The direct host is
    IPv6-only and GitHub Actions has no IPv6.

## Coding conventions


- Type hints everywhere; small, single-purpose functions; pure functions where possible
  (parsing, metrics) so they test without I/O.
- Series codes, names and units live in `config/series.yaml`, never in code.
- No secrets in code or logs: the key is a `SecretStr`, sent only as a header.
- Fail loudly on unexpected data (empty series, unexpected or missing columns, bad dates,
  non-numeric values) with a clear message; use the standard `logging` module, not `print`.
- Open files with an explicit `encoding="utf-8"` (Windows defaults to the ANSI codepage).
- Tests never call the real API or read `.env`:
  - HTTP goes through `httpx.MockTransport`.
  - Settings are built with `Settings(_env_file=None, ...)`.
  - Databases are `:memory:` or `tmp_path`.
  - `tests/conftest.py` removes `DATABASE_URL` and `EVDS_API_KEY` from the environment for
    every test.
  - Postgres tests need `TEST_DATABASE_URL` (a disposable server; CI provides one). Each test
    gets its own schema. Repository behavior tests run on both backends.
- **Raw responses are public** (CI artifact of a public repo):
  - Save only response bodies.
  - Pass secrets as `redact=` to `save_raw_response` / `request_with_retries`.
  - Never put secrets in file names.
- **Scheduled fetch** (`.github/workflows/fetch.yml`):
  - Runs Tue and Fri 04:00 UTC, plus `workflow_dispatch`.
  - Secrets `EVDS_API_KEY` and `DATABASE_URL` (`fetch_writer`) go only to the steps that need
    them.
  - Steps: db check → `db migrate --check` → fetch → check-freshness → scan-raw → upload
    artifact (only if the scan passed).
  - **Adding a migration:**
    1. Run the one-off owner command (see Deployment facts) **before** pushing code that
       needs it.
    2. Otherwise the scheduled run fails at the schema check.
- **CI** (`.github/workflows/ci.yml`):
  - Runs on every push: `uv sync --locked`, ruff check/format and pytest, against a
    `postgres:17` service container.
  - It uses no secrets. Keep it that way; secrets belong only in the scheduled fetch workflow.
  - `astral-sh/setup-uv` has no floating major tags since v8, so it is pinned to a full version.
- Dashboard charts use one series per chart, with each series on its own y-scale. Never use a
  dual axis. A reference line in the same unit (yearly inflation on a % rate chart) shares the
  one axis.
- Real values deflate only monetary units (`metrics.MONETARY_UNITS`); rates stay in % with
  changes in pp; card counts stay counts.
- Dashboard layout (`app/dashboard.py`):
  - Tabs are stateful and lazy (`on_change="rerun"`): only the open tab runs and only its
    filters appear in the sidebar. The open tab is kept in `st.session_state["open_section"]`.
    The tabs' `default` is part of the widget identity, so it changes only when the language
    changes (changing it on every switch dropped every second click).
  - KPI tiles are `st.metric` with a sparkline. A change that rounds to zero gets
    `delta_color="off"` and no arrow (st.metric treats "0,0%" as a rise).
  - One color per `category`: `PALETTE`/`CATEGORY_HUES` in `dashboard.py`, the same steps as
    `chartCategoricalColors` in `.streamlit/config.toml`. The palette passed the dataviz
    validator (CVD and contrast) in both modes; three light steps are below 3:1, so every
    chart names its series in the title and every tab has a table.
  - Streamlit keeps a radio's or multiselect's choice as its **label**. Widgets with translated
    option labels (source, nominal/real, series) therefore get one key per language
    (`source_tr`, `source_en`) and start from the other language's value
    (`translated_start`); otherwise a language switch leaves nothing selected in the browser.
    AppTest cannot see this (it keeps the value); check it in a browser.
  - KPI tiles use short units (`SHORT_UNIT_LABELS`, "bn TRY") so values fit a quarter width.
  - Time axes use `DATE_AXIS` (labels keep the year).
  - Alerts: `render_alerts` runs above the tabs on every weekly series except `macro`. The
    threshold (`ALERT_THRESHOLD = 5`) was calibrated on the 2014-2026 history: about 1.5% of
    weeks per series. Recalibrate with the history, not by guess, if series are added.
  - `st.dataframe` draws empty cells as "None" whatever the Styler says: pass a column that
    is often empty as formatted text (e.g. the real-rate column).
  - Every table is drawn by `show_table`: no inner scroll and a pinned first column, so the
    series name stays in view when a wide table scrolls sideways on a phone.
  - Visual checks: run the app on a scratch SQLite (`DATABASE_URL = " "`, `DB_PATH`) and run
    `scripts/screenshots.py` (Playwright with the installed Edge, via `uv run --with
    playwright`; not a project dependency). It writes the README image and, with `--all`,
    every tab in light and dark. AppTest does not see layout or widget-identity bugs.
- The dashboard is public:
  - `load_data` is an `st.cache_data` with a 1 h TTL, shared by all visitors. It opens one
    short connection per cache miss, never one per rerun.
  - Show the page's read time next to the last fetch time.
  - Database errors show `i18n` text `db_unavailable`, never exception details.
    `.streamlit/config.toml` sets `client.showErrorDetails = "type"`.
  - `.streamlit/secrets.toml` is gitignored.
- The dashboard is bilingual (TR default, EN). Every UI text and all number/date formatting
  live in `app/i18n.py`, with both languages always filled in (a test enforces this).
  - Turkish formats: `18.445,2` and `-1,1%`. English formats: `18,445.2` and `-1.1%`.
  - Up/down colors always come with a +/- sign.

## Documentation and modules

- `README.md` is the portfolio showcase (summary, highlights, Mermaid architecture, quick
  start, lessons, roadmap). Keep it short; details go into `docs/`. When behavior changes,
  update the doc that owns the topic. The README roadmap order mirrors the Backlog.
- Modules (`module` value): `credit` (EVDS + BDDK loans), `rates` (EVDS loan rates and the
  policy rate), `cards` (BKM), `banking` (BDDK deposits, NPL, bank groups), `macro` (inputs
  such as CPI, no tab of its own).
- New modules plug into the existing tables; never rewrite the schema for them. Module 3's
  bank rates would join `rates`, next to the EVDS averages; campaigns would need a new table.
