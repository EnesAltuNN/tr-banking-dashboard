"""Weekly summary: Python computes the facts, a Claude model only writes the text.

`build_brief` turns the stored series into a small JSON of numbers (pure, no I/O);
`write_summary` asks the model for a short Turkish and English text about exactly those
numbers. The JSON is stored next to the text, so every sentence can be checked against it.
"""

import json
import math
from typing import Any

import anthropic
import pandas as pd

from tr_banking.app.i18n import text
from tr_banking.app.metrics import (
    MONETARY_UNITS,
    YEAR,
    annual_inflation,
    banking_ratios,
    category_id,
    deflate,
    display_unit,
    real_rates,
    spec_id,
    spec_observations,
    summarize,
    summarize_rates,
    unusual_changes,
    value_near,
    with_categories,
)
from tr_banking.config import SeriesConfig

# Server-side fallback: if the model's safety classifiers decline the request, the API re-runs
# it on the model Anthropic recommends for that case, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16_000

SYSTEM_PROMPT = """\
You write the weekly summary for a public dashboard of Türkiye's banking market (loans, \
interest rates, card spending, deposits and non-performing loans).

The user message is a JSON of facts computed from official statistics (CBRT EVDS, BDDK, \
BKM). Write only about these facts:
- Every number you mention must appear in the JSON (rounding is fine). Do not compute new \
numbers, and do not add causes, forecasts, opinions or advice.
- Mention the entries of "unusual_changes" first, if there are any.
- Then cover, in this order: loans (nominal and real growth), interest rates and inflation, \
the banking sector ratios, card spending. Pick the few most informative facts; do not list \
everything.
- Changes of rates and ratios are in percentage points ("puan" in Turkish, "pp" in English); \
changes of amounts are in %.
- Name the period the data covers (the data week, and the month for card spending and CPI).

Write the same summary twice: in Turkish (number format 18.445,2 and %3,0) and in English \
(18,445.2 and 3.0%). Each version is plain text of 120 to 170 words in 2 or 3 short \
paragraphs, neutral and factual, without headings, lists or markdown."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "tr": {"type": "string", "description": "The summary in Turkish."},
        "en": {"type": "string", "description": "The same summary in English."},
    },
    "required": ["tr", "en"],
    "additionalProperties": False,
}


class SummaryError(RuntimeError):
    """The model did not return a usable summary (refusal, truncation, bad output)."""


def number(value: float | None, digits: int = 2) -> float | None:
    """JSON-friendly rounding; NaN (no comparison possible) becomes null."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits)


def build_brief(
    series: pd.DataFrame, observations: pd.DataFrame, config: SeriesConfig
) -> dict[str, Any]:
    """The facts of the latest week, as plain JSON-ready data."""
    series = with_categories(series, config)
    price_index = spec_observations(series, observations, config.deflator)
    inflation = annual_inflation(price_index) if not price_index.empty else price_index
    weekly = series[(series["frequency"] == "weekly") & (series["module"] != "macro")]
    weekly_rows = observations[observations["series_id"].isin(weekly["id"])]
    return {
        "data_week": weekly_rows["date"].max().date().isoformat(),
        "unusual_changes": _unusual(weekly, weekly_rows),
        "loans": _loans(series, observations, price_index),
        "interest_rates": _rates(
            series, observations, inflation, spec_id(series, config.policy_rate)
        ),
        "inflation": _inflation(inflation),
        "banking_sector": _banking(series, observations),
        "card_spending": _cards(series, observations),
    }


def _rows(observations: pd.DataFrame, series_id: int) -> pd.DataFrame:
    return observations[observations["series_id"] == series_id]


def _unusual(weekly: pd.DataFrame, rows: pd.DataFrame) -> list[dict[str, Any]]:
    points = set(weekly.loc[weekly["unit"] == "%", "id"])
    meta = weekly.set_index("id")
    return [
        {
            "series": meta.loc[alert.series_id, "name_en"],
            "source": meta.loc[alert.series_id, "source"].upper(),
            "week": alert.date.date().isoformat(),
            "change": number(alert.change),
            "change_unit": "pp" if alert.series_id in points else "%",
            "typical_change_past_year": number(alert.typical),
            "typical_spread_past_year": number(alert.spread),
        }
        for alert in unusual_changes(rows, points_ids=points).itertuples()
    ]


def _loans(
    series: pd.DataFrame, observations: pd.DataFrame, price_index: pd.DataFrame
) -> dict[str, Any]:
    """CBRT loan stocks (weekly), plus BDDK's total, nominal and in real terms."""
    credit = series[series["module"] == "credit"]
    chosen = credit[(credit["source"] == "evds") | (credit["category"] == "total")]
    items = []
    reference = None
    for spec in chosen.itertuples():
        rows = _rows(observations, spec.id)
        if rows.empty:
            continue
        factor, unit = display_unit(spec.unit)
        last = summarize(rows).iloc[0]
        item = {
            "series": spec.name_en,
            "source": spec.source.upper(),
            "week": last.last_date.date().isoformat(),
            "value": number(last.last_value * factor, 1),
            "unit": unit,
            "weekly_change_pct": number(last.prev_pct),
            "yearly_change_pct": number(last.yoy_pct),
        }
        if not price_index.empty and spec.unit in MONETARY_UNITS:
            real, reference = deflate(rows, price_index)
            if not real.empty:
                item["real_yearly_change_pct"] = number(summarize(real).iloc[0].yoy_pct)
        items.append(item)
    real_note = f"real values in prices of {reference}" if reference is not None else None
    return {"note": real_note, "series": items}


