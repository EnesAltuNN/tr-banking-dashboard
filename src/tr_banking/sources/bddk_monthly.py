"""BDDK monthly bulletin (Aylık Bülten): BDDK's own sector ratios, one request per table and month.

There is no official API. The bulletin page loads its tables from a JSON endpoint (verified
2026-10-03): a POST of tabloNo, yil, ay, paraBirimi=TL and taraf=10001 (sector) returns
{"success": true, "Json": {"data": {"rows": [{"cell": [group, no, label, _, value]}]}}}.
An unpublished month returns success false with "... en son N ayına ait veri bulunmaktadır!".

Row numbers shift over the years (the ratios table had 31 rows until 2017 and 32 since), so a
series is found by its row label, which has stayed the same. Series code: "<table>:<label>" for
the sector, "<table>@<taraf>:<label>" for a bank group, e.g.
"15@10009:Yasal Özkaynak / Risk Ağırlıklı Kalemler Toplamı (%)". One request carries every
group of a table; the response names each group (cell 0).
"""

import logging
import time
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Self

import httpx
import pandas as pd

from tr_banking.sources import OBSERVATION_COLUMNS
from tr_banking.sources.bddk import bddk_ssl_context
from tr_banking.sources.common import (
    SourceApiError,
    month_end,
    months_between,
    request_with_retries,
    save_raw_response,
)

logger = logging.getLogger(__name__)

BDDK_MONTHLY_URL = "https://www.bddk.org.tr/BultenAylik/tr/Home/BasitRaporGetir"
SECTOR = "10001"
# taraf -> the group name in the response's first cell, from the page's own list (verified
# 2014-01, 2019-06, 2026-08). Mevduat-* are deposit banks by ownership.
GROUP_NAMES = {
    "10001": "Sektör",
    "10009": "Mevduat-Kamu",
    "10008": "Mevduat-Yerli Özel",
    "10010": "Mevduat-Yabancı",
    "10003": "Katılım",
}
CHUNK_MONTHS = 12
# What the endpoint says for a month that is not published yet, or before its first month.
NOT_PUBLISHED_MARKERS = ("en son", "başlamaktadır")


class BddkMonthlyApiError(SourceApiError):
    """The monthly bulletin endpoint failed (HTTP error or network problem)."""


class BddkMonthlyResponseError(ValueError):
    """The response does not look as expected (missing row, odd layout)."""


def parse_series_code(code: str) -> tuple[str, str, str]:
    """'15:<label>' -> ('15', '10001', '<label>'); '15@10009:<label>' -> ('15', '10009', ...)."""
    head, _, label = code.partition(":")
    table, _, taraf = head.partition("@")
    taraf = taraf or SECTOR
    if not table.isdigit() or not label.strip() or taraf not in GROUP_NAMES:
        raise ValueError(
            f"invalid BDDK monthly series code {code!r}, expected <table>[@<taraf>]:<label> "
            f"with taraf one of {sorted(GROUP_NAMES)}"
        )
    return table, taraf, label.strip()


class BddkMonthlyClient:
    """Fetches one table per month, politely spaced, saving raw responses."""

    def __init__(
        self,
        raw_dir: Path,
        *,
        base_url: str = BDDK_MONTHLY_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_wait: float = 2.0,
        request_interval: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._raw_dir = raw_dir / "bddk_monthly"
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._request_interval = request_interval
        self._http = httpx.Client(
            timeout=timeout,
            verify=bddk_ssl_context(),  # same host as the weekly bulletin: bundled intermediate
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
        """Values of `codes` for every published month from start's month to end's month."""
        return pd.concat(self.iter_observations(codes, start, end), ignore_index=True)

    def iter_observations(
        self, codes: Sequence[str], start: date, end: date, chunk_months: int = CHUNK_MONTHS
    ) -> Iterator[pd.DataFrame]:
        """Like fetch_observations, but yields the rows of every `chunk_months` months.

        Fails at the end if no month in the whole range is published.
        """
        if not codes:
            raise ValueError("at least one series code is required")
        keys = {code: parse_series_code(code) for code in codes}  # validate before requests
        tables = sorted({table for table, _, _ in keys.values()})
        months = months_between(start, end)
        logger.info("BDDK monthly: requesting %d months from %s to %s", len(months), start, end)

        rows: list[tuple[str, date, float]] = []
        published = requests = 0
        for index, (year, month) in enumerate(months):
            for table in tables:
                if requests:
                    time.sleep(self._request_interval)  # a public website: go slowly
                requests += 1
                tarafs = sorted(
                    {taraf for code_table, taraf, _ in keys.values() if code_table == table}
                )
                values = self._fetch(table, tarafs, year, month)
                if values is None:
                    logger.info("BDDK monthly: %d-%02d is not published", year, month)
                    continue
                published += 1
                day = month_end(year, month)
                for code, (code_table, taraf, label) in keys.items():
                    if code_table != table:
                        continue
                    key = (GROUP_NAMES[taraf], label)
                    if key not in values:
                        raise BddkMonthlyResponseError(
                            f"row {key} is missing from table {table} for {year}-{month:02d}"
                        )
                    if values[key] is not None:
                        rows.append((code, day, values[key]))
            if rows and ((index + 1) % chunk_months == 0 or index + 1 == len(months)):
                yield pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
                rows = []
        if not published:
            raise BddkMonthlyResponseError(
                f"no BDDK monthly data published between {start} and {end}"
            )

    def _fetch(
        self, table: str, tarafs: list[str], year: int, month: int
    ) -> dict[tuple[str, str], float | None] | None:
        form = {"tabloNo": table, "yil": year, "ay": month, "paraBirimi": "TL", "taraf": tarafs}
        response = request_with_retries(
            self._http,
            "POST",
            self._base_url,
            label="BDDK monthly",
            error_cls=BddkMonthlyApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
            data=form,
        )
        save_raw_response(self._raw_dir, response.content, f"{table}_{year}{month:02d}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BddkMonthlyResponseError("BDDK monthly did not return JSON") from exc
        return parse_report(payload, table)


def parse_report(payload: Any, table: str) -> dict[tuple[str, str], float | None] | None:
    """{(group name, row label): value} of a one-value table, or None if not published."""
    if not isinstance(payload, dict):
        raise BddkMonthlyResponseError("BDDK monthly response is not a JSON object")
    if not payload.get("success"):
        error = str(payload.get("error") or "")
        if any(marker in error for marker in NOT_PUBLISHED_MARKERS):
            return None
        raise BddkMonthlyResponseError(f"BDDK monthly table {table} failed: {error or 'no reason'}")
    try:
        rows = payload["Json"]["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise BddkMonthlyResponseError(f"table {table} has no rows") from exc
    values: dict[tuple[str, str], float | None] = {}
    for row in rows:
        cell = row.get("cell") if isinstance(row, dict) else None
        if not isinstance(cell, list) or len(cell) != 5:
            raise BddkMonthlyResponseError(
                f"table {table}: expected [group, no, label, _, value] cells, got {cell!r}"
            )
        key, raw = (str(cell[0]).strip(), str(cell[2]).strip()), cell[4]
        if raw in (None, ""):
            values[key] = None
            continue
        try:
            values[key] = float(raw)
        except (TypeError, ValueError) as exc:
            raise BddkMonthlyResponseError(f"table {table}: {key} is {raw!r}") from exc
    if not values:
        raise BddkMonthlyResponseError(f"table {table} is empty")
    return values
