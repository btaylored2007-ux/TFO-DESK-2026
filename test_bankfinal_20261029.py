"""2026-10-29 bankfinal (Brett 17:30/17:34/17:38 WITA): hunts half at net 1R (trend 1.5R), fee floor only after half,
full flat at net 3R of ORIGINAL risk (replaces +5% full flat), +2%/+5% do nothing, R locked at entry. PAPER. Py3.9."""
import datetime as dt, json, os, sys, tempfile
D = os.environ.get("TEST_CODE_DIR") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D); sys.path.insert(0, os.path.join(D, "tests")); sys.path.insert(0, "/Users/test/Applications/MultiVenueDesk/tests")
import hunt_giveback as GB, cal_paper_bench as CB, hunt48_short_paper as H
GB.LOG_FILE = os.path.join(tempfile.mkdtemp(), "early_banks.jsonl")
U = dt.timezone.utc; T0 = dt.datetime(2026, 10, 29, 1, tzinfo=U); NOWC = T0 + dt.timedelta(hours=10)
def bar(i, o, h, l, c, v=1000.0): return dict(t=T0 + dt.timedelta(hours=i), o=o, h=h, l=l, c=c, v=v)
def ct(est=0.0, **k):
    t = dict(sym="TST.L", direction="long", armed_utc=T0.isoformat(), status="FILLED", fill=100.0, fill_utc=T0.isoformat(),
             stop=90.0, stop_live=90.0, t1=102.0, t2=115.0, qty_open=100, shares=100, realised_usd=0.0, est_cost_usd=est, last_bar_utc=T0.isoformat())
    t.update(k); return t
def run(t, bars): CB.step(t, bars, NOWC, 3, rule="touch", half_1r=True); return t
def test_cal_half_1r_not_0_9r():
    assert not run(ct(), [bar(1, 100, 109.0, 100, 108.5)]).get("t1_done")
    t = run(ct(), [bar(1, 100, 110.0, 100, 109.8)]); assert t["t1_done"] and t["half_rule"] == "1R_net"
def test_cal_trend_1_5r():
    t = run(ct(setup="trend"), [bar(1, 100, 114.9, 100, 114.5)]); assert not t.get("t1_done")
    run(t, [bar(2, 114, 115.1, 114, 115.0)]); assert t["half_rule"] == "1.5R_net"
def test_cal_plus2_and_plus5_do_nothing():
    t = run(ct(est=400.0), [bar(1, 100, 102.1, 100, 102.0), bar(2, 102, 105.1, 102, 105.0)])
    assert t["status"] == "FILLED" and not t.get("t1_done") and t["stop_live"] == 90.0 and t["qty_open"] == 100
def test_cal_fee_floor_only_after_half():
    t = run(ct(est=400.0), [bar(1, 100, 113.0, 100, 112.0)]); assert t["stop_live"] == 90.0
    run(t, [bar(2, 112, 114.2, 112, 114.1)]); assert t["t1_done"] and t["stop_live"] == 104.0
def test_cal_t2_no_longer_exits_flat_at_3r():
    t = run(ct(), [bar(1, 100, 116, 100, 115.5)]); assert t["status"] == "FILLED" and t["qty_open"] == 50   # T2 115 ignored
    run(t, [bar(2, 115, 129.5, 115, 129)]); assert t["status"] == "FILLED"
    run(t, [bar(3, 129, 130.5, 129, 129.8)]); assert t["status"] == "CLOSED" and t["exit"] == "FLAT_3R" and t["exit_px"] == 130.0
def test_cal_3r_net_of_costs():
    t = run(ct(est=400.0), [bar(1, 100, 114.2, 100, 114.1), bar(2, 114, 133.5, 114, 133.2)]); assert t["status"] == "FILLED"   # need 134
    run(t, [bar(3, 133, 134.2, 133, 134.1)]); assert t["exit"] == "FLAT_3R"
def test_cal_r_locked_after_stop_move():
    t = run(ct(est=400.0), [bar(1, 100, 114.2, 100, 114.1)]); assert t["stop_live"] == 104.0 and t["stop"] == 90.0
    run(t, [bar(2, 114, 133.9, 114, 133.5)]); assert t["status"] == "FILLED"   # R from stop 90 (not 104) -> still need 134
def test_calendar_bench_unchanged():
    t = ct(); CB.step(t, [bar(1, 100, 116, 100, 115.5)], NOWC, 3, rule="touch"); assert t["exit"] == "T2"
# ---- US hunt48_short_paper (main/gpt books; BOOK != claude) ----
from test_hunt_fixes_20261028 import _t, _b
NOW = dt.datetime(2026, 10, 27, 20, 0, tzinfo=U)
def test_us_short_plus5_nothing_then_3r_flat():
    t = _t(shares=100); H.manage(t, [_b(17, 0, 10.0, 10.0, 9.45, 9.5)], NOW)   # +5.5%, 0.55R
    assert t["status"] == "FILLED" and not t["half_done"] and t["trail"] is None
    H.manage(t, [_b(17, 15, 9.5, 9.5, 8.9, 8.95)], NOW); assert t["half_done"] and t["open_qty"] == 50
    H.manage(t, [_b(17, 30, 8.0, 8.0, 7.5, 7.6)], NOW); assert t["status"] == "FILLED"   # 2.5R
    H.manage(t, [_b(17, 45, 7.4, 7.4, 6.9, 6.95)], NOW); assert t["status"] == "CLOSED" and "flat_3r" not in [] and "3R" in t["exit_reason"] and t["open_qty"] == 0
