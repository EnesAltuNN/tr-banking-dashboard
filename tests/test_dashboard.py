"""Smoke tests: run the Streamlit script headlessly against a temporary database."""

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tr_banking import db as db_module
from tr_banking import settings as settings_module
from tr_banking.config import load_series_config
from tr_banking.db import Repository, SqliteRepository, Summary
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
ALL_BDDK_SPECS = CONFIG.for_source("bddk")
BDDK_SPECS = [spec for spec in ALL_BDDK_SPECS if spec.module == "credit"]
BANKING_SPECS = [spec for spec in ALL_BDDK_SPECS if spec.module == "banking"]
BANK_GROUPS = {"state_banks", "private_banks", "foreign_banks"}
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


def run_dashboard(section: str = "credit") -> AppTest:
    """Run with one tab open; tabs run lazily, so only that tab's content exists."""
    app = AppTest.from_file(str(DASHBOARD), default_timeout=60)
    app.session_state["open_section"] = section
    return app.run()


def chart_color(spec: dict) -> str:
    """The line color of a single-series chart (first layer)."""
    mark = spec["layer"][0]["mark"]
    if "color" in mark:
        return mark["color"]
    return spec["layer"][0]["encoding"]["color"]["scale"]["range"][0]


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
        for spec in [*SPECS, CPI_SPEC, *RATE_SPECS, *ALL_BDDK_SPECS, *BKM_SPECS]:
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
        for spec in ALL_BDDK_SPECS:  # the housing fixture stands in for every BDDK series
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

    app.radio(key="source_tr").set_value("bddk").run()

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
    app.radio(key="source_tr").set_value("bddk").run()

    app.radio(key="lang").set_value("en").run()

    assert app.radio(key="source_en").value == "bddk"
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


@pytest.mark.parametrize("section", ["credit", "rates", "cards"])
def test_info_line_shows_fetch_time_and_page_read_time(
    use_db: Callable, tmp_path: Path, section: str
) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard(section)

    text = page_text(app)
    assert "resmî kaynaklardan otomatik güncellenir" in text  # the one-line description
    assert "Son veri çekimi" in text
    assert re.search(r"Bu sayfa veriyi \d{2}:\d{2} \(TSİ\) itibarıyla gösteriyor", text)
    assert "en geç saatte bir yenilenir" in text
    assert "github.com/EnesAltuNN/tr-banking-dashboard" in text  # footer


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

    app.radio(key="value_mode_tr").set_value("real").run()

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

    app.radio(key="value_mode_tr").set_value("real").run()

    table = app.dataframe[0].value
    assert set(table["Tarih"]) == {date(2024, 6, 28)}  # July weeks have no CPI yet
    assert "Haziran 2024 fiyatlarıyla" in table.columns[2]
    assert "TÜFE'si olan son haftayı (**28 Haziran 2024**)" in page_text(app)


