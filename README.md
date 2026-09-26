# Turkish Banking Market Dashboard

[![CI](https://github.com/EnesAltuNN/tr-banking-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/EnesAltuNN/tr-banking-dashboard/actions/workflows/ci.yml)
[![Weekly fetch](https://github.com/EnesAltuNN/tr-banking-dashboard/actions/workflows/fetch.yml/badge.svg)](https://github.com/EnesAltuNN/tr-banking-dashboard/actions/workflows/fetch.yml)
[![Open the dashboard](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://tr-banking-dashboard.streamlit.app/)

**Live dashboard: [tr-banking-dashboard.streamlit.app](https://tr-banking-dashboard.streamlit.app/)** (Turkish/English)

A learning/portfolio project that tracks the Turkish banking market in one place.
Three data modules feed **one database** and **one Streamlit dashboard**, with a weekly
AI-generated summary planned on top.

| Module | Source | Frequency | Status |
|---|---|---|---|
| 1. Credit market | CBRT (TCMB) EVDS API and BDDK weekly bulletin | weekly | **done** |
| 2. Card spending | BKM monthly statistics | monthly | researched, on hold |
| 3. Bank loan/deposit rates and campaigns | bank websites (scraping) | daily | planned |

## What it does today

- Fetches weekly banking-sector loan series from two sources:
  - **EVDS3 (CBRT)**, 6 series from 2024-06-28: total consumer loans, housing, auto, general
    purpose (ihtiyaç), individual credit cards and commercial loans.
  - **BDDK weekly bulletin**, 7 series from 2014-01-03: the same consumer items, plus total
    loans and commercial and other loans.
- Stores them with idempotent upserts, either in a local SQLite file (default) or in
  Postgres/Supabase (when `DATABASE_URL` is set). Re-running a fetch never duplicates rows,
  and revised values replace old ones.
- Saves every raw API response under `data/raw/` for debugging.
- Streamlit dashboard in Turkish or English (TR/EN switch, Turkish number formats):
  - a source picker, date range and series filters,
  - one line chart per series,
  - a table with the last value, weekly % and yearly % change (rises green, falls red),
  - a note that values are nominal TRY.

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs the pinned Python version (3.13;
3.11+ is supported) and all dependencies:

```powershell
git clone <this repo>
cd tr-banking-dashboard
uv sync
Copy-Item .env.example .env   # then put your EVDS key in .env
```

### Getting an EVDS API key

1. Create an account at [evds3.tcmb.gov.tr](https://evds3.tcmb.gov.tr/) and log in.
2. Generate an API key from your profile page.
3. Put it in `.env` as `EVDS_API_KEY=...`.

The key is sent only as an HTTP header, never in the URL, and it is never logged.

## Usage

```powershell
# Load history (BDDK goes back to 2014-01-03; EVDS data simply starts on 2024-06-28)
uv run tr-banking backfill --start 2014-01-03

# Fetch the latest 8 weeks from all sources (safe to run repeatedly)
uv run tr-banking fetch
uv run tr-banking fetch --weeks 4 --source bddk   # one source only: evds | bddk

# Open the dashboard
uv run streamlit run src/tr_banking/app/dashboard.py
```

Health checks (used by the scheduled workflow):

```powershell
uv run tr-banking check-freshness   # exit 1 if a series has no or too old data
uv run tr-banking scan-raw          # exit 1 if a raw response file contains a secret
```

Add `-v` for debug logging (`uv run tr-banking -v fetch`). If one source fails, the others are
still loaded. The CLI then exits with code 1, so a scheduler can detect the failure.

New data is published by the CBRT on Thursdays at 14:30 (Istanbul time), for the week ending
the previous Friday.

### Automatic fetch (GitHub Actions)

[`.github/workflows/fetch.yml`](.github/workflows/fetch.yml) runs `db check`,
`db migrate --check`, `fetch`, `check-freshness` and `scan-raw` against Supabase, as the
least-privilege `fetch_writer` role.

- **When:** every **Tuesday and Friday at 04:00 UTC** (07:00 Istanbul; Türkiye is UTC+3 all
  year). CBRT and BDDK publish on Thursdays, so the Friday run brings the new week. The Tuesday
  run keeps the Supabase free project active. Repeated runs are harmless because of upserts.
- **Run it by hand:** **Actions → Weekly fetch → Run workflow**.
- **A silent outage fails the run.** `tr-banking check-freshness` exits 1 when any configured
  series has no data, or data older than its rhythm allows (13 days for weekly series). A
  missed release therefore turns the run red instead of passing quietly.
- **Raw responses are uploaded as an artifact, kept 14 days.** Artifacts of a public
  repository are public, so these safeguards apply:
  - only response bodies are saved, never request headers or connection details;
  - the EVDS key is masked if a server ever echoes it;
  - `tr-banking scan-raw` checks every file against the real secret values, and the upload is
    skipped unless that scan passes.

**Secrets:** add them **one by one, by name** under *Settings → Secrets and variables →
Actions*, or with `gh secret set NAME` (it prompts for the value). Do not use
`gh secret set -f .env`: it would upload the owner string.

| Name | Value |
|---|---|
| `EVDS_API_KEY` | your EVDS API key (same as in `.env`) |
| `DATABASE_URL` | the Session pooler string of the least-privilege **`fetch_writer`** role: `postgresql://fetch_writer.<project-ref>:<its password>@<pooler-host>:5432/postgres`. **Not** the owner string from `.env`, and not `dashboard_reader`. |

> **Scheduled runs stop after 60 days without repository activity** in public repositories.
> GitHub then disables the workflow; re-enable it under **Actions → Weekly fetch → Enable
> workflow**. Any push counts as activity.
>
> **Supabase pauses free projects after about a week without database activity.** The
> twice-weekly schedule prevents that. A paused project can be restored from the Supabase
> dashboard.

### Weekly automatic fetch on your own PC (Windows, optional)

Not needed once GitHub Actions runs the fetch; kept as an offline alternative.

```powershell
# Register a scheduled task: every Thursday 15:00, or as soon as the PC is on after that
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1

Start-ScheduledTask -TaskName "tr-banking weekly fetch"                  # run it now
Get-Content data\logs\fetch.log -Tail 20                                 # check the log
Unregister-ScheduledTask -TaskName "tr-banking weekly fetch" -Confirm:$false   # remove it
```

The task runs as the current user while logged on. It needs no admin rights and stores no password.
A missed week does no harm, because every fetch re-reads the last 8 weeks.

## Cloud database (Supabase)

By default data goes to `data/tr_banking.db` (SQLite). Setting `DATABASE_URL` switches every
command, including the dashboard, to Postgres. No other change is needed.

1. Create a project at [supabase.com](https://supabase.com).
2. In **Connect**, copy the **Session pooler** connection string:
   - host `aws-0-<region>.pooler.supabase.com`, port `5432`, user `postgres.<project-ref>`;
   - put your database password in place of `[YOUR-PASSWORD]`.
3. Put it in `.env` as `DATABASE_URL=...`. It never goes into the repo; GitHub and Streamlit
   get it as secrets later.
4. Run these commands:

   ```powershell
   uv run tr-banking db check                     # expect "Supabase session pooler (IPv4)"
   uv run tr-banking db migrate                   # create tables, RLS and the read-only role
   uv run tr-banking backfill --start 2014-01-03  # fill it (EVDS + BDDK, about 15 seconds)
   uv run tr-banking db check                     # expect row counts and RLS "on"
   ```

5. In Supabase's **SQL Editor**, enable the two limited roles with a different strong password
   each:
   - [`sql/enable_fetch_writer.sql`](sql/enable_fetch_writer.sql) for the scheduled job;
   - [`sql/enable_dashboard_reader.sql`](sql/enable_dashboard_reader.sql) for the dashboard.

   Type passwords into the editor only, never into the files, and delete the queries from the
   editor history afterwards.

Filling Supabase with `backfill` is simpler than copying the SQLite file. It uses exactly the
code path of the weekly job, and the sources keep the full history anyway.

**Why the session pooler.**
- The direct connection (`db.<project-ref>.supabase.co`) is IPv6-only unless you buy the
  IPv4 add-on, and GitHub Actions runners have no IPv6.
- The session pooler (port 5432) is reachable over IPv4 and behaves like a normal session.
- The transaction pooler (6543) is not used.
- `tr-banking db check` warns if `DATABASE_URL` points to the direct host or port 6543.

**Access model.**

| Who | Connects as | Can do |
|---|---|---|
| You, locally (`.env`) | `postgres` (table owner) | everything: `db migrate` (DDL), large backfills |
| Scheduled fetch (GitHub Actions) | `fetch_writer` | `SELECT`/`INSERT`/`UPDATE` on `series` and `observations`; no delete, no schema changes |
| Dashboard (Streamlit Cloud) | `dashboard_reader` | `SELECT` on `series` and `observations` only; sessions are read-only |
| Supabase REST API (`anon`, `authenticated`) | - | nothing |

The owner string exists only in your local `.env`. If a GitHub secret leaked, it could add or
correct rows but not delete data or alter tables. Schema changes are applied by hand:

1. Run `uv run tr-banking db migrate` locally.
2. Then push.

The scheduled job runs `db migrate --check` and fails loudly if a migration is still pending.

How the REST API is locked out:
- Row Level Security is enabled on every table.
- There is no policy for `anon`/`authenticated`, and their table privileges are revoked, so
  REST requests get `permission denied`.
- The only policy lets `dashboard_reader`, and nobody else, read rows.

To check that the REST API stays closed, run this with your project URL and anon key. It must
fail with *permission denied*:

```powershell
Invoke-RestMethod "https://<project-ref>.supabase.co/rest/v1/series?select=*" -Headers @{ apikey = "<anon key>" }
```

Schema changes are numbered files in `src/tr_banking/db/migrations/`. `db migrate` applies
the missing ones in order and records them in `schema_migrations`.

## Dashboard hosting (Streamlit Community Cloud)

The public dashboard runs on [Streamlit Community Cloud](https://share.streamlit.io) at
**[tr-banking-dashboard.streamlit.app](https://tr-banking-dashboard.streamlit.app/)** and reads Supabase as the read-only
`dashboard_reader` role.

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

     Use the `dashboard_reader` session-pooler string, **not** the `postgres` one. No
     `EVDS_API_KEY` is needed; the dashboard never fetches.
4. Click **Deploy**. Later pushes to `main` redeploy automatically. Secrets can be edited
   under the app's **Settings → Secrets**.

Apps without traffic for 12 hours go to sleep; any visitor can wake them with one click.

## Tests and linting

```powershell
uv run pytest            # all HTTP is mocked; tests never call the real API
uv run ruff check .
uv run ruff format .
```

Postgres tests run only when `TEST_DATABASE_URL` points to a **disposable** Postgres server;
CI provides one. Each test creates and drops its own schema. Never point it at Supabase.

**CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull
request.
- It runs `uv sync --locked`, `ruff check`, `ruff format --check` and the full test suite.
- The tests run against a throwaway Postgres 17 service container, so no Postgres test is
  skipped there.
- CI needs no secrets.

## Configuration

- **Series** live in [`config/series.yaml`](config/series.yaml): code, Turkish/English name,
  unit, frequency and module. To track another series, add an entry there; no code changes.
- **Settings** come from environment variables or `.env`:
  - `EVDS_API_KEY`, needed only for fetching;
  - `DATABASE_URL`, optional: Postgres instead of SQLite;
  - `DB_PATH` and `RAW_DIR`, optional.
- **Streamlit** settings live in [`.streamlit/config.toml`](.streamlit/config.toml). The
  first-run email prompt and usage statistics are turned off for every machine that runs the
  dashboard from the repo root.

## Data sources and notes

- **CBRT EVDS3**, data group `bie_hpbitablo6`: *Bankacılık Sektörü Seçilmiş Kredi
  Büyüklükleri (Yurt İçi Şubeler)* (Banking Sector Selected Loans, Domestic Branches).
  - Weekly (Friday) values in thousand TRY, with TRY and FX loans combined.
  - The dashboard shows billions.

| Code | Series |
|---|---|
| `TP.HPBITABLO6.2` | Consumer loans (total, incl. individual credit cards) |
| `TP.HPBITABLO6.3` | Housing loans |
| `TP.HPBITABLO6.7` | Auto loans |
| `TP.HPBITABLO6.11` | General purpose loans (ihtiyaç) |
| `TP.HPBITABLO6.16` | Individual credit cards |
| `TP.HPBITABLO6.20` | Commercial loans |

- **Consumer loans (total) include individual credit cards.**
  - In the CBRT table, `TP.HPBITABLO6.2` (1.1 Tüketici Kredileri) is the sum of housing, auto,
    general purpose *and* individual credit cards (1.1.1 to 1.1.4).
  - Those four rows add up to the total exactly. Do not add credit cards on top of it.
  - BDDK's matching row is `1.0.2`; BDDK row 1.0.3 (*Tüketici Kredileri*) excludes cards.
- **History starts 2024-06-28.** The older weekly loan groups (`bie_kredi`, `bie_tukkre`) were
  archived on 2025-01-31 and use a different methodology. They are not merged with the current
  series, because joining them would hide a break in the data.
- **Values are nominal.** Inflation, and the TRY value of FX loans, drive much of the growth.

- **BDDK weekly bulletin** ([bddk.org.tr/BultenHaftalik](https://www.bddk.org.tr/BultenHaftalik)),
  table *Krediler*, whole sector.
  - Weekly (Friday) values in million TRY, TRY + FX, from 2014-01-03.
  - Codes have the form `<row id>:<bank group>:<currency>:<column>`.

| Code | Series |
|---|---|
| `1.0.1:10001:TRY:3` | Total loans |
| `1.0.2:10001:TRY:3` | Consumer loans (total, incl. credit cards) |
| `1.0.4:10001:TRY:3` | Housing loans |
| `1.0.5:10001:TRY:3` | Auto loans |
| `1.0.6:10001:TRY:3` | General purpose loans |
| `1.0.8:10001:TRY:3` | Individual credit cards |
| `1.0.12:10001:TRY:3` | Commercial and other loans |

- **How BDDK data is fetched:**
  - BDDK has no official API. The client calls the JSON endpoint behind the bulletin's charts,
    one request per series, spaced 1 second apart.
  - Bank-group breakdowns (state / domestic private / foreign, which add up to the sector) are
    config-only additions: change the `10001` group code.
  - `bddk.org.tr` does not send its intermediate TLS certificate (GlobalSign RSA OV SSL CA
    2018). Browsers fetch it themselves; Python on Linux does not.
  - The client therefore trusts certifi's roots plus that public intermediate, bundled in
    `src/tr_banking/sources/certs/`. It works the same on Windows and in CI, and
    verification is never disabled.
  - BDDK's certificate expires 2026-11-15. If the renewal uses another intermediate, BDDK
    fetches fail with a certificate error until the bundled file is updated.
- **EVDS and BDDK agree closely:** within about 0.3% on common series. Their bank coverage
  differs slightly, and BDDK *commercial and other loans* is broader than EVDS *commercial
  loans*.
- **EVDS3 API notes:**
  - Base URL: `https://evds3.tcmb.gov.tr/igmevdsms-dis/`.
  - Parameters are appended to the path without `?`, e.g. `series=A-B&startDate=DD-MM-YYYY&endDate=...&type=json`.
  - The key goes in a `key` header.
  - The old EVDS2 service endpoint now redirects to EVDS3.

## Project layout

```
config/series.yaml         series definitions
src/tr_banking/
  settings.py              env/.env settings (pydantic-settings)
  config.py                series.yaml loading and validation
  sources/evds.py          EVDS3 client and response parser
  sources/bddk.py          BDDK weekly bulletin client and parser
  sources/common.py        shared HTTP retries and raw-response saving
  db/repository.py         storage interface: validation, upserts, queries (shared)
  db/sqlite.py, schema.sql local SQLite backend
  db/postgres.py           Postgres/Supabase backend, migration runner
  db/migrations/           numbered Postgres migrations (tables, RLS, read-only role)
  pipeline.py, cli.py      fetch/backfill, db and health-check commands
  freshness.py             stale-series detection (pure)
  security.py              secret scan for files that get published
  app/dashboard.py         Streamlit dashboard
  app/metrics.py           last value, weekly/yearly % (pure functions)
  app/i18n.py              TR/EN texts and number/date formatting (pure functions)
.github/workflows/         ci.yml (lint + tests on every push), fetch.yml (Tue/Fri fetch)
scripts/                   Windows Task Scheduler scripts for the weekly fetch
sql/                       one-off SQL to run by hand in Supabase (enable the reader role)
.streamlit/config.toml     Streamlit settings (no email prompt, no telemetry)
tests/                     pytest suite with real EVDS and BDDK response fixtures
```

## Roadmap

1. ~~Credit market from EVDS~~ ✔
2. ~~BDDK weekly bulletin as a second credit source~~ ✔ (bank-group breakdown next)
3. Card spending from BKM monthly statistics (researched; series list pending)
4. Bank loan/deposit rates and campaigns (daily scraping)
5. Weekly AI-generated market summary combining all modules
6. ~~Postgres/Supabase storage, scheduled GitHub Actions fetch, public dashboard~~ ✔
