import math
from datetime import date, timedelta

import pandas as pd
import pytest

from tr_banking.app.metrics import display_unit, pct_change, period_changes, summarize

LAST = date(2026, 9, 18)


def weekly(series_id: int, values: list[float], last: date = LAST) -> pd.DataFrame:
    """Consecutive weekly observations ending at `last`."""
    dates = [last - timedelta(weeks=i) for i in reversed(range(len(values)))]
    return pd.DataFrame({"series_id": series_id, "date": pd.to_datetime(dates), "value": values})


def test_week_over_week_and_year_over_year() -> None:
    # 53 weeks: first value is exactly 52 weeks before the last one.
    values = [100.0] + [110.0] * 50 + [120.0, 132.0]

    [row] = summarize(weekly(1, values)).itertuples()

    assert row.last_date == pd.Timestamp(LAST)
    assert row.last_value == 132.0
    assert row.prev_pct == pytest.approx(10.0)
    assert row.yoy_pct == pytest.approx(32.0)


def test_short_history_has_no_year_over_year() -> None:
    [row] = summarize(weekly(1, [100.0, 101.0])).itertuples()

    assert row.prev_pct == pytest.approx(1.0)
    assert math.isnan(row.yoy_pct)


def test_missing_previous_week_gives_nan_not_another_week() -> None:
    data = weekly(1, [100.0, 105.0, 110.0])
    data = data[data["date"] != pd.Timestamp(LAST - timedelta(weeks=1))]

    [row] = summarize(data).itertuples()

    assert math.isnan(row.prev_pct)


def test_as_of_uses_latest_value_on_or_before_that_date() -> None:
    [row] = summarize(weekly(1, [100.0, 110.0, 121.0]), as_of=LAST - timedelta(days=3)).itertuples()

    assert row.last_date == pd.Timestamp(LAST - timedelta(weeks=1))
    assert row.last_value == 110.0
    assert row.prev_pct == pytest.approx(10.0)


def test_one_row_per_series() -> None:
    data = pd.concat([weekly(2, [10.0, 20.0]), weekly(1, [100.0, 50.0])])

    summary = summarize(data)

    assert summary["series_id"].tolist() == [1, 2]
    assert summary["prev_pct"].tolist() == pytest.approx([-50.0, 100.0])


def test_empty_input_gives_empty_summary() -> None:
    empty = pd.DataFrame({"series_id": [], "date": pd.to_datetime([]), "value": []})

    assert summarize(empty).empty


@pytest.mark.parametrize("previous", [None, 0.0, float("nan")])
def test_pct_change_without_valid_base_is_nan(previous: float | None) -> None:
    assert math.isnan(pct_change(10.0, previous))


def test_display_unit() -> None:
    assert display_unit("thousand TRY") == (1e-6, "billion TRY")
    assert display_unit("million TRY") == (1e-3, "billion TRY")
    assert display_unit("%") == (1.0, "%")


def test_monthly_summary_compares_with_previous_month_and_a_year_earlier() -> None:
    months = pd.to_datetime(["2025-07-31", "2026-06-30", "2026-07-31"])
    frame = pd.DataFrame({"series_id": 1, "date": months, "value": [50.0, 90.0, 100.0]})

    [row] = summarize(frame, frequency="monthly").itertuples()

    assert row.prev_pct == pytest.approx(100 / 90 * 100 - 100)
    assert row.yoy_pct == pytest.approx(100.0)


def test_monthly_summary_missing_month_gives_nan() -> None:
    months = pd.to_datetime(["2026-05-31", "2026-07-31"])
    frame = pd.DataFrame({"series_id": 1, "date": months, "value": [90.0, 100.0]})

    [row] = summarize(frame, frequency="monthly").itertuples()

    assert math.isnan(row.prev_pct)  # June is missing: not compared with May
    assert math.isnan(row.yoy_pct)


def test_period_changes_over_weeks_and_since_new_year() -> None:
    days = pd.date_range("2024-12-27", "2026-01-30", freq="W-FRI")
    values = pd.Series(range(100, 100 + len(days)), index=days, dtype=float)
    observations = pd.DataFrame({"series_id": 1, "date": days, "value": values.to_numpy()})

    [row] = period_changes(observations).to_dict("records")

    last = values.iloc[-1]
    assert row["last_date"] == pd.Timestamp("2026-01-30")
    assert row["w1"] == pytest.approx((last / values.iloc[-2] - 1) * 100)
    assert row["w13"] == pytest.approx((last / values.iloc[-14] - 1) * 100)
    # New Year base: the last Friday of 2025 (26 December).
    assert row["ytd"] == pytest.approx((last / values[pd.Timestamp("2025-12-26")] - 1) * 100)
    assert row["w52"] == pytest.approx((last / values.iloc[-53] - 1) * 100)


def test_period_changes_are_nan_without_enough_history() -> None:
    observations = pd.DataFrame(
        {"series_id": 1, "date": pd.to_datetime(["2026-01-23", "2026-01-30"]), "value": [1.0, 2.0]}
    )

    [row] = period_changes(observations).to_dict("records")

    assert row["w1"] == pytest.approx(100.0)
    assert all(math.isnan(row[name]) for name in ("w4", "w13", "ytd", "w52"))
