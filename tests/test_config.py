from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from tr_banking.config import MpcCalendar, SeriesConfig, load_mpc_calendar, load_series_config
from tr_banking.settings import PROJECT_ROOT
from tr_banking.sources.bddk import parse_series_code
from tr_banking.sources.bkm import parse_series_code as bkm_cells

VALID_SPEC = {
    "source": "evds",
    "code": "TP.TEST.1",
    "name_tr": "Test serisi",
    "name_en": "Test series",
    "unit": "thousand TRY",
    "frequency": "weekly",
    "module": "credit",
}


def test_project_series_yaml_loads() -> None:
    config = load_series_config(PROJECT_ROOT / "config" / "series.yaml")

    codes = [spec.code for spec in config.for_source("evds")]
    assert codes == [
        "TP.HPBITABLO6.2",
        "TP.HPBITABLO6.3",
        "TP.HPBITABLO6.7",
        "TP.HPBITABLO6.11",
        "TP.HPBITABLO6.16",
        "TP.HPBITABLO6.20",
        "TP.TUKFIY2025.GENEL",
        "TP.DK.USD.A.YTL",
        "TP.KTF10",
        "TP.KTF11",
        "TP.KTF12",
        "TP.KTF18",
        "TP.TRY.MT06",
        "TP.TRY.MT01",
        "TP.TRY.MT02",
        "TP.TRY.MT03",
        "TP.TRY.MT04",
        "TP.TRY.MT05",
        "TP.PY.P02.1H",
        "TP.APIFON4",
    ]
    assert {spec.module for spec in config.series} == {
        "credit",
        "macro",
        "rates",
        "cards",
        "banking",
    }


def test_project_bkm_series_have_valid_codes() -> None:
    specs = load_series_config(PROJECT_ROOT / "config" / "series.yaml").for_source("bkm")

    assert len(specs) == 8
    assert all(bkm_cells(spec.code) for spec in specs)  # raises on an invalid code
    assert {spec.unit for spec in specs} == {"million TRY", "cards", "terminals"}
    assert {(spec.frequency, spec.module, spec.max_age_days) for spec in specs} == {
        ("monthly", "cards", 100)
    }


def test_project_rates_are_percent_and_the_policy_rate_is_marked() -> None:
    config = load_series_config(PROJECT_ROOT / "config" / "series.yaml")

    rates = [spec for spec in config.series if spec.module == "rates"]
    assert {spec.unit for spec in rates} == {"%"}
    policy = config.policy_rate
    assert policy is not None
    assert (policy.code, policy.frequency, policy.max_age_days) == ("TP.PY.P02.1H", "daily", 14)


def test_project_mpc_calendar_is_ordered_and_starts_inside_the_policy_rate_series() -> None:
    meetings = load_mpc_calendar(PROJECT_ROOT / "config" / "mpc_meetings.yaml").meetings

    assert meetings[0] == date(2018, 10, 25)  # the EVDS series starts on 2018-09-14
    assert {date(2024, 12, 26), date(2025, 3, 20), date(2026, 9, 10)} <= set(meetings)
    assert len([day for day in meetings if day.year == 2024]) == 12


def test_mpc_calendar_rejects_duplicates_and_disorder() -> None:
    with pytest.raises(ValidationError, match="increasing order"):
        MpcCalendar.model_validate({"meetings": ["2026-03-12", "2026-03-12"]})
    with pytest.raises(ValidationError, match="increasing order"):
        MpcCalendar.model_validate({"meetings": ["2026-04-22", "2026-03-12"]})


def test_project_deflator_is_the_2025_based_cpi() -> None:
    config = load_series_config(PROJECT_ROOT / "config" / "series.yaml")

    deflator = config.deflator
    assert deflator is not None
    assert (deflator.source, deflator.code) == ("evds", "TP.TUKFIY2025.GENEL")
    assert (deflator.frequency, deflator.module) == ("monthly", "macro")
    # The old 2003=100 index must never be mixed in (see docs/DATA_SOURCES.md).
    assert all(spec.code != "TP.GENENDEKS.T1" for spec in config.series)


