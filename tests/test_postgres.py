from collections.abc import Iterator
from datetime import date

import pandas as pd
import psycopg
import pytest

from tr_banking.config import SeriesSpec
from tr_banking.db import PostgresRepository
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
MIGRATIONS = ["0001_initial.sql", "0002_dashboard_reader.sql"]


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
