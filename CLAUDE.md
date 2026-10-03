# CLAUDE.md

Turkish Banking Market Dashboard, a learning/portfolio project: EVDS, BDDK and BKM data in one
database (SQLite locally, Supabase in the cloud), one bilingual Streamlit dashboard and a weekly
AI summary. Repo `github.com/EnesAltuNN/tr-banking-dashboard` (public); live at
https://tr-banking-dashboard.streamlit.app/.

## Every session (keep token use low)

1. Read only this file. Check "Calendar"; tell the user first about anything due or overdue.
2. Open other files only when the task needs them:
   - `docs/CONVENTIONS.md`: deployment facts, architecture, data model, coding and dashboard
     rules. Read the relevant section before changing code.
   - `docs/DATA_SOURCES.md`: before touching a source client.
   - `docs/SETUP.md` (commands, hosting, disaster recovery) and `docs/SECURITY.md` (roles,
     secrets).
3. Never scan the whole repo; search for what the task names.
4. At the end of a session, rewrite "Status" below: at most 25 lines, current state only.
   Delete finished details instead of appending; git history keeps them.

## Rules

- Talk to the user in **Turkish**. Keep replies short and say *why* behind key decisions.
  Do not paste file contents into the chat.
- Everything in the repo stays in **English**: code, comments, commits, docs.
- Work in small steps: show the plan, then run `uv run pytest` and `uv run ruff check .` at
  the end of each step, and give a short manual checklist and a suggested commit. Then stop.
  Local commits are fine when the user asks for uninterrupted work; the user pushes.
- The user is on Windows: give PowerShell commands. Call `gh` by full path:
  `C:\Program Files\GitHub CLI\gh.exe`. Use it for runs and logs, never for secret values.
- **Secrets:**
  - Never read, print or log `.env`, not even key names. Project commands that load it
    (`tr-banking ...`, the dashboard) are fine.
  - Never ask for passwords, keys or connection strings. Tell the user where to enter them:
    `.env`, Streamlit secrets, or GitHub secrets one by one (`gh secret set NAME --repo ...`
    prompts for the value). Never use `gh secret set -f .env`.
  - The Supabase owner string lives only in the user's password manager. It is used one-off:
    `$env:DATABASE_URL = Read-Host "postgres URL"; uv run tr-banking db migrate; Remove-Item Env:DATABASE_URL`
- **SQLite for one command:** set `$env:DATABASE_URL = " "` (a space) and `$env:DB_PATH`.
  Never `""`: PowerShell 5.1 deletes the variable, `.env` wins, and the command writes to
  Supabase.
- **New migration:** the owner applies it **before** the push, or the scheduled fetch fails at
  its schema check.
- Every live Claude API call costs money: ask the user first.
- Never guess data (series codes, dates, rates). Verify it at the source.

## Calendar (UTC)

| Date | Event | What to do |
|---|---|---|
| 2026-10-19 | `ubuntu-latest` moves to Ubuntu 26 | Check the next CI and fetch runs. |
| 2026-11-15 | BDDK TLS certificate renewed | On a certificate error, update the bundled intermediate (DATA_SOURCES). |
| 2026-12-18 | CBRT publishes "2027 Para Politikası" | Add the rest of the 2027 MPC dates to `config/mpc_meetings.yaml`. |

## Status (2026-10-01)

- **Live:** 25 series; Loans, Interest rates and Cards tabs.
- **Local, not pushed (all tested), since the live 25 series:** Banking tab (BDDK weekly
  and the monthly bulletin `bddk_monthly`), AI summary (migration 0004), alerts, MPC
  calendar, filter row, funding cost, deposit rates (total, maturities, spread), USD/TRY,
  cards 12-month view, POS/ATM, bank-group capital and ROE. 79 series. The bond share
  starts 2022-09-16 (BDDK break).
- **The user's steps, in order:**
  1. Apply migration 0004 as the owner.
  2. `git push`; on an ImportError, reboot the Streamlit app.
  3. `uv run tr-banking backfill --start 2014-01-03` once more for every source (new
     BDDK, EVDS and BDDK monthly history; about 6 minutes). A fetch brings 8 weeks only.
  4. Add the GitHub secret `ANTHROPIC_API_KEY`; the first `summarize` needs a go-ahead
     (`--dry-run` first).
  5. Remove the Windows scheduled task (the cloud fetch runs green).
  6. Retake the README image (it shows the old sidebar):
     `uv run --with playwright python scripts/screenshots.py --url https://tr-banking-dashboard.streamlit.app/`;
     check the Mermaid diagram on GitHub.
- **Next:** CSV downloads for the tables; KKM balance if EVDS has it.
- **Open questions:** module 3 (calculator JSON or Playwright, see DATA_SOURCES); the real
  rate column is empty until CPI is out (ask before changing); AI summary effort and eval.

## Backlog (open items)

3. Check that the Supabase free-tier project does not pause.
4. Remove the Windows scheduled task: both scheduled runs (09-29, 10-02) were green.
7. Module 3: bank rates and campaigns from bank sites. Postponed; decide the approach first.
11. Revision history: upserts overwrite. Add a vintage table only if revisions matter.

## Commands

```powershell
uv run pytest; uv run ruff check .; uv run ruff format .
uv run tr-banking fetch [--source evds|bddk|bddk_monthly|bkm]  # latest 8 weeks
uv run tr-banking backfill --start 2014-01-03 [--source ...]
uv run tr-banking db migrate [--check] | db check | check-freshness | scan-raw | clean-raw
uv run tr-banking summarize [--dry-run] [--force]     # Claude API, costs money
uv run streamlit run src/tr_banking/app/dashboard.py
```
