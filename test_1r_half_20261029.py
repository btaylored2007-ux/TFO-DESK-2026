"""2026-10-29 17:30 WITA Brett: hunt half-bank at net >= +1R of original risk after costs (not +2% of notional / not T1). Py3.9."""
import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.environ.get("TEST_CODE_DIR") or os.path.dirname(os.path.abspath(__file__)))
import hunt_giveback as GB, cal_paper_bench as CB
GB.LOG_FILE = os.path.join(tempfile.mkdtemp(), "early_banks.jsonl")
U = dt.timezone.utc; T0 = dt.datetime(2026, 10, 29, 1, tzinfo=U)
def bar(i, o, h, l, c, v=1000.0): return dict(t=T0 + dt.timedelta(hours=i), o=o, h=h, l=l, c=c, v=v)
def trade(est=0.0):
    return dict(sym="TST.L", direction="long", armed_utc=T0.isoformat(), status="FILLED", fill=100.0, fill_utc=T0.isoformat(),
                stop=90.0, stop_live=90.0, t1=102.0, t2=115.0, qty_open=100, shares=100, realised_usd=0.0, est_cost_usd=est,
                last_bar_utc=T0.isoformat())
def test_half_at_1r():
    # R_px=10, risk_usd=1000, cost_half=0; need fav >= 110 for 1R on full 100 sh
    t = trade(); CB.step(t, [bar(1, 100, 110.5, 100, 110.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert t["t1_done"] and t["half_rule"] == "1R_net" and t["qty_open"] == 50 and t["stop_live"] == 100.0  # zero cost: fee floor == entry
def test_no_half_at_0_9r():
    t = trade(); CB.step(t, [bar(1, 100, 109.0, 100, 108.5)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert not t.get("t1_done") and t["qty_open"] == 100
def test_costs_raise_the_bar():
    # amend 17:37: full round-trip 400 counted; need gross >= 1400 -> fav >= 114
    t = trade(est=400.0); CB.step(t, [bar(1, 100, 113.5, 100, 113.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert not t.get("t1_done")
    CB.step(t, [bar(2, 113, 114.5, 113, 114.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert t["t1_done"] and t["half_rule"] == "1R_net"
def test_old_t1_not_trigger_on_hunt():
    # t1=102 (+2% of notional-ish) but half_1r on: must NOT bank at 102
    t = trade(); CB.step(t, [bar(1, 100, 102.5, 100, 102.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert not t.get("t1_done")
def test_plus2pct_does_not_touch_stop():
    # amend 17:37: +2% (and above, short of 1R net) must NOT move the stop anywhere (no fee floor at +2%)
    t = trade(est=400.0); CB.step(t, [bar(1, 100, 102.5, 100, 102.2), bar(2, 102, 105, 101, 104.8)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert not t.get("t1_done") and t["stop_live"] == 90.0 and not t.get("stop_rule")
def test_fee_floor_only_after_half():
    # half at 1R net -> stop = entry + RT cost/share = 100 + 400/100 = 104
    t = trade(est=400.0); CB.step(t, [bar(1, 100, 114.5, 100, 114.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert t["t1_done"] and t["stop_live"] == 104.0 and t["stop_rule"] == "fee_floor_after_half"
def test_trend_setup_needs_1_5r():
    t = trade(); t["setup"] = "trend"; CB.step(t, [bar(1, 100, 112, 100, 111.8)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert not t.get("t1_done")
    CB.step(t, [bar(2, 112, 115.5, 112, 115.2)], T0 + dt.timedelta(hours=5), 3, rule="touch", half_1r=True)
    assert t["t1_done"] and t["half_rule"] == "1.5R_net"
def test_us_short_fee_floor_includes_borrow():
    import hunt48_short_paper as HS
    assert HS.BORROW_PA > 0
def test_calendar_bench_keeps_t1():
    t = trade(); CB.step(t, [bar(1, 100, 102.5, 100, 102.2)], T0 + dt.timedelta(hours=5), 3, rule="touch")  # half_1r False
    assert t["t1_done"] and t["qty_open"] == 50 and not t.get("half_rule")
def test_flg_stop_untouched():
    import json
    f = [x for x in json.load(open("/Users/test/Applications/MultiVenueDesk/state/hunt48_short/ledger.json"))["trades"] if x["ticker"]=="FLG" and x["status"]=="FILLED"]
    assert f and abs(float(f[0]["stop"]) - 10.73) < 1e-9
