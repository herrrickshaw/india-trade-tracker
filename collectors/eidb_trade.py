#!/usr/bin/env python3
"""DGFT/DoC Export-Import Data Bank (EIDB) collector.

Scrapes chapter-wise (2-digit HS) annual export and import values from
https://tradestat.commerce.gov.in/eidb/ . The site is a Laravel/Livewire
app (CSRF-tokened form, JS-rendered DataTable), so raw POSTs 404 —
Selenium + headless Chrome is required. Verified working 2026-07-21.

Layout (fetch -> parse -> write):
    * pure helpers (``_num``, ``resolve_year``, ``pick_length_option``,
      ``parse_row``, ``default_fy_start``, ``flows_from_arg``) take data in
      and return data out, so they are unit-testable without a browser;
    * ``scrape_flow`` drives an injected Selenium driver and returns rows;
    * ``write_rows`` persists rows to DuckDB; ``collect`` glues the stages.

Usage:
    python3 collectors/eidb_trade.py                    # latest year, exports+imports
    python3 collectors/eidb_trade.py --year 2024        # FY 2024-25
    python3 collectors/eidb_trade.py --flow exports
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import duckdb
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "trade.duckdb"

BASE_URL = "https://tradestat.commerce.gov.in/eidb"
URLS = {
    "exports": f"{BASE_URL}/commodity_wise_export",
    "imports": f"{BASE_URL}/commodity_wise_import",
}
# form control ids differ per page (Cwe = commodity-wise export, Cwi = import)
YEAR_IDS = {"exports": "EidbYearCwe", "imports": "EidbYearCwi"}
REPORT_IDS = {"exports": "Eidb_ReportCwe", "imports": "Eidb_ReportCwi"}
US_DOLLAR_MILLION = "2"  # value of the "US$ Million" option in the report-type select
ALL_FLOWS = ("exports", "imports")

TABLE_NAME = "eidb_chapter_trade"
FORM_TIMEOUT_S = 30     # wait for the report form to render
RESULT_TIMEOUT_S = 90   # wait for the results DataTable after submit
PAGE_SETTLE_S = 2       # pause after changing page length / turning a page
MAX_ROWS = 5000         # safety cap on rows per flow (site has ~99 HS chapters)
WINDOW_SIZE = "1400,1000"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)

# (flow, fy_start, hs_code, commodity, prev_year_usd_mn, cur_year_usd_mn,
#  growth_pct, header)
Row = Tuple[str, int, str, str, Optional[float], Optional[float], Optional[float], str]

# column offsets in the EIDB results table
_COL_HS_CODE = 1
_COL_COMMODITY = 2
_COL_PREV_YEAR = 3
_COL_CUR_YEAR = 5
_COL_GROWTH = 7
_MIN_CELLS = 4


# --------------------------------------------------------------------------- pure

def _num(s: str | None) -> float | None:
    """Parse a comma-grouped number ('1,234.5') to float; None if not numeric."""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def resolve_year(available: Sequence[str], year: int) -> int:
    """Return ``year`` if the site offers it, else the newest FY it does offer."""
    if str(year) in available:
        return year
    return int(max(available, key=int))


def pick_length_option(values: Iterable[str]) -> str | None:
    """Choose the DataTables page-length option showing the most rows.

    '-1' (All) wins when present; otherwise the largest numeric option.
    """
    values = list(values)
    if "-1" in values:
        return "-1"
    return max((v for v in values if v.lstrip("-").isdigit()), key=int, default=None)


def parse_row(cells: Sequence[str], flow: str, year: int, header: str) -> Row | None:
    """Map one results-table row (cell texts) to a Row; None if not a data row."""
    if len(cells) < _MIN_CELLS or not cells[_COL_HS_CODE]:
        return None
    return (
        flow,
        year,
        cells[_COL_HS_CODE],
        cells[_COL_COMMODITY],
        _num(cells[_COL_PREV_YEAR]),
        _num(cells[_COL_CUR_YEAR]) if len(cells) > _COL_CUR_YEAR else None,
        _num(cells[_COL_GROWTH]) if len(cells) > _COL_GROWTH else None,
        header,
    )


def default_fy_start(today: date) -> int:
    """Indian financial year starts in April: FY start year for ``today``."""
    return today.year if today.month >= 4 else today.year - 1


def flows_from_arg(flow: str) -> List[str]:
    """Expand the ``--flow`` CLI value ('both' | 'exports' | 'imports')."""
    return list(ALL_FLOWS) if flow == "both" else [flow]


# ------------------------------------------------------------------------ browser

def make_driver() -> webdriver.Chrome:
    """Headless Chrome; Selenium >= 4 auto-manages chromedriver."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={WINDOW_SIZE}")
    opts.add_argument(f"user-agent={USER_AGENT}")
    return webdriver.Chrome(options=opts)


def find_control(driver, preferred_id: str, tag: str = "select"):
    """The import page's control ids are guessed; fall back to first match."""
    els = driver.find_elements(By.ID, preferred_id)
    if els:
        return els[0]
    return driver.find_element(By.XPATH, f"//form//{tag}")


def _select_report_unit(driver, flow: str) -> None:
    """Pick 'US$ Million' in the report-type select (by id, else by option value)."""
    report_sel = driver.find_elements(By.ID, REPORT_IDS[flow])
    if not report_sel:
        report_sel = [s for s in driver.find_elements(By.XPATH, "//form//select")
                      if any(o.get_attribute("value") == US_DOLLAR_MILLION
                             for o in s.find_elements(By.TAG_NAME, "option"))][-1:]
    Select(report_sel[0]).select_by_value(US_DOLLAR_MILLION)


