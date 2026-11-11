
```markdown
# DATA_SOURCES

All data are unofficial extracts for research; verify against the source before use. Collection dates are in each table
(`pulled_on` / `source_last_updated`) and in git history.

## 1. TradeStat EIDB - chapter-wise trade
- Publisher: Directorate General of Commercial Intelligence & Statistics (DGCI&S), Department of Commerce, Ministry of Commerce & Industry
- URL: https://tradestat.commerce.gov.in/ (Export Import Data Bank)
- Licence: not stated on the site (page shows "DGCI&S Ownership" metadata and a Disclaimer); treat as Government of India
  copyright pending written confirmation. Do not assume GODL-India.
- Table: eidb_chapter_trade (flow, fy_start, hs_code, commodity, prev_year_usd_mn, cur_year_usd_mn, growth_pct, pulled_on)
- Coverage: HS 2-digit, FY2024-25 and FY2025-26 (provisional), exports and imports, US$ million. Chapter 01 is currently missing (parser bug).
- Refresh: monthly (DoC lag ~2 months); pulled 2026-07-21 and 2026-09-15.
- Collection: headless Chrome (Selenium) drives the Laravel/Livewire form (CSRF-tokened, JS-rendered). robots.txt allows all. No login, CAPTCHA or WAF.
- Caveats: current FY is provisional and revised; totals row is stored in `header` text.

## 2. NIRYAT / Trade Intelligence & Analytics (TIA) portal
- Publisher: Department of Commerce. URL: https://trade-analytics.commerce.gov.in/ (formerly niryat.gov.in)
- Licence: footer "Copyright 2024 ... all rights reserved"; permission to be sought.
- Tables: niryat_bilateral (country, period_type, period, exports/imports/balance US$ mn; 16 partners; calendar years 2017-2026, 2026 partial; months Apr-May 2026)
  and niryat_hs8 (country, flow, finyear, hs8, description, value_usd_mn; 5 partners; FY2025-26).
- Collection: open JSON endpoints (`bilateralMonthlyDataPublic`, `getIndiaSupplyDataPublic`) with TLS verification currently disabled because of a certificate mismatch.
- Caveats: yearly 2026 is year-to-date; HS8 table to be removed from the committed database.

## 3. UN Comtrade
- Publisher: UN DESA Statistics Division. URL: https://comtradeapi.un.org/public/v1/preview
- Licence: UN Comtrade terms of use (to be reviewed); attribute "UN Comtrade".
- Table: comtrade_india_chapters (year, flow M/X, hs_chapter, value_usd, net_weight_kg), India-reported, 2020-2024, 96 chapters.
- Collection: keyless preview endpoint, 500-row cap, one period per call, ~3 s between calls.
- Caveats: calendar-year basis (not fiscal); net weight missing for 18% of rows.

## 4. World Bank indicators
- Publisher: The World Bank. URL: https://api.worldbank.org/v2 ; Licence: CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses)
- Table: worldbank_trade (country, indicator_code, indicator, year, value, source_last_updated): 10 indicators x 8 countries, 1960-2025.
- Attribution: "The World Bank, World Development Indicators / API, retrieved <date>."

## 5. Fodder export series and reports
- data/fodder_hs_export_series.csv: HS 1213, 1214, 2301-2306, 2308, 2309 from TradeStat commodity-wise exports, FY2017-18..FY2025-26, pulled 2026-07-23.
- reports/*: author analysis. Global market-size figures from vendors (Mordor Intelligence, Precedence Research, Tridge, Volza, AgNavigator) are modelled estimates and are cited, not redistributed.
```
