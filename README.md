# Turkish Banking Market Dashboard

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

Add `-v` for debug logging (`uv run tr-banking -v fetch`). If one source fails, the others are
still loaded. The CLI then exits with code 1, so a scheduler can detect the failure.

New data is published by the CBRT on Thursdays at 14:30 (Istanbul time), for the week ending
the previous Friday.

### Weekly automatic fetch (Windows)

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

5. In Supabase's **SQL Editor**, run [`sql/enable_dashboard_reader.sql`](sql/enable_dashboard_reader.sql).
   Type a strong password into the editor only; never save it in the file.

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
| Fetch job (local / GitHub Actions) | `postgres` (table owner) | read and write |
| Dashboard (Streamlit Cloud) | `dashboard_reader` | `SELECT` on `series` and `observations` only; sessions are read-only |
| Supabase REST API (`anon`, `authenticated`) | - | nothing |

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

## Tests and linting

```powershell
uv run pytest            # all HTTP is mocked; tests never call the real API
uv run ruff check .
uv run ruff format .
```

Postgres tests run only when `TEST_DATABASE_URL` points to a **disposable** Postgres server;
CI provides one. Each test creates and drops its own schema. Never point it at Supabase.

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
  - `bddk.org.tr` does not send its intermediate TLS certificate. The client verifies against
    the operating system's certificate store via
    [truststore](https://pypi.org/project/truststore/), never by disabling verification.
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
  pipeline.py, cli.py      fetch/backfill
  app/dashboard.py         Streamlit dashboard
  app/metrics.py           last value, weekly/yearly % (pure functions)
  app/i18n.py              TR/EN texts and number/date formatting (pure functions)
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
6. ~~Postgres/Supabase storage~~ ✔ (GitHub Actions and Streamlit Cloud next)
