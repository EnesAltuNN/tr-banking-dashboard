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
from pandas.io.formats.style import Styler
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
    BANK_GROUPS,
    MONETARY_UNITS,
    YEAR,
    Ratio,
    alert_series,
    annual_inflation,
    annualized,
    banking_ratios,
    category_id,
    deflate,
    display_unit,
    in_usd,
    period_changes,
    policy_decisions,
    rate_spread,
    real_rates,
    rolling_12m,
    spec_id,
    spec_observations,
    summarize,
    summarize_rates,
    unusual_changes,
    value_near,
    with_categories,
)
from tr_banking.config import load_mpc_calendar, load_series_config
from tr_banking.db import StorageError, Summary, open_repository
from tr_banking.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")
# The data changes twice a week, so one read per hour is plenty. st.cache_data is shared by
# every visitor, so a public page costs Supabase at most one connection per hour, not one per
# visitor or click.
CACHE_TTL = timedelta(hours=1)
SECTIONS = ("credit", "rates", "cards", "banking")

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
    # banking sector tab
    "fx_deposits": "yellow",
    "household_deposits": "magenta",
    "commercial_deposits": "green",
    "npl": "red",
    "npl_commercial": "green",
    "state_banks": "orange",
    "private_banks": "violet",
    "foreign_banks": "aqua",
    "fx_position": "yellow",
    "cbrt_funding": "red",
    "repo_funding": "violet",
    "foreign_bank_funding": "aqua",
    "issued_securities": "magenta",
    "reserve_requirements": "orange",
    "securities": "green",
    "government_bonds": "red",
    "deposit_rate": "yellow",
    "deposit_term": "yellow",
    "usd_try": "green",
    "capital_adequacy": "blue",
    "roe": "violet",
    "roa": "magenta",
    "net_interest_margin": "aqua",
    "npl_coverage": "red",
    "demand_deposits": "orange",
}
# Banking series a click away in the filter bar rather than charted by default: bank groups,
# the single funding items and the bond rows (their totals and ratios are shown).
DETAIL_CATEGORIES = (
    *BANK_GROUPS,
    "fx_position",
    "cbrt_funding",
    "repo_funding",
    "foreign_bank_funding",
    "issued_securities",
    "government_bonds",
)
CROSSHAIR_COLOR = "#898781"
# Yearly inflation is a reference, not a series of its own: a muted grey, dashed line.
INFLATION_COLORS = {"light": "#6f6d66", "dark": "#a3a19a"}
RATE_DECIMALS = 2  # EVDS publishes rates with two decimals, e.g. 41.96
DATE_FILTER_WIDTH = 290  # px: fits two dd.mm.yyyy dates, and a phone screen
SERIES_FILTER_WIDTH = 340  # px: the series names fit, and so does the button's panel on a phone
SOURCE_ICON, MODE_ICON, SERIES_ICON = ":material/database:", ":material/tune:", ":material/list:"
PERIOD_ICON = ":material/calendar_month:"
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
) -> tuple[pd.DataFrame, pd.DataFrame, datetime | None, datetime, Summary | None]:
    """Series, observations, last fetch time, when this snapshot was read, latest summary.

    The connection is opened only on a cache miss and closed right after reading. A pooled
    connection kept open across the app's sleep would go stale, while one short read per hour
    is cheap. Streamlit caches by `storage_key` only; the leading underscore keeps `_settings`
    (which holds secrets) out of the cache key.
    """
    loaded_at = datetime.now(UTC)
    if _settings.database_url is None and not Path(_settings.db_path).exists():
        return pd.DataFrame(), pd.DataFrame(), None, loaded_at, None
    with open_repository(_settings) as repo:
        return (
            repo.list_series(),
            repo.get_observations(),
            repo.last_fetched_at(),
            loaded_at,
            repo.latest_summary(),
        )


def storage_key(settings: Settings) -> str:
    return "postgres" if settings.database_url is not None else str(settings.db_path)


def main() -> None:
    st.set_page_config(page_title="TR Banking Dashboard", layout="wide")
    theme = st.context.theme.type or "light"
    lang = render_header()

    settings = get_settings()
    try:
        series, observations, fetched_at, loaded_at, summary = load_data(
            storage_key(settings), settings
        )
    except (StorageError, psycopg.Error):
        # Details (host, role) go to the server log only; visitors see a plain message.
        logger.exception("dashboard could not read the database")
        st.error(text("db_unavailable", lang))
        st.stop()
    if observations.empty:
        st.info(text("no_data", lang))
        st.stop()
    render_info_line(fetched_at, loaded_at, lang)
    render_weekly_summary(summary, lang)

    config = load_series_config(settings.series_config_path)
    meetings = load_mpc_calendar(settings.mpc_calendar_path).meetings
    series = with_categories(series, config)
    render_alerts(series, observations, lang)
    price_index = spec_observations(series, observations, config.deflator)
    policy_id = spec_id(series, config.policy_rate)

    section, tab = render_tabs(lang)
    with tab:
        module = series[series["module"] == section]
        if section == "credit":
            render_credit_view(module, observations, price_index, lang, theme)
        elif section == "rates":
            render_rates_view(module, observations, price_index, policy_id, meetings, lang, theme)
        elif section == "cards":
            render_cards_view(module, observations, price_index, lang, theme)
        else:
            render_banking_view(series, observations, price_index, lang, theme)
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


