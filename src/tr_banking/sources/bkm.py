"""BKM (Bankalararası Kart Merkezi) monthly card statistics: client and parser.

BKM has no API. It publishes one statistics page per month from 2017-01; the page's "Excel"
download (`xls=1`) is really an HTML table, parsed here with the standard library. Cells are
located by their row and column labels, never by position, so a new row or column fails
loudly instead of shifting values. The units are part of the labels ("İşlem Tutarı (Milyon
TL)"), so a unit change fails too.

Series codes in config:
- `cards:<card>`: number of cards, e.g. `cards:credit` (KART SAYILARI).
- `txn:<card>:<usage>:<measure>:<kind>`: İŞLEM ADET VE TUTARLARI, e.g.
  `txn:credit:domestic:amount:shopping`. The card `credit+debit` sums both card types.
- `vpos:<channel>:<measure>`: Sanal POS İşlemleri, e.g. `vpos:internet:amount`.
"""

import logging
import re
import time
from collections.abc import Iterator, Sequence
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple, Self

import httpx
import pandas as pd

from tr_banking.sources import OBSERVATION_COLUMNS
from tr_banking.sources.common import (
    SourceApiError,
    month_end,
    request_with_retries,
    save_raw_response,
)

logger = logging.getLogger(__name__)

BKM_URL = "https://bkm.com.tr/secilen-aya-ait-istatistikler/"
# Rows are handed to the pipeline every 12 requested months, so an interrupted backfill (about
# 117 pages, 1 s apart) keeps what it has downloaded.
CHUNK_MONTHS = 12
FIRST_MONTH = (2017, 1)  # earlier months return the "pick a date" page
# Shown instead of the tables for months that are not published (yet).
NOT_PUBLISHED_TEXT = "tarih seçiniz"
USER_AGENT = "tr-banking-dashboard (+https://github.com/EnesAltuNN/tr-banking-dashboard)"

# Labels exactly as they appear on the page (after whitespace normalization).
CARD_COUNT_TITLE = "KART SAYILARI"
CARD_TOTALS = {"credit": "Toplam Kredi Kartı", "debit": "Toplam Banka Kartı"}
CARD_TYPES = {"credit": "Kredi Kartı", "debit": "Banka Kartı"}
USAGES = {
    "domestic": "Yerli Kartların Yurt İçi Kullanımı",
    "abroad": "Yerli Kartların Yurtdışı Kullanımı",
    "foreign": "Yabancı Kartların Yurt İçi Kullanımı",
    "domestic_all": "Yerli Kartların Yurt İçi ve Yurtdışı Kullanımı",
    "in_country": "Yerli ve Yabancı Kartların Yurt İçi Kullanımı",
}
MEASURES = {"count": "İşlem Adedi", "amount": "İşlem Tutarı (Milyon TL)"}
KINDS = {"shopping": "Alışveriş", "cash": "Nakit Çekme", "total": "Toplam"}
VPOS_TITLE = "Sanal POS İşlemleri"
VPOS_CHANNELS = {
    "internet": "İnternetten Kartlı Ödemeler",
    "mail_phone": "Mektup / Telefonla Yapılan Kartlı Ödemeler",
}

Grid = list[list[str]]


class BkmApiError(SourceApiError):
    """The BKM website could not be reached or answered with an error status."""


class BkmResponseError(ValueError):
    """The BKM page does not have the shape we expect."""


class Cell(NamedTuple):
    """A table cell addressed by labels: the row's leading labels and its column's labels."""

    row: tuple[str, ...]
    column: tuple[str, ...]


def parse_series_code(code: str) -> list[Cell]:
    """The cells whose sum is the series' value, e.g. two cells for `credit+debit`."""
    parts = code.split(":")
    try:
        match parts:
            case ["cards", card]:
                return [Cell((CARD_TOTALS[card],), (CARD_COUNT_TITLE,))]
            case ["txn", cards, usage, measure, kind]:
                return [
                    Cell((CARD_TYPES[card], USAGES[usage]), (MEASURES[measure], KINDS[kind]))
                    for card in cards.split("+")
                ]
            case ["vpos", channel, measure]:
                return [Cell((VPOS_TITLE, VPOS_CHANNELS[channel]), (MEASURES[measure],))]
    except KeyError as exc:
        raise ValueError(f"invalid BKM series code {code!r}: unknown part {exc}") from None
    raise ValueError(
        f"invalid BKM series code {code!r}, expected cards:<card>, "
        "txn:<card>:<usage>:<measure>:<kind> or vpos:<channel>:<measure>"
    )


