"""Storage layer: SQLite locally, Postgres (Supabase) when DATABASE_URL is set."""

from tr_banking.db.postgres import PostgresRepository
from tr_banking.db.repository import Repository, StorageError
from tr_banking.db.sqlite import SqliteRepository
from tr_banking.settings import Settings

__all__ = [
    "PostgresRepository",
    "Repository",
    "SqliteRepository",
    "StorageError",
    "open_repository",
]


def open_repository(settings: Settings) -> Repository:
    """The one place that decides where data lives."""
    if settings.database_url is not None:
        return PostgresRepository(settings.database_url.get_secret_value())
    return SqliteRepository(settings.db_path)
