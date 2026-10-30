"""17 Oct 2026: markets without outcomePrices are skipped, not fatal; absent on every market = schema drift."""
import os, sys, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import polymarket_collector as PC


def _m(i, priced=True):
    m = {"id": i, "question": "q", "outcomes": '["Yes","No"]', "spread": 0.01, "liquidityNum": 1, "volumeNum": 1, "endDate": "2026-12-01"}
    if priced: m["outcomePrices"] = '["0.4","0.6"]'
    return m


def _run(monkeypatch, ms):
    got = {}
    monkeypatch.setattr(PC, "fetch_markets", lambda: list(ms))
    monkeypatch.setattr(PC, "fetch_crypto_markets", lambda: ([], []))
    class Stop(Exception): pass
    def boom(): raise Stop()
    monkeypatch.setattr(PC, "db_conn", boom)
    real = PC.to_row
    monkeypatch.setattr(PC, "to_row", lambda m, u: got.setdefault("ids", []).append(m["id"]) or real(m, u))
    with pytest.raises(Stop): PC.run_once()
    return got["ids"]


def test_unpriced_skipped(monkeypatch):
    assert _run(monkeypatch, [_m(1), _m(2, False), _m(3)]) == [1, 3]


def test_all_unpriced_is_drift(monkeypatch):
    monkeypatch.setattr(PC, "fetch_markets", lambda: [_m(1, False), _m(2, False)])
    monkeypatch.setattr(PC, "fetch_crypto_markets", lambda: ([], []))
    with pytest.raises(PC.SchemaDriftError): PC.run_once()
