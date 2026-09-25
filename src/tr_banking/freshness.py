"""Detect series whose newest observation is older than their publishing rhythm allows."""

from collections.abc import Mapping
from datetime import date

import pandas as pd

# Weekly data (week ending Friday) is published the next Thursday. A run on Tuesday therefore
# sees data 11 days old and a run on Friday 7 days old; 13 days means one missed release.
MAX_AGE_DAYS: Mapping[str, int] = {"daily": 4, "weekly": 13, "monthly": 75}


def find_stale(
    latest: pd.DataFrame, today: date, max_age_days: Mapping[str, int] = MAX_AGE_DAYS
) -> pd.DataFrame:
    """Rows of `latest` (source, code, frequency, latest_date) that are too old or empty.

    Adds `age_days` (NaN for a series with no data at all, which always counts as stale).
    """
    ages = (pd.Timestamp(today) - pd.to_datetime(latest["latest_date"])).dt.days
    limits = latest["frequency"].map(max_age_days)
    if limits.isna().any():
        unknown = sorted(set(latest.loc[limits.isna(), "frequency"]))
        raise ValueError(f"no freshness limit for frequency {unknown}")
    stale = ages.isna() | (ages > limits)
    return latest.assign(age_days=ages)[stale].reset_index(drop=True)
