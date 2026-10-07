# Data sources

Detailed, verified facts about each source; read this before touching a source client. Update the "verified" dates when you re-check a fact.

| Source | Series | Unit (stored) | History from | Access |
|---|---|---|---|---|
| [TCMB EVDS3](#tcmb-evds3-verified-2026-09-24) | 6 weekly loan series | thousand TRY | 2024-06-28 | official API, free key |
| [TCMB EVDS3, CPI](#consumer-price-index-verified-2026-09-27) | 1 monthly price index (deflator) | index, 2025=100 | 2005-01 | official API, free key |
| [TCMB EVDS3, rates](#interest-rates-verified-2026-09-27) | 4 weekly loan rates, the daily policy rate and the daily funding cost | % | 2014-01-03 (policy rate 2018-09-14) | official API, free key |
| [BDDK weekly bulletin](#bddk-weekly-bulletin-verified-2026-09-24) | 7 weekly loan series; 13 deposit, NPL and bank-group series | million TRY | 2014-01-03 | public website, no API |
| [BKM](#bkm-card-statistics-verified-2026-09-27) | 4 monthly card spending series, 2 card counts | million TRY, cards | 2017-01 | public website (HTML), no API |

Series codes, names and units are configured in [`config/series.yaml`](../config/series.yaml).
Values are stored exactly as published; the dashboard converts TRY units to billion TRY and
card counts to million cards.
Rates stay in % and their changes are shown in percentage points.

**Cross-source notes:**
- **EVDS and BDDK agree closely:** within about 0.3% on common series (checked 2026-09-24).
  - Their bank coverage differs slightly.
  - BDDK *commercial and other loans* is broader than EVDS *commercial loans*.
- **Values are stored nominal.** Inflation, and the TRY value of FX loans, drive much of the
  growth. The dashboard can also show real values, deflated by CPI (see
  [Consumer price index](#consumer-price-index-verified-2026-09-27)).
- **Consumer loans (total) include individual credit cards in both sources.**
  - EVDS `TP.HPBITABLO6.2` (1.1 Tüketici Kredileri) is the sum of housing, auto, general
    purpose *and* individual credit cards (1.1.1 to 1.1.4). The four rows add up to the total
    exactly, so do not add credit cards on top of it.
  - BDDK's matching row is `1.0.2`. BDDK row 1.0.3 (*Tüketici Kredileri*) excludes cards.

## TCMB EVDS3 (verified 2026-09-24)

Data group `bie_hpbitablo6`: *Bankacılık Sektörü Seçilmiş Kredi Büyüklükleri (Yurt İçi
Şubeler)* (Banking Sector Selected Loans, Domestic Branches). Values are weekly (Friday), in
thousand TRY, with TRY and FX loans combined.

| Code | Series |
|---|---|
| `TP.HPBITABLO6.2` | Consumer loans (total, incl. individual credit cards) |
| `TP.HPBITABLO6.3` | Housing loans |
| `TP.HPBITABLO6.7` | Auto loans |
| `TP.HPBITABLO6.11` | General purpose loans (ihtiyaç) |
| `TP.HPBITABLO6.16` | Individual credit cards |
| `TP.HPBITABLO6.20` | Commercial loans |

**Getting a key:**
1. Create an account at [evds3.tcmb.gov.tr](https://evds3.tcmb.gov.tr/) and log in.
2. Generate an API key from your profile page.
3. Put it in `.env` as `EVDS_API_KEY=...`.

The key is sent only as an HTTP header, never in the URL, and it is never logged.

**API facts:**
- Base URL `https://evds3.tcmb.gov.tr/igmevdsms-dis/`.
- The EVDS2 service URL now 302-redirects to EVDS3. The client never follows redirects, so the
  key header can't leak to another host.
- The API key goes in the `key` HTTP header; a request without it gets 403.
- Parameters are appended to the path **without `?`**:
  - Data: `series=A-B&startDate=DD-MM-YYYY&endDate=DD-MM-YYYY&type=json`.
  - Metadata: `categories/type=json`, `datagroups/mode=0&type=json`,
    `serieList/type=json&code=<datagroup>`.
- Response format:
  - Shape: `{"totalCount", "items": [{"Tarih": "DD-MM-YYYY", "YEARWEEK", "<CODE_WITH_UNDERSCORES>": "123.00000000" | null, "UNIXTIME"}]}`.
  - Values are strings. Weeks outside a series' range are null.
- **History starts 2024-06-28.** The older weekly loan groups `bie_kredi` / `bie_tukkre` were
  archived on 2025-01-31 and use a different methodology. They are not stitched to the current
  series, because joining them would hide a break in the data.
- New weekly data appears on Thursdays around 14:30 Istanbul time, for the week ending the
  previous Friday.
- Never guess series codes: discover them through the metadata endpoints and show the official
  name, frequency and unit before using them.
- **Monthly series** (checked 2026-09-27):
  - Dates come as `YYYY-M` (`"2026-1"`, `"2026-12"`). They are stored as the month's last day.
  - A monthly request must start on the **1st of a month**. A mid-month `startDate` returns an
    empty `items` list, so the pipeline aligns monthly windows to the 1st, 3 months back.
- **Never mix frequencies in one request.** A request with weekly and monthly codes silently
  returns every series converted to monthly (averaged). The pipeline sends one request per
  frequency.
- **A response holds at most 1000 items** (checked 2026-09-27). A longer range silently returns
  only the newest 1000 rows, and `totalCount` then also says 1000. For example, a daily series
  requested from 2014 came back from 2022-11-28 only. The client therefore:
  - splits every request into 2-year windows (about 520 business days, 105 weeks, 24 months);
  - fails on a response with 1000 items instead of storing a cut series;
  - accepts a window in which a series has no values, but fails if it has none in the whole
    range.
- **A period that touches two windows comes back from both.** A window ending on Saturday
  2016-01-02 and the next one starting on Sunday 2016-01-03 both return the week ending Friday
  2016-01-01. The client keeps one copy and fails if the two copies differ.

## Consumer price index (verified 2026-09-27)

Used only to compute real (inflation-adjusted) values; stored like any other series with
`module: macro` and `deflator: true` in `config/series.yaml`.

| Code | Series | Data group | Unit | From |
|---|---|---|---|---|
| `TP.TUKFIY2025.GENEL` | Tüketici Fiyat Endeksi, Genel Endeks (TÜİK) | `bie_tukfiy2025` | index, 2025=100 | 2005-01 |

- Monthly, published by TÜİK around the 3rd of the next month (freshness limit 45 days from the
  month's end, via `max_age_days`).
- **Base change, January 2026.** TÜİK moved the CPI from 2003=100 (`TP.GENENDEKS.T1`) to
  2025=100 and **back-cast the new base to 2005**. Across 260 overlapping months, the two
  bases' month-over-month changes differ by at most 0.007 pp, including the transition month
  (January 2026: +4.84% in both). So the project uses only the 2025=100 series and does **no
  own chaining**. The old series lives only in a test fixture
  (`tests/fixtures/evds_cpi_bases_2025_2026.json`), which a test uses to prove this continuity.
- **How real values are computed** (`app/metrics.py: deflate`):
  - Real value = nominal value × CPI(reference month) / CPI(month of the value).
  - The reference month is the latest month with CPI, so real values read as "in August 2026
    prices" and the latest real value is close to today's nominal one.
  - Weeks whose month has no CPI yet are left out of the real view; the table then shows the
    last week that has CPI, and says so.
  - Weekly % is hidden in the real view: every week of a month uses the same CPI, so a weekly
    real change would show a jump at each month boundary and none inside the month.
- **Timing mismatch.** Weekly loan stocks are point-in-time (Friday) values, while CPI is a
  monthly *average* price level. Deflating a stock by its month's average CPI is the usual
  approximation, but it is off by up to half a month of inflation (roughly 1% at 2% a month).
  Yearly real changes are hardly affected; do not read week-to-week real changes into it.
- **Guard against mixed bases.** `check_price_index` fails loudly if the index moves more than
  20% in one month. A base mix-up gives a jump of tens of percent (2003=100 is about 3,500,
  2025=100 about 110), while real monthly changes since 2005 stay well below 20%.
- **If TÜİK changes the base again** and does *not* back-cast the new series:
  1. Keep the old-base series and add the new one as a second series in `series.yaml`
     (only one of them may have `deflator: true`).
  2. Pick a link month present in both (ideally the last month of the old base).
  3. Chain: `chained(t) = old(t) × new(link) / old(link)` for months up to the link month, and
     `new(t)` after it. Do this in one pure function next to `deflate`, with a test like the
     existing continuity test (new vs chained month-over-month changes equal across the link).
  4. Store the raw published values only; chaining happens at display time, so a later
     back-cast by TÜİK just replaces the input.

## Interest rates (verified 2026-09-27)

Stored with `module: rates` and unit `%`. The dashboard's *Interest rates* tab shows levels in %,
weekly and yearly changes in percentage points, and no real or billion-TRY conversion.

**Loan rates:** data group `bie_kt100h`, *Kredi Faiz Oranları (Akım)* (Interest Rates on Loans,
Flow Data). These are the weighted average rates banks applied to **new** TRY loans opened that
week, not the rate on the whole loan stock. Weekly (Friday), published with the weekly credit
data.

| Code | Official name | From |
|---|---|---|
| `TP.KTF10` | İhtiyaç Kredisi (TL, Akım, %) | 2002-01-04 |
| `TP.KTF11` | Taşıt Kredisi (TL, Akım, %) | 2002-01-04 |
| `TP.KTF12` | Konut Kredisi (TL, Akım, %) | 2002-01-04 |
| `TP.KTF18` | Ticari Krediler (Tüzel Kişi KMH ve Kurumsal Kredi Kartları Hariç) (TL, Akım, %) | 2012-07-27 |

- The personal and commercial rates both exclude overdrafts (KMH) and corporate credit cards,
  whose rates sit near the legal maximum. The variants that include them are `TP.KTF101`
  (personal) and `TP.KTF17` (commercial, about 3 pp higher in September 2026).
- EUR/USD commercial rates (`TP.KTF17.EUR`, `TP.KTF17.USD`) exist but are not used.

**Policy rate:** `TP.PY.P02.1H` in data group `bie_pyintbnk`, *(1H) TCMB Kotasyonları SATIŞ
(%) (1 Haftalık İşlem)*: the CBRT one-week repo lending rate, on business days.
- Its change dates are the MPC (PPK) meeting days, e.g. 2023-06-22 → 15%, 2024-03-21 → 50%,
  2024-12-26 → 47.5%, 2025-04-17 → 46%, 2026-01-22 → 37%.
- The metadata says the series starts in 1996, but **the first value is 2018-09-14** (24%, the
  day after the 13 September 2018 decision). All earlier days since at least 2014 are null, so
  the dashboard has no policy rate and no decision markers before then.
- Holidays are null (e.g. 2025-01-01) and are skipped. Long holidays can last 9 days, so the
  series has `max_age_days: 14`.
- The monthly BIS series `TP.BISPOLFAIZ.TUR` has the same values but lags about two months
  and has no decision dates, so it is not used.
- **2016–2018: the one-week repo rate was not the effective funding cost.** In that period
  the CBRT funded banks mostly through other facilities, and its weighted average funding
  cost (`TP.APIFON4`, data group `bie_apifon`) was at times well above the one-week repo
  rate. In 2017 it was about 12% while the one-week repo rate stayed at 8%. So even where a
  one-week repo value exists, it does not show that period's actual funding cost. The
  series used here has no values before 2018-09-14 anyway.

**Funding cost:** `TP.APIFON4` in data group `bie_apifon`, the CBRT's weighted average cost of
funding (AOFM), on business days (verified 2026-10-01).
- It runs from **2014-01-02**, so it covers the years the one-week repo series does not, and
  it is what the 2016-2018 note above calls for: in 2017 it ranged 8.28-12.75% while the
  one-week repo rate stayed at 8%.
- Since 2018 the two agree: both were 37.00% on 2026-09-30, and both 50.00% -> 47.50% over the
  test fixture's window (`evds_funding_cost_2024_2025.json`, recorded 2026-10-01).
- It is charted as a plain line, not a step: unlike a policy rate it moves between meetings.
  It shares the `policy` category and so gets no real-rate column, which is for loan rates.
- This is why the dashboard does **not** splice a pre-2018 policy rate from another source:
  the two measures are different things and are shown as two series.

**Deposit rate** (verified 2026-10-01): `TP.TRY.MT06` in data group `bie_mt100h`, *Mevduat
Faiz Oranları (Akım)*, "Toplam (TL Mevduat, Akım, %)": the weighted average rate on new TRY
deposits that week, all maturities, weekly (Friday) from 2002-01-04. It is the deposit side
of `bie_kt100h`. 43.56% on 2026-09-18.
- The maturities `TP.TRY.MT01`-`MT05` (up to 1, 3, 6, 12 months, and 1 year and longer) are
  loaded too (category `deposit_term`, `alerts: false`) and drawn as one chart: the latest
  week as bars, 52 weeks earlier as ticks. On 2026-09-25 short maturities paid about 43%
  and 1 year and longer 31%. The same group also has USD and EUR deposits, and savings
  (`TAS`) and commercial (`TIC`) splits from 2012-07-06; not used.
- **Spread** (`app/metrics.py: rate_spread`) = `TP.KTF18` commercial loan rate − deposit rate:
  6.2 pp on 2026-09-18, between −17.8 and +15.9 pp since 2014. There is no single rate for
  all loans; commercial loans are the largest book.
- `alerts: false`: the weekly flow rate flagged 3.8% of weeks.

**USD/TRY** (verified 2026-10-01): `TP.DK.USD.A.YTL` in data group `bie_dkdovytl`, *Döviz
Kurları*, the CBRT indicative USD buying rate (Döviz Alış), business days from 1950
(weekend dates come back null). Module `macro`, no tab. It turns FX deposits into USD
(`app/metrics.py: in_usd`, the latest rate within 6 days up to each Friday): about 259
billion USD on 2026-09-18, up from 231 a year earlier, while the TRY value rose 32%.
Euro and gold are converted at the dollar rate too, so the USD figure is approximate.

**FX-protected deposits (KKM), not loaded** (checked 2026-10-04): data group `bie_kkm`,
monthly from 2021-12: `TP.KKM.K1` converted from FX (billion USD; 86.6 at the end of 2023),
`TP.KKM.K2`/`K3` its individual/corporate split, `TP.KKM.K4` TRY KKM (billion TRY; 487.3 at
the end of 2022). TRY KKM was 0 from 2024-12 and the FX-converted part 0 from 2026; a dead
series would only add a freshness failure once the CBRT stops updating it, so the banking
note mentions it instead.

**MPC calendar and decision markers** (verified 2026-09-29):
- The meeting dates live in [`config/mpc_meetings.yaml`](../config/mpc_meetings.yaml), from
  the first meeting inside the series (2018-10-25). Past dates come from the CBRT's decision
  press releases (tcmb.gov.tr > PPK > PPK Toplantı Kararları; each release's own page,
  `.../duyurular/basin/<year>/duy<year>-<no>`, has the date and the decision). Announced
  dates come from the calendar in "2026 Para Politikası" (Ek 2), which also lists the first
  four meetings of 2027.
- The decisions list page is incomplete: it misses the 26 December 2024 cut (release
  2024-70) and the 10 September 2026 hold (2026-38); both were found on the press release
  pages.
- The EVDS series changes **on the meeting day itself**: all 40 changes from 2018-09-14 to
  2026-09-25 fall exactly on a listed meeting. `app/metrics.py: policy_decisions` therefore
  compares the rate on the meeting day with the last rate before it (hike, cut or hold).
  Checked against the wording of every release: 85 meetings, 14 hikes, 26 cuts, 45 holds.
- The 20 March 2025 interim meeting raised only the overnight lending rate; the policy rate
  stayed at 42.5%, so it is a hold here.
- A rate change on a day that is not listed (an unscheduled meeting, or a calendar not yet
  updated) is still marked, so an out-of-date file only loses hold markers.
- The CBRT publishes the next year's calendar in December ("2027 Para Politikası" on
  2026-12-18); add it to the YAML then.

**Yearly inflation and real rates:**
- Yearly inflation is `CPI(month) / CPI(same month a year earlier) − 1`, from
  `TP.TUKFIY2025.GENEL`. It matches TÜİK's published figures, e.g. June 2024 71.60%, July
  2024 61.78%, December 2024 44.38%.
- The rate charts draw it as a dashed reference line on the same % axis (one axis, never a
  dual axis).
- The table's *real rate ≈ rate − yearly inflation* is a **simple difference, not the Fisher
  equation** `(1 + i) / (1 + π) − 1`. At these levels the two differ by several points, e.g.
  63% and 33% give 30 pp vs 22.6%.
- The real rate is shown for loan rates only, using the inflation of the rate's own month. It
  stays empty while that month's CPI is not published yet, which is most of the month for the
  latest week.

## BDDK weekly bulletin (verified 2026-09-24)

[bddk.org.tr/BultenHaftalik](https://www.bddk.org.tr/BultenHaftalik), table *Krediler*, whole
sector. Values are weekly (Friday), in million TRY, TRY + FX, from 2014-01-03.

| Code | Series |
|---|---|
| `1.0.1:10001:TRY:3` | Total loans |
| `1.0.2:10001:TRY:3` | Consumer loans (total, incl. credit cards) |
| `1.0.4:10001:TRY:3` | Housing loans |
| `1.0.5:10001:TRY:3` | Auto loans |
| `1.0.6:10001:TRY:3` | General purpose loans |
| `1.0.8:10001:TRY:3` | Individual credit cards |
| `1.0.12:10001:TRY:3` | Commercial and other loans |

- **Codes:** series codes in config are `<row>:<group>:<currency>:<column>`.
  - Bank groups: 10001 sector, 10002 deposit, 10003 development & investment, 10004
    participation, 10005 state, 10006 foreign, 10007 domestic private.
  - State + foreign + domestic private = sector (checked).
  - Bank-group breakdowns are config-only additions: change the `10001` group code.
  - Rows come from the bulletin table *Krediler* (tabloId 1 on the page).
- **No official API.** The client uses the endpoint behind the "advanced" page's charts,
  **one request per series, 1 s apart** (it is a public website, not an API).
  - `POST https://www.bddk.org.tr/BultenHaftalik/tr/Gelismis/KiyaslamaJsonGetir`
  - Form fields: `dil=tr`, `baslangicTarihi`/`bitisTarihi` (`DD.MM.YYYY`), `id` (row, e.g.
    `1.0.4`), `parabirimi` (`TRY`|`USD`), `sutun` (1 TL, 2 FX, 3 total), `tarafKodu` (bank group).
  - No token or cookie is needed.
  - Response: `{"Baslik", "XEkseni": ["D.MM.YYYY"...], "YEkseni": [numbers]}`.
  - One request returns the full range.
  - The home-page variant `tr/Home/KiyaslamaJsonGetir` caps results at 13 weeks; don't use it.
- **An unknown row or bank group returns HTTP 200 with empty lists**, so the parser treats an
  empty series as an error.
- **A week can be listed twice.** Row `2.0.9` returns 18.12.2020 and 5.02.2021 twice, with the
  same value (seen 2026-09-29). The parser keeps one copy; two different values for one week
  stay an error.

### Deposits, non-performing loans and bank groups (verified 2026-09-29)

Module `banking`, the dashboard's *Sektör* (*Banking sector*) tab. Same endpoint, code format
and unit as the loans above; full history from 2014-01-03 (664 weeks on 2026-09-18).

**Finding row ids.** The bulletin page switches tables through a form: `TabloDegistir('289')`
posts `tabloId` (with the page's anti-forgery token) to `/BultenHaftalik/`. Table ids on the
page are 289 *Krediler*, 290 *Takipteki Alacaklar*, 291 *Menkul Değerler*, 292 *Mevduat*,
293-297 other balance sheet tables. Each value cell calls `ShowModalGraph('<row>', ...)`, which
is where the row ids below come from. The row prefix follows the table: `1.` loans, `2.` NPL,
`4.` deposits.

| Code | Table / row | Series | 2026-09-18 |
|---|---|---|---|
| `4.0.1:10001:TRY:3` | Mevduat 1, total | Total deposits (incl. participation funds) | 32,553,270 |
| `4.0.1:10001:TRY:2` | Mevduat 1, YP column | FX deposits, at their TRY value | 12,584,999 |
| `4.0.2:10001:TRY:3` | Mevduat 2 | Deposits of individuals (Gerçek Kişiler) | 18,247,553 |
| `4.0.5:10001:TRY:3` | Mevduat 5 | Deposits of companies (Ticari Kuruluşlar) | 11,649,094 |
| `2.0.1:10001:TRY:3` | Takipteki 1 | Non-performing loans | 876,498 |
| `2.0.9:10001:TRY:3` | Takipteki 2 | NPL: consumer loans and individual cards | 369,835 |
| `2.0.5:10001:TRY:3` | Takipteki 8 | NPL: commercial and other loans | 506,664 |
| `1.0.1:{10005,10007,10006}:TRY:3` | Krediler 1 | Total loans of state / domestic private / foreign banks | 13,209,240 / 8,030,389 / 6,998,060 |
| `4.0.1:{10005,10007,10006}:TRY:3` | Mevduat 1 | Total deposits of the same groups | 15,275,970 / 9,033,830 / 8,243,471 |
| `2.0.1:{10005,10007,10006}:TRY:3` | Takipteki 1 | NPL of the same groups | 324,873 / 294,555 / 257,071 |
| `4.0.1:{10005,10007,10006}:TRY:2` | Mevduat 1, YP | FX deposits of the same groups | 5,524,920 / 3,606,521 / 3,453,558 |
| `9.0.18:10001:TRY:3` | YP Pozisyonu 8 | Regulatory capital (Yasal Özkaynak) | 6,056,344 |
| `9.0.17:10001:TRY:3` | YP Pozisyonu 7 | Net FX position, on + off balance sheet | 6,854 |
| `5.0.8:10001:TRY:3` | Diğer Bilanço 8 | Due to the CBRT | 878,758 |
| `5.0.16:10001:TRY:3` | Diğer Bilanço 13 | Funds from repo transactions | 3,334,973 |
| `5.0.11:10001:TRY:3` | Diğer Bilanço 12 | Due to foreign banks | 6,600,424 |
| `5.0.12:10001:TRY:3` | Diğer Bilanço 14 | Securities issued (net) | 2,158,790 |
| `5.0.4:10001:TRY:3` | Diğer Bilanço 4 | Reserve requirements | 4,365,021 |
| `3.0.1:10001:TRY:3` | Menkul 1 | Total securities | 8,045,613 |
| `3.0.{15,18,21}:10001:TRY:3` | Menkul 3, 6, 9 | Government bonds at FV through P&L / through OCI / amortised cost | 353,636 / 2,722,927 / 1,650,340 |

- **State + domestic private + foreign = sector, exactly**, for loans and deposits (checked on
  2026-09-18). Participation and development banks belong to these ownership groups.
- **New rows checked on 2026-10-01** against the live endpoint: all start on 2014-01-03, and
  the three groups' NPL and FX deposits add up to the sector rows.
- **Table 297 (FX position) has only a TOPLAM column**: column 1 returns 0, use column 3.
- **"Due to the CBRT" (`5.0.8`) leaves weeks out before 2018-09-28**: it sends a few weeks,
  all 0 (2015-05-29 to 2017-10-13), and nothing for the rest. The non-deposit funding sum
  counts those weeks as 0; from 2018-09-28 the row is complete.
- **Ratios are computed for display, never stored** (`app/metrics.py: ratio_pct`):
  - NPL ratio = NPL / (loans + NPL): 3.01% on 2026-09-18. BDDK's loan table holds performing
    loans only, so the denominator adds the NPL back, as BDDK's own reports do. Consumer and
    commercial NPL ratios use the matching loan rows (`1.0.2`, `1.0.12`).
  - FX share of deposits = `4.0.1` column 2 / column 3: 38.7%.
  - Loan-to-deposit ratio = `1.0.1` / `4.0.1`: 86.7%.
  - Bank-group shares of loans and deposits: group / sector total.
  - NPL ratio and FX share of deposits per bank group, as above with the group's rows.
  - Own funds / loans: 21.5%. Net FX position / own funds: 0.1% (legal limit ±20%).
  - Non-deposit funding / deposits: (due to the CBRT + repo + due to foreign banks +
    securities issued) / deposits: 39.9%. Reserve requirements / deposits: 13.4%.
  - Government bonds in securities: the three bond rows / `3.0.1`: 58.8%. **It starts on
    2022-09-16**: that week the three bond rows nearly doubled (e.g. `3.0.18` 366 -> 620
    billion TRY) while `3.0.1` moved from 2,088 to 2,113, a definition break rather than
    purchases. The share was about 33% before and 59% after; the bond rows themselves keep
    the break (they are detail series, hidden by default).
- **TLS:** `bddk.org.tr` sends only its leaf certificate; the intermediate "GlobalSign RSA OV
  SSL CA 2018" is missing.
  - Browsers download the missing intermediate themselves; Python on Linux does not.
  - `bddk_ssl_context()` trusts certifi's roots plus that public intermediate, bundled in
    `src/tr_banking/sources/certs/`. It works the same on Windows and in CI. Never use
    `verify=False`.
  - The leaf expires 2026-11-15. If the renewal changes the issuer, BDDK fetches fail with a
    certificate error. Download the new intermediate from the leaf's "CA Issuers" URL and
    replace the bundled file.

## BDDK monthly bulletin (verified 2026-10-03)

[Aylık Bülten](https://www.bddk.org.tr/BultenAylik): 17 tables (balance sheet, profit and
loss, loans, deposits by type and maturity, liquidity, capital adequacy, FX position,
ratios, ...), month-end values from 2003-01. Client: `sources/bddk_monthly.py`, source
`bddk_monthly`.

- **No official API.** The page loads each table from
  `POST /BultenAylik/tr/Home/BasitRaporGetir` with `tabloNo`, `yil`, `ay`, `paraBirimi=TL`
  and `taraf` (`10001` sector; also `10003` participation, `10004` development and
  investment, `10008`-`10010` deposit banks by ownership). It answers JSON
  `{"success", "Json": {"data": {"rows": [{"cell": [group, no, label, _, value]}]}}}`.
  No cookie or token is needed. Same host as the weekly bulletin, so the same bundled TLS
  intermediate.
- **Unpublished months** answer `success: false` with "2026 yılının en son 8 ayına ait veri
  bulunmaktadır!"; months before 2003 with "... 2003 yılı, 1. aydan başlamaktadır." Both
  are skipped. August 2026 was out on 2026-10-03: about a month after the month ends
  (`max_age_days: 75`, lookback 3 months).
- **Rows are found by label, not number.** Table 15 *Rasyolar* had 31 rows until 2017 and 32
  since; the capital adequacy ratio moved from row 29 to row 30. The labels have stayed the
  same (checked 2014-01, 2017-06, 2020-12, 2023-06, 2026-08). Series code
  `<table>:<label>`; a renamed row fails the fetch.
- **Six series from table 15**, sector, module `banking`:

| Label | Series | 2026-08 |
|---|---|---|
| Yasal Özkaynak / Risk Ağırlıklı Kalemler Toplamı (%) | Capital adequacy ratio | 16.60 |
| Dönem Net Kârı (Zararı) / Ortalama Özkaynaklar (%) | Return on equity (year to date) | 16.54 (24.8 a year) |
| Dönem Net Kârı (Zararı) / Ortalama Toplam Aktifler (%) | Return on assets (year to date) | 1.31 (1.97 a year) |
| Net Faiz Geliri (Gideri) / Ortalama Toplam Aktifler (%) | Net interest margin (year to date) | 3.28 (4.92 a year) |
| Takipteki Alacaklar Karşılığı / Brüt Takipteki Alacaklar (%) | NPL coverage ratio | 76.35 |
| Vadesiz Mevduat / Toplam Mevduat (%) | Demand deposits share | 36.26 |

- **Profitability ratios add up from January** (January 2014 ROE 0.73%, December the full
  year). They are stored as published, flagged `year_to_date: true` and annualized for
  display as `value * 12 / month` (`app/metrics.py: annualized`). July 2026 (14.59, 25.0 a
  year) and August (16.54, 24.8) then agree. The net interest margin runs smoothly across
  year ends (December 2024 3.5, January 2025 3.4); return on equity is noisier in January,
  which holds one month only (December 2024 30.5, January 2025 19.3).
- **Bank groups** (added 2026-10-03): one request takes several `taraf` values (a repeated
  form field) and returns every group's rows, named in cell 0: `Sektör`, `Mevduat-Kamu`,
  `Mevduat-Yerli Özel`, `Mevduat-Yabancı`, `Katılım` (the same names in 2014, 2019 and 2026).
  Series code `15@<taraf>:<label>`; the capital adequacy ratio and return on equity are
  loaded for the four groups and shown as one comparison chart each. August 2026: capital
  adequacy 14.1 (state), 17.1 (domestic private), 17.9 (foreign), 17.8 (participation).
- A backfill from 2014 is 152 requests, 1 s apart (all groups in each): about 2.5 minutes.

## BKM card statistics (verified 2026-09-27)

Module 2 (`module: cards`, the dashboard's *Cards* tab). Client: `sources/bkm.py`.

- **Source page:** one page per month:
  `https://bkm.com.tr/secilen-aya-ait-istatistikler/?filter_year=YYYY&filter_month=M&List=Listele&xls=1`.
  `robots.txt` allows it. Requests go 1 s apart with a descriptive User-Agent.
- **The "Excel" download (`xls=1`) is really an HTML table** with an `.xls` content type. It is
  parsed with the standard library `html.parser`; no Excel library is needed. Raw pages are
  saved as `.html`.
- **Coverage:** 2017-01 onward. The client never asks for earlier months. The labels and
  layout were identical in 2017-01, 2021-03 and 2026-07, and a backfill of all 115 months up to
  2026-07 parsed without error.
- **Unpublished months** (and months before 2017) return a page with only "Lütfen listeyi
  görebilmek için yukarıdan tarih seçiniz." The client skips such a month, but fails if no month
  in the range has data, or if a page has neither the tables nor that text.
- **Publication lag:** about 1.5 to 2 months. On 2026-09-27 the latest month was 2026-07.
  Freshness limit: `max_age_days: 100`, counted from the month's end.
- **Values:** amounts are in million TL in Turkish format (`2.450.238,01`); card counts are
  integers (`151.730.027`). Stored as published, dated at the month's end.

**Cells are found by labels, not positions.** Each table becomes a grid with `rowspan`/`colspan`
expanded. A value is the one numeric cell whose row starts with the row labels and whose
column carries all the column labels. No match, or more than one, raises an error. Units are
part of the labels (`İşlem Tutarı (Milyon TL)`), so a unit change fails loudly instead of
mixing units.

Series codes (the label mapping lives in `sources/bkm.py`):
- `cards:<card>`: *KART SAYILARI*, card = `credit` (Toplam Kredi Kartı) or `debit` (Toplam Banka
  Kartı).
- `terminals:<pos|atm>`: *POS SAYILARI* / *ATM SAYILARI*, row "POS Sayısı" / "ATM Sayısı"
  (added 2026-10-03; on every page since 2017-01: 1,703,599 POS and 48,530 ATMs in
  January 2017, 2,020,116 and 56,814 in July 2026). Unit `terminals`, shown in thousands;
  stocks like the card counts, so the 12-month view never sums them.
- `txn:<card>:<usage>:<measure>:<kind>`: *İŞLEM ADET VE TUTARLARI*.
  - card: `credit` (Kredi Kartı) or `debit` (Banka Kartı). `credit+debit` sums both rows.
  - usage: `domestic` (Yerli Kartların Yurt İçi Kullanımı), `abroad` (Yerli Kartların
    Yurtdışı Kullanımı), `foreign` (Yabancı Kartların Yurt İçi Kullanımı), `domestic_all`,
    `in_country`.
  - measure: `count` (İşlem Adedi) or `amount` (İşlem Tutarı, million TL).
  - kind: `shopping` (Alışveriş), `cash` (Nakit Çekme) or `total` (Toplam).
- `vpos:<channel>:<measure>`: *Sanal POS İşlemleri*, channel = `internet` (İnternetten Kartlı
  Ödemeler) or `mail_phone`.

| Code | Series | Unit | July 2026 |
|---|---|---|---|
| `txn:credit:domestic:amount:shopping` | Credit card spending, domestic cards in Türkiye | million TRY | 2,450,238.01 |
| `txn:debit:domestic:amount:shopping` | Debit card spending, domestic cards in Türkiye | million TRY | 418,420.61 |
| `vpos:internet:amount` | Online card payments | million TRY | 908,898.53 |
| `txn:credit+debit:foreign:amount:shopping` | Foreign cards' spending in Türkiye (a tourism signal) | million TRY | 128,787.02 |
| `cards:credit` | Number of credit cards | cards | 151,730,027 |
| `cards:debit` | Number of debit cards | cards | 223,234,663 |

**On the dashboard:**
- Amounts are shown in billion TL and card counts in million cards.
- Changes are monthly % (vs the previous month) and yearly % (vs the same month a year
  earlier).
- The real view deflates the amounts by the CPI of their own month. Card counts are not money
  and stay unchanged.
- Amounts are nominal flows and are not seasonally adjusted: December and the summer
  (tourism) months stand out, so compare yearly % rather than monthly % across seasons.
  The cards tab's "12-month total" view (`app/metrics.py: rolling_12m`) sums the TRY
  amounts over the 12 months ending in each month, which holds every season once; card
  counts are stocks and are not summed. A month missing from the window leaves no total.

## Bank websites (module 3, verified 2026-10-07)

**Deposit rates, loaded** (source `bank_site`, client `sources/bank_site.py`). The user chose
static HTML only (2026-10-07): no browser automation, no undocumented endpoints. One page per
bank per run, `robots.txt` checked first, the project's User-Agent.

| Bank | Page | Table | 100,000 TRY, 32 days (2026-10-07) |
|---|---|---|---|
| Ziraat | `ziraatbank.com.tr/tr/fiyatlar-ve-oranlar` | `data-id="rdIntBranchVadeliTL"` (internet branch) | 31.00% |
| Ziraat | same page | `data-id="rdBranchVadeliTL"` (branch) | 5.00% |
| İş Bankası | `isbank.com.tr/kampanyali-mevduat-oranlari` | the page's only table (İşCep, internet campaign) | 36.50% |

- Tables are vade groups (rows, "32 - 45 gün", "367 Üzeri Gün") by amount brackets (columns,
  "100.000 - 499.999,99 TL", "10.000.001 TL Üzeri"); the client takes the one cell whose
  ranges hold the series' days and amount. Yearly simple rates.
- Number formats differ: Ziraat writes `%31.00` (decimal point), İş Bankası `% 36,50`, typed
  by hand: one cell read `% 3 7,85` plus a zero-width space. Spaces and U+200B are dropped,
  a comma is the decimal mark when present, and anything outside 0-100% fails.
- Series code `<bank>:<table>:<days>:<amount>`, frequency `daily`, `max_age_days: 7` (fetched
  twice a week). The pages show today's rates only: a backfill stores nothing, and the
  history starts with the first fetch.
- **Checked and left out:** Halkbank (no table in the HTML), Yapı Kredi (the table's cells
  are empty until JavaScript fills them from `/_ajaxproxy/...`), Garanti BBVA (only the
  withholding-tax table and one campaign headline), Akbank (campaign headline only),
  VakıfBank (no rate page found; URLs are never guessed). Aggregators (hesapkurdu,
  hangikredi, ...) are commercial secondary sources and are not used.

**Loan rates (research 2026-09-29, not loaded):** (robots.txt, sitemaps, one fetch per product page):
- `robots.txt` allows the product pages at Ziraat, VakıfBank, Halkbank, Garanti BBVA, İş
  Bankası, Akbank, Yapı Kredi, ING and Enpara (Enpara also sends "ai-train=no").
- Comparable rates are rarely in the HTML. İş Bankası states personalised rates ("kişiye özel
  uygulanacak faiz oranı"); Yapı Kredi and Enpara fill their calculators with JavaScript;
  Halkbank's product URLs redirect to the home page; Ziraat and Akbank URLs from guesses 404.
- Only Garanti BBVA shows a statutory "örnek hesaplama" table in HTML (personal loan, 100,000
  TRY, 36 months, 3.94% monthly, with an update date); ING shows campaign "from" rates.
- Taşıt and konut pages show almost no rates in HTML.
- Loan rates and campaigns would need browser automation or undocumented endpoints, which
  the user ruled out on 2026-10-07; campaigns would also need their own table.
