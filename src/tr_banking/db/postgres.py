"""PostgreSQL storage (Supabase in production), used when DATABASE_URL is set."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from tr_banking.db.repository import Repository, StorageError

logger = logging.getLogger(__name__)

MIGRATIONS = files("tr_banking.db").joinpath("migrations")
CREATE_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
# Any constant works; it only has to be the same for every process running migrations.
MIGRATION_LOCK_ID = 7_302_025

SUPABASE_POOLER_SUFFIX = ".pooler.supabase.com"
SESSION_POOLER_PORT = 5432
TRANSACTION_POOLER_PORT = 6543


def migration_names() -> list[str]:
    """Packaged migration files in the order they must be applied."""
    return sorted(path.name for path in MIGRATIONS.iterdir() if path.name.endswith(".sql"))


@dataclass(frozen=True)
class ConnectionInfo:
    """What a connection string points to, without the password."""

    kind: str
    host: str
    port: int
    role: str
    warnings: list[str] = field(default_factory=list)


def describe_connection(conninfo: str) -> ConnectionInfo:
    """Classify a connection string and flag setups that will not work from GitHub Actions."""
    params = conninfo_to_dict(conninfo)
    host = str(params.get("host") or "localhost")
    port = int(params.get("port") or 5432)
    user = str(params.get("user") or "")
    role, _, project_ref = user.partition(".")
    warnings: list[str] = []

    if host.endswith(SUPABASE_POOLER_SUFFIX):
        if port == SESSION_POOLER_PORT:
            kind = "Supabase session pooler (IPv4)"
        else:
            kind = "Supabase transaction pooler"
            warnings.append(f"port {port}: use the session pooler (port {SESSION_POOLER_PORT})")
        if not project_ref:
            warnings.append("pooler user must be <role>.<project-ref>, e.g. postgres.abcdefgh")
    elif host.startswith("db.") and host.endswith(".supabase.co"):
        kind = "Supabase direct connection (IPv6 only)"
        warnings.append(
            "GitHub Actions has no IPv6: use the session pooler string from Supabase > Connect"
        )
    else:
        kind = "other Postgres server"
    return ConnectionInfo(kind=kind, host=host, port=port, role=role, warnings=warnings)


class PostgresRepository(Repository):
    placeholder = "%s"
    backend = "postgres"

    def __init__(self, conninfo: str, *, connect_timeout: int = 15) -> None:
        extra: dict[str, Any] = {}
        # Supabase always supports TLS; "require" stops a silent fallback to plain text.
        if "supabase.co" in describe_connection(conninfo).host and "sslmode" not in conninfo:
            extra["sslmode"] = "require"
        try:
            self._conn = psycopg.connect(
                conninfo,
                autocommit=True,  # explicit transactions via conn.transaction()
                prepare_threshold=None,  # poolers may not keep prepared statements
                connect_timeout=connect_timeout,
                **extra,
            )
        except psycopg.OperationalError as exc:
            # The libpq message names host and user but never the password.
            raise StorageError(f"could not connect to Postgres: {exc}") from None

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        """Apply pending migrations in file-name order, each in its own transaction."""
        with self._conn.transaction():
            self._conn.execute(CREATE_MIGRATIONS_TABLE_SQL)
        for name in migration_names():
            self._apply(name, MIGRATIONS.joinpath(name).read_text(encoding="utf-8"))

    def pending_migrations(self) -> list[str]:
        """Migration files not yet recorded in schema_migrations (all of them on a new database)."""
        [(has_table,)] = self._fetch_all("SELECT to_regclass('schema_migrations') IS NOT NULL")
        applied = set()
        if has_table:
            applied = {name for (name,) in self._fetch_all("SELECT version FROM schema_migrations")}
        return [name for name in migration_names() if name not in applied]

    def ensure_ready(self) -> None:
        """Check only: schema changes are applied by hand with `tr-banking db migrate`.

        The scheduled job runs as a role without DDL rights, so it must never try to migrate;
        a pending migration fails it loudly instead.
        """
        if pending := self.pending_migrations():
            raise StorageError(
                f"pending migrations {pending}: run `uv run tr-banking db migrate` with the owner "
                "connection (DATABASE_URL in .env), then re-run"
            )

    def _apply(self, version: str, sql: str) -> None:
        with self._conn.transaction():
            # Serialize concurrent runs (e.g. a scheduled job and a manual one).
            self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            done = self._conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if done:
                return
            self._conn.execute(sql)  # no parameters, so the file may hold many statements
            self._conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        logger.info("applied migration %s", version)

    def server_status(self) -> dict[str, Any]:
        """Server facts for `tr-banking db check`: version, role, migrations and RLS.

        `migrations` is None when the table exists but this role may not read it (the
        dashboard's read-only role, by design).
        """
        # CASE guarantees the privilege check only runs when the table exists.
        [(version, role, has_table, can_read)] = self._fetch_all(
            "SELECT current_setting('server_version'), current_user, "
            "to_regclass('schema_migrations') IS NOT NULL, "
            "CASE WHEN to_regclass('schema_migrations') IS NULL THEN false "
            "ELSE has_table_privilege('schema_migrations', 'SELECT') END"
        )
        migrations: list[str] | None = [] if not has_table else None
        if can_read:
            rows = self._fetch_all("SELECT version FROM schema_migrations ORDER BY version")
            migrations = [name for (name,) in rows]
        rls = dict(
            self._fetch_all(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relnamespace = current_schema()::regnamespace "
                "AND relname IN ('series', 'observations', 'schema_migrations')"
            )
        )
        return {"server_version": version, "role": role, "migrations": migrations, "rls": rls}

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _write_one(self, sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        with self._conn.transaction(), self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _write_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self._conn.transaction(), self._conn.cursor() as cursor:
            cursor.executemany(sql, rows)
