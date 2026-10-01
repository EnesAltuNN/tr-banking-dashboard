"""Alerts on unusual weekly changes: robust score against the series' own past year."""

import numpy as np
import pandas as pd
import pytest

from tr_banking.app.metrics import alert_series, unusual_changes, weekly_changes

FRIDAYS = pd.date_range("2025-01-03", periods=60, freq="W-FRI")


def steady(series_id: int = 1, last_change: float = 1.0, seed: int = 1) -> pd.DataFrame:
    """About +1% a week with a little noise, then a chosen last weekly change (in %)."""
    rng = np.random.default_rng(seed)
    growth = 1 + (1.0 + rng.normal(0, 0.2, len(FRIDAYS) - 1)) / 100
    growth[-1] = 1 + last_change / 100
    values = 100 * np.concatenate([[1.0], np.cumprod(growth)])
    return pd.DataFrame({"series_id": series_id, "date": FRIDAYS, "value": values})


def test_a_normal_week_gives_no_alert() -> None:
    assert unusual_changes(steady(last_change=1.1)).empty


def test_a_jump_far_outside_the_past_year_is_flagged() -> None:
    alerts = unusual_changes(steady(last_change=-6.0))

    [row] = alerts.to_dict("records")
    assert row["date"] == FRIDAYS[-1]
    assert row["change"] == pytest.approx(-6.0)
    assert row["typical"] == pytest.approx(1.0, abs=0.1)
    assert row["score"] < -5


def test_rates_are_measured_in_points() -> None:
    rates = pd.DataFrame(
        {
            "series_id": 7,
            "date": FRIDAYS,
            "value": [40.0 + 0.01 * (i % 3) for i in range(59)] + [45.0],
        }
    )

    [row] = unusual_changes(rates, points_ids=[7]).to_dict("records")
    assert row["change"] == pytest.approx(5.0 - 0.01 * (58 % 3))


def test_short_history_or_a_gap_gives_no_score() -> None:
    short = steady(last_change=-20.0).tail(20)
    gap = steady(last_change=-20.0).drop(index=58)  # the week before the last is missing

    assert unusual_changes(short).empty
    assert unusual_changes(gap).empty


def test_flat_history_gives_no_score() -> None:
    flat = pd.DataFrame({"series_id": 1, "date": FRIDAYS, "value": [5.0] * 59 + [6.0]})

    assert unusual_changes(flat, points_ids=[1]).empty


def test_weekly_changes_skip_gaps() -> None:
    values = pd.Series(
        [100.0, 110.0, 121.0], index=pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-23"])
    )

    assert weekly_changes(values, in_points=False).tolist() == pytest.approx([10.0])


def test_only_weekly_series_with_alerts_on_are_checked() -> None:
    series = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "frequency": ["weekly", "weekly", "monthly", "weekly"],
            "module": ["banking", "banking", "cards", "macro"],
            "alerts": [True, False, True, True],
        }
    )

    assert alert_series(series)["id"].tolist() == [1]
