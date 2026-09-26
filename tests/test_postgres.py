from collections.abc import Iterator
from datetime import date

import pandas as pd
import psycopg
import pytest

from tr_banking.config import SeriesSpec
from tr_banking.db import PostgresRepository, StorageError
from tr_banking.db.postgres import describe_connection

SECRET = "pw-must-not-leak"
HOUSING = SeriesSpec(
    source="evds",
    code="TP.HPBITABLO6.3",
    name_tr="Konut kredileri",
    name_en="Housing loans",
    unit="thousand TRY",
    frequency="weekly",
    module="credit",
)
MIGRATIONS = ["0001_initial.sql", "0002_dashboard_reader.sql", "0003_fetch_writer.sql"]
AUTO = HOUSING.model_copy(update={"code": "TP.HPBITABLO6.7", "name_en": "Auto loans"})


# --- connection strings (pure, always run) ---


def test_session_pooler_is_accepted() -> None:
    url = f"postgresql://postgres.abcdefgh:{SECRET}@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"

    info = describe_connection(url)

    assert info.kind == "Supabase session pooler (IPv4)"
    assert (info.host, info.port, info.role) == (
        "aws-0-eu-central-1.pooler.supabase.com",
        5432,
        "postgres",
    )
    assert info.warnings == []
    assert SECRET not in repr(info)


def test_custom_role_through_pooler() -> None:
    url = "postgresql://dashboard_reader.abcdefgh:x@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"

    assert describe_connection(url).role == "dashboard_reader"


@pytest.mark.parametrize(
    ("url", "warning"),
    [
        (
            "postgresql://postgres.abc:x@aws-0-eu.pooler.supabase.com:6543/postgres",
            "session pooler",
        ),
        ("postgresql://postgres:x@db.abcdefgh.supabase.co:5432/postgres", "no IPv6"),
        (
            "postgresql://postgres:x@aws-0-eu.pooler.supabase.com:5432/postgres",
            "<role>.<project-ref>",
        ),
    ],
)
def test_setups_that_break_in_github_actions_are_flagged(url: str, warning: str) -> None:
    [message] = describe_connection(url).warnings

    assert warning in message


def test_other_servers_are_not_flagged() -> None:
    info = describe_connection("postgresql://postgres@localhost:5432/test")

    assert info.kind == "other Postgres server"
    assert info.warnings == []


# --- migrations and access control (need TEST_DATABASE_URL) ---


@pytest.fixture
def pg(postgres_url: str) -> Iterator[PostgresRepository]:
    with PostgresRepository(postgres_url) as repository:
        repository.init_schema()
        repository.upsert_series(HOUSING)
        repository.upsert_observations(
            "evds",
            pd.DataFrame(
                [("TP.HPBITABLO6.3", date(2026, 9, 18), 1.0)], columns=["code", "date", "value"]
            ),
        )
        yield repository


def test_migrations_are_recorded_and_idempotent(pg: PostgresRepository) -> None:
    pg.init_schema()  # second run: nothing to apply, no error

    assert pg.server_status()["migrations"] == MIGRATIONS


def test_row_level_security_is_on(pg: PostgresRepository) -> None:
    assert pg.server_status()["rls"] == {
        "series": True,
        "observations": True,
        "schema_migrations": True,
    }


def test_dashboard_reader_can_read_but_not_write(postgres_url: str, pg: PostgresRepository) -> None:
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute("SET ROLE dashboard_reader")

        [(rows,)] = conn.execute("SELECT COUNT(*) FROM observations").fetchall()
        assert rows == 1  # the policy lets this role through RLS
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM observations")


@pytest.mark.parametrize("api_role", ["anon", "authenticated"])
def test_supabase_api_roles_get_nothing(
    postgres_url: str, pg: PostgresRepository, api_role: str
) -> None:
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(f"SET ROLE {api_role}")

        for table in ("series", "observations", "schema_migrations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM {table}")


def test_status_as_dashboard_reader_does_not_fail(
    postgres_url: str, pg: PostgresRepository
) -> None:
    with PostgresRepository(postgres_url) as reader:
        reader._conn.execute("SET ROLE dashboard_reader")

        status = reader.server_status()

        assert status["role"] == "dashboard_reader"
        assert status["migrations"] is None  # exists, but this role may not read it
        assert reader.counts() == {"series": 1, "observations": 1}


# --- least-privilege writer (migration 0003) ---


def observations(*rows: tuple[str, date, float]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["code", "date", "value"])


@pytest.fixture
def writer(postgres_url: str, pg: PostgresRepository) -> Iterator[PostgresRepository]:
    """A repository connected like the scheduled job: as fetch_writer, not the owner."""
    with PostgresRepository(postgres_url) as repository:
        repository._conn.execute("SET ROLE fetch_writer")
        yield repository


def test_fetch_writer_can_upsert_new_and_existing_rows(writer: PostgresRepository) -> None:
    writer.ensure_ready()  # a check only: no DDL needed

    writer.upsert_series(HOUSING)  # existing row: UPDATE path
    writer.upsert_series(AUTO)  # new row: INSERT path, uses the identity sequence
    batch = observations(
        ("TP.HPBITABLO6.3", date(2026, 9, 18), 2.0),  # revision of the fixture value
        ("TP.HPBITABLO6.7", date(2026, 9, 18), 41.0),
    )
    writer.upsert_observations("evds", batch)
    writer.upsert_observations("evds", batch)  # idempotent

    assert writer.counts() == {"series": 2, "observations": 2}
    assert writer.get_observations()["value"].tolist() == [2.0, 41.0]


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM observations",
        "TRUNCATE observations",
        "DROP TABLE observations",
        "ALTER TABLE series ADD COLUMN extra INT",
        "CREATE TABLE extra (id INT)",
        "INSERT INTO schema_migrations (version) VALUES ('9999_fake.sql')",
        "DELETE FROM schema_migrations",
    ],
)
def test_fetch_writer_cannot_delete_or_change_the_schema(
    writer: PostgresRepository, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer._conn.execute(statement)


def test_fetch_writer_can_check_for_pending_migrations(writer: PostgresRepository) -> None:
    assert writer.pending_migrations() == []


def test_unmigrated_database_fails_loudly_instead_of_migrating(postgres_url: str) -> None:
    with PostgresRepository(postgres_url) as repository:
        assert repository.pending_migrations() == MIGRATIONS

        with pytest.raises(StorageError, match="tr-banking db migrate"):
            repository.ensure_ready()

        assert repository.pending_migrations() == MIGRATIONS  # nothing was applied


def test_migrate_as_fetch_writer_is_a_no_op_when_up_to_date(writer: PostgresRepository) -> None:
    writer.init_schema()  # read-only check first: nothing pending, so no DDL is attempted

    assert writer.pending_migrations() == []


def test_migrate_as_fetch_writer_explains_that_the_owner_is_needed(
    pg: PostgresRepository, writer: PostgresRepository
) -> None:
    # Pretend 0003 is new: the owner forgets it was applied.
    pg._conn.execute("DELETE FROM schema_migrations WHERE version = '0003_fetch_writer.sql'")

    with pytest.raises(StorageError) as exc_info:
        writer.init_schema()

    message = str(exc_info.value)
    assert "needs the table owner" in message
    assert "connected as fetch_writer" in message
    assert "0003_fetch_writer.sql" in message
    assert writer.pending_migrations() == ["0003_fetch_writer.sql"]  # nothing was applied