def test_real_view_without_cpi_falls_back_to_nominal(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db", cpi_until="2023-12-31"))
    app = run_dashboard()

    app.radio(key="value_mode_tr").set_value("real").run()

    assert not app.exception
    assert "TÜFE verisi henüz yok" in app.info[0].value
    assert list(app.dataframe[0].value.columns) == TR_COLUMNS


def test_english_real_view(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="lang").set_value("en").run()
    app.radio(key="value_mode_en").set_value("real").run()

    columns = list(app.dataframe[0].value.columns)
    assert columns == ["Series", "Date", "Last value (billion TRY, Dec 2024 prices)", "Yearly %"]


# --- interest rates tab ---


def test_rates_tab_shows_levels_pp_changes_and_real_rates(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("rates")

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Krediler", "Faizler", "Kartlar", "Sektör"]
    table = app.dataframe[0].value
    assert list(table.columns) == TR_RATE_COLUMNS
    assert table["Seri"].tolist() == [spec.name_tr for spec in RATE_SPECS]
    rows = table.set_index("Seri")
    personal = rows.loc["İhtiyaç kredisi faizi"]
    assert personal["Tarih"] == date(2024, 7, 12)
    assert personal["Son değer (%)"] == pytest.approx(77.90)
    assert personal["Haftalık değişim (puan)"] == pytest.approx(77.90 - 76.92)
    # July 2024 yearly CPI inflation was 61.78%: 77.90 - 61.78 = 16.12.
    assert personal["Reel faiz ≈ (puan)"] == "+16,12"
    policy = rows.loc["TCMB politika faizi (1 hafta vadeli repo)"]
    assert policy["Son değer (%)"] == 47.5
    assert policy["Reel faiz ≈ (puan)"] == "–"  # real rate only for loan rates
    assert "Fisher denklemi değildir" in page_text(app)


def test_rates_without_cpi_leave_the_real_rate_empty(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db", cpi_until="2024-06-30"))

    app = run_dashboard("rates")

    table = app.dataframe[0].value
    assert set(table["Reel faiz ≈ (puan)"]) == {"–"}  # the last loan-rate week is in July


def test_rate_charts_share_one_axis_with_inflation_and_decisions(
    use_db: Callable, tmp_path: Path
) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("rates")

    charts = [json.loads(chart.proto.spec) for chart in app.get("vega_lite_chart")]
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
    app = run_dashboard("rates")

    app.radio(key="lang").set_value("en").run()

    assert [tab.label for tab in app.tabs] == ["Loans", "Interest rates", "Cards", "Banking sector"]
    # The open tab survives the language switch (its label, and widget, changed).
    assert list(app.dataframe[0].value.columns) == [
        "Series",
        "Date",
        "Last value (%)",
        "Weekly change (pp)",
        "Yearly change (pp)",
        "Real rate ≈ (pp)",
    ]


def test_empty_selection_keeps_the_kpi_tiles(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.multiselect[0].set_value([]).run()

    assert "En az bir seri seçin" in app.info[0].value
    assert len(app.metric) == 4
    assert len(app.dataframe) == 0


# --- cards tab (BKM, monthly) ---


def test_cards_tab_shows_monthly_changes_and_units(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("cards")

    assert not app.exception
    table = app.dataframe[0].value
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
    assert len(app.get("vega_lite_chart")) == len(BKM_SPECS)


def test_cards_real_view_deflates_amounts_but_not_card_counts(
    use_db: Callable, tmp_path: Path
) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard("cards")
    nominal = app.dataframe[0].value.set_index("Seri")

    app.radio(key="cards_value_mode_tr").set_value("real").run()

    assert not app.exception
    real = app.dataframe[0].value.set_index("Seri")
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
    app = run_dashboard("cards")

    app.radio(key="lang").set_value("en").run()

    table = app.dataframe[0].value
    assert list(table.columns) == ["Series", "Month", "Last value", "Unit", "Monthly %", "Yearly %"]
    assert "million cards" in table["Unit"].tolist()


# --- layout: KPI tiles, sidebar filters, one color per category, chart sources ---


def metric_values(app: AppTest) -> dict[str, tuple[str, str]]:
    return {metric.label: (metric.value, metric.delta) for metric in app.metric}


def test_credit_kpis(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    kpis = metric_values(app)
    assert list(kpis) == [
        "Tüketici kredileri (toplam)",
        "Ticari krediler",
        "Tüketici kredileri (toplam), reel büyüme",
        "Ticari krediler, reel büyüme",
    ]
    assert kpis["Tüketici kredileri (toplam)"][0] == "3.223,3 milyar TL"
    # The fixture covers five weeks only, so there is no yearly change yet: no delta at all.
    assert not kpis["Tüketici kredileri (toplam)"][1]


def test_rate_kpis(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("rates")

    kpis = metric_values(app)
    assert list(kpis) == [
        "TCMB politika faizi (1 hafta vadeli repo)",
        "İhtiyaç kredisi faizi",
        "Yıllık enflasyon (TÜFE)",
        "İhtiyaç kredisi faizi, reel ≈",
    ]
    assert kpis["TCMB politika faizi (1 hafta vadeli repo)"][0] == "47,50%"
    # TÜİK: December 2024 44.38%, November 47.09%.
    assert kpis["Yıllık enflasyon (TÜFE)"] == ("44,4%", "-2,7 puan")
    # 12 July 2024: 77.90% minus July's 61.78% inflation.
    assert kpis["İhtiyaç kredisi faizi, reel ≈"][0] == "+16,12 puan"


def test_card_kpis(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("cards")

    kpis = metric_values(app)
    assert list(kpis) == [
        "Kartla alışveriş (kredi + banka kartı, yurt içi)",
        "İnternetten kartlı ödemeler",
        "Yabancı kartlarla alışveriş (Türkiye'de)",
    ]
    # July 2024 = the July 2026 page; credit 2,450,238.01 + debit 418,420.61 million TRY.
    assert kpis["Kartla alışveriş (kredi + banka kartı, yurt içi)"] == (
        "2.868,7 milyar TL",
        "+100,0%",
    )


def test_filters_live_in_the_sidebar_and_follow_the_open_tab(
    use_db: Callable, tmp_path: Path
) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    credit, rates = run_dashboard(), run_dashboard("rates")

    assert [widget.key for widget in credit.sidebar.radio] == ["source_tr", "value_mode_tr"]
    assert len(credit.sidebar.multiselect) == 1
    assert [widget.key for widget in rates.sidebar.radio] == []
    assert [widget.key for widget in rates.sidebar.multiselect] == ["rates_series_tr"]


def test_each_category_keeps_its_color_across_tabs(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    credit, rates = run_dashboard(), run_dashboard("rates")

    credit_charts = [json.loads(chart.proto.spec) for chart in credit.get("vega_lite_chart")]
    rate_charts = [json.loads(chart.proto.spec) for chart in rates.get("vega_lite_chart")]
    by_name = dict(zip([s.name_tr for s in SPECS], map(chart_color, credit_charts), strict=True))
    rate_by_name = dict(
        zip([s.name_tr for s in RATE_SPECS], map(chart_color, rate_charts), strict=True)
    )
    assert by_name["Konut kredileri"] == rate_by_name["Konut kredisi faizi"] == "#eb6834"
    assert by_name["İhtiyaç kredileri"] == rate_by_name["İhtiyaç kredisi faizi"] == "#4a3aa7"
    assert by_name["Ticari krediler"] == rate_by_name["Ticari kredi faizi"] == "#008300"
    assert len(set(by_name.values())) == len(by_name)  # six categories, six colors


def test_every_chart_names_its_source(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("cards")

    captions = [caption.value for caption in app.caption]
    assert captions.count("Kaynak: BKM aylık istatistikler") == len(BKM_SPECS)


def test_switching_tabs_twice_opens_each_tab(use_db: Callable, tmp_path: Path) -> None:
    # Regression: the tabs' default used to follow the open tab, which recreated the widget
    # after the first switch and dropped the second one.
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.session_state["tabs_tr"] = "Faizler"
    app.run()
    assert app.session_state["open_section"] == "rates"
    assert "TCMB politika faizi (1 hafta vadeli repo)" in [m.label for m in app.metric]

    app.session_state["tabs_tr"] = "Kartlar"
    app.run()
    assert app.session_state["open_section"] == "cards"
    assert list(app.dataframe[0].value.columns)[1] == "Ay"


def test_time_axis_labels_keep_the_year(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    spec = json.loads(app.get("vega_lite_chart")[0].proto.spec)
    label_expr = spec["layer"][0]["encoding"]["x"]["axis"]["labelExpr"]
    assert "'%b %Y'" in label_expr and "'%Y'" in label_expr


# --- banking sector tab (BDDK deposits, non-performing loans, bank groups) ---


def test_banking_tab_shows_kpis_ratios_and_sector_series(use_db: Callable, tmp_path: Path) -> None:
    # Every BDDK series holds the same housing fixture, so the ratios are easy to predict:
    # NPL / (loans + NPL) = 50%, every other ratio = 100%.
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard("banking")

    assert not app.exception
    kpis = metric_values(app)
    assert list(kpis) == [
        "Toplam mevduat",
        "Mevduatta döviz payı",
        "Takipteki alacak oranı",
        "Kredi/mevduat oranı",
    ]
    assert kpis["Takipteki alacak oranı"][0] == "50,00%"
    ratios = app.dataframe[0].value.set_index("Oran")["Son değer (%)"]
    assert len(ratios) == 11
    assert ratios["Takipteki alacak oranı"] == pytest.approx(50.0)
    assert ratios["Kredilerdeki pay: kamu bankaları"] == pytest.approx(100.0)
    # Sector series are shown by default; bank-group amounts are one click away.
    amounts = app.dataframe[1].value
    sector = [spec.name_tr for spec in BANKING_SPECS if spec.category not in BANK_GROUPS]
    assert amounts["Seri"].tolist() == sector
    assert len(app.get("vega_lite_chart")) == 4 + len(sector)
    assert "takipteki / (krediler + takipteki)" in page_text(app)


def test_banking_tab_in_english(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard("banking")

    app.radio(key="lang").set_value("en").run()

    assert list(app.dataframe[0].value.columns) == [
        "Ratio",
        "Date",
        "Last value (%)",
        "Weekly change (pp)",
        "Yearly change (pp)",
    ]
    assert "Non-performing loan ratio" in [metric.label for metric in app.metric]


def test_banking_series_stay_out_of_the_loans_tab(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()

    app.radio(key="source_tr").set_value("bddk").run()

    assert "Toplam mevduat" not in app.dataframe[0].value["Seri"].tolist()


# --- translated widgets keep their choice across a language switch ---


def test_real_view_survives_a_language_switch(use_db: Callable, tmp_path: Path) -> None:
    # Streamlit stores a radio's choice as its label; "Reel (...)" is not an English label.
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()
    app.radio(key="value_mode_tr").set_value("real").run()

    app.radio(key="lang").set_value("en").run()

    assert app.radio(key="value_mode_en").value == "real"
    assert "Dec 2024 prices" in list(app.dataframe[0].value.columns)[2]


def test_series_selection_survives_a_language_switch(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))
    app = run_dashboard()
    picked = app.multiselect[0].value[:2]
    app.multiselect[0].set_value(picked).run()

    app.radio(key="lang").set_value("en").run()

    assert app.multiselect[0].value == picked
    assert app.dataframe[0].value["Series"].tolist() == [spec.name_en for spec in SPECS[:2]]


# --- alerts on unusual weekly changes ---


def spiked_db(path: Path) -> Path:
    """Sixty weeks of steady ~1% growth for the EVDS loans, then a -6% week for housing."""
    fridays = pd.date_range("2025-01-03", periods=60, freq="W-FRI")
    rows = []
    for index, spec in enumerate(SPECS):
        value = 1_000_000.0 * (index + 1)
        for step, friday in enumerate(fridays):
            if step:
                wobble = 0.002 * ((step * (index + 3)) % 5)  # deterministic small noise
                last_week = step == len(fridays) - 1
                growth = 0.94 if (last_week and spec.category == "housing") else 1.01 + wobble
                value *= growth
            rows.append((spec.code, friday.date(), value))
    with SqliteRepository(path) as repo:
        repo.init_schema()
        for spec in SPECS:
            repo.upsert_series(spec)
        repo.upsert_observations("evds", pd.DataFrame(rows, columns=["code", "date", "value"]))
    return path


def test_no_alert_note_when_nothing_is_unusual(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert not app.warning
    assert "Son haftada olağan dışı bir değişim yok" in page_text(app)


def test_unusual_weekly_change_is_flagged_above_the_tabs(use_db: Callable, tmp_path: Path) -> None:
    use_db(spiked_db(tmp_path / "spike.db"))

    app = run_dashboard("rates")  # alerts show on every tab

    assert not app.exception
    [warning] = app.warning
    assert "Son haftada olağan dışı değişim" in warning.value
    assert "Konut kredileri (TCMB EVDS)" in warning.value
    assert "**-6,0%**" in warning.value
    assert "İhtiyaç kredileri" not in warning.value  # steady series stay quiet


# --- weekly AI summary ---


def test_weekly_summary_is_shown_and_labelled(use_db: Callable, tmp_path: Path) -> None:
    db_path = populated_db(tmp_path / "test.db")
    with SqliteRepository(db_path) as repo:
        repo.upsert_summary(
            Summary(
                date(2024, 7, 12),
                datetime(2024, 7, 19, 5, 0, tzinfo=UTC),
                "claude-opus-5",
                "{}",
                "Tüketici kredileri yıllık %37,7 arttı.",
                "Consumer loans rose 37.7%.",
            )
        )
    use_db(db_path)
    app = run_dashboard()

    text = page_text(app)
    assert "Haftanın özeti" in text
    assert "Tüketici kredileri yıllık %37,7 arttı." in text
    assert "Metni yapay zekâ (claude-opus-5) yazdı" in text

    app.radio(key="lang").set_value("en").run()
    assert "Consumer loans rose 37.7%." in page_text(app)


def test_no_summary_box_without_a_summary(use_db: Callable, tmp_path: Path) -> None:
    use_db(populated_db(tmp_path / "test.db"))

    app = run_dashboard()

    assert "Haftanın özeti" not in page_text(app)
