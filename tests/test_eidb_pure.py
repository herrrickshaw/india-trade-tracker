"""Unit tests for the browser-free helpers in collectors/eidb_trade.py.

No Selenium driver is ever created: the pure parsers are called directly, and
the injected ``driver_factory`` seam of collect() is exercised with a stub.
"""
from datetime import date

import duckdb

from collectors import eidb_trade as eidb


# --------------------------- resolve_year() ---------------------------

def test_resolve_year_keeps_requested_year_when_offered():
    assert eidb.resolve_year(["2022", "2023", "2024"], 2023) == 2023


def test_resolve_year_falls_back_to_newest_offered():
    assert eidb.resolve_year(["2022", "2023", "2024"], 2030) == 2024
    assert eidb.resolve_year(["2024", "2022"], 1999) == 2024  # max by int, not order


# --------------------------- pick_length_option() ---------------------------

def test_pick_length_option_prefers_all():
    assert eidb.pick_length_option(["10", "25", "-1", "100"]) == "-1"


def test_pick_length_option_takes_largest_numeric():
    assert eidb.pick_length_option(["10", "25", "100"]) == "100"


def test_pick_length_option_ignores_non_numeric_and_handles_empty():
    assert eidb.pick_length_option(["abc", "10"]) == "10"
    assert eidb.pick_length_option([]) is None
    assert eidb.pick_length_option(["abc"]) is None


# --------------------------- parse_row() ---------------------------

FULL = ["1", "01", "Live animals", "1,234.5", "x", "1,500.0", "y", "21.5"]


def test_parse_row_maps_full_row():
    assert eidb.parse_row(FULL, "exports", 2024, "hdr") == (
        "exports", 2024, "01", "Live animals", 1234.5, 1500.0, 21.5, "hdr")


def test_parse_row_tolerates_short_rows():
    # 4 cells: previous-year value only, cur/growth columns absent -> None
    assert eidb.parse_row(["1", "02", "Meat", "5"], "imports", 2023, "h") == (
        "imports", 2023, "02", "Meat", 5.0, None, None, "h")
    # 6 cells: cur present, growth absent
    row = eidb.parse_row(["1", "02", "Meat", "5", "", "4"], "imports", 2023, "h")
    assert row[4:7] == (5.0, 4.0, None)


def test_parse_row_rejects_non_data_rows():
    assert eidb.parse_row(["Total", "", "", "99"], "exports", 2024, "h") is None  # blank HS
    assert eidb.parse_row(["1", "01"], "exports", 2024, "h") is None             # too short
    assert eidb.parse_row([], "exports", 2024, "h") is None


# --------------------------- default_fy_start() / flows_from_arg() ---------------------------

def test_default_fy_start_rolls_over_in_april():
    assert eidb.default_fy_start(date(2026, 3, 31)) == 2025
    assert eidb.default_fy_start(date(2026, 4, 1)) == 2026
    assert eidb.default_fy_start(date(2026, 12, 15)) == 2026


def test_flows_from_arg():
    assert eidb.flows_from_arg("both") == ["exports", "imports"]
    assert eidb.flows_from_arg("exports") == ["exports"]
    assert eidb.flows_from_arg("imports") == ["imports"]


# --------------------------- write_rows() / collect() seams ---------------------------

def test_write_rows_stamps_explicit_pulled_on(tmp_path):
    db = tmp_path / "t.duckdb"
    eidb.write_rows(db, [("exports", 2024, "01", "Live animals", 1.0, 2.0, 100.0, "h")],
                    pulled_on="2026-01-01")
    con = duckdb.connect(str(db))
    assert con.execute("SELECT pulled_on FROM eidb_chapter_trade").fetchone() == ("2026-01-01",)
    con.close()


def test_collect_uses_injected_driver_factory_and_quits(monkeypatch, tmp_path):
    events = []

    class _Driver:
        def quit(self):
            events.append("quit")

    monkeypatch.setattr(eidb, "scrape_flow",
                        lambda driver, flow, year: [(flow, year, "01", "X", 1.0, 2.0, 3.0, "h")])
    eidb.collect(tmp_path / "t.duckdb", 2024, ["exports"],
                 driver_factory=lambda: (events.append("made"), _Driver())[1])
    assert events == ["made", "quit"]


def test_collect_quits_driver_even_when_scrape_fails(monkeypatch, tmp_path):
    quit_calls = []

    class _Driver:
        def quit(self):
            quit_calls.append(1)

    def boom(driver, flow, year):
        raise RuntimeError("site down")

    monkeypatch.setattr(eidb, "scrape_flow", boom)
    try:
        eidb.collect(tmp_path / "t.duckdb", 2024, ["exports"], driver_factory=_Driver)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError to propagate")
    assert quit_calls == [1]


def test_main_parses_flags_and_calls_collect(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(eidb, "collect",
                        lambda db, year, flows, driver_factory=None: captured.update(
                            db=db, year=year, flows=flows))
    eidb.main(["--db", str(tmp_path / "x.duckdb"), "--year", "2022", "--flow", "imports"])
    assert captured == {"db": str(tmp_path / "x.duckdb"), "year": 2022, "flows": ["imports"]}


def test_main_defaults_year_to_current_fy(monkeypatch):
    captured = {}
    monkeypatch.setattr(eidb, "collect",
                        lambda db, year, flows, driver_factory=None: captured.update(year=year))
    eidb.main([])
    assert captured["year"] == eidb.default_fy_start(date.today())
