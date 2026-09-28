"""Streamlit dashboard. Run with: uv run streamlit run src/tr_banking/app/dashboard.py"""

import html
import logging
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import psycopg
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from tr_banking.app.i18n import (
    LANGUAGES,
    MISSING,
    SOURCE_LABELS,
    SOURCE_NOTES,
    VEGA_LOCALE_TR,
    Lang,
    change_direction,
    format_date,
    format_month,
    format_number,
    format_pct,
    format_signed,
    text,
    unit_label,
)
from tr_banking.app.metrics import (
    MONETARY_UNITS,
    YEAR,
    annual_inflation,
    deflate,
    display_unit,
    rate_changes,
    real_rates,
    summarize,
    summarize_rates,
    value_near,
)
from tr_banking.config import SeriesConfig, SeriesSpec, load_series_config
from tr_banking.db import StorageError, open_repository
from tr_banking.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")
# The data changes twice a week, so one read per hour is plenty. st.cache_data is shared by
# every visitor, so a public page costs Supabase at most one connection per hour, not one per
# visitor or click.
CACHE_TTL = timedelta(hours=1)
SECTIONS = ("credit", "rates", "cards")

# Categorical palette validated for color-vision deficiency and contrast in each mode (the
# same steps as chartCategoricalColors in .streamlit/config.toml). Each category keeps one hue
# in every chart; totals and other aggregates take the first slot. Three light steps are below
# 3:1 on the light surface, so every chart names its series in the title and every tab has a
# table view.
PALETTE = {
    "light": {
        "blue": "#2a78d6",
        "orange": "#eb6834",
        "aqua": "#1baf7a",
        "yellow": "#eda100",
        "magenta": "#e87ba4",
        "green": "#008300",
        "violet": "#4a3aa7",
        "red": "#e34948",
    },
    "dark": {
        "blue": "#3987e5",
        "orange": "#d95926",
        "aqua": "#199e70",
        "yellow": "#c98500",
        "magenta": "#d55181",
        "green": "#008300",
        "violet": "#9085e9",
        "red": "#e66767",
    },
}
CATEGORY_HUES = {
    "housing": "orange",
    "auto": "aqua",
    "debit_card": "yellow",
    "credit_card": "magenta",
    "commercial": "green",
    "personal": "violet",
    "policy": "red",
}
CROSSHAIR_COLOR = "#898781"
# Yearly inflation is a reference, not a series of its own: a muted grey, dashed line.
INFLATION_COLORS = {"light": "#6f6d66", "dark": "#a3a19a"}
RATE_DECIMALS = 2  # EVDS publishes rates with two decimals, e.g. 41.96
# Up / down text colors, >= 4.5:1 on each theme's background. The sign (+/-) is always shown
# too, so the direction never depends on color alone.
DELTA_COLORS = {
    "light": {1: "#006300", -1: "#d03b3b"},
    "dark": {1: "#0ca30c", -1: "#e66767"},
}
CHARTS_PER_ROW = 2  # st.columns stack into one column on narrow (mobile) screens
CHART_HEIGHT = 260
# Time-axis labels that never lose the year: "2025" on January ticks, "Tem 2024" on other
# month starts and "12 Tem" on day ticks. Vega's default shows a bare month name, which on a
# narrow chart turns into "Temmuz Temmuz Temmuz" across three years.
DATE_AXIS = alt.Axis(
    labelFlush=True,  # keep the first and last label inside narrow (mobile) charts
    labelExpr=(
        "date(datum.value) != 1 ? timeFormat(datum.value, '%d %b')"
        " : month(datum.value) == 0 ? timeFormat(datum.value, '%Y')"
        " : timeFormat(datum.value, '%b %Y')"
    ),
)
# Sparkline lengths in the KPI tiles: about one year.
SPARK_POINTS = {"weekly": 52, "monthly": 24, "daily": 260}
# Streamlit has no size option for tab labels; the labels are the page's main navigation.
TAB_STYLE = '<style>[data-testid="stTab"] p { font-size: 1.2rem; font-weight: 600; }</style>'


