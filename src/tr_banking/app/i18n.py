"""Dashboard texts and number/date formatting in Turkish and English (pure, no Streamlit)."""

import math
from datetime import date, datetime
from typing import Literal

import pandas as pd

Lang = Literal["tr", "en"]
LANGUAGES: dict[Lang, str] = {"tr": "TR", "en": "EN"}

TEXTS: dict[str, dict[Lang, str]] = {
    "title": {
        "tr": "Türkiye Bankacılık Piyasası Paneli",
        "en": "Turkish Banking Market Dashboard",
    },
    "tagline": {
        "tr": (
            "Türkiye bankacılık piyasasının kredi, faiz ve kart harcaması verileri; resmî "
            "kaynaklardan otomatik güncellenir, nominal ve enflasyondan arındırılmış olarak."
        ),
        "en": (
            "Loans, interest rates and card spending in Türkiye's banking market, updated "
            "automatically from official sources, in nominal and inflation-adjusted terms."
        ),
    },
    "info_line": {
        "tr": (
            "Son veri çekimi: **{updated}** (TSİ) · Veriler salı ve cuma sabahları otomatik "
            "çekilir. Bu sayfa veriyi {loaded} (TSİ) itibarıyla gösteriyor; en geç saatte bir "
            "yenilenir."
        ),
        "en": (
            "Last data fetch: **{updated}** (TRT) · Data is fetched automatically on Tuesday and "
            "Friday mornings. This page shows it as of {loaded} (TRT) and refreshes at least "
            "hourly."
        ),
    },
    "filters": {"tr": "Filtreler", "en": "Filters"},
    "latest_week": {
        "tr": "Son veri: **{week}** haftası",
        "en": "Latest data: week ending **{week}**",
    },
    "chart_source": {"tr": "Kaynak: {source}", "en": "Source: {source}"},
    "notes": {"tr": "Notlar ve yöntem", "en": "Notes and method"},
    "footer_sources": {
        "tr": (
            "**Kaynaklar:** [TCMB EVDS](https://evds3.tcmb.gov.tr/) · "
            "[BDDK haftalık bülteni](https://www.bddk.org.tr/BultenHaftalik) · "
            "[BKM aylık istatistikler](https://bkm.com.tr/secilen-aya-ait-istatistikler/) · "
            "Kaynak kod: [GitHub](https://github.com/EnesAltuNN/tr-banking-dashboard)"
        ),
        "en": (
            "**Sources:** [CBRT EVDS](https://evds3.tcmb.gov.tr/) · "
            "[BDDK weekly bulletin](https://www.bddk.org.tr/BultenHaftalik) · "
            "[BKM monthly statistics](https://bkm.com.tr/secilen-aya-ait-istatistikler/) · "
            "Source code: [GitHub](https://github.com/EnesAltuNN/tr-banking-dashboard)"
        ),
    },
    "footer_values": {
        "tr": (
            "**Nominal ve reel:** Değerler yayımlandığı gibi nominal TL'dir. *Reel* görünüm TL "
            "tutarlarını TÜFE ile son TÜFE ayının fiyatlarına çevirir; faizlerde reel faiz, "
            "faiz eksi yıllık enflasyon olarak yaklaşık hesaplanır. Yeşil artışı, kırmızı "
            "düşüşü gösterir ve her zaman +/- işaretiyle birlikte gelir."
        ),
        "en": (
            "**Nominal and real:** values are nominal TRY, as published. The *real* view "
            "converts TRY amounts to the prices of the latest CPI month; for interest rates, "
            "the real rate is approximated as the rate minus yearly inflation. Green marks a "
            "rise and red a fall, always together with a +/- sign."
        ),
    },
    "pp": {"tr": "puan", "en": "pp"},
    "desc_yearly": {"tr": "yıllık", "en": "year on year"},
    "desc_monthly": {"tr": "önceki aya göre", "en": "vs. previous month"},
    "desc_real_short": {"tr": "reel, yıllık", "en": "real, year on year"},
    "desc_real": {
        "tr": "reel, yıllık · {month} fiyatlarıyla",
        "en": "real, year on year · {month} prices",
    },
    "kpi_real_growth": {"tr": "{name}, reel büyüme", "en": "{name}, real growth"},
    "kpi_inflation": {"tr": "Yıllık enflasyon (TÜFE)", "en": "Yearly inflation (CPI)"},
    "kpi_real_rate": {"tr": "{name}, reel ≈", "en": "{name}, real ≈"},
    "kpi_real_rate_desc": {
        "tr": "TÜFE'si açıklanmış son hafta: {week}",
        "en": "Latest week with published CPI: {week}",
    },
    "kpi_card_spending": {
        "tr": "Kartla alışveriş (kredi + banka kartı, yurt içi)",
        "en": "Card spending (credit + debit, domestic)",
    },
    "tab_credit": {"tr": "Krediler", "en": "Loans"},
    "tab_rates": {"tr": "Faizler", "en": "Interest rates"},
    "tab_cards": {"tr": "Kartlar", "en": "Cards"},
    "cards_unavailable": {"tr": "Henüz kart verisi yok.", "en": "No card data yet."},
    "latest_month": {"tr": "Son veri: **{month}**", "en": "Latest data: **{month}**"},
    "col_mom": {"tr": "Aylık %", "en": "Monthly %"},
    "col_month": {"tr": "Ay", "en": "Month"},
    "cards_note": {
        "tr": (
            "Kaynak: BKM (Bankalararası Kart Merkezi) aylık istatistikleri, Ocak 2017'den "
            "itibaren; veriler ay bittikten yaklaşık 1,5-2 ay sonra yayımlanır. Alışveriş "
            "tutarları Türkiye'de çıkarılmış kartların yurt içi kullanımıdır. Yabancı kart "
            "harcaması, yabancı kredi ve banka kartlarının Türkiye'deki alışverişlerinin "
            "toplamıdır. İnternetten kartlı ödemeler BKM'nin *Sanal POS işlemleri* tablosundan "
            "gelir. Aylık % bir önceki ayla, yıllık % 12 ay önceki aynı ayla karşılaştırır."
        ),
        "en": (
            "Source: BKM (Interbank Card Center) monthly statistics, from January 2017; data "
            "is published about 1.5 to 2 months after the month ends. Spending amounts are "
            "the domestic use of cards issued in Türkiye. Foreign card spending is the sum of "
            "foreign credit and debit card purchases in Türkiye. Online card payments come "
            "from BKM's *virtual POS transactions* table. Monthly % compares with the previous "
            "month; yearly % with the same month a year earlier."
        ),
    },
    "cards_real_note": {
        "tr": (
            "Tutarlar **{month} fiyatlarıyla** gösterilir (TÜFE, 2025=100): her ayın tutarı "
            "o ayın TÜFE'siyle düzeltilir. Kart sayıları para olmadığı için değişmez."
        ),
        "en": (
            "Amounts are shown in **{month} prices** (CPI, 2025=100): each month's amount is "
            "deflated by that month's CPI. Card counts are not money and stay unchanged."
        ),
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
    "value_mode": {"tr": "Değerler", "en": "Values"},
    "mode_nominal": {"tr": "Nominal", "en": "Nominal"},
    "mode_real": {"tr": "Reel (enflasyondan arındırılmış)", "en": "Real (inflation-adjusted)"},
    "real_unit": {"tr": "{unit}, {month} fiyatlarıyla", "en": "{unit}, {month} prices"},
    "real_note": {
        "tr": (
            "Reel değerler **{month} fiyatlarıyla** gösterilir (TÜFE, 2025=100). Haftalık stoklar "
            "içinde bulundukları ayın ortalama TÜFE'siyle düzeltilir. TÜFE'si henüz açıklanmamış "
            "haftalar gösterilmez: tablo, TÜFE'si olan son haftayı (**{week}**) gösterir. TÜFE "
            "aylık olduğu için haftalık % bu görünümde gizlidir."
        ),
        "en": (
            "Real values are shown in **{month} prices** (CPI, 2025=100). Weekly stocks are "
            "deflated by the average CPI of their month. Weeks whose CPI is not published yet "
            "are left out: the table shows the last week with CPI (**{week}**). Weekly % is "
            "hidden in this view because CPI is monthly."
        ),
    },
    "real_unavailable": {
        "tr": "Reel görünüm için TÜFE verisi henüz yok; nominal değerler gösteriliyor.",
        "en": "No CPI data for the real view yet; showing nominal values.",
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
    "col_wow_pp": {"tr": "Haftalık değişim (puan)", "en": "Weekly change (pp)"},
    "col_yoy_pp": {"tr": "Yıllık değişim (puan)", "en": "Yearly change (pp)"},
    "col_real_rate": {"tr": "Reel faiz ≈ (puan)", "en": "Real rate ≈ (pp)"},
    "inflation_line": {"tr": "Yıllık enflasyon (TÜFE)", "en": "Yearly inflation (CPI)"},
    "policy_change": {"tr": "PPK kararı", "en": "MPC decision"},
    "tooltip_previous": {"tr": "Önceki (%)", "en": "Before (%)"},
    "tooltip_new": {"tr": "Yeni (%)", "en": "After (%)"},
    "rates_unavailable": {"tr": "Henüz faiz verisi yok.", "en": "No interest rate data yet."},
    "rates_note": {
        "tr": (
            "Kaynak: TCMB EVDS. Kredi faizleri `bie_kt100h` veri grubundan: bankaların o hafta "
            "verdiği yeni TL kredilere uyguladığı ağırlıklı ortalama faiz (akım), haftalık cuma "
            "değerleri. İhtiyaç ve ticari kredi faizleri KMH ve kurumsal kredi kartlarını "
            "içermez. Politika faizi, TCMB'nin bir hafta vadeli repo faizidir (iş günü verisi); "
            "EVDS'deki serisi 14.09.2018'de başlar, daha önceki yıllarda karar işareti yoktur. "
            "Dikey kesikli çizgiler politika faizini değiştiren PPK kararlarıdır; faizi sabit "
            "tutan kararlar işaretlenmez. Gri kesikli çizgi TÜFE'den hesaplanan yıllık "
            "enflasyondur. Değişimler yüzde puan (puan) cinsindendir: haftalık bir hafta, "
            "yıllık 52 hafta önceki değerle karşılaştırır. **Reel faiz ≈ faiz − yıllık "
            "enflasyon**: basit farktır, Fisher denklemi değildir; yalnız kredi faizleri için "
            "hesaplanır ve TÜFE'si henüz açıklanmamış aylarda boş kalır."
        ),
        "en": (
            "Source: CBRT (TCMB) EVDS. Loan rates come from data group `bie_kt100h`: the "
            "weighted average rate banks applied to new TRY loans that week (flow data), weekly "
            "Friday values. General purpose and commercial loan rates exclude overdrafts and "
            "corporate credit cards. The policy rate is the CBRT one-week repo rate (business "
            "days); its EVDS series starts on 2018-09-14, so earlier years have no decision "
            "markers. Dashed vertical lines mark MPC decisions that changed the policy rate; "
            "decisions that kept it unchanged are not marked. The dashed grey line is yearly "
            "inflation computed from CPI. Changes are in percentage points (pp): weekly compares "
            "with a week earlier, yearly with 52 weeks earlier. **Real rate ≈ rate − yearly "
            "inflation**: a simple difference, not the Fisher equation; shown for loan rates "
            "only and left empty for months whose CPI is not published yet."
        ),
    },
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
    "bkm": {"tr": "BKM aylık istatistikler", "en": "BKM monthly statistics"},
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
    "million cards": {"tr": "milyon adet", "en": "million cards"},
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
    signed = format_signed(value, lang)
    return signed if signed == MISSING else f"{signed}%"


def format_signed(value: float, lang: Lang, decimals: int = 1) -> str:
    """Signed number: 1.254 -> '+1,3' (tr) / '+1.3' (en); used for pp changes too."""
    if value is None or math.isnan(value):
        return MISSING
    rounded = round(value, decimals)
    if rounded == 0:
        rounded = 0.0  # round(-0.04, 1) is -0.0, which would print as "-0,0"
    sign = "+" if rounded > 0 else ""
    return f"{sign}{format_number(rounded, lang, decimals)}"


def change_direction(value: float, decimals: int = 1) -> int:
    """1 / -1 / 0 for up / down / flat-or-missing, using the same rounding as the display."""
    if value is None or math.isnan(value):
        return 0
    rounded = round(float(value), decimals)  # float(): numpy booleans cannot be subtracted
    return (rounded > 0) - (rounded < 0)


def format_month(month: date | datetime | pd.Period, lang: Lang) -> str:
    """Ağustos 2026 / Aug 2026."""
    return f"{MONTHS[lang][month.month - 1]} {month.year}"


def format_date(day: date | datetime, lang: Lang, long: bool = False) -> str:
    """Table dates: 18.09.2026 / 2026-09-18. Long form: 18 Eylül 2026 / 18 Sep 2026."""
    if long:
        return f"{day.day} {MONTHS[lang][day.month - 1]} {day.year}"
    return day.strftime("%d.%m.%Y" if lang == "tr" else "%Y-%m-%d")