def render_weekly_summary(summary: Summary | None, lang: Lang) -> None:
    """The weekly AI-written text, clearly labelled as such, above everything else."""
    if summary is None:
        return
    week = format_date(summary.data_date, lang, long=True)
    with st.container(border=True):
        st.markdown(f"**{text('summary_title', lang)}**")
        st.markdown(summary.text_tr if lang == "tr" else summary.text_en)
        st.caption(text("summary_caption", lang).format(week=week, model=summary.model))


def render_info_line(fetched_at: datetime | None, loaded_at: datetime, lang: Lang) -> None:
    """When the fetch job last wrote data, and when this (cached) page read it."""
    updated = "-"
    if fetched_at:
        local = fetched_at.astimezone(DISPLAY_TZ)
        updated = f"{format_date(local, lang, long=True)} {local:%H:%M}"
    loaded = loaded_at.astimezone(DISPLAY_TZ).strftime("%H:%M")
    st.caption(text("info_line", lang).format(updated=updated, loaded=loaded))


def render_alerts(series: pd.DataFrame, observations: pd.DataFrame, lang: Lang) -> None:
    """Weekly series whose latest change is far outside their own past year (all tabs)."""
    weekly = alert_series(series)
    rows = observations[observations["series_id"].isin(weekly["id"])]
    points = weekly.loc[weekly["unit"] == "%", "id"]
    alerts = unusual_changes(rows, points_ids=points)
    if alerts.empty:
        st.caption(text("alerts_none", lang), help=text("alerts_method", lang))
        return
    meta = weekly.set_index("id")
    lines = []
    for alert in alerts.itertuples():
        in_points = alert.series_id in set(points)

        def shown(value: float, in_points: bool = in_points) -> str:
            if in_points:
                return f"{format_signed(value, lang, RATE_DECIMALS)} {text('pp', lang)}"
            return format_pct(value, lang)

        typical = f"{shown(alert.typical)} ± {shown(alert.spread).lstrip('+')}"
        lines.append(
            text("alerts_line", lang).format(
                name=meta.loc[alert.series_id, f"name_{lang}"],
                source=SOURCE_LABELS[meta.loc[alert.series_id, "source"]][lang],
                week=format_date(alert.date, lang, long=True),
                change=shown(alert.change),
                typical=typical,
            )
        )
    body = "\n".join(f"- {line}" for line in lines)
    # A status color never carries meaning alone: the icon and the words say it too.
    st.warning(f"{text('alerts_title', lang)}\n\n{body}", icon="⚠️")
    st.caption(text("alerts_method", lang))


