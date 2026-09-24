from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tr_banking.config import load_series_config
from tr_banking.db.repository import Repository
from tr_banking.pipeline import load_evds
from tr_banking.settings import PROJECT_ROOT
from tr_banking.sources.evds import EvdsClient, EvdsResponseError

FIXTURE_BYTES = (Path(__file__).parent / "fixtures" / "evds_hpbitablo6_2024.json").read_bytes()
SPECS = load_series_config(PROJECT_ROOT / "config" / "series.yaml").for_source("evds")
START, END = date(2024, 6, 14), date(2024, 7, 12)


@pytest.fixture
def repo() -> Iterator[Repository]:
    with Repository(":memory:") as repository:
        repository.init_schema()
        yield repository


def make_client(tmp_path: Path, body: bytes = FIXTURE_BYTES) -> EvdsClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    return EvdsClient(SecretStr("fake"), "https://evds.test/x/", tmp_path, transport=transport)


def test_load_evds_stores_series_and_observations(repo: Repository, tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        written = load_evds(repo, client, SPECS, START, END)

    assert written == 18
    assert repo.list_series()["code"].tolist() == [spec.code for spec in SPECS]
    assert len(repo.get_observations()) == 18


def test_load_evds_twice_is_idempotent(repo: Repository, tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        load_evds(repo, client, SPECS, START, END)
        load_evds(repo, client, SPECS, START, END)

    assert len(repo.list_series()) == 6
    assert len(repo.get_observations()) == 18


def test_load_evds_writes_nothing_when_response_is_bad(repo: Repository, tmp_path: Path) -> None:
    with make_client(tmp_path, body=b'{"items": []}') as client, pytest.raises(EvdsResponseError):
        load_evds(repo, client, SPECS, START, END)

    assert repo.get_observations().empty


def test_load_evds_requires_series(repo: Repository, tmp_path: Path) -> None:
    with make_client(tmp_path) as client, pytest.raises(ValueError, match="no EVDS series"):
        load_evds(repo, client, [], START, END)
