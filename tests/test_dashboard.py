"""Smoke tests: run the Streamlit script headlessly against a temporary database."""

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Tab

from tr_banking import db as db_module
from tr_banking import settings as settings_module
from tr_banking.config import load_series_config
from tr_banking.db import Repository, SqliteRepository
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.sources.bddk import parse_bddk_response
from tr_banking.sources.bkm import parse_bkm_page, parse_series_code, sum_cells
from tr_banking.sources.evds import parse_evds_response

DASHBOARD = PROJECT_ROOT / "src" / "tr_banking" / "app" / "dashboard.py"
FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
SPECS = [spec for spec in CONFIG.for_source("evds") if spec.module == "credit"]
CPI_SPEC = CONFIG.deflator
RATE_SPECS = [spec for spec in CONFIG.series if spec.module == "rates"]
BDDK_SPECS = CONFIG.for_source("bddk")
BKM_SPECS = CONFIG.for_source("bkm")
# The real July 2026 BKM page, stored for three months at these fractions of its values.
BKM_MONTHS = {"2023-07-31": 0.5, "2024-06-30": 0.9, "2024-07-31": 1.0}
TR_COLUMNS = ["Seri", "Tarih", "Son değer (milyar TL)", "Haftalık %", "Yıllık %"]
TR_RATE_COLUMNS = [
    "Seri",
    "Tarih",
    "Son değer (%)",
    "Haftalık değişim (puan)",
    "Yıllık değişim (puan)",
    "Reel faiz ≈ (puan)",
]


