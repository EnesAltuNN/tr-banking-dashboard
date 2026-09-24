"""Streamlit dashboard. Run with: uv run streamlit run src/tr_banking/app/dashboard.py"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from tr_banking.app.metrics import display_unit, summarize
from tr_banking.db.repository import Repository
from tr_banking.settings import get_settings

DISPLAY_TZ = ZoneInfo("Europe/Istanbul")
# One series per chart, so one color; steps validated for contrast on each theme's surface.
LINE_COLORS = {"light": "#2a78d6", "dark": "#3987e5"}
CROSSHAIR_COLOR = "#898781"
CHARTS_PER_ROW = 2

INFLATION_NOTE = (
    "Values are **nominal TRY**, not adjusted for inflation. With consumer price inflation in "
    "Türkiye still high, much of the growth shown here reflects rising prices rather than real "
    "credit expansion. FX-denominated loans are included at their TRY value, so lira "
    "depreciation also pushes these figures up."
)
# Display order of the source picker; only sources with data are shown.
SOURCE_LABELS = {"evds": "TCMB EVDS", "bddk": "BDDK weekly bulletin"}
SOURCE_NOTES = {
    "evds": (
        "Source: CBRT (TCMB) EVDS, data group `bie_hpbitablo6` - Banking Sector Selected Loans "
        "(domestic branches), weekly Friday values, TRY + FX, available from 2024-06-28."
    ),
    "bddk": (
        "Source: BDDK weekly bulletin (Haftalık Bülten), table *Krediler*, whole sector, "
        "weekly Friday values, TRY + FX, available from 2014-01-03. Coverage differs slightly "
        "from EVDS (common series agree within about 0.3%); BDDK *Commercial and other loans* "
        "is broader than EVDS *Commercial loans*."
    ),
}
COMPARISON_NOTE = (
    "Week-over-week compares with the previous week; year-over-year with the same week "
    "52 weeks earlier."
)


@st.cache_data(ttl=300)
def load_data(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame, datetime | None]:
    """Series, observations and last fetch time; empty frames if the database does not exist."""
    if not Path(db_path).exists():
        return pd.DataFrame(), pd.DataFrame(), None
    with Repository(db_path) as repo:
        return repo.list_series(), repo.get_observations(), repo.last_fetched_at()


def main() -> None:
    st.set_page_config(page_title="TR Banking Dashboard", layout="wide")
    st.title("Turkish Banking Market Dashboard")
    st.caption("Credit market · weekly banking sector loans")

    series, observations, fetched_at = load_data(str(get_settings().db_path))
    if observations.empty:
        st.info("No data yet. Load it with `uv run tr-banking backfill --start 2014-01-03`.")
        st.stop()

    source = render_source_picker(series)
    series = series[series["source"] == source]
    observations = observations[observations["series_id"].isin(series["id"])]
    selected_ids, start, end = render_filters(series, observations)
    selected = observations[observations["series_id"].isin(selected_ids)]
    render_status(selected, fetched_at)

    # The table uses full history up to `end`, so YoY works even for a short date range.
    render_summary_table(summarize(selected, as_of=end), series)
    st.caption(INFLATION_NOTE)

    in_range = selected[selected["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    render_charts(in_range, series.set_index("id"), selected_ids)
    st.caption(f"{SOURCE_NOTES.get(source, '')} {COMPARISON_NOTE}")


def render_source_picker(series: pd.DataFrame) -> str:
    """One source at a time: each has its own history length, units and definitions."""
    available = [source for source in SOURCE_LABELS if source in set(series["source"])]
    return st.radio(
        "Source", options=available, format_func=SOURCE_LABELS.__getitem__, horizontal=True
    )


def render_filters(
    series: pd.DataFrame, observations: pd.DataFrame
) -> tuple[list[int], date, date]:
    labels = {int(row.id): row.name_en for row in series.itertuples()}
    first, last = observations["date"].min().date(), observations["date"].max().date()

    left, right = st.columns([2, 1])
    selected_ids = left.multiselect(
        "Series", options=list(labels), default=list(labels), format_func=labels.__getitem__
    )
    picked = right.date_input(
        "Date range", value=(first, last), min_value=first, max_value=last, format="YYYY-MM-DD"
    )

    if not selected_ids:
        st.info("Select at least one series.")
        st.stop()
    # While the user is still choosing, date_input returns only the start date.
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Pick an end date for the range.")
        st.stop()
    start, end = picked
    return selected_ids, start, end


def render_status(observations: pd.DataFrame, fetched_at: datetime | None) -> None:
    latest_week = observations["date"].max().strftime("%d %b %Y")
    updated = fetched_at.astimezone(DISPLAY_TZ).strftime("%d %b %Y %H:%M") if fetched_at else "-"
    st.markdown(f"Latest data: week ending **{latest_week}** · Last updated: **{updated}** (TRT)")


def render_summary_table(summary: pd.DataFrame, series: pd.DataFrame) -> None:
    table = summary.merge(series, left_on="series_id", right_on="id")
    units = table["unit"].map(display_unit)
    st.dataframe(
        pd.DataFrame(
            {
                "Series": table["name_en"],
                "Seri": table["name_tr"],
                "Week ending": table["last_date"].dt.date,
                "Last value": (table["last_value"] * units.str[0]).round(1),
                "Unit": units.str[1],
                "WoW %": table["wow_pct"],
                "YoY %": table["yoy_pct"],
            }
        ),
        hide_index=True,
        column_config={
            "Last value": st.column_config.NumberColumn(format="localized"),
            "WoW %": st.column_config.NumberColumn(format="%+.2f%%"),
            "YoY %": st.column_config.NumberColumn(format="%+.1f%%"),
        },
    )


def render_charts(
    observations: pd.DataFrame, series: pd.DataFrame, selected_ids: list[int]
) -> None:
    color = LINE_COLORS.get(st.context.theme.type or "light", LINE_COLORS["light"])
    for row_start in range(0, len(selected_ids), CHARTS_PER_ROW):
        row_ids = selected_ids[row_start : row_start + CHARTS_PER_ROW]
        for column, series_id in zip(st.columns(CHARTS_PER_ROW), row_ids, strict=False):
            meta = series.loc[series_id]
            factor, unit_label = display_unit(meta["unit"])
            data = observations[observations["series_id"] == series_id]
            data = data.assign(value=data["value"] * factor)
            with column:
                st.markdown(f"**{meta['name_en']}** · {meta['name_tr']}")
                st.altair_chart(line_chart(data, unit_label, color))


def line_chart(data: pd.DataFrame, unit_label: str, color: str) -> alt.LayerChart:
    """2px line with a snapping crosshair; each series gets its own y-scale."""
    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(data).encode(x=alt.X("date:T", title=None))
    y = alt.Y(
        "value:Q", title=unit_label, scale=alt.Scale(zero=False), axis=alt.Axis(format=",.0f")
    )

    line = base.mark_line(color=color, strokeWidth=2, strokeCap="round", strokeJoin="round")
    # Invisible full-height rules make the whole column the hover target, not the thin line.
    targets = base.mark_rule(strokeWidth=12, opacity=0).encode(
        tooltip=[
            alt.Tooltip("date:T", title="Week ending", format="%d %b %Y"),
            alt.Tooltip("value:Q", title=unit_label, format=",.1f"),
        ]
    )
    crosshair = base.mark_rule(color=CROSSHAIR_COLOR, strokeWidth=1).transform_filter(hover)
    dot = base.mark_circle(color=color, size=64, opacity=1).encode(y=y).transform_filter(hover)
    return alt.layer(line.encode(y=y), targets.add_params(hover), crosshair, dot).properties(
        height=260
    )


if __name__ == "__main__":
    main()
