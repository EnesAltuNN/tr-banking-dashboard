"""SQLite storage for series and observations.

This is the only module that knows SQL. Moving to Postgres/Supabase means rewriting this
file (placeholders and connection handling); ON CONFLICT and RETURNING work the same there.
"""

import logging
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Self

import pandas as pd

from tr_banking.config import SeriesSpec
from tr_banking.sources import OBSERVATION_COLUMNS

logger = logging.getLogger(__name__)

IN_MEMORY = ":memory:"

UPSERT_SERIES_SQL = """
INSERT INTO series (source, code, name_tr, name_en, unit, frequency, module)
VALUES (:source, :code, :name_tr, :name_en, :unit, :frequency, :module)
ON CONFLICT (source, code) DO UPDATE SET
    name_tr = excluded.name_tr,
    name_en = excluded.name_en,
    unit = excluded.unit,
    frequency = excluded.frequency,
    module = excluded.module
RETURNING id
"""

# Update on conflict (not "ignore"): EVDS revises past weeks, and a re-fetch must store the
# corrected value. Running the same fetch twice therefore leaves the same rows.
UPSERT_OBSERVATION_SQL = """
INSERT INTO observations (series_id, date, value, fetched_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (series_id, date) DO UPDATE SET
    value = excluded.value,
    fetched_at = excluded.fetched_at
"""


class Repository:
    def __init__(self, path: Path | str) -> None:
        if str(path) != IN_MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        # SQLite does not enforce foreign keys unless asked, per connection.
        self._conn.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        """Create tables if missing; safe to call on every start."""
        sql = files("tr_banking.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self._conn.executescript(sql)

    def upsert_series(self, spec: SeriesSpec) -> int:
        """Insert or update a series definition and return its id."""
        # `with conn` wraps the statement in a transaction: commit on success, rollback on error.
        with self._conn:
            [(series_id,)] = self._conn.execute(UPSERT_SERIES_SQL, spec.model_dump()).fetchall()
        return series_id

    def upsert_observations(
        self,
        source: str,
        observations: pd.DataFrame,
        fetched_at: datetime | None = None,
    ) -> int:
        """Store (code, date, value) rows for already registered series; returns rows written."""
        if list(observations.columns) != OBSERVATION_COLUMNS:
            raise ValueError(
                f"expected columns {OBSERVATION_COLUMNS}, got {list(observations.columns)}"
            )
        if observations["value"].isna().any():
            raise ValueError("observations contain missing values")

        series_ids = self._series_ids(source)
        if unknown := sorted(set(observations["code"]) - set(series_ids)):
            raise ValueError(f"unknown {source} series (call upsert_series first): {unknown}")

        stamp = (fetched_at or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        dates = pd.to_datetime(observations["date"]).dt.strftime("%Y-%m-%d")
        rows = [
            (series_ids[code], day, float(value), stamp)
            for code, day, value in zip(
                observations["code"], dates, observations["value"], strict=True
            )
        ]
        # One transaction for the whole batch: either every row is stored or none is.
        with self._conn:
            self._conn.executemany(UPSERT_OBSERVATION_SQL, rows)
        logger.info("stored %d %s observations", len(rows), source)
        return len(rows)

    def list_series(self, module: str | None = None) -> pd.DataFrame:
        query = "SELECT id, source, code, name_tr, name_en, unit, frequency, module FROM series"
        params: tuple[Any, ...] = ()
        if module is not None:
            query += " WHERE module = ?"
            params = (module,)
        return pd.read_sql_query(query + " ORDER BY id", self._conn, params=params)

    def get_observations(
        self,
        series_ids: Sequence[int] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return series_id, date (datetime64), value; filters are optional and inclusive."""
        conditions: list[str] = []
        params: list[Any] = []
        if series_ids is not None:
            # An empty selection becomes "IN (NULL)", which matches no rows.
            placeholders = ", ".join("?" * len(series_ids)) or "NULL"
            conditions.append(f"series_id IN ({placeholders})")
            params.extend(series_ids)
        if start is not None:
            conditions.append("date >= ?")
            params.append(start.isoformat())
        if end is not None:
            conditions.append("date <= ?")
            params.append(end.isoformat())

        query = "SELECT series_id, date, value FROM observations"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY series_id, date"
        return pd.read_sql_query(query, self._conn, params=params, parse_dates=["date"])

    def last_fetched_at(self) -> datetime | None:
        [(stamp,)] = self._conn.execute("SELECT MAX(fetched_at) FROM observations").fetchall()
        return datetime.fromisoformat(stamp) if stamp else None

    def _series_ids(self, source: str) -> dict[str, int]:
        rows = self._conn.execute("SELECT code, id FROM series WHERE source = ?", (source,))
        return dict(rows.fetchall())
