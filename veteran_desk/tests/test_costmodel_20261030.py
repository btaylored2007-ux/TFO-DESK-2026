"""PER-VENUE COST MODEL (tfo_costmodel_20261030). PAPER only. AUDUSD fixed at 0.70; listing FX stubbed."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import pytest
AU = 0.70
RATES = {"USD": 1 / AU, "AUD": 1.0, "GBp": 0.019, "GBP": 1.9, "EUR": 1.65, "HKD": 0.18, "KRW": 0.0011, "TWD": 0.047, "JPY": 0.0095, "CHF": 1.75}
CCY = {".L": "GBp", ".PA": "EUR", ".MC": "EUR", ".MI": "EUR", ".SW": "CHF", ".AX": "AUD", ".HK": "HKD", ".KS": "KRW", ".TW": "TWD", ".T": "JPY"}
def _ccy(sym):
    for k, v in CCY.items():
        if sym.endswith(k): return v
    return "USD"
@pytest.fixture(autouse=True)
def _m(monkeypatch):
    monkeypatch.setattr(BK, "listing_ccy", _ccy)
    monkeypatch.setattr(BK, "aud_per_unit", lambda sym, au, *a, **k: RATES[_ccy(sym)] if not sym.endswith(("=F",)) else 1 / AU)
def P(tk, venue, side="LONG", **kw): d = dict(ticker=tk, venue=venue, side=side, adv_aud=100e6); d.update(kw); return d
def L(p, px, q, leg): return BK.leg_cost_aud(p, px, q, AU, None, leg, True)[1]
def test_us_per_share_min_sec_fx():
    e = L(P("AAA", "US"), 100.0, 1000, "entry"); x = L(P("AAA", "US"), 100.0, 1000, "exit")
    assert e["comm"] == round((3.5 + 0.2) / AU, 2) and "tax" not in e and x["tax"] == round(100000 / AU * 20.6e-6, 2) and e["fx_conv"] == round(100000 / AU * .0003, 2)
    assert L(P("AAA", "US"), 100.0, 10, "entry")["comm"] == round((0.35 + 0.002) / AU, 2)   # min $0.35
def test_spread_buckets():
    assert L(P("AAA", "US", adv_aud=300e6), 100.0, 1000, "entry")["spread"] == round(100000 / AU * 1e-4, 2)
    assert L(P("AAA", "US", adv_aud=5e5), 100.0, 1000, "entry")["spread"] == round(100000 / AU * 25e-4, 2)
    assert L(P("AAA", "US", adv_aud=None), 100.0, 1000, "entry")["spread"] == round(100000 / AU * 3e-4, 2) and L(P("XLE", "US", adv_aud=None), 100.0, 1000, "entry")["spread"] == round(100000 / AU * 1.5e-4, 2)
def test_uk_stamp_ptm_buy_only():
    e = L(P("SMT.L", "LSE"), 1000.0, 1000, "entry"); x = L(P("SMT.L", "LSE"), 1000.0, 1000, "exit")   # 1000p x 1000 = GBP10,000 -> A$19,000
    assert e["comm"] == 9.5 and e["tax"] == 95.0 and "tax" not in x            # 10k not > 10k -> no PTM
    assert L(P("SMT.L", "LSE"), 1000.0, 2000, "entry")["tax"] == round(38000 * .005 + 1.5 * 1.9, 2)
    assert L(P("SMT.L", "LSE", side="SHORT"), 1000.0, 1000, "exit")["tax"] == 95.0   # short cover is a buy
def test_fr_es_it_ch():
    assert L(P("MC.PA", "Euronext"), 100.0, 100, "entry")["tax"] == round(16500 * .004, 2)
    assert L(P("SAN.MC", "BME"), 100.0, 100, "entry")["tax"] == round(16500 * .002, 2)
    assert L(P("ENI.MI", "MILAN"), 100.0, 100, "entry")["tax"] == round(16500 * .001, 2)
    s = L(P("NESN.SW", "SIX"), 100.0, 100, "entry"); assert "tax" not in s and s["comm"] == round(17500 * .0005, 2)
def test_asx_min_and_no_fx():
    e = L(P("BHP.AX", "ASX"), 10.0, 100, "entry"); assert e["comm"] == 5.0 and "fx_conv" not in e
    assert L(P("BHP.AX", "ASX"), 10.0, 100000, "entry")["comm"] == 800.0
def test_hk_kr_tw_jp():
    assert L(P("0700.HK", "HKEX"), 100.0, 100, "entry")["tax"] == L(P("0700.HK", "HKEX"), 100.0, 100, "exit")["tax"] == round(1800 * .001, 2)
    assert "tax" not in L(P("005930.KS", "KRX"), 1000.0, 10, "entry") and L(P("005930.KS", "KRX"), 1000.0, 10, "exit")["tax"] == round(11 * .0018, 2)
    assert L(P("2330.TW", "TWSE"), 100.0, 100, "exit")["tax"] == round(470 * .003, 2)
    assert L(P("7203.T", "TSE"), 1000.0, 100, "entry")["comm"] == round(950 * .0005, 2)
def test_kraken_legs():
    p = P("KRAKEN:XBTUSD", "CRYPTO", adv_aud=None)
    assert L(p, 100.0, 10, "entry")["kraken"] == round(1000 / AU * .0025, 2) and L(p, 100.0, 10, "exit")["kraken"] == round(1000 / AU * .004, 2)
    assert L(p, 100.0, 10, "entry")["spread"] == round(1000 / AU * 5e-4, 2)
def test_futures_whole_contracts():
    assert L(P("GC=F", "FUT"), 4000.0, 200, "entry")["contract_fees"] == round(2 * (0.85 + 1.55) / AU, 2)   # 2 GC
    assert L(P("GC=F", "FUT"), 4000.0, 87, "entry")["contract_fees"] == round(9 * (0.25 + 0.35) / AU, 2)    # 87oz -> 9 MGC (whole, rounded up)
def test_borrow_daily_us_short(monkeypatch):
    B = dict(cash_aud=1000.0); p = dict(ticker="AAA", venue="US", side="SHORT", status="open", filled_utc="2026-10-28T02:00:00+00:00", mark_notional_aud=36000.0)
    a = BK.borrow_accrue(B, p, dt.datetime(2026, 10, 30, 2, tzinfo=dt.timezone.utc)); assert round(a, 4) == round(36000 * .0025 * 2 / 360, 4) and p["borrow_accrued_aud"] == round(a, 2)
    assert BK.borrow_accrue(B, p, dt.datetime(2026, 10, 30, 5, tzinfo=dt.timezone.utc)) == 0.0   # once per day
    assert BK.borrow_accrue(B, dict(p, side="LONG"), dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc)) == 0.0
def test_rt_rate_fallback_and_rule_log():
    assert BK.rt_rate(dict(venue="US")) == BK.COST_RT["US"] and BK.rt_rate(dict(venue="US", cost_rt_rate=.0012)) == .0012
    assert any(r.get("id") == "tfo_costmodel_20261030" for r in BK.RULE_LOG)
