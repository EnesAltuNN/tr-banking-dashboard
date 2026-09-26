# CLAUDE.md

Turkish Banking Market Dashboard: a learning/portfolio project. Three data modules feed one
database and one Streamlit dashboard, plus a planned weekly AI-generated summary of all three.

## Language rules

- **Talk to the user in Turkish**: explanations, plans, step summaries and "manual check" notes.
- **Everything in the repo stays in English**: code, code comments, commit messages,
  README.md and this file.

## Working agreement

- **At the start of every session, read "Calendar" below.** If something is due today or
  overdue, tell the user first.
- The user is learning: keep explanations short, but say *why* behind key decisions.
- Work in phases or small steps:
  1. Show the plan first.
  2. At the end of each step, run tests + ruff, give a short manual checklist and a suggested
     commit.
  3. Then stop and wait for approval.

  The user usually pushes; commits may be made locally when the user asks for uninterrupted
  work.
- The user is on Windows: give PowerShell commands, not bash.
- Never print, log or read the contents of `.env`, not even key names. Running project
  commands that load `.env` internally (`tr-banking ...`, the dashboard) is agreed; they never
  print its values.
- Never ask for passwords, API keys or connection strings in chat. Tell the user where to
  enter them and let them do it:
  - `.env`;
  - GitHub Secrets, **one by one, by name**: `gh secret set NAME --repo ...` prompts for the
    value, or use the web UI. Never `gh secret set -f .env`: it turns every `.env` line into a
    secret and would overwrite the least-privilege `DATABASE_URL` with the owner string;
  - Streamlit secrets.

## Calendar

Check at the start of every session.

| Date (UTC) | Event | What to do |
|---|---|---|
| 2026-09-29 Tue 04:00 | First scheduled fetch | Check the run (`gh run list --workflow fetch.yml`). |
| 2026-10-02 Fri 04:00 | Second scheduled fetch | If both runs are green, remind the user to remove the Windows task (Backlog 4). |
| 2026-10-19 | `ubuntu-latest` moves to Ubuntu 26 | Check the first CI and fetch runs after it. |
| 2026-11-15 | BDDK TLS certificate expires and gets renewed | Check the next BDDK fetch. On a certificate error, update the bundled intermediate (see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)). |

## Project status (updated 2026-09-26)

Hosting:
- Repo: `github.com/EnesAltuNN/tr-banking-dashboard` (**public**).
- Database: Supabase, project in Frankfurt, reached through the session pooler.
- Dashboard: **https://tr-banking-dashboard.streamlit.app/** on Streamlit Community Cloud,
  connected as `dashboard_reader`, Python 3.13.

Cloud migration of module 1 (phases A–E) is done:
- Supabase is backfilled.
- The REST API is closed (401).
- CI is green.
- The Tue/Fri fetch workflow runs in GitHub Actions.
- The dashboard is live.

## Next steps

1. **README as a portfolio showcase** (planned for the next session).
2. **Verify the `fetch_writer` switch** if not confirmed yet: the user sets its password
   (`sql/enable_fetch_writer.sql`) and the GitHub secret `DATABASE_URL`. Then run
   `gh workflow run fetch.yml`: every step must be green, and `db check` in the log must say
   `connected as fetch_writer`.

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
- **GitHub CLI:**
  - Installed at `C:\Program Files\GitHub CLI\gh.exe` and logged in as EnesAltuNN. It is not
    on the PATH of old terminals, so call it by full path.
  - Use it to trigger runs (`gh workflow run fetch.yml`) and to read logs and artifacts.
  - Never use it to read or set secret values.
- **Local `.env`:**
  - Holds `EVDS_API_KEY` and the **owner** (`postgres.<ref>`) `DATABASE_URL`. That is
    intended and it stays that way: local runs are the only place where
    `tr-banking db migrate` (DDL) and large backfills happen. The owner string lives only in
    `.env`, never in GitHub or Streamlit.
  - Local commands and the local dashboard therefore use Supabase. Comment the line out to go
    back to SQLite.
- **Windows task:** it still runs locally on Thursdays at 15:00 and writes to Supabase. That is
  harmless because upserts are idempotent. It gets removed per the Calendar.
- **Local Postgres tests:**
  - The machine has no Docker. A throwaway `pgserver` works: Python 3.12, and initdb with
    `--no-locale`, because the Turkish Windows locale crashes initdb.
  - Point `TEST_DATABASE_URL` at it.

## Disaster recovery

If the Supabase data is lost or the project is recreated:
1. Point `DATABASE_URL` in `.env` at it (owner string).
2. Run `uv run tr-banking db migrate`.
3. Run `uv run tr-banking backfill --start 2014-01-03`. This takes about 15 s; the sources keep
   the full history.
4. Re-enable both roles' logins in the SQL editor: `sql/enable_fetch_writer.sql` and
   `sql/enable_dashboard_reader.sql`.
5. Update the GitHub and Streamlit secrets.

## Roadmap (3 modules)

| Module | Source | Frequency | `module` value | Status |
|---|---|---|---|---|
| 1. Credit market | TCMB EVDS3 + BDDK weekly bulletin | weekly | `credit` | done, live |
| 2. Card spending | BKM monthly statistics (HTML tables) | monthly | `cards` | on hold, see Data sources |
| 3. Bank loan/deposit rates + campaigns | bank websites, scraping | daily | `rates` | planned |

New modules must plug into the existing tables; do not rewrite the schema for them.

## Backlog

1. ~~Least-privilege writer role for the fetch job~~ **done**: `fetch_writer` (migration 0003);
   GitHub `DATABASE_URL` switched to it