class Tile(NamedTuple):
    """One KPI tile: a formatted value, an optional signed change and a sparkline."""

    label: str
    value: str
    change: float | None = None  # drives the arrow and color
    change_text: str | None = None  # the same change, formatted with its sign and unit
    description: str | None = None  # short: shown next to the change
    details: str | None = None  # longer context (month, week) in the tile's help tooltip
    sparkline: list[float] | None = None
    decimals: int = 1  # rounding used to decide whether the change is zero


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_data(
    storage_key: str, _settings: Settings
) -> tuple[pd.DataFrame, pd.DataFrame, datetime | None, datetime]:
    """Series, observations, last fetch time and when this snapshot was read.

    The connection is opened only on a cache miss and closed right after reading. A pooled
    connection kept open across the app's sleep would go stale, while one short read per hour
    is cheap. Streamlit caches by `storage_key` only; the leading underscore keeps `_settings`
    (which holds secrets) out of the cache key.
    """
    loaded_at = datetime.now(UTC)
    if _settings.database_url is None and not Path(_settings.db_path).exists():
        return pd.DataFrame(), pd.DataFrame(), None, loaded_at
    with open_repository(_settings) as repo:
        return repo.list_series(), repo.get_observations(), repo.last_fetched_at(), loaded_at


def storage_key(settings: Settings) -> str:
    return "postgres" if settings.database_url is not None else str(settings.db_path)


def main() -> None:
    st.set_page_config(page_title="TR Banking Dashboard", layout="wide")
    theme = st.context.theme.type or "light"
    lang = render_header()

    settings = get_settings()
    try:
        series, observations, fetched_at, loaded_at = load_data(storage_key(settings), settings)
    except (StorageError, psycopg.Error):
        # Details (host, role) go to the server log only; visitors see a plain message.
        logger.exception("dashboard could not read the database")
        st.error(text("db_unavailable", lang))
        st.stop()
    if observations.empty:
        st.info(text("no_data", lang))
        st.stop()
    render_info_line(fetched_at, loaded_at, lang)

    config = load_series_config(settings.series_config_path)
    series = with_categories(series, config)
    price_index = spec_observations(series, observations, config.deflator)
    policy_id = spec_id(series, config.policy_rate)

    st.sidebar.header(text("filters", lang))
    section, tab = render_tabs(lang)
    with tab:
        module = series[series["module"] == section]
        if section == "credit":
            render_credit_view(module, observations, price_index, lang, theme)
        elif section == "rates":
            render_rates_view(module, observations, price_index, policy_id, lang, theme)
        else:
            render_cards_view(module, observations, price_index, lang, theme)
    render_footer(lang)


def render_header() -> Lang:
    """Title and one-line description on the left, TR/EN switch on the right."""
    title_column, language_column = st.columns([6, 1])
    lang = language_column.radio(
        "Dil / Language",
        options=list(LANGUAGES),
        format_func=LANGUAGES.__getitem__,
        horizontal=True,
        key="lang",
    )
    title_column.title(text("title", lang))
    title_column.markdown(text("tagline", lang))
    return lang


def render_info_line(fetched_at: datetime | None, loaded_at: datetime, lang: Lang) -> None:
    """When the fetch job last wrote data, and when this (cached) page read it."""
    updated = "-"
    if fetched_at:
        local = fetched_at.astimezone(DISPLAY_TZ)
        updated = f"{format_date(local, lang, long=True)} {local:%H:%M}"
    loaded = loaded_at.astimezone(DISPLAY_TZ).strftime("%H:%M")
    st.caption(text("info_line", lang).format(updated=updated, loaded=loaded))


def render_tabs(lang: Lang) -> tuple[str, DeltaGenerator]:
    """The open section and its tab. Only that tab's code runs, and only its filters appear in
    the sidebar. The open section is remembered by key rather than label, so switching the
    language keeps it open (the labels, and with them the widget, change)."""
    st.markdown(TAB_STYLE, unsafe_allow_html=True)
    # `default` is part of the widget's identity: change it only when the language changes.
    # Changing it after every click would recreate the widget and drop the next click.
    if st.session_state.get("tabs_lang") != lang:
        st.session_state["tabs_lang"] = lang
        st.session_state["tabs_default"] = st.session_state.get("open_section", SECTIONS[0])
    tabs = st.tabs(
        [text(f"tab_{section}", lang) for section in SECTIONS],
        default=text(f"tab_{st.session_state['tabs_default']}", lang),
        key=f"tabs_{lang}",
        on_change="rerun",
    )
    index = next((i for i, tab in enumerate(tabs) if tab.open), 0)
    st.session_state["open_section"] = SECTIONS[index]
    return SECTIONS[index], tabs[index]


def render_footer(lang: Lang) -> None:
    st.divider()
    st.caption(text("footer_sources", lang))
    st.caption(text("footer_values", lang))


