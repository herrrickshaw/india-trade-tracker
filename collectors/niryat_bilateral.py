#!/usr/bin/env python3
"""NIRYAT (TIA portal) India bilateral trade by partner country — open JSON, no key.

Yearly (calendar) and monthly (latest year) exports/imports/balance per partner,
US$ million. niryat.gov.in's TLS cert does not match its hostname, so
certificate verification is disabled for this host only.

Usage:  python3 collectors/niryat_bilateral.py [--db data/trade.duckdb] [--year 2026]
"""

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
BASE = "https://niryat.gov.in/public/country/bilateralMonthlyDataPublic"
# ISO-3 codes of major partners
PARTNERS = ["USA", "ARE", "CHN", "NLD", "SGP", "GBR", "DEU", "SAU", "BGD",
            "CHE", "JPN", "KOR", "RUS", "IDN", "AUS", "FRA"]
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def get_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=90, context=_CTX
            ) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                json.JSONDecodeError):
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def parse(payload, country, period_type):
    """payload = [labels, exports, imports, balance], each a list of single-key dicts."""
    labels, exp, imp, bal = ([list(x.values())[0] for x in arr] for arr in payload)
    return [(country, period_type, str(l), float(e), float(i), float(b))
            for l, e, i, b in zip(labels, exp, imp, bal)
            if e is not None and i is not None and b is not None]


def collect(db_path, year=2026):
    rows = []
    for iso in PARTNERS:
        for indi in ("yearly", "monthly"):
            d = get_json(f"{BASE}?indi={indi}&countryCode={iso}&year={year}"
                         "&region=COUNTRY&regionCode=&regionCodetd=&currency=USD")
            if d and len(d) == 4:
                rows += parse(d, iso, indi)
            time.sleep(0.5)
        print(f"  {iso}: done ({len(rows)} rows cumulative)")
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE niryat_bilateral (
        country TEXT, period_type TEXT, period TEXT,
        exports_usd_mn DOUBLE, imports_usd_mn DOUBLE, balance_usd_mn DOUBLE)""")
    con.executemany("INSERT INTO niryat_bilateral VALUES (?,?,?,?,?,?)", rows)
    con.close()
    print(f"niryat_bilateral: {len(rows):,} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "trade.duckdb"))
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()
    collect(args.db, args.year)
