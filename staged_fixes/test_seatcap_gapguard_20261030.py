"""seatcap + gapguard 2026-10-30 21:23 WITA (Brett FULL AUTH, PAPER): 40% per-position notional cap, gap-inclusive sizing for futures/thin,
dip-buy stabilisation guard, honest gap-through stop flag."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests import test_insufficient_cash_20261029 as IC
SEATCAP_LIVE = True
def _e(eid, **k):
    d = IC._entry(eid); d.update(k); return d
def test_cap_cuts_to_40pct():
    # A$500k seat, stop 1 USD below 40 -> 2% risk = 6,500 sh = A$400k (80%) -> cut to 40% = 5,000 sh (A$200k-ish at .65)
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SC1": _e("SC1")}, IC.MK, session=IC.CLOSED)
    x = D["seats"]["banks"]["positions"]["SC1"]; eq = 500000
    assert x["shares"] * 40 / IC.AU <= eq * BK.SEAT_NOTIONAL_CAP + 1
def test_cap_rejects_when_risk_below_floor():
    # very wide stop never hits cap; very tight stop -> cap leaves < 1% risk -> reject
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SC2": _e("SC2", stop=39.95, entry=40.0)}, IC.MK, session=IC.CLOSED)
    assert Q["SC2"]["state"] == "expired" and "seat_cap_notional_40pct" in Q["SC2"]["why"]
def test_gap_pct():
    assert BK.gap_risk_pct(dict(ticker="GC=F")) == BK.GAP_PCT_FUTURES
    assert BK.gap_risk_pct(dict(ticker="XYZ", adv_aud=1e6)) == BK.GAP_PCT_THIN
    assert BK.gap_risk_pct(dict(ticker="BAC", adv_aud=5e8)) == 0 and BK.gap_risk_pct(dict(ticker="BAC")) == 0
def test_stabilised():
    up = [dict(o=1, h=2, l=.5, c=1.5), dict(o=1, h=2, l=.6, c=1.8)]; dn = [dict(o=1, h=2, l=.5, c=1.5), dict(o=1.8, h=2, l=.4, c=1.0)]
    assert BK.stabilised(dict(side="LONG"), up) and not BK.stabilised(dict(side="LONG"), dn) and not BK.stabilised(dict(side="LONG"), None)
def test_thin_dip_buy_needs_stabilisation(monkeypatch):
    monkeypatch.setattr(BK, "BARS_FN", lambda s, c: None)
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SC3": _e("SC3", entry=39.0, stop=37.0, target=45.0, adv_aud=1e6)}, IC.MK, session=IC.CLOSED)
    assert Q["SC3"]["state"] == "expired" and "gap_guard" in Q["SC3"]["why"]
def test_gap_sizing_cuts_risk():
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SC4": _e("SC4", entry=40.5, stop=36.0, target=50.0, adv_aud=1e6, order_type="stop_limit")}, IC.MK, session=IC.CLOSED)
    x = D["seats"]["banks"]["positions"]["SC4"]
    assert x["gap_risk_aud"] is not None and x["gap_risk_aud"] <= 500000 * .02 + 1 and x["risk_aud"] < x["gap_risk_aud"]
def test_stop_entry_arms_as_stop_limit():
    e = _e("SE1", entry=40.0, stop=39.0, target=44.0); e.pop("order_type"); e["stop_entry"] = 40.8   # px 40.5 is already above 40.0: would arm as a plain limit
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SE1": e}, IC.MK, session=IC.CLOSED)
    x = D["seats"]["banks"]["positions"]["SE1"]; assert x["order_type"] == "stop_limit" and x["entry"] == 40.8
def test_stop_entry_bad_level_rejected():
    e = _e("SE2"); e.pop("order_type"); e["entry_type"] = "stop"; e["entry"] = 0
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SE2": e}, IC.MK, session=IC.CLOSED)
    assert Q["SE2"]["state"] == "expired"
def test_tiers():
    assert BK.seat_cap_pct(dict(ticker="DGRO")) == (BK.SEAT_ETF_CAP, "notional") and BK.seat_cap_pct(dict(ticker="AAPL")) == (0.40, "notional")
    assert BK.seat_cap_pct(dict(ticker="GLD")) == (0.40, "notional") and BK.seat_cap_pct(dict(ticker="GC=F"))[1] == "margin"
def test_etf_cap_75(monkeypatch):
    e = _e("SC9", ticker="DGRO", entry=40.0, stop=39.0, target=44.0)
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"SC9": e}, dict(IC.MK, DGRO=dict(last=40.5, hi=40.6, lo=40.4)), session=IC.CLOSED)
    x = D["seats"]["banks"]["positions"]["SC9"]; n = x["shares"] * 40 / IC.AU
    assert 500000 * .40 < n <= 500000 * .75 + 1
