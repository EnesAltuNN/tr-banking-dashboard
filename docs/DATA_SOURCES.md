# Data sources

Detailed, verified facts about each source. `CLAUDE.md` keeps only a short summary per source
and links here. Update the "verified" dates when you re-check a fact.

## TCMB EVDS3 (verified 2026-09-24)

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
- Tracked series come from data group `bie_hpbitablo6` (weekly Friday, thousand TRY, TRY+FX) and
  start on 2024-06-28.
  - The older groups `bie_kredi` / `bie_tukkre` were archived on 2025-01-31 with a different
    methodology; do not stitch them together.
- New weekly data appears on Thursdays around 14:30 Istanbul time.
- Never guess series codes: discover them through the metadata endpoints and show the official
  name, frequency and unit before using them.

## BDDK weekly bulletin (verified 2026-09-24)

- There is no official API. The client uses the endpoint behind the "advanced" page's charts:
  - `POST https://www.bddk.org.tr/BultenHaftalik/tr/Gelismis/KiyaslamaJsonGetir`
  - Form fields: `dil=tr`, `baslangicTarihi`/`bitisTarihi` (`DD.MM.YYYY`), `id` (row, e.g.
    `1.0.4`), `parabirimi` (`TRY`|`USD`), `sutun` (1 TL, 2 FX, 3 total), `tarafKodu` (bank group).
  - No token or cookie is needed.
  - Response: `{"Baslik", "XEkseni": ["D.MM.YYYY"...], "YEkseni": [numbers]}`.
  - One request returns the full range; data starts 2014-01-03.
- An unknown row or bank group returns HTTP 200 with **empty lists**, so the parser treats an
  empty series as an error.
- The home-page variant `tr/Home/KiyaslamaJsonGetir` caps results at 13 weeks; don't use it.
- Series code in config: `<row>:<group>:<currency>:<column>`.
  - Bank groups: 10001 sector, 10002 deposit, 10003 development & investment, 10004
    participation, 10005 state, 10006 foreign, 10007 domestic private.
  - State + foreign + domestic private = sector (checked).
- Rows come from the bulletin table *Krediler* (tabloId 1 on the page). Row 1.0.3 (consumer
  loans) excludes credit cards; 1.0.2 includes them.
- `bddk.org.tr` sends only its leaf certificate; the intermediate "GlobalSign RSA OV SSL CA
  2018" is missing.
  - `bddk_ssl_context()` trusts certifi plus the bundled public intermediate
    (`sources/certs/`). It works on Windows and Linux CI. Never use `verify=False`.
  - The leaf expires 2026-11-15. If the renewal changes the issuer, download the new
    intermediate from the leaf's "CA Issuers" URL.
- It is a public website, not an API: keep the 1 s delay between requests.

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
