import json
import ssl
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from tr_banking.sources.bddk import (
    BddkApiError,
    BddkClient,
    BddkResponseError,
    BddkSeriesKey,
    bddk_ssl_context,
    parse_bddk_response,
    parse_series_code,
)

# Real response: housing loans, sector, TRY, total column, 2024-06-28..2024-07-12.
FIXTURE_BYTES = (Path(__file__).parent / "fixtures" / "bddk_konut_2024.json").read_bytes()
HOUSING = "1.0.4:10001:TRY:3"
START, END = date(2024, 6, 28), date(2024, 7, 12)


def make_client(tmp_path: Path, *responses: httpx.Response) -> tuple[BddkClient, list]:
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = BddkClient(
        tmp_path,
        base_url="https://bddk.test/BultenHaftalik/",
        retry_wait=0,
        request_interval=0,
        transport=httpx.MockTransport(handler),
    )
    return client, requests


def form_of(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


# --- series codes ---


def test_parse_series_code() -> None:
    assert parse_series_code(HOUSING) == BddkSeriesKey("1.0.4", "10001", "TRY", "3")


@pytest.mark.parametrize("bad", ["1.0.4", "1.0.4:10001:EUR:3", "1.0.4:1:TRY:3", "x:10001:TRY:3"])
def test_invalid_series_code_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid BDDK series code"):
        parse_series_code(bad)


# --- parser ---


def test_real_response_parses() -> None:
    df = parse_bddk_response(json.loads(FIXTURE_BYTES), HOUSING)

    assert list(df.columns) == ["code", "date", "value"]
    assert df["date"].tolist() == [date(2024, 6, 28), date(2024, 7, 5), date(2024, 7, 12)]
    assert df["value"].iloc[0] == 447103.264
    assert set(df["code"]) == {HOUSING}


def test_unpadded_day_is_accepted() -> None:
    df = parse_bddk_response({"XEkseni": ["3.01.2014"], "YEkseni": [1.0]}, HOUSING)

    assert df["date"].tolist() == [date(2014, 1, 3)]


def test_unknown_row_response_fails() -> None:
    # What BDDK actually returns (HTTP 200) for a row id that does not exist.
    payload = {"Baslik": "Grafik Başlığı", "XEkseni": [], "YEkseni": []}

    with pytest.raises(BddkResponseError, match="returned no values"):
        parse_bddk_response(payload, HOUSING)


def test_null_values_are_dropped() -> None:
    payload = {"XEkseni": ["5.07.2024", "12.07.2024"], "YEkseni": [None, 2.0]}

    assert parse_bddk_response(payload, HOUSING)["value"].tolist() == [2.0]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "not a JSON object"),
        ({"XEkseni": []}, "no XEkseni/YEkseni"),
        ({"XEkseni": ["5.07.2024"], "YEkseni": []}, "1 dates but 0 values"),
        ({"XEkseni": ["2024-07-05"], "YEkseni": [1.0]}, "unexpected date"),
        ({"XEkseni": ["5.07.2024"], "YEkseni": ["1.0"]}, "non-numeric"),
        ({"XEkseni": ["5.07.2024"], "YEkseni": [True]}, "non-numeric"),
        ({"XEkseni": ["5.07.2024", "5.07.2024"], "YEkseni": [1.0, 2.0]}, "duplicate dates"),
    ],
)
def test_malformed_responses_fail(payload: Any, message: str) -> None:
    with pytest.raises(BddkResponseError, match=message):
        parse_bddk_response(payload, HOUSING)


# --- client ---


def test_client_posts_expected_form(tmp_path: Path) -> None:
    client, requests = make_client(tmp_path, httpx.Response(200, content=FIXTURE_BYTES))

    with client:
        df = client.fetch_observations([HOUSING], START, END)

    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/BultenHaftalik/tr/Gelismis/KiyaslamaJsonGetir"
    assert form_of(request) == {
        "dil": "tr",
        "baslangicTarihi": "28.06.2024",
        "bitisTarihi": "12.07.2024",
        "id": "1.0.4",
        "parabirimi": "TRY",
        "sutun": "3",
        "tarafKodu": "10001",
    }
    assert len(df) == 3


def test_one_request_per_code(tmp_path: Path) -> None:
    client, requests = make_client(tmp_path, httpx.Response(200, content=FIXTURE_BYTES))

    with client:
        df = client.fetch_observations([HOUSING, "1.0.5:10001:TRY:3"], START, END)

    assert [form_of(r)["id"] for r in requests] == ["1.0.4", "1.0.5"]
    assert df["code"].unique().tolist() == [HOUSING, "1.0.5:10001:TRY:3"]


def test_raw_responses_saved_with_safe_file_names(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, httpx.Response(200, content=FIXTURE_BYTES))

    with client:
        client.fetch_observations([HOUSING], START, END)

    [saved] = (tmp_path / "bddk").glob("*.json")
    assert ":" not in saved.name
    assert saved.name.endswith("_1.0.4-10001-TRY-3_20240628_20240712.json")
    assert saved.read_bytes() == FIXTURE_BYTES


def test_invalid_code_fails_before_any_request(tmp_path: Path) -> None:
    client, requests = make_client(tmp_path, httpx.Response(200, content=FIXTURE_BYTES))

    with client, pytest.raises(ValueError, match="invalid BDDK series code"):
        client.fetch_observations([HOUSING, "bad"], START, END)

    assert requests == []


def test_start_after_end_fails(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, httpx.Response(200, content=FIXTURE_BYTES))

    with client, pytest.raises(ValueError, match="is after"):
        client.fetch_observations([HOUSING], END, START)


def test_transient_error_is_retried(tmp_path: Path) -> None:
    client, requests = make_client(
        tmp_path, httpx.Response(503), httpx.Response(200, content=FIXTURE_BYTES)
    )

    with client:
        client.fetch_observations([HOUSING], START, END)

    assert len(requests) == 2


def test_server_error_page_is_reported(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, httpx.Response(404, text="Not Found"))

    with client, pytest.raises(BddkApiError, match="BDDK returned HTTP 404"):
        client.fetch_observations([HOUSING], START, END)


def test_html_instead_of_json_fails(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, httpx.Response(200, text="<html>error</html>"))

    with client, pytest.raises(BddkResponseError, match="not valid JSON"):
        client.fetch_observations([HOUSING], START, END)


def test_ssl_context_trusts_the_intermediate_bddk_does_not_send() -> None:
    context = bddk_ssl_context()

    names = [
        dict(part[0] for part in cert["subject"]).get("commonName")
        for cert in context.get_ca_certs()
    ]
    assert "GlobalSign RSA OV SSL CA 2018" in names
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
