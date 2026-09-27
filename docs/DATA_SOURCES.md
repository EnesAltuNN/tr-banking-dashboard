# Data sources

Detailed, verified facts about each source. `CLAUDE.md` keeps only a short summary per source
and links here. Update the "verified" dates when you re-check a fact.

| Source | Series | Unit (stored) | History from | Access |
|---|---|---|---|---|
| [TCMB EVDS3](#tcmb-evds3-verified-2026-09-24) | 6 weekly loan series | thousand TRY | 2024-06-28 | official API, free key |
| [BDDK weekly bulletin](#bddk-weekly-bulletin-verified-2026-09-24) | 7 weekly loan series | million TRY | 2014-01-03 | public website, no API |
| [BKM](#bkm-card-spending-module-2-pending-decision) | 6 proposed monthly card series | million TL, counts | 2017-01 | public website (not built yet) |

Series codes, names and units are configured in [`config/series.yaml`](../config/series.yaml).
Values are stored exactly as published; the dashboard converts both TRY units to billion TRY.

**Cross-source notes:**
- **EVDS and BDDK agree closely:** within about 0.3% on common series (checked 2026-09-24).
  - Their bank coverage differs slightly.
  - BDDK *commercial and other loans* is broader than EVDS *commercial loans*.
- **Values are nominal TRY.** Inflation, and the TRY value of FX loans, drive much of the growth.
  Inflation-adjusted values are on the roadmap.
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
- **TLS:** `bddk.org.tr` sends only its leaf certificate; the intermediate "GlobalSign RSA OV
  SSL CA 2018" is missing.
  - Browsers download the missing intermediate themselves; Python on Linux does not.
  - `bddk_ssl_context()` trusts certifi's roots plus that public intermediate, bundled in
    `src/tr_banking/sources/certs/`. It works the same on Windows and in CI. Never use
    `verify=False`.
  - The leaf expires 2026-11-15. If the renewal changes the issuer, BDDK fetches fail with a
    certificate error. Download the new intermediate from the leaf's "CA Issuers" URL and
    replace the bundled file.

## BKM card spending (module 2, pending decision)

Research is done (2026-09-24). **No code has been written.** The user put module 2 on hold and
still has to confirm the series list below before implementation starts.

- **Source page:** one page per month:
  `https://bkm.com.tr/secilen-aya-ait-istatistikler/?filter_year=YYYY&filter_month=M&List=Listele`
  (`robots.txt` allows it).
- **The "Excel" download (`&xls=1`) is really an HTML table** with an `.xls` name, so parse the
  HTML with the stdlib `html.parser`; no Excel library is needed.
- **Coverage:** 2017-01 onward, with the same table layout every month. Publication lags about
  1.5 to 2 months (on 2026-09-24 the latest month was 2026-07).
- **Values:** amounts are in million TL, written in Turkish format (`2.450.238,01`). Card counts
  are plain integers.
- **Proposed series** (July 2026 values):
  - Credit card shopping amount, domestic cards used domestically: 2,450,238 million TL.
  - Debit card shopping amount, domestic cards used domestically: 418,421 million TL.
  - Online card payments (internetten kartli odemeler), amount: 908,899 million TL.
  - Foreign cards used domestically, shopping amount, credit + debit (a tourism signal):
    128,787 million TL.
  - Number of credit cards: 151.7 million.
  - Number of debit cards: 223.2 million.
- **Implementation plan:**
  - Locate cells by row and column labels, not by position.
  - Monthly metrics: month-over-month % and 12-month %.
  - Add dashboard tabs per module.
  - Backfill with one request per month, 1 s apart.
