# Turkish Banking Market Dashboard

A learning/portfolio project that tracks the Turkish banking market in one place.
Three data modules feed **one database** and **one Streamlit dashboard**, with a weekly
AI-generated summary planned on top.

| Module | Source | Frequency | Status |
|---|---|---|---|
| 1. Credit market | CBRT (TCMB) EVDS API; BDDK weekly bulletin later | weekly | **EVDS part done** |
| 2. Card spending | BKM statistics (Excel) | monthly | planned |
| 3. Bank loan/deposit rates and campaigns | bank websites (scraping) | daily | planned |

## What it does today

- Fetches six weekly banking-sector loan series from EVDS3: total consumer loans, housing,
  auto, general purpose (ihtiyaç), individual credit cards and commercial loans.
- Stores them in SQLite with idempotent upserts. Re-running a fetch never duplicates rows,
  and revised values replace old ones.
- Saves every raw API response under `data/raw/` for debugging.
- Streamlit dashboard: one line chart per series, date range and series filters, and a table
  with the last value, week-over-week % and year-over-year % change.

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
# Load history (weekly data in this data group starts on 2024-06-28)
uv run tr-banking backfill --start 2024-06-28

# Fetch the latest 8 weeks (safe to run repeatedly, e.g. daily or weekly)
uv run tr-banking fetch
uv run tr-banking fetch --weeks 4

# Open the dashboard
uv run streamlit run src/tr_banking/app/dashboard.py
```

Add `-v` for debug logging (`uv run tr-banking -v fetch`). The CLI exits with code 1 on API or
data errors, so a scheduler can detect failures.

New data is published by the CBRT on Thursdays at 14:30 (Istanbul time), for the week ending
the previous Friday.

## Tests and linting

```powershell
uv run pytest            # all HTTP is mocked; tests never call the real API
uv run ruff check .
uv run ruff format .
```

## Configuration

- **Series** live in [`config/series.yaml`](config/series.yaml): code, Turkish/English name,
  unit, frequency and module. To track another series, add an entry there; no code changes.
- **Settings** come from environment variables or `.env`: `EVDS_API_KEY` (needed only for
  fetching), plus the optional `DB_PATH` and `RAW_DIR`.

## Data sources and notes

- **CBRT EVDS3**, data group `bie_hpbitablo6`: *Bankacılık Sektörü Seçilmiş Kredi
  Büyüklükleri (Yurt İçi Şubeler)* (Banking Sector Selected Loans, Domestic Branches).
  - Weekly (Friday) values in thousand TRY, with TRY and FX loans combined.
  - The dashboard shows billions.

| Code | Series |
|---|---|
| `TP.HPBITABLO6.2` | Consumer loans (total) |
| `TP.HPBITABLO6.3` | Housing loans |
| `TP.HPBITABLO6.7` | Auto loans |
| `TP.HPBITABLO6.11` | General purpose loans (ihtiyaç) |
| `TP.HPBITABLO6.16` | Individual credit cards |
| `TP.HPBITABLO6.20` | Commercial loans |

- **History starts 2024-06-28.** The older weekly loan groups (`bie_kredi`, `bie_tukkre`) were
  archived on 2025-01-31 and use a different methodology. They are not merged with the current
  series, because joining them would hide a break in the data.
- **Values are nominal.** Inflation, and the TRY value of FX loans, drive much of the growth.
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
  db/schema.sql            series + observations tables
  db/repository.py         the only code that knows SQL
  pipeline.py, cli.py      fetch/backfill
  app/                     Streamlit dashboard and metrics
tests/                     pytest suite with a real EVDS response fixture
```

## Roadmap

1. ~~Credit market from EVDS~~ ✔
2. BDDK weekly bulletin as a second credit source
3. Card spending from BKM monthly statistics
4. Bank loan/deposit rates and campaigns (daily scraping)
5. Weekly AI-generated market summary combining all modules
6. Move storage from SQLite to Postgres/Supabase (only `db/repository.py` changes)
