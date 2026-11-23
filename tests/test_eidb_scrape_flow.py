"""scrape_flow / make_driver / collect / CLI of collectors/eidb_trade.py with a scripted fake Selenium driver.

No browser, no network: the page is a tiny in-memory model; selenium's Select and WebDriverWait are replaced."""
import datetime

import duckdb
import pytest
from selenium.webdriver.common.by import By

from collectors import eidb_trade as eidb
from _mainblock import run_main_block

SUBMIT = "//button[@type='submit'] | //input[@type='submit']"
FORM_SELECT = "//form//select"
LENGTH_SEL = "//select[contains(@name,'length')]"
HEADER = "//table//tr[1]/th | //table//tr[1]/td"
ROWS = "//table//tr[position()>1]"
NEXT = ("//a[contains(@class,'next') and not(contains(@class,'disabled'))]"
        " | //li[contains(@class,'next') and not(contains(@class,'disabled'))]/a")


class El:
    def __init__(self, text="", value=None, options=None, on_click=None):
        self.text = text
        self.value = value
        self.options = options or []
        self.selected = []
        self.clicks = 0
        self.on_click = on_click
        self.cells = []

    def get_attribute(self, name):
        return self.value if name == "value" else ""

    def find_elements(self, by, value):
        if (by, value) == (By.TAG_NAME, "option"):
            return self.options
        if (by, value) == (By.XPATH, "./td"):
            return self.cells
        return []

    def click(self):
        self.clicks += 1
        if self.on_click:
            self.on_click()


def opts(*values):
    return [El(value=v) for v in values]


class FakeSelect:
    def __init__(self, el):
        self.el = el
        self.options = el.options

    def select_by_value(self, v):
        self.el.selected.append(v)


class FakeWait:
    timeouts = []

    def __init__(self, driver, timeout):
        FakeWait.timeouts.append(timeout)

    def until(self, cond):
        return True


class Page:
    """Pages of table rows (each row is a list of cell texts); next-button advances the page index."""

    def __init__(self, pages, header=("#", "HS", "Commodity", "py", "x", "cy", "y", "gr")):
        self.pages, self.i, self.header = pages, 0, header
        self.next_clicks = 0

    def advance(self):
        self.next_clicks += 1
        self.i += 1


class Driver:
    def __init__(self, flow="exports", year_values=("2023", "2024", ""), page=None, report_by_id=True,
                 length_values=None, fallback_selects=None):
        self.visited = []
        self.page = page or Page([[["1", "01", "Live animals", "10", "", "12", "", "20"]]])
        self.year_el = El(options=opts(*year_values))
        self.report_el = El()
        self.submit = El()
        self.flow = flow
        self.report_by_id = report_by_id
        self.length_els = [El(options=opts(*v)) for v in (length_values or [])]
        self.fallback = fallback_selects if fallback_selects is not None else []
        self.next_btn = El(on_click=self.page.advance)

    def get(self, url):
        self.visited.append(url)

    def find_elements(self, by, value):
        if by == By.ID:
            if value == eidb.YEAR_IDS[self.flow]:
                return [self.year_el]
            if value == eidb.REPORT_IDS[self.flow]:
                return [self.report_el] if self.report_by_id else []
            return []
        if value == FORM_SELECT:
            return self.fallback
        if value == LENGTH_SEL:
            return self.length_els
        if value == HEADER:
            return [El(text=f" {h} ") for h in self.page.header]
        if value == ROWS:
            rows = []
            for cells in self.page.pages[self.page.i]:
                tr = El()
                tr.cells = [El(text=f"  {c} ") for c in cells]
                rows.append(tr)
            return rows
        if value == NEXT:
            return [self.next_btn] if self.page.i < len(self.page.pages) - 1 else []
        raise AssertionError(f"unexpected lookup {by!r} {value!r}")

    def find_element(self, by, value):
        if value == SUBMIT:
            return self.submit
        if value == FORM_SELECT:
            return self.year_el
        raise AssertionError(f"unexpected lookup {value!r}")


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeWait.timeouts = []
    monkeypatch.setattr(eidb, "Select", FakeSelect)
    monkeypatch.setattr(eidb, "WebDriverWait", FakeWait)
    slept = []
    monkeypatch.setattr(eidb.time, "sleep", slept.append)
    return slept


