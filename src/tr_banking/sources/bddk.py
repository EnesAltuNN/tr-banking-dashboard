"""BDDK weekly bulletin (Haftalik Bulten) source: client and parser.

BDDK has no official API. The bulletin's "advanced" page loads charts from a JSON endpoint
that returns one row of a table (e.g. housing loans) for one bank group over a date range;
that endpoint is what we call. Data goes back to 2014-01-03.

Series codes in config are `<row id>:<bank group>:<currency>:<column>`, e.g.
`1.0.4:10001:TRY:3` = housing loans, whole sector, in TRY, total (TL + FX) column.
"""

import logging
import math
import re
import ssl
import time
from collections.abc import Sequence
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, NamedTuple, Self

import certifi
import httpx
import pandas as pd

from tr_banking.sources import OBSERVATION_COLUMNS
from tr_banking.sources.common import SourceApiError, request_with_retries, save_raw_response

logger = logging.getLogger(__name__)

BDDK_BASE_URL = "https://www.bddk.org.tr/BultenHaftalik/"
SERIES_ENDPOINT = "tr/Gelismis/KiyaslamaJsonGetir"
# Requests and responses use D.MM.YYYY; strptime also accepts the unpadded day ("3.01.2014").
DATE_FORMAT = "%d.%m.%Y"
SERIES_CODE_PATTERN = re.compile(
    r"^(?P<row>\d+(?:\.\d+)+):(?P<group>\d{5}):(?P<currency>TRY|USD):(?P<column>[123])$"
)
BDDK_INTERMEDIATE_CERT = files("tr_banking.sources").joinpath(
    "certs/globalsign_rsa_ov_ssl_ca_2018.pem"
)


class BddkApiError(SourceApiError):
    """The BDDK bulletin could not be reached or answered with an error status."""


class BddkResponseError(ValueError):
    """The BDDK response does not have the shape we expect."""


class BddkSeriesKey(NamedTuple):
    row: str  # table row id, e.g. "1.0.4" (table 1 = loans)
    group: str  # bank group (tarafKodu), e.g. "10001" = sector
    currency: str  # TRY or USD
    column: str  # 1 = TL, 2 = FX, 3 = total


def bddk_ssl_context() -> ssl.SSLContext:
    """certifi's root certificates plus the one intermediate BDDK forgets to send.

    www.bddk.org.tr serves only its own certificate. Browsers download the missing
    intermediate by themselves; OpenSSL (Python on Linux, e.g. GitHub Actions) does not.
    Trusting that public intermediate lets normal verification succeed on every OS.
    Verification is never turned off. If BDDK renews with another issuer, requests fail with
    a certificate error: fetch the new intermediate (see the leaf's "CA Issuers" URL).
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=BDDK_INTERMEDIATE_CERT.read_text(encoding="ascii"))
    return context


def parse_series_code(code: str) -> BddkSeriesKey:
    match = SERIES_CODE_PATTERN.match(code)
    if not match:
        raise ValueError(f"invalid BDDK series code {code!r}, expected row:group:currency:column")
    return BddkSeriesKey(**match.groupdict())


class BddkClient:
    """Fetches bulletin series one request per code, politely spaced, saving raw responses."""

    def __init__(
        self,
        raw_dir: Path,
        *,
        base_url: str = BDDK_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_wait: float = 2.0,
        request_interval: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._raw_dir = raw_dir / "bddk"
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._request_interval = request_interval
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            verify=bddk_ssl_context(),
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
        if not codes:
            raise ValueError("at least one series code is required")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        keys = [parse_series_code(code) for code in codes]  # validate all before any request

        logger.info("BDDK: requesting %d series from %s to %s", len(codes), start, end)
        frames = []
        for index, (code, key) in enumerate(zip(codes, keys, strict=True)):
            if index:
                time.sleep(self._request_interval)  # a public website, not an API: go slowly
            frames.append(self._fetch_one(code, key, start, end))
        return pd.concat(frames, ignore_index=True)

    def _fetch_one(self, code: str, key: BddkSeriesKey, start: date, end: date) -> pd.DataFrame:
        form = {
            "dil": "tr",
            "baslangicTarihi": start.strftime(DATE_FORMAT),
            "bitisTarihi": end.strftime(DATE_FORMAT),
            "id": key.row,
            "parabirimi": key.currency,
            "sutun": key.column,
            "tarafKodu": key.group,
        }
        response = request_with_retries(
            self._http,
            "POST",
            SERIES_ENDPOINT,
            label="BDDK",
            error_cls=BddkApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
            data=form,
        )
        raw_path = save_raw_response(
            self._raw_dir, response.content, f"{code}_{start:%Y%m%d}_{end:%Y%m%d}"
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BddkResponseError(f"response is not valid JSON (see {raw_path})") from exc
        return parse_bddk_response(payload, code)


def parse_bddk_response(payload: Any, code: str) -> pd.DataFrame:
    """Convert {"Baslik", "XEkseni": [dates], "YEkseni": [values]} into (code, date, value) rows.

    An unknown row or bank group still returns HTTP 200 with empty lists, so an empty series is
    an error here. Null values are dropped; anything else unexpected raises BddkResponseError.
    """
    if not isinstance(payload, dict):
        raise BddkResponseError("response is not a JSON object")
    dates, values = payload.get("XEkseni"), payload.get("YEkseni")
    if not isinstance(dates, list) or not isinstance(values, list):
        raise BddkResponseError("response has no XEkseni/YEkseni lists")
    if len(dates) != len(values):
        raise BddkResponseError(f"series {code}: {len(dates)} dates but {len(values)} values")

    rows = [
        (code, _parse_date(raw_date), _parse_value(raw_value, code))
        for raw_date, raw_value in zip(dates, values, strict=True)
        if raw_value is not None
    ]
    if not rows:
        raise BddkResponseError(
            f"series {code} returned no values (unknown row or bank group, or empty range)"
        )
    logger.debug("series %s (%s): %d values", code, payload.get("Baslik"), len(rows))

    frame = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
    if frame["date"].duplicated().any():
        raise BddkResponseError(f"series {code} has duplicate dates")
    return frame.sort_values("date", ignore_index=True)


def _parse_date(raw: Any) -> date:
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except (TypeError, ValueError) as exc:
        raise BddkResponseError(f"unexpected date {raw!r}, expected D.MM.YYYY") from exc


def _parse_value(raw: Any, code: str) -> float:
    # BDDK sends JSON numbers; bool is an int subclass in Python, so reject it explicitly.
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise BddkResponseError(f"series {code}: non-numeric value {raw!r}")
    if not math.isfinite(raw):
        raise BddkResponseError(f"series {code}: non-finite value {raw!r}")
    return float(raw)
