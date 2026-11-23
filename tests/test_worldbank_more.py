"""Extra coverage for collectors/worldbank_trade.py: pagination, retry back-off, request shape, CLI block."""
import io
import json
import urllib.error

import duckdb
import pytest

from collectors import worldbank_trade as wb
from _mainblock import run_main_block


def _meta(pages=1, updated="2026-01-15"):
    return {"pages": pages, "lastupdated": updated}


@pytest.fixture()
def sleeps(monkeypatch):
    rec = []
    monkeypatch.setattr(wb.time, "sleep", rec.append)
    return rec


def _collect_with(monkeypatch, tmp_path, responder, countries=("IND",), indicators=None):
    urls = []

    def fake(url, retries=3):
        urls.append(url)
        return responder(url)
    monkeypatch.setattr(wb, "get_json", fake)
    monkeypatch.setattr(wb, "COUNTRIES", list(countries))
    monkeypatch.setattr(wb, "INDICATORS", indicators or {"NE.EXP.GNFS.ZS": "exports_pct_gdp"})
    db = tmp_path / "t.duckdb"
    wb.collect(str(db))
    con = duckdb.connect(str(db))
    rows = con.execute("SELECT * FROM worldbank_trade ORDER BY year").fetchall()
    con.close()
    return urls, rows


def test_collect_follows_pages_until_last_and_builds_urls(monkeypatch, tmp_path, sleeps):
    def responder(url):
        page = int(url.rsplit("page=", 1)[1])
        return [_meta(pages=3), [{"date": str(2000 + page), "value": float(page)}]]
    urls, rows = _collect_with(monkeypatch, tmp_path, responder)
    base = "https://api.worldbank.org/v2/country/IND/indicator/NE.EXP.GNFS.ZS?format=json&per_page=1000&page="
    assert urls == [base + "1", base + "2", base + "3"]
    assert [(r[3], r[4]) for r in rows] == [(2001, 1.0), (2002, 2.0), (2003, 3.0)]
    assert rows[0][:3] == ("IND", "NE.EXP.GNFS.ZS", "exports_pct_gdp")


def test_collect_single_page_when_pages_key_missing(monkeypatch, tmp_path, sleeps):
    urls, rows = _collect_with(monkeypatch, tmp_path, lambda u: [{}, [{"date": "2020", "value": 1}]])
    assert len(urls) == 1 and rows[0][3] == 2020 and rows[0][5] is None   # lastupdated absent -> NULL


@pytest.mark.parametrize("payload", [None, [], [_meta()], [_meta(), None], [_meta(), []]])
def test_collect_stops_on_empty_or_malformed_payloads(monkeypatch, tmp_path, sleeps, payload):
    def responder(url):
        return payload if "/country/IND/" in url else [_meta(), [{"date": "2020", "value": 1}]]
    urls, rows = _collect_with(monkeypatch, tmp_path, responder, countries=("IND", "CHN"))
    assert len([u for u in urls if "/IND/" in u]) == 1 and len(urls) == 2   # no retry/paging on a bad payload
    assert [r[0] for r in rows] == ["CHN"]               # IND yields nothing but does not stop CHN


@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="BUG worldbank_trade.py collect: if no country/indicator returns an observation, "
                          "con.executemany(INSERT, []) raises InvalidInputException after the table was already "
                          "replaced, so the run crashes instead of leaving an empty table")
def test_collect_with_no_observations_at_all_does_not_crash(monkeypatch, tmp_path, sleeps):
    _, rows = _collect_with(monkeypatch, tmp_path, lambda u: [_meta(), []])
    assert rows == []


def test_collect_keeps_zero_values_and_skips_only_none(monkeypatch, tmp_path, sleeps):
    data = [{"date": "2021", "value": 0}, {"date": "2020", "value": None}, {"date": "2019", "value": "7.5"}]
    _, rows = _collect_with(monkeypatch, tmp_path, lambda u: [_meta(), data])
    assert [(r[3], r[4]) for r in rows] == [(2019, 7.5), (2021, 0.0)]


