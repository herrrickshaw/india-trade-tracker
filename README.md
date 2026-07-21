# india-trade-tracker

India merchandise trade tracking from the official DGFT/Department of Commerce
**Export-Import Data Bank (EIDB)** plus a World Bank trade indicator panel.
A free alternative to commercial trade-data vendors (Volza, Export Genius) at
HS-chapter granularity.

## Sources

| Collector | Source | Data | Cadence |
|---|---|---|---|
| `collectors/eidb_trade.py` | [tradestat.commerce.gov.in/eidb](https://tradestat.commerce.gov.in/eidb/) | Chapter-wise (2-digit HS) annual exports & imports, US$ million, current + previous FY with growth % | Monthly (DoC updates with ~2-month lag) |
| `collectors/worldbank_trade.py` | [World Bank API](https://api.worldbank.org/v2) | 10 trade/FDI/exchange indicators × 8 countries (IN CN US DE JP KR VN BD), 1960–present | Annual series; re-pull monthly |

## Why Selenium for EIDB

tradestat.commerce.gov.in is a Laravel/Livewire app: the report form is
CSRF-tokened and the results table is JS-rendered, so raw `requests` POSTs
return 404. The collector drives headless Chrome (Selenium ≥ 4, which
auto-manages chromedriver). Verified working 2026-07-21 — FY2024-25 totals
reconcile with official figures (exports ≈ $438B, imports ≈ $721B).

## Usage

```bash
pip install -r requirements.txt      # needs Google Chrome installed
python3 collectors/eidb_trade.py --year 2024          # FY 2024-25, both flows
python3 collectors/eidb_trade.py --year 2025 --flow exports
python3 collectors/worldbank_trade.py
```

Both write to `data/trade.duckdb` (gitignored). CSV snapshots of each table
are committed under `data/` for reference.

## Schema

- `eidb_chapter_trade(flow, fy_start, hs_code, commodity, prev_year_usd_mn, cur_year_usd_mn, growth_pct, header, pulled_on)`
- `worldbank_trade(country, indicator_code, indicator, year, value, source_last_updated)`

## Roadmap

- Country-wise and commodity-x-country EIDB reports (same form pattern:
  `/eidb/country_wise_export`, `/eidb/commodityx_countries_wise_export`)
- 4/6/8-digit HS drill-down via the "specific commodity" form mode
- Monthly (not just annual) series from the tradestat monthly module
