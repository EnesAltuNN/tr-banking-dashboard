"""Shared fixtures. Postgres tests run only when TEST_DATABASE_URL points at a disposable server."""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# Roles Supabase creates for its REST API; created in the test server so migration 0001's
# REVOKE is exercised exactly as on Supabase.
API_ROLES_SQL = """
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', api_role);
        END IF;
    END LOOP;
END
$$;
"""


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach Supabase or EVDS, even if the shell has these variables set."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("EVDS_API_KEY", raising=False)


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Connection string whose search_path is a fresh, empty schema, dropped afterwards."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run Postgres tests")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(API_ROLES_SQL)
        admin.execute(f'CREATE SCHEMA "{schema}"')
        # Like Supabase's public schema: the API roles can use it and get full privileges on
        # new tables by default. Only migration 0001's REVOKE keeps them out.
        admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO anon, authenticated')
        admin.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            "GRANT ALL ON TABLES TO anon, authenticated"
        )
    try:
        yield make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