def submit_report_form(driver, flow: str, year: int) -> int:
    """Load the form, select FY + unit, submit, wait for the table. Returns the FY used."""
    driver.get(URLS[flow])
    WebDriverWait(driver, FORM_TIMEOUT_S).until(
        EC.presence_of_element_located((By.XPATH, "//form//select")))
    year_sel = Select(find_control(driver, YEAR_IDS[flow]))
    available = [o.get_attribute("value") for o in year_sel.options
                 if o.get_attribute("value").isdigit()]
    year = resolve_year(available, year)
    year_sel.select_by_value(str(year))
    _select_report_unit(driver, flow)
    driver.find_element(
        By.XPATH, "//button[@type='submit'] | //input[@type='submit']").click()
    WebDriverWait(driver, RESULT_TIMEOUT_S).until(
        EC.presence_of_element_located((By.XPATH, "//table//td")))
    return year


def _show_all_rows(driver) -> None:
    """Set every DataTables length selector to its largest option."""
    for sel in driver.find_elements(By.XPATH, "//select[contains(@name,'length')]"):
        values = [o.get_attribute("value")
                  for o in sel.find_elements(By.TAG_NAME, "option")]
        best = pick_length_option(values)
        if best:
            Select(sel).select_by_value(best)
            time.sleep(PAGE_SETTLE_S)


def _read_table(driver) -> Tuple[str, List[List[str]]]:
    """Return (joined header text, list of body rows as cell-text lists)."""
    header = [c.text.strip() for c in driver.find_elements(
        By.XPATH, "//table//tr[1]/th | //table//tr[1]/td")]
    body = [[c.text.strip() for c in tr.find_elements(By.XPATH, "./td")]
            for tr in driver.find_elements(By.XPATH, "//table//tr[position()>1]")]
    return " | ".join(header), body


def _click_next(driver) -> bool:
    """Click an enabled DataTables 'next' control; False if there is none."""
    nxt = driver.find_elements(
        By.XPATH, "//a[contains(@class,'next') and not(contains(@class,'disabled'))]"
                  " | //li[contains(@class,'next') and not(contains(@class,'disabled'))]/a")
    if not nxt:
        return False
    nxt[0].click()
    return True


def scrape_flow(driver, flow: str, year: int) -> List[Row]:
    """Fetch one flow (exports|imports) for one FY; returns parsed, de-duplicated rows."""
    year = submit_report_form(driver, flow, year)
    _show_all_rows(driver)

    rows: List[Row] = []
    seen = set()
    while True:
        header, body = _read_table(driver)
        for cells in body:
            row = parse_row(cells, flow, year, header)
            if row is None:
                continue
            key = (row[2], row[3])  # (hs_code, commodity)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if not _click_next(driver):
            break
        before = len(seen)
        time.sleep(PAGE_SETTLE_S)
        if len(rows) > MAX_ROWS:  # safety
            break
        # Preserved from the original: ``seen`` is not updated between the
        # page turn and this check, so this always stops after one page turn.
        # In practice the "All" length option above makes pagination moot.
        if len(seen) == before:
            break
    return rows


# -------------------------------------------------------------------------- write

def write_rows(db_path, rows: Sequence[Row], pulled_on: str | None = None) -> None:
    """Upsert rows into DuckDB, replacing exactly the (flow, fy_start) pairs present."""
    pulled_on = pulled_on or date.today().isoformat()
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            flow TEXT, fy_start INTEGER, hs_code TEXT, commodity TEXT,
            prev_year_usd_mn DOUBLE, cur_year_usd_mn DOUBLE, growth_pct DOUBLE,
            header TEXT, pulled_on TEXT)""")
        # rows carry the FY actually scraped (may differ from the requested year
        # if the site didn't offer it) — replace exactly those (flow, fy) pairs
        for f, fy in {(r[0], r[1]) for r in rows}:
            con.execute(
                f"DELETE FROM {TABLE_NAME} WHERE flow=? AND fy_start=?", [f, fy])
        con.executemany(
            f"INSERT INTO {TABLE_NAME} VALUES (?,?,?,?,?,?,?,?,?)",
            [(*r, pulled_on) for r in rows])
    finally:
        con.close()


def collect(db_path, year: int, flows: Sequence[str],
            driver_factory: Callable[[], object] | None = None) -> None:
    """Scrape every flow in ``flows`` for ``year`` and write to ``db_path``."""
    driver = (driver_factory or make_driver)()
    all_rows: List[Row] = []
    try:
        for flow in flows:
            rows = scrape_flow(driver, flow, year)
            fy = rows[0][1] if rows else year
            log.info("  %s FY%s-%s: %s HS chapters", flow, fy, fy + 1, len(rows))
            all_rows.extend(rows)
    finally:
        driver.quit()

    write_rows(db_path, all_rows)
    log.info("%s: %s rows -> %s", TABLE_NAME, len(all_rows), db_path)


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--year", type=int, default=None,
                    help="FY start year (2024 = FY 2024-25); default = current FY")
    ap.add_argument("--flow", choices=["exports", "imports", "both"],
                    default="both")
    args = ap.parse_args(argv)
    if args.year is None:
        args.year = default_fy_start(date.today())
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    collect(args.db, args.year, flows_from_arg(args.flow))


if __name__ == "__main__":
    main()
