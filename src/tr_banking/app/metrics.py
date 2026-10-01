"""Pure calculations behind the dashboard (no Streamlit, so they are easy to test)."""

from collections.abc import Iterable
from datetime import date, timedelta
from typing import NamedTuple

import pandas as pd

from tr_banking.config import SeriesConfig, SeriesSpec
from tr_banking.sources.bddk import parse_series_code as bddk_key

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


def policy_decisions(rates: pd.DataFrame, meetings: Iterable[date]) -> pd.DataFrame:
    """MPC decisions read from the policy rate (date/value rows): "hike", "cut" or "hold".

    A meeting compares the rate on its day (the CBRT applies a decision from the meeting day)
    with the last rate before it; meetings outside the data (before it, or still to come) are
    left out. A change on a day that is not a listed meeting is kept as well, so an unscheduled
    meeting or a calendar not yet updated never hides a decision.
    """
    ordered = rates.sort_values("date")
    rows = []
    for meeting in pd.to_datetime(list(meetings)):
        before = ordered.loc[ordered["date"] < meeting, "value"]
        after = ordered.loc[ordered["date"] >= meeting, "value"]
        if not before.empty and not after.empty:
            rows.append((meeting, before.iloc[-1], after.iloc[0]))
    listed = {row[0] for row in rows}
    rows += [
        (change.date, change.previous, change.value)
        for change in rate_changes(ordered).itertuples()
        if change.date not in listed
    ]
    frame = pd.DataFrame(rows, columns=["date", "previous", "value"])
    frame["decision"] = "hold"
    frame.loc[frame["value"] > frame["previous"], "decision"] = "hike"
    frame.loc[frame["value"] < frame["previous"], "decision"] = "cut"
    return frame.sort_values("date").reset_index(drop=True)


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


def ratio_pct(
    numerator: pd.DataFrame, denominator: pd.DataFrame, of_total: bool = False
) -> pd.DataFrame:
    """date/value rows of numerator / denominator in %, on the dates both have.

    `of_total` adds the numerator to the denominator. The NPL ratio needs it: BDDK's loan table
    holds performing loans only, and the ratio is NPL / (loans + NPL), as in BDDK's reports.
    """
    merged = numerator[["date", "value"]].merge(
        denominator[["date", "value"]], on="date", suffixes=("_part", "_base")
    )
    base = merged["value_base"] + (merged["value_part"] if of_total else 0)
    ratio = pd.DataFrame({"date": merged["date"], "value": merged["value_part"] / base * 100})
    return ratio.sort_values("date").reset_index(drop=True)


# Alerts compare the latest weekly change with the series' own past year (median and MAD, which
# ignore the odd extreme week). A robust score of 5 flagged about 1.5% of weeks per series on
# the 2014-2026 history: rare enough to mean something, about one alert every two weeks over
# all 30 weekly series.
ALERT_THRESHOLD = 5.0
ALERT_WINDOW_WEEKS = 52
ALERT_MIN_HISTORY_WEEKS = 26
ALERT_COLUMNS = ["series_id", "date", "change", "typical", "spread", "score"]


def weekly_changes(values: pd.Series, in_points: bool) -> pd.Series:
    """Changes between weeks exactly 7 days apart: in % or, for rates, in points."""
    values = values.sort_index()
    change = values.diff() if in_points else values.pct_change() * 100
    consecutive = values.index.to_series().diff() == pd.Timedelta(days=7)
    return change[consecutive].dropna()


