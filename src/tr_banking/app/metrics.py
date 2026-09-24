"""Pure calculations behind the dashboard (no Streamlit, so they are easy to test)."""

from datetime import date, timedelta

import pandas as pd

WEEK = timedelta(weeks=1)
# 52 weeks (not 365 days) lands on the same weekday, so a weekly series has a value there.
YEAR = timedelta(weeks=52)
SUMMARY_COLUMNS = ["series_id", "last_date", "last_value", "wow_pct", "yoy_pct"]

# Stored unit -> (multiplier, label) used for display. Unknown units are shown as stored.
DISPLAY_UNITS = {"thousand TRY": (1e-6, "billion TRY")}


def display_unit(unit: str) -> tuple[float, str]:
    return DISPLAY_UNITS.get(unit, (1.0, unit))


def summarize(observations: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Latest value per series (on or before `as_of`) with week-over-week and year-over-year %.

    Expects columns series_id, date (datetime64), value. Changes compare with the value exactly
    1 and 52 weeks earlier; if that week is missing the change is NaN rather than a comparison
    with some other week. Meant for weekly series.
    """
    if as_of is not None:
        observations = observations[observations["date"] <= pd.Timestamp(as_of)]
    rows = [
        _summarize_series(series_id, group)
        for series_id, group in observations.groupby("series_id", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def pct_change(current: float, previous: float | None) -> float:
    if previous is None or pd.isna(previous) or previous == 0:
        return float("nan")
    return (current / previous - 1) * 100


def _summarize_series(series_id: int, group: pd.DataFrame) -> tuple:
    values = group.set_index("date")["value"]
    last_date = values.index.max()
    last_value = values[last_date]
    return (
        series_id,
        last_date,
        last_value,
        pct_change(last_value, values.get(last_date - WEEK)),
        pct_change(last_value, values.get(last_date - YEAR)),
    )
