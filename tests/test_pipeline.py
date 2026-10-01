import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tr_banking import pipeline
from tr_banking.config import load_series_config
from tr_banking.db import SqliteRepository
from tr_banking.pipeline import UpdateError, fetch_window_start, load_source, run_update
from tr_banking.security import files_containing_secrets, secret_values
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.bddk import BddkClient
from tr_banking.sources.bkm import BkmClient
from tr_banking.sources.common import month_end
from tr_banking.sources.evds import EvdsClient, EvdsResponseError

FIXTURES = Path(__file__).parent / "fixtures"
# Real EVDS responses. Loan credit and loan rates share the same five Fridays.
EVDS_FIXTURES = [
    "evds_hpbitablo6_2024.json",  # 6 credit series, 3 of 5 weeks non-null
    "evds_loan_rates_2024.json",  # 4 loan rates x 5 weeks
    "evds_cpi_2024.json",  # 12 CPI months
    "evds_policy_rate_2024_2025.json",  # 19 business days + 1 holiday (null)
    "evds_funding_cost_2024_2025.json",  # the same window for the funding cost
]
BDDK_BYTES = (FIXTURES / "bddk_konut_2024.json").read_bytes()
BKM_PAGE = (FIXTURES / "bkm_2026_07.html").read_bytes()
BKM_NOT_PUBLISHED = (FIXTURES / "bkm_not_published.html").read_bytes()
CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
CREDIT_EVDS_SPECS = [spec for spec in CONFIG.for_source("evds") if spec.module == "credit"]
CPI_SPEC = CONFIG.deflator
BDDK_SPECS = CONFIG.for_source("bddk")
BKM_SPECS = CONFIG.for_source("bkm")
BKM_ROWS = len(BKM_SPECS)  # only June is "published" in bkm_transport
EVDS_ROWS = 6 * 3 + 4 * 5 + 12 + 19 + 19  # credit, loan rates, CPI, policy, funding cost
START, END = date(2024, 6, 14), date(2024, 7, 12)


@pytest.fixture
def repo() -> Iterator[SqliteRepository]:
    with SqliteRepository(":memory:") as repository:
        repository.init_schema()
        yield repository


def mock_transport(status: int = 200, body: bytes = b"") -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


def fixture_response(codes: list[str]) -> dict:
    """An EVDS response for `codes`, built from the fixtures and merged by date like EVDS does."""
    columns = {code.replace(".", "_") for code in codes}
    items: dict[str, dict] = {}
    for name in EVDS_FIXTURES:
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for item in payload["items"]:
            wanted = {key: value for key, value in item.items() if key in columns}
            if wanted:
                items.setdefault(item["Tarih"], {"Tarih": item["Tarih"]}).update(wanted)
    return {"totalCount": len(items), "items": list(items.values())}


