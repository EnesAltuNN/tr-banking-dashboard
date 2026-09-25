"""SQLite storage: the local default when DATABASE_URL is not set."""

import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from tr_banking.db.repository import Repository

IN_MEMORY = ":memory:"


class SqliteRepository(Repository):
    placeholder = "?"
    backend = "sqlite"

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != IN_MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        # SQLite does not enforce foreign keys unless asked, per connection.
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        sql = files("tr_banking.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self._conn.executescript(sql)

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        return self._conn.execute(sql, params).fetchall()

    def _write_one(self, sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        # `with conn` wraps the statement in a transaction: commit on success, rollback on error.
        with self._conn:
            return self._conn.execute(sql, params).fetchall()

    def _write_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self._conn:
            self._conn.executemany(sql, rows)

    # SQLite has no date types: store ISO 8601 text, which also sorts chronologically.
    def _to_db_date(self, day: date) -> str:
        return day.isoformat()

    def _to_db_timestamp(self, moment: datetime) -> str:
        return moment.isoformat(timespec="seconds")
