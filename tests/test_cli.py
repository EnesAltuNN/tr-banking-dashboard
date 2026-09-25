import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tr_banking import cli
from tr_banking.config import load_series_config
from tr_banking.db import SqliteRepository
from tr_banking.pipeline import UpdateError
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.evds import EvdsApiError


class FakeRun:
    """Stands in for run_update so CLI tests never touch .env, the network or a DB."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[date, date]] = []
        self.sources: list[tuple[str, ...]] = []

    def __call__(self, settings: Any, start: date, end: date, sources: Any) -> dict:
        self.calls.append((start, end))
        self.sources.append(tuple(sources))
        if self.error:
            raise self.error
        return {}


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    run = FakeRun()
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "run_update", run)
    return run


def test_fetch_defaults_to_recent_weeks(fake_run: FakeRun) -> None:
    assert cli.main(["fetch"]) == 0

    [(start, end)] = fake_run.calls
    assert end == date.today()
    assert end - start == timedelta(weeks=cli.DEFAULT_FETCH_WEEKS)


def test_fetch_weeks_option(fake_run: FakeRun) -> None:
    cli.main(["fetch", "--weeks", "2"])

    [(start, end)] = fake_run.calls
    assert end - start == timedelta(weeks=2)


def test_backfill_uses_start_and_end(fake_run: FakeRun) -> None:
    assert cli.main(["backfill", "--start", "2024-06-28", "--end", "2024-12-31"]) == 0

    assert fake_run.calls == [(date(2024, 6, 28), date(2024, 12, 31))]


def test_backfill_end_defaults_to_today(fake_run: FakeRun) -> None:
    cli.main(["backfill", "--start", "2024-06-28"])

    assert fake_run.calls == [(date(2024, 6, 28), date.today())]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["backfill"],
        ["backfill", "--start", "28.06.2024"],
        ["backfill", "--start", "2024-12-31", "--end", "2024-01-01"],
        ["fetch", "--weeks", "0"],
        ["fetch", "--weeks", "abc"],
        ["fetch", "--source", "bkm"],
    ],
)
def test_invalid_arguments_exit_with_usage_error(fake_run: FakeRun, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)

    assert exc_info.value.code == 2
    assert fake_run.calls == []


def test_known_failure_returns_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "run_update", FakeRun(error=EvdsApiError("403 Forbidden")))

    assert cli.main(["fetch"]) == 1
    assert "fetch failed: 403 Forbidden" in caplog.text


def test_all_sources_by_default(fake_run: FakeRun) -> None:
    cli.main(["fetch"])

    assert fake_run.sources == [("evds", "bddk")]


def test_source_option_limits_the_update(fake_run: FakeRun) -> None:
    cli.main(["backfill", "--start", "2014-01-03", "--source", "bddk"])

    assert fake_run.sources == [("bddk",)]


def test_partial_failure_returns_exit_code_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "run_update", FakeRun(error=UpdateError("update failed for: bddk")))

    assert cli.main(["fetch"]) == 1


# --- db commands (real SQLite in tmp_path; Postgres errors via an unreachable port) ---


def use_settings(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def test_db_migrate_creates_sqlite_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    use_settings(monkeypatch, db_path=tmp_path / "t.db")

    assert cli.main(["db", "migrate"]) == 0

    assert "sqlite schema is up to date" in caplog.text
    with SqliteRepository(tmp_path / "t.db") as repo:
        assert repo.counts() == {"series": 0, "observations": 0}


def test_db_check_reports_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    use_settings(monkeypatch, db_path=tmp_path / "t.db")
    cli.main(["db", "migrate"])

    assert cli.main(["db", "check"]) == 0

    assert "rows: 0 series, 0 observations" in caplog.text
    assert "last fetch: never" in caplog.text


def test_db_check_before_migrate_asks_for_migrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_settings(monkeypatch, db_path=tmp_path / "empty.db")

    assert cli.main(["db", "check"]) == 0

    assert "run `tr-banking db migrate`" in caplog.text


def test_unreachable_postgres_fails_without_leaking_password(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Port 1 on localhost refuses immediately; no network access needed.
    use_settings(monkeypatch, database_url="postgresql://postgres:pw-must-not-leak@127.0.0.1:1/x")

    assert cli.main(["db", "check"]) == 1

    assert "could not connect to Postgres" in caplog.text
    assert "pw-must-not-leak" not in caplog.text


# --- check-freshness and scan-raw ---

SPECS = load_series_config(PROJECT_ROOT / "config" / "series.yaml").series
KEY = "fake-evds-key-must-not-leak"


def seed(db_path: Path, day: date, sources: tuple[str, ...] = ("evds", "bddk")) -> None:
    with SqliteRepository(db_path) as repo:
        repo.init_schema()
        for spec in SPECS:
            if spec.source in sources:
                repo.upsert_series(spec)
                repo.upsert_observations(
                    spec.source,
                    pd.DataFrame([(spec.code, day, 1.0)], columns=["code", "date", "value"]),
                )


def test_check_freshness_passes_with_recent_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    seed(tmp_path / "t.db", date.today() - timedelta(days=5))
    use_settings(monkeypatch, db_path=tmp_path / "t.db")

    assert cli.main(["check-freshness"]) == 0

    assert f"all {len(SPECS)} series are fresh" in caplog.text


def test_check_freshness_fails_with_old_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seed(tmp_path / "t.db", date.today() - timedelta(days=30))
    use_settings(monkeypatch, db_path=tmp_path / "t.db")

    assert cli.main(["check-freshness"]) == 1

    assert f"{len(SPECS)} of {len(SPECS)} series are stale" in caplog.text


def test_check_freshness_fails_when_a_configured_series_has_no_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seed(tmp_path / "t.db", date.today(), sources=("evds",))
    use_settings(monkeypatch, db_path=tmp_path / "t.db")

    assert cli.main(["check-freshness"]) == 1
    assert "(no data)" in caplog.text
    assert cli.main(["check-freshness", "--source", "evds"]) == 0


def test_scan_raw_fails_on_a_leaked_secret_without_printing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    raw = tmp_path / "raw" / "evds"
    raw.mkdir(parents=True)
    (raw / "ok.json").write_text('{"items": []}', encoding="utf-8")
    (raw / "bad.json").write_text(f'{{"echo": "{KEY}"}}', encoding="utf-8")
    use_settings(monkeypatch, raw_dir=tmp_path / "raw", evds_api_key=KEY)

    assert cli.main(["scan-raw"]) == 1

    assert "secret found in" in caplog.text
    assert "bad.json" in caplog.text
    assert KEY not in caplog.text


def test_scan_raw_passes_on_clean_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "ok.json").write_text('{"items": []}', encoding="utf-8")
    use_settings(monkeypatch, raw_dir=tmp_path / "raw", evds_api_key=KEY)

    assert cli.main(["scan-raw"]) == 0

    assert "scanned 1 raw files: no secrets found" in caplog.text
