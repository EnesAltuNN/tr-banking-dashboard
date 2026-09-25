"""Storage interface for series and observations, shared by SQLite and Postgres.

The db package is the only place that knows SQL. The rules (validation, upsert semantics,
query shapes) live here once; subclasses only supply the connection, transactions, schema
setup and the placeholder style of their driver.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Self

import pandas as pd

from tr_banking.config import SeriesSpec
from tr_banking.sources import OBSERVATION_COLUMNS

logger = logging.getLogger(__name__)

SERIES_COLUMNS = ["id", "source", "code", "name_tr", "name_en", "unit", "frequency", "module"]

# `{p}` is replaced by the driver's placeholder: "?" (sqlite3) or "%s" (psycopg).
UPSERT_SERIES_SQL = """
INSERT INTO series (source, code, name_tr, name_en, unit, frequency, module)
VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
ON CONFLICT (source, code) DO UPDATE SET
    name_tr = excluded.name_tr,
    name_en = excluded.name_en,
    unit = excluded.unit,
    frequency = excluded.frequency,
    module = excluded.module
RETURNING id
"""

# Update on conflict (not "ignore"): sources revise past weeks, and a re-fetch must store the
# corrected value. Running the same fetch twice therefore leaves the same rows.
UPSERT_OBSERVATION_SQL = """
INSERT INTO observations (series_id, date, value, fetched_at)
VALUES ({p}, {p}, {p}, {p})
ON CONFLICT (series_id, date) DO UPDATE SET
    value = excluded.value,
    fetched_at = excluded.fetched_at
"""


class StorageError(RuntimeError):
    """The database could not be reached or set up."""


class Repository(ABC):
    placeholder: ClassVar[str]
    backend: ClassVar[str]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def init_schema(self) -> None:
        """Create or upgrade the schema; safe to call on every start."""

    @abstractmethod
    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]: ...

    @abstractmethod
    def _write_one(self, sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        """Run one statement in its own transaction and return its RETURNING rows."""

    @abstractmethod
    def _write_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        """Run a statement once per row, all in one transaction (all rows or none)."""

    def _to_db_date(self, day: date) -> Any:
        return day

    def _to_db_timestamp(self, moment: datetime) -> Any:
        return moment

    def _sql(self, template: str) -> str:
        return template.format(p=self.placeholder)

    # --- writes ---

    def upsert_series(self, spec: SeriesSpec) -> int:
        """Insert or update a series definition and return its id."""
        values = spec.model_dump()
        params = [values[column] for column in SERIES_COLUMNS[1:]]
        [(series_id,)] = self._write_one(self._sql(UPSERT_SERIES_SQL), params)
        return int(series_id)

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

        stamp = (fetched_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        days = pd.to_datetime(observations["date"]).dt.date
        rows = [
            (series_ids[code], self._to_db_date(day), float(value), self._to_db_timestamp(stamp))
            for code, day, value in zip(
                observations["code"], days, observations["value"], strict=True
            )
        ]
        self._write_many(self._sql(UPSERT_OBSERVATION_SQL), rows)
        logger.info("stored %d %s observations", len(rows), source)
        return len(rows)

    # --- reads ---

    def list_series(self, module: str | None = None) -> pd.DataFrame:
        query = f"SELECT {', '.join(SERIES_COLUMNS)} FROM series"
        params: list[Any] = []
        if module is not None:
            query += f" WHERE module = {self.placeholder}"
            params.append(module)
        rows = self._fetch_all(query + " ORDER BY id", params)
        return pd.DataFrame(rows, columns=SERIES_COLUMNS)

    def get_observations(
        self,
        series_ids: Sequence[int] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return series_id, date (datetime64), value; filters are optional and inclusive."""
        p = self.placeholder
        conditions: list[str] = []
        params: list[Any] = []
        if series_ids is not None:
            # An empty selection becomes "IN (NULL)", which matches no rows.
            conditions.append(f"series_id IN ({', '.join([p] * len(series_ids)) or 'NULL'})")
            params.extend(int(series_id) for series_id in series_ids)
        if start is not None:
            conditions.append(f"date >= {p}")
            params.append(self._to_db_date(start))
        if end is not None:
            conditions.append(f"date <= {p}")
            params.append(self._to_db_date(end))

        query = "SELECT series_id, date, value FROM observations"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        rows = self._fetch_all(query + " ORDER BY series_id, date", params)

        frame = pd.DataFrame(rows, columns=["series_id", "date", "value"])
        return frame.astype({"series_id": "int64", "value": "float64"}).assign(
            date=pd.to_datetime(frame["date"])
        )

    def last_fetched_at(self) -> datetime | None:
        [(stamp,)] = self._fetch_all("SELECT MAX(fetched_at) FROM observations")
        if stamp is None:
            return None
        moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
        return moment.astimezone(UTC)

    def latest_dates(self) -> pd.DataFrame:
        """source, code, frequency and newest observation date (NaT if none) per series."""
        rows = self._fetch_all(
            "SELECT s.source, s.code, s.frequency, MAX(o.date) "
            "FROM series s LEFT JOIN observations o ON o.series_id = s.id "
            "GROUP BY s.id, s.source, s.code, s.frequency ORDER BY s.id"
        )
        frame = pd.DataFrame(rows, columns=["source", "code", "frequency", "latest_date"])
        return frame.assign(latest_date=pd.to_datetime(frame["latest_date"]))

    def counts(self) -> dict[str, int]:
        """Row counts per table, for health checks."""
        return {
            table: int(self._fetch_all(f"SELECT COUNT(*) FROM {table}")[0][0])
            for table in ("series", "observations")
        }

    def _series_ids(self, source: str) -> dict[str, int]:
        rows = self._fetch_all(
            f"SELECT code, id FROM series WHERE source = {self.placeholder}", [source]
        )
        return {code: int(series_id) for code, series_id in rows}
