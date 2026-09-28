"""Pure calculations behind the dashboard (no Streamlit, so they are easy to test)."""

from collections.abc import Iterable
from datetime import date, timedelta

import pandas as pd

WEEK = timedelta(weeks=1)
# 52 weeks (not 365 days) lands on the same weekday, so a weekly series has a value there.
YEAR = timedelta(weeks=52)
# prev_pct compares with the previous period: a week for weekly, a month for monthly series.
SUMMARY_COLUMNS = ["series_id", "last_date", "last_value", "prev_pct", "yoy_pct"]
RATE_SUMMARY_COLUMNS = ["series_id", "last_date", "last_value", "wow_pp", "yoy_pp", "real_pp"]
# A daily series has no value on weekends and holidays, so "a week earlier" for rates means the
# latest value in the 6 days up to that date. Weekly series are 7 days apart, so for them this
# still finds only the exact week.
LOOKBACK_TOLERANCE = timedelta(days=6)

# Stored unit -> (multiplier, label) used for display. Unknown units are shown as stored.
DISPLAY_UNITS = {
    "thousand TRY": (1e-6, "billion TRY"),  # EVDS
    "million TRY": (1e-3, "billion TRY"),  # BDDK, BKM
    "cards": (1e-6, "million cards"),  # BKM card counts
}
# Units that are money and can therefore be deflated into real values (card counts cannot).
MONETARY_UNITS = frozenset({"thousand TRY", "million TRY"})


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
    index_by_month = by_month(price_index)
    reference = index_by_month.index.max()
    monthly_index = observations["date"].dt.to_period("M").map(index_by_month)
    real = observations.assign(
        value=observations["value"] * index_by_month[reference] / monthly_index
    )
    return real[monthly_index.notna()].reset_index(drop=True), reference


def by_month(monthly: pd.DataFrame) -> pd.Series:
    """Values of a monthly date/value frame, indexed by month (pd.Period)."""
    months = pd.PeriodIndex(monthly["date"].dt.to_period("M"))
    return pd.Series(monthly["value"].to_numpy(), index=months).sort_index()


def annual_inflation(price_index: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year change of a monthly price index in %, as date (month end) / value rows.

    A month without the same month a year earlier gets no row rather than a guess.
    """
    check_price_index(price_index)
    index = by_month(price_index)
    year_ago = index.reindex(index.index - 12).to_numpy()
    inflation = pd.DataFrame(
        {
            "date": index.index.to_timestamp(how="end").normalize(),
            "value": (index / year_ago - 1) * 100,
        }
    )
    return inflation.dropna().reset_index(drop=True)


def rate_changes(rates: pd.DataFrame) -> pd.DataFrame:
    """Dates on which a rate (date/value rows) changed, with the old and the new value.

    For the policy rate these are the MPC decisions that moved it; decisions that kept the rate
    unchanged leave no trace in the data.
    """
    ordered = rates.sort_values("date")
    previous = ordered["value"].shift()
    changed = previous.notna() & (ordered["value"] != previous)
    return pd.DataFrame(
        {
            "date": ordered.loc[changed, "date"],
            "previous": previous[changed],
            "value": ordered.loc[changed, "value"],
        }
    ).reset_index(drop=True)


def summarize_rates(
    observations: pd.DataFrame,
    inflation: pd.DataFrame,
    as_of: date | None = None,
    real_rate_ids: Iterable[int] = (),
) -> pd.DataFrame:
    """Latest rate per series with weekly and yearly changes in percentage points (pp).

    `real_pp` is the rate minus the yearly inflation of the rate's month: a simple difference
    (not the Fisher equation), only for `real_rate_ids`, and NaN while that month has no CPI.
    """
    if as_of is not None:
        observations = observations[observations["date"] <= pd.Timestamp(as_of)]
    inflation_by_month = by_month(inflation) if not inflation.empty else pd.Series(dtype=float)
    real_ids = set(real_rate_ids)
    rows = []
    for series_id, group in observations.groupby("series_id", sort=True):
        values = group.set_index("date")["value"].sort_index()
        last_date = values.index.max()
        last_value = values[last_date]
        month_inflation = inflation_by_month.get(last_date.to_period("M"), float("nan"))
        rows.append(
            (
                series_id,
                last_date,
                last_value,
                last_value - value_near(values, last_date - WEEK),
                last_value - value_near(values, last_date - YEAR),
                last_value - month_inflation if series_id in real_ids else float("nan"),
            )
        )
    return pd.DataFrame(rows, columns=RATE_SUMMARY_COLUMNS)


def real_rates(rates: pd.DataFrame, inflation: pd.DataFrame) -> pd.DataFrame:
    """date/value rows of rate minus the yearly inflation of the rate's month (simple difference,
    not Fisher); rows whose month has no CPI yet are dropped."""
    if inflation.empty:
        return rates.iloc[0:0][["date", "value"]]
    month_inflation = rates["date"].dt.to_period("M").map(by_month(inflation))
    real = rates.assign(value=rates["value"] - month_inflation)[["date", "value"]]
    return real[month_inflation.notna()].reset_index(drop=True)


def value_near(values: pd.Series, target: pd.Timestamp) -> float:
    """Latest value within LOOKBACK_TOLERANCE up to `target`, else NaN."""
    window = values[(values.index > target - LOOKBACK_TOLERANCE) & (values.index <= target)]
    return window.iloc[-1] if not window.empty else float("nan")


def summarize(
    observations: pd.DataFrame, as_of: date | None = None, frequency: str = "weekly"
) -> pd.DataFrame:
    """Latest value per series (on or before `as_of`) with period-over-period and yearly %.

    Expects columns series_id, date (datetime64), value. Weekly series compare with the value
    exactly 1 and 52 weeks earlier, monthly series with the previous month and the same month
    a year earlier. If that period is missing the change is NaN rather than a comparison with
    some other period.
    """
    if as_of is not None:
        observations = observations[observations["date"] <= pd.Timestamp(as_of)]
    rows = [
        _summarize_series(series_id, group, frequency)
        for series_id, group in observations.groupby("series_id", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def pct_change(current: float, previous: float | None) -> float:
    if previous is None or pd.isna(previous) or previous == 0:
        return float("nan")
    return (current / previous - 1) * 100


def _summarize_series(series_id: int, group: pd.DataFrame, frequency: str) -> tuple:
    values = group.set_index("date")["value"]
    last_date = values.index.max()
    last_value = values[last_date]
    if frequency == "monthly":
        by_month = pd.Series(values.to_numpy(), index=values.index.to_period("M"))
        month = last_date.to_period("M")
        previous, year_ago = by_month.get(month - 1), by_month.get(month - 12)
    else:
        previous, year_ago = values.get(last_date - WEEK), values.get(last_date - YEAR)
    return (
        series_id,
        last_date,
        last_value,
        pct_change(last_value, previous),
        pct_change(last_value, year_ago),
    )
