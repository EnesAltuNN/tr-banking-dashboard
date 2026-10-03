"""BKM monthly card statistics: label-based parsing of the real pages and the client."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from tr_banking.sources.bkm import (
    USER_AGENT,
    BkmClient,
    BkmResponseError,
    Cell,
    RawCell,
    months_between,
    parse_bkm_page,
    parse_number,
    parse_series_code,
    sum_cells,
    to_grid,
)

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_2026_07 = (FIXTURES / "bkm_2026_07.html").read_text(encoding="utf-8")
PAGE_2017_01 = (FIXTURES / "bkm_2017_01.html").read_text(encoding="utf-8")
NOT_PUBLISHED = (FIXTURES / "bkm_not_published.html").read_text(encoding="utf-8")
CODES = [
    "txn:credit:domestic:amount:shopping",
    "txn:debit:domestic:amount:shopping",
    "vpos:internet:amount",
    "txn:credit+debit:foreign:amount:shopping",
    "cards:credit",
    "cards:debit",
]


def values(html: str) -> dict[str, float]:
    grids = parse_bkm_page(html)
    assert grids is not None
    return {code: sum_cells(grids, parse_series_code(code)) for code in CODES}


# --- parsing the real pages ---


def test_july_2026_values_match_the_published_page() -> None:
    assert values(PAGE_2026_07) == pytest.approx(
        {
            "txn:credit:domestic:amount:shopping": 2_450_238.01,
            "txn:debit:domestic:amount:shopping": 418_420.61,
            "vpos:internet:amount": 908_898.53,
            # Foreign credit (68,586.23) + foreign debit (60,200.79) card spending.
            "txn:credit+debit:foreign:amount:shopping": 128_787.02,
            "cards:credit": 151_730_027,
            "cards:debit": 223_234_663,
        }
    )


def test_first_month_2017_has_the_same_layout() -> None:
    assert values(PAGE_2017_01)["cards:credit"] == 58_782_921
    assert values(PAGE_2017_01)["txn:credit:domestic:amount:shopping"] == pytest.approx(44_118.75)


def test_other_cells_are_reachable_by_their_labels() -> None:
    grids = parse_bkm_page(PAGE_2026_07)

    cash = sum_cells(grids, parse_series_code("txn:debit:domestic:amount:cash"))
    count = sum_cells(grids, parse_series_code("txn:credit:domestic:count:total"))
    assert cash == pytest.approx(565_875.96)
    assert count == 1_142_122_667


def test_not_published_month_gives_none() -> None:
    assert parse_bkm_page(NOT_PUBLISHED) is None


def test_unexpected_page_fails() -> None:
    with pytest.raises(BkmResponseError, match="neither"):
        parse_bkm_page("<html><body><p>Bakım çalışması</p></body></html>")


def test_changed_unit_fails_instead_of_mixing_units() -> None:
    page = PAGE_2026_07.replace("(Milyon TL)", "(Bin TL)")

    with pytest.raises(BkmResponseError, match="no cells found"):
        values(page)


def test_renamed_row_fails() -> None:
    page = PAGE_2026_07.replace("Yabanc&#305; Kartlar&#305;n Yurt", "Yabanc&#305; Kart Yurt")

    with pytest.raises(BkmResponseError, match="no cells found"):
        values(page)


def test_spans_are_expanded_into_a_grid() -> None:
    rows = [
        [RawCell("", 1, 2), RawCell("Amount", 2, 1)],
        [RawCell("Shop", 1, 1), RawCell("Cash", 1, 1)],
        [RawCell("Credit", 1, 1), RawCell("1,5", 1, 1), RawCell("2,5", 1, 1)],
    ]

    assert to_grid(rows) == [
        ["", "Amount", "Amount"],
        ["", "Shop", "Cash"],
        ["Credit", "1,5", "2,5"],
    ]


def test_ambiguous_cell_fails() -> None:
    grid = [["", "A", "A"], ["Row", "1", "2"]]

    with pytest.raises(BkmResponseError, match="2 cells found"):
        sum_cells([grid], [Cell(("Row",), ("A",))])


@pytest.mark.parametrize(
    ("raw", "value"),
    [("2.450.238,01", 2450238.01), ("151.730.027", 151730027.0), ("0", 0.0), ("-1,5", -1.5)],
)
def test_turkish_numbers(raw: str, value: float) -> None:
    assert parse_number(raw) == value


@pytest.mark.parametrize("raw", ["", "-", "1,234.5", "12a"])
def test_non_numbers_fail(raw: str) -> None:
    with pytest.raises(BkmResponseError, match="non-numeric"):
        parse_number(raw)


@pytest.mark.parametrize(
    "code",
    ["cards", "cards:gold", "txn:credit:domestic:amount", "txn:credit:moon:amount:total", "x:y"],
)
def test_invalid_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValueError, match="invalid BKM series code"):
        parse_series_code(code)


def test_months_between() -> None:
    assert months_between(date(2025, 11, 15), date(2026, 2, 1)) == [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]
    assert months_between(date(2026, 3, 1), date(2026, 2, 1)) == []


# --- client ---


class Pages:
    """Mock BKM: July 2026 is published; every other month shows the 'pick a date' page."""

    def __init__(self, published: set[tuple[int, int]]) -> None:
        self.published = published
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        month = (int(request.url.params["filter_year"]), int(request.url.params["filter_month"]))
        return httpx.Response(200, text=PAGE_2026_07 if month in self.published else NOT_PUBLISHED)


def make_client(pages: Pages, raw_dir: Path) -> BkmClient:
    return BkmClient(
        raw_dir,
        base_url="https://bkm.test/",
        request_interval=0,
        retry_wait=0,
        transport=httpx.MockTransport(pages),
    )


def test_client_skips_unpublished_months_and_stores_month_ends(tmp_path: Path) -> None:
    pages = Pages(published={(2026, 7)})

    with make_client(pages, tmp_path) as client:
        rows = client.fetch_observations(CODES, date(2026, 6, 1), date(2026, 9, 27))

    assert len(pages.requests) == 4  # June to September
    assert set(rows["date"]) == {date(2026, 7, 31)}
    assert rows.set_index("code").loc["cards:credit", "value"] == 151_730_027
    assert pages.requests[0].headers["User-Agent"] == USER_AGENT
    assert pages.requests[0].url.params["xls"] == "1"
    saved = sorted(path.suffix for path in (tmp_path / "bkm").iterdir())
    assert saved == [".html"] * 4


def test_client_never_asks_for_months_before_2017(tmp_path: Path) -> None:
    pages = Pages(published={(2017, 1)})

    with make_client(pages, tmp_path) as client:
        rows = client.fetch_observations(CODES, date(2014, 1, 3), date(2017, 1, 31))

    assert [request.url.params["filter_year"] for request in pages.requests] == ["2017"]
    assert set(rows["date"]) == {date(2017, 1, 31)}


def test_client_fails_when_no_month_is_published(tmp_path: Path) -> None:
    pages = Pages(published=set())

    with (
        make_client(pages, tmp_path) as client,
        pytest.raises(BkmResponseError, match="no BKM statistics published"),
    ):
        client.fetch_observations(CODES, date(2026, 8, 1), date(2026, 9, 27))


def test_invalid_code_fails_before_any_request(tmp_path: Path) -> None:
    pages = Pages(published={(2026, 7)})

    with make_client(pages, tmp_path) as client, pytest.raises(ValueError):
        client.fetch_observations(["cards:gold"], date(2026, 7, 1), date(2026, 7, 31))

    assert pages.requests == []


def test_client_yields_chunks_of_twelve_months(tmp_path: Path) -> None:
    months = {(2024, month) for month in range(1, 13)} | {(2025, month) for month in range(1, 7)}
    pages = Pages(published=months)

    with make_client(pages, tmp_path) as client:
        chunks = list(client.iter_observations(CODES, date(2024, 1, 1), date(2025, 9, 30)))

    # 21 requested months (Jan 2024 to Sep 2025): chunks after month 12 and at the end.
    assert [len(chunk) // len(CODES) for chunk in chunks] == [12, 6]
    assert len(pages.requests) == 21


def test_terminal_counts_come_from_their_own_tables() -> None:
    for page, pos, atm in (
        ("bkm_2017_01.html", 1_703_599, 48_530),
        ("bkm_2026_07.html", 2_020_116, 56_814),
    ):
        grids = parse_bkm_page((FIXTURES / page).read_text(encoding="utf-8"))

        assert sum_cells(grids, parse_series_code("terminals:pos")) == pos
        assert sum_cells(grids, parse_series_code("terminals:atm")) == atm