def test_collect_throttles_per_indicator_and_reports_progress(monkeypatch, tmp_path, sleeps, capsys):
    ind = {"A": "a", "B": "b"}
    _, rows = _collect_with(monkeypatch, tmp_path, lambda u: [_meta(), [{"date": "2020", "value": 1}]],
                            countries=("IND", "CHN"), indicators=ind)
    assert sleeps == [0.3] * 4
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "  IND: done (2 rows cumulative)" and out[1] == "  CHN: done (4 rows cumulative)"
    assert out[2] == f"worldbank_trade: 4 rows -> {tmp_path / 't.duckdb'}"


def test_collect_replaces_existing_table(monkeypatch, tmp_path, sleeps):
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE worldbank_trade (junk INTEGER)")
    con.close()
    _, rows = _collect_with(monkeypatch, tmp_path, lambda u: [_meta(), [{"date": "2020", "value": 1}]])
    assert len(rows) == 1


class _R:
    def __init__(self, payload):
        self.b = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    def __enter__(self):
        return self.b

    def __exit__(self, *a):
        return False


def test_get_json_request_shape(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen["url"], seen["ua"], seen["timeout"] = req.full_url, req.get_header("User-agent"), timeout
        return _R({"ok": 1})
    monkeypatch.setattr(wb.urllib.request, "urlopen", fake_open)
    assert wb.get_json("http://example/x") == {"ok": 1}
    assert seen == {"url": "http://example/x", "ua": wb.UA["User-Agent"], "timeout": 30}


@pytest.mark.parametrize("exc", [urllib.error.URLError("x"), TimeoutError(), ConnectionError(),
                                 json.JSONDecodeError("m", "d", 0)])
def test_get_json_retries_each_network_or_parse_error_with_backoff(monkeypatch, sleeps, exc):
    n = {"c": 0}

    def flaky(req, timeout):
        n["c"] += 1
        if n["c"] < 3:
            raise exc
        return _R([1])
    monkeypatch.setattr(wb.urllib.request, "urlopen", flaky)
    assert wb.get_json("http://u", retries=3) == [1]
    assert sleeps == [2, 4]                              # 2 * (i + 1)


def test_get_json_retries_on_garbage_body_then_raises(monkeypatch, sleeps):
    monkeypatch.setattr(wb.urllib.request, "urlopen", lambda req, timeout: _R(b"<html>not json"))
    with pytest.raises(json.JSONDecodeError):
        wb.get_json("http://u", retries=2)
    assert sleeps == [2]                                 # no sleep after the final attempt


def test_get_json_zero_retries_returns_none(monkeypatch):
    monkeypatch.setattr(wb.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not be called"))
    assert wb.get_json("http://u", retries=0) is None


def test_default_retries_is_three(monkeypatch, sleeps):
    n = {"c": 0}

    def down(req, timeout):
        n["c"] += 1
        raise TimeoutError
    monkeypatch.setattr(wb.urllib.request, "urlopen", down)
    with pytest.raises(TimeoutError):
        wb.get_json("http://u")
    assert n["c"] == 3 and sleeps == [2, 4]


def test_indicator_catalogue_names_are_unique_and_prefixed_sanely():
    assert len(set(wb.INDICATORS.values())) == len(wb.INDICATORS) == 10
    assert wb.COUNTRIES[0] == "IND" and len(set(wb.COUNTRIES)) == len(wb.COUNTRIES) == 8


def test_cli_passes_db_argument_or_default(monkeypatch):
    calls = []
    run_main_block(wb, ["--db", "somewhere.duckdb"], collect=calls.append)
    run_main_block(wb, [], collect=calls.append)
    assert calls[0] == "somewhere.duckdb"
    assert calls[1].endswith("data/trade.duckdb")
