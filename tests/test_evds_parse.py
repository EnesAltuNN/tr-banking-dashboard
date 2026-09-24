import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tr_banking.sources.evds import EvdsResponseError, evds_column_name, parse_evds_response

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_CODES = [
    "TP.HPBITABLO6.2",
    "TP.HPBITABLO6.3",
    "TP.HPBITABLO6.7",
    "TP.HPBITABLO6.11",
    "TP.HPBITABLO6.16",
    "TP.HPBITABLO6.20",
]


def load_fixture() -> dict[str, Any]:
    # Real EVDS3 response, 2024-06-14..2024-07-12; the first two weeks are null.
    return json.loads((FIXTURES / "evds_hpbitablo6_2024.json").read_text(encoding="utf-8"))


def make_payload(*items: dict[str, Any]) -> dict[str, Any]:
    return {"totalCount": len(items), "items": list(items)}


def make_item(tarih: str, value: Any = "1.5", code: str = "TP.X.1") -> dict[str, Any]:
    return {"Tarih": tarih, "YEARWEEK": "2026-1", evds_column_name(code): value}


def test_column_name_replaces_dots() -> None:
    assert evds_column_name("TP.HPBITABLO6.20") == "TP_HPBITABLO6_20"


def test_real_response_parses_into_long_format() -> None:
    df = parse_evds_response(load_fixture(), FIXTURE_CODES)

    assert list(df.columns) == ["code", "date", "value"]
    # 6 series x 3 non-null weeks; the 2 leading null weeks are dropped.
    assert len(df) == 18
    assert df["code"].unique().tolist() == FIXTURE_CODES
    assert df["date"].min() == date(2024, 6, 28)


def test_real_response_values_are_floats() -> None:
    df = parse_evds_response(load_fixture(), FIXTURE_CODES)

    first = df[(df["code"] == "TP.HPBITABLO6.2") & (df["date"] == date(2024, 6, 28))]
    assert first["value"].item() == 3_191_973_690.0


def test_unrequested_series_column_fails() -> None:
    payload = make_payload(
        {"Tarih": "02-01-2026", "TP_A_1": "1", "TP_B_1": "2"},
    )

    with pytest.raises(EvdsResponseError, match="unexpected columns"):
        parse_evds_response(payload, ["TP.A.1"])


def test_rows_are_sorted_by_date() -> None:
    payload = make_payload(make_item("09-01-2026", "2"), make_item("02-01-2026", "1"))

    df = parse_evds_response(payload, ["TP.X.1"])

    assert df["date"].tolist() == [date(2026, 1, 2), date(2026, 1, 9)]


@pytest.mark.parametrize("payload", [None, [], {}, {"items": "x"}, {"totalCount": 0}])
def test_malformed_payload_fails(payload: Any) -> None:
    with pytest.raises(EvdsResponseError, match="no 'items' list"):
        parse_evds_response(payload, ["TP.X.1"])


def test_empty_items_fail() -> None:
    with pytest.raises(EvdsResponseError, match="no observations"):
        parse_evds_response(make_payload(), ["TP.X.1"])


def test_missing_series_column_fails() -> None:
    with pytest.raises(EvdsResponseError, match="TP_Y_1"):
        parse_evds_response(make_payload(make_item("02-01-2026")), ["TP.X.1", "TP.Y.1"])


def test_missing_date_column_fails() -> None:
    with pytest.raises(EvdsResponseError, match="Tarih"):
        parse_evds_response(make_payload({"TP_X_1": "1"}), ["TP.X.1"])


def test_all_null_series_fails() -> None:
    payload = make_payload(make_item("02-01-2026", None), make_item("09-01-2026", None))

    with pytest.raises(EvdsResponseError, match="TP.X.1 returned no values"):
        parse_evds_response(payload, ["TP.X.1"])


@pytest.mark.parametrize("bad_date", ["2026-01-02", "2026-1", "", None])
def test_bad_date_fails(bad_date: Any) -> None:
    with pytest.raises(EvdsResponseError, match="unexpected date"):
        parse_evds_response(make_payload(make_item(bad_date)), ["TP.X.1"])


@pytest.mark.parametrize("bad_value", ["abc", "", "nan", "inf", {"x": 1}])
def test_bad_value_fails(bad_value: Any) -> None:
    with pytest.raises(EvdsResponseError, match="TP.X.1"):
        parse_evds_response(make_payload(make_item("02-01-2026", bad_value)), ["TP.X.1"])


def test_duplicate_dates_fail() -> None:
    payload = make_payload(make_item("02-01-2026", "1"), make_item("02-01-2026", "2"))

    with pytest.raises(EvdsResponseError, match="duplicate dates"):
        parse_evds_response(payload, ["TP.X.1"])
