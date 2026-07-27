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