def with_categories(series: pd.DataFrame, config: SeriesConfig) -> pd.DataFrame:
    """Adds each series' config-only `category` (not stored in the database)."""
    categories = {(spec.source, spec.code): spec.category for spec in config.series}
    return series.assign(
        category=[categories.get((row.source, row.code)) for row in series.itertuples()]
    )


def spec_id(series: pd.DataFrame, spec: SeriesSpec | None) -> int | None:
    """Database id of a configured series, or None if it is not configured or not loaded."""
    if spec is None:
        return None
    ids = series.loc[(series["source"] == spec.source) & (series["code"] == spec.code), "id"]
    return int(ids.iloc[0]) if not ids.empty else None


def spec_observations(
    series: pd.DataFrame, observations: pd.DataFrame, spec: SeriesSpec | None
) -> pd.DataFrame:
    """date/value rows of a configured series such as the deflator (may be empty)."""
    rows = observations[observations["series_id"] == spec_id(series, spec)]
    return rows[["date", "value"]].reset_index(drop=True)


def category_id(series: pd.DataFrame, category: str, monetary: bool | None = None) -> int | None:
    """The first series of a category, optionally only TRY amounts (or only non-amounts)."""
    rows = series[series["category"] == category]
    if monetary is not None:
        rows = rows[rows["unit"].isin(MONETARY_UNITS) == monetary]
    return int(rows["id"].iloc[0]) if not rows.empty else None


def series_color(category: str | None, theme: str) -> str:
    return PALETTE.get(theme, PALETTE["light"])[CATEGORY_HUES.get(category or "", "blue")]


# --- sections ---


def render_credit_view(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    price_index: pd.DataFrame,
    lang: Lang,
    theme: str,
) -> None:
    source = render_source_picker(series, lang)
    real = render_value_mode(lang) == "real"
    series = series[series["source"] == source]
    observations = observations[observations["series_id"].isin(series["id"])]
    render_kpis(credit_tiles(series, observations, price_index, lang), theme)

    filters = render_filters(series, observations, lang, key=f"credit_{source}")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]
    week = format_date(selected["date"].max(), lang, long=True)
    st.caption(text("latest_week", lang).format(week=week))

    month = None
    if real:
        selected, month = deflate_money(selected, series, price_index, lang)

    # The table uses full history up to `end`, so yearly % works even for a short date range.
    summary = summarize(selected, as_of=end)
    # Real values move in monthly CPI steps, so a weekly % would mislead.
    render_summary_table(summary, series, lang, theme, month, show_prev=month is None)

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme, month)
    with st.expander(text("notes", lang)):
        if month is None:
            st.caption(text("inflation_note", lang))
        else:
            real_week = format_date(summary["last_date"].max(), lang, long=True)
            st.caption(text("real_note", lang).format(month=month, week=real_week))
        st.caption(f"{SOURCE_NOTES[source][lang]} {text('comparison_note', lang)}")


def render_rates_view(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    price_index: pd.DataFrame,
    policy_id: int | None,
    lang: Lang,
    theme: str,
) -> None:
    """Loan rates and the policy rate in % and pp: no real conversion, no billion scaling."""
    observations = observations[observations["series_id"].isin(series["id"])]
    if observations.empty:
        st.info(text("rates_unavailable", lang))
        return
    inflation = annual_inflation(price_index) if not price_index.empty else price_index
    render_kpis(rate_tiles(series, observations, inflation, policy_id, lang), theme)

    filters = render_filters(series, observations, lang, key="rates")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]

    loan_ids = [int(series_id) for series_id in series["id"] if series_id != policy_id]
    summary = summarize_rates(selected, inflation, as_of=end, real_rate_ids=loan_ids)
    render_rates_table(summary, series, lang, theme)

    policy = observations[observations["series_id"] == policy_id]
    changes = rate_changes(policy[["date", "value"]])

    def in_range(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame["date"].between(pd.Timestamp(start), pd.Timestamp(end))]

    render_rate_charts(
        in_range(selected),
        in_range(inflation),
        in_range(changes),
        series.set_index("id"),
        selected_ids,
        policy_id,
        lang,
        theme,
    )
    with st.expander(text("notes", lang)):
        st.caption(text("rates_note", lang))


