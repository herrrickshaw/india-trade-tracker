"""Tests for collectors/eidb_trade.py — the DGFT/EIDB chapter-wise trade collector.

The live scrape is Selenium-driven, so we never launch a browser: the pure numeric
parser (_num) and the control-locator fallback (find_control, driven by a fake
driver) are tested directly, and collect() is exercised end-to-end with make_driver
and scrape_flow mocked out and a temporary DuckDB — verifying schema creation, row
mapping (incl. the appended pulled_on), and the per-(flow, fy) delete/insert that
makes a same-day re-run idempotent (replaces the FY's rows, never doubles them).
"""
import duckdb
import pytest

from collectors import eidb_trade as eidb


# --------------------------- _num() : pure parser ---------------------------

def test_num_parses_comma_grouped_number():
    assert eidb._num("1,234.5") == 1234.5
    assert eidb._num("42") == 42.0


def test_num_returns_none_on_junk():
    assert eidb._num("N/A") is None
    assert eidb._num("") is None
    assert eidb._num(None) is None          # AttributeError path


# --------------------------- find_control() : locator fallback ---------------------------

class _FakeDriver:
    """Records lookups; returns preset results for find_elements / find_element."""
    def __init__(self, by_id=None, xpath_el="XPATH_MATCH"):
        self._by_id = by_id or []
        self._xpath_el = xpath_el
        self.xpath_called_with = None

    def find_elements(self, by, value):
        return self._by_id

    def find_element(self, by, value):
        self.xpath_called_with = value
        return self._xpath_el


def test_find_control_prefers_matching_id():
    d = _FakeDriver(by_id=["PREFERRED"])
    assert eidb.find_control(d, "SomeId") == "PREFERRED"
    assert d.xpath_called_with is None      # never fell back


def test_find_control_falls_back_to_xpath():
    d = _FakeDriver(by_id=[])                # id not present
    assert eidb.find_control(d, "MissingId", tag="select") == "XPATH_MATCH"
    assert "select" in d.xpath_called_with   # fallback xpath targets the tag


# --------------------------- collect() : end-to-end (mocked scrape + temp DB) --------

def _fake_rows(flow, fy):
    """Two HS chapters for a given flow/FY, shaped exactly like scrape_flow output."""
    return [
        (flow, fy, "01", "Live animals", 10.0, 12.0, 20.0, "hdr"),
        (flow, fy, "02", "Meat", 5.0, 4.0, -20.0, "hdr"),
    ]


@pytest.fixture
def mocked_scrape(monkeypatch):
    """Stub the browser: make_driver returns a dummy, scrape_flow returns fixed rows."""
    class _Dummy:
        def quit(self):
            pass

    monkeypatch.setattr(eidb, "make_driver", lambda: _Dummy())
    monkeypatch.setattr(eidb, "scrape_flow",
                        lambda driver, flow, year: _fake_rows(flow, year))
    monkeypatch.setattr(eidb.time, "sleep", lambda *_: None)


def test_collect_creates_table_and_rows(mocked_scrape, tmp_path):
    db = tmp_path / "trade.duckdb"
    eidb.collect(str(db), year=2024, flows=["exports", "imports"])

    con = duckdb.connect(str(db))
    cols = [r[1] for r in con.execute(
        "PRAGMA table_info('eidb_chapter_trade')").fetchall()]
    assert {"flow", "fy_start", "hs_code", "commodity",
            "cur_year_usd_mn", "pulled_on"} <= set(cols)
    count = con.execute("SELECT COUNT(*) FROM eidb_chapter_trade").fetchone()[0]
    con.close()
    assert count == 4   # 2 chapters x 2 flows


def test_collect_maps_row_fields_and_stamps_pulled_on(mocked_scrape, tmp_path):
    db = tmp_path / "trade.duckdb"
    eidb.collect(str(db), year=2024, flows=["exports"])
    con = duckdb.connect(str(db))
    row = con.execute(
        "SELECT flow, fy_start, hs_code, commodity, cur_year_usd_mn, pulled_on "
        "FROM eidb_chapter_trade WHERE hs_code = '01'").fetchone()
    con.close()
    assert row[:5] == ("exports", 2024, "01", "Live animals", 12.0)
    assert row[5] and len(row[5]) == 10   # ISO date string 'YYYY-MM-DD'


def test_collect_is_idempotent_per_flow_fy(mocked_scrape, tmp_path):
    db = tmp_path / "trade.duckdb"
    eidb.collect(str(db), year=2024, flows=["exports", "imports"])
    eidb.collect(str(db), year=2024, flows=["exports", "imports"])  # same FY re-run

    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM eidb_chapter_trade").fetchone()[0]
    con.close()
    assert count == 4   # replaced the (flow, fy) rows, not doubled to 8


def test_collect_keeps_other_fy_when_replacing(mocked_scrape, tmp_path):
    db = tmp_path / "trade.duckdb"
    eidb.collect(str(db), year=2023, flows=["exports"])   # seed FY2023
    eidb.collect(str(db), year=2024, flows=["exports"])   # add FY2024, keep 2023

    con = duckdb.connect(str(db))
    years = {r[0] for r in con.execute(
        "SELECT DISTINCT fy_start FROM eidb_chapter_trade").fetchall()}
    con.close()
    assert years == {2023, 2024}   # delete is scoped to the FY being written


