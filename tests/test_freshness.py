from datetime import date

import pandas as pd
import pytest

from tr_banking.freshness import find_stale

TUESDAY = date(2026, 9, 29)


def latest(*rows: tuple[str, str, str | None]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [("evds", code, frequency, day) for code, frequency, day in rows],
        columns=["source", "code", "frequency", "latest_date"],
    )
    return frame.assign(latest_date=pd.to_datetime(frame["latest_date"]))


def test_normal_weekly_rhythm_is_fresh() -> None:
    # Tuesday run: newest week ended the Friday before last (published last Thursday).
    frame = latest(("A", "weekly", "2026-09-18"), ("B", "weekly", "2026-09-25"))

    assert find_stale(frame, TUESDAY).empty


def test_one_missed_weekly_release_is_stale() -> None:
    frame = latest(("A", "weekly", "2026-09-11"), ("B", "weekly", "2026-09-18"))

    stale = find_stale(frame, TUESDAY)

    assert stale["code"].tolist() == ["A"]
    assert stale["age_days"].tolist() == [18]


def test_series_without_data_is_stale() -> None:
    stale = find_stale(latest(("A", "weekly", None)), TUESDAY)

    assert stale["code"].tolist() == ["A"]
    assert pd.isna(stale["age_days"].iloc[0])


def test_limits_depend_on_frequency() -> None:
    frame = latest(("M", "monthly", "2026-08-31"), ("D", "daily", "2026-09-24"))

    assert find_stale(frame, TUESDAY)["code"].tolist() == ["D"]


def test_custom_limits() -> None:
    frame = latest(("A", "weekly", "2026-09-25"))

    assert find_stale(frame, TUESDAY, {"weekly": 3})["code"].tolist() == ["A"]


def test_unknown_frequency_fails_loudly() -> None:
    with pytest.raises(ValueError, match="hourly"):
        find_stale(latest(("A", "hourly", "2026-09-28")), TUESDAY)


def test_series_limit_overrides_the_frequency_limit() -> None:
    # CPI for August is published in early September; 45 days from the month's end is enough,
    # while a 20-day limit would already call it stale.
    frame = latest(("CPI", "monthly", "2026-08-31"), ("X", "monthly", "2026-08-31"))
    frame["max_age_days"] = [20, None]

    stale = find_stale(frame, TUESDAY)

    assert stale["code"].tolist() == ["CPI"]
