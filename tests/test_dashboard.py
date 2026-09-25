"""Smoke tests: run the Streamlit script headlessly against a temporary database."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tr_banking import settings as settings_module
from tr_banking.config import load_series_config
from tr_banking.db import SqliteRepository
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.bddk import parse_bddk_response
from tr_banking.sources.evds import parse_evds_response

DASHBOARD = PROJECT_ROOT / "src" / "tr_banking" / "app" / "dashboard.py"
FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
SPECS = CONFIG.for_source("evds")
BDDK_SPECS = CONFIG.for_source("bddk")
TR_COLUMNS = ["Seri", "Tarih", "Son değer (milyar TL)", "Haftalık %", "Yıllık %"]


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
    evds = json.loads((FIXTURES / "evds_hpbitablo6_2024.json").read_text(encoding="utf-8"))
    bddk = json.loads((FIXTURES / "bddk_konut_2024.json").read_text(encoding="utf-8"))
    with SqliteRepository(path) as repo:
        repo.init_schema()
        for spec in SPECS + BDDK_SPECS:
            repo.upsert_series(spec)
        repo.upsert_observations("evds", parse_evds_response(evds, [s.code for s in SPECS]))
        for spec in BDDK_SPECS:  # the housing fixture stands in for every BDDK series
            repo.upsert_observations("bddk", parse_bddk_response(bddk, spec.code))
    return path


def test_empty_database_shows_instructions(use_db: Callable, tmp_path: Path) -> None:
    use_db(tmp_path / "missing.db")

    app = run_dashboard()

    assert not app.exception
    assert "Henüz veri yok" in app.info[0].value


def test_populated_database_renders_table_and_charts(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert not app.exception
    table = app.dataframe[0].value
    assert list(table.columns) == TR_COLUMNS
    assert table["Seri"].tolist() == [spec.name_tr for spec in SPECS]
    # thousand TRY -> billion TRY
    assert table["Son değer (milyar TL)"].iloc[0] == pytest.approx(3223.3)
    assert len(app.get("vega_lite_chart")) == len(SPECS)


def test_empty_selection_shows_hint(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.multiselect[0].set_value([]).run()

    assert not app.exception
    assert "En az bir seri seçin" in app.info[0].value


def test_source_picker_switches_to_bddk(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="source").set_value("bddk").run()

    assert not app.exception
    table = app.dataframe[0].value
    assert table["Seri"].tolist() == [spec.name_tr for spec in BDDK_SPECS]
    # million TRY -> billion TRY
    assert table["Son değer (milyar TL)"].iloc[0] == pytest.approx(448.6)
    assert len(app.get("vega_lite_chart")) == len(BDDK_SPECS)


def test_english_switch_translates_table(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="lang").set_value("en").run()

    assert not app.exception
    table = app.dataframe[0].value
    assert list(table.columns) == [
        "Series",
        "Date",
        "Last value (billion TRY)",
        "Weekly %",
        "Yearly %",
    ]
    assert table["Series"].tolist() == [spec.name_en for spec in SPECS]
    assert app.title[0].value == "Turkish Banking Market Dashboard"


def test_language_switch_keeps_selected_source(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()
    app.radio(key="source").set_value("bddk").run()

    app.radio(key="lang").set_value("en").run()

    assert app.radio(key="source").value == "bddk"
    assert app.dataframe[0].value["Series"].tolist() == [spec.name_en for spec in BDDK_SPECS]


def test_turkish_charts_use_turkish_number_format(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    spec = json.loads(app.get("vega_lite_chart")[0].proto.spec)
    assert spec["config"]["locale"]["number"]["decimal"] == ","
