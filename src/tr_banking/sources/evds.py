"""TCMB EVDS (evds3) source: HTTP client and parsing into long-format observations."""

import logging
import math
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

import httpx
import pandas as pd
from pydantic import SecretStr

from tr_banking.sources import OBSERVATION_COLUMNS
from tr_banking.sources.common import SourceApiError, request_with_retries, save_raw_response

logger = logging.getLogger(__name__)

DATE_COLUMN = "Tarih"
# Columns EVDS adds next to the series values; any other column must be a requested series.
META_COLUMNS = frozenset({DATE_COLUMN, "YEARWEEK", "UNIXTIME"})
# Daily and weekly series use DD-MM-YYYY (monthly series use another format; not supported yet).
# Request parameters use the same format.
DATE_FORMAT = "%d-%m-%Y"

# Codes are embedded in the URL path, so only allow characters EVDS codes actually use.
SERIES_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.]+$")


class EvdsApiError(SourceApiError):
    """EVDS could not be reached or answered with an error status."""


class EvdsResponseError(ValueError):
    """The EVDS response does not have the shape we expect."""


class EvdsClient:
    """Fetches series data from EVDS3 and saves every raw response for debugging."""

    def __init__(
        self,
        api_key: SecretStr,
        base_url: str,
        raw_dir: Path,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_wait: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._raw_dir = raw_dir / "evds"
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        # The key goes only in a header (never the URL), and redirects are not followed,
        # so the key is never sent to a host we did not choose.
        self._http = httpx.Client(
            headers={"key": api_key.get_secret_value()},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def fetch_observations(self, codes: Sequence[str], start: date, end: date) -> pd.DataFrame:
        url = build_series_url(self._base_url, codes, start, end)
        logger.info("EVDS: requesting %d series from %s to %s", len(codes), start, end)
        response = request_with_retries(
            self._http,
            "GET",
            url,
            label="EVDS",
            error_cls=EvdsApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
            forbidden_hint=": check EVDS_API_KEY",
        )
        raw_path = save_raw_response(
            self._raw_dir, response.content, f"{start:%Y%m%d}_{end:%Y%m%d}"
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise EvdsResponseError(f"response is not valid JSON (see {raw_path})") from exc
        return parse_evds_response(payload, codes)


def build_series_url(base_url: str, codes: Sequence[str], start: date, end: date) -> str:
    """EVDS expects parameters appended to the path without a '?'."""
    if not codes:
        raise ValueError("at least one series code is required")
    if bad := [code for code in codes if not SERIES_CODE_PATTERN.match(code)]:
        raise ValueError(f"invalid series codes: {bad}")
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    params = (
        f"series={'-'.join(codes)}"
        f"&startDate={start.strftime(DATE_FORMAT)}"
        f"&endDate={end.strftime(DATE_FORMAT)}"
        "&type=json"
    )
    return f"{base_url.rstrip('/')}/{params}"


def evds_column_name(code: str) -> str:
    """EVDS returns each series as a column whose name has dots replaced by underscores."""
    return code.replace(".", "_")


def parse_evds_response(payload: Any, codes: Sequence[str]) -> pd.DataFrame:
    """Convert an EVDS data response into rows of (code, date, value).

    Null values (weeks outside a series' range) are dropped. Anything unexpected raises
    EvdsResponseError instead of silently producing partial data.
    """
    items = _get_items(payload)
    columns = {evds_column_name(code): code for code in codes}
    _check_columns(items, set(columns))
    frames = [_series_frame(items, column, code) for column, code in columns.items()]
    return pd.concat(frames, ignore_index=True)


def _get_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise EvdsResponseError("response has no 'items' list")
    items = payload["items"]
    if not items:
        raise EvdsResponseError("response contains no observations")
    return items


def _check_columns(items: list[dict[str, Any]], series_columns: set[str]) -> None:
    required = series_columns | {DATE_COLUMN}
    allowed = series_columns | META_COLUMNS
    for item in items:
        keys = set(item)
        if missing := required - keys:
            raise EvdsResponseError(f"missing columns: {sorted(missing)}")
        if unexpected := keys - allowed:
            raise EvdsResponseError(f"unexpected columns: {sorted(unexpected)}")


def _series_frame(items: list[dict[str, Any]], column: str, code: str) -> pd.DataFrame:
    rows = [
        (code, _parse_date(item[DATE_COLUMN]), _parse_value(item[column], code))
        for item in items
        if item[column] is not None
    ]
    if not rows:
        raise EvdsResponseError(f"series {code} returned no values in the requested range")
    logger.debug("series %s: %d values, %d nulls dropped", code, len(rows), len(items) - len(rows))

    frame = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
    if frame["date"].duplicated().any():
        raise EvdsResponseError(f"series {code} has duplicate dates")
    return frame.sort_values("date", ignore_index=True)


def _parse_date(raw: Any) -> date:
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except (TypeError, ValueError) as exc:
        raise EvdsResponseError(f"unexpected date {raw!r}, expected DD-MM-YYYY") from exc


def _parse_value(raw: Any, code: str) -> float:
    # EVDS sends numbers as strings, e.g. "833152530.00000000".
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise EvdsResponseError(f"series {code}: non-numeric value {raw!r}") from exc
    if not math.isfinite(value):
        raise EvdsResponseError(f"series {code}: non-finite value {raw!r}")
    return value
