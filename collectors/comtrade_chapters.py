#!/usr/bin/env python3
"""UN Comtrade public preview API (no key): India's world-total exports/imports by
HS chapter (2-digit), annual, as reported by India (reporter 699), US$.

Preview endpoint caps at 500 rows and one period per call, so this loops one
year at a time with all chapters in a single cmdCode list (~190 rows/call).
Keep volume low: 3 s between calls.

Usage:  python3 collectors/comtrade_chapters.py [--db data/trade.duckdb] [--years 2020 2021 2022 2023 2024]
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
URL = ("https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=699"
       "&period={y}&flowCode=X,M&partnerCode=0&partner2Code=0&customsCode=C00"
       "&motCode=0&cmdCode={cmds}")
CHAPTERS = ",".join(f"{c:02d}" for c in range(1, 98) if c != 77)


def get_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=90
            ) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                json.JSONDecodeError):
            if i == retries - 1:
                raise
            time.sleep(5 * (i + 1))


def parse(payload):
    return [(int(r["period"]), r["flowCode"], r["cmdCode"], r.get("primaryValue"),
             r.get("netWgt"))
            for r in payload.get("data", []) if r.get("primaryValue") is not None]


def collect(db_path, years):
    rows = []
    for y in years:
        d = get_json(URL.format(y=y, cmds=CHAPTERS))
        if d and d.get("error"):
            print(f"  {y}: API error {d['error']}")
        else:
            rows += parse(d or {})
        print(f"  {y}: done ({len(rows)} rows cumulative)")
        time.sleep(3)
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE comtrade_india_chapters (
        year INTEGER, flow TEXT, hs_chapter TEXT, value_usd DOUBLE, net_weight_kg DOUBLE)""")
    con.executemany("INSERT INTO comtrade_india_chapters VALUES (?,?,?,?,?)", rows)
    con.close()
    print(f"comtrade_india_chapters: {len(rows):,} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "trade.duckdb"))
    ap.add_argument("--years", type=int, nargs="+", default=[2020, 2021, 2022, 2023, 2024])
    args = ap.parse_args()
    collect(args.db, args.years)