def render_cards_view(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    price_index: pd.DataFrame,
    lang: Lang,
    theme: str,
) -> None:
    """BKM monthly card statistics; in the real view only the TRY amounts are deflated."""
    observations = observations[observations["series_id"].isin(series["id"])]
    if observations.empty:
        st.info(text("cards_unavailable", lang))
        return
    real = render_value_mode(lang, key="cards_value_mode") == "real"
    render_kpis(card_tiles(series, observations, lang), theme)

    filters = render_filters(series, observations, lang, key="cards")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]
    latest = format_month(selected["date"].max(), lang)
    st.caption(text("latest_month", lang).format(month=latest))

    month = None
    if real:
        selected, month = deflate_money(selected, series, price_index, lang)
    # Monthly flows are deflated by their own month's CPI, so monthly % stays meaningful.
    summary = summarize(selected, as_of=end, frequency="monthly")
    render_summary_table(summary, series, lang, theme, month, monthly=True)

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme, month, monthly=True)
    with st.expander(text("notes", lang)):
        if month is not None:
            st.caption(text("cards_real_note", lang).format(month=month))
        st.caption(text("cards_note", lang))


# --- KPI tiles ---


def credit_tiles(
    series: pd.DataFrame, observations: pd.DataFrame, price_index: pd.DataFrame, lang: Lang
) -> list[Tile]:
    """Consumer and commercial loans (level, yearly %) and their real yearly growth."""
    meta = series.set_index("id")
    tiles, real_tiles = [], []
    for category in ("consumer", "commercial"):
        series_id = category_id(series, category)
        if series_id is None:
            continue
        rows = observations[observations["series_id"] == series_id]
        name = meta.loc[series_id, f"name_{lang}"]
        factor, unit = shown_unit(meta.loc[series_id, "unit"], lang, None)
        last = summarize(rows).iloc[0]
        tiles.append(
            Tile(
                label=name,
                value=f"{format_number(last.last_value * factor, lang)} {unit}",
                change=last.yoy_pct,
                change_text=format_pct(last.yoy_pct, lang),
                description=text("desc_yearly", lang),
                sparkline=sparkline(rows, "weekly", factor),
            )
        )
        if price_index.empty:
            continue
        real, reference = deflate(rows, price_index)
        if real.empty:
            continue
        real_last = summarize(real).iloc[0]
        real_tiles.append(
            Tile(
                label=text("kpi_real_growth", lang).format(name=name),
                value=format_pct(real_last.yoy_pct, lang),
                description=text("desc_real_short", lang),
                details=text("desc_real", lang).format(month=format_month(reference, lang)),
                sparkline=sparkline(real, "weekly", factor),
            )
        )
    return tiles + real_tiles


def rate_tiles(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    inflation: pd.DataFrame,
    policy_id: int | None,
    lang: Lang,
) -> list[Tile]:
    """Policy rate, personal loan rate, yearly inflation and the real personal loan rate."""
    meta = series.set_index("id")
    pp = text("pp", lang)

    def rate_tile(series_id: int, frequency: str) -> Tile:
        rows = observations[observations["series_id"] == series_id]
        last = summarize_rates(rows, inflation).iloc[0]
        return Tile(
            label=meta.loc[series_id, f"name_{lang}"],
            value=f"{format_number(last.last_value, lang, RATE_DECIMALS)}%",
            change=last.yoy_pp,
            change_text=f"{format_signed(last.yoy_pp, lang, RATE_DECIMALS)} {pp}",
            description=text("desc_yearly", lang),
            sparkline=sparkline(rows, frequency),
            decimals=RATE_DECIMALS,
        )

    tiles = []
    if policy_id is not None:
        tiles.append(rate_tile(policy_id, "daily"))
    personal_id = category_id(series, "personal")
    if personal_id is not None:
        tiles.append(rate_tile(personal_id, "weekly"))
    if not inflation.empty:
        values = inflation.sort_values("date")["value"]
        month_change = values.iloc[-1] - values.iloc[-2] if len(values) > 1 else float("nan")
        month = format_month(inflation["date"].max(), lang)
        tiles.append(
            Tile(
                label=text("kpi_inflation", lang),
                value=f"{format_number(values.iloc[-1], lang)}%",
                change=month_change,
                change_text=f"{format_signed(month_change, lang)} {pp}",
                description=text("desc_monthly", lang),
                details=month,
                sparkline=sparkline(inflation, "monthly"),
            )
        )
    if personal_id is not None and not inflation.empty:
        rows = observations[observations["series_id"] == personal_id]
        real = real_rates(rows, inflation)
        if not real.empty:
            values = real.set_index("date")["value"].sort_index()
            last_date = values.index.max()
            change = values[last_date] - value_near(values, last_date - YEAR)
            week = format_date(last_date, lang, long=True)
            name = meta.loc[personal_id, f"name_{lang}"]
            tiles.append(
                Tile(
                    label=text("kpi_real_rate", lang).format(name=name),
                    value=f"{format_signed(values[last_date], lang, RATE_DECIMALS)} {pp}",
                    change=change,
                    change_text=f"{format_signed(change, lang, RATE_DECIMALS)} {pp}",
                    description=text("desc_yearly", lang),
                    details=text("kpi_real_rate_desc", lang).format(week=week),
                    sparkline=sparkline(real, "weekly"),
                    decimals=RATE_DECIMALS,
                )
            )
    return tiles


