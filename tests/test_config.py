from pathlib import Path

import pytest
from pydantic import ValidationError

from tr_banking.config import SeriesConfig, load_series_config
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
        "TP.KTF10",
        "TP.KTF11",
        "TP.KTF12",
        "TP.KTF18",
        "TP.PY.P02.1H",
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

    assert len(specs) == 6
    assert all(bkm_cells(spec.code) for spec in specs)  # raises on an invalid code
    assert {spec.unit for spec in specs} == {"million TRY", "cards"}
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
    assert (len(loans), len(banking)) == (7, 13)
    assert {parse_series_code(spec.code).group for spec in loans} == {"10001"}
    # Bank groups: state, domestic private and foreign add up to the sector (10001).
    assert {parse_series_code(spec.code).group for spec in banking} == {
        "10001",
        "10005",
        "10006",
        "10007",
    }
    assert {parse_series_code(spec.code).row.split(".")[0] for spec in banking} == {"1", "2", "4"}
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