# ---------------------------------- year / report selection ----------------------------------

def test_requested_year_is_selected_when_offered():
    d = Driver(year_values=("2022", "2023", "2024"))
    eidb.scrape_flow(d, "exports", 2023)
    assert d.year_el.selected == ["2023"]
    assert d.visited == [eidb.URLS["exports"]]


def test_missing_year_falls_back_to_newest_numeric_option_and_rows_carry_it():
    d = Driver(year_values=("", "2009", "2024", "2010", "All"))     # numeric max, not lexical; junk ignored
    rows = eidb.scrape_flow(d, "exports", 2030)
    assert d.year_el.selected == ["2024"]
    assert rows[0][1] == 2024 and isinstance(rows[0][1], int)


def test_imports_use_their_own_url_and_control_ids():
    d = Driver(flow="imports")
    eidb.scrape_flow(d, "imports", 2024)
    assert d.visited == [eidb.URLS["imports"]]
    assert d.report_el.selected == ["2"] and d.year_el.selected == ["2024"]


def test_report_type_is_us_dollar_million_and_form_submitted():
    d = Driver()
    eidb.scrape_flow(d, "exports", 2024)
    assert d.report_el.selected == [eidb.US_DOLLAR_MILLION] == ["2"]
    assert d.submit.clicks == 1
    assert FakeWait.timeouts == [30, 90]


def test_report_select_fallback_picks_last_select_offering_value_two():
    a, b, c = El(options=opts("1", "2")), El(options=opts("1", "3")), El(options=opts("2", "5"))
    d = Driver(report_by_id=False, fallback_selects=[a, b, c])
    eidb.scrape_flow(d, "exports", 2024)
    assert c.selected == ["2"] and a.selected == [] and b.selected == []


# ---------------------------------- row parsing ----------------------------------

