from pathlib import Path

import pytest
from pydantic import ValidationError

from tr_banking.config import SeriesConfig, load_series_config
from tr_banking.settings import PROJECT_ROOT
from tr_banking.sources.bddk import parse_series_code

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
    ]
    assert {spec.module for spec in config.series} == {"credit", "macro"}


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

    assert len(specs) == 7
    assert {parse_series_code(spec.code).group for spec in specs} == {"10001"}
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


def test_only_one_deflator_is_allowed() -> None:
    other = {**VALID_SPEC, "code": "TP.TEST.2", "deflator": True}

    with pytest.raises(ValidationError, match="at most one series may be the deflator"):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, "deflator": True}, other]})


def test_optional_fields_default_to_off() -> None:
    config = SeriesConfig.model_validate({"series": [VALID_SPEC]})

    assert config.series[0].max_age_days is None
    assert config.deflator is None


def test_max_age_days_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="max_age_days"):
        SeriesConfig.model_validate({"series": [{**VALID_SPEC, "max_age_days": 0}]})
