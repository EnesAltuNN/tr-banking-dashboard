"""Smoke tests: run the Streamlit script headlessly against a temporary database."""

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tr_banking import db as db_module
from tr_banking import settings as settings_module
from tr_banking.config import load_series_config
from tr_banking.db import Repository, SqliteRepository
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.bddk import parse_bddk_response
from tr_banking.sources.evds import parse_evds_response

DASHBOARD = PROJECT_ROOT / "src" / "tr_banking" / "app" / "dashboard.py"
FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
SPECS = [spec for spec in CONFIG.for_source("evds") if spec.module == "credit"]
CPI_SPEC = CONFIG.deflator
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


def populated_db(path: Path, cpi_until: str = "2024-12-31") -> Path:
    """Weekly credit data 2024-06-14 .. 2024-07-12 and monthly CPI for 2024 up to `cpi_until`."""
    evds = json.loads((FIXTURES / "evds_hpbitablo6_2024.json").read_text(encoding="utf-8"))
    cpi = json.loads((FIXTURES / "evds_cpi_2024.json").read_text(encoding="utf-8"))
    bddk = json.loads((FIXTURES / "bddk_konut_2024.json").read_text(encoding="utf-8"))
    cpi_rows = parse_evds_response(cpi, [CPI_SPEC.code])
    cpi_rows = cpi_rows[cpi_rows["date"] <= date.fromisoformat(cpi_until)]
    with SqliteRepository(path) as repo:
        repo.init_schema()
        for spec in [*SPECS, CPI_SPEC, *BDDK_SPECS]:
            repo.upsert_series(spec)
        repo.upsert_observations("evds", parse_evds_response(evds, [s.code for s in SPECS]))
        repo.upsert_observations(
            "evds", cpi_rows[cpi_rows["date"] <= date.fromisoformat(cpi_until)]
        )
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


# --- caching, freshness line and database errors (phase E) ---


def page_text(app: AppTest) -> str:
    elements = [*app.title, *app.markdown, *app.caption, *app.info, *app.error, *app.warning]
    return "\n".join(str(element.value) for element in elements)


def test_status_shows_fetch_time_and_page_read_time(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    text = page_text(app)
    assert "Son veri çekimi" in text
    assert re.search(r"Bu sayfa veriyi \d{2}:\d{2} \(TSİ\) itibarıyla gösteriyor", text)
    assert "en geç saatte bir yenilenir" in text


def test_database_is_read_once_per_cache_period(
    use_db: Callable, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[Path] = []
    real_open = db_module.open_repository

    def counting_open(settings: Settings) -> Repository:
        opened.append(settings.db_path)
        return real_open(settings)

    monkeypatch.setattr(db_module, "open_repository", counting_open)
    use_db(populated_db(tmp_path / "cache.db"))

    first, second = run_dashboard(), run_dashboard()  # e.g. two visitors within the hour

    assert not first.exception and not second.exception
    assert opened == [tmp_path / "cache.db"]


def test_unreachable_database_shows_a_plain_message(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 on localhost refuses at once; the message must reveal neither host nor password.
    settings = Settings(
        _env_file=None,
        database_url="postgresql://dashboard_reader.ref:pw-must-not-leak@127.0.0.1:1/postgres",
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    app = run_dashboard()

    assert not app.exception
    assert "Veritabanına şu anda ulaşılamıyor" in app.error[0].value
    text = page_text(app)
    for leak in ("pw-must-not-leak", "127.0.0.1", "dashboard_reader.ref"):
        assert leak not in text


# --- real (inflation-adjusted) view ---


def test_price_index_is_not_shown_as_a_credit_series(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert CPI_SPEC.name_tr not in app.dataframe[0].value["Seri"].tolist()
    assert CPI_SPEC.name_tr not in app.multiselect[0].options


def test_real_view_uses_prices_of_the_latest_cpi_month(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()
    nominal = app.dataframe[0].value["Son değer (milyar TL)"].iloc[0]

    app.radio(key="value_mode").set_value("real").run()

    assert not app.exception
    table = app.dataframe[0].value
    # The weekly % is hidden: real values move in monthly CPI steps.
    assert list(table.columns) == [
        "Seri",
        "Tarih",
        "Son değer (milyar TL, Aralık 2024 fiyatlarıyla)",
        "Yıllık %",
    ]
    # The last week is in July 2024; December 2024 prices are higher, so the real value is too.
    assert table.iloc[0, 2] > nominal
    assert "Aralık 2024 fiyatlarıyla" in page_text(app)


def test_real_view_ends_at_the_last_week_with_cpi(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db", cpi_until="2024-06-30"))
    app = run_dashboard()

    app.radio(key="value_mode").set_value("real").run()

    table = app.dataframe[0].value
    assert set(table["Tarih"]) == {date(2024, 6, 28)}  # July weeks have no CPI yet
    assert "Haziran 2024 fiyatlarıyla" in table.columns[2]
    assert "TÜFE'si olan son haftayı (**28 Haziran 2024**)" in page_text(app)


def test_real_view_without_cpi_falls_back_to_nominal(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db", cpi_until="2023-12-31"))
    app = run_dashboard()

    app.radio(key="value_mode").set_value("real").run()

    assert not app.exception
    assert "TÜFE verisi henüz yok" in app.info[0].value
    assert list(app.dataframe[0].value.columns) == TR_COLUMNS


def test_english_real_view(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="lang").set_value("en").run()
    app.radio(key="value_mode").set_value("real").run()

    columns = list(app.dataframe[0].value.columns)
    assert columns == ["Series", "Date", "Last value (billion TRY, Dec 2024 prices)", "Yearly %"]
