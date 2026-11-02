# India merchandise trade tracker

Collectors for India's merchandise trade statistics: chapter-wise (2-digit HS)
annual exports and imports from the official DGFT / Department of Commerce
**Export-Import Data Bank (EIDB)**, plus a World Bank panel of trade, FDI and
exchange-rate indicators for India and seven comparator economies. Both land in
one DuckDB file (`data/trade.duckdb`) with CSV snapshots committed alongside —
a free, HS-chapter-granularity alternative to commercial trade-data vendors
(Volza, Export Genius).

> Status: active (cron re-collects monthly). Data is scraped/pulled from public
> government and World Bank sources; check their terms before redistribution.
> Educational and research use; not investment advice.

<!-- how-to-use-with-ai -->
> **New here?** See [How to use this repo with AI (Claude, Gemini, ChatGPT)](HOW_TO_USE_WITH_AI.md).

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/india-trade-tracker/blob/main/notebooks/colab_test.ipynb)

## What it does
- Scrapes EIDB chapter-wise exports and imports (US$ million, current + previous
  FY, growth %) for a chosen financial year via headless Chrome (`collectors/eidb_trade.py`).
- Pulls 10 World Bank trade/FDI/exchange indicators × 8 countries
  (IN CN US DE JP KR VN BD), full history 1960–present (`collectors/worldbank_trade.py`).
- Upserts into `data/trade.duckdb` — EIDB rows are replaced per `(flow, fy_start)`,
  the World Bank table is rebuilt on every run — so re-runs are idempotent.
- Exports CSV snapshots of both tables and commits them (`scripts/sync_data.sh`, cron).
- Ships a Colab notebook and a worked example (fodder exports report) under `reports/`.

## Quick start
```bash
git clone https://github.com/herrrickshaw/india-trade-tracker.git
cd india-trade-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # needs Google Chrome installed for EIDB
python3 collectors/eidb_trade.py --year 2024   # FY 2024-25, exports + imports
python3 collectors/worldbank_trade.py
```

Other invocations (flags and output paths are stable — cron calls them by name):
```bash
python3 collectors/eidb_trade.py                       # current FY, both flows
python3 collectors/eidb_trade.py --year 2025 --flow exports
python3 collectors/worldbank_trade.py --db /tmp/other.duckdb
```

## How it works
Each collector is a fetch → parse → write pipeline; the parsers are pure
functions and the browser / HTTP client is injected, so everything below
"fetch" is unit-tested without a network.

| Stage | EIDB (`collectors/eidb_trade.py`) | World Bank (`collectors/worldbank_trade.py`) |
|---|---|---|
| fetch | `submit_report_form` drives headless Chrome through the Livewire form, `_read_table` / `_click_next` walk the DataTable | `get_json` (urllib, 3 retries with backoff), `fetch_indicator` pages through `api.worldbank.org/v2` |
| parse | `resolve_year`, `pick_length_option`, `parse_row` (cell texts → row tuple), `_num` | `indicator_url`, `parse_page` (skips null observations), `has_more_pages` |
| write | `write_rows`: `CREATE TABLE IF NOT EXISTS` + delete/insert per `(flow, fy_start)` | `write_rows`: `CREATE OR REPLACE TABLE` |
| glue | `collect(db_path, year, flows, driver_factory=make_driver)` | `collect(db_path, fetch=get_json)` |

Why Selenium for EIDB: tradestat.commerce.gov.in is a Laravel/Livewire app —
the report form is CSRF-tokened and the results table is JS-rendered, so raw
`requests` POSTs return 404. Selenium ≥ 4 auto-manages chromedriver. Verified
working 2026-07-21: FY2024-25 totals reconcile with official figures
(exports ≈ $438B, imports ≈ $721B).

If the requested FY is not offered by the site, the collector falls back to the
newest FY available and stores rows under that FY.

