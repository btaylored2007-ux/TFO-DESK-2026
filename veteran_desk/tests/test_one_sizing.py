import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def test_band():
    assert BK.risk_plan(500000, "A")["risk_aud"] == 5000 and BK.risk_plan(500000, "B")["risk_aud"] == 10000   # tfo_review_fix_20261031
    assert BK.risk_plan(500000, "A+")["pct"] == .04 and BK.risk_plan(500000, "A+", True)["pct"] == .04   # Brett 2026-10-29 12:59 WITA
def test_cut_toward_2pct_never_below():
    r = BK.risk_plan(500000, "A+", True, .75); assert abs(r["pct"] - .03) < 1e-9 and r["warning"] is None
    r = BK.risk_plan(500000, "B", False, .4); assert r["pct"] == 0 and r["reject"] == "below_2pct_nonA_floor"; assert BK.risk_plan(500000, "A", False, .4)["pct"] == .01   # tfo_review_fix_20261031   # tfo_review_20261030: floor 1% (A x0.5 = 1% now allowed)
    assert BK.risk_plan(500000, "A", False, .5)["pct"] == .01   # railfix 2026-10-29: rejected, not lifted
def test_never_above_5pct(): assert BK.risk_plan(500000, "A+", True, 9)["pct"] == .05