2. ~~Add .gitattributes (* text=auto eol=lf)~~ **done**: index was already LF, nothing
   renormalized
3. Verify Supabase free-tier project is not paused after a few weeks
4. Remove the Windows scheduled task once Actions has run reliably (see Calendar)
5. ~~Decide public vs private repo for portfolio~~ **closed**: public, dashboard live
6. Module 2: BKM (see Data sources)
7. Module 3: bank rates/campaigns scraping, start with 3 banks (see Open questions)
8. Weekly AI summary: compute changes in Python, LLM only writes text (see Open questions)
9. ~~`.devcontainer/devcontainer.json` from Streamlit's deploy flow~~ **closed**: deleted (it used
   Python 3.11 + pip and could not install this uv project)
10. Real, inflation-adjusted values: deflate by CPI (TÜFE) from EVDS, next to the nominal view
11. Revision history is not kept. This is a deliberate choice: upserts overwrite revised
    values. A "vintage" table (value per fetch date) could be added later if revisions matter.

## Open questions

- **Module 3 (bank sites):**
  - Check each bank's terms of use and `robots.txt` before scraping.
  - Decide rate limits.
  - Decide whether campaign text needs its own table.
- **AI summary:**
  - Which model?
  - Where does it run? Likely a GitHub Actions step after the fetch, with its own API-key
    secret.
  - Which table stores the generated summaries, with inputs and date, so the dashboard can
    show them?

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
  - `tr-banking fetch [--weeks N] [--source evds|bddk]`
  - `tr-banking backfill --start YYYY-MM-DD [--end] [--source ...]`
  - `tr-banking db migrate` (idempotent; needs the owner role)
  - `tr-banking db migrate --check`: applies nothing, exit 1 if a migration is pending. It works
    for `fetch_writer`.
  - `tr-banking db check`: connection kind, migrations, RLS, row counts. It never logs the
    connection string.
  - `tr-banking check-freshness [--source]`: exit 1 if a configured series has no data or data
    older than `freshness.MAX_AGE_DAYS` for its frequency (weekly: 13 days).
  - `tr-banking scan-raw`: exit 1 if any raw file (name or content) contains a configured
    secret. The secrets checked are the EVDS key, the full `DATABASE_URL` and its password.
- `pipeline.run_update` runs each source independently. A failing source is logged and the
  others still load; the CLI then exits 1.
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
- Values are stored exactly as published. Display scaling (thousand TRY -> billion TRY) lives in
  `app/metrics.py`.
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
  - **Owner `postgres`:** only from the local `.env`. It is used for `db migrate` (DDL) and
    large backfills, and bypasses RLS as the table owner.
  - **`fetch_writer`** (GitHub Actions):
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

## Data sources

Full, verified details: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). Read it before touching
a source client.

- **TCMB EVDS3:**
  - API key in the `key` header; parameters go in the path without `?`.
  - Six weekly series from data group `bie_hpbitablo6`, in thousand TRY, from 2024-06-28.
  - Never guess series codes; discover them through the metadata endpoints.
- **BDDK weekly bulletin:**
  - No official API: one POST per series to the charts' JSON endpoint, 1 s apart.
  - Seven sector series in million TRY from 2014-01-03.
  - The site omits its TLS intermediate, which is bundled in `sources/certs/`.
- **BKM (module 2, on hold):**
  - Researched; 6 proposed series; the monthly "Excel" is really HTML.
  - Before coding:
    - Monthly freshness must allow the 1.5–2 month publication lag. The current
      `MAX_AGE_DAYS["monthly"] = 75` is too tight; use roughly 100 days.
    - Dashboard scaling and labels must follow each series' unit (million TL, card counts),
      not assume TRY.

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
    1. Run `uv run tr-banking db migrate` locally (owner `.env`) **before** pushing code that
       needs it.
    2. Otherwise the scheduled run fails at the schema check.
- **CI** (`.github/workflows/ci.yml`):
  - Runs on every push: `uv sync --locked`, ruff check/format and pytest, against a
    `postgres:17` service container.
  - It uses no secrets. Keep it that way; secrets belong only in the scheduled fetch workflow.
  - `astral-sh/setup-uv` has no floating major tags since v8, so it is pinned to a full version.
- Dashboard charts use one series per chart, with each series on its own y-scale. Never use a
  dual axis.
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

## Commands

```powershell
uv sync                                               # install/update dependencies into .venv
uv run pytest                                         # run tests
uv run ruff check .                                   # lint
uv run ruff format .                                  # format
uv run tr-banking backfill --start 2014-01-03         # load history (all sources)
uv run tr-banking fetch [--source evds|bddk]          # latest 8 weeks
uv run tr-banking db migrate                          # create/upgrade schema (owner role only)
uv run tr-banking db migrate --check                  # exit 1 if a migration is pending
uv run tr-banking db check                            # connection, migrations, RLS, row counts
uv run tr-banking check-freshness                     # exit 1 if data is stale
uv run tr-banking scan-raw                            # exit 1 if a raw file holds a secret
uv run streamlit run src/tr_banking/app/dashboard.py  # dashboard
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1  # weekly task
```

The GitHub Actions workflow is the main scheduler. The optional local Windows task
("tr-banking weekly fetch", Thursdays 15:00) runs `scripts\scheduled_fetch.ps1`, which logs to
`data\logs\fetch.log`. Keep `.ps1` files pure ASCII: Windows PowerShell 5.1 reads BOM-less
files as ANSI.
