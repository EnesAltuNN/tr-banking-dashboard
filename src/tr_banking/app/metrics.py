"""Pure calculations behind the dashboard (no Streamlit, so they are easy to test)."""

from datetime import date, timedelta

import pandas as pd

WEEK = timedelta(weeks=1)
# 52 weeks (not 365 days) lands on the same weekday, so a weekly series has a value there.
YEAR = timedelta(weeks=52)
SUMMARY_COLUMNS = ["series_id", "last_date", "last_value", "wow_pct", "yoy_pct"]

# Stored unit -> (multiplier, label) used for display. Unknown units are shown as stored.
DISPLAY_UNITS = {
    "thousand TRY": (1e-6, "billion TRY"),  # EVDS
    "million TRY": (1e-3, "billion TRY"),  # BDDK
}


def display_unit(unit: str) -> tuple[float, str]:
    return DISPLAY_UNITS.get(unit, (1.0, unit))


# The largest month-over-month CPI change Türkiye has seen is about +13.6% (2021-12). A bigger
# jump means two index bases were mixed without chaining (e.g. 3513.87 on 2003=100 followed by
# 115.73 on 2025=100), which would silently distort every real value.
MAX_PRICE_INDEX_MONTHLY_CHANGE = 0.20


def check_price_index(price_index: pd.DataFrame) -> None:
    """Fail loudly on a month-over-month change no real month produces."""
    values = price_index.sort_values("date").set_index("date")["value"]
    changes = values.pct_change().abs()
    if (changes > MAX_PRICE_INDEX_MONTHLY_CHANGE).any():
        month = changes.idxmax()
        raise ValueError(
            f"price index changes {changes.max():.0%} in {month:%Y-%m}; were two bases mixed "
            "without chaining?"
        )


def deflate(
    observations: pd.DataFrame, price_index: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Period]:
    """Express values in prices of the latest month of `price_index` (real values).

    Each value is divided by the index of its own month: weekly stocks use their month's
    average CPI, monthly flows use the same month's CPI. Rows from months without an index yet
    (CPI is published with a lag) are dropped instead of guessed. Returns the real rows and the
    reference month.
    """
    check_price_index(price_index)
    index_by_month = price_index.set_index(price_index["date"].dt.to_period("M"))["value"]
    reference = index_by_month.index.max()
    monthly_index = observations["date"].dt.to_period("M").map(index_by_month)
    real = observations.assign(
        value=observations["value"] * index_by_month[reference] / monthly_index
    )
    return real[monthly_index.notna()].reset_index(drop=True), reference


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