def card_tiles(series: pd.DataFrame, observations: pd.DataFrame, lang: Lang) -> list[Tile]:
    """Last month's card spending (credit + debit), online payments and foreign cards."""
    meta = series.set_index("id")
    factor, unit = shown_unit("million TRY", lang, None)
    candidates: list[tuple[str, list[int | None]]] = [
        (
            text("kpi_card_spending", lang),
            [category_id(series, "credit_card", True), category_id(series, "debit_card", True)],
        )
    ]
    for category in ("online", "foreign"):
        series_id = category_id(series, category, True)
        label = meta.loc[series_id, f"name_{lang}"] if series_id is not None else ""
        candidates.append((label, [series_id]))

    tiles = []
    for label, ids in candidates:
        if None in ids:
            continue
        rows = observations[observations["series_id"].isin(ids)]
        # A sum only for months where every part is published.
        by_month = rows.groupby("date")["value"].agg(["sum", "count"])
        total = by_month.loc[by_month["count"] == len(ids), "sum"]
        frame = pd.DataFrame({"series_id": 0, "date": total.index, "value": total.to_numpy()})
        last = summarize(frame, frequency="monthly").iloc[0]
        month = format_month(last.last_date, lang)
        tiles.append(
            Tile(
                label=label,
                value=f"{format_number(last.last_value * factor, lang)} {unit}",
                change=last.yoy_pct,
                change_text=format_pct(last.yoy_pct, lang),
                description=text("desc_yearly", lang),
                details=month,
                sparkline=sparkline(frame, "monthly", factor),
            )
        )
    return tiles


def sparkline(rows: pd.DataFrame, frequency: str, factor: float = 1.0) -> list[float]:
    """About the last year of values, oldest first, for a KPI tile's small chart."""
    values = rows.sort_values("date")["value"].tail(SPARK_POINTS[frequency]) * factor
    return [round(value, 4) for value in values]


def render_kpis(tiles: list[Tile], theme: str) -> None:
    """A row of bordered st.metric tiles. The change always carries its sign; a change that
    rounds to zero gets neither color nor arrow (st.metric would show "0,0%" as a rise)."""
    if not tiles:
        return
    for column, tile in zip(st.columns(len(tiles)), tiles, strict=True):
        has_change = tile.change is not None and not math.isnan(tile.change)
        direction = change_direction(tile.change, tile.decimals) if has_change else 0
        column.metric(
            tile.label,
            tile.value,
            delta=tile.change_text if has_change else None,
            delta_color="normal" if direction else "off",
            delta_arrow="auto" if direction else "off",
            delta_description=tile.description,
            help=tile.details,
            border=True,
            chart_data=tile.sparkline or None,
            chart_type="line",
        )


# --- sidebar filters ---


def render_source_picker(series: pd.DataFrame, lang: Lang) -> str:
    """One source at a time: each has its own history length, units and definitions."""
    available = [source for source in SOURCE_LABELS if source in set(series["source"])]
    return st.sidebar.radio(
        text("source", lang),
        options=available,
        format_func=lambda source: SOURCE_LABELS[source][lang],
        key="source",
    )


def render_value_mode(lang: Lang, key: str = "value_mode") -> str:
    return st.sidebar.radio(
        text("value_mode", lang),
        options=["nominal", "real"],
        format_func=lambda mode: text(f"mode_{mode}", lang),
        key=key,
    )