# --------------------------- scrape_flow() : table parsing over a fake DOM ----------
#
# A static HTML page shaped like the TradeStat result (a DataTable with thead /
# tbody / tfoot, the "India's Total Export" row in tfoot, colspan'd label) is
# parsed with lxml and served through a minimal fake WebDriver so the real
# XPath expressions in scrape_flow are evaluated. No browser, no network.

lxml_html = pytest.importorskip("lxml.html")

_RESULT_PAGE = """
<html><body>
<form>
  <select id="EidbYearCwe"><option value="">--</option><option value="2023">2023-24</option>
          <option value="2024">2024-25</option></select>
  <select id="Eidb_ReportCwe"><option value="1">Rs lacs</option><option value="2">US$ mn</option></select>
  <button type="submit">Submit</button>
</form>
<table>
 <thead><tr><th>S.No.</th><th>HSCode</th><th>Commodity</th><th>2023 - 2024</th><th>%Share</th>
            <th>2024 - 2025</th><th>%Share</th><th>%Growth</th></tr></thead>
 <tbody>
  <tr><td>1</td><td>01</td><td>LIVE ANIMALS.</td><td>12.37</td><td>0.0028</td><td>19.70</td><td>0.0045</td><td>59.17</td></tr>
  <tr><td>2</td><td>02</td><td>MEAT AND EDIBLE MEAT OFFAL.</td><td>3,832.60</td><td>0.88</td><td>4,159.00</td><td>0.95</td><td>8.52</td></tr>
  <tr><td>3</td><td>03</td><td>FISH.</td><td>6,124.21</td><td>1.4</td><td>6,296.53</td><td>1.44</td><td>2.81</td></tr>
 </tbody>
 <tfoot>
  <tr><td colspan="3">India's Total Export</td><td>9,969.18</td><td></td><td>10,475.23</td><td></td><td>5.08</td></tr>
 </tfoot>
</table>
</body></html>
"""


class _El:
    def __init__(self, node):
        self.node = node

    @property
    def text(self):
        return self.node.text_content()

    @property
    def tag_name(self):
        return self.node.tag

    def get_attribute(self, name):
        return self.node.get(name)

    def find_elements(self, by, value):
        if by == "tag name":
            return [_El(n) for n in self.node.iter(value)]
        return [_El(n) for n in self.node.xpath(value)]

    def click(self):
        pass


class _DomDriver(_El):
    def __init__(self, html):
        super().__init__(lxml_html.fromstring(html))

    def get(self, url):
        pass

    def find_element(self, by, value):
        return self.find_elements(by, value)[0]

    def find_elements(self, by, value):
        if by == "id":
            return [_El(n) for n in self.node.xpath(f"//*[@id='{value}']")]
        return super().find_elements(by, value)


class _FakeSelect:
    def __init__(self, el):
        self.options = el.find_elements("tag name", "option")

    def select_by_value(self, v):
        pass


class _NoWait:
    def __init__(self, *a, **k):
        pass

    def until(self, *_):
        return True


@pytest.fixture
def dom_driver(monkeypatch):
    monkeypatch.setattr(eidb, "Select", _FakeSelect)
    monkeypatch.setattr(eidb, "WebDriverWait", _NoWait)
    monkeypatch.setattr(eidb.time, "sleep", lambda *_: None)
    return _DomDriver(_RESULT_PAGE)


def test_scrape_flow_keeps_first_hs_chapter(dom_driver):
    """//table//tr[position()>1] is evaluated per parent, so it skipped the first
    <tbody> row: chapter 01 (LIVE ANIMALS) was missing from every scrape (the
    committed data/eidb_chapter_trade_snapshot.csv has 97 chapters, no '01')."""
    rows = eidb.scrape_flow(dom_driver, "exports", 2024)
    assert [r[2] for r in rows] == ["01", "02", "03"]


def test_scrape_flow_excludes_national_total_row(dom_driver):
    rows = eidb.scrape_flow(dom_driver, "exports", 2024)
    assert all(r[2].isdigit() for r in rows)
    assert sum(r[5] for r in rows) == pytest.approx(10475.23)


def test_scrape_flow_maps_columns_and_header(dom_driver):
    rows = eidb.scrape_flow(dom_driver, "exports", 2024)
    meat = next(r for r in rows if r[2] == "02")
    assert meat[:7] == ("exports", 2024, "02", "MEAT AND EDIBLE MEAT OFFAL.", 3832.6, 4159.0, 8.52)
    assert meat[7] == ("S.No. | HSCode | Commodity | 2023 - 2024 | %Share | "
                       "2024 - 2025 | %Share | %Growth")


def test_scrape_flow_falls_back_to_newest_offered_year(dom_driver):
    rows = eidb.scrape_flow(dom_driver, "exports", 2030)
    assert {r[1] for r in rows} == {2024}
