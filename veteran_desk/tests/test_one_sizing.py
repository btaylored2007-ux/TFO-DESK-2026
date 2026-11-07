import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def test_band():
    # t2808u: B risk is half of the 2% non-A full risk. A stays 1%. A+ stays 4%.
    assert BK.risk_plan(500000, "A")["risk_aud"] == 5000 and BK.risk_plan(500000, "B")["risk_aud"] == 5000
    assert BK.risk_plan(500000, "A+")["pct"] == .04 and BK.risk_plan(500000, "A+", True)["pct"] == .04
def test_cut_toward_2pct_never_below():
    r = BK.risk_plan(500000, "A+", True, .75); assert abs(r["pct"] - .03) < 1e-9 and r["warning"] is None
    # t2808u: these cuts are kept. They are not rejected and not lifted back to the floor.
    r = BK.risk_plan(500000, "B", False, .4); assert abs(r["pct"] - .004) < 1e-9 and r["reject"] is None and not r["sized_up"]
    assert abs(BK.risk_plan(500000, "A", False, .4)["pct"] - .004) < 1e-9
    assert abs(BK.risk_plan(500000, "A", False, .5)["pct"] - .005) < 1e-9
def test_never_above_5pct(): assert BK.risk_plan(500000, "A+", True, 9)["pct"] == .05
