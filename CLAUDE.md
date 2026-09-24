# CLAUDE.md

Turkish Banking Market Dashboard: a learning/portfolio project. Three data modules (credit
market, card spending, bank rates) feed one SQLite database and one Streamlit dashboard.
Only module 1 (credit market, TCMB EVDS) is being built right now.

> This file is intentionally short; project overview, data model and conventions are filled
> in once the first module is complete.

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

## Commands

```powershell
uv sync                 # install/update dependencies into .venv
uv run pytest           # run tests (must never hit the real API)
uv run ruff check .     # lint
uv run ruff format .    # format
```
