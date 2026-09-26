"""Dashboard texts and number/date formatting in Turkish and English (pure, no Streamlit)."""

import math
from datetime import date, datetime
from typing import Literal

Lang = Literal["tr", "en"]
LANGUAGES: dict[Lang, str] = {"tr": "TR", "en": "EN"}

TEXTS: dict[str, dict[Lang, str]] = {
    "title": {
        "tr": "Türkiye Bankacılık Piyasası Paneli",
        "en": "Turkish Banking Market Dashboard",
    },
    "subtitle": {
        "tr": "Kredi piyasası · bankacılık sektörü haftalık kredi verileri",
        "en": "Credit market · weekly banking sector loans",
    },
    "language": {"tr": "Dil", "en": "Language"},
    "source": {"tr": "Kaynak", "en": "Source"},
    "series": {"tr": "Seriler", "en": "Series"},
    "date_range": {"tr": "Tarih aralığı", "en": "Date range"},
    "no_data": {
        "tr": "Henüz veri yok. Yüklemek için: `uv run tr-banking backfill --start 2014-01-03`",
        "en": "No data yet. Load it with `uv run tr-banking backfill --start 2014-01-03`.",
    },
    "select_series": {"tr": "En az bir seri seçin.", "en": "Select at least one series."},
    "pick_end": {
        "tr": "Aralık için bir bitiş tarihi seçin.",
        "en": "Pick an end date for the range.",
    },
    "status": {
        "tr": "Son veri: **{week}** haftası · Son veri çekimi: **{updated}** (TSİ)",
        "en": "Latest data: week ending **{week}** · Last data fetch: **{updated}** (TRT)",
    },
    "freshness_note": {
        "tr": (
            "Veriler salı ve cuma sabahları otomatik çekilir. Bu sayfa veriyi {loaded} (TSİ) "
            "itibarıyla gösteriyor; en geç saatte bir yenilenir."
        ),
        "en": (
            "Data is fetched automatically on Tuesday and Friday mornings. This page shows it "
            "as of {loaded} (TRT) and refreshes at least hourly."
        ),
    },
    "db_unavailable": {
        "tr": "Veritabanına şu anda ulaşılamıyor. Lütfen birkaç dakika sonra tekrar deneyin.",
        "en": "The database is not reachable right now. Please try again in a few minutes.",
    },
    "col_series": {"tr": "Seri", "en": "Series"},
    "col_date": {"tr": "Tarih", "en": "Date"},
    "col_last": {"tr": "Son değer", "en": "Last value"},
    "col_unit": {"tr": "Birim", "en": "Unit"},
    "col_wow": {"tr": "Haftalık %", "en": "Weekly %"},
    "col_yoy": {"tr": "Yıllık %", "en": "Yearly %"},
    "tooltip_date": {"tr": "Tarih", "en": "Week ending"},
    "inflation_note": {
        "tr": (
            "Değerler **nominal TL**'dir, enflasyondan arındırılmamıştır. Türkiye'de tüketici "
            "enflasyonu hâlâ yüksek olduğundan, buradaki artışın önemli bir kısmı reel kredi "
            "büyümesinden değil fiyat artışlarından kaynaklanır. Döviz cinsi krediler TL "
            "karşılıklarıyla dahildir; TL'deki değer kaybı da bu rakamları yukarı iter."
        ),
        "en": (
            "Values are **nominal TRY**, not adjusted for inflation. With consumer price "
            "inflation in Türkiye still high, much of the growth shown here reflects rising "
            "prices rather than real credit expansion. FX-denominated loans are included at "
            "their TRY value, so lira depreciation also pushes these figures up."
        ),
    },
    "comparison_note": {
        "tr": (
            "Haftalık % bir önceki haftayla, yıllık % 52 hafta önceki aynı haftayla karşılaştırır."
        ),
        "en": (
            "Weekly % compares with the previous week; yearly % with the same week "
            "52 weeks earlier."
        ),
    },
}

SOURCE_LABELS: dict[str, dict[Lang, str]] = {
    "evds": {"tr": "TCMB EVDS", "en": "CBRT EVDS"},
    "bddk": {"tr": "BDDK haftalık bülteni", "en": "BDDK weekly bulletin"},
}

