"""Real values: CPI base continuity and deflation (fixtures are real EVDS responses)."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tr_banking.app.metrics import check_price_index, deflate
from tr_banking.sources.evds import parse_evds_response

FIXTURES = Path(__file__).parent / "fixtures"
NEW_BASE = "TP.TUKFIY2025.GENEL"  # 2025=100, back-cast by TUIK; the one series.yaml uses
OLD_BASE = "TP.GENENDEKS.T1"  # 2003=100, discontinued; only here, to check the transition


def base_indexes() -> pd.DataFrame:
    """Both bases for 2025-10 .. 2026-03, one column per code, indexed by month-end date."""
    payload = json.loads((FIXTURES / "evds_cpi_bases_2025_2026.json").read_text(encoding="utf-8"))
    rows = parse_evds_response(payload, [NEW_BASE, OLD_BASE])
    return rows.pivot(index="date", columns="code", values="value").rename_axis(None, axis=1)


def price_index(values: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(values.index), "value": values.to_numpy()})


def test_new_base_keeps_the_old_monthly_changes_across_the_base_change() -> None:
    # The base changed in January 2026. TUIK's back-cast must reproduce the old base's
    # month-over-month changes, including Dec 2025 -> Jan 2026, or we would need own chaining.
    changes = base_indexes().pct_change().dropna() * 100

    assert (changes[NEW_BASE] - changes[OLD_BASE]).abs().max() <= 0.01
    assert changes.loc[date(2026, 1, 31), NEW_BASE] == pytest.approx(4.84, abs=0.01)


def test_mixed_bases_without_chaining_are_rejected() -> None:
    # Old base up to Dec 2025 glued to the new base from Jan 2026: a fake -97% "deflation".
    indexes = base_indexes()
    before = [day < date(2026, 1, 1) for day in indexes.index]
    mixed = indexes[OLD_BASE].where(before, indexes[NEW_BASE])

    with pytest.raises(ValueError, match="2026-01; were two bases mixed"):
        check_price_index(price_index(mixed))
    check_price_index(price_index(indexes[NEW_BASE]))  # the real series passes


def test_constant_nominal_value_falls_only_by_the_monthly_cpi_change() -> None:
    cpi = base_indexes()[NEW_BASE]
    weeks = pd.date_range("2025-10-03", "2026-03-27", freq="W-FRI")
    nominal = pd.DataFrame({"series_id": 1, "date": weeks, "value": 100.0})

    real, reference = deflate(nominal, price_index(cpi))

    assert reference == pd.Period("2026-03", "M")
    by_month = real.groupby(real["date"].dt.to_period("M"))["value"]
    assert (by_month.nunique() == 1).all()  # flat within a month: one CPI per month
    monthly = by_month.first()
    # Each month-to-month step is exactly the inverse of that month's CPI change...
    expected = cpi.to_numpy()[:-1] / cpi.to_numpy()[1:]
    assert (monthly / monthly.shift()).dropna().to_numpy() == pytest.approx(expected)
    # ...so across the base change it falls by January's 4.84%, nothing more.
    assert monthly[pd.Period("2026-01", "M")] / monthly[pd.Period("2025-12", "M")] == pytest.approx(
        1 / 1.0484, abs=1e-4
    )
    assert monthly[pd.Period("2026-03", "M")] == pytest.approx(100.0)  # reference month


def test_weeks_without_cpi_are_dropped() -> None:
    cpi = price_index(pd.Series([100.0, 102.0], index=pd.to_datetime(["2026-07-31", "2026-08-31"])))
    weeks = pd.to_datetime(["2026-07-24", "2026-08-28", "2026-09-04", "2026-09-11"])
    nominal = pd.DataFrame({"series_id": 1, "date": weeks, "value": 50.0})

    real, reference = deflate(nominal, cpi)

    assert reference == pd.Period("2026-08", "M")
    assert real["date"].tolist() == list(weeks[:2])  # September has no CPI yet
    assert real["value"].tolist() == pytest.approx([51.0, 50.0])


def test_deflate_keeps_other_columns() -> None:
    cpi = price_index(pd.Series([100.0], index=pd.to_datetime(["2026-08-31"])))
    nominal = pd.DataFrame(
        {"series_id": [1, 2], "date": pd.to_datetime(["2026-08-07"] * 2), "value": [1.0, 2.0]}
    )

    real, _ = deflate(nominal, cpi)

    assert real.columns.tolist() == ["series_id", "date", "value"]
    assert real["series_id"].tolist() == [1, 2]
