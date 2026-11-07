import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk.tests.test_gate_v2 import g, P0, HIST
from veteran_desk import book as BK
def test_below_floor_rejected_not_lifted():   # Marcus 2026-10-29 13:08 WITA (3533.TW lifted 0.5% -> 2%)
    r = g(ch=dict(call="STAND_ASIDE"))   # TFO 19:18: challenger advisory -> no longer cuts below the floor
    assert r["passed"] and r["size_mult"] == 1.0
    assert g(ch=None, da=None)["passed"]   # debatefix: outage no longer cuts
def test_full_size_passes():
    assert g()["passed"]
def test_min_bars_all_venues():   # 20 bars (TW Lotes) hard reject
    r = g(hist=(HIST[0][-20:], "t")); assert not r["passed"] and any("insufficient PIT history" in x for x in r["reasons"])
def test_crypto_floor_half_pct():
    assert BK.risk_plan(500000, "A", False, .6, floor=BK.RISK_CRYPTO_MIN)["pct"] == .006   # kairails 14:28 WITA: crypto base now 1% (was 2%)
    # t2808u: the 0.1 cut stays at 0.1%. It is not below_crypto_floor and not lifted.
    cut = BK.risk_plan(500000, "A", False, .1, floor=BK.RISK_CRYPTO_MIN); assert cut["reject"] is None and abs(cut["pct"] - .001) < 1e-9 and not cut["sized_up"]
    b = BK.risk_plan(500000, "B", False, .3); assert b["reject"] is None and abs(b["pct"] - .003) < 1e-9 and not b["sized_up"]
    a = BK.risk_plan(500000, "A", False, .3); assert a["reject"] is None and abs(a["pct"] - .003) < 1e-9 and not a["sized_up"]
