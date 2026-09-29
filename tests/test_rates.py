"""Interest-rate metrics: yearly inflation, policy-rate changes, pp summaries."""

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tr_banking.app.metrics import (
    annual_inflation,
    policy_decisions,
    rate_changes,
    ratio_pct,
    real_rates,
    summarize_rates,
)
from tr_banking.config import load_mpc_calendar
from tr_banking.settings import PROJECT_ROOT
from tr_banking.sources.evds import parse_evds_response

FIXTURES = Path(__file__).parent / "fixtures"
CPI = "TP.TUKFIY2025.GENEL"
POLICY = "TP.PY.P02.1H"
NAN = float("nan")


def fixture_rows(name: str, codes: list[str]) -> pd.DataFrame:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    rows = parse_evds_response(payload, codes)
    return rows.assign(date=pd.to_datetime(rows["date"]))


def cpi_2023_2024() -> pd.DataFrame:
    rows = [fixture_rows(name, [CPI]) for name in ("evds_cpi_2023.json", "evds_cpi_2024.json")]
    return pd.concat(rows)[["date", "value"]].reset_index(drop=True)


def rates(series_id: int, values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": pd.to_datetime(list(values)),
            "value": list(values.values()),
        }
    )


# --- annual_inflation ---


def test_annual_inflation_matches_the_published_figures() -> None:
    inflation = annual_inflation(cpi_2023_2024()).set_index("date")["value"]

    # TUIK: June 2024 71.60%, July 2024 61.78%, December 2024 44.38%.
    assert inflation[pd.Timestamp("2024-06-30")] == pytest.approx(71.60, abs=0.01)
    assert inflation[pd.Timestamp("2024-07-31")] == pytest.approx(61.78, abs=0.01)
    assert inflation[pd.Timestamp("2024-12-31")] == pytest.approx(44.38, abs=0.01)


def test_annual_inflation_needs_the_same_month_a_year_earlier() -> None:
    inflation = annual_inflation(cpi_2023_2024())

    # 2023 has no 2022 to compare with, so the first row is January 2024.
    assert inflation["date"].tolist() == list(pd.date_range("2024-01-31", periods=12, freq="ME"))


def test_annual_inflation_rejects_mixed_bases() -> None:
    cpi = cpi_2023_2024()
    cpi.loc[cpi["date"] >= "2024-06-30", "value"] *= 30  # an old-base level glued on

    with pytest.raises(ValueError, match="were two bases mixed"):
        annual_inflation(cpi)


# --- rate_changes ---


def test_policy_rate_changes_are_the_decision_dates() -> None:
    policy = fixture_rows("evds_policy_rate_2024_2025.json", [POLICY])[["date", "value"]]

    changes = rate_changes(policy)

    # MPC decision of 26 December 2024: 50% -> 47.5%. The 1 January holiday is no change.
    assert changes.to_dict("records") == [
        {"date": pd.Timestamp("2024-12-26"), "previous": 50.0, "value": 47.5}
    ]


def test_no_changes_in_an_unchanged_or_empty_rate() -> None:
    flat = rates(1, {"2026-01-02": 37.0, "2026-01-05": 37.0})[["date", "value"]]

    assert rate_changes(flat).empty
    assert rate_changes(flat.iloc[0:0]).empty


# --- policy_decisions ---


def test_the_project_calendar_classifies_the_real_december_2024_cut() -> None:
    policy = fixture_rows("evds_policy_rate_2024_2025.json", [POLICY])[["date", "value"]]
    meetings = load_mpc_calendar(PROJECT_ROOT / "config" / "mpc_meetings.yaml").meetings

    decisions = policy_decisions(policy, meetings)

    # The fixture runs 2024-12-16 .. 2025-01-10: only the 26 December meeting is inside it.
    assert decisions.to_dict("records") == [
        {"date": pd.Timestamp("2024-12-26"), "previous": 50.0, "value": 47.5, "decision": "cut"}
    ]


def test_holds_are_marked_and_unlisted_changes_are_kept() -> None:
    policy = rates(
        1,
        {
            "2026-03-11": 38.0,
            "2026-03-12": 37.0,  # meeting: cut
            "2026-04-22": 37.0,  # meeting: hold
            "2026-05-05": 40.0,  # no meeting listed: an unscheduled hike
            "2026-06-11": 40.0,  # meeting: hold
        },
    )[["date", "value"]]
    meetings = [
        date(2026, 1, 22),  # before the data: left out
        date(2026, 3, 12),
        date(2026, 4, 22),
        date(2026, 6, 11),
        date(2026, 7, 23),  # still to come: left out
    ]

    decisions = policy_decisions(policy, meetings)

    assert decisions[["date", "decision"]].to_dict("records") == [
        {"date": pd.Timestamp("2026-03-12"), "decision": "cut"},
        {"date": pd.Timestamp("2026-04-22"), "decision": "hold"},
        {"date": pd.Timestamp("2026-05-05"), "decision": "hike"},
        {"date": pd.Timestamp("2026-06-11"), "decision": "hold"},
    ]


