import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from tr_banking.sources.common import REDACTED
from tr_banking.sources.evds import (
    EvdsApiError,
    EvdsClient,
    EvdsResponseError,
    add_years,
    build_series_url,
    request_windows,
)

FIXTURE_BYTES = (Path(__file__).parent / "fixtures" / "evds_hpbitablo6_2024.json").read_bytes()
CODES = [
    "TP.HPBITABLO6.2",
    "TP.HPBITABLO6.3",
    "TP.HPBITABLO6.7",
    "TP.HPBITABLO6.11",
    "TP.HPBITABLO6.16",
    "TP.HPBITABLO6.20",
]
FAKE_KEY = "fake-key-must-not-leak"
# .test never resolves, so a misconfigured test cannot reach a real server.
BASE_URL = "https://evds.test/igmevdsms-dis/"
START, END = date(2024, 6, 14), date(2024, 7, 12)

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """Mock transport handler that replays a list of responses and records requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def make_client(handler: Handler, raw_dir: Path) -> EvdsClient:
    return EvdsClient(
        SecretStr(FAKE_KEY),
        BASE_URL,
        raw_dir,
        retry_wait=0,
        transport=httpx.MockTransport(handler),
    )


def ok() -> httpx.Response:
    return httpx.Response(200, content=FIXTURE_BYTES)


def test_key_is_sent_in_header_not_url(tmp_path: Path) -> None:
    recorder = Recorder(ok())

    with make_client(recorder, tmp_path) as client:
        client.fetch_observations(CODES, START, END)

    request = recorder.requests[0]
    assert request.headers["key"] == FAKE_KEY
    assert FAKE_KEY not in str(request.url)


def test_url_uses_evds_path_style_parameters() -> None:
    url = build_series_url(BASE_URL, ["TP.A.1", "TP.B.2"], date(2026, 1, 2), date(2026, 1, 9))

    assert url == (
        "https://evds.test/igmevdsms-dis/"
        "series=TP.A.1-TP.B.2&startDate=02-01-2026&endDate=09-01-2026&type=json"
    )


def test_client_requests_url_unchanged(tmp_path: Path) -> None:
    recorder = Recorder(ok())

    with make_client(recorder, tmp_path) as client:
        client.fetch_observations(CODES, START, END)

    # httpx must not percent-encode '=' / '&' or move them into a query string.
    url = recorder.requests[0].url
    assert str(url) == build_series_url(BASE_URL, CODES, START, END)
    assert url.query == b""


def test_returns_parsed_observations(tmp_path: Path) -> None:
    with make_client(Recorder(ok()), tmp_path) as client:
        df = client.fetch_observations(CODES, START, END)

    assert len(df) == 18
    assert list(df.columns) == ["code", "date", "value"]


def test_raw_response_is_saved_byte_for_byte(tmp_path: Path) -> None:
    with make_client(Recorder(ok()), tmp_path) as client:
        client.fetch_observations(CODES, START, END)

    saved = list((tmp_path / "evds").glob("*.json"))
    assert len(saved) == 1
    assert saved[0].name.endswith("_20240614_20240712.json")
    assert saved[0].read_bytes() == FIXTURE_BYTES


def test_invalid_json_fails_but_raw_is_kept(tmp_path: Path) -> None:
    with (
        make_client(Recorder(httpx.Response(200, content=b"<html>")), tmp_path) as client,
        pytest.raises(EvdsResponseError, match="not valid JSON"),
    ):
        client.fetch_observations(CODES, START, END)

    assert len(list((tmp_path / "evds").glob("*.json"))) == 1


def test_forbidden_fails_with_hint(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(403))

    with make_client(recorder, tmp_path) as client, pytest.raises(EvdsApiError) as exc_info:
        client.fetch_observations(CODES, START, END)

    assert "EVDS_API_KEY" in str(exc_info.value)
    assert FAKE_KEY not in str(exc_info.value)
    assert len(recorder.requests) == 1


def test_redirect_is_not_followed(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(302, headers={"location": "https://elsewhere.test/"}))

    with make_client(recorder, tmp_path) as client, pytest.raises(EvdsApiError, match="redirected"):
        client.fetch_observations(CODES, START, END)

    assert [r.url.host for r in recorder.requests] == ["evds.test"]


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(400, json={"message": "Bad Request"}))

    with make_client(recorder, tmp_path) as client, pytest.raises(EvdsApiError, match="400"):
        client.fetch_observations(CODES, START, END)

    assert len(recorder.requests) == 1


def test_transient_error_is_retried(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(503), ok())

    with make_client(recorder, tmp_path) as client:
        df = client.fetch_observations(CODES, START, END)

    assert len(recorder.requests) == 2
    assert not df.empty


def test_gives_up_after_max_retries(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(500))

    with (
        make_client(recorder, tmp_path) as client,
        pytest.raises(EvdsApiError, match="after 3 attempts: HTTP 500"),
    ):
        client.fetch_observations(CODES, START, END)

    assert len(recorder.requests) == 3


def test_network_error_is_retried_then_fails(tmp_path: Path) -> None:
    recorder = Recorder(httpx.ConnectError("connection refused"))

    with make_client(recorder, tmp_path) as client, pytest.raises(EvdsApiError, match="network"):
        client.fetch_observations(CODES, START, END)

    assert len(recorder.requests) == 3


def test_api_key_never_appears_in_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    recorder = Recorder(httpx.Response(503), ok())

    with make_client(recorder, tmp_path) as client:
        client.fetch_observations(CODES, START, END)

    assert caplog.records, "expected some log output to check"
    assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize(
    ("codes", "start", "end", "message"),
    [
        ([], START, END, "at least one"),
        (["TP.A.1&key=x"], START, END, "invalid series codes"),
        (["TP A 1"], START, END, "invalid series codes"),
        (["TP.A.1"], END, START, "is after"),
    ],
)
def test_build_series_url_rejects_bad_input(
    codes: list[str], start: date, end: date, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_series_url(BASE_URL, codes, start, end)


def test_key_echoed_by_the_server_is_masked_in_raw_file(tmp_path: Path) -> None:
    body = FIXTURE_BYTES.replace(b'"totalCount"', f'"echo": "{FAKE_KEY}", "totalCount"'.encode())

    with make_client(Recorder(httpx.Response(200, content=body)), tmp_path) as client:
        client.fetch_observations(CODES, START, END)

    [saved] = (tmp_path / "evds").glob("*.json")
    text = saved.read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert REDACTED in text


def test_key_echoed_in_an_error_is_masked(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(400, text=f"invalid key {FAKE_KEY}"))

    with make_client(recorder, tmp_path) as client, pytest.raises(EvdsApiError) as exc_info:
        client.fetch_observations(CODES, START, END)

    assert FAKE_KEY not in str(exc_info.value)


# --- EVDS returns at most 1000 items per response, so long ranges go in windows ---


def daily_payload(code: str, days: list[str], value: str | None = "1.5") -> dict:
    column = code.replace(".", "_")
    return {"items": [{"Tarih": day, column: value} for day in days]}


def test_request_windows_cover_the_range_without_overlap() -> None:
    windows = request_windows(date(2014, 1, 3), date(2019, 6, 30))

    assert windows == [
        (date(2014, 1, 3), date(2016, 1, 2)),
        (date(2016, 1, 3), date(2018, 1, 2)),
        (date(2018, 1, 3), date(2019, 6, 30)),
    ]
    assert request_windows(START, END) == [(START, END)]


def test_add_years_handles_leap_day() -> None:
    assert add_years(date(2024, 2, 29), 2) == date(2026, 2, 28)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)


def test_long_range_is_fetched_in_windows_and_combined(tmp_path: Path) -> None:
    code = "TP.PY.P02.1H"
    recorder = Recorder(
        # Like the real one-week repo series: no values before September 2018.
        httpx.Response(200, json=daily_payload(code, ["04-01-2016"], value=None)),
        httpx.Response(200, json=daily_payload(code, ["14-09-2018", "17-09-2018"])),
        httpx.Response(200, json=daily_payload(code, ["02-01-2019"])),
    )

    with make_client(recorder, tmp_path) as client:
        rows = client.fetch_observations([code], date(2015, 1, 1), date(2019, 6, 30))

    urls = [str(request.url) for request in recorder.requests]
    assert len(urls) == 3
    assert "startDate=01-01-2015&endDate=31-12-2016" in urls[0]
    assert "startDate=01-01-2017&endDate=31-12-2018" in urls[1]
    assert "startDate=01-01-2019&endDate=30-06-2019" in urls[2]
    assert rows["date"].tolist() == [date(2018, 9, 14), date(2018, 9, 17), date(2019, 1, 2)]
    assert len(list((tmp_path / "evds").iterdir())) == 3  # one raw file per window


def test_series_without_values_in_any_window_fails(tmp_path: Path) -> None:
    code = "TP.PY.P02.1H"
    empty = httpx.Response(200, json=daily_payload(code, ["03-01-2017"], value=None))

    with (
        make_client(Recorder(empty), tmp_path) as client,
        pytest.raises(EvdsResponseError, match="returned no values"),
    ):
        client.fetch_observations([code], date(2017, 1, 1), date(2019, 6, 30))


def test_response_at_the_evds_cap_fails_instead_of_losing_rows(tmp_path: Path) -> None:
    code = "TP.PY.P02.1H"
    days = [f"{day:%d-%m-%Y}" for day in pd.date_range("2016-01-01", periods=1000, freq="D")]
    capped = httpx.Response(200, json=daily_payload(code, days))

    with (
        make_client(Recorder(capped), tmp_path) as client,
        pytest.raises(EvdsResponseError, match="EVDS's cap"),
    ):
        client.fetch_observations([code], START, END)


def test_week_returned_by_two_windows_is_kept_once(tmp_path: Path) -> None:
    # A request ending Saturday 2016-01-02 and the next starting Sunday 2016-01-03 both return
    # the week ending Friday 2016-01-01 (seen in a real backfill on 2026-09-27).
    code = "TP.KTF10"
    recorder = Recorder(
        httpx.Response(200, json=daily_payload(code, ["25-12-2015", "01-01-2016"])),
        httpx.Response(200, json=daily_payload(code, ["01-01-2016", "08-01-2016"])),
    )

    with make_client(recorder, tmp_path) as client:
        rows = client.fetch_observations([code], date(2014, 1, 3), date(2016, 1, 10))

    assert rows["date"].tolist() == [date(2015, 12, 25), date(2016, 1, 1), date(2016, 1, 8)]


def test_windows_disagreeing_on_a_shared_week_fail(tmp_path: Path) -> None:
    code = "TP.KTF10"
    recorder = Recorder(
        httpx.Response(200, json=daily_payload(code, ["01-01-2016"], value="1.5")),
        httpx.Response(200, json=daily_payload(code, ["01-01-2016"], value="2.5")),
    )

    with (
        make_client(recorder, tmp_path) as client,
        pytest.raises(EvdsResponseError, match="disagree"),
    ):
        client.fetch_observations([code], date(2014, 1, 3), date(2016, 1, 10))