def unusual_changes(
    observations: pd.DataFrame,
    points_ids: Iterable[int] = (),
    threshold: float = ALERT_THRESHOLD,
) -> pd.DataFrame:
    """Series whose latest weekly change is far outside their own past year.

    score = (latest change - median) / (1.4826 * MAD) over the previous ALERT_WINDOW_WEEKS
    changes (1.4826 * MAD matches the standard deviation for normal data). A series needs
    ALERT_MIN_HISTORY_WEEKS of history, and a flat history (MAD 0) gives no score.
    `points_ids` are rates and ratios, whose changes are measured in points, not %.
    """
    points = set(points_ids)
    rows = []
    for series_id, group in observations.groupby("series_id", sort=True):
        values = group.set_index("date")["value"]
        changes = weekly_changes(values, in_points=series_id in points)
        if changes.empty or changes.index[-1] != values.index.max():
            continue  # the latest week has no change (a gap before it)
        history = changes.iloc[:-1].tail(ALERT_WINDOW_WEEKS)
        if len(history) < ALERT_MIN_HISTORY_WEEKS:
            continue
        typical = history.median()
        spread = 1.4826 * (history - typical).abs().median()
        if spread == 0:
            continue
        latest = changes.iloc[-1]
        score = (latest - typical) / spread
        if abs(score) >= threshold:
            rows.append((series_id, changes.index[-1], latest, typical, spread, score))
    return pd.DataFrame(rows, columns=ALERT_COLUMNS)


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


# --- selecting series by config (category, deflator, policy rate) ---

BANK_GROUPS = ("state_banks", "private_banks", "foreign_banks")


def with_categories(series: pd.DataFrame, config: SeriesConfig) -> pd.DataFrame:
    """Adds the config-only `category` and `alerts` flag of each series (not stored)."""
    specs = {(spec.source, spec.code): spec for spec in config.series}
    found = [specs.get((row.source, row.code)) for row in series.itertuples()]
    return series.assign(
        category=[spec.category if spec else None for spec in found],
        alerts=[spec.alerts if spec else True for spec in found],
    )


def alert_series(series: pd.DataFrame) -> pd.DataFrame:
    """The series checked for unusual weekly changes: weekly, not macro, alerts not off."""
    return series[
        (series["frequency"] == "weekly") & (series["module"] != "macro") & series["alerts"]
    ]


def spec_id(series: pd.DataFrame, spec: SeriesSpec | None) -> int | None:
    """Database id of a configured series, or None if it is not configured or not loaded."""
    if spec is None:
        return None
    ids = series.loc[(series["source"] == spec.source) & (series["code"] == spec.code), "id"]
    return int(ids.iloc[0]) if not ids.empty else None


def spec_observations(
    series: pd.DataFrame, observations: pd.DataFrame, spec: SeriesSpec | None
) -> pd.DataFrame:
    """date/value rows of a configured series such as the deflator (may be empty)."""
    rows = observations[observations["series_id"] == spec_id(series, spec)]
    return rows[["date", "value"]].reset_index(drop=True)


def category_id(series: pd.DataFrame, category: str, monetary: bool | None = None) -> int | None:
    """The first series of a category, optionally only TRY amounts (or only non-amounts)."""
    rows = series[series["category"] == category]
    if monetary is not None:
        rows = rows[rows["unit"].isin(MONETARY_UNITS) == monetary]
    return int(rows["id"].iloc[0]) if not rows.empty else None


# --- banking sector ratios ---


class Ratio(NamedTuple):
    """A ratio of two stored series, computed for display only (never stored)."""

    key: str  # i18n text key, e.g. "ratio_npl"
    category: str | None  # color of its chart
    values: pd.DataFrame  # date/value in %