def render_filters(
    series: pd.DataFrame, observations: pd.DataFrame, lang: Lang, key: str
) -> tuple[list[int], date, date] | None:
    """Series and date range pickers in the sidebar; None (after a hint) while the choice is
    incomplete."""
    labels = {int(row.id): getattr(row, f"name_{lang}") for row in series.itertuples()}
    first, last = observations["date"].min().date(), observations["date"].max().date()

    selected_ids = st.sidebar.multiselect(
        text("series", lang),
        options=list(labels),
        default=list(labels),
        format_func=labels.__getitem__,
        key=f"{key}_series",
    )
    picked = st.sidebar.date_input(
        text("date_range", lang),
        value=(first, last),
        min_value=first,
        max_value=last,
        format="DD.MM.YYYY" if lang == "tr" else "YYYY-MM-DD",
        key=f"{key}_range",
    )

    if not selected_ids:
        st.info(text("select_series", lang))
        return None
    # While the user is still choosing, date_input returns only the start date.
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info(text("pick_end", lang))
        return None
    start, end = picked
    return selected_ids, start, end


# --- values ---


def deflate_money(
    observations: pd.DataFrame, series: pd.DataFrame, price_index: pd.DataFrame, lang: Lang
) -> tuple[pd.DataFrame, str | None]:
    """Real values for the TRY series (others unchanged) and the price month's label.

    Without CPI for any of those rows it shows a hint and returns the nominal values and None.
    """
    money_ids = series.loc[series["unit"].isin(MONETARY_UNITS), "id"]
    is_money = observations["series_id"].isin(money_ids)
    if not price_index.empty and is_money.any():
        real, reference = deflate(observations[is_money], price_index)
        if not real.empty:
            rows = pd.concat([real, observations[~is_money]], ignore_index=True)
            return rows, format_month(reference, lang)
    st.info(text("real_unavailable", lang))
    return observations, None


def shown_unit(stored_unit: str, lang: Lang, month: str | None) -> tuple[float, str]:
    """Display multiplier and label; real TRY values get "..., Aug 2026 prices"."""
    factor, unit = display_unit(stored_unit)
    label = unit_label(unit, lang)
    if month is not None and stored_unit in MONETARY_UNITS:
        label = text("real_unit", lang).format(unit=label, month=month)
    return factor, label


# --- tables ---


def render_summary_table(
    summary: pd.DataFrame,
    series: pd.DataFrame,
    lang: Lang,
    theme: str,
    month: str | None = None,
    show_prev: bool = True,
    monthly: bool = False,
) -> None:
    """Latest value, change on the previous period and yearly change per series.

    `month` names the price month of real values; `show_prev` hides the previous-period
    change; `monthly` labels periods as months instead of weeks.
    """
    table = summary.merge(series, left_on="series_id", right_on="id")
    factors = table["unit"].map(lambda unit: shown_unit(unit, lang, month)[0])
    units = table["unit"].map(lambda unit: shown_unit(unit, lang, month)[1])

    series_col = text("col_series", lang)
    date_col = text("col_month" if monthly else "col_date", lang)
    wow_col, yoy_col = text("col_mom" if monthly else "col_wow", lang), text("col_yoy", lang)
    format_period = format_month if monthly else format_date
    # One display unit for every row: put it in the header instead of repeating it per row.
    last_col = text("col_last", lang)
    if units.nunique() == 1:
        last_col = f"{last_col} ({units.iloc[0]})"

    frame = pd.DataFrame(
        {
            series_col: table[f"name_{lang}"],
            date_col: table["last_date"].dt.date,
            last_col: (table["last_value"] * factors).round(1),
            wow_col: table["prev_pct"],
            yoy_col: table["yoy_pct"],
        }
    )
    if units.nunique() > 1:
        frame.insert(3, text("col_unit", lang), units)
    pct_cols = [wow_col, yoy_col]
    if not show_prev:
        frame = frame.drop(columns=[wow_col])
        pct_cols = [yoy_col]

    # The Styler changes only what is displayed; cells stay numeric, so sorting still works.
    styled = (
        frame.style.format(lambda value: format_number(value, lang), subset=[last_col])
        .format(lambda value: format_pct(value, lang), subset=pct_cols, na_rep=MISSING)
        .format(lambda day: format_period(day, lang), subset=[date_col])
        .map(delta_style(theme), subset=pct_cols)
    )
    st.dataframe(styled, hide_index=True)


def delta_style(theme: str, decimals: int = 1) -> Callable[[float], str]:
    """Cell style: up / down color, judged with the same rounding as the displayed number."""
    colors = DELTA_COLORS.get(theme, DELTA_COLORS["light"])

    def style(value: float) -> str:
        direction = change_direction(value, decimals)
        return f"color: {colors[direction]}" if direction else ""

    return style


