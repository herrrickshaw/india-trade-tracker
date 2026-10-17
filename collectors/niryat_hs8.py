#!/usr/bin/env python3
"""NIRYAT (TIA portal) India exports/imports at HS8 by partner country — open JSON.

One financial year per run; values US$ million. Same cert caveat as
niryat_bilateral.py (hostname mismatch -> verification disabled for this host).

Usage:  python3 collectors/niryat_hs8.py [--db data/trade.duckdb] [--finyear 2025-26]
"""

import argparse
import time
from pathlib import Path

import duckdb

try:  # works both as a script and as collectors.niryat_hs8
    from niryat_bilateral import get_json
except ImportError:
    from collectors.niryat_bilateral import get_json

BASE = "https://niryat.gov.in/public/country/getIndiaSupplyDataPublic"
PARTNERS = ["CHE", "ARE", "USA", "DEU", "JPN"]
FLOWS = ["Export", "Import"]


def parse(payload, country, flow, finyear):
    rows = []
    for lab, val in zip(payload.get("label") or [], payload.get("value") or []):
        code, _, desc = lab["label"].partition(": ")
        if val["value"] is None:
            continue
        rows.append((country, flow.lower(), finyear, code.strip(), desc.strip(),
                     float(val["value"])))
    return rows


def collect(db_path, finyear="2025-26"):
    calyear = int(finyear[:4]) + 1
    rows = []
    for iso in PARTNERS:
        for flow in FLOWS:
            d = get_json(f"{BASE}?impexptype={flow}&calyear={calyear}&hscode=HS8"
                         f"&commocode=HS&countryCode={iso}&region=COUNTRY&regionCode="
                         f"&qeCodes=&pcCodes=&yeartype=fin&finyear={finyear}&currency=USD")
            rows += parse(d or {}, iso, flow, finyear)
            time.sleep(1)
        print(f"  {iso}: done ({len(rows)} rows cumulative)")
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE niryat_hs8 (
        country TEXT, flow TEXT, finyear TEXT, hs8 TEXT, description TEXT,
        value_usd_mn DOUBLE)""")
    con.executemany("INSERT INTO niryat_hs8 VALUES (?,?,?,?,?,?)", rows)
    con.close()
    print(f"niryat_hs8: {len(rows):,} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "trade.duckdb"))
    ap.add_argument("--finyear", default="2025-26")
    args = ap.parse_args()
    collect(args.db, args.finyear)
