"""Bank websites (module 3): rate tables read from recorded, trimmed real pages."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from tr_banking.sources.bank_site import (
    TABLES,
    BankSiteApiError,
    BankSiteClient,
    BankSiteResponseError,
    amount_in,
    days_in,
    find_rate,
    parse_rate,
    parse_series_code,
    table_after,
)

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = {
    "www.ziraatbank.com.tr": (FIXTURES / "bank_ziraat_2026_10_07.html").read_text(encoding="utf-8"),
    "www.isbank.com.tr": (FIXTURES / "bank_isbank_2026_10_07.html").read_text(encoding="utf-8"),
}
TODAY = date(2026, 10, 7)


def grid(bank: str, table: str) -> list[list[str]]:
    host = {"ziraat": "www.ziraatbank.com.tr", "isbank": "www.isbank.com.tr"}[bank]
    return table_after(PAGES[host], TABLES[(bank, table)].marker)


@pytest.mark.parametrize(
    ("bank", "table", "days", "amount", "rate"),
    [
        ("ziraat", "internet", 32, 100_000, 31.0),  # 32 - 45 gün, 100.000 - 499.999,99 TL
        ("ziraat", "internet", 32, 99_999, 28.0),  # 5.000 - 99.999,99 TL
        ("ziraat", "internet", 400, 600_000, 26.0),  # 365 - 400 gün, 500.000 TL ve üzeri
        ("ziraat", "branch", 32, 100_000, 5.0),  # the branch pays a fraction of internet
        ("isbank", "campaign", 32, 100_000, 36.5),  # 5.000 - 100.000 TL includes 100.000
        ("isbank", "campaign", 32, 20_000_000, 37.85),  # the "% 3 7,85 ​" cell
        ("isbank", "campaign", 500, 100_000, 28.0),  # 367 Üzeri Gün
    ],
)
def test_rates_come_from_the_matching_cell(
    bank: str, table: str, days: int, amount: float, rate: float
) -> None:
    assert find_rate(grid(bank, table), days, amount) == rate


def test_a_cell_outside_the_table_fails_loudly() -> None:
    with pytest.raises(BankSiteResponseError, match="0 rows for 1000 days"):
        find_rate(grid("ziraat", "internet"), 1000, 100_000)  # the table ends at 730 days
    with pytest.raises(BankSiteResponseError, match="0 columns"):
        find_rate(grid("isbank", "campaign"), 32, 1_000)  # the first bracket starts at 5.000


@pytest.mark.parametrize(
    ("text", "value"),
    [("%31.00", 31.0), ("% 36,50", 36.5), ("% 3 7,85 ​", 37.85), ("%31", 31.0)],
)
def test_rate_cells_typed_by_hand_still_parse(text: str, value: float) -> None:
    assert parse_rate(text) == value


@pytest.mark.parametrize("text", ["%", "yok", "% 120", "%31.00 / %32"])
def test_odd_rate_cells_fail(text: str) -> None:
    with pytest.raises(BankSiteResponseError):
        parse_rate(text)


def test_day_and_amount_labels() -> None:
    assert days_in("32 - 45 gün", 32) and days_in("367 Üzeri Gün", 500)
    assert not days_in("Vadesiz", 32) and not days_in("1 Yıl Vadeli 1 Ayda Bir Faiz Ödemeli", 365)
    assert amount_in("100.000 - 499.999,99 TL", 100_000)
    assert amount_in("10.000.001 TL Üzeri", 20_000_000)
    assert amount_in("500.000,00 TL ve üzeri", 500_000)
    assert not amount_in("Vade Grupları", 100_000)


def test_series_code() -> None:
    assert parse_series_code("ziraat:internet:32:100000").amount == 100_000
    with pytest.raises(ValueError, match="expected <bank>"):
        parse_series_code("garanti:internet:32:100000")  # no static table, not supported


def client(
    tmp_path: Path, requests: list[httpx.Request], robots: str = "User-agent: *\nAllow: /\n"
) -> BankSiteClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text=PAGES[request.url.host])

    return BankSiteClient(tmp_path, transport=httpx.MockTransport(handler), today=TODAY)


def test_each_page_is_read_once_and_stored_with_today(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    codes = ["ziraat:internet:32:100000", "ziraat:branch:32:100000", "isbank:campaign:32:100000"]

    with client(tmp_path, requests) as banks:
        rows = banks.fetch_observations(codes, date(2026, 10, 1), TODAY)

    pages = [r.url.host for r in requests if r.url.path != "/robots.txt"]
    assert sorted(pages) == ["www.isbank.com.tr", "www.ziraatbank.com.tr"]  # once each
    assert rows["value"].tolist() == [31.0, 5.0, 36.5]
    assert set(rows["date"]) == {TODAY}


def test_robots_txt_is_respected(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    no = "User-agent: *\nDisallow: /tr/\n"

    with client(tmp_path, requests, robots=no) as banks, pytest.raises(BankSiteApiError):
        banks.fetch_observations(["ziraat:internet:32:100000"], TODAY, TODAY)

    assert [r.url.path for r in requests] == ["/robots.txt"]  # the page itself is never asked


def test_a_past_range_has_nothing_and_asks_nothing(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    with client(tmp_path, requests) as banks:
        rows = banks.fetch_observations(
            ["ziraat:internet:32:100000"], date(2014, 1, 3), date(2026, 9, 30)
        )

    assert rows.empty and requests == []
