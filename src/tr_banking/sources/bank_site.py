"""Bank websites (module 3): TRY deposit rates from the banks' own rate tables.

Only banks whose rate tables are in the page's HTML are read (checked 2026-10-07): Ziraat
(branch and internet-branch tables) and İş Bankası (the İşCep / internet campaign table).
Halkbank, Yapı Kredi and Garanti BBVA fill their tables with JavaScript and are left out on
purpose: no browser automation and no undocumented endpoints.

Series code "<bank>:<table>:<days>:<amount>", e.g. "ziraat:internet:32:100000": the yearly
simple rate (%) for <amount> TRY deposited for <days> days, read from the cell whose day range
and amount bracket contain them. The pages show today's rates only, so each fetch stores one
value dated today and the history starts with the first fetch.

One request per page per run, robots.txt checked first, an identifying User-Agent.
"""

import logging
import re
import urllib.robotparser
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import NamedTuple, Self
from urllib.parse import urlsplit

import httpx
import pandas as pd

from tr_banking.sources import OBSERVATION_COLUMNS
from tr_banking.sources.bkm import USER_AGENT, Grid, TableParser, to_grid
from tr_banking.sources.common import SourceApiError, request_with_retries, save_raw_response

logger = logging.getLogger(__name__)


class Table(NamedTuple):
    url: str
    marker: str  # the rate table is the first <table> after this text


ZIRAAT_URL = "https://www.ziraatbank.com.tr/tr/fiyatlar-ve-oranlar"
ISBANK_URL = "https://www.isbank.com.tr/kampanyali-mevduat-oranlari"
TABLES = {
    ("ziraat", "branch"): Table(ZIRAAT_URL, 'data-id="rdBranchVadeliTL"'),
    ("ziraat", "internet"): Table(ZIRAAT_URL, 'data-id="rdIntBranchVadeliTL"'),
    ("isbank", "campaign"): Table(ISBANK_URL, 'class="isb_tableT7'),
}
DAYS = re.compile(r"^(\d+)\s*-\s*(\d+)\s*gün$|^(\d+)\s*üzeri\s*gün$", re.IGNORECASE)
AMOUNT_RANGE = re.compile(r"^([\d.,]+)\s*-\s*([\d.,]+)\s*TL$", re.IGNORECASE)
AMOUNT_FROM = re.compile(r"^([\d.,]+)\s*TL\s*(?:ve\s*)?üzeri$", re.IGNORECASE)


class BankSiteApiError(SourceApiError):
    """A bank page could not be fetched (HTTP error, network problem, robots.txt says no)."""


class BankSiteResponseError(ValueError):
    """A bank page does not look as expected (table gone, cell not found, odd value)."""


class SeriesKey(NamedTuple):
    bank: str
    table: str
    days: int
    amount: float


def parse_series_code(code: str) -> SeriesKey:
    parts = code.split(":")
    if len(parts) != 4 or (parts[0], parts[1]) not in TABLES:
        raise ValueError(
            f"invalid bank series code {code!r}, expected <bank>:<table>:<days>:<amount> with "
            f"<bank>:<table> one of {sorted(':'.join(key) for key in TABLES)}"
        )
    try:
        return SeriesKey(parts[0], parts[1], int(parts[2]), float(parts[3]))
    except ValueError:
        raise ValueError(f"invalid bank series code {code!r}: days, amount are numbers") from None