def _rates(
    series: pd.DataFrame,
    observations: pd.DataFrame,
    inflation: pd.DataFrame,
    policy_id: int | None,
) -> list[dict[str, Any]]:
    """Loan rates and the policy rate in %, changes in percentage points."""
    items = []
    for spec in series[series["module"] == "rates"].itertuples():
        rows = _rows(observations, spec.id)
        if rows.empty:
            continue
        last = summarize_rates(rows, inflation).iloc[0]
        item = {
            "series": spec.name_en,
            "date": last.last_date.date().isoformat(),
            "rate_pct": number(last.last_value),
            "weekly_change_pp": number(last.wow_pp),
            "yearly_change_pp": number(last.yoy_pp),
        }
        if spec.id != policy_id and not inflation.empty:
            real = real_rates(rows, inflation)
            if not real.empty:
                values = real.set_index("date")["value"].sort_index()
                week = values.index.max()
                item["real_rate_pp"] = {
                    "definition": "rate minus yearly CPI inflation of the same month",
                    "week": week.date().isoformat(),
                    "value": number(values[week]),
                    "yearly_change_pp": number(values[week] - value_near(values, week - YEAR)),
                }
        items.append(item)
    return items


def _inflation(inflation: pd.DataFrame) -> dict[str, Any] | None:
    if inflation.empty:
        return None
    values = inflation.sort_values("date")
    latest, before = values.iloc[-1], values.iloc[-2] if len(values) > 1 else None
    return {
        "measure": "yearly CPI inflation (TÜİK, 2025=100)",
        "month": latest["date"].strftime("%Y-%m"),
        "value_pct": number(latest["value"]),
        "previous_month_pct": number(before["value"]) if before is not None else None,
    }


def _banking(series: pd.DataFrame, observations: pd.DataFrame) -> dict[str, Any]:
    """BDDK deposits and the ratios computed from BDDK's tables."""
    banking = series[series["module"] == "banking"]
    deposits = None
    deposits_id = category_id(banking, "deposits")
    if deposits_id is not None and not _rows(observations, deposits_id).empty:
        last = summarize(_rows(observations, deposits_id)).iloc[0]
        factor, unit = display_unit("million TRY")
        deposits = {
            "week": last.last_date.date().isoformat(),
            "value": number(last.last_value * factor, 1),
            "unit": unit,
            "yearly_change_pct": number(last.yoy_pct),
        }
    ratios = []
    for ratio in banking_ratios(series, observations):
        last = summarize_rates(ratio.values.assign(series_id=0), pd.DataFrame()).iloc[0]
        ratios.append(
            {
                "ratio": text(ratio.key, "en"),
                "week": last.last_date.date().isoformat(),
                "value_pct": number(last.last_value),
                "weekly_change_pp": number(last.wow_pp),
                "yearly_change_pp": number(last.yoy_pp),
            }
        )
    return {"total_deposits": deposits, "ratios": ratios}


def _cards(series: pd.DataFrame, observations: pd.DataFrame) -> list[dict[str, Any]]:
    """BKM monthly card statistics (nominal)."""
    items = []
    for spec in series[series["module"] == "cards"].itertuples():
        rows = _rows(observations, spec.id)
        if rows.empty:
            continue
        factor, unit = display_unit(spec.unit)
        last = summarize(rows, frequency="monthly").iloc[0]
        items.append(
            {
                "series": spec.name_en,
                "month": last.last_date.strftime("%Y-%m"),
                "value": number(last.last_value * factor, 1),
                "unit": unit,
                "monthly_change_pct": number(last.prev_pct),
                "yearly_change_pct": number(last.yoy_pct),
            }
        )
    return items


def write_summary(
    client: anthropic.Anthropic, brief: dict[str, Any], model: str
) -> tuple[str, str, str]:
    """(model that wrote it, Turkish text, English text) for the facts in `brief`."""
    response = client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(brief, ensure_ascii=False, indent=1)}],
    )
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise SummaryError(f"the model declined the request (category: {category})")
    if response.stop_reason == "max_tokens":
        raise SummaryError(f"the summary was cut off at {MAX_TOKENS} tokens")
    reply = next((block.text for block in response.content if block.type == "text"), "")
    try:
        texts = json.loads(reply)
    except json.JSONDecodeError as exc:
        raise SummaryError("the model did not return JSON") from exc
    text_tr, text_en = str(texts.get("tr", "")).strip(), str(texts.get("en", "")).strip()
    if not text_tr or not text_en:
        raise SummaryError("the summary is missing its Turkish or English text")
    return response.model, text_tr, text_en