class BkmClient:
    """Fetches one statistics page per month, politely spaced, saving raw responses."""

    def __init__(
        self,
        raw_dir: Path,
        *,
        base_url: str = BKM_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_wait: float = 2.0,
        request_interval: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._raw_dir = raw_dir / "bkm"
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._request_interval = request_interval
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
        """Values of `codes` for every published month from start's month to end's month."""
        return pd.concat(self.iter_observations(codes, start, end), ignore_index=True)

    def iter_observations(
        self, codes: Sequence[str], start: date, end: date, chunk_months: int = CHUNK_MONTHS
    ) -> Iterator[pd.DataFrame]:
        """Like fetch_observations, but yields the rows of every `chunk_months` requested months.

        Fails at the end if no month in the whole range is published.
        """
        if not codes:
            raise ValueError("at least one series code is required")
        cells = {code: parse_series_code(code) for code in codes}  # validate before requests
        start = max(start, date(*FIRST_MONTH, 1))
        months = months_between(start, end)
        logger.info("BKM: requesting %d months from %s to %s", len(months), start, end)

        rows: list[tuple[str, date, float]] = []
        published = 0
        for index, (year, month) in enumerate(months):
            if index:
                time.sleep(self._request_interval)  # a public website, not an API: go slowly
            grids = self._fetch_month(year, month)
            if grids is None:
                logger.info("BKM: %d-%02d is not published yet", year, month)
            else:
                day = month_end(year, month)
                rows += [(code, day, sum_cells(grids, parts)) for code, parts in cells.items()]
                published += 1
            if rows and ((index + 1) % chunk_months == 0 or index + 1 == len(months)):
                yield pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
                rows = []
        if not published:
            raise BkmResponseError(f"no BKM statistics published between {start} and {end}")

    def _fetch_month(self, year: int, month: int) -> list[Grid] | None:
        params = {"filter_year": year, "filter_month": month, "List": "Listele", "xls": 1}
        response = request_with_retries(
            self._http,
            "GET",
            self._base_url,
            label="BKM",
            error_cls=BkmApiError,
            max_retries=self._max_retries,
            retry_wait=self._retry_wait,
            params=params,
        )
        save_raw_response(self._raw_dir, response.content, f"{year}{month:02d}", suffix=".html")
        return parse_bkm_page(response.content.decode("utf-8"))


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """(year, month) from start's month to end's month, inclusive; empty if start > end."""
    first, last = start.year * 12 + start.month - 1, end.year * 12 + end.month - 1
    return [(index // 12, index % 12 + 1) for index in range(first, last + 1)]


def parse_bkm_page(html: str) -> list[Grid] | None:
    """All tables of a month's page as grids, or None if the month is not published."""
    grids = [to_grid(rows) for rows in TableParser.tables_of(html)]
    if any(CARD_COUNT_TITLE in row for grid in grids for row in grid):
        return grids
    if NOT_PUBLISHED_TEXT in html_text(grids):
        return None
    raise BkmResponseError("page has neither the statistics tables nor the 'pick a date' text")


def sum_cells(grids: list[Grid], cells: list[Cell]) -> float:
    return sum(find_value(grids, cell) for cell in cells)


def find_value(grids: list[Grid], cell: Cell) -> float:
    """The single numeric cell in the row starting with cell.row, under all of cell.column."""
    matches = []
    for grid in grids:
        for row in grid:
            if tuple(row[: len(cell.row)]) != cell.row:
                continue
            for column, raw in enumerate(row):
                labels = {line[column] for line in grid if column < len(line)}
                if set(cell.column) <= labels and is_number(raw):
                    matches.append(raw)
    if len(matches) != 1:
        found = "no" if not matches else f"{len(matches)}"
        raise BkmResponseError(f"{found} cells found for row {cell.row} / column {cell.column}")
    return parse_number(matches[0])


NUMBER = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$")


def is_number(raw: str) -> bool:
    return bool(NUMBER.match(raw))


def parse_number(raw: str) -> float:
    """Turkish format: '2.450.238,01' -> 2450238.01, '151.730.027' -> 151730027.0."""
    if not is_number(raw):
        raise BkmResponseError(f"non-numeric value {raw!r}")
    return float(raw.replace(".", "").replace(",", "."))


def normalize(text: str) -> str:
    """Collapse whitespace; 'İşlem Tutarı(Milyon TL)' -> 'İşlem Tutarı (Milyon TL)'."""
    return re.sub(r"\s*\(", " (", " ".join(text.split())).strip()


def html_text(grids: list[Grid]) -> str:
    return " ".join(cell for grid in grids for row in grid for cell in row)


class RawCell(NamedTuple):
    text: str
    colspan: int
    rowspan: int


class TableParser(HTMLParser):
    """Collects every <table> (nested ones separately) as rows of RawCell."""

    def __init__(self) -> None:
        super().__init__()  # convert_charrefs: '&#305;' arrives as 'ı'
        self.tables: list[list[list[RawCell]]] = []
        self._open_tables: list[list[list[RawCell]]] = []
        self._open_rows: list[list[RawCell]] = []
        self._open_cells: list[tuple[list[str], int, int]] = []

    @classmethod
    def tables_of(cls, html: str) -> list[list[list[RawCell]]]:
        parser = cls()
        parser.feed(html)
        parser.close()
        return parser.tables

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._open_tables.append([])
        elif tag == "tr":
            self._open_rows.append([])
        elif tag in ("td", "th"):
            spans = dict(attrs)
            self._open_cells.append(
                ([], int(spans.get("colspan") or 1), int(spans.get("rowspan") or 1))
            )

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._open_cells and self._open_rows:
            parts, colspan, rowspan = self._open_cells.pop()
            self._open_rows[-1].append(RawCell(normalize("".join(parts)), colspan, rowspan))
        elif tag == "tr" and self._open_rows and self._open_tables:
            self._open_tables[-1].append(self._open_rows.pop())
        elif tag == "table" and self._open_tables:
            self.tables.append(self._open_tables.pop())

    def handle_data(self, data: str) -> None:
        if self._open_cells:
            self._open_cells[-1][0].append(data)


def to_grid(rows: list[list[RawCell]]) -> Grid:
    """Expand rowspan/colspan so every row has its labels at the right column positions."""
    grid: Grid = []
    pending: dict[tuple[int, int], str] = {}  # (row, column) -> text from a rowspan above
    for row_index, row in enumerate(rows):
        line: list[str] = []
        cells = iter(row)
        column = 0
        while True:
            if (row_index, column) in pending:
                line.append(pending.pop((row_index, column)))
                column += 1
                continue
            cell = next(cells, None)
            if cell is None:
                break
            for offset in range(cell.colspan):
                line.append(cell.text)
                for below in range(1, cell.rowspan):
                    pending[(row_index + below, column + offset)] = cell.text
            column += cell.colspan
        grid.append(line)
    return grid