def test_no_decisions_without_data() -> None:
    empty = rates(1, {})[["date", "value"]]

    assert policy_decisions(empty, [date(2026, 3, 12)]).empty


# --- summarize_rates ---


def test_weekly_and_yearly_changes_are_in_percentage_points() -> None:
    weekly = rates(1, {"2025-09-19": 50.0, "2026-09-11": 42.5, "2026-09-18": 41.25})

    [row] = summarize_rates(weekly, pd.DataFrame(columns=["date", "value"])).to_dict("records")

    assert row["last_value"] == 41.25
    assert row["wow_pp"] == pytest.approx(-1.25)  # not -2.9%
    assert row["yoy_pp"] == pytest.approx(-8.75)  # 52 weeks earlier


def test_missing_week_gives_nan_not_an_older_week() -> None:
    weekly = rates(1, {"2026-09-04": 40.0, "2026-09-18": 41.0})

    [row] = summarize_rates(weekly, pd.DataFrame(columns=["date", "value"])).to_dict("records")

    assert math.isnan(row["wow_pp"])


def test_daily_rate_compares_with_the_last_business_day_a_week_earlier() -> None:
    # Friday 2 Jan 2026 -> a week earlier is Friday 26 Dec; there it is Thursday 25 Dec.
    daily = rates(2, {"2025-12-24": 38.0, "2025-12-25": 38.0, "2026-01-02": 37.0})

    [row] = summarize_rates(daily, pd.DataFrame(columns=["date", "value"])).to_dict("records")

    assert row["wow_pp"] == pytest.approx(-1.0)


def test_real_rate_is_rate_minus_the_inflation_of_its_month() -> None:
    inflation = annual_inflation(cpi_2023_2024())
    loan = rates(1, {"2024-07-05": 76.92, "2024-07-12": 77.90})
    policy = rates(2, {"2024-07-12": 50.0})

    summary = summarize_rates(pd.concat([loan, policy]), inflation, real_rate_ids=[1])

    real = dict(zip(summary["series_id"], summary["real_pp"], strict=True))
    assert real[1] == pytest.approx(77.90 - 61.78, abs=0.01)  # simple difference, not Fisher
    assert math.isnan(real[2])  # the policy rate gets no real rate column value


def test_real_rate_is_empty_while_the_month_has_no_cpi() -> None:
    inflation = annual_inflation(cpi_2023_2024())  # up to December 2024
    loan = rates(1, {"2025-01-03": 60.0})

    [row] = summarize_rates(loan, inflation, real_rate_ids=[1]).to_dict("records")

    assert math.isnan(row["real_pp"])


def test_as_of_limits_the_summary() -> None:
    weekly = rates(1, {"2026-09-11": 42.5, "2026-09-18": 41.25})

    summary = summarize_rates(
        weekly, pd.DataFrame(columns=["date", "value"]), as_of=date(2026, 9, 11)
    )

    assert summary["last_value"].tolist() == [42.5]


def test_real_rates_keep_only_weeks_with_cpi() -> None:
    inflation = annual_inflation(cpi_2023_2024())  # up to December 2024
    loan = rates(1, {"2024-07-12": 77.90, "2024-12-27": 60.0, "2025-01-03": 59.0})

    real = real_rates(loan, inflation)

    assert real["date"].tolist() == [pd.Timestamp("2024-07-12"), pd.Timestamp("2024-12-27")]
    assert real["value"].iloc[0] == pytest.approx(77.90 - 61.78, abs=0.01)
    assert real_rates(loan, inflation.iloc[0:0]).empty


# --- ratios (banking sector) ---


def frame(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(list(values)), "value": list(values.values())})


def test_ratio_uses_only_common_dates() -> None:
    fx = frame({"2026-09-11": 12.0, "2026-09-18": 12.585})
    total = frame({"2026-09-04": 30.0, "2026-09-18": 32.553})

    share = ratio_pct(fx, total)

    assert share["date"].tolist() == [pd.Timestamp("2026-09-18")]
    assert share["value"].iloc[0] == pytest.approx(38.66, abs=0.01)


def test_npl_ratio_divides_by_loans_plus_npl() -> None:
    # 2026-09-18, million TRY: NPL 876,498; performing loans 28,237,686 -> 3.01%.
    npl = frame({"2026-09-18": 876_498.0})
    loans = frame({"2026-09-18": 28_237_686.0})

    assert ratio_pct(npl, loans, of_total=True)["value"].iloc[0] == pytest.approx(3.01, abs=0.005)
    assert ratio_pct(npl, loans)["value"].iloc[0] == pytest.approx(3.10, abs=0.005)
