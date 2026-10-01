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
| 2026-10-02 Fri 04:00 | Scheduled fetch | The 09-29 run was green on schedule. Check `gh run list --workflow fetch.yml`; the Windows task is no longer needed (Backlog 4). |
| 2026-10-19 | `ubuntu-latest` moves to Ubuntu 26 | Check the next CI and fetch runs. |
| 2026-11-15 | BDDK TLS certificate renewed | On a certificate error, update the bundled intermediate (DATA_SOURCES). |
| 2026-12-18 | CBRT publishes "2027 Para Politikası" | Add the rest of the 2027 MPC dates to `config/mpc_meetings.yaml`. |

## Status (2026-09-29)

- **Live:** 25 series, with Loans, Interest rates and Cards tabs, plus the redesign.
- **Local, 8 commits not pushed:** the Banking sector tab (13 BDDK series), the translated
  widget fix, `scripts/screenshots.py`, unusual-change alerts, the weekly AI summary
  (migration 0004), the MPC calendar, the slimmed CLAUDE.md and the pinned table column.
- **Uncommitted:** the filters left the sidebar; `filter_bar` draws one row at the top of the
  open tab: three buttons that open their picker and show its choice, plus the date field
  itself. pytest and ruff green, checked at 1440 px and 390 px. The README image still shows
  the old sidebar: retake it.
- **The user's steps, in order:**
  1. Apply migration 0004 as the owner.
  2. `git push`.
  3. `uv run tr-banking backfill --start 2014-01-03 --source bddk`.
  4. Add the GitHub secret `ANTHROPIC_API_KEY`.
  5. The first `summarize` needs the user's go-ahead: run `--dry-run` first.
  6. On an ImportError, reboot the Streamlit app.
  7. Remove the Windows scheduled task (the cloud fetch runs green).
- **README screenshot:** the user takes it:
  `uv run --with playwright python scripts/screenshots.py --url https://tr-banking-dashboard.streamlit.app/`.
  After the push, check that the Mermaid diagram renders on GitHub.
- **Next:** ask the user which item comes next. Show the plan first.
- **Open questions:**
  - Module 3: calculator JSON endpoints or Playwright? See the research in DATA_SOURCES.
  - Real rate column: the latest week is empty until CPI is out. Ask before changing it.
  - AI summary: tune effort and build a small eval once a few weeks exist.

## Backlog (open items)

3. Check that the Supabase free-tier project does not pause.
4. Remove the Windows scheduled task (see Calendar).
7. Module 3: bank rates and campaigns from bank sites. Postponed; decide the approach first.
11. Revision history: upserts overwrite. Add a vintage table only if revisions matter.
18. Policy rate before 2018-09-14: the BIS series `TP.BISPOLFAIZ.TUR` or a YAML of
    decisions. Decide first.
19. BKM seasonality: a seasonally adjusted view, or a yearly-%-only default.

## Commands

```powershell
uv run pytest; uv run ruff check .; uv run ruff format .
uv run tr-banking fetch [--source evds|bddk|bkm]      # latest 8 weeks
uv run tr-banking backfill --start 2014-01-03 [--source ...]
uv run tr-banking db migrate [--check] | db check | check-freshness | scan-raw | clean-raw
uv run tr-banking summarize [--dry-run] [--force]     # Claude API, costs money
uv run streamlit run src/tr_banking/app/dashboard.py
```
