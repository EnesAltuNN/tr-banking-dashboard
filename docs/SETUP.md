# Setup and operations

Everything needed to run the project locally, fill the cloud database, schedule the fetch and
host the dashboard. For the security model see [SECURITY.md](SECURITY.md); for the sources and
series see [DATA_SOURCES.md](DATA_SOURCES.md).

- [Local setup](#local-setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Cloud database (Supabase)](#cloud-database-supabase)
- [Applying a new migration](#applying-a-new-migration)
- [Automatic fetch (GitHub Actions)](#automatic-fetch-github-actions)
- [Dashboard hosting (Streamlit Community Cloud)](#dashboard-hosting-streamlit-community-cloud)
- [Weekly fetch on your own PC (Windows, optional)](#weekly-fetch-on-your-own-pc-windows-optional)
- [Development](#development)
- [Disaster recovery](#disaster-recovery)
- [Project layout](#project-layout)

## Local setup

Requires [uv](https://docs.astral.sh/uv/). uv installs the pinned Python version (3.13;
3.11+ is supported) and all dependencies.

PowerShell:

```powershell
git clone https://github.com/EnesAltuNN/tr-banking-dashboard.git
cd tr-banking-dashboard
uv sync
Copy-Item .env.example .env   # then put your EVDS key in .env
```

bash:

```bash
git clone https://github.com/EnesAltuNN/tr-banking-dashboard.git
cd tr-banking-dashboard
uv sync
cp .env.example .env          # then put your EVDS key in .env
```

- **EVDS key:** [DATA_SOURCES.md](DATA_SOURCES.md#tcmb-evds3-verified-2026-09-24) explains how
  to get one. BDDK and BKM need no key, so `--source bddk` and `--source bkm` work without it.
- **Where data goes:** by default to a local SQLite file, `data/tr_banking.db`. Setting
  `DATABASE_URL` switches every command, including the dashboard, to Postgres; see
  [Cloud database](#cloud-database-supabase).

## Usage

```powershell
# Load history: BDDK and the EVDS rates from 2014-01-03, EVDS loans from 2024-06-28, the
# policy rate from 2018-09-14 and BKM from 2017-01. About 3 minutes: BKM pages go 1 s apart.
uv run tr-banking backfill --start 2014-01-03

# Fetch the latest 8 weeks from all sources (safe to run repeatedly)
uv run tr-banking fetch
uv run tr-banking fetch --weeks 4 --source bddk   # one source only: evds | bddk | bkm

# Open the dashboard
uv run streamlit run src/tr_banking/app/dashboard.py
```

Health checks, used by the scheduled workflow:

```powershell
uv run tr-banking db check          # connection kind, migrations, RLS, row counts
uv run tr-banking db migrate --check  # exit 1 if a migration is pending
uv run tr-banking check-freshness   # exit 1 if a series has no or too old data
uv run tr-banking scan-raw          # exit 1 if a raw response file contains a secret
```

Housekeeping:

```powershell
uv run tr-banking clean-raw             # delete raw response files older than 30 days
uv run tr-banking clean-raw --days 7    # keep only the last week
```

- **Upserts are idempotent:** re-running a fetch never duplicates rows, and revised values
  replace old ones.
- **Raw responses are saved** under `data/raw/` for debugging; `clean-raw` removes old ones.
- **Monthly sources publish late**, so every fetch re-reads at least the last 3 months of CPI
  and the last 6 months of BKM, whatever `--weeks` says (`MONTHLY_LOOKBACK_MONTHS` in
  `pipeline.py`).
- **BKM is stored every 12 months** during a backfill, so stopping it with Ctrl+C keeps the
  months already downloaded.
- **Failures:** if one source fails, the others are still loaded. The CLI then exits with code
  1, so a scheduler can detect the failure.
- **Debug logging:** add `-v`, e.g. `uv run tr-banking -v fetch`.

The dashboard runs in Turkish or English, with a TR/EN switch and Turkish number formats. It
has four tabs, each opening with KPI tiles (value, signed change and a one-year sparkline):
- **Loans:** consumer and commercial loans and their real yearly growth; a source picker
  (EVDS or BDDK), weekly % and yearly % change, and a nominal/real switch. The real view shows
  TRY values in the prices of the latest CPI month.
- **Interest rates:** the policy rate, the personal loan rate, yearly inflation and the real
  personal loan rate; loan rates and the policy rate in %, with changes in percentage points,
  yearly inflation as a reference line and MPC meetings as ticks along the time axis (long:
  hike or cut, short: hold).
- **Cards:** card spending (credit + debit), online payments and foreign cards; BKM monthly
  card spending and card counts, with monthly % and yearly % change. The real view deflates
  the amounts only.
- **Banking sector:** total deposits, the FX share of deposits, the NPL ratio and the
  loan-to-deposit ratio; a table of 11 ratios (NPL by segment, bank-group shares of loans and
  deposits) with weekly and yearly changes in pp, charts of the headline ratios, and BDDK's
  deposit and NPL amounts. Bank-group amounts can be added from the sidebar.

Above the tabs, the newest weekly AI summary is shown (labelled as written by AI, with its
model), when one exists. Below it, an alert box lists weekly series whose latest change is far
outside their own past year: more than 5 scaled MADs from the median of the last 52 weekly
changes (in % for amounts, in points for rates). On the 2014-2026 history that flags about
1.5% of weeks per series. When nothing is unusual, one grey line says so.

Layout:
- The filters (source, nominal/real, series, date range) live in the sidebar and show only
  the open tab's controls. Only the open tab runs.
- Charts sit in a two-column grid of cards (one column on phones). Each has a colored dot in
  its title and its source below it. Each category (housing, auto, personal, credit/debit
  card, commercial, policy) keeps one color everywhere.
- Tables show rises in green and falls in red, always with a +/- sign.
- Notes on method sit in a "Notes and method" expander; the footer links the sources and the
  code and explains nominal vs real values.
- The theme is set in [`.streamlit/config.toml`](../.streamlit/config.toml): light and dark
  colors, the Inter font and the chart palette. The page follows the visitor's light/dark
  setting.

### Screenshots

[`scripts/screenshots.py`](../scripts/screenshots.py) shoots the running dashboard with
Playwright and the installed Edge browser (Playwright is not a project dependency):

```powershell
uv run streamlit run src/tr_banking/app/dashboard.py            # in another terminal
uv run --with playwright python scripts/screenshots.py          # docs/images/dashboard.png
uv run --with playwright python scripts/screenshots.py --all data/screenshots  # every tab
uv run --with playwright python scripts/screenshots.py --url https://tr-banking-dashboard.streamlit.app/
```

The README image is the English view in the light theme, 1600×1000; the script warns if the
PNG is over 500 KB.

## Configuration

- **Series** live in [`config/series.yaml`](../config/series.yaml): code, Turkish/English
  name, unit, frequency and module. To track another series, add an entry there; no code
  changes.
- **Settings** come from environment variables or `.env`:
  - `EVDS_API_KEY`, needed only for fetching EVDS;
  - `DATABASE_URL`, optional: Postgres instead of SQLite;
  - `DB_PATH` and `RAW_DIR`, optional.
- **Streamlit** settings live in [`.streamlit/config.toml`](../.streamlit/config.toml). The
  first-run email prompt and usage statistics are off for every machine that runs the
  dashboard from the repo root, and visitors see only exception types.

## Cloud database (Supabase)

1. Create a project at [supabase.com](https://supabase.com).
2. In **Connect**, copy the **Session pooler** connection string:
   - host `aws-0-<region>.pooler.supabase.com`, port `5432`, user `postgres.<project-ref>`;
   - put your database password in place of `[YOUR-PASSWORD]`.

   This is the **owner** string. Keep it in your password manager only, never in a file.
3. Create the schema and load the history once with it. `Read-Host` keeps it out of files and
   out of the shell history:

   ```powershell
   $env:DATABASE_URL = Read-Host "postgres URL"
   uv run tr-banking db check                     # expect "Supabase session pooler (IPv4)"
   uv run tr-banking db migrate                   # tables, RLS, the two limited roles
   uv run tr-banking backfill --start 2014-01-03  # fill it (EVDS + BDDK, about 15 seconds)
   Remove-Item Env:DATABASE_URL
   ```

   In bash, use `read -rs DATABASE_URL && export DATABASE_URL` and `unset DATABASE_URL`.
4. In Supabase's **SQL Editor**, enable the two limited roles with a different strong password
   each:
   - [`sql/enable_fetch_writer.sql`](../sql/enable_fetch_writer.sql) for the scheduled job and
     your daily local work;
   - [`sql/enable_dashboard_reader.sql`](../sql/enable_dashboard_reader.sql) for the dashboard.

   Type passwords into the editor only, never into the files, and delete the queries from the
   editor history afterwards.
5. For daily work, put the **`fetch_writer`** string in `.env` as `DATABASE_URL=...`. It is
   enough for `fetch`, `backfill`, `db check` and the local dashboard. Check it with
   `uv run tr-banking db check`; expect `connected as fetch_writer`.

Filling Supabase with `backfill` is simpler than copying the SQLite file. It uses exactly the
code path of the scheduled job, and the sources keep the full history anyway.

**Why the session pooler:**
- The direct connection (`db.<project-ref>.supabase.co`) is IPv6-only unless you buy the
  IPv4 add-on, and GitHub Actions runners have no IPv6.
- The session pooler (port 5432) is reachable over IPv4 and behaves like a normal session.
- The transaction pooler (6543) is not used.
- `tr-banking db check` warns if `DATABASE_URL` points to the direct host or port 6543.

Schema changes are numbered files in
[`src/tr_banking/db/migrations/`](../src/tr_banking/db/migrations/). `db migrate` applies the
missing ones in order and records them in `schema_migrations`.

## Applying a new migration

1. Run it once as the owner, **before** pushing code that needs it:

   ```powershell
   $env:DATABASE_URL = Read-Host "postgres URL"; uv run tr-banking db migrate; Remove-Item Env:DATABASE_URL
   ```

2. Then push.

Checks along the way:
- `db migrate` first checks read-only. With nothing pending it only says "up to date", even as
  `fetch_writer`.
- With a migration pending and a limited role, it stops with a message saying the owner is
  needed.
- The scheduled job runs `db migrate --check` and fails loudly if a migration is still
  pending.

## Automatic fetch (GitHub Actions)

[`.github/workflows/fetch.yml`](../.github/workflows/fetch.yml) runs `db check`,
`db migrate --check`, `fetch`, `check-freshness`, `summarize` (only with the
`ANTHROPIC_API_KEY` secret) and `scan-raw` against Supabase, as the least-privilege
`fetch_writer` role.

- **When:** every **Tuesday and Friday at 04:00 UTC**, which is 07:00 in Istanbul (Türkiye is
  UTC+3 all year).
  - CBRT and BDDK publish on Thursdays, so the Friday run brings the new week.
  - The Tuesday run keeps the Supabase free project active.
  - Repeated runs are harmless because of upserts.
- **Run it by hand:** **Actions → Weekly fetch → Run workflow**, or
  `gh workflow run fetch.yml`.
- **A silent outage fails the run.** `tr-banking check-freshness` exits 1 when any configured
  series has no data, or data older than its rhythm allows (13 days for weekly series). A
  missed release therefore turns the run red instead of passing quietly.
- **Raw responses are uploaded as a 14-day artifact.** Only after a secret scan; see
  [SECURITY.md](SECURITY.md#public-artifacts).

**Secrets:** add them **one by one, by name** under *Settings → Secrets and variables →
Actions*, or with `gh secret set NAME`, which prompts for the value. Do not use
`gh secret set -f .env`: it turns every `.env` line into a secret, whatever it is.

| Name | Value |
|---|---|
| `EVDS_API_KEY` | your EVDS API key (same as in `.env`) |
| `DATABASE_URL` | the Session pooler string of the least-privilege **`fetch_writer`** role: `postgresql://fetch_writer.<project-ref>:<its password>@<pooler-host>:5432/postgres`. **Not** the owner (`postgres`) string, and not `dashboard_reader`. |
| `ANTHROPIC_API_KEY` | optional: a Claude API key for the weekly AI summary. Without it the summary step is skipped. |

> **Scheduled runs stop after 60 days without repository activity** in public repositories.
> GitHub then disables the workflow; re-enable it under **Actions → Weekly fetch → Enable
> workflow**. Any push counts as activity.
>
> **Supabase pauses free projects after about a week without database activity.** The
> twice-weekly schedule prevents that. A paused project can be restored from the Supabase
> dashboard.

## Dashboard hosting (Streamlit Community Cloud)

The public dashboard runs on [Streamlit Community Cloud](https://share.streamlit.io) at
**[tr-banking-dashboard.streamlit.app](https://tr-banking-dashboard.streamlit.app/)** and reads
Supabase as the read-only `dashboard_reader` role.

**How the deployment works:**
- **Dependencies:** Community Cloud reads `uv.lock` first and installs with `uv sync`, which
  also installs this package, so no `requirements.txt` is needed.
- **Settings:** `.streamlit/config.toml` in the repo root applies there too.
- **Database connection:** root-level Streamlit secrets become environment variables before
  the app starts. A secret named `DATABASE_URL` is therefore picked up by `Settings` like
  everywhere else.
- **Load on Supabase:**
  - Query results are cached for 1 hour in a cache shared by all visitors, so the database
    sees at most one short connection per hour.
  - The page shows both the last data fetch and the time it read the database.
- **Errors:** visitors only see a plain "database not reachable" message and, for unexpected
  errors, the exception type. Details stay in the app log.

**Deploy steps:**

1. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub, then click
   **Create app** → **Deploy a public app from GitHub**.
2. Fill in:
   - Repository `EnesAltuNN/tr-banking-dashboard`, branch `main`
   - Main file path `src/tr_banking/app/dashboard.py`
   - App URL: a short name, e.g. `tr-banking-dashboard`
3. Open **Advanced settings**:
   - **Python version:** 3.13, the version in `.python-version`.
   - **Secrets:** TOML, so the value is **quoted**. GitHub Secrets take the bare value
     instead; that is the one difference.

     ```toml
     DATABASE_URL = "postgresql://dashboard_reader.<project-ref>:<reader password>@<pooler-host>:5432/postgres"
     ```

     Use the `dashboard_reader` session-pooler string, **not** the `postgres` or `fetch_writer`
     one. No `EVDS_API_KEY` is needed; the dashboard never fetches.
4. Click **Deploy**. Later pushes to `main` redeploy automatically. Secrets can be edited
   under the app's **Settings → Secrets**.

Apps without traffic for 12 hours go to sleep; any visitor can wake them with one click.

## Weekly fetch on your own PC (Windows, optional)

Not needed while GitHub Actions runs the fetch; kept as an offline alternative.

```powershell
# Register a scheduled task: every Thursday 15:00, or as soon as the PC is on after that
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1

Start-ScheduledTask -TaskName "tr-banking weekly fetch"                  # run it now
Get-Content data\logs\fetch.log -Tail 20                                 # check the log
Unregister-ScheduledTask -TaskName "tr-banking weekly fetch" -Confirm:$false   # remove it
```

- **Permissions:** the task runs as the current user while logged on. It needs no admin rights
  and stores no password; it uses `DATABASE_URL` from `.env`, i.e. `fetch_writer`.
- **Missed runs:** a missed week does no harm, because every fetch re-reads the last 8 weeks.

## Development

```powershell
uv run pytest            # all HTTP is mocked; tests never call the real API
uv run ruff check .
uv run ruff format .
```

- **Postgres tests** run only when `TEST_DATABASE_URL` points to a **disposable** Postgres
  server; CI provides one.
  - Each test creates and drops its own schema. Never point it at Supabase.
  - Repository behavior tests run on both SQLite and Postgres.
  - The role tests prove what `fetch_writer` and `dashboard_reader` can and cannot do.
- **CI:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push and pull
  request.
  - It runs `uv sync --locked`, `ruff check`, `ruff format --check` and the full test suite.
  - The tests run against a throwaway Postgres 17 service container, so no test is skipped
    there.
  - CI needs no secrets.

## Disaster recovery

If the Supabase data is lost or the project is recreated:
1. Rerun step 3 of [Cloud database](#cloud-database-supabase) with the owner string:
   `db migrate` plus `backfill --start 2014-01-03`, about 3 minutes.
2. Re-enable both roles (step 4).
3. Update `.env` and the GitHub and Streamlit secrets if the project ref or passwords changed.

## Weekly AI summary

Python computes the facts of the newest data week (loans, rates, inflation, banking ratios,
cards, unusual changes); a Claude model (`claude-opus-5` by default, `SUMMARY_MODEL` to change)
writes a short Turkish and English text about exactly those numbers. The text, the model and
the facts are stored in the `summaries` table (migration `0004`); the dashboard shows the
newest one above the tabs.

```powershell
uv run tr-banking summarize --dry-run   # print the facts; nothing is sent or stored
uv run tr-banking summarize             # write the summary of the newest data week (once)
uv run tr-banking summarize --force     # rewrite it
```

- **Once per data week:** a week that already has a summary is skipped, so the scheduled job
  (Friday run after new data) makes about one API call a week, a few cents.
- **Refusals** are handled: the request asks for a server-side fallback
  (`fallbacks: "default"`); if the whole chain declines, the command fails loudly and stores
  nothing.
- **Key:** GitHub secret `ANTHROPIC_API_KEY` for the scheduled job; for a manual run put
  `ANTHROPIC_API_KEY=...` in `.env`.

## Project layout

```
config/series.yaml         series definitions
config/mpc_meetings.yaml   CBRT MPC meeting dates (decision markers)
src/tr_banking/
  settings.py              env/.env settings (pydantic-settings)
  config.py                series.yaml and mpc_meetings.yaml loading and validation
  sources/evds.py          EVDS3 client and response parser
  sources/bddk.py          BDDK weekly bulletin client and parser
  sources/bkm.py           BKM monthly card statistics client and label-based HTML parser
  sources/common.py        shared HTTP retries, raw-response saving, month ends
  sources/certs/           public TLS intermediate that bddk.org.tr does not send
  db/repository.py         storage interface: validation, upserts, queries (shared)
  db/sqlite.py, schema.sql local SQLite backend
  db/postgres.py           Postgres/Supabase backend, migration runner
  db/migrations/           numbered Postgres migrations (tables, RLS, limited roles)
  pipeline.py, cli.py      fetch/backfill, summarize, db and health-check commands
  summary.py               weekly AI summary: facts (pure) and the Claude API call
  freshness.py             stale-series detection (pure)
  security.py              secret scan for files that get published
  app/dashboard.py         Streamlit dashboard
  app/metrics.py           changes, real values, ratios, alerts, series selection (pure)
  app/i18n.py              TR/EN texts and number/date formatting (pure functions)
.github/workflows/         ci.yml (lint + tests on every push), fetch.yml (Tue/Fri fetch)
docs/                      setup, data sources, security, images
scripts/                   screenshots.py; Windows Task Scheduler scripts (optional local fetch)
sql/                       one-off SQL to run by hand in Supabase (enable the limited roles)
.streamlit/config.toml     Streamlit settings (no email prompt, no telemetry, safe errors)
tests/                     pytest suite with real EVDS and BDDK response fixtures
```
