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

## Status (2026-10-07)

- **Everything is pushed and live** (checked 2026-10-07): 79 series in four tabs, migration
  0004 applied, full history loaded (incl. the monthly bulletin's bank groups), CI green,
  the scheduled fetch of 2026-10-06 green with all 79 series fresh. The Windows task is gone.
- **Still open for the user:**
  1. Add the GitHub secret `ANTHROPIC_API_KEY`; until then the summary step is skipped and
     the summary box stays hidden. The first `summarize` needs a go-ahead (`--dry-run`
     first).
  2. Retake the README image (it shows the old sidebar):
     `uv run --with playwright python scripts/screenshots.py --url https://tr-banking-dashboard.streamlit.app/`.
- **Next:** ask the user. Skipped on 2026-10-04: KKM (back to 0), monthly
  tables 11 (no LCR) and 12 (labels hold row numbers); see DATA_SOURCES.
- **Module 3 (local, not pushed):** source `bank_site`, Ziraat branch and internet, İş
  Bankası campaign, 100,000 TRY for 32 days; a bar chart in the Rates tab. No history
  until the scheduled fetch runs (today's rates only). 82 series.
- **Open questions:** the real rate column is empty until CPI is out (ask before
  changing); AI summary effort and eval.

## Backlog (open items)

3. Check that the Supabase free-tier project does not pause.
7. Module 3: more banks only when their rate table is in plain HTML (the user's choice,
   2026-10-07: no Playwright, no hidden endpoints). Loans and campaigns are out for now.
11. Revision history: upserts overwrite. Add a vintage table only if revisions matter.

## Commands

```powershell
uv run pytest; uv run ruff check .; uv run ruff format .
uv run tr-banking fetch [--source evds|bddk|bddk_monthly|bkm|bank_site]  # latest 8 weeks
uv run tr-banking backfill --start 2014-01-03 [--source ...]
uv run tr-banking db migrate [--check] | db check | check-freshness | scan-raw | clean-raw
uv run tr-banking summarize [--dry-run] [--force]     # Claude API, costs money
uv run streamlit run src/tr_banking/app/dashboard.py
```