@pytest.fixture
def use_db(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """Point the dashboard at a given database without reading the real .env."""

    def _use(db_path: Path) -> None:
        settings = Settings(_env_file=None, db_path=db_path)
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    return _use


def run_dashboard() -> AppTest:
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def credit_tab(app: AppTest) -> Tab:
    return app.tabs[0]


def rates_tab(app: AppTest) -> Tab:
    return app.tabs[1]


def cards_tab(app: AppTest) -> Tab:
    return app.tabs[2]


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def bkm_rows() -> pd.DataFrame:
    grids = parse_bkm_page((FIXTURES / "bkm_2026_07.html").read_text(encoding="utf-8"))
    july = {spec.code: sum_cells(grids, parse_series_code(spec.code)) for spec in BKM_SPECS}
    rows = [
        (code, date.fromisoformat(day), value * share)
        for day, share in BKM_MONTHS.items()
        for code, value in july.items()
    ]
    return pd.DataFrame(rows, columns=["code", "date", "value"])


def populated_db(path: Path, cpi_until: str = "2024-12-31") -> Path:
    """Real EVDS/BDDK fixtures in a fresh SQLite file.

    Weekly credit and loan rates 2024-06-14 .. 2024-07-12, the policy rate 2024-12-16 ..
    2025-01-10 (one cut, on 2024-12-26), monthly CPI for 2023-2024 up to `cpi_until` and BKM
    card statistics for BKM_MONTHS.
    """
    cpi_rows = pd.concat(
        parse_evds_response(fixture(name), [CPI_SPEC.code])
        for name in ("evds_cpi_2023.json", "evds_cpi_2024.json")
    )
    loan_codes = [spec.code for spec in RATE_SPECS if not spec.policy_rate]
    with SqliteRepository(path) as repo:
        repo.init_schema()
        for spec in [*SPECS, CPI_SPEC, *RATE_SPECS, *BDDK_SPECS, *BKM_SPECS]:
            repo.upsert_series(spec)
        repo.upsert_observations("bkm", bkm_rows())
        evds = fixture("evds_hpbitablo6_2024.json")
        repo.upsert_observations("evds", parse_evds_response(evds, [s.code for s in SPECS]))
        repo.upsert_observations(
            "evds", cpi_rows[cpi_rows["date"] <= date.fromisoformat(cpi_until)]
        )
        repo.upsert_observations(
            "evds", parse_evds_response(fixture("evds_loan_rates_2024.json"), loan_codes)
        )
        policy = fixture("evds_policy_rate_2024_2025.json")
        repo.upsert_observations("evds", parse_evds_response(policy, [CONFIG.policy_rate.code]))
        bddk = fixture("bddk_konut_2024.json")
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
    assert len(credit_tab(app).get("vega_lite_chart")) == len(SPECS)


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
    assert len(credit_tab(app).get("vega_lite_chart")) == len(BDDK_SPECS)


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


# --- interest rates tab ---


def test_rates_tab_shows_levels_pp_changes_and_real_rates(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Krediler", "Faizler", "Kartlar"]
    table = rates_tab(app).dataframe[0].value
    assert list(table.columns) == TR_RATE_COLUMNS
    assert table["Seri"].tolist() == [spec.name_tr for spec in RATE_SPECS]
    rows = table.set_index("Seri")
    personal = rows.loc["İhtiyaç kredisi faizi"]
    assert personal["Tarih"] == date(2024, 7, 12)
    assert personal["Son değer (%)"] == pytest.approx(77.90)
    assert personal["Haftalık değişim (puan)"] == pytest.approx(77.90 - 76.92)
    # July 2024 yearly CPI inflation was 61.78%.
    assert personal["Reel faiz ≈ (puan)"] == pytest.approx(77.90 - 61.78, abs=0.01)
    policy = rows.loc["TCMB politika faizi (1 hafta vadeli repo)"]
    assert policy["Son değer (%)"] == 47.5
    assert pd.isna(policy["Reel faiz ≈ (puan)"])  # real rate only for loan rates
    assert "Fisher denklemi değildir" in page_text(app)


def test_rates_without_cpi_leave_the_real_rate_empty(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db", cpi_until="2024-06-30"))

    app = run_dashboard()

    table = rates_tab(app).dataframe[0].value
    assert table["Reel faiz ≈ (puan)"].isna().all()  # the last loan-rate week is in July


def test_rate_charts_share_one_axis_with_inflation_and_decisions(
    use_db: Callable, tmp_path: Path
) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    charts = [json.loads(chart.proto.spec) for chart in rates_tab(app).get("vega_lite_chart")]
    assert len(charts) == len(RATE_SPECS)
    for spec in charts:
        text = json.dumps(spec, ensure_ascii=False)
        assert "Yıllık enflasyon (TÜFE)" in text  # the reference line
        assert "PPK kararı" in text  # the 26 Dec 2024 cut is in the date range
        assert '"resolve"' not in text  # no independent (dual) y-axis
    policy_marks = [
        layer["mark"] for layer in charts[-1]["layer"] if isinstance(layer["mark"], dict)
    ]
    assert {"type": "line", "interpolate": "step-after"}.items() <= policy_marks[0].items()


def test_rates_tab_in_english(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="lang").set_value("en").run()

    assert [tab.label for tab in app.tabs] == ["Loans", "Interest rates", "Cards"]
    assert list(rates_tab(app).dataframe[0].value.columns) == [
        "Series",
        "Date",
        "Last value (%)",
        "Weekly change (pp)",
        "Yearly change (pp)",
        "Real rate ≈ (pp)",
    ]


def test_empty_credit_selection_keeps_the_rates_tab(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.multiselect[0].set_value([]).run()

    assert "En az bir seri seçin" in credit_tab(app).info[0].value
    assert len(rates_tab(app).dataframe) == 1


# --- cards tab (BKM, monthly) ---


def test_cards_tab_shows_monthly_changes_and_units(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert not app.exception
    table = cards_tab(app).dataframe[0].value
    assert list(table.columns) == ["Seri", "Ay", "Son değer", "Birim", "Aylık %", "Yıllık %"]
    assert table["Seri"].tolist() == [spec.name_tr for spec in BKM_SPECS]
    rows = table.set_index("Seri")
    spending = rows.loc["Kredi kartıyla alışveriş (yurt içi)"]
    assert spending["Ay"] == date(2024, 7, 31)
    assert spending["Son değer"] == pytest.approx(2450.2)  # million TRY -> billion TRY
    assert spending["Birim"] == "milyar TL"
    assert spending["Aylık %"] == pytest.approx(100 / 0.9 - 100)
    assert spending["Yıllık %"] == pytest.approx(100.0)
    cards = rows.loc["Kredi kartı sayısı"]
    assert cards["Son değer"] == pytest.approx(151.7)  # cards -> million cards
    assert cards["Birim"] == "milyon adet"
    assert "Son veri: **Temmuz 2024**" in page_text(app)
    assert len(cards_tab(app).get("vega_lite_chart")) == len(BKM_SPECS)


def test_cards_real_view_deflates_amounts_but_not_card_counts(
    use_db: Callable, tmp_path: Path
) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()
    nominal = cards_tab(app).dataframe[0].value.set_index("Seri")

    app.radio(key="cards_value_mode").set_value("real").run()

    assert not app.exception
    real = cards_tab(app).dataframe[0].value.set_index("Seri")
    spending = "Kredi kartıyla alışveriş (yurt içi)"
    assert real.loc[spending, "Birim"] == "milyar TL, Aralık 2024 fiyatlarıyla"
    # July 2024 in December 2024 prices: higher than nominal.
    assert real.loc[spending, "Son değer"] > nominal.loc[spending, "Son değer"]
    assert "Aylık %" in real.columns  # monthly flows use their own month's CPI
    count = "Kredi kartı sayısı"
    assert real.loc[count, ["Son değer", "Birim"]].tolist() == [pytest.approx(151.7), "milyon adet"]
    assert "Kart sayıları para olmadığı için değişmez" in page_text(app)


def test_cards_tab_in_english(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="lang").set_value("en").run()

    table = cards_tab(app).dataframe[0].value
    assert list(table.columns) == ["Series", "Month", "Last value", "Unit", "Monthly %", "Yearly %"]
    assert "million cards" in table["Unit"].tolist()