def test_us_long_3r_flat_and_r_locked():
    t = _t(direction="long", entry=10.0, stop=9.0, trigger=10.0, t1=11.0, t2=11.5, lowest=10.0, shares=100)
    H.manage(t, [_b(17, 0, 10.0, 11.1, 10.0, 11.05)], NOW); assert t["half_done"] and t["trail"] > 10.0 and t["stop"] == 9.0
    H.manage(t, [_b(17, 15, 11.05, 12.5, 11.0, 12.45)], NOW); assert t["status"] == "FILLED"
    H.manage(t, [_b(17, 30, 12.5, 13.2, 12.4, 13.1)], NOW); assert t["status"] == "CLOSED" and "3R" in t["exit_reason"]
def test_flg_unchanged():
    for b in ("hunt48_short", "hunt48_short_claude"):
        p = "/Users/test/Applications/MultiVenueDesk/state/%s/ledger.json" % b
        if os.path.exists(p):
            for x in json.load(open(p))["trades"]:
                if x.get("ticker") == "FLG" and x.get("status") == "FILLED": assert abs(float(x["stop"]) - 10.73) < 1e-9
# ---- TIME STOP (Brett 17:44 WITA "Yes go ahead") ----
F1 = dt.datetime(2026, 10, 30, 0, tzinfo=U)
def tct(**k):
    t = ct(fill_utc=F1.isoformat(), armed_utc=F1.isoformat(), last_bar_utc=F1.isoformat()); t.update(k); return t
def bt(h, o, hi, lo, c): return dict(t=F1 + dt.timedelta(hours=h), o=o, h=hi, l=lo, c=c, v=1000.0)
def rs(t, bars, now=F1 + dt.timedelta(hours=60)): CB.step(t, bars, now, 3, rule="touch", half_1r=True); return t
def test_ts_exits_under_half_r_at_deadline():
    t = rs(tct(), [bt(47, 103, 104, 102, 103), bt(48, 103, 104, 102, 104)])   # +0.4R at 48h
    assert t["status"] == "CLOSED" and t["exit_reason"] == "rail_time_stop" and t["giveback"]["reason"] == "time_stop" and t["giveback"]["rail_px"] == 90.0
def test_ts_holds_at_or_above_half_r():
    t = rs(tct(), [bt(48, 104, 105.5, 104, 105.0)]); assert t["status"] == "FILLED"   # exactly 0.5R
def test_ts_holds_when_mark_missing():
    t = rs(tct(), [bt(10, 100, 101, 99, 100.5)]); assert t["status"] == "FILLED" and "time_stop_utc" in t   # no bar after deadline
    rs(t, [dict(t=F1 + dt.timedelta(hours=49), o=None, h=101, l=99, c=None, v=1.0)]); assert t["status"] == "FILLED"
def test_ts_deadline_not_extended():
    t = rs(tct(), [bt(10, 100, 101, 99, 100.5)]); d0 = t["time_stop_utc"]
    t["fill_utc"] = (F1 + dt.timedelta(hours=30)).isoformat(); rs(t, [bt(20, 100, 101, 99, 100.5)]); assert t["time_stop_utc"] == d0
def test_ts_not_on_calendar_bench_or_grandfathered():
    t = tct(); CB.step(t, [bt(48, 103, 104, 102, 103)], F1 + dt.timedelta(hours=60), 3, rule="touch"); assert t["status"] != "CLOSED" or t.get("exit") != "TIME_STOP"
    t = run(ct(), [bar(49, 101, 102, 100, 101)]); assert t.get("exit") != "TIME_STOP"   # filled before deploy
def test_ts_no_stop_move_at_half_r():
    t = rs(tct(), [bt(5, 100, 105.5, 100, 105.2)]); assert t["stop_live"] == 90.0
def test_us_long_time_stop():
    E = "2026-11-02T15:00:00+00:00"; N2 = dt.datetime(2026, 11, 4, 0, tzinfo=U)
    t = _t(direction="long", entry=10.0, stop=9.0, trigger=10.0, t1=11.0, t2=11.5, lowest=10.0, shares=100, entry_utc=E, time_exit_utc="2026-11-05T21:00:00+00:00")
    H.manage(t, [dict(start=dt.datetime(2026, 11, 3, 20, 40, tzinfo=U), o=10.2, h=10.3, l=10.15, c=10.2)], N2)
    assert t["time_stop_utc"].startswith("2026-11-03T20:55") and t["status"] == "CLOSED" and t["exit_reason"] == "rail_time_stop"
    t = _t(direction="long", entry=10.0, stop=9.0, trigger=10.0, t1=11.0, t2=11.5, lowest=10.0, shares=100, entry_utc=E, time_exit_utc="2026-11-05T21:00:00+00:00")
    H.manage(t, [dict(start=dt.datetime(2026, 11, 3, 20, 40, tzinfo=U), o=10.6, h=10.7, l=10.55, c=10.65)], N2)
    assert t["status"] == "FILLED"