def banking_ratios(series: pd.DataFrame, observations: pd.DataFrame) -> list[Ratio]:
    """NPL, deposit, capital, funding and securities ratios, overall and per bank group.

    Loan totals come from BDDK's loan table (module "credit"), so every ratio compares BDDK
    with BDDK. A ratio whose inputs are not loaded is left out.
    """
    bddk_loans = series[(series["source"] == "bddk") & (series["module"] == "credit")]
    banking = series[series["module"] == "banking"]

    def rows(series_id: int | None) -> pd.DataFrame | None:
        if series_id is None:
            return None
        found = observations[observations["series_id"] == series_id]
        return found if not found.empty else None

    def group(category: str, row: str, column: str = "3") -> int | None:
        keys = banking["code"].map(bddk_key)
        matches = banking[
            (banking["category"] == category)
            & (keys.map(lambda key: key.row) == row)
            & (keys.map(lambda key: key.column) == column)
        ]
        return int(matches["id"].iloc[0]) if not matches.empty else None

    def total(*categories: str, zero_if_missing: tuple[str, ...] = ()) -> pd.DataFrame | None:
        """Sum of every series in these categories, on the weeks all of them have.

        A series in `zero_if_missing` counts as 0 in weeks it leaves out: BDDK omits most
        weeks of "Due to the CBRT" before 2018-09-28, and the weeks it does send are 0.
        """
        chosen = banking[banking["category"].isin(categories)]
        found = observations[observations["series_id"].isin(chosen["id"])]
        if chosen.empty or found.empty:
            return None
        wide = found.pivot_table(index="date", columns="series_id", values="value")
        optional = chosen.loc[chosen["category"].isin(zero_if_missing), "id"]
        wide[wide.columns.intersection(optional)] = wide[
            wide.columns.intersection(optional)
        ].fillna(0)
        complete = wide.reindex(columns=chosen["id"]).dropna()
        return complete.sum(axis=1).rename("value").reset_index() if not complete.empty else None

    loans = rows(category_id(bddk_loans, "total"))
    deposits = rows(category_id(banking, "deposits"))
    equity = rows(category_id(banking, "equity"))
    definitions: list[tuple[str, str | None, pd.DataFrame | None, pd.DataFrame | None, bool]] = [
        ("ratio_npl", "npl", rows(category_id(banking, "npl")), loans, True),
        (
            "ratio_npl_consumer",
            "npl_consumer",
            rows(category_id(banking, "npl_consumer")),
            rows(category_id(bddk_loans, "consumer")),
            True,
        ),
        (
            "ratio_npl_commercial",
            "npl_commercial",
            rows(category_id(banking, "npl_commercial")),
            rows(category_id(bddk_loans, "commercial")),
            True,
        ),
    ]
    definitions += [
        (
            f"ratio_npl_{bank_group}",
            bank_group,
            rows(group(bank_group, "2.0.1")),
            rows(group(bank_group, "1.0.1")),
            True,
        )
        for bank_group in BANK_GROUPS
    ]
    definitions.append(
        (
            "ratio_fx_share",
            "fx_deposits",
            rows(category_id(banking, "fx_deposits")),
            deposits,
            False,
        )
    )
    definitions += [
        (
            f"ratio_fx_share_{bank_group}",
            bank_group,
            rows(group(bank_group, "4.0.1", column="2")),
            rows(group(bank_group, "4.0.1")),
            False,
        )
        for bank_group in BANK_GROUPS
    ]
    definitions += [
        ("ratio_loan_deposit", None, loans, deposits, False),
        # Capital: own funds against loans, and the net FX position (legal limit: +/-20%).
        ("ratio_equity_loans", "equity", equity, loans, False),
        (
            "ratio_fx_position_equity",
            "fx_position",
            rows(category_id(banking, "fx_position")),
            equity,
            False,
        ),
        # Funding beyond deposits: the CBRT, repo, foreign banks and bonds the banks issued.
        (
            "ratio_wholesale_funding",
            "foreign_bank_funding",
            total(
                "cbrt_funding",
                "repo_funding",
                "foreign_bank_funding",
                "issued_securities",
                zero_if_missing=("cbrt_funding",),
            ),
            deposits,
            False,
        ),
        (
            "ratio_reserves_deposits",
            "reserve_requirements",
            rows(category_id(banking, "reserve_requirements")),
            deposits,
            False,
        ),
        (
            "ratio_bonds_securities",
            "government_bonds",
            total("government_bonds"),
            rows(category_id(banking, "securities")),
            False,
        ),
    ]
    for bank_group in BANK_GROUPS:
        definitions.append(
            (
                f"ratio_loans_{bank_group}",
                bank_group,
                rows(group(bank_group, "1.0.1")),
                loans,
                False,
            )
        )
    for bank_group in BANK_GROUPS:
        definitions.append(
            (
                f"ratio_deposits_{bank_group}",
                bank_group,
                rows(group(bank_group, "4.0.1")),
                deposits,
                False,
            )
        )
    return [
        Ratio(key, category, ratio_pct(part, base, of_total))
        for key, category, part, base, of_total in definitions
        if part is not None and base is not None
    ]