SOURCE_NOTES: dict[str, dict[Lang, str]] = {
    "evds": {
        "tr": (
            "Kaynak: TCMB EVDS, veri grubu `bie_hpbitablo6` - Bankacılık Sektörü Seçilmiş Kredi "
            "Büyüklükleri (yurt içi şubeler), haftalık cuma değerleri, TL + YP, 28.06.2024'ten "
            "itibaren. Tüketici kredileri (toplam) bireysel kredi kartlarını da içerir."
        ),
        "en": (
            "Source: CBRT (TCMB) EVDS, data group `bie_hpbitablo6` - Banking Sector Selected "
            "Loans (domestic branches), weekly Friday values, TRY + FX, from 2024-06-28. "
            "Consumer loans (total) include individual credit cards."
        ),
    },
    "bddk": {
        "tr": (
            "Kaynak: BDDK Haftalık Bülten, *Krediler* tablosu, sektör toplamı, haftalık cuma "
            "değerleri, TL + YP, 03.01.2014'ten itibaren. Kapsamı EVDS'den biraz farklıdır "
            "(ortak seriler yaklaşık %0,3 içinde tutarlıdır); BDDK *Ticari ve diğer krediler* "
            "kalemi EVDS *Ticari krediler* kaleminden daha geniştir."
        ),
        "en": (
            "Source: BDDK weekly bulletin (Haftalık Bülten), table *Krediler*, whole sector, "
            "weekly Friday values, TRY + FX, from 2014-01-03. Coverage differs slightly from "
            "EVDS (common series agree within about 0.3%); BDDK *Commercial and other loans* "
            "is broader than EVDS *Commercial loans*."
        ),
    },
}

# Display unit labels produced by metrics.display_unit.
UNIT_LABELS: dict[str, dict[Lang, str]] = {
    "billion TRY": {"tr": "milyar TL", "en": "billion TRY"},
}

MONTHS: dict[Lang, list[str]] = {
    "tr": [
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    ],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

# Vega-Lite locale for Turkish charts (English uses Vega's default en-US locale).
VEGA_LOCALE_TR = {
    "number": {"decimal": ",", "thousands": ".", "grouping": [3], "currency": ["", " ₺"]},
    "time": {
        "dateTime": "%e %B %Y %A %X",
        "date": "%d.%m.%Y",
        "time": "%H:%M:%S",
        "periods": ["ÖÖ", "ÖS"],
        "days": ["Pazar", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi"],
        "shortDays": ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"],
        "months": MONTHS["tr"],
        "shortMonths": [
            "Oca",
            "Şub",
            "Mar",
            "Nis",
            "May",
            "Haz",
            "Tem",
            "Ağu",
            "Eyl",
            "Eki",
            "Kas",
            "Ara",
        ],
    },
}

MISSING = "–"


def text(key: str, lang: Lang) -> str:
    return TEXTS[key][lang]


def unit_label(unit: str, lang: Lang) -> str:
    return UNIT_LABELS.get(unit, {}).get(lang, unit)


def format_number(value: float, lang: Lang, decimals: int = 1) -> str:
    """18445.2 -> '18.445,2' (tr) or '18,445.2' (en)."""
    if value is None or math.isnan(value):
        return MISSING
    english = f"{value:,.{decimals}f}"
    if lang == "en":
        return english
    return english.replace(",", "_").replace(".", ",").replace("_", ".")


def format_pct(value: float, lang: Lang) -> str:
    """Signed, one decimal: -1.14 -> '-1,1%' (tr) / '-1.1%' (en); rounds-to-zero -> '0,0%'."""
    if value is None or math.isnan(value):
        return MISSING
    rounded = round(value, 1)
    if rounded == 0:
        rounded = 0.0  # round(-0.04, 1) is -0.0, which would print as "-0,0%"
    sign = "+" if rounded > 0 else ""
    return f"{sign}{format_number(rounded, lang)}%"


def change_direction(value: float) -> int:
    """1 / -1 / 0 for up / down / flat-or-missing, using the same rounding as format_pct."""
    if value is None or math.isnan(value):
        return 0
    rounded = round(value, 1)
    return (rounded > 0) - (rounded < 0)


def format_date(day: date | datetime, lang: Lang, long: bool = False) -> str:
    """Table dates: 18.09.2026 / 2026-09-18. Long form: 18 Eylül 2026 / 18 Sep 2026."""
    if long:
        return f"{day.day} {MONTHS[lang][day.month - 1]} {day.year}"
    return day.strftime("%d.%m.%Y" if lang == "tr" else "%Y-%m-%d")
