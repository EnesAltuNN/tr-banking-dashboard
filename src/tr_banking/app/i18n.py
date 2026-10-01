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
    "summary_title": {"tr": "Haftanın özeti", "en": "This week in brief"},
    "summary_caption": {
        "tr": (
            "{week} haftası verileri · Metni yapay zekâ ({model}) yazdı; rakamlar resmî "
            "verilerden Python ile hesaplandı ve metinle birlikte saklanır."
        ),
        "en": (
            "Data for the week ending {week} · Written by AI ({model}); the numbers were "
            "computed in Python from official data and are stored with the text."
        ),
    },
    "alerts_title": {
        "tr": "**Son haftada olağan dışı değişim**",
        "en": "**Unusual change in the latest week**",
    },
    "alerts_line": {
        "tr": "{name} ({source}), {week} haftası: **{change}**; son bir yılda tipik: {typical}",
        "en": "{name} ({source}), week ending {week}: **{change}**; typical over the past "
        "year: {typical}",
    },
    "alerts_none": {
        "tr": (
            "Son haftada olağan dışı bir değişim yok: her haftalık seri, son haftalık değişimi "
            "kendi son bir yılıyla karşılaştırılarak izlenir."
        ),
        "en": (
            "No unusual change in the latest week: every weekly series' latest change is "
            "compared with its own past year."
        ),
    },
    "alerts_method": {
        "tr": (
            "Değişim, serinin son 52 haftasının medyanından 5 ölçeklenmiş MAD'den fazla "
            "sapıyorsa işaretlenir (2014-2026'da haftaların yaklaşık %1,5'i)."
        ),
        "en": (
            "A change is flagged when it is more than 5 scaled MADs away from the median of the "
            "series' last 52 weeks (about 1.5% of weeks in 2014-2026)."
        ),
    },
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
    "tab_banking": {"tr": "Sektör", "en": "Banking sector"},
    "banking_unavailable": {"tr": "Henüz sektör verisi yok.", "en": "No banking sector data yet."},
    "ratios": {"tr": "Oranlar", "en": "Ratios"},
    "col_topic": {"tr": "Konu", "en": "Topic"},
    "topic_asset_quality": {"tr": "Aktif kalitesi", "en": "Asset quality"},
    "topic_deposits": {"tr": "Mevduat", "en": "Deposits"},
    "topic_capital": {"tr": "Sermaye", "en": "Capital"},
    "topic_funding": {"tr": "Fonlama", "en": "Funding"},
    "topic_securities": {"tr": "Menkul değerler", "en": "Securities"},
    "topic_groups": {"tr": "Banka grupları", "en": "Bank groups"},
    "group_shares_loans": {
        "tr": "Kredilerde banka grubu payları",
        "en": "Bank-group shares of loans",
    },
    "group_shares_deposits": {
        "tr": "Mevduatta banka grubu payları",
        "en": "Bank-group shares of deposits",
    },
    "group_state_banks": {"tr": "Kamu", "en": "State"},
    "group_private_banks": {"tr": "Yerli özel", "en": "Domestic private"},
    "group_foreign_banks": {"tr": "Yabancı", "en": "Foreign"},
    "col_group": {"tr": "Banka grubu", "en": "Bank group"},
    "col_share": {"tr": "Pay (%)", "en": "Share (%)"},
    "period_changes": {"tr": "Dönem değişimleri (%)", "en": "Changes over periods (%)"},
    "col_1w": {"tr": "1 hafta", "en": "1 week"},
    "col_4w": {"tr": "4 hafta", "en": "4 weeks"},
    "col_13w": {"tr": "13 hafta", "en": "13 weeks"},
    "col_ytd": {"tr": "Yılbaşından beri", "en": "Year to date"},
    "col_52w": {"tr": "52 hafta", "en": "52 weeks"},
    "amounts": {"tr": "Tutarlar", "en": "Amounts"},
    "col_ratio": {"tr": "Oran", "en": "Ratio"},
    "ratio_npl": {"tr": "Takipteki alacak oranı", "en": "Non-performing loan ratio"},
    "ratio_npl_consumer": {
        "tr": "Takipteki oranı: tüketici kredileri ve kartlar",
        "en": "NPL ratio: consumer loans and cards",
    },
    "ratio_npl_commercial": {
        "tr": "Takipteki oranı: ticari ve diğer krediler",
        "en": "NPL ratio: commercial and other loans",
    },
    "ratio_fx_share": {"tr": "Mevduatta döviz payı", "en": "FX share of deposits"},
    "ratio_loan_deposit": {"tr": "Kredi/mevduat oranı", "en": "Loan-to-deposit ratio"},
    "ratio_loans_state_banks": {
        "tr": "Kredilerdeki pay: kamu bankaları",
        "en": "Share of loans: state banks",
    },
    "ratio_loans_private_banks": {
        "tr": "Kredilerdeki pay: yerli özel bankalar",
        "en": "Share of loans: domestic private banks",
    },
    "ratio_loans_foreign_banks": {
        "tr": "Kredilerdeki pay: yabancı bankalar",
        "en": "Share of loans: foreign banks",
    },
    "ratio_deposits_state_banks": {
        "tr": "Mevduattaki pay: kamu bankaları",
        "en": "Share of deposits: state banks",
    },
    "ratio_deposits_private_banks": {
        "tr": "Mevduattaki pay: yerli özel bankalar",
        "en": "Share of deposits: domestic private banks",
    },
    "ratio_deposits_foreign_banks": {
        "tr": "Mevduattaki pay: yabancı bankalar",
        "en": "Share of deposits: foreign banks",
    },
    "ratio_npl_state_banks": {
        "tr": "Takipteki oranı: kamu bankaları",
        "en": "NPL ratio: state banks",
    },
    "ratio_npl_private_banks": {
        "tr": "Takipteki oranı: yerli özel bankalar",
        "en": "NPL ratio: domestic private banks",
    },
    "ratio_npl_foreign_banks": {
        "tr": "Takipteki oranı: yabancı bankalar",
        "en": "NPL ratio: foreign banks",
    },
    "ratio_fx_share_state_banks": {
        "tr": "Mevduatta döviz payı: kamu bankaları",
        "en": "FX share of deposits: state banks",
    },
    "ratio_fx_share_private_banks": {
        "tr": "Mevduatta döviz payı: yerli özel bankalar",
        "en": "FX share of deposits: domestic private banks",
    },
    "ratio_fx_share_foreign_banks": {
        "tr": "Mevduatta döviz payı: yabancı bankalar",
        "en": "FX share of deposits: foreign banks",
    },
    "ratio_equity_loans": {"tr": "Yasal özkaynak / krediler", "en": "Own funds / loans"},
    "ratio_fx_position_equity": {
        "tr": "Döviz net genel pozisyonu / özkaynak",
        "en": "Net FX position / own funds",
    },
    "ratio_wholesale_funding": {
        "tr": "Mevduat dışı fonlama / mevduat",
        "en": "Non-deposit funding / deposits",
    },
    "ratio_reserves_deposits": {
        "tr": "Zorunlu karşılıklar / mevduat",
        "en": "Reserve requirements / deposits",
    },
    "ratio_bonds_securities": {
        "tr": "Menkul değerlerde devlet tahvili payı",
        "en": "Government bonds in securities",
    },
    "week_of": {"tr": "{week} haftası", "en": "week ending {week}"},
    "banking_note": {
        "tr": (
            "Kaynak: BDDK haftalık bülteni, *Mevduat*, *Takipteki Alacaklar*, *Krediler*, "
            "*Yabancı Para Pozisyonu*, *Diğer Bilanço Kalemleri* ve *Menkul Değerler* "
            "tabloları, haftalık cuma değerleri, TL + YP, 03.01.2014'ten itibaren. Mevduat "
            "katılım fonlarını içerir; döviz mevduatı YP sütununun TL karşılığıdır. **Takipteki "
            "alacak oranı = takipteki / (krediler + takipteki)**: BDDK'nın kredi tablosu yalnız "
            "donmamış kredileri içerir. Kredi/mevduat oranı ve banka grubu payları BDDK sektör "
            "toplamlarına göre hesaplanır; kamu + yerli özel + yabancı bankalar sektör toplamını "
            "tam olarak verir (katılım ve kalkınma bankaları bu gruplara dağılmıştır). Oranların "
            "değişimi yüzde puan (puan) cinsindendir. **Mevduat dışı fonlama** = TCMB'ye borçlar + "
            "repo + yurt dışı bankalara borçlar + ihraç edilen menkul kıymetler. Döviz net genel "
            "pozisyonunun yasal sınırı özkaynağın ±%20'sidir. Devlet tahvili payı, menkul "
            "değerler tablosundaki üç devlet tahvili satırının toplamıdır. Bir banka el "
            "değiştirdiğinde grubu da değişir; grup paylarındaki ani sıçramalar (ör. 2016) "
            "bundandır. Dönem değişimleri, son değeri 1, 4, 13 ve 52 hafta önceki değerle ve "
            "önceki yılın son haftasıyla karşılaştırır."
        ),
        "en": (
            "Source: BDDK weekly bulletin, tables *Mevduat* (deposits), *Takipteki Alacaklar* "
            "(non-performing loans), *Krediler* (loans), *Yabancı Para Pozisyonu* (FX "
            "position), *Diğer Bilanço Kalemleri* (other balance sheet items) and *Menkul "
            "Değerler* (securities), weekly Friday values, TRY + FX, from "
            "2014-01-03. Deposits include participation funds; FX deposits are the FX column at "
            "its TRY value. **NPL ratio = NPL / (loans + NPL)**: BDDK's loan table holds "
            "performing loans only. The loan-to-deposit ratio and the bank-group shares use "
            "BDDK's sector totals; state + domestic private + foreign banks add up to the sector "
            "exactly (participation and development banks are spread over these groups). "
            "Changes of ratios are in percentage points (pp). **Non-deposit funding** = due to the "
            "CBRT + repo + due to foreign banks + securities issued. The legal limit of the net "
            "FX position is ±20% of own funds. The bond share sums the three government bond "
            "rows of the securities table. When a bank changes hands it moves to another group, "
            "which causes the sudden jumps in group shares (e.g. 2016). The period changes "
            "compare the latest value with 1, 4, 13 and 52 weeks earlier and with the last week "
            "of the previous year."
        ),
    },
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
    "mode_nominal_short": {"tr": "Nominal", "en": "Nominal"},
    "mode_real_short": {"tr": "Reel", "en": "Real"},
    "series_all": {"tr": "tümü", "en": "all"},
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
    "tooltip_decision": {"tr": "Karar", "en": "Decision"},
    "decision_hike": {"tr": "Artırım", "en": "Hike"},
    "decision_cut": {"tr": "İndirim", "en": "Cut"},
    "decision_hold": {"tr": "Sabit", "en": "Hold"},
    "rates_unavailable": {"tr": "Henüz faiz verisi yok.", "en": "No interest rate data yet."},
    "rates_note": {
        "tr": (
            "Kaynak: TCMB EVDS. Kredi faizleri `bie_kt100h` veri grubundan: bankaların o hafta "
            "verdiği yeni TL kredilere uyguladığı ağırlıklı ortalama faiz (akım), haftalık cuma "
            "değerleri. İhtiyaç ve ticari kredi faizleri KMH ve kurumsal kredi kartlarını "
            "içermez. Politika faizi, TCMB'nin bir hafta vadeli repo faizidir (iş günü verisi); "
            "EVDS'deki serisi 14.09.2018'de başlar, daha önceki yıllarda karar işareti yoktur. "
            "Grafiklerin altındaki kısa çizgiler PPK toplantılarıdır: uzun ve koyu olanlar "
            "politika faizini değiştiren, kısa ve soluk olanlar faizi sabit tutan kararlar "
            "(toplantı takvimi TCMB duyurularından). Gri kesikli çizgi TÜFE'den hesaplanan yıllık "
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
            "markers. Short ticks along the bottom are MPC meetings: long, dark ticks changed the "
            "policy rate, short, faint ones kept it unchanged (meeting calendar from CBRT press "
            "releases). The dashed grey line is yearly "
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
# Shorter labels for the KPI tiles, where "18,445.2 billion TRY" does not fit a quarter width.
SHORT_UNIT_LABELS: dict[str, dict[Lang, str]] = {
    "billion TRY": {"tr": "milyar TL", "en": "bn TRY"},
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


def unit_label(unit: str, lang: Lang, short: bool = False) -> str:
    labels = SHORT_UNIT_LABELS if short and unit in SHORT_UNIT_LABELS else UNIT_LABELS
    return labels.get(unit, {}).get(lang, unit)


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
