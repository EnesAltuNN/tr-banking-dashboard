from datetime import date, timedelta
from typing import Any

import pytest

from tr_banking import cli
from tr_banking.pipeline import UpdateError
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