def test_project_bddk_series_have_valid_codes() -> None:
    specs = load_series_config(PROJECT_ROOT / "config" / "series.yaml").for_source("bddk")

    loans = [spec for spec in specs if spec.module == "credit"]
    banking = [spec for spec in specs if spec.module == "banking"]
    assert (len(loans), len(banking)) == (7, 30)
    assert {parse_series_code(spec.code).group for spec in loans} == {"10001"}
    # Bank groups: state, domestic private and foreign add up to the sector (10001).
    assert {parse_series_code(spec.code).group for spec in banking} == {
        "10001",
        "10005",
        "10006",
        "10007",
    }
    # Tables: 1 loans, 2 NPL, 3 securities, 4 deposits, 5 other items, 9 FX position.
    assert {parse_series_code(spec.code).row.split(".")[0] for spec in banking} == {
        "1",
        "2",
        "3",
        "4",
        "5",
        "9",
    }
    assert {spec.unit for spec in specs} == {"million TRY"}


def test_turkish_characters_survive_loading(tmp_path: Path) -> None:
    path = tmp_path / "series.yaml"
    path.write_text(
        "series:\n"
        "  - {source: evds, code: X, name_tr: İhtiyaç kredileri, name_en: General,"
        " unit: thousand TRY, frequency: weekly, module: credit}\n",
        encoding="utf-8",
    )

    config = load_series_config(path)

    assert config.series[0].name_tr == "İhtiyaç kredileri"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("source", "twitter"), ("frequency", "hourly"), ("module", "stocks"), ("code", "")],
)
def test_invalid_field_values_are_rejected(field: str, bad_value: str) -> None:
    with pytest.raises(ValidationError, match=field):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, field: bad_value}]})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="frequncy"):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, "frequncy": "weekly"}]})


def test_duplicate_series_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate series"):
        SeriesConfig.model_validate({"series": [VALID_SPEC, VALID_SPEC]})


def test_same_code_in_different_sources_is_allowed() -> None:
    config = SeriesConfig.model_validate({"series": [VALID_SPEC, {**VALID_SPEC, "source": "bddk"}]})

    assert len(config.series) == 2


def test_empty_series_list_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate({"series": []})


@pytest.mark.parametrize("role", ["deflator", "policy_rate"])
def test_only_one_series_per_role_is_allowed(role: str) -> None:
    other = {**VALID_SPEC, "code": "TP.TEST.2", role: True}

    with pytest.raises(ValidationError, match=f"at most one series may be the {role}"):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, role: True}, other]})


def test_optional_fields_default_to_off() -> None:
    config = SeriesConfig.model_validate({"series": [VALID_SPEC]})

    assert config.series[0].max_age_days is None
    assert config.deflator is None
    assert config.policy_rate is None


def test_max_age_days_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="max_age_days"):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, "max_age_days": 0}]})


def test_noisy_series_are_kept_out_of_the_alerts() -> None:
    # Measured on 2014-2026: these flagged 3.8-7.5% of weeks (target about 1.5%). NPL drops
    # with monthly write-offs, own funds jump with monthly profits, the net FX position sits
    # near zero (so its % change means nothing), and the weekly deposit rate is a noisy flow.
    specs = load_series_config(PROJECT_ROOT / "config" / "series.yaml").series

    assert {spec.code for spec in specs if not spec.alerts} == {
        "2.0.1:10005:TRY:3",
        "2.0.1:10006:TRY:3",
        "2.0.1:10007:TRY:3",
        "9.0.17:10001:TRY:3",
        "9.0.18:10001:TRY:3",
        "TP.TRY.MT06",
        "TP.TRY.MT01",
        "TP.TRY.MT02",
        "TP.TRY.MT03",
        "TP.TRY.MT04",
        "TP.TRY.MT05",
    }
