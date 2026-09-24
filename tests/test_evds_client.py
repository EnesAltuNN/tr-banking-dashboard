import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tr_banking.sources.evds import (
    EvdsApiError,
    EvdsClient,
    EvdsResponseError,
    build_series_url,
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
