"""Streamlit dashboard. Run with: uv run streamlit run src/tr_banking/app/dashboard.py"""

import logging
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
    format_number,
    format_pct,
    text,
    unit_label,
)
from tr_banking.app.metrics import display_unit, summarize
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

    source = render_source_picker(series, lang)
    series = series[series["source"] == source]
    observations = observations[observations["series_id"].isin(series["id"])]
    selected_ids, start, end = render_filters(series, observations, lang)
    selected = observations[observations["series_id"].isin(selected_ids)]
    render_status(selected, fetched_at, loaded_at, lang)

    # The table uses full history up to `end`, so yearly % works even for a short date range.
    render_summary_table(summarize(selected, as_of=end), series, lang, theme)
    st.caption(text("inflation_note", lang))

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids, lang, theme)
    st.caption(f"{SOURCE_NOTES[source][lang]} {text('comparison_note', lang)}")


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
    series: pd.DataFrame, observations: pd.DataFrame, lang: Lang
) -> tuple[list[int], date, date]:
    labels = {int(row.id): getattr(row, f"name_{lang}") for row in series.itertuples()}
    first, last = observations["date"].min().date(), observations["date"].max().date()

    left, right = st.columns([2, 1])
    selected_ids = left.multiselect(
        text("series", lang),
        options=list(labels),
        default=list(labels),
        format_func=labels.__getitem__,
    )
    picked = right.date_input(
        text("date_range", lang),
        value=(first, last),
        min_value=first,
        max_value=last,
        format="DD.MM.YYYY" if lang == "tr" else "YYYY-MM-DD",
    )

    if not selected_ids:
        st.info(text("select_series", lang))
        st.stop()
    # While the user is still choosing, date_input returns only the start date.
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info(text("pick_end", lang))
        st.stop()
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
    summary: pd.DataFrame, series: pd.DataFrame, lang: Lang, theme: str
) -> None:
    table = summary.merge(series, left_on="series_id", right_on="id")
    factors = table["unit"].map(lambda unit: display_unit(unit)[0])
    units = table["unit"].map(lambda unit: unit_label(display_unit(unit)[1], lang))

    series_col, date_col = text("col_series", lang), text("col_date", lang)
    wow_col, yoy_col = text("col_wow", lang), text("col_yoy", lang)
    # One display unit for every row: put it in the header instead of repeating it per row.
    last_col = text("col_last", lang)
    if units.nunique() == 1:
        last_col = f"{last_col} ({units.iloc[0]})"

    frame = pd.DataFrame(
        {
            series_col: table[f"name_{lang}"],
            date_col: table["last_date"].dt.date,
            last_col: (table["last_value"] * factors).round(1),
            wow_col: table["wow_pct"],
            yoy_col: table["yoy_pct"],
        }
    )
    if units.nunique() > 1:
        frame.insert(3, text("col_unit", lang), units)

    colors = DELTA_COLORS.get(theme, DELTA_COLORS["light"])

    def delta_style(value: float) -> str:
        direction = change_direction(value)
        return f"color: {colors[direction]}" if direction else ""

    # The Styler changes only what is displayed; cells stay numeric, so sorting still works.
    styled = (
        frame.style.format(lambda value: format_number(value, lang), subset=[last_col])
        .format(lambda value: format_pct(value, lang), subset=[wow_col, yoy_col])
        .format(lambda day: format_date(day, lang), subset=[date_col])
        .map(delta_style, subset=[wow_col, yoy_col])
    )
    st.dataframe(styled, hide_index=True)


def render_charts(
    observations: pd.DataFrame,
    series: pd.DataFrame,
    selected_ids: list[int],
    lang: Lang,
    theme: str,
) -> None:
    color = LINE_COLORS.get(theme, LINE_COLORS["light"])
    for row_start in range(0, len(selected_ids), CHARTS_PER_ROW):
        row_ids = selected_ids[row_start : row_start + CHARTS_PER_ROW]
        for column, series_id in zip(st.columns(CHARTS_PER_ROW), row_ids, strict=False):
            meta = series.loc[series_id]
            factor, unit = display_unit(meta["unit"])
            data = observations[observations["series_id"] == series_id]
            data = data.assign(value=data["value"] * factor)
            with column:
                st.markdown(f"**{meta[f'name_{lang}']}**")
                st.altair_chart(line_chart(data, unit_label(unit, lang), color, lang))


def line_chart(data: pd.DataFrame, unit: str, color: str, lang: Lang) -> alt.LayerChart:
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
            alt.Tooltip("date:T", title=text("tooltip_date", lang), format="%d %b %Y"),
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
