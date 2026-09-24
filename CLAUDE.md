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
- Never print, log or read the contents of `.env`.

## Roadmap (3 modules)

| Module | Source | Frequency | `module` value | Status |
|---|---|---|---|---|
| 1. Credit market | TCMB EVDS3 + BDDK weekly bulletin | weekly | `credit` | done |
| 2. Card spending | BKM statistics, Excel parsing | monthly | `cards` | planned |
| 3. Bank loan/deposit rates + campaigns | bank websites, scraping | daily | `rates` | planned |

New modules must plug into the existing tables; do not rewrite the schema for them.

## Architecture

```
config/series.yaml  ->  config.py (validated SeriesSpec)
                          |
sources/<source>.py -> DataFrame[code, date, value]  (OBSERVATION_COLUMNS, sources/__init__.py)
                          |
pipeline.py  ->  db/repository.py (only place with SQL)  ->  SQLite data/tr_banking.db
                                                              |
                                          app/dashboard.py + app/metrics.py (pure)
```

- `settings.py`: pydantic-settings from env vars / `.env`; `EVDS_API_KEY` is optional there
  (the dashboard does not need it) and is checked by the fetch pipeline.
- `cli.py`:
  - `tr-banking fetch [--weeks N] [--source evds|bddk]`
  - `tr-banking backfill --start YYYY-MM-DD [--end] [--source ...]`
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
- `bddk.org.tr` sends an incomplete TLS chain. Use `truststore` (OS certificate store); never
  `verify=False`.
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
- Dashboard charts use one series per chart, with each series on its own y-scale. Never use a
  dual axis.

## Commands

```powershell
uv sync                                               # install/update dependencies into .venv
uv run pytest                                         # run tests
uv run ruff check .                                   # lint
uv run ruff format .                                  # format
uv run tr-banking backfill --start 2024-06-28         # load history
uv run tr-banking fetch                               # latest 8 weeks
uv run streamlit run src/tr_banking/app/dashboard.py  # dashboard
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1  # weekly task
```

The scheduled task ("tr-banking weekly fetch", Thursdays 15:00) runs `scripts\scheduled_fetch.ps1`,
which logs to `data\logs\fetch.log`. Keep `.ps1` files pure ASCII: Windows PowerShell 5.1 reads
BOM-less files as ANSI.