def render_rates_table(summary: pd.DataFrame, series: pd.DataFrame, lang: Lang, theme: str) -> None:
    table = summary.merge(series, left_on="series_id", right_on="id")
    series_col, date_col = text("col_series", lang), text("col_date", lang)
    last_col = f"{text('col_last', lang)} ({table['unit'].iloc[0]})"
    wow_col, yoy_col = text("col_wow_pp", lang), text("col_yoy_pp", lang)
    real_col = text("col_real_rate", lang)
    frame = pd.DataFrame(
        {
            series_col: table[f"name_{lang}"],
            date_col: table["last_date"].dt.date,
            last_col: table["last_value"],
            wow_col: table["wow_pp"],
            yoy_col: table["yoy_pp"],
            # Formatted text: st.dataframe draws empty cells as "None" whatever the Styler
            # says, and this column is empty for most of the month (CPI comes later).
            real_col: [format_signed(value, lang, RATE_DECIMALS) for value in table["real_pp"]],
        }
    )
    styled = (
        frame.style.format(
            lambda value: format_number(value, lang, RATE_DECIMALS), subset=[last_col]
        )
        .format(
            lambda value: format_signed(value, lang, RATE_DECIMALS),
            subset=[wow_col, yoy_col],
            na_rep=MISSING,  # Styler skips the formatter for missing values
        )
        .format(lambda day: format_date(day, lang), subset=[date_col])
        # A real rate is a level, not a change: it keeps its sign but gets no up/down color.
        .map(delta_style(theme, RATE_DECIMALS), subset=[wow_col, yoy_col])
    )
    st.dataframe(styled, hide_index=True)


# --- charts ---


def chart_card(meta: pd.Series, lang: Lang, color: str) -> None:
    """Title with the series' color dot (identity is never color alone: the name is there)."""
    name = html.escape(str(meta[f"name_{lang}"]))
    st.markdown(
        f'<span style="color:{color}" aria-hidden="true">●</span> **{name}**',
        unsafe_allow_html=True,
    )


def chart_source(meta: pd.Series, lang: Lang) -> None:
    st.caption(text("chart_source", lang).format(source=SOURCE_LABELS[meta["source"]][lang]))


def render_charts(
    observations: pd.DataFrame,
    series: pd.DataFrame,
    selected_ids: list[int],
    lang: Lang,
    theme: str,
    month: str | None = None,
    monthly: bool = False,
) -> None:
    for row_start in range(0, len(selected_ids), CHARTS_PER_ROW):
        row_ids = selected_ids[row_start : row_start + CHARTS_PER_ROW]
        for column, series_id in zip(st.columns(CHARTS_PER_ROW), row_ids, strict=False):
            meta = series.loc[series_id]
            color = series_color(meta["category"], theme)
            factor, unit = shown_unit(meta["unit"], lang, month)
            data = observations[observations["series_id"] == series_id]
            data = data.assign(value=data["value"] * factor)
            with column.container(border=True):
                chart_card(meta, lang, color)
                st.altair_chart(line_chart(data, unit, color, lang, monthly))
                chart_source(meta, lang)


def render_rate_charts(
    observations: pd.DataFrame,
    inflation: pd.DataFrame,
    changes: pd.DataFrame,
    series: pd.DataFrame,
    selected_ids: list[int],
    policy_id: int | None,
    lang: Lang,
    theme: str,
) -> None:
    for row_start in range(0, len(selected_ids), CHARTS_PER_ROW):
        row_ids = selected_ids[row_start : row_start + CHARTS_PER_ROW]
        for column, series_id in zip(st.columns(CHARTS_PER_ROW), row_ids, strict=False):
            meta = series.loc[series_id]
            color = series_color(meta["category"], theme)
            data = observations[observations["series_id"] == series_id]
            step = series_id == policy_id
            with column.container(border=True):
                chart_card(meta, lang, color)
                chart = rate_chart(
                    data, inflation, changes, meta[f"name_{lang}"], lang, theme, color, step
                )
                st.altair_chart(chart)
                chart_source(meta, lang)