def evds_transport(requests: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        codes = re.search(r"series=([^&]+)", str(request.url)).group(1).split("-")
        return httpx.Response(200, json=fixture_response(codes))

    return httpx.MockTransport(handler)


def evds_client(
    tmp_path: Path, body: bytes | None = None, requests: list[httpx.Request] | None = None
) -> EvdsClient:
    transport = mock_transport(body=body) if body is not None else evds_transport(requests)
    return EvdsClient(SecretStr("fake"), "https://evds.test/x/", tmp_path, transport=transport)


def bkm_transport(requests: list[httpx.Request] | None = None) -> httpx.MockTransport:
    """June has a statistics page (the July 2026 fixture); other months are not published."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        published = request.url.params.get("filter_month") == "6"
        return httpx.Response(200, content=BKM_PAGE if published else BKM_NOT_PUBLISHED)

    return httpx.MockTransport(handler)


def bkm_client(tmp_path: Path, requests: list[httpx.Request] | None = None) -> BkmClient:
    return BkmClient(
        tmp_path,
        base_url="https://bkm.test/",
        request_interval=0,
        transport=bkm_transport(requests),
    )


def bddk_client(tmp_path: Path, status: int = 200) -> BddkClient:
    # Every BDDK code gets the same housing fixture; enough to exercise the pipeline.
    return BddkClient(
        tmp_path,
        base_url="https://bddk.test/",
        request_interval=0,
        retry_wait=0,
        transport=mock_transport(status, BDDK_BYTES),
    )


# --- load_source ---


def test_load_source_stores_series_and_observations(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client:
        written = load_source(repo, "evds", client, CREDIT_EVDS_SPECS, START, END)

    assert written == 18
    assert repo.list_series()["code"].tolist() == [spec.code for spec in CREDIT_EVDS_SPECS]
    assert len(repo.get_observations()) == 18


def test_load_source_twice_is_idempotent(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client:
        load_source(repo, "evds", client, CREDIT_EVDS_SPECS, START, END)
        load_source(repo, "evds", client, CREDIT_EVDS_SPECS, START, END)

    assert len(repo.list_series()) == 6
    assert len(repo.get_observations()) == 18


def test_load_source_writes_nothing_when_response_is_bad(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    with (
        evds_client(tmp_path, body=b'{"items": []}') as client,
        pytest.raises(EvdsResponseError),
    ):
        load_source(repo, "evds", client, CREDIT_EVDS_SPECS, START, END)

    assert repo.get_observations().empty


def test_load_source_requires_series(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client, pytest.raises(ValueError, match="no evds series"):
        load_source(repo, "evds", client, [], START, END)


# --- run_update ---


def settings_for(tmp_path: Path, api_key: str | None = "fake") -> Settings:
    return Settings(
        _env_file=None, evds_api_key=api_key, db_path=tmp_path / "t.db", raw_dir=tmp_path / "raw"
    )


def use_clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bddk_status: int = 200) -> None:
    """Replace real clients with mocked ones; keep the real missing-key check for EVDS."""
    real_open_client = pipeline.open_client

    def fake_open_client(source: str, settings: Settings) -> EvdsClient | BddkClient | BkmClient:
        if source == "evds":
            real_open_client(source, settings).close()  # raises if the key is missing
            return evds_client(tmp_path)
        if source == "bkm":
            return bkm_client(tmp_path)
        return bddk_client(tmp_path, bddk_status)

    monkeypatch.setattr(pipeline, "open_client", fake_open_client)


def stored_rows_per_source(db_path: Path) -> dict[str, int]:
    with SqliteRepository(db_path) as repo:
        series = repo.list_series().set_index("id")
        observations = repo.get_observations()
    return observations["series_id"].map(series["source"]).value_counts().to_dict()


def test_run_update_loads_all_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_clients(monkeypatch, tmp_path)
    settings = settings_for(tmp_path)

    written = run_update(settings, START, END)

    assert written == {"evds": EVDS_ROWS, "bddk": 3 * len(BDDK_SPECS), "bkm": BKM_ROWS}
    assert stored_rows_per_source(settings.db_path) == written


def test_failing_source_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_clients(monkeypatch, tmp_path, bddk_status=404)
    settings = settings_for(tmp_path)

    with pytest.raises(UpdateError, match="update failed for: bddk$"):
        run_update(settings, START, END)

    assert stored_rows_per_source(settings.db_path) == {"evds": EVDS_ROWS, "bkm": BKM_ROWS}
    assert "bddk update failed: BDDK returned HTTP 404" in caplog.text


def test_missing_api_key_only_fails_evds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_clients(monkeypatch, tmp_path)
    settings = settings_for(tmp_path, api_key=None)

    with pytest.raises(UpdateError, match="update failed for: evds$"):
        run_update(settings, START, END)

    assert "EVDS_API_KEY is not set" in caplog.text
    assert stored_rows_per_source(settings.db_path) == {
        "bddk": 3 * len(BDDK_SPECS),
        "bkm": BKM_ROWS,
    }


def test_run_update_single_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_clients(monkeypatch, tmp_path)

    written = run_update(settings_for(tmp_path), START, END, sources=["bddk"])

    assert list(written) == ["bddk"]


def test_raw_files_hold_no_secrets(repo: SqliteRepository, tmp_path: Path) -> None:
    """Raw responses become a public CI artifact: no key, URL, password or headers inside."""
    key = "fake-evds-key-must-not-leak"
    url = "postgresql://postgres.ref:db-password-must-not-leak@h.pooler.supabase.com:5432/postgres"
    raw_dir = tmp_path / "raw"
    evds = EvdsClient(SecretStr(key), "https://evds.test/x/", raw_dir, transport=evds_transport())
    bddk = BddkClient(
        raw_dir,
        base_url="https://bddk.test/",
        request_interval=0,
        transport=mock_transport(200, BDDK_BYTES),
    )

    bkm = BkmClient(
        raw_dir, base_url="https://bkm.test/", request_interval=0, transport=bkm_transport()
    )

    with evds, bddk, bkm:
        load_source(repo, "evds", evds, CONFIG.for_source("evds"), START, END)
        load_source(repo, "bddk", bddk, BDDK_SPECS, START, END)
        load_source(repo, "bkm", bkm, BKM_SPECS, START, END)

    saved = [path for path in raw_dir.rglob("*") if path.is_file()]
    secrets = secret_values(Settings(_env_file=None, evds_api_key=key, database_url=url))
    # EVDS: one request per frequency; BKM: one page per month, January to July 2024.
    assert len(saved) == 3 + len(BDDK_SPECS) + 7
    assert files_containing_secrets(raw_dir, [*secrets, "key:"]) == []


# --- mixed frequencies (EVDS converts a mixed request to the lowest frequency) ---


def test_weekly_and_monthly_series_are_requested_separately(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    requests: list[httpx.Request] = []

    with evds_client(tmp_path, requests=requests) as client:
        written = load_source(repo, "evds", client, CONFIG.for_source("evds"), START, END)

    urls = [str(request.url) for request in requests]
    assert len(urls) == 3  # daily, monthly, weekly
    [monthly_url] = [url for url in urls if "TUKFIY2025" in url]
    [daily_url] = [url for url in urls if "TP.PY.P02.1H" in url]
    [weekly_url] = [url for url in urls if "HPBITABLO6" in url]
    assert "series=TP.TUKFIY2025.GENEL&" in monthly_url
    assert "series=TP.PY.P02.1H-TP.APIFON4&" in daily_url  # both daily rates, one request
    assert "TP.KTF10" in weekly_url  # loan credit and loan rates are both weekly
    assert "startDate=01-04-2024" in monthly_url  # month-aligned, 3 months before July
    assert "startDate=14-06-2024" in weekly_url
    assert "startDate=14-06-2024" in daily_url
    assert written == EVDS_ROWS


def test_monthly_cpi_is_stored_at_month_end(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client:
        load_source(repo, "evds", client, [CPI_SPEC], START, END)

    dates = repo.get_observations()["date"].dt.date.tolist()
    assert dates[0] == date(2024, 1, 31)
    assert dates[1] == date(2024, 2, 29)  # leap year
    assert len(dates) == 12


def test_holidays_in_a_daily_series_are_skipped(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client:
        load_source(repo, "evds", client, [CONFIG.policy_rate], START, END)

    dates = repo.get_observations()["date"].dt.date.tolist()
    assert len(dates) == 19
    assert date(2025, 1, 1) not in dates  # New Year's Day: null in EVDS


def test_bkm_reads_monthly_pages_from_the_aligned_month(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    requests: list[httpx.Request] = []

    with bkm_client(tmp_path, requests) as client:
        written = load_source(repo, "bkm", client, BKM_SPECS, START, END)

    months = [request.url.params["filter_month"] for request in requests]
    assert months == ["1", "2", "3", "4", "5", "6", "7"]  # 6 months before July, up to July
    assert written == BKM_ROWS
    assert set(repo.get_observations()["date"].dt.date) == {date(2024, 6, 30)}


# --- fetch windows: monthly sources publish late ---

FETCH_DAY = date(2026, 9, 28)  # a Monday; the default fetch reads 8 weeks back
EIGHT_WEEKS_BACK = FETCH_DAY - timedelta(weeks=8)


@pytest.mark.parametrize(
    ("source", "frequency", "start", "end", "expected"),
    [
        ("evds", "weekly", EIGHT_WEEKS_BACK, FETCH_DAY, EIGHT_WEEKS_BACK),
        ("evds", "daily", EIGHT_WEEKS_BACK, FETCH_DAY, EIGHT_WEEKS_BACK),
        # CPI: June to September, i.e. the last 3 months before September.
        ("evds", "monthly", EIGHT_WEEKS_BACK, FETCH_DAY, date(2026, 6, 1)),
        # BKM: March to September, whatever --weeks says.
        ("bkm", "monthly", EIGHT_WEEKS_BACK, FETCH_DAY, date(2026, 3, 1)),
        ("bkm", "monthly", FETCH_DAY - timedelta(weeks=1), FETCH_DAY, date(2026, 3, 1)),
        # A backfill's earlier start wins.
        ("evds", "monthly", date(2014, 1, 3), FETCH_DAY, date(2014, 1, 1)),
        # Crossing a year.
        ("bkm", "monthly", date(2026, 2, 15), date(2026, 2, 20), date(2025, 8, 1)),
    ],
)
def test_fetch_window_start(
    source: str, frequency: str, start: date, end: date, expected: date
) -> None:
    assert fetch_window_start(source, frequency, start, end) == expected


def test_monthly_source_without_a_lookback_fails() -> None:
    with pytest.raises(ValueError, match="no monthly lookback defined for source 'bank_site'"):
        fetch_window_start("bank_site", "monthly", EIGHT_WEEKS_BACK, FETCH_DAY)


@pytest.mark.parametrize(("source", "lag_months"), [("evds", 2), ("bkm", 3)])
def test_every_fetch_covers_the_publication_lag(source: str, lag_months: int) -> None:
    # On every day of a year, with the shortest possible fetch (1 week), the window must reach
    # the oldest month that can still be new: CPI of the month before last (published around
    # the 3rd), BKM of three months ago (published 1.5 to 2 months after the month ends).
    for offset in range(365):
        today = date(2026, 1, 1) + timedelta(days=offset)
        window = fetch_window_start(source, "monthly", today - timedelta(weeks=1), today)
        oldest_new = date(today.year, today.month, 1)
        for _ in range(lag_months):
            oldest_new = (oldest_new - timedelta(days=1)).replace(day=1)
        assert window <= oldest_new, (today, window)


# --- BKM stores what it has downloaded, 12 months at a time ---


def test_interrupted_bkm_backfill_keeps_the_stored_months(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 15:
            raise KeyboardInterrupt  # Ctrl+C during the 15th page
        return httpx.Response(200, content=BKM_PAGE)

    client = BkmClient(
        tmp_path,
        base_url="https://bkm.test/",
        request_interval=0,
        transport=httpx.MockTransport(handler),
    )
    with client, pytest.raises(KeyboardInterrupt):
        load_source(repo, "bkm", client, BKM_SPECS, date(2023, 1, 1), date(2024, 12, 31))

    stored = repo.get_observations()["date"].dt.date
    # All of 2023: the first chunk of 12 months was committed before the interrupt.
    assert sorted(set(stored)) == [month_end(2023, month) for month in range(1, 13)]
    assert len(stored) == 12 * len(BKM_SPECS)
