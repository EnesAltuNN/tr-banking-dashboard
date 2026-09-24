"""Smoke tests: run the Streamlit script headlessly against a temporary database."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tr_banking import settings as settings_module
from tr_banking.config import load_series_config
from tr_banking.db.repository import Repository
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.evds import parse_evds_response

DASHBOARD = PROJECT_ROOT / "src" / "tr_banking" / "app" / "dashboard.py"
FIXTURE = Path(__file__).parent / "fixtures" / "evds_hpbitablo6_2024.json"
SPECS = load_series_config(PROJECT_ROOT / "config" / "series.yaml").for_source("evds")


@pytest.fixture
def use_db(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """Point the dashboard at a given database without reading the real .env."""

    def _use(db_path: Path) -> None:
        settings = Settings(_env_file=None, db_path=db_path)
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    return _use


def run_dashboard() -> AppTest:
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def populated_db(path: Path) -> Path:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with Repository(path) as repo:
        repo.init_schema()
        for spec in SPECS:
            repo.upsert_series(spec)
        repo.upsert_observations("evds", parse_evds_response(payload, [s.code for s in SPECS]))
    return path


def test_empty_database_shows_instructions(use_db: Callable, tmp_path: Path) -> None:
    use_db(tmp_path / "missing.db")

    app = run_dashboard()

    assert not app.exception
    assert "No data yet" in app.info[0].value


def test_populated_database_renders_table_and_charts(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert not app.exception
    table = app.dataframe[0].value
    assert table["Series"].tolist() == [spec.name_en for spec in SPECS]
    assert table["Last value"].iloc[0] == pytest.approx(3223.3)  # thousand TRY -> billion TRY
    assert len(app.get("vega_lite_chart")) == len(SPECS)


def test_empty_selection_shows_hint(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.multiselect[0].set_value([]).run()

    assert not app.exception
    assert "Select at least one series" in app.info[0].value