def rate_chart(
    data: pd.DataFrame,
    inflation: pd.DataFrame,
    changes: pd.DataFrame,
    name: str,
    lang: Lang,
    theme: str,
    color: str,
    step: bool = False,
) -> alt.LayerChart:
    """A rate with yearly inflation as a reference line and policy-rate changes as rules.

    Rate and inflation are both in %, so they share one y-axis (never a dual axis). The policy
    rate is drawn as steps: it holds its value until the next decision.
    """
    inflation_label = text("inflation_line", lang)
    kinds = [name] + ([inflation_label] if not inflation.empty else [])
    palette = [color, INFLATION_COLORS.get(theme, INFLATION_COLORS["light"])][: len(kinds)]
    legend_color = alt.Color(
        "kind:N",
        scale=alt.Scale(domain=kinds, range=palette),
        legend=alt.Legend(orient="bottom", title=None),
    )
    x = alt.X("date:T", title=None, axis=DATE_AXIS)
    y = alt.Y("value:Q", title="%", scale=alt.Scale(zero=False), axis=alt.Axis(format=",.0f"))

    rate = alt.Chart(data[["date", "value"]].assign(kind=name)).encode(x=x)
    line = rate.mark_line(
        strokeWidth=2,
        strokeCap="round",
        strokeJoin="round",
        interpolate="step-after" if step else "linear",
    )
    layers: list[alt.Chart] = [line.encode(y=y, color=legend_color)]
    if not inflation.empty:
        # CPI is a monthly average dated at the month's end, so the step covers that month.
        reference = alt.Chart(inflation.assign(kind=inflation_label)).mark_line(
            strokeWidth=1.5, strokeDash=[4, 3], interpolate="step-before"
        )
        layers.append(reference.encode(x=x, y=y, color=legend_color))

    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    targets = rate.mark_rule(strokeWidth=12, opacity=0).encode(
        tooltip=[
            alt.Tooltip("date:T", title=text("col_date", lang), format="%d %b %Y"),
            alt.Tooltip("value:Q", title=name, format=",.2f"),
        ]
    )
    layers += [
        targets.add_params(hover),
        rate.mark_rule(color=CROSSHAIR_COLOR, strokeWidth=1).transform_filter(hover),
        rate.mark_circle(color=color, size=64, opacity=1).encode(y=y).transform_filter(hover),
    ]
    if not changes.empty:
        # Short ticks along the bottom (a rug), not full-height lines: 2019-2025 had dozens of
        # decisions close together. Last layer, so hovering a tick shows its own tooltip.
        # y="height" is the plot's bottom edge whatever size Streamlit fits the chart to.
        decisions = alt.Chart(changes).mark_tick(
            color=CROSSHAIR_COLOR, thickness=2, size=14, opacity=0.9, yOffset=-7
        )
        layers.append(
            decisions.encode(
                x=x,
                y=alt.value("height"),
                tooltip=[
                    alt.Tooltip("date:T", title=text("policy_change", lang), format="%d %b %Y"),
                    alt.Tooltip("previous:Q", title=text("tooltip_previous", lang), format=",.2f"),
                    alt.Tooltip("value:Q", title=text("tooltip_new", lang), format=",.2f"),
                ],
            )
        )
    chart = alt.layer(*layers).properties(height=CHART_HEIGHT)
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


def line_chart(
    data: pd.DataFrame, unit: str, color: str, lang: Lang, monthly: bool = False
) -> alt.LayerChart:
    """2px line with a snapping crosshair; each series gets its own y-scale."""
    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(data).encode(x=alt.X("date:T", title=None, axis=DATE_AXIS))
    y = alt.Y("value:Q", title=unit, scale=alt.Scale(zero=False), axis=alt.Axis(format=",.0f"))

    line = base.mark_line(color=color, strokeWidth=2, strokeCap="round", strokeJoin="round")
    # Invisible full-height rules make the whole column the hover target, not the thin line.
    targets = base.mark_rule(strokeWidth=12, opacity=0).encode(
        tooltip=[
            alt.Tooltip(
                "date:T",
                title=text("col_month" if monthly else "tooltip_date", lang),
                format="%b %Y" if monthly else "%d %b %Y",
            ),
            alt.Tooltip("value:Q", title=unit, format=",.1f"),
        ]
    )
    crosshair = base.mark_rule(color=CROSSHAIR_COLOR, strokeWidth=1).transform_filter(hover)
    dot = base.mark_circle(color=color, size=64, opacity=1).encode(y=y).transform_filter(hover)
    chart = alt.layer(line.encode(y=y), targets.add_params(hover), crosshair, dot).properties(
        height=CHART_HEIGHT
    )
    # Turkish: 18.445,2 and month names on axes and tooltips; English uses Vega's default.
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


if __name__ == "__main__":
    main()