def render_tabs(lang: Lang) -> tuple[str, DeltaGenerator]:
    """The open section and its tab. Only that tab's code runs, and only its own filters are
    drawn. The open section is remembered by key rather than label, so switching the
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
    bar = filter_bar()
    source = render_source_picker(bar, series, lang)
    real = render_value_mode(bar, lang) == "real"
    series = series[series["source"] == source]
    observations = observations[observations["series_id"].isin(series["id"])]
    render_kpis(credit_tiles(series, observations, price_index, lang), theme)

    filters = render_filters(bar, series, observations, lang, key=f"credit_{source}")
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
    meetings: list[date],
    lang: Lang,
    theme: str,
) -> None:
    """Loan rates and the policy rate in % and pp: no real conversion, no billion scaling."""
    observations = observations[observations["series_id"].isin(series["id"])]
    if observations.empty:
        st.info(text("rates_unavailable", lang))
        return
    inflation = annual_inflation(price_index) if not price_index.empty else price_index
    bar = filter_bar()
    render_kpis(rate_tiles(series, observations, inflation, policy_id, lang), theme)

    # The maturities have their own chart below; as time series they are a click away.
    main_ids = [int(i) for i in series.loc[series["category"] != "deposit_term", "id"]]
    filters = render_filters(bar, series, observations, lang, key="rates", default_ids=main_ids)
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]

    # A real rate is for loans: the policy rate and the funding cost are policy rates.
    loan_ids = [int(row.id) for row in series.itertuples() if row.category != "policy"]
    summary = summarize_rates(selected, inflation, as_of=end, real_rate_ids=loan_ids)
    render_rates_table(summary, series, lang, theme)

    policy = observations[observations["series_id"] == policy_id]
    changes = policy_decisions(policy[["date", "value"]], meetings)

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
    spread_column, terms_column = st.columns(CHARTS_PER_ROW)
    render_spread_chart(spread_column, series, observations, in_range, lang, theme)
    render_deposit_terms(terms_column, series, observations, end, lang, theme)
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
    bar = filter_bar()
    real = render_value_mode(bar, lang, key="cards_value_mode") == "real"
    period = translated_radio(
        bar,
        "cards_period",
        text("period_view", lang),
        ["monthly", "rolling12"],
        lambda option: text(f"period_{option}", lang),
        lang,
        PERIOD_ICON,
    )
    render_kpis(card_tiles(series, observations, lang), theme)

    filters = render_filters(bar, series, observations, lang, key="cards")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]
    latest = format_month(selected["date"].max(), lang)
    st.caption(text("latest_month", lang).format(month=latest))

    month = None
    if real:
        selected, month = deflate_money(selected, series, price_index, lang)
    if period == "rolling12":
        # After deflating: each month's real flow, then the 12-month total of those.
        flow_ids = series.loc[series["unit"].isin(MONETARY_UNITS), "id"]
        selected = rolling_12m(selected, flow_ids)
        st.caption(text("rolling_note", lang))
    # Monthly flows are deflated by their own month's CPI, so monthly % stays meaningful.
    summary = summarize(selected, as_of=end, frequency="monthly")
    render_summary_table(summary, series, lang, theme, month, monthly=True)

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme, month, monthly=True)
    with st.expander(text("notes", lang)):
        if month is not None:
            st.caption(text("cards_real_note", lang).format(month=month))
        st.caption(text("cards_note", lang))


def render_banking_view(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    price_index: pd.DataFrame,
    lang: Lang,
    theme: str,
) -> None:
    """Deposits, non-performing loans and bank groups (BDDK), with ratios computed on the fly.

    Gets every series, not only module "banking": the ratios need BDDK's loan totals too.
    """
    in_module = series[series["module"] == "banking"]
    # Weekly BDDK series drive the tiles, filters and tables; the monthly bulletin's ratios
    # have a section of their own.
    banking = in_module[in_module["frequency"] == "weekly"]
    monthly = in_module[in_module["frequency"] == "monthly"]
    observations_of_tab = observations[observations["series_id"].isin(banking["id"])]
    if observations_of_tab.empty:
        st.info(text("banking_unavailable", lang))
        return
    bar = filter_bar()
    real = render_value_mode(bar, lang, key="banking_value_mode") == "real"
    ratios = banking_ratios(series, observations)
    render_kpis(banking_tiles(banking, observations, ratios, lang), theme)

    st.markdown(f"**{text('ratios', lang)}**")
    render_ratio_table(ratios, lang, theme)
    headline = [ratio for ratio in ratios if ratio.key in HEADLINE_RATIOS]
    render_ratio_charts(headline, lang, theme)
    render_group_shares(ratios, lang, theme)
    render_fx_in_usd(series, observations, lang, theme)
    render_monthly_ratios(monthly, observations, lang, theme)

    st.markdown(f"**{text('amounts', lang)}**")
    sector_ids = [int(i) for i in banking.loc[~banking["category"].isin(DETAIL_CATEGORIES), "id"]]
    filters = render_filters(bar, banking, observations_of_tab, lang, "banking", sector_ids)
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations_of_tab[observations_of_tab["series_id"].isin(selected_ids)]
    week = format_date(selected["date"].max(), lang, long=True)
    st.caption(text("latest_week", lang).format(week=week))

    month = None
    if real:
        selected, month = deflate_money(selected, banking, price_index, lang)
    summary = summarize(selected, as_of=end)
    render_summary_table(summary, banking, lang, theme, month, show_prev=month is None)
    st.markdown(f"**{text('period_changes', lang)}**")
    render_period_table(period_changes(selected, as_of=end), banking, lang, theme)
    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, banking.set_index("id"), selected_ids, lang, theme, month)
    with st.expander(text("notes", lang)):
        st.caption(text("banking_note", lang))


HEADLINE_RATIOS = (
    "ratio_npl",
    "ratio_fx_share",
    "ratio_loan_deposit",
    "ratio_bonds_securities",
    "ratio_equity_loans",
    "ratio_wholesale_funding",
)
# Topic of each ratio in the ratio table, by key prefix (the first match wins).
RATIO_TOPICS = (
    ("ratio_npl", "topic_asset_quality"),
    ("ratio_fx_share", "topic_deposits"),
    ("ratio_loan_deposit", "topic_deposits"),
    ("ratio_equity", "topic_capital"),
    ("ratio_fx_position", "topic_capital"),
    ("ratio_wholesale", "topic_funding"),
    ("ratio_reserves", "topic_funding"),
    ("ratio_bonds", "topic_securities"),
    ("ratio_loans_", "topic_groups"),
    ("ratio_deposits_", "topic_groups"),
)


def ratio_topic(key: str) -> str:
    return next(topic for prefix, topic in RATIO_TOPICS if key.startswith(prefix))


def render_ratio_table(ratios: list[Ratio], lang: Lang, theme: str) -> None:
    if not ratios:
        return
    frames = [ratio.values.assign(series_id=index) for index, ratio in enumerate(ratios)]
    summary = summarize_rates(pd.concat(frames, ignore_index=True), pd.DataFrame())
    ratio_col, date_col = text("col_ratio", lang), text("col_date", lang)
    last_col = f"{text('col_last', lang)} (%)"
    wow_col, yoy_col = text("col_wow_pp", lang), text("col_yoy_pp", lang)
    frame = pd.DataFrame(
        {
            text("col_topic", lang): [
                text(ratio_topic(ratios[i].key), lang) for i in summary["series_id"]
            ],
            ratio_col: [text(ratios[i].key, lang) for i in summary["series_id"]],
            date_col: summary["last_date"].dt.date,
            last_col: summary["last_value"],
            wow_col: summary["wow_pp"],
            yoy_col: summary["yoy_pp"],
        }
    )
    styled = (
        frame.style.format(
            lambda value: format_number(value, lang, RATE_DECIMALS), subset=[last_col]
        )
        .format(
            lambda value: format_signed(value, lang, RATE_DECIMALS),
            subset=[wow_col, yoy_col],
            na_rep=MISSING,
        )
        .format(lambda day: format_date(day, lang), subset=[date_col])
        .map(delta_style(theme, RATE_DECIMALS), subset=[wow_col, yoy_col])
    )
    show_table(styled, ratio_col)


def render_spread_chart(
    column: DeltaGenerator,
    series: pd.DataFrame,
    observations: pd.DataFrame,
    in_range: Callable[[pd.DataFrame], pd.DataFrame],
    lang: Lang,
    theme: str,
) -> None:
    """Commercial loan rate minus the TRY deposit rate, in pp (a rough lending margin)."""
    loan_id, deposit_id = category_id(series, "commercial"), category_id(series, "deposit_rate")
    if loan_id is None or deposit_id is None:
        return
    spread = in_range(
        rate_spread(
            observations[observations["series_id"] == loan_id],
            observations[observations["series_id"] == deposit_id],
        )
    )
    if spread.empty:
        return
    color = series_color("deposit_rate", theme)
    with column.container(border=True):
        chart_title(text("rate_spread", lang), color)
        st.altair_chart(line_chart(spread, text("pp", lang), color, lang, axis_format=",.0f"))
        st.caption(text("chart_source", lang).format(source=SOURCE_LABELS["evds"][lang]))


def render_deposit_terms(
    column: DeltaGenerator,
    series: pd.DataFrame,
    observations: pd.DataFrame,
    as_of: date,
    lang: Lang,
    theme: str,
) -> None:
    """TRY deposit rate per maturity: the latest week as bars, 52 weeks earlier as ticks."""
    terms = series[series["category"] == "deposit_term"]
    rows = observations[observations["series_id"].isin(terms["id"])]
    if rows.empty:
        return
    summary = summarize_rates(rows, pd.DataFrame(), as_of=as_of).merge(
        terms, left_on="series_id", right_on="id"
    )
    # "TL mevduat faizi: 1 aya kadar" -> "1 aya kadar": the title already names the rate.
    labels = summary[f"name_{lang}"].str.split(": ").str[-1]
    order = [name.split(": ")[-1] for name in terms[f"name_{lang}"]]
    now_label, year_label = text("term_now", lang), text("term_year_ago", lang)
    data = pd.concat(
        [
            pd.DataFrame({"term": labels, "kind": now_label, "value": summary["last_value"]}),
            pd.DataFrame(
                {
                    "term": labels,
                    "kind": year_label,
                    "value": summary["last_value"] - summary["yoy_pp"],
                }
            ),
        ],
        ignore_index=True,
    ).dropna()
    color = series_color("deposit_term", theme)
    y = alt.Y("term:N", title=None, sort=order)
    x = alt.X("value:Q", title="%", axis=alt.Axis(format=",.0f"))
    legend = alt.Color(
        "kind:N",
        scale=alt.Scale(domain=[now_label, year_label], range=[color, CROSSHAIR_COLOR]),
        legend=alt.Legend(orient="bottom", title=None),
    )
    tooltip = [
        alt.Tooltip("term:N", title=text("col_term", lang)),
        alt.Tooltip("kind:N", title=text("col_date", lang)),
        alt.Tooltip("value:Q", title="%", format=",.2f"),
    ]
    base = alt.Chart(data).encode(y=y, x=x, color=legend, tooltip=tooltip)
    chart = alt.layer(
        base.transform_filter(alt.datum.kind == now_label).mark_bar(height=14),
        base.transform_filter(alt.datum.kind == year_label).mark_tick(thickness=3, size=22),
    ).properties(height=CHART_HEIGHT)
    if lang == "tr":
        chart = chart.configure(locale=VEGA_LOCALE_TR)
    week = format_date(summary["last_date"].max(), lang, long=True)
    with column.container(border=True):
        chart_title(text("deposit_terms", lang), color)
        st.altair_chart(chart)
        st.caption(
            f"{text('week_of', lang).format(week=week)} · "
            + text("chart_source", lang).format(source=SOURCE_LABELS["evds"][lang])
        )


def render_fx_in_usd(
    series: pd.DataFrame, observations: pd.DataFrame, lang: Lang, theme: str
) -> None:
    """FX deposits at the USD/TRY rate of their week, next to the rate itself.

    Separates real dollarization from the TRY value of FX deposits rising with the rate.
    """
    usd_id, fx_id = category_id(series, "usd_try"), category_id(series, "fx_deposits")
    if usd_id is None or fx_id is None:
        return
    usd_rows = observations[observations["series_id"] == usd_id]
    fx_usd = in_usd(observations[observations["series_id"] == fx_id], usd_rows)
    if fx_usd.empty:
        return
    charts = (
        (
            "fx_deposits_usd",
            fx_usd.assign(value=fx_usd["value"] * 1e-3),
            "billion_usd",
            "fx_deposits",
            ",.0f",
        ),
        ("usd_try_rate", usd_rows, "try_per_usd", "usd_try", ",.1f"),
    )
    for column, (title, data, unit, category, axis_format) in zip(
        st.columns(CHARTS_PER_ROW), charts, strict=False
    ):
        color = series_color(category, theme)
        with column.container(border=True):
            chart_title(text(title, lang), color)
            st.altair_chart(
                line_chart(data, text(unit, lang), color, lang, axis_format=axis_format)
            )
            source = SOURCE_LABELS["evds"][lang]
            if title == "fx_deposits_usd":
                source = f"{SOURCE_LABELS['bddk'][lang]}, {source}"
            st.caption(text("chart_source", lang).format(source=source))


def render_monthly_ratios(
    monthly: pd.DataFrame, observations: pd.DataFrame, lang: Lang, theme: str
) -> None:
    """BDDK's own monthly sector ratios: a table and one chart each, profitability annualized."""
    shown = []
    for spec in monthly.itertuples():
        values = observations.loc[observations["series_id"] == spec.id, ["date", "value"]]
        if values.empty:
            continue
        shown.append((spec, annualized(values) if spec.year_to_date else values))
    if not shown:
        return
    st.markdown(f"**{text('monthly_ratios', lang)}**")
    frames = [values.assign(series_id=index) for index, (_, values) in enumerate(shown)]
    summary = summarize_rates(pd.concat(frames, ignore_index=True), pd.DataFrame())
    ratio_col, month_col = text("col_ratio", lang), text("col_month", lang)
    last_col, yoy_col = f"{text('col_last', lang)} (%)", text("col_yoy_pp", lang)
    frame = pd.DataFrame(
        {
            ratio_col: [getattr(shown[i][0], f"name_{lang}") for i in summary["series_id"]],
            month_col: summary["last_date"].dt.date,
            last_col: summary["last_value"],
            yoy_col: summary["yoy_pp"],
        }
    )
    styled = (
        frame.style.format(
            lambda value: format_number(value, lang, RATE_DECIMALS), subset=[last_col]
        )
        .format(
            lambda value: format_signed(value, lang, RATE_DECIMALS),
            subset=[yoy_col],
            na_rep=MISSING,
        )
        .format(lambda day: format_month(day, lang), subset=[month_col])
        .map(delta_style(theme, RATE_DECIMALS), subset=[yoy_col])
    )
    show_table(styled, ratio_col)
    source = text("chart_source", lang).format(source=SOURCE_LABELS["bddk_monthly"][lang])
    for row_start in range(0, len(shown), CHARTS_PER_ROW):
        row = shown[row_start : row_start + CHARTS_PER_ROW]
        for column, (spec, values) in zip(st.columns(CHARTS_PER_ROW), row, strict=False):
            color = series_color(spec.category, theme)
            with column.container(border=True):
                chart_title(getattr(spec, f"name_{lang}"), color)
                chart = line_chart(values, "%", color, lang, monthly=True, axis_format=",.1f")
                st.altair_chart(chart)
                st.caption(source)
    st.caption(text("monthly_ratios_note", lang))


