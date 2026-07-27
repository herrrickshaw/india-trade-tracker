"""Tests for collectors/worldbank_trade.py — the World Bank trade-indicator collector.

Covers get_json()'s fetch/retry logic (network mocked) and an end-to-end collect()
run with the paged World Bank API mocked and a temporary DuckDB — verifying schema
creation, row mapping, that null observations are skipped, and that the CREATE OR
REPLACE build makes a re-run idempotent (rebuilds the table, never doubles rows).
"""
import io
import json

import duckdb
import pytest

from collectors import worldbank_trade as wb


# --------------------------- constants: sanity ---------------------------

def test_indicators_and_countries_are_nonempty():
    assert wb.COUNTRIES and all(isinstance(c, str) and c for c in wb.COUNTRIES)
    assert wb.INDICATORS and all(isinstance(k, str) for k in wb.INDICATORS)


# --------------------------- get_json() : fetch + retry ---------------------------

class _Resp:
    """Minimal context-manager wrapping JSON bytes, like urlopen's return."""
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


def test_get_json_returns_parsed_payload(monkeypatch):
    monkeypatch.setattr(wb.urllib.request, "urlopen",
                        lambda *a, **k: _Resp([{"pages": 1}, [{"date": "2023"}]]))
    out = wb.get_json("http://x")
    assert out[0]["pages"] == 1 and out[1][0]["date"] == "2023"


def test_get_json_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise ConnectionError("down")

    monkeypatch.setattr(wb.urllib.request, "urlopen", boom)
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)  # no real waiting
    with pytest.raises(ConnectionError):
        wb.get_json("http://x", retries=3)
    assert calls["n"] == 3   # exhausted all retries before raising


def test_get_json_recovers_after_transient_error(monkeypatch):
    seq = [ConnectionError("transient"), _Resp([{"pages": 1}, [{"value": 1}]])]

    def flaky(*a, **k):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(wb.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    assert wb.get_json("http://x", retries=5)[1] == [{"value": 1}]


# --------------------------- collect() : end-to-end (mocked net + temp DB) --------

@pytest.fixture
def mocked_api(monkeypatch):
    """One valued + one null observation per (country, indicator), single page."""
    def fake_get_json(url, retries=3):
        return [
            {"pages": 1, "lastupdated": "2026-01-15"},
            [
                {"date": "2023", "value": 42.5},
                {"date": "2022", "value": None},   # must be skipped
            ],
        ]

    monkeypatch.setattr(wb, "get_json", fake_get_json)
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    return len(wb.COUNTRIES) * len(wb.INDICATORS)   # expected row count


def test_collect_creates_table_and_rows(mocked_api, tmp_path):
    db = tmp_path / "trade.duckdb"
    wb.collect(str(db))

    con = duckdb.connect(str(db))
    cols = [r[1] for r in con.execute(
        "PRAGMA table_info('worldbank_trade')").fetchall()]
    assert {"country", "indicator_code", "indicator", "year", "value",
            "source_last_updated"} == set(cols)
    count = con.execute("SELECT COUNT(*) FROM worldbank_trade").fetchone()[0]
    con.close()
    assert count == mocked_api   # one row per (country, indicator); nulls skipped


def test_collect_maps_row_fields(mocked_api, tmp_path):
    db = tmp_path / "trade.duckdb"
    wb.collect(str(db))
    con = duckdb.connect(str(db))
    row = con.execute(
        "SELECT year, value, source_last_updated FROM worldbank_trade LIMIT 1"
    ).fetchone()
    con.close()
    assert row[0] == 2023               # date coerced to INTEGER year
    assert row[1] == 42.5               # value stored as DOUBLE
    assert row[2] == "2026-01-15"       # metadata lastupdated carried through


def test_collect_skips_null_observations(mocked_api, tmp_path):
    db = tmp_path / "trade.duckdb"
    wb.collect(str(db))
    con = duckdb.connect(str(db))
    # the 2022 obs had value None and must not have been inserted
    null_years = con.execute(
        "SELECT COUNT(*) FROM worldbank_trade WHERE year = 2022").fetchone()[0]
    con.close()
    assert null_years == 0


def test_collect_is_idempotent(mocked_api, tmp_path):
    db = tmp_path / "trade.duckdb"
    wb.collect(str(db))
    wb.collect(str(db))   # CREATE OR REPLACE => rebuild, not append

    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM worldbank_trade").fetchone()[0]
    con.close()
    assert count == mocked_api   # still one row per (country, indicator), not doubled
