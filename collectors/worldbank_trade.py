#!/usr/bin/env python3
"""World Bank trade indicator panel (open API, no key).

Annual trade/FDI/exchange series for India and comparators, full history,
rebuilt on each run.

Usage:  python3 collectors/worldbank_trade.py [--db data/trade.duckdb]
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

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


def get_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30
            ) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def collect(db_path):
    rows = []
    for iso in COUNTRIES:
        for code, name in INDICATORS.items():
            page = 1
            while True:
                d = get_json(
                    f"https://api.worldbank.org/v2/country/{iso}/indicator/"
                    f"{code}?format=json&per_page=1000&page={page}")
                if not d or len(d) < 2 or not d[1]:
                    break
                for obs in d[1]:
                    if obs.get("value") is not None:
                        rows.append((iso, code, name, int(obs["date"]),
                                     float(obs["value"]),
                                     d[0].get("lastupdated")))
                if page >= d[0].get("pages", 1):
                    break
                page += 1
            time.sleep(0.3)
        print(f"  {iso}: done ({len(rows)} rows cumulative)")

    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE worldbank_trade (
        country TEXT, indicator_code TEXT, indicator TEXT,
        year INTEGER, value DOUBLE, source_last_updated TEXT)""")
    con.executemany("INSERT INTO worldbank_trade VALUES (?,?,?,?,?,?)", rows)
    con.close()
    print(f"worldbank_trade: {len(rows):,} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "trade.duckdb"))
    args = ap.parse_args()
    collect(args.db)
