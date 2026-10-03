"""BDDK monthly bulletin: parsing the ratios table and the client, on a recorded response."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from tr_banking.sources.bddk_monthly import (
    BddkMonthlyClient,
    BddkMonthlyResponseError,
    parse_report,
    parse_series_code,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPORT = json.loads((FIXTURES / "bddk_monthly_ratios_2026_08.json").read_text(encoding="utf-8"))
CAR = "Yasal Özkaynak / Risk Ağırlıklı Kalemler Toplamı (%)"
ROE = "Dönem Net Kârı (Zararı) / Ortalama Özkaynaklar (%)"
NOT_PUBLISHED = {"success": False, "error": "2026 yılının en son 8 ayına ait veri bulunmaktadır!"}


def test_parse_report_finds_rows_by_group_and_label() -> None:
    values = parse_report(REPORT, "15")

    assert len(values) == 5 * 32  # sector and four groups, 32 ratios each
    assert values[("Sektör", CAR)] == pytest.approx(16.601448)  # August 2026
    assert values[("Sektör", ROE)] == pytest.approx(16.54, abs=0.01)  # year to date
    assert values[("Mevduat-Kamu", CAR)] == pytest.approx(14.13, abs=0.01)
    assert values[("Katılım", ROE)] == pytest.approx(21.2, abs=0.01)


@pytest.mark.parametrize(
    "error",
    [
        "2026 yılının en son 8 ayına ait veri bulunmaktadır!",
        " Seçili tablo için veriler 2003 yılı, 1. aydan başlamaktadır.",
    ],
)
def test_unpublished_months_are_none(error: str) -> None:
    assert parse_report({"success": False, "error": error}, "15") is None


def test_other_failures_and_odd_layouts_fail_loudly() -> None:
    with pytest.raises(BddkMonthlyResponseError, match="failed: Sunucu hatası"):
        parse_report({"success": False, "error": "Sunucu hatası"}, "15")
    odd = {"success": True, "Json": {"data": {"rows": [{"cell": ["Sektör", 1, "X", 2.0]}]}}}
    with pytest.raises(BddkMonthlyResponseError, match="expected"):
        parse_report(odd, "15")


def test_series_code_is_table_group_and_label() -> None:
    assert parse_series_code(f"15:{CAR}") == ("15", "10001", CAR)  # no group: the sector
    assert parse_series_code(f"15@10009:{CAR}") == ("15", "10009", CAR)
    with pytest.raises(ValueError, match="expected <table>"):
        parse_series_code(CAR)
    with pytest.raises(ValueError, match="taraf one of"):
        parse_series_code(f"15@99999:{CAR}")


def client(
    raw_dir: Path, published: set[int], requests: list[httpx.Request] | None = None
) -> BddkMonthlyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        month = int(dict(pair.split("=") for pair in request.content.decode().split("&"))["ay"])
        return httpx.Response(200, json=REPORT if month in published else NOT_PUBLISHED)

    return BddkMonthlyClient(
        raw_dir,
        base_url="https://bddk.test/monthly",
        request_interval=0,
        transport=httpx.MockTransport(handler),
    )


def test_client_stores_month_end_values_and_skips_unpublished_months(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    with client(tmp_path, {7, 8}, requests) as monthly:
        rows = monthly.fetch_observations(
            [f"15:{CAR}", f"15@10009:{CAR}", f"15:{ROE}"], date(2026, 7, 1), date(2026, 9, 30)
        )

    # One table, three months: one request each, carrying both groups.
    assert len(requests) == 3
    assert b"taraf=10001&taraf=10009" in requests[0].content
    assert sorted(set(rows["date"])) == [date(2026, 7, 31), date(2026, 8, 31)]
    assert len(rows) == 6
    state = rows[rows["code"] == f"15@10009:{CAR}"]["value"]
    assert state.iloc[-1] == pytest.approx(14.13, abs=0.01)


def test_a_missing_row_fails_loudly(tmp_path: Path) -> None:
    with (
        client(tmp_path, {8}) as monthly,
        pytest.raises(BddkMonthlyResponseError, match="is missing"),
    ):
        monthly.fetch_observations(["15:Renamed row (%)"], date(2026, 8, 1), date(2026, 8, 31))


def test_no_published_month_fails(tmp_path: Path) -> None:
    with (
        client(tmp_path, set()) as monthly,
        pytest.raises(BddkMonthlyResponseError, match="no BDDK"),
    ):
        monthly.fetch_observations([f"15:{CAR}"], date(2026, 9, 1), date(2026, 9, 30))