class BankSiteClient:
    """Reads each needed bank page once and stores today's rates."""

    def __init__(
        self,
        raw_dir: Path,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_wait: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        today: date | None = None,
    ) -> None:
        self._raw_dir = raw_dir / "bank_site"
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._today = today or date.today()
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
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
        """Today's rate for every code; nothing if today is outside [start, end]."""
        if not codes:
            raise ValueError("at least one series code is required")
        keys = {code: parse_series_code(code) for code in codes}  # validate before requests
        if not start <= self._today <= end:
            logger.info("bank sites: only today's rates exist; %s..%s has none", start, end)
            return pd.DataFrame(columns=OBSERVATION_COLUMNS)
        pages: dict[str, str] = {}
        rows = []
        for code, key in keys.items():
            table = TABLES[(key.bank, key.table)]
            if table.url not in pages:
                pages[table.url] = self._fetch(table.url, key.bank)
            grid = table_after(pages[table.url], table.marker)
            rows.append((code, self._today, find_rate(grid, key.days, key.amount)))
        return pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)

    def _fetch(self, url: str, bank: str) -> str:
        self._check_robots(url)
        response = request_with_retries(
            self._http,
            "GET",
            url,
            label=f"bank site {bank}",
            error_cls=BankSiteApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
        )
        save_raw_response(self._raw_dir, response.content, bank, suffix=".html")
        return response.text

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        response = request_with_retries(
            self._http,
            "GET",
            robots_url,
            label="robots.txt",
            error_cls=BankSiteApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
        )
        robots = urllib.robotparser.RobotFileParser(robots_url)
        robots.parse(response.text.splitlines())
        if not robots.can_fetch(USER_AGENT, url):
            raise BankSiteApiError(f"robots.txt of {parts.netloc} disallows {url}")


def table_after(html: str, marker: str) -> Grid:
    """The first table from the tag that holds `marker` on, as a grid of cell texts."""
    start = html.find(marker)
    if start < 0:
        raise BankSiteResponseError(f"rate table marker {marker!r} not found on the page")
    # Back to the "<" of the tag the marker sits in, so a marker inside <table ...> still counts.
    tables = TableParser.tables_of(html[html.rfind("<", 0, start) :])
    if not tables:
        raise BankSiteResponseError(f"no table after {marker!r}")
    return to_grid(tables[0])


def find_rate(grid: Grid, days: int, amount: float) -> float:
    """The rate (%) in the row whose day range holds `days` and the column whose amount bracket
    holds `amount`; exactly one of each must match."""
    header = grid[0]
    columns = [index for index, label in enumerate(header) if index and amount_in(label, amount)]
    rows = [row for row in grid[1:] if row and days_in(row[0], days)]
    if len(columns) != 1 or len(rows) != 1:
        raise BankSiteResponseError(
            f"{len(rows)} rows for {days} days and {len(columns)} columns for {amount:,.0f} TRY "
            f"(header {header})"
        )
    return parse_rate(rows[0][columns[0]])


def days_in(label: str, days: int) -> bool:
    """'32 - 45 gün' / '367 Üzeri Gün' holds `days`; other rows ('Vadesiz', ...) never do."""
    match = DAYS.match(" ".join(label.split()))
    if not match:
        return False
    low, high, open_low = match.groups()
    if open_low:
        return days >= int(open_low)
    return int(low) <= days <= int(high)


def amount_in(label: str, amount: float) -> bool:
    """'100.000 - 499.999,99 TL' / '10.000.001 TL Üzeri' / '500.000,00 TL ve üzeri'."""
    label = " ".join(label.split())
    if match := AMOUNT_RANGE.match(label):
        return turkish_number(match[1]) <= amount <= turkish_number(match[2])
    if match := AMOUNT_FROM.match(label):
        return amount >= turkish_number(match[1])
    return False


def turkish_number(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def parse_rate(text: str) -> float:
    """'%31.00' (Ziraat), '% 36,50' or '% 3 7,85 \\u200b' (İş Bankası, typed by hand) -> float.

    A comma is the decimal mark when present; Ziraat writes a dot. Fails outside 0-100%.
    """
    digits = re.sub(r"[\s%​]", "", text)
    if not re.fullmatch(r"\d+([.,]\d+)?", digits):
        raise BankSiteResponseError(f"rate cell {text!r} is not a number")
    value = float(digits.replace(",", "."))
    if not 0 <= value <= 100:
        raise BankSiteResponseError(f"rate {value} is outside 0-100%")
    return value