def test_rows_are_mapped_stripped_and_deduplicated():
    page = Page([[
        ["1", "01", "Live animals", "1,000.5", "", "1,200", "", "20.0"],
        ["2", "02", "Meat", "5", "", "N/A", "", ""],
        ["3", "01", "Live animals", "9", "", "9", "", "9"],       # duplicate (hs, commodity): dropped
        ["4", "01", "Different name", "7", "", "8", "", "1"],     # same hs, other commodity: kept
        ["5", "", "No code", "1", "", "1", "", "1"],               # empty hs: skipped
        ["6", "03", "Short row"],                                  # < 4 cells: skipped
        ["7", "04", "Exactly four", "3"],                          # == 4 cells: kept, optional cols None
        ["8", "05", "Six cells", "3", "x", "4"],                   # has col 5, not col 7
    ]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    hdr = "# | HS | Commodity | py | x | cy | y | gr"
    assert rows == [
        ("exports", 2024, "01", "Live animals", 1000.5, 1200.0, 20.0, hdr),
        ("exports", 2024, "02", "Meat", 5.0, None, None, hdr),
        ("exports", 2024, "01", "Different name", 7.0, 8.0, 1.0, hdr),
        ("exports", 2024, "04", "Exactly four", 3.0, None, None, hdr),
        ("exports", 2024, "05", "Six cells", 3.0, 4.0, None, hdr),
    ]


# ---------------------------------- page length selector ----------------------------------

def test_length_selector_prefers_all_rows_value_and_waits(fakes):
    d = Driver(length_values=[("10", "25", "-1")])
    eidb.scrape_flow(d, "exports", 2024)
    assert d.length_els[0].selected == ["-1"] and fakes == [2]


def test_length_selector_falls_back_to_largest_numeric(fakes):
    d = Driver(length_values=[("10", "100", "25", "all")])
    eidb.scrape_flow(d, "exports", 2024)
    assert d.length_els[0].selected == ["100"]


def test_length_selector_without_numeric_options_is_left_alone(fakes):
    d = Driver(length_values=[("all", "")])
    eidb.scrape_flow(d, "exports", 2024)
    assert d.length_els[0].selected == [] and fakes == []


# ---------------------------------- pagination ----------------------------------

def row(i):
    return [str(i), f"{i:02d}", f"c{i}", "1", "", "2", "", "3"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BUG eidb_trade.py scrape_flow: `before = len(seen)` is taken after the page is read and "
                          "compared right after clicking 'next' (before any new page is read), so "
                          "`len(seen) == before` is always true and pagination always stops after the first click: "
                          "rows beyond page 1 are never collected")
def test_pagination_collects_all_pages_with_a_sleep_per_turn(fakes):
    page = Page([[row(1), row(2)], [row(3)], [row(4), row(5)]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    assert [r[2] for r in rows] == ["01", "02", "03", "04", "05"]
    assert page.next_clicks == 2 and fakes == [2, 2]


def test_pagination_turns_the_page_once_then_sleeps(fakes):
    page = Page([[row(1), row(2)], [row(3)]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    assert page.next_clicks == 1 and fakes == [2]
    assert [r[2] for r in rows][:2] == ["01", "02"]


def test_pagination_does_not_run_past_a_repeating_page():
    # page 2 repeats page 1: whatever the guard's exact timing, the third page must never be reached
    page = Page([[row(1)], [row(1)], [row(2)]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    assert [r[2] for r in rows] == ["01"] and page.next_clicks == 1


def test_pagination_safety_cap_stops_after_5000_rows():
    big = [row(i) for i in range(1, 5002)]                # 5001 rows > 5000
    page = Page([big, [row(9999)]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    assert len(rows) == 5001 and page.next_clicks == 1


def test_no_next_link_means_single_page():
    page = Page([[row(1)]])
    rows = eidb.scrape_flow(Driver(page=page), "exports", 2024)
    assert len(rows) == 1 and page.next_clicks == 0


# ---------------------------------- make_driver ----------------------------------

def test_make_driver_builds_headless_chrome(monkeypatch):
    made = {}

    class Opts:
        def __init__(self):
            self.args = []
            made["opts"] = self

        def add_argument(self, a):
            self.args.append(a)

    class FakeWebdriver:
        ChromeOptions = Opts

        @staticmethod
        def Chrome(options):
            made["chrome_opts"] = options
            return "DRIVER"
    monkeypatch.setattr(eidb, "webdriver", FakeWebdriver)
    assert eidb.make_driver() == "DRIVER"
    args = made["chrome_opts"].args
    assert "--headless=new" in args and "--window-size=1400,1000" in args
    ua = [a for a in args if a.startswith("user-agent=")]
    assert len(ua) == 1 and "Mozilla/5.0" in ua[0] and "Chrome/126.0" in ua[0]


# ---------------------------------- collect ----------------------------------

def test_collect_quits_driver_even_when_scrape_fails(monkeypatch, tmp_path):
    state = {"quit": 0}

    class D:
        def quit(self):
            state["quit"] += 1
    monkeypatch.setattr(eidb, "make_driver", lambda: D())

    def boom(driver, flow, year):
        raise RuntimeError("site down")
    monkeypatch.setattr(eidb, "scrape_flow", boom)
    with pytest.raises(RuntimeError, match="site down"):
        eidb.collect(str(tmp_path / "t.duckdb"), 2024, ["exports"])
    assert state["quit"] == 1
    assert not (tmp_path / "t.duckdb").exists()          # nothing written when the scrape failed


def test_collect_prints_chapter_counts_and_scraped_fy(monkeypatch, tmp_path, capsys):
    class D:
        def quit(self):
            pass
    monkeypatch.setattr(eidb, "make_driver", lambda: D())
    # site did not offer 2025: rows carry FY 2024
    monkeypatch.setattr(eidb, "scrape_flow", lambda d, flow, year: [(flow, 2024, "01", "A", 1.0, 2.0, 3.0, "h")])
    db = tmp_path / "t.duckdb"
    eidb.collect(str(db), 2025, ["exports", "imports"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "  exports FY2024-2025: 1 HS chapters"
    assert out[1] == "  imports FY2024-2025: 1 HS chapters"
    assert out[2] == f"eidb_chapter_trade: 2 rows -> {db}"
    con = duckdb.connect(str(db))
    assert {r[0] for r in con.execute("SELECT DISTINCT fy_start FROM eidb_chapter_trade").fetchall()} == {2024}
    con.close()


def test_collect_reports_zero_chapters_for_an_empty_flow(monkeypatch, tmp_path, capsys):
    class D:
        def quit(self):
            pass
    monkeypatch.setattr(eidb, "make_driver", lambda: D())
    monkeypatch.setattr(eidb, "scrape_flow", lambda d, flow, year: [] if flow == "exports" else
                        [(flow, year, "01", "A", 1.0, 2.0, 3.0, "h")])
    db = tmp_path / "t.duckdb"
    eidb.collect(str(db), 2024, ["exports", "imports"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "  exports FY2024-2025: 0 HS chapters"      # no rows: falls back to the requested year
    assert out[1] == "  imports FY2024-2025: 1 HS chapters"
    con = duckdb.connect(str(db))
    assert con.execute("SELECT flow FROM eidb_chapter_trade").fetchall() == [("imports",)]
    con.close()


@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="BUG eidb_trade.py collect: when every flow scrapes zero rows, "
                          "con.executemany(INSERT, []) raises InvalidInputException (duckdb needs a non-empty "
                          "parameter list) so the run crashes instead of recording an empty result")
def test_collect_with_no_rows_at_all_does_not_crash(monkeypatch, tmp_path):
    class D:
        def quit(self):
            pass
    monkeypatch.setattr(eidb, "make_driver", lambda: D())
    monkeypatch.setattr(eidb, "scrape_flow", lambda d, flow, year: [])
    db = tmp_path / "t.duckdb"
    eidb.collect(str(db), 2024, ["exports"])
    con = duckdb.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM eidb_chapter_trade").fetchone()[0] == 0
    con.close()


def test_collect_stamps_todays_date(monkeypatch, tmp_path):
    class D:
        def quit(self):
            pass

    class FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 21)
    monkeypatch.setattr(eidb, "date", FakeDate)
    monkeypatch.setattr(eidb, "make_driver", lambda: D())
    monkeypatch.setattr(eidb, "scrape_flow", lambda d, flow, year: [(flow, year, "01", "A", 1.0, 2.0, 3.0, "h")])
    db = tmp_path / "t.duckdb"
    eidb.collect(str(db), 2024, ["exports"])
    con = duckdb.connect(str(db))
    assert con.execute("SELECT pulled_on, growth_pct, prev_year_usd_mn, header FROM eidb_chapter_trade").fetchone() == (
        "2026-07-21", 3.0, 1.0, "h")
    con.close()


# ---------------------------------- CLI block ----------------------------------

def _cli(monkeypatch, argv, today):
    calls = []

    class FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(*today)
    run_main_block(eidb, argv, collect=lambda *a: calls.append(a), date=FakeDate)
    return calls


@pytest.mark.parametrize("today,year", [((2026, 4, 1), 2026), ((2026, 3, 31), 2025), ((2026, 12, 31), 2026),
                                        ((2026, 1, 1), 2025)])
def test_cli_default_year_is_current_financial_year(monkeypatch, today, year):
    (db, y, flows), = _cli(monkeypatch, [], today)
    assert y == year and flows == ["exports", "imports"]
    assert db.endswith("data/trade.duckdb")


def test_cli_explicit_year_flow_and_db(monkeypatch):
    (db, y, flows), = _cli(monkeypatch, ["--year", "2019", "--flow", "imports", "--db", "x.db"], (2026, 5, 5))
    assert (db, y, flows) == ("x.db", 2019, ["imports"])
    (_, _, flows), = _cli(monkeypatch, ["--flow", "exports"], (2026, 5, 5))
    assert flows == ["exports"]


def test_cli_rejects_unknown_flow(monkeypatch):
    with pytest.raises(SystemExit):
        _cli(monkeypatch, ["--flow", "re-exports"], (2026, 5, 5))
