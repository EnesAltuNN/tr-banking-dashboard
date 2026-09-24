from datetime import date, datetime

import pytest

from tr_banking.app.i18n import (
    LANGUAGES,
    SOURCE_LABELS,
    SOURCE_NOTES,
    TEXTS,
    UNIT_LABELS,
    change_direction,
    format_date,
    format_number,
    format_pct,
    unit_label,
)

NAN = float("nan")


@pytest.mark.parametrize("table", [TEXTS, SOURCE_LABELS, SOURCE_NOTES, UNIT_LABELS])
def test_every_text_exists_in_every_language(table: dict) -> None:
    for key, translations in table.items():
        assert set(translations) == set(LANGUAGES), key


@pytest.mark.parametrize(
    ("value", "tr", "en"),
    [
        (18445.2, "18.445,2", "18,445.2"),
        (28237.66, "28.237,7", "28,237.7"),
        (41.25, "41,2", "41.2"),  # Python rounds exact halves to even
        (1234567.0, "1.234.567,0", "1,234,567.0"),
        (-1500.55, "-1.500,5", "-1,500.5"),
        (NAN, "–", "–"),
    ],
)
def test_format_number(value: float, tr: str, en: str) -> None:
    assert format_number(value, "tr") == tr
    assert format_number(value, "en") == en


@pytest.mark.parametrize(
    ("value", "tr", "en"),
    [
        (-1.141597, "-1,1%", "-1.1%"),
        (0.38224, "+0,4%", "+0.4%"),
        (37.682995, "+37,7%", "+37.7%"),
        (0.04, "0,0%", "0.0%"),
        (-0.04, "0,0%", "0.0%"),
        (1234.5, "+1.234,5%", "+1,234.5%"),
        (NAN, "–", "–"),
    ],
)
def test_format_pct(value: float, tr: str, en: str) -> None:
    assert format_pct(value, "tr") == tr
    assert format_pct(value, "en") == en


@pytest.mark.parametrize(
    ("value", "direction"), [(0.38, 1), (-1.14, -1), (0.04, 0), (-0.04, 0), (NAN, 0)]
)
def test_change_direction_matches_displayed_rounding(value: float, direction: int) -> None:
    assert change_direction(value) == direction


def test_format_date() -> None:
    day = date(2026, 9, 18)

    assert format_date(day, "tr") == "18.09.2026"
    assert format_date(day, "en") == "2026-09-18"
    assert format_date(day, "tr", long=True) == "18 Eylül 2026"
    assert format_date(datetime(2026, 9, 4, 14, 30), "en", long=True) == "4 Sep 2026"


def test_unit_label() -> None:
    assert unit_label("billion TRY", "tr") == "milyar TL"
    assert unit_label("billion TRY", "en") == "billion TRY"
    assert unit_label("%", "tr") == "%"
