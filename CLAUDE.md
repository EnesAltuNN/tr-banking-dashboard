# CLAUDE.md

Turkish Banking Market Dashboard: a learning/portfolio project. Three data modules feed one
database and one Streamlit dashboard, plus a planned weekly AI-generated summary of all three.

## Language rules

- **Talk to the user in Turkish**: explanations, plans, step summaries and "manual check" notes.
- **Everything in the repo stays in English**: code, code comments, commit messages,
  README.md and this file.

## Working agreement

- The user is learning: briefly explain *why* behind key decisions.
- Work in small steps; run tests after each step, list what to check manually,
  suggest a commit point, then stop and wait for approval before the next step.
- The user is on Windows: give PowerShell commands, not bash.
- Never print, log or read the contents of `.env`, not even key names. Running project
  commands that load `.env` internally (`tr-banking ...`, the dashboard) is agreed; they never
  print its values.
- Never ask for passwords, API keys or connection strings in chat. Tell the user where to
  enter them (`.env`, GitHub Secrets, Streamlit secrets) and let them do it.
- Keep explanations short. Larger work is split into phases: show the plan first, stop at the
  end of each phase with tests + ruff, a short manual checklist and a suggested commit, then
  wait for approval. The user usually commits and pushes themselves.

## Project status (updated 2026-09-26, after phase D)

Hosting: repo `github.com/EnesAltuNN/tr-banking-dashboard` (**public**), database on Supabase
(project in Frankfurt, reached through the session pooler), dashboard deployment pending.

Cloud migration phases for module 1:

| Phase | Scope | Status |
|---|---|---|
| A | Finish docs, disable Streamlit email prompt | done |
| B | Postgres/Supabase backend, migrations, RLS, `dashboard_reader` | done: Supabase backfilled (13 series, 5,350 rows); REST API returns 401; reader role verified |
| C | GitHub push, history secret scan, CI | done: history clean, CI green |
| D | Scheduled fetch workflow (Tue/Fri 04:00 UTC) | done: manual run 36237384894 green; BDDK OK on Linux; public artifact holds response bodies only |
| E | Streamlit Community Cloud deployment with `dashboard_reader` | code ready: 1 h shared cache, freshness line, safe errors; waiting for the user to deploy |

**Next steps:**
1. **GitHub CLI** is installed at `C:\Program Files\GitHub CLI\gh.exe` and logged in as
   EnesAltuNN. It is not on the PATH of old terminals, so call it by full path.
   - Use it to trigger runs (`gh workflow run fetch.yml`) and read logs and artifacts.
   - Never use it to read or set secret values. The user sets secrets with
     `gh secret set -f .env` or through the web UI.
2. **Reminder owed to the user:** after the first scheduled Tuesday **and** Friday runs succeed,
   remind them to remove the Windows task (Backlog 4). Do not remove it before that.
   The first scheduled runs are Tue 2026-09-29 and Fri 2026-10-02, 04:00 UTC.
3. **Phase E:** code is ready; the user deploys (steps in README, "Dashboard hosting").
   - Afterwards, add the live URL to README and check the app.
   - Verified facts:
     - Community Cloud reads `uv.lock` first and uses `uv sync` (supported since 2024-11), so
       no `requirements.txt` is needed.
     - `streamlit run` promotes root-level string secrets to `os.environ` at bootstrap,
       before the script runs.
   - Secret: TOML with a quoted value,
     `DATABASE_URL = "postgresql://dashboard_reader.<ref>:<pw>@<pooler-host>:5432/postgres"`.
     GitHub Secrets take the bare value.

Local setup notes:
- `.env` holds `EVDS_API_KEY` and `DATABASE_URL`, the write-capable `postgres.<ref>` string.
  Local commands and the local dashboard therefore use Supabase. Comment the line out to go
  back to SQLite.
