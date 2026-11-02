#!/usr/bin/env python3
"""World Bank trade indicator panel (open API, no key).

Annual trade/FDI/exchange series for India and comparators, full history,
rebuilt on each run.

Layout (fetch -> parse -> write):
    * ``indicator_url`` / ``parse_page`` are pure (unit-testable offline);
    * ``get_json`` is the only network call; ``fetch_indicator`` /
      ``fetch_panel`` accept an injectable ``fetch`` callable;
    * ``write_rows`` persists to DuckDB; ``collect`` glues the stages.

Usage:  python3 collectors/worldbank_trade.py [--db data/trade.duckdb]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

import duckdb

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "trade.duckdb"

API_BASE = "https://api.worldbank.org/v2"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
PER_PAGE = 1000
REQUEST_TIMEOUT_S = 30
DEFAULT_RETRIES = 3
RETRY_BACKOFF_S = 2      # sleep = RETRY_BACKOFF_S * attempt
COUNTRY_PAUSE_S = 0.3    # politeness pause between indicators
TABLE_NAME = "worldbank_trade"

INDICATORS = {
    "NE.EXP.GNFS.ZS": "exports_pct_gdp",
    "NE.IMP.GNFS.ZS": "imports_pct_gdp",
    "TX.VAL.MRCH.CD.WT": "merch_exports_usd",
    "TM.VAL.MRCH.CD.WT": "merch_imports_usd",
    "BX.GSR.NFSV.CD": "services_exports_usd",
    "BM.GSR.NFSV.CD": "services_imports_usd",
    "BN.CAB.XOKA.GD.ZS": "current_account_pct_gdp",
    "BX.KLT.DINV.WD.GD.ZS": "fdi_inflow_pct_gdp",
    "PA.NUS.FCRF": "lcu_usd_official_rate",
    "TT.PRI.MRCH.XD.WD": "terms_of_trade_index",
}
COUNTRIES = ["IND", "CHN", "USA", "DEU", "JPN", "KOR", "VNM", "BGD"]

# (country, indicator_code, indicator, year, value, source_last_updated)
Row = Tuple[str, str, str, int, float, Optional[str]]
Fetch = Callable[..., Any]


# --------------------------------------------------------------------------- pure

def indicator_url(iso: str, code: str, page: int) -> str:
    """World Bank v2 URL for one country/indicator page (JSON, PER_PAGE rows)."""
    return (f"{API_BASE}/country/{iso}/indicator/{code}"
            f"?format=json&per_page={PER_PAGE}&page={page}")


def parse_page(payload: Any, iso: str, code: str, name: str) -> List[Row]:
    """Map one API page ``[meta, observations]`` to rows.

    Null-valued observations are skipped; an empty or malformed payload
    yields ``[]``.
    """
    if not payload or len(payload) < 2 or not payload[1]:
        return []
    meta, observations = payload[0], payload[1]
    return [
        (iso, code, name, int(obs["date"]), float(obs["value"]),
         meta.get("lastupdated"))
        for obs in observations
        if obs.get("value") is not None
    ]


def has_more_pages(payload: Any, page: int) -> bool:
    """True if ``page`` (1-based) is not the last page reported by the API meta."""
    if not payload or len(payload) < 2 or not payload[1]:
        return False
    return page < payload[0].get("pages", 1)


# -------------------------------------------------------------------------- fetch

def get_json(url: str, retries: int = DEFAULT_RETRIES) -> Any:
    """GET ``url`` and decode JSON, retrying transient network/parse failures."""
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=REQUEST_TIMEOUT_S
            ) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                json.JSONDecodeError):
            # Only retry genuine network/parse failures — let programming
            # errors (TypeError, etc.) surface instead of hiding behind retries.
            if i == retries - 1:
                raise
            time.sleep(RETRY_BACKOFF_S * (i + 1))
    return None  # unreachable: retries >= 1 either returns or raises


def fetch_indicator(iso: str, code: str, name: str,
                    fetch: Optional[Fetch] = None) -> List[Row]:
    """All pages of one country/indicator series."""
    fetch = fetch or get_json
    rows: List[Row] = []
    page = 1
    while True:
        payload = fetch(indicator_url(iso, code, page))
        rows.extend(parse_page(payload, iso, code, name))
        if not has_more_pages(payload, page):
            break
        page += 1
    return rows


def fetch_panel(countries: Sequence[str] = COUNTRIES,
                indicators: Mapping[str, str] = INDICATORS,
                fetch: Optional[Fetch] = None) -> List[Row]:
    """Every (country, indicator) series, with a politeness pause between calls."""
    rows: List[Row] = []
    for iso in countries:
        for code, name in indicators.items():
            rows.extend(fetch_indicator(iso, code, name, fetch=fetch))
            time.sleep(COUNTRY_PAUSE_S)
        log.info("  %s: done (%s rows cumulative)", iso, len(rows))
    return rows


# -------------------------------------------------------------------------- write

def write_rows(db_path, rows: Sequence[Row]) -> None:
    """Rebuild the ``worldbank_trade`` table from ``rows`` (CREATE OR REPLACE)."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"""CREATE OR REPLACE TABLE {TABLE_NAME} (
            country TEXT, indicator_code TEXT, indicator TEXT,
            year INTEGER, value DOUBLE, source_last_updated TEXT)""")
        con.executemany(f"INSERT INTO {TABLE_NAME} VALUES (?,?,?,?,?,?)", rows)
    finally:
        con.close()


def collect(db_path, fetch: Optional[Fetch] = None) -> None:
    """Pull the full panel and rebuild the DuckDB table at ``db_path``."""
    rows = fetch_panel(fetch=fetch)
    write_rows(db_path, rows)
    log.info("%s: %s rows -> %s", TABLE_NAME, f"{len(rows):,}", db_path)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    collect(args.db)


if __name__ == "__main__":
    main()
