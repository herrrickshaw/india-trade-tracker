"""Tests for niryat_bilateral, niryat_hs8 and comtrade_chapters collectors
(network mocked, temporary DuckDB)."""
import duckdb

from collectors import comtrade_chapters as ct
from collectors import niryat_bilateral as nb
from collectors import niryat_hs8 as nh


def _series(vals):
    return [{"value": v} for v in vals]


def test_niryat_bilateral_parse_and_collect(monkeypatch, tmp_path):
    payload = [[{"label": "2024"}, {"label": "2025"}], _series([1.0, 2.0]),
               _series([3.0, 4.0]), _series([-2.0, -2.0])]
    monkeypatch.setattr(nb, "get_json", lambda url, retries=3: payload)
    monkeypatch.setattr(nb.time, "sleep", lambda *_: None)
    assert nb.parse(payload, "USA", "yearly")[0] == ("USA", "yearly", "2024", 1.0, 3.0, -2.0)
    db = tmp_path / "t.duckdb"
    nb.collect(str(db))
    nb.collect(str(db))  # idempotent
    n = duckdb.connect(str(db)).execute("SELECT COUNT(*) FROM niryat_bilateral").fetchone()[0]
    assert n == len(nb.PARTNERS) * 2 * 2


def test_niryat_hs8_parse_and_collect(monkeypatch, tmp_path):
    payload = {"label": [{"label": "71081210: GOLD, 99.5 PERCENT"}, {"label": "29339990: OTHER"}],
               "value": [{"value": "20.5"}, {"value": None}]}
    assert nh.parse(payload, "CHE", "Export", "2025-26") == [
        ("CHE", "export", "2025-26", "71081210", "GOLD, 99.5 PERCENT", 20.5)]
    monkeypatch.setattr(nh, "get_json", lambda url, retries=3: payload)
    monkeypatch.setattr(nh.time, "sleep", lambda *_: None)
    db = tmp_path / "t.duckdb"
    nh.collect(str(db))
    n = duckdb.connect(str(db)).execute("SELECT COUNT(*) FROM niryat_hs8").fetchone()[0]
    assert n == len(nh.PARTNERS) * len(nh.FLOWS)


def test_comtrade_parse_and_collect(monkeypatch, tmp_path):
    payload = {"data": [{"period": "2023", "flowCode": "X", "cmdCode": "27",
                         "primaryValue": 9.5, "netWgt": 1.0},
                        {"period": "2023", "flowCode": "M", "cmdCode": "27",
                         "primaryValue": None}]}
    assert ct.parse(payload) == [(2023, "X", "27", 9.5, 1.0)]
    monkeypatch.setattr(ct, "get_json", lambda url, retries=3: payload)
    monkeypatch.setattr(ct.time, "sleep", lambda *_: None)
    db = tmp_path / "t.duckdb"
    ct.collect(str(db), [2022, 2023])
    n = duckdb.connect(str(db)).execute("SELECT COUNT(*) FROM comtrade_india_chapters").fetchone()[0]
    assert n == 2
