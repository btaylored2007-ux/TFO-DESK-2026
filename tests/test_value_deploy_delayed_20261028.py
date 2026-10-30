import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_deploy as vd
def _q(**k): return dict({"px_usd": 1.0, "px": 1.0, "fx_usd": 1.0, "executable": False, "paper_executable": True, "paper_price_class": "DELAYED PAPER", "age_sec": 1200}, **k)
def test_delayed_asx_fillable_us_not():
    ok, d, c = vd.fillable("BPT.AX", _q()); assert ok and d == 1200 and c == "DELAYED PAPER"
    assert not vd.fillable("HCI", _q())[0]
    assert not vd.fillable("BPT.AX", _q(paper_executable=False))[0]   # closed session never fills
def test_delay_slippage_positive_and_monotone():
    assert vd.delay_slip_bps(0.02, 30) == 0
    a, b = vd.delay_slip_bps(0.02, 900), vd.delay_slip_bps(0.02, 1800)
    assert 0 < a < b and abs(b - 1e4 * 0.02 * (1800 / 23400) ** 0.5) < 1e-9
def test_plan_logs_delay_and_charges_it():
    led = {"positions": {}}
    ranked = [{"symbol": "BPT.AX", "sector": "Energy"}]
    orders, sk = vd.plan(led, ranked, {"BPT.AX": _q()}, {"BPT.AX": (50e6, 0.02)}, lambda *a: (True, ""), 65000, 65000, {}, 1)
    o = orders[0]; assert o["delay_s"] == 1200 and o["delay_slip_bps"] > 0
    base, _ = vd.cost_bps(o["adv"], o["sigma"], o["usd"])
    assert abs(o["cost_bps"] - base - o["delay_slip_bps"]) < 1e-9
def test_preview_uses_assumed_delay_not_overnight_age():
    q = _q(executable=True, _preview=True, age_sec=50000)
    assert vd.fillable("BPT.AX", q)[1] == vd.PREVIEW_ASSUMED_DELAY_S and vd.fillable("HCI", q)[1] == 0.0