- The Windows task still runs locally on Thursdays 15:00 and writes to Supabase. That is
  harmless, because upserts are idempotent.
- Local Postgres tests: the repo has no Docker. A throwaway `pgserver` (Python 3.12, initdb
  with `--no-locale` because the Turkish Windows locale crashes initdb) worked; point
  `TEST_DATABASE_URL` at it.

## Roadmap (3 modules)

| Module | Source | Frequency | `module` value | Status |
|---|---|---|---|---|
| 1. Credit market | TCMB EVDS3 + BDDK weekly bulletin | weekly | `credit` | done |
| 2. Card spending | BKM monthly statistics (HTML tables) | monthly | `cards` | on hold, see below |
| 3. Bank loan/deposit rates + campaigns | bank websites, scraping | daily | `rates` | planned |

New modules must plug into the existing tables; do not rewrite the schema for them.

## Backlog

1. Least-privilege writer role for the fetch job (replace the postgres role in GitHub Secrets)
2. Add .gitattributes (* text=auto eol=lf) if missing
3. Verify Supabase free-tier project is not paused after a few weeks
4. Remove the Windows scheduled task once Actions has run reliably
5. Decide public vs private repo for portfolio
6. Module 2: BKM (see pending decision)
7. Module 3: bank rates/campaigns scraping, start with 3 banks
8. Weekly AI summary: compute changes in Python, LLM only writes text

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
  - `tr-banking db migrate` (idempotent)
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
  - The fetch job connects as `postgres`, the table owner, which bypasses RLS.
  - The dashboard connects as `dashboard_reader`: SELECT only, read-only sessions. Its password
    is set by hand with `sql/enable_dashboard_reader.sql` and never stored in the repo.
  - Use the **session pooler** (port 5432, user `<role>.<project-ref>`). The direct host is
    IPv6-only and GitHub Actions has no IPv6.

## Pending decision: BKM card spending (module 2)

Research is done (2026-09-24). **No code has been written.** The user put module 2 on hold and
still has to confirm the series list below before implementation starts.

- **Source page:** one page per month:
  `https://bkm.com.tr/secilen-aya-ait-istatistikler/?filter_year=YYYY&filter_month=M&List=Listele`
  (`robots.txt` allows it).
- **The "Excel" download (`&xls=1`) is really an HTML table** with an `.xls` name, so parse the
  HTML with the stdlib `html.parser`; no Excel library is needed.
- **Coverage:** 2017-01 onward, with the same table layout every month. Publication lags about
  1.5 to 2 months (on 2026-09-24 the latest month was 2026-07).
- **Values:** amounts are in million TL, written in Turkish format (`2.450.238,01`). Card counts
  are plain integers.
- **Proposed series** (July 2026 values):
  - Credit card shopping amount, domestic cards used domestically: 2,450,238 million TL.
  - Debit card shopping amount, domestic cards used domestically: 418,421 million TL.
  - Online card payments (internetten kartli odemeler), amount: 908,899 million TL.
  - Foreign cards used domestically, shopping amount, credit + debit (a tourism signal):
    128,787 million TL.
  - Number of credit cards: 151.7 million.
  - Number of debit cards: 223.2 million.
- **Implementation plan:**
  - Locate cells by row and column labels, not by position.
  - Monthly metrics: month-over-month % and 12-month %.
  - Add dashboard tabs per module.
  - Backfill with one request per month, 1 s apart.

## EVDS3 facts (verified 2026-09-24)

- Base URL `https://evds3.tcmb.gov.tr/igmevdsms-dis/`.
- The EVDS2 service URL now 302-redirects to EVDS3. The client never follows redirects, so the
  key header can't leak to another host.
- The API key goes in the `key` HTTP header; a request without it gets 403.
- Parameters are appended to the path **without `?`**:
  - Data: `series=A-B&startDate=DD-MM-YYYY&endDate=DD-MM-YYYY&type=json`.
  - Metadata: `categories/type=json`, `datagroups/mode=0&type=json`,
    `serieList/type=json&code=<datagroup>`.
