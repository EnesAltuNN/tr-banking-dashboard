from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from tr_banking.config import SeriesSpec
from tr_banking.db import PostgresRepository, Repository, SqliteRepository

HOUSING = SeriesSpec(
    source="evds",
    code="TP.HPBITABLO6.3",
    name_tr="Konut kredileri",
    name_en="Housing loans",
    unit="thousand TRY",
    frequency="weekly",
    module="credit",
)
AUTO = HOUSING.model_copy(update={"code": "TP.HPBITABLO6.7", "name_en": "Auto loans"})
FIRST_FETCH = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
SECOND_FETCH = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request: pytest.FixtureRequest) -> Iterator[Repository]:
    """Every behavior test runs against both backends (Postgres only with TEST_DATABASE_URL)."""
    if request.param == "sqlite":
        repository: Repository = SqliteRepository(":memory:")
    else:
        repository = PostgresRepository(request.getfixturevalue("postgres_url"))
    with repository:
        repository.init_schema()
        yield repository


def observations(*rows: tuple[str, date, float]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["code", "date", "value"])


def two_weeks() -> pd.DataFrame:
    return observations(
        ("TP.HPBITABLO6.3", date(2026, 9, 4), 100.0),
        ("TP.HPBITABLO6.3", date(2026, 9, 11), 101.0),
    )


def test_init_schema_is_idempotent(repo: Repository) -> None:
    repo.init_schema()

    assert repo.list_series().empty


def test_upsert_series_returns_stable_id(repo: Repository) -> None:
    first_id = repo.upsert_series(HOUSING)
    second_id = repo.upsert_series(HOUSING)

    assert first_id == second_id
    assert len(repo.list_series()) == 1


def test_upsert_series_updates_names(repo: Repository) -> None:
    repo.upsert_series(HOUSING)
    repo.upsert_series(HOUSING.model_copy(update={"name_en": "Mortgages"}))

    assert repo.list_series()["name_en"].tolist() == ["Mortgages"]


def test_same_code_in_other_source_is_a_different_series(repo: Repository) -> None:
    evds_id = repo.upsert_series(HOUSING)
    bddk_id = repo.upsert_series(HOUSING.model_copy(update={"source": "bddk"}))

    assert evds_id != bddk_id


def test_list_series_filters_by_module(repo: Repository) -> None:
    repo.upsert_series(HOUSING)
    repo.upsert_series(AUTO.model_copy(update={"module": "cards"}))

    assert repo.list_series(module="credit")["code"].tolist() == ["TP.HPBITABLO6.3"]


def test_upsert_observations_inserts_rows(repo: Repository) -> None:
    series_id = repo.upsert_series(HOUSING)

    written = repo.upsert_observations("evds", two_weeks(), fetched_at=FIRST_FETCH)

    stored = repo.get_observations()
    assert written == 2
    assert stored["series_id"].unique().tolist() == [series_id]
    assert stored["date"].dt.date.tolist() == [date(2026, 9, 4), date(2026, 9, 11)]
    assert stored["value"].tolist() == [100.0, 101.0]


def test_running_the_same_load_twice_does_not_duplicate(repo: Repository) -> None:
    repo.upsert_series(HOUSING)

    repo.upsert_observations("evds", two_weeks(), fetched_at=FIRST_FETCH)
    repo.upsert_observations("evds", two_weeks(), fetched_at=SECOND_FETCH)

    assert len(repo.get_observations()) == 2


def test_revised_value_replaces_old_value(repo: Repository) -> None:
    repo.upsert_series(HOUSING)
    repo.upsert_observations("evds", two_weeks(), fetched_at=FIRST_FETCH)

    revised = observations(("TP.HPBITABLO6.3", date(2026, 9, 11), 105.5))
    repo.upsert_observations("evds", revised, fetched_at=SECOND_FETCH)

    stored = repo.get_observations()
    assert stored["value"].tolist() == [100.0, 105.5]
    assert repo.last_fetched_at() == SECOND_FETCH


def test_unknown_series_is_rejected_and_nothing_written(repo: Repository) -> None:
    repo.upsert_series(HOUSING)
    batch = observations(
        ("TP.HPBITABLO6.3", date(2026, 9, 4), 100.0),
        ("TP.UNKNOWN.1", date(2026, 9, 4), 1.0),
    )

    with pytest.raises(ValueError, match=r"TP\.UNKNOWN\.1"):
        repo.upsert_observations("evds", batch)

    assert repo.get_observations().empty


def test_series_of_another_source_is_not_matched(repo: Repository) -> None:
    repo.upsert_series(HOUSING)

    with pytest.raises(ValueError, match="unknown bkm series"):
        repo.upsert_observations("bkm", two_weeks())


def test_wrong_columns_are_rejected(repo: Repository) -> None:
    with pytest.raises(ValueError, match="expected columns"):
        repo.upsert_observations("evds", pd.DataFrame({"code": [], "value": []}))


def test_missing_values_are_rejected(repo: Repository) -> None:
    repo.upsert_series(HOUSING)

    with pytest.raises(ValueError, match="missing values"):
        repo.upsert_observations(
            "evds", observations(("TP.HPBITABLO6.3", date(2026, 9, 4), float("nan")))
        )


def test_get_observations_filters(repo: Repository) -> None:
    housing_id = repo.upsert_series(HOUSING)
    auto_id = repo.upsert_series(AUTO)
    repo.upsert_observations(
        "evds",
        observations(
            ("TP.HPBITABLO6.3", date(2026, 8, 28), 99.0),
            ("TP.HPBITABLO6.3", date(2026, 9, 4), 100.0),
            ("TP.HPBITABLO6.3", date(2026, 9, 11), 101.0),
            ("TP.HPBITABLO6.7", date(2026, 9, 4), 40.0),
        ),
    )

    by_series = repo.get_observations(series_ids=[auto_id])
    by_range = repo.get_observations(start=date(2026, 9, 4), end=date(2026, 9, 4))
    combined = repo.get_observations(series_ids=[housing_id], start=date(2026, 9, 4))

    assert by_series["value"].tolist() == [40.0]
    assert by_range["value"].tolist() == [100.0, 40.0]
    assert combined["value"].tolist() == [100.0, 101.0]
    assert repo.get_observations(series_ids=[]).empty


def test_last_fetched_at_is_none_when_empty(repo: Repository) -> None:
    assert repo.last_fetched_at() is None


def test_data_persists_in_file_database(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "test.db"

    with SqliteRepository(db_path) as repo:
        repo.init_schema()
        repo.upsert_series(HOUSING)
        repo.upsert_observations("evds", two_weeks(), fetched_at=FIRST_FETCH)

    with SqliteRepository(db_path) as reopened:
        assert len(reopened.get_observations()) == 2
        assert reopened.last_fetched_at() == FIRST_FETCH


def test_latest_dates(repo: Repository) -> None:
    repo.upsert_series(HOUSING)
    repo.upsert_series(AUTO)
    repo.upsert_observations("evds", two_weeks())

    latest = repo.latest_dates()

    assert latest["code"].tolist() == [HOUSING.code, AUTO.code]
    assert latest["frequency"].tolist() == ["weekly", "weekly"]
    assert latest["latest_date"].iloc[0] == pd.Timestamp("2026-09-11")
    assert pd.isna(latest["latest_date"].iloc[1])  # no data yet
