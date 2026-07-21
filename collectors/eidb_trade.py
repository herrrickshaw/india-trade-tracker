#!/usr/bin/env python3
"""DGFT/DoC Export-Import Data Bank (EIDB) collector.

Scrapes chapter-wise (2-digit HS) annual export and import values from
https://tradestat.commerce.gov.in/eidb/ . The site is a Laravel/Livewire
app (CSRF-tokened form, JS-rendered DataTable), so raw POSTs 404 —
Selenium + headless Chrome is required. Verified working 2026-07-21.

Usage:
    python3 collectors/eidb_trade.py                    # latest year, exports+imports
    python3 collectors/eidb_trade.py --year 2024        # FY 2024-25
    python3 collectors/eidb_trade.py --flow exports
"""

import argparse
import time
from datetime import date
from pathlib import Path

import duckdb
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

URLS = {
    "exports": "https://tradestat.commerce.gov.in/eidb/commodity_wise_export",
    "imports": "https://tradestat.commerce.gov.in/eidb/commodity_wise_import",
}
# form control ids differ per page (Cwe = commodity-wise export, Cwi = import)
YEAR_IDS = {"exports": "EidbYearCwe", "imports": "EidbYearCwi"}
REPORT_IDS = {"exports": "Eidb_ReportCwe", "imports": "Eidb_ReportCwi"}
US_DOLLAR_MILLION = "2"


def make_driver():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/126.0 Safari/537.36")
    return webdriver.Chrome(options=opts)


def find_control(driver, preferred_id, tag="select"):
    """The import page's control ids are guessed; fall back to first match."""
    els = driver.find_elements(By.ID, preferred_id)
    if els:
        return els[0]
    return driver.find_element(By.XPATH, f"//form//{tag}")


def scrape_flow(driver, flow, year):
    driver.get(URLS[flow])
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.XPATH, "//form//select")))
    year_sel = Select(find_control(driver, YEAR_IDS[flow]))
    available = [o.get_attribute("value") for o in year_sel.options
                 if o.get_attribute("value").isdigit()]
    if str(year) not in available:
        year = int(max(available, key=int))  # newest FY the site offers
    year_sel.select_by_value(str(year))
    report_sel = driver.find_elements(By.ID, REPORT_IDS[flow])
    if not report_sel:
        report_sel = [s for s in driver.find_elements(By.XPATH, "//form//select")
                      if any(o.get_attribute("value") == US_DOLLAR_MILLION
                             for o in s.find_elements(By.TAG_NAME, "option"))][-1:]
    Select(report_sel[0]).select_by_value(US_DOLLAR_MILLION)
    driver.find_element(
        By.XPATH, "//button[@type='submit'] | //input[@type='submit']").click()
    WebDriverWait(driver, 90).until(
        EC.presence_of_element_located((By.XPATH, "//table//td")))

    # show all rows if a DataTables length selector exists
    for sel in driver.find_elements(By.XPATH, "//select[contains(@name,'length')]"):
        values = [o.get_attribute("value")
                  for o in sel.find_elements(By.TAG_NAME, "option")]
        best = "-1" if "-1" in values else max(
            (v for v in values if v.lstrip("-").isdigit()), key=int, default=None)
        if best:
            Select(sel).select_by_value(best)
            time.sleep(2)

    rows, seen = [], set()
    while True:
        header = [c.text.strip() for c in driver.find_elements(
            By.XPATH, "//table//tr[1]/th | //table//tr[1]/td")]
        for tr in driver.find_elements(By.XPATH, "//table//tr[position()>1]"):
            cells = [c.text.strip() for c in tr.find_elements(By.XPATH, "./td")]
            if len(cells) < 4 or not cells[1]:
                continue
            key = (cells[1], cells[2])
            if key in seen:
                continue
            seen.add(key)
            rows.append((flow, year, cells[1], cells[2],
                         _num(cells[3]), _num(cells[5]) if len(cells) > 5 else None,
                         _num(cells[7]) if len(cells) > 7 else None,
                         " | ".join(header)))
        nxt = driver.find_elements(
            By.XPATH, "//a[contains(@class,'next') and not(contains(@class,'disabled'))]"
                      " | //li[contains(@class,'next') and not(contains(@class,'disabled'))]/a")
        if not nxt:
            break
        before = len(seen)
        nxt[0].click()
        time.sleep(2)
        if len(seen) == before and not driver.find_elements(
                By.XPATH, "//table//tr[position()>1]/td"):
            break
        if len(rows) > 5000:  # safety
            break
        # stop if pagination stopped adding rows
        if len(seen) == before:
            break
    return rows


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def collect(db_path, year, flows):
    driver = make_driver()
    all_rows = []
    try:
        for flow in flows:
            rows = scrape_flow(driver, flow, year)
            fy = rows[0][1] if rows else year
            print(f"  {flow} FY{fy}-{fy + 1}: {len(rows)} HS chapters")
            all_rows.extend(rows)
    finally:
        driver.quit()

    con = duckdb.connect(str(db_path))
    con.execute("""CREATE TABLE IF NOT EXISTS eidb_chapter_trade (
        flow TEXT, fy_start INTEGER, hs_code TEXT, commodity TEXT,
        prev_year_usd_mn DOUBLE, cur_year_usd_mn DOUBLE, growth_pct DOUBLE,
        header TEXT, pulled_on TEXT)""")
    # rows carry the FY actually scraped (may differ from the requested year
    # if the site didn't offer it) — replace exactly those (flow, fy) pairs
    for f, fy in {(r[0], r[1]) for r in all_rows}:
        con.execute(
            "DELETE FROM eidb_chapter_trade WHERE flow=? AND fy_start=?", [f, fy])
    con.executemany(
        "INSERT INTO eidb_chapter_trade VALUES (?,?,?,?,?,?,?,?,?)",
        [(*r, date.today().isoformat()) for r in all_rows])
    con.close()
    print(f"eidb_chapter_trade: {len(all_rows)} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "trade.duckdb"))
    ap.add_argument("--year", type=int, default=None,
                    help="FY start year (2024 = FY 2024-25); default = current FY")
    ap.add_argument("--flow", choices=["exports", "imports", "both"],
                    default="both")
    args = ap.parse_args()
    if args.year is None:
        today = date.today()
        args.year = today.year if today.month >= 4 else today.year - 1
    flows = ["exports", "imports"] if args.flow == "both" else [args.flow]
    collect(args.db, args.year, flows)
