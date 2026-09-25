from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tr_banking import pipeline
from tr_banking.config import load_series_config
from tr_banking.db import SqliteRepository
from tr_banking.pipeline import UpdateError, load_source, run_update
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.bddk import BddkClient
from tr_banking.sources.evds import EvdsClient, EvdsResponseError

FIXTURES = Path(__file__).parent / "fixtures"
EVDS_BYTES = (FIXTURES / "evds_hpbitablo6_2024.json").read_bytes()
BDDK_BYTES = (FIXTURES / "bddk_konut_2024.json").read_bytes()
CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
EVDS_SPECS = CONFIG.for_source("evds")
BDDK_SPECS = CONFIG.for_source("bddk")
START, END = date(2024, 6, 14), date(2024, 7, 12)


@pytest.fixture
def repo() -> Iterator[SqliteRepository]:
    with SqliteRepository(":memory:") as repository:
        repository.init_schema()
        yield repository


def mock_transport(status: int = 200, body: bytes = b"") -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


def evds_client(tmp_path: Path, body: bytes = EVDS_BYTES) -> EvdsClient:
    return EvdsClient(
        SecretStr("fake"), "https://evds.test/x/", tmp_path, transport=mock_transport(body=body)
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
        written = load_source(repo, "evds", client, EVDS_SPECS, START, END)

    assert written == 18
    assert repo.list_series()["code"].tolist() == [spec.code for spec in EVDS_SPECS]
    assert len(repo.get_observations()) == 18


def test_load_source_twice_is_idempotent(repo: SqliteRepository, tmp_path: Path) -> None:
    with evds_client(tmp_path) as client:
        load_source(repo, "evds", client, EVDS_SPECS, START, END)
        load_source(repo, "evds", client, EVDS_SPECS, START, END)

    assert len(repo.list_series()) == 6
    assert len(repo.get_observations()) == 18


def test_load_source_writes_nothing_when_response_is_bad(
    repo: SqliteRepository, tmp_path: Path
) -> None:
    with (
        evds_client(tmp_path, body=b'{"items": []}') as client,
        pytest.raises(EvdsResponseError),
    ):
        load_source(repo, "evds", client, EVDS_SPECS, START, END)

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

    def fake_open_client(source: str, settings: Settings) -> EvdsClient | BddkClient:
        if source == "evds":
            real_open_client(source, settings).close()  # raises if the key is missing
            return evds_client(tmp_path)
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

    assert written == {"evds": 18, "bddk": 3 * len(BDDK_SPECS)}
    assert stored_rows_per_source(settings.db_path) == written


def test_failing_source_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_clients(monkeypatch, tmp_path, bddk_status=404)
    settings = settings_for(tmp_path)

    with pytest.raises(UpdateError, match="update failed for: bddk"):
        run_update(settings, START, END)

    assert stored_rows_per_source(settings.db_path) == {"evds": 18}
    assert "bddk update failed: BDDK returned HTTP 404" in caplog.text


def test_missing_api_key_only_fails_evds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_clients(monkeypatch, tmp_path)
    settings = settings_for(tmp_path, api_key=None)

    with pytest.raises(UpdateError, match="update failed for: evds"):
        run_update(settings, START, END)

    assert "EVDS_API_KEY is not set" in caplog.text
    assert stored_rows_per_source(settings.db_path) == {"bddk": 3 * len(BDDK_SPECS)}


def test_run_update_single_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_clients(monkeypatch, tmp_path)

    written = run_update(settings_for(tmp_path), START, END, sources=["bddk"])

    assert list(written) == ["bddk"]
