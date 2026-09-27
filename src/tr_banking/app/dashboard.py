"""Streamlit dashboard. Run with: uv run streamlit run src/tr_banking/app/dashboard.py"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from tr_banking.app.i18n import (
    LANGUAGES,
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
    annual_inflation,
    deflate,
    display_unit,
    rate_changes,
    summarize,
    summarize_rates,
)
from tr_banking.config import SeriesSpec, load_series_config
from tr_banking.db import StorageError, open_repository
from tr_banking.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")
# The data changes twice a week, so one read per hour is plenty. st.cache_data is shared by
# every visitor, so a public page costs Supabase at most one connection per hour, not one per
# visitor or click.
CACHE_TTL = timedelta(hours=1)
# One series per chart, so one color; steps validated for contrast on each theme's surface.
LINE_COLORS = {"light": "#2a78d6", "dark": "#3987e5"}
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
CHARTS_PER_ROW = 2


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

    config = load_series_config(settings.series_config_path)
    price_index = spec_observations(series, observations, config.deflator)
    policy_id = spec_id(series, config.policy_rate)

    tab_keys = ["tab_credit", "tab_rates", "tab_cards"]
    credit_tab, rates_tab, cards_tab = st.tabs([text(key, lang) for key in tab_keys])
    with credit_tab:
        credit = series[series["module"] == "credit"]
        render_credit_view(credit, observations, price_index, fetched_at, loaded_at, lang, theme)
    with rates_tab:
        rates = series[series["module"] == "rates"]
        render_rates_view(rates, observations, price_index, policy_id, lang, theme)
    with cards_tab:
        cards = series[series["module"] == "cards"]
        render_cards_view(cards, observations, price_index, lang, theme)


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


def render_credit_view(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    price_index: pd.DataFrame,
    fetched_at: datetime | None,
    loaded_at: datetime,
    lang: Lang,
    theme: str,
) -> None:
    source_column, mode_column = st.columns([2, 1])
    with source_column:
        source = render_source_picker(series, lang)
    with mode_column:
        real = render_value_mode(lang) == "real"
    series = series[series["source"] == source]
    observations = observations[observations["series_id"].isin(series["id"])]
    filters = render_filters(series, observations, lang, key=f"credit_{source}")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]
    render_status(selected, fetched_at, loaded_at, lang)

    month = None
    if real:
        selected, month = deflate_money(selected, series, price_index, lang)

    # The table uses full history up to `end`, so yearly % works even for a short date range.
    summary = summarize(selected, as_of=end)
    # Real values move in monthly CPI steps, so a weekly % would mislead.
    render_summary_table(summary, series, lang, theme, month, show_prev=month is None)
    if month is None:
        st.caption(text("inflation_note", lang))
    else:
        week = format_date(summary["last_date"].max(), lang, long=True)
        st.caption(text("real_note", lang).format(month=month, week=week))

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme, month)
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
    filters = render_filters(series, observations, lang, key="rates")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]

    inflation = annual_inflation(price_index) if not price_index.empty else price_index
    loan_ids = [int(series_id) for series_id in series["id"] if series_id != policy_id]
    summary = summarize_rates(selected, inflation, as_of=end, real_rate_ids=loan_ids)
    render_rates_table(summary, series, lang, theme)
    st.caption(text("rates_note", lang))

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
    filters = render_filters(series, observations, lang, key="cards")
    if filters is None:
        return
    selected_ids, start, end = filters
    selected = observations[observations["series_id"].isin(selected_ids)]
    latest = format_month(selected["date"].max(), lang)
    st.markdown(text("latest_month", lang).format(month=latest))

    month = None
    if real:
        selected, month = deflate_money(selected, series, price_index, lang)
    # Monthly flows are deflated by their own month's CPI, so monthly % stays meaningful.
    summary = summarize(selected, as_of=end, frequency="monthly")
    render_summary_table(summary, series, lang, theme, month, monthly=True)
    if month is not None:
        st.caption(text("cards_real_note", lang).format(month=month))

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme, month, monthly=True)
    st.caption(text("cards_note", lang))


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


def render_value_mode(lang: Lang, key: str = "value_mode") -> str:
    return st.radio(
        text("value_mode", lang),
        options=["nominal", "real"],
        format_func=lambda mode: text(f"mode_{mode}", lang),
        horizontal=True,
        key=key,
    )


def shown_unit(stored_unit: str, lang: Lang, month: str | None) -> tuple[float, str]:
    """Display multiplier and label; real TRY values get "..., Aug 2026 prices"."""
    factor, unit = display_unit(stored_unit)
    label = unit_label(unit, lang)
    if month is not None and stored_unit in MONETARY_UNITS:
        label = text("real_unit", lang).format(unit=label, month=month)
    return factor, label


def render_header() -> Lang:
    """Title on the left, TR/EN switch on the right; the switch decides the title language."""
    title_column, language_column = st.columns([6, 1])
    lang = language_column.radio(
        "Dil / Language",
        options=list(LANGUAGES),
        format_func=LANGUAGES.__getitem__,
        horizontal=True,
        key="lang",
    )
    title_column.title(text("title", lang))
    title_column.caption(text("subtitle", lang))
    return lang


def render_source_picker(series: pd.DataFrame, lang: Lang) -> str:
    """One source at a time: each has its own history length, units and definitions."""
    available = [source for source in SOURCE_LABELS if source in set(series["source"])]
    return st.radio(
        text("source", lang),
        options=available,
        format_func=lambda source: SOURCE_LABELS[source][lang],
        horizontal=True,
        key="source",
    )


def render_filters(
    series: pd.DataFrame, observations: pd.DataFrame, lang: Lang, key: str
) -> tuple[list[int], date, date] | None:
    """Series and date range pickers; None (after a hint) while the choice is incomplete.

    Returning None instead of calling st.stop() keeps the other tabs rendering.
    """
    labels = {int(row.id): getattr(row, f"name_{lang}") for row in series.itertuples()}
    first, last = observations["date"].min().date(), observations["date"].max().date()

    left, right = st.columns([2, 1])
    selected_ids = left.multiselect(
        text("series", lang),
        options=list(labels),
        default=list(labels),
        format_func=labels.__getitem__,
        key=f"{key}_series",
    )
    picked = right.date_input(
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


def render_status(
    observations: pd.DataFrame, fetched_at: datetime | None, loaded_at: datetime, lang: Lang
) -> None:
    """Newest week, when the fetch job last wrote data, and when this page read it.

    Showing the read time as well keeps the cached page honest: "last fetch" is as of that read.
    """
    week = format_date(observations["date"].max(), lang, long=True)
    updated = "-"
    if fetched_at:
        local = fetched_at.astimezone(DISPLAY_TZ)
        updated = f"{format_date(local, lang, long=True)} {local:%H:%M}"
    loaded = loaded_at.astimezone(DISPLAY_TZ).strftime("%H:%M")
    st.markdown(text("status", lang).format(week=week, updated=updated))
    st.caption(text("freshness_note", lang).format(loaded=loaded))


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
        .format(lambda value: format_pct(value, lang), subset=pct_cols)
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
            real_col: table["real_pp"],
        }
    )
    styled = (
        frame.style.format(
            lambda value: format_number(value, lang, RATE_DECIMALS), subset=[last_col]
        )
        .format(
            lambda value: format_signed(value, lang, RATE_DECIMALS),
            subset=[wow_col, yoy_col, real_col],
        )
        .format(lambda day: format_date(day, lang), subset=[date_col])
        # A real rate is a level, not a change: it keeps its sign but gets no up/down color.
        .map(delta_style(theme, RATE_DECIMALS), subset=[wow_col, yoy_col])
    )
    st.dataframe(styled, hide_index=True)


def render_charts(
    observations: pd.DataFrame,
    series: pd.DataFrame,
    selected_ids: list[int],
    lang: Lang,
    theme: str,
    month: str | None = None,
    monthly: bool = False,
) -> None:
    color = LINE_COLORS.get(theme, LINE_COLORS["light"])
    for row_start in range(0, len(selected_ids), CHARTS_PER_ROW):
        row_ids = selected_ids[row_start : row_start + CHARTS_PER_ROW]
        for column, series_id in zip(st.columns(CHARTS_PER_ROW), row_ids, strict=False):
            meta = series.loc[series_id]
            factor, unit = shown_unit(meta["unit"], lang, month)
            data = observations[observations["series_id"] == series_id]
            data = data.assign(value=data["value"] * factor)
            with column:
                st.markdown(f"**{meta[f'name_{lang}']}**")
                st.altair_chart(line_chart(data, unit, color, lang, monthly))


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
            name = series.loc[series_id, f"name_{lang}"]
            data = observations[observations["series_id"] == series_id]
            step = series_id == policy_id
            with column:
                st.markdown(f"**{name}**")
                st.altair_chart(rate_chart(data, inflation, changes, name, lang, theme, step))


def rate_chart(
    data: pd.DataFrame,
    inflation: pd.DataFrame,
    changes: pd.DataFrame,
    name: str,
    lang: Lang,
    theme: str,
    step: bool = False,
) -> alt.LayerChart:
    """A rate with yearly inflation as a reference line and policy-rate changes as rules.

    Rate and inflation are both in %, so they share one y-axis (never a dual axis). The policy
    rate is drawn as steps: it holds its value until the next decision.
    """
    color = LINE_COLORS.get(theme, LINE_COLORS["light"])
    inflation_label = text("inflation_line", lang)
    kinds = [name] + ([inflation_label] if not inflation.empty else [])
    palette = [color, INFLATION_COLORS.get(theme, INFLATION_COLORS["light"])][: len(kinds)]
    legend_color = alt.Color(
        "kind:N",
        scale=alt.Scale(domain=kinds, range=palette),
        legend=alt.Legend(orient="bottom", title=None),
    )
    x = alt.X("date:T", title=None)
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
        # Last layer, so hovering a decision line shows its own tooltip.
        decisions = alt.Chart(changes).mark_rule(
            color=CROSSHAIR_COLOR, strokeWidth=1, strokeDash=[2, 3]
        )
        layers.append(
            decisions.encode(
                x=x,
                tooltip=[
                    alt.Tooltip("date:T", title=text("policy_change", lang), format="%d %b %Y"),
                    alt.Tooltip("previous:Q", title=text("tooltip_previous", lang), format=",.2f"),
                    alt.Tooltip("value:Q", title=text("tooltip_new", lang), format=",.2f"),
                ],
            )
        )
    chart = alt.layer(*layers).properties(height=260)
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


def line_chart(
    data: pd.DataFrame, unit: str, color: str, lang: Lang, monthly: bool = False
) -> alt.LayerChart:
    """2px line with a snapping crosshair; each series gets its own y-scale."""
    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(data).encode(x=alt.X("date:T", title=None))
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
        height=260
    )
    # Turkish: 18.445,2 and month names on axes and tooltips; English uses Vega's default.
    return chart.configure(locale=VEGA_LOCALE_TR) if lang == "tr" else chart


if __name__ == "__main__":
    main()
