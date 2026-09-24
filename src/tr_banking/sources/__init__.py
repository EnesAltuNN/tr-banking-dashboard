"""Data source clients (EVDS and BDDK now; BKM and bank sites later).

Every source returns observations in the same long format: one row per (code, date, value).
The repository stores exactly this shape, so new sources plug in without storage changes.
"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

import pandas as pd

OBSERVATION_COLUMNS = ["code", "date", "value"]


class ObservationClient(Protocol):
    """What the pipeline needs from a source client."""

    def fetch_observations(self, codes: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Return OBSERVATION_COLUMNS rows for the given codes and inclusive date range."""
        ...