def render_group_shares(ratios: list[Ratio], lang: Lang, theme: str) -> None:
    """Stacked areas of the bank groups' shares of loans and of deposits (they add to 100%)."""
    by_key = {ratio.key: ratio for ratio in ratios}
    source = text("chart_source", lang).format(source=SOURCE_LABELS["bddk"][lang])
    columns = st.columns(CHARTS_PER_ROW)
    for column, kind in zip(columns, ("loans", "deposits"), strict=False):
        parts = [
            by_key[key].values.assign(group=text(f"group_{bank_group}", lang))
            for bank_group in BANK_GROUPS
            if (key := f"ratio_{kind}_{bank_group}") in by_key
        ]
        if not parts:
            continue
        with column.container(border=True):
            chart_title(text(f"group_shares_{kind}", lang), series_color(BANK_GROUPS[0], theme))
            st.altair_chart(share_chart(pd.concat(parts, ignore_index=True), lang, theme))
            st.caption(source)


def share_chart(data: pd.DataFrame, lang: Lang, theme: str) -> alt.Chart:
    """Shares that add up to 100%, stacked in the order of BANK_GROUPS."""
    labels = [text(f"group_{bank_group}", lang) for bank_group in BANK_GROUPS]
    colors = [series_color(bank_group, theme) for bank_group in BANK_GROUPS]
    data = data.assign(order=data["group"].map(labels.index))
    chart = (
        alt.Chart(data)
        .mark_area(opacity=0.85)
        .encode(
            x=alt.X("date:T", title=None, axis=DATE_AXIS),
            y=alt.Y(
                "value:Q",
                title="%",
                stack="zero",
                scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(format=",.0f"),
            ),
            color=alt.Color(
                "group:N",
                scale=alt.Scale(domain=labels, range=colors),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            order=alt.Order("order:Q"),
            tooltip=[
                alt.Tooltip("date:T", title=text("col_date", lang), format="%d %b %Y"),
                alt.Tooltip("group:N", title=text("col_group", lang)),
                alt.Tooltip("value:Q", title=text("col_share", lang), format=",.1f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


def render_period_table(
    changes: pd.DataFrame, series: pd.DataFrame, lang: Lang, theme: str
) -> None:
    """% change of each selected series over 1, 4, 13 and 52 weeks and since New Year."""
    table = changes.merge(series, left_on="series_id", right_on="id")
    series_col = text("col_series", lang)
    names = {"w1": "col_1w", "w4": "col_4w", "w13": "col_13w", "ytd": "col_ytd", "w52": "col_52w"}
    frame = pd.DataFrame(
        {series_col: table[f"name_{lang}"]}
        | {text(label, lang): table[column] for column, label in names.items()}
    )
    pct_cols = [text(label, lang) for label in names.values()]
    styled = frame.style.format(
        lambda value: format_pct(value, lang), subset=pct_cols, na_rep=MISSING
    ).map(delta_style(theme), subset=pct_cols)
    show_table(styled, series_col)


def render_ratio_charts(ratios: list[Ratio], lang: Lang, theme: str) -> None:
    source = text("chart_source", lang).format(source=SOURCE_LABELS["bddk"][lang])
    for row_start in range(0, len(ratios), CHARTS_PER_ROW):
        row = ratios[row_start : row_start + CHARTS_PER_ROW]
        for column, ratio in zip(st.columns(CHARTS_PER_ROW), row, strict=False):
            color = series_color(ratio.category, theme)
            with column.container(border=True):
                chart_title(text(ratio.key, lang), color)
                st.altair_chart(line_chart(ratio.values, "%", color, lang, axis_format=",.1f"))
                st.caption(source)


# --- KPI tiles ---


def banking_tiles(
    banking: pd.DataFrame, observations: pd.DataFrame, ratios: list[Ratio], lang: Lang
) -> list[Tile]:
    """Total deposits (level, yearly %), FX share, NPL ratio and loan-to-deposit ratio."""
    tiles = []
    deposits_id = category_id(banking, "deposits")
    if deposits_id is not None:
        rows = observations[observations["series_id"] == deposits_id]
        factor, unit = kpi_unit("million TRY", lang)
        last = summarize(rows).iloc[0]
        tiles.append(
            Tile(
                label=banking.set_index("id").loc[deposits_id, f"name_{lang}"],
                value=f"{format_number(last.last_value * factor, lang)} {unit}",
                change=last.yoy_pct,
                change_text=format_pct(last.yoy_pct, lang),
                description=text("desc_yearly", lang),
                details=text("week_of", lang).format(
                    week=format_date(last.last_date, lang, long=True)
                ),
                sparkline=sparkline(rows, "weekly", factor),
            )
        )
    by_key = {ratio.key: ratio for ratio in ratios}
    pp = text("pp", lang)
    for key in ("ratio_fx_share", "ratio_npl", "ratio_loan_deposit"):
        if key not in by_key:
            continue
        values = by_key[key].values
        last = summarize_rates(values.assign(series_id=0), pd.DataFrame()).iloc[0]
        tiles.append(
            Tile(
                label=text(key, lang),
                value=f"{format_number(last.last_value, lang, RATE_DECIMALS)}%",
                change=last.yoy_pp,
                change_text=f"{format_signed(last.yoy_pp, lang, RATE_DECIMALS)} {pp}",
                description=text("desc_yearly", lang),
                details=text("week_of", lang).format(
                    week=format_date(last.last_date, lang, long=True)
                ),
                sparkline=sparkline(values, "weekly"),
                decimals=RATE_DECIMALS,
            )
        )
    return tiles


# --- KPI tiles (other sections) ---


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
        factor, unit = kpi_unit(meta.loc[series_id, "unit"], lang)
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
    factor, unit = kpi_unit("million TRY", lang)
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


# --- filters ---


def filter_bar() -> DeltaGenerator:
    """One row of buttons above the tab's content, one button per filter.

    The pickers are called at different points of a view (the source picker before the KPI
    tiles, the series picker after them), so they draw into this container instead of where
    they are called. Each button carries its own choice, so the row says what is on screen
    without being opened, and it stays one short row on a phone instead of a wide box of
    controls. What the button says comes from the widget's own state, which Streamlit has
    already updated when the click reruns the script.
    """
    return st.container(horizontal=True, gap="small")


def translated_start(name: str, lang: Lang, fallback: object) -> object:
    """Where a widget with translated option labels starts in this language.

    Streamlit keeps a radio's or multiselect's choice as its label. After a language switch the
    old label matches no option, and the browser shows nothing selected (or the old language's
    chips). So each such widget gets one key per language (`<name>_<lang>`) and starts from the
    value the other language's widget had. The start only changes with the language: like the
    tabs' default, it is part of the widget's identity.
    """
    if st.session_state.get(f"{name}__lang") != lang:
        st.session_state[f"{name}__lang"] = lang
        st.session_state[f"{name}__start"] = st.session_state.get(f"{name}__value", fallback)
    return st.session_state[f"{name}__start"]


def translated_radio(
    bar: DeltaGenerator,
    name: str,
    label: str,
    options: list[str],
    format_func: Callable[[str], str],
    lang: Lang,
    icon: str,
    button_func: Callable[[str], str] | None = None,
) -> str:
    """A radio inside its own button. `button_func` shortens the choice for the button."""
    start = translated_start(name, lang, options[0])
    shown = st.session_state.get(f"{name}_{lang}", start)
    shown = shown if shown in options else options[0]
    with bar.popover(f"{label}: {(button_func or format_func)(shown)}", icon=icon):
        value = st.radio(
            label,
            options=options,
            index=options.index(start) if start in options else 0,
            format_func=format_func,
            key=f"{name}_{lang}",
            label_visibility="collapsed",
        )
    st.session_state[f"{name}__value"] = value
    return value


def render_source_picker(bar: DeltaGenerator, series: pd.DataFrame, lang: Lang) -> str:
    """One source at a time: each has its own history length, units and definitions."""
    available = [source for source in SOURCE_LABELS if source in set(series["source"])]
    format_source = lambda source: SOURCE_LABELS[source][lang]  # noqa: E731
    return translated_radio(
        bar, "source", text("source", lang), available, format_source, lang, SOURCE_ICON
    )


def render_value_mode(bar: DeltaGenerator, lang: Lang, key: str = "value_mode") -> str:
    format_mode = lambda mode: text(f"mode_{mode}", lang)  # noqa: E731
    # "Real (inflation-adjusted)" explains itself in the list but is too long for the button.
    short_mode = lambda mode: text(f"mode_{mode}_short", lang)  # noqa: E731
    modes = ["nominal", "real"]
    return translated_radio(
        bar, key, text("value_mode", lang), modes, format_mode, lang, MODE_ICON, short_mode
    )


def render_filters(
    bar: DeltaGenerator,
    series: pd.DataFrame,
    observations: pd.DataFrame,
    lang: Lang,
    key: str,
    default_ids: list[int] | None = None,
) -> tuple[list[int], date, date] | None:
    """Series and date range pickers in the filter bar; None (after a hint) while the choice
    is incomplete. All series are selected by default unless `default_ids` says otherwise."""
    labels = {int(row.id): getattr(row, f"name_{lang}") for row in series.itertuples()}
    first, last = observations["date"].min().date(), observations["date"].max().date()

    name = f"{key}_series"
    start = translated_start(name, lang, list(labels) if default_ids is None else default_ids)
    chosen = st.session_state.get(f"{name}_{lang}", start)
    count = sum(1 for series_id in chosen if series_id in labels)
    # The button says what is on screen: all of them, or how many of how many.
    picked_label = text("series_all", lang) if count == len(labels) else f"{count}/{len(labels)}"
    with bar.popover(f"{text('series', lang)}: {picked_label}", icon=SERIES_ICON):
        selected_ids = st.multiselect(
            text("series", lang),
            options=list(labels),
            default=[series_id for series_id in start if series_id in labels],
            format_func=labels.__getitem__,
            key=f"{name}_{lang}",
            label_visibility="collapsed",
            width=SERIES_FILTER_WIDTH,
        )
    st.session_state[f"{name}__value"] = selected_ids
    # Not in a popover: the field already shows the range and opens the calendar on a click.
    picked = bar.date_input(
        text("date_range", lang),
        value=(first, last),
        min_value=first,
        max_value=last,
        format="DD.MM.YYYY" if lang == "tr" else "YYYY-MM-DD",
        key=f"{key}_range",
        label_visibility="collapsed",
        # A date range needs both dates side by side; "content" is not a legal width here.
        width=DATE_FILTER_WIDTH,
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


def kpi_unit(stored_unit: str, lang: Lang) -> tuple[float, str]:
    """Display multiplier and the short unit label used in KPI tiles."""
    factor, unit = display_unit(stored_unit)
    return factor, unit_label(unit, lang, short=True)


def shown_unit(stored_unit: str, lang: Lang, month: str | None) -> tuple[float, str]:
    """Display multiplier and label; real TRY values get "..., Aug 2026 prices"."""
    factor, unit = display_unit(stored_unit)
    label = unit_label(unit, lang)
    if month is not None and stored_unit in MONETARY_UNITS:
        label = text("real_unit", lang).format(unit=label, month=month)
    return factor, label


# --- tables ---


def show_table(styled: Styler, first_col: str) -> None:
    """Every table looks the same: no inner scroll (they are short) and a pinned first
    column, so the name stays in view when a wide table scrolls sideways on a phone."""
    st.dataframe(
        styled,
        hide_index=True,
        height="content",
        column_config={first_col: st.column_config.Column(pinned=True)},
    )


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
    show_table(styled, series_col)


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
    show_table(styled, series_col)


# --- charts ---


def chart_card(meta: pd.Series, lang: Lang, color: str) -> None:
    chart_title(str(meta[f"name_{lang}"]), color)


def chart_title(name: str, color: str) -> None:
    """Title with the series' color dot (identity is never color alone: the name is there)."""
    st.markdown(
        f'<span style="color:{color}" aria-hidden="true">●</span> **{html.escape(name)}**',
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
    """A rate with yearly inflation as a reference line and MPC decisions as ticks.

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
        # decisions close together. Holds are shorter and fainter than hikes and cuts (size
        # and opacity, not color alone). Last layers, so hovering a tick shows its tooltip.
        # y="height" is the plot's bottom edge whatever size Streamlit fits the chart to.
        labeled = changes.assign(
            label=[text(f"decision_{decision}", lang) for decision in changes["decision"]]
        )
        tooltip = [
            alt.Tooltip("date:T", title=text("policy_change", lang), format="%d %b %Y"),
            alt.Tooltip("label:N", title=text("tooltip_decision", lang)),
            alt.Tooltip("previous:Q", title=text("tooltip_previous", lang), format=",.2f"),
            alt.Tooltip("value:Q", title=text("tooltip_new", lang), format=",.2f"),
        ]
        for moved, size, opacity in ((False, 7, 0.45), (True, 14, 0.9)):
            ticks = labeled[(labeled["decision"] != "hold") == moved]
            if not ticks.empty:
                mark = alt.Chart(ticks).mark_tick(
                    color=CROSSHAIR_COLOR,
                    thickness=2,
                    size=size,
                    opacity=opacity,
                    yOffset=-size / 2,
                )
                layers.append(mark.encode(x=x, y=alt.value("height"), tooltip=tooltip))
    chart = alt.layer(*layers).properties(height=CHART_HEIGHT)
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


def line_chart(
    data: pd.DataFrame,
    unit: str,
    color: str,
    lang: Lang,
    monthly: bool = False,
    axis_format: str = ",.0f",
) -> alt.LayerChart:
    """2px line with a snapping crosshair; each series gets its own y-scale."""
    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(data).encode(x=alt.X("date:T", title=None, axis=DATE_AXIS))
    y = alt.Y("value:Q", title=unit, scale=alt.Scale(zero=False), axis=alt.Axis(format=axis_format))

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