## Repository layout
| Path | Purpose |
|---|---|
| `collectors/eidb_trade.py` | EIDB chapter-wise collector (Selenium) |
| `collectors/worldbank_trade.py` | World Bank indicator panel collector |
| `scripts/sync_data.sh` | Dump both tables to CSV, commit + push `data/` if changed (cron) |
| `tests/` | pytest suite; no browser or network (parsers, mocked drivers/fetch, temp DuckDB) |
| `data/trade.duckdb` | Tracked DuckDB file (≈1.1 MB) written by both collectors |
| `data/*_snapshot.csv` | CSV snapshots of each table, refreshed by `sync_data.sh` |
| `data/fodder_hs_export_series.csv`, `reports/` | Worked example: India fodder exports report, deck and chart |
| `notebooks/colab_test.ipynb` | Colab smoke notebook |
| `DATA_ACCESS.md` | Git LFS status and account-wide data policy |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| [DGFT/DoC EIDB](https://tradestat.commerce.gov.in/eidb/) — `/eidb/commodity_wise_export`, `/eidb/commodity_wise_import` | Chapter-wise (2-digit HS) annual exports & imports, US$ million, current + previous FY with growth % | **Scraped** with Selenium headless Chrome | Government of India data; DoC updates monthly with a ~2-month lag |
| [World Bank Indicators API v2](https://api.worldbank.org/v2) | `NE.EXP.GNFS.ZS`, `NE.IMP.GNFS.ZS`, `TX.VAL.MRCH.CD.WT`, `TM.VAL.MRCH.CD.WT`, `BX.GSR.NFSV.CD`, `BM.GSR.NFSV.CD`, `BN.CAB.XOKA.GD.ZS`, `BX.KLT.DINV.WD.GD.ZS`, `PA.NUS.FCRF`, `TT.PRI.MRCH.XD.WD` for IND CHN USA DEU JPN KOR VNM BGD | Open JSON API, no key | World Bank open data terms; annual series |

### Schema
- `eidb_chapter_trade(flow, fy_start, hs_code, commodity, prev_year_usd_mn, cur_year_usd_mn, growth_pct, header, pulled_on)`
- `worldbank_trade(country, indicator_code, indicator, year, value, source_last_updated)`

## Configuration
No secrets or environment variables are required; neither source needs an API key.

| Setting | Where | Default |
|---|---|---|
| `--db` | both collectors | `data/trade.duckdb` (relative to repo root) |
| `--year` | `eidb_trade.py` | current Indian FY (April start; `2024` = FY 2024-25) |
| `--flow` | `eidb_trade.py` | `both` (`exports` \| `imports`) |
| Timeouts, page-settle pause, 5,000-row safety cap, Chrome UA/window | `eidb_trade.py` constants | 30 s / 90 s / 2 s / 5000 |
| Page size, request timeout, retries, politeness pause | `worldbank_trade.py` constants | 1000 / 30 s / 3 / 0.3 s |

## Testing
```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```
Tests never launch Chrome or touch the network.

## Limitations and known issues
- **HS chapter 01 is missing from every EIDB pull.** The header locator
  `//table//tr[1]` matches the first row of `tbody` as well as `thead`, so
  `position()>1` drops chapter 01 (LIVE ANIMALS) and its text leaks into the
  `header` column — the snapshots hold 97 chapters per flow/FY. Fixing it needs a
  live-site check; not changed in the SDLC refactor.
- The DataTable pagination guard in `scrape_flow` stops after a single page turn;
  in practice the "All" page-length option makes this moot.
- The import page's form-control ids (`EidbYearCwi`, `Eidb_ReportCwi`) are guessed
  and fall back to the first `<select>` in the form.
- 🔴 Git LFS in this account is exhausted (see `DATA_ACCESS.md`): do not add new LFS
  objects; `.gitattributes` is intentionally empty and `data/` is plain git.
- EIDB chapter data is annual only; the roadmap items below are not implemented:
  country-wise and commodity × country reports (`/eidb/country_wise_export`,
  `/eidb/commodityx_countries_wise_export`), 4/6/8-digit HS drill-down, and the
  monthly tradestat module.

## Related repositories
- [herrrickshaw/repo-data-dedup](https://github.com/herrrickshaw/repo-data-dedup) — LFS inventory, data-source catalogue and re-collection SOP for this account.

## Licence
No licence file yet — all rights reserved.
