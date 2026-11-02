"""Unit tests for the network-free helpers in collectors/worldbank_trade.py."""
import duckdb

from collectors import worldbank_trade as wb

# --------------------------- indicator_url() ---------------------------

def test_indicator_url_shape():
    url = wb.indicator_url("IND", "NE.EXP.GNFS.ZS", 2)
    assert url.startswith("https://api.worldbank.org/v2/country/IND/indicator/NE.EXP.GNFS.ZS?")
    assert "format=json" in url and f"per_page={wb.PER_PAGE}" in url and "page=2" in url


# --------------------------- parse_page() ---------------------------

PAGE = [
    {"page": 1, "pages": 1, "lastupdated": "2026-01-15"},
    [{"date": "2023", "value": 42.5}, {"date": "2022", "value": None},
     {"date": "2021", "value": "7"}],
]


def test_parse_page_maps_rows_and_skips_nulls():
    rows = wb.parse_page(PAGE, "IND", "X", "name")
    assert rows == [("IND", "X", "name", 2023, 42.5, "2026-01-15"),
                    ("IND", "X", "name", 2021, 7.0, "2026-01-15")]


def test_parse_page_handles_empty_or_malformed_payloads():
    assert wb.parse_page(None, "IND", "X", "n") == []
    assert wb.parse_page([], "IND", "X", "n") == []
    assert wb.parse_page([{"message": "no data"}], "IND", "X", "n") == []
    assert wb.parse_page([{"pages": 1}, []], "IND", "X", "n") == []


# --------------------------- has_more_pages() ---------------------------

def test_has_more_pages():
    assert wb.has_more_pages([{"pages": 3}, [{}]], 1) is True
    assert wb.has_more_pages([{"pages": 3}, [{}]], 3) is False
    assert wb.has_more_pages([{}, [{}]], 1) is False          # pages defaults to 1
    assert wb.has_more_pages([{"pages": 3}, []], 1) is False  # empty page ends the loop
    assert wb.has_more_pages(None, 1) is False


# --------------------------- fetch_indicator() : paging with injected fetch -------------

def test_fetch_indicator_walks_all_pages(monkeypatch):
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    pages = {
        1: [{"pages": 2, "lastupdated": "u"}, [{"date": "2000", "value": 1}]],
        2: [{"pages": 2, "lastupdated": "u"}, [{"date": "2001", "value": 2}]],
    }
    urls = []

    def fetch(url):
        urls.append(url)
        return pages[int(url.rsplit("page=", 1)[1])]

    rows = wb.fetch_indicator("IND", "X", "n", fetch=fetch)
    assert [r[3] for r in rows] == [2000, 2001]
    assert len(urls) == 2 and "page=1" in urls[0] and "page=2" in urls[1]


def test_fetch_panel_covers_every_country_indicator_pair(monkeypatch):
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    calls = []

    def fetch(url):
        calls.append(url)
        return [{"pages": 1}, [{"date": "2020", "value": 1}]]

    rows = wb.fetch_panel(["IND", "CHN"], {"A": "a", "B": "b"}, fetch=fetch)
    assert len(rows) == 4 and len(calls) == 4
    assert {(r[0], r[1]) for r in rows} == {
        ("IND", "A"), ("IND", "B"), ("CHN", "A"), ("CHN", "B")}


# --------------------------- write_rows() / main() ---------------------------

def test_write_rows_rebuilds_table(tmp_path):
    db = tmp_path / "t.duckdb"
    wb.write_rows(db, [("IND", "X", "n", 2020, 1.0, "u")])
    wb.write_rows(db, [("IND", "X", "n", 2021, 2.0, "u")])
    con = duckdb.connect(str(db))
    assert con.execute("SELECT year FROM worldbank_trade").fetchall() == [(2021,)]
    con.close()


def test_main_passes_db_flag(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(wb, "collect", lambda db, fetch=None: captured.update(db=db))
    wb.main(["--db", str(tmp_path / "x.duckdb")])
    assert captured["db"] == str(tmp_path / "x.duckdb")


def test_main_defaults_db_to_repo_data_dir(monkeypatch):
    captured = {}
    monkeypatch.setattr(wb, "collect", lambda db, fetch=None: captured.update(db=db))
    wb.main([])
    assert captured["db"].endswith("data/trade.duckdb")
