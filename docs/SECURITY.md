# Security

The repository and the dashboard are public; the database is not. This page describes who can
do what, and the safeguards that keep secrets out of public places.

## Access model

| Who | Connects as | Can do |
|---|---|---|
| You, one-off (owner string from your password manager) | `postgres` (table owner) | everything: applying migrations (DDL), disaster recovery |
| You, daily (`.env`) | `fetch_writer` | same as the scheduled fetch |
| Scheduled fetch (GitHub Actions) | `fetch_writer` | `SELECT`/`INSERT`/`UPDATE` on `series` and `observations`, `SELECT` on `schema_migrations`; no delete, no schema changes |
| Dashboard (Streamlit Cloud) | `dashboard_reader` | `SELECT` on `series` and `observations` only; sessions are read-only |
| Supabase REST API (`anon`, `authenticated`) | - | nothing |

- **The owner string is stored in no file at all,** only in a password manager. It is typed in
  one-off with `Read-Host` (see [SETUP.md](SETUP.md#applying-a-new-migration)).
- **If a GitHub secret or `.env` leaked,** the attacker could add or correct rows, but not
  delete data or alter tables. Rows they wrote would be overwritten by the next fetch, and the
  role's password can be rotated with one `ALTER ROLE`.
- **Both limited roles are created without a password** (`NOLOGIN`) by the migrations
  [`0002`](../src/tr_banking/db/migrations/0002_dashboard_reader.sql) and
  [`0003`](../src/tr_banking/db/migrations/0003_fetch_writer.sql).
  - Their passwords are set by hand in the Supabase SQL editor, with
    [`sql/enable_fetch_writer.sql`](../sql/enable_fetch_writer.sql) and
    [`sql/enable_dashboard_reader.sql`](../sql/enable_dashboard_reader.sql).
  - They never enter the repository.
  - Delete those queries from the editor history after running them.
- **`fetch_writer` has a 60 s `statement_timeout`.** `dashboard_reader` sessions default to
  `default_transaction_read_only`.

### Why migrations are separated from the scheduled job

Applying migrations needs DDL rights, i.e. the table owner. Giving that string to GitHub
Actions would put full control of the database one leaked secret away. Instead:
- migrations are applied by hand, as the owner, before pushing code that needs them;
- the scheduled job only runs `tr-banking db migrate --check` and fails loudly if a migration
  is pending;
- `tr-banking db migrate` itself checks read-only first. It is a harmless no-op for a limited
  role when nothing is pending, and explains that the owner is needed when something is.

## Row Level Security and the Supabase REST API

Supabase serves the `public` schema over its REST API (PostgREST) as the `anon` and
`authenticated` roles. How it is locked out:
- Row Level Security is enabled on every table (`series`, `observations`,
  `schema_migrations`).
- There is no policy for `anon`/`authenticated`, and their table privileges are revoked, so
  REST requests get `permission denied`.
- Policies exist only for `dashboard_reader` (SELECT) and `fetch_writer` (one per allowed
  command).
- Every new table must repeat this in its migration: enable RLS, revoke the API roles, add
  grants and policies for the two limited roles.

To check that the REST API stays closed, run this with your project URL and anon (publishable)
key. It must fail with *permission denied* (HTTP 401):

```powershell
Invoke-RestMethod "https://<project-ref>.supabase.co/rest/v1/series?select=*" -Headers @{ apikey = "<anon key>" }
```

```bash
curl -s "https://<project-ref>.supabase.co/rest/v1/series?select=*" -H "apikey: <anon key>"
```

## Where secrets live

| Secret | Where | Never in |
|---|---|---|
| EVDS API key | `.env`, GitHub secret `EVDS_API_KEY` | the repo, logs, URLs |
| `fetch_writer` connection string | `.env`, GitHub secret `DATABASE_URL` | the repo, Streamlit |
| `dashboard_reader` connection string | Streamlit secret `DATABASE_URL` | the repo, GitHub |
| Owner (`postgres`) connection string | password manager only | any file, GitHub, Streamlit |

- **In code,** secrets are `SecretStr` values, masked in `repr()` and tracebacks. `db check`
  reports the connection kind, host and role, never the string. Tests assert that passwords do
  not appear in logs or error messages.
- **GitHub secrets are set one by one, by name** (web UI or `gh secret set NAME`, which prompts
  for the value). Never use `gh secret set -f .env`: it turns every `.env` line into a secret
  without asking.
- **`.env`, `*.db`, `data/raw/`, `data/logs/` and `.streamlit/secrets.toml` are gitignored.**
  The git history was scanned for secrets before the first push.

## Public artifacts

The scheduled fetch uploads the raw API responses as a workflow artifact, kept 14 days, to help
debugging. Artifacts of a public repository are public, so:
- **only response bodies are saved:** never request headers, URLs with credentials or
  connection details. File names hold only series codes and dates;
- **the EVDS key is masked** (`***REDACTED***`) if a server ever echoes it, in saved files and
  in error messages;
- **`tr-banking scan-raw` checks every file** (name and content) against the real secret
  values: the EVDS key, the full `DATABASE_URL` and its password. The upload step runs only if
  that scan passes.

## Public dashboard

- **Visitors never see exception details.** A database error shows a plain "database not
  reachable" message. `.streamlit/config.toml` sets `client.showErrorDetails = "type"`, so any
  other error shows only its type; details stay in the app log.
- **Database load is bounded.** Results are cached for one hour in a cache shared by all
  visitors, so the public page causes at most one short database connection per hour.

## TLS

Certificate verification is never disabled. `www.bddk.org.tr` omits its intermediate
certificate, so the client trusts the public intermediate bundled in the package; see
[DATA_SOURCES.md](DATA_SOURCES.md#bddk-weekly-bulletin-verified-2026-09-24).
