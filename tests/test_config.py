from pathlib import Path

import pytest
from pydantic import ValidationError

from tr_banking.config import SeriesConfig, load_series_config
from tr_banking.settings import PROJECT_ROOT

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
    ]
    assert all(spec.module == "credit" for spec in config.series)


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