- Response format:
  - Shape: `{"totalCount", "items": [{"Tarih": "DD-MM-YYYY", "YEARWEEK", "<CODE_WITH_UNDERSCORES>": "123.00000000" | null, "UNIXTIME"}]}`.
  - Values are strings. Weeks outside a series' range are null.
- Tracked series come from data group `bie_hpbitablo6` (weekly Friday, thousand TRY, TRY+FX) and
  start on 2024-06-28.
  - The older groups `bie_kredi` / `bie_tukkre` were archived on 2025-01-31 with a different
    methodology; do not stitch them together.
- New weekly data appears on Thursdays around 14:30 Istanbul time.
- Never guess series codes: discover them through the metadata endpoints and show the official
  name, frequency and unit before using them.

## BDDK weekly bulletin facts (verified 2026-09-24)

- There is no official API. The client uses the endpoint behind the "advanced" page's charts:
  - `POST https://www.bddk.org.tr/BultenHaftalik/tr/Gelismis/KiyaslamaJsonGetir`
  - Form fields: `dil=tr`, `baslangicTarihi`/`bitisTarihi` (`DD.MM.YYYY`), `id` (row, e.g.
    `1.0.4`), `parabirimi` (`TRY`|`USD`), `sutun` (1 TL, 2 FX, 3 total), `tarafKodu` (bank group).
  - No token or cookie is needed.
  - Response: `{"Baslik", "XEkseni": ["D.MM.YYYY"...], "YEkseni": [numbers]}`.
  - One request returns the full range; data starts 2014-01-03.
- An unknown row or bank group returns HTTP 200 with **empty lists**, so the parser treats an
  empty series as an error.
- The home-page variant `tr/Home/KiyaslamaJsonGetir` caps results at 13 weeks; don't use it.
- Series code in config: `<row>:<group>:<currency>:<column>`.
  - Bank groups: 10001 sector, 10002 deposit, 10003 development & investment, 10004
    participation, 10005 state, 10006 foreign, 10007 domestic private.
  - State + foreign + domestic private = sector (checked).
- Rows come from the bulletin table *Krediler* (tabloId 1 on the page). Row 1.0.3 (consumer
  loans) excludes credit cards; 1.0.2 includes them.
- `bddk.org.tr` sends only its leaf certificate; the intermediate "GlobalSign RSA OV SSL CA
  2018" is missing.
  - `bddk_ssl_context()` trusts certifi plus the bundled public intermediate
    (`sources/certs/`). It works on Windows and Linux CI. Never use `verify=False`.
  - The leaf expires 2026-11-15. If the renewal changes the issuer, download the new
    intermediate from the leaf's "CA Issuers" URL.
- It is a public website, not an API: keep the 1 s delay between requests.

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
  - Secrets `EVDS_API_KEY` and `DATABASE_URL` (the write-capable `postgres.<ref>` session
    pooler string) go only to the steps that need them.
  - Steps: migrate → fetch → check-freshness → scan-raw → upload artifact (only if the scan
    passed).
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
uv run tr-banking db migrate                          # create/upgrade schema (SQLite or Postgres)
uv run tr-banking db check                            # connection, migrations, RLS, row counts
uv run tr-banking check-freshness                     # exit 1 if data is stale
uv run tr-banking scan-raw                            # exit 1 if a raw file holds a secret
uv run streamlit run src/tr_banking/app/dashboard.py  # dashboard
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1  # weekly task
```

The GitHub Actions workflow is the main scheduler. The optional local Windows task ("tr-banking
weekly fetch", Thursdays 15:00) runs `scripts\scheduled_fetch.ps1`,
which logs to `data\logs\fetch.log`. Keep `.ps1` files pure ASCII: Windows PowerShell 5.1 reads
BOM-less files as ANSI.
