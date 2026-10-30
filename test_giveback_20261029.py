"""2026-10-29 Brett DNA giveback: banks on open profit + adverse turn; never without open profit; FLG levels unchanged. Py3.9."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.environ.get("TEST_CODE_DIR") or os.path.dirname(os.path.abspath(__file__)))
import hunt_giveback as GB, cal_paper_bench as CB
GB.LOG_FILE = os.path.join(tempfile.mkdtemp(), "early_banks.jsonl")   # never write the live log from tests
U = dt.timezone.utc; T0 = dt.datetime(2026, 10, 29, 1, tzinfo=U)
def bar(i, o, h, l, c, v=1000.0): return dict(t=T0 + dt.timedelta(hours=i), o=o, h=h, l=l, c=c, v=v)
def trade():
    return dict(sym="TST.L", direction="long", armed_utc=T0.isoformat(), status="FILLED", fill=100.0, fill_utc=T0.isoformat(),
                stop=95.0, stop_live=95.0, t1=110.0, t2=115.0, qty_open=100, shares=100, realised_usd=0.0, est_cost_usd=0.0,
                last_bar_utc=T0.isoformat())
def test_a_giveback_banks():
    t = trade(); bars = [bar(1, 100, 104, 100, 103.5), bar(2, 103.5, 103.6, 101, 101.5)]   # peak +0.8R, back to +0.3R
    CB.step(t, bars, T0 + dt.timedelta(hours=5), 3, rule="touch", giveback=True, news={}, engine="test")
    assert t["status"] == "CLOSED" and t["exit"] == "GIVEBACK_A" and t["giveback"]["exit_px"] == 101.5 and t["giveback"]["rail_px"] == 95.0
def test_b_volume_turn_banks():
    t = trade(); bars = [bar(1, 100, 101.2, 100.5, 101, 1000), bar(2, 101, 101.3, 100.2, 100.3, 3000)]   # close < prior low on 3x vol, still +
    CB.step(t, bars, T0 + dt.timedelta(hours=5), 3, rule="touch", giveback=True, news={}, engine="test")
    assert t["exit"] == "GIVEBACK_B"
def test_c_news_banks():
    t = trade(); bars = [bar(1, 100, 100.6, 100.1, 100.5)]
    CB.step(t, bars, T0 + dt.timedelta(hours=5), 3, rule="touch", giveback=True, news={"TST.L": [T0 + dt.timedelta(minutes=30)]}, engine="test")
    assert t["exit"] == "GIVEBACK_C"
def test_no_open_profit_no_bank():
    t = trade(); bars = [bar(1, 100, 104, 100, 103.5), bar(2, 103.5, 103.6, 99, 99.5, 5000)]   # gave it all back: under water
    CB.step(t, bars, T0 + dt.timedelta(hours=5), 3, rule="touch", giveback=True, news={"TST.L": [T0 + dt.timedelta(minutes=90)]}, engine="test")   # news lands on the losing bar
    assert t["status"] == "FILLED" and "giveback" not in t
def test_off_by_default_and_half_unchanged():
    t = trade(); bars = [bar(1, 100, 104, 100, 103.5), bar(2, 103.5, 103.6, 101, 101.5)]
    CB.step(t, bars, T0 + dt.timedelta(hours=5), 3, rule="touch")
    assert t["status"] == "FILLED"
    assert __import__("tfo_trail_rail").HALF_AT == 0.02
def test_flg_levels_unchanged():
    p = "/Users/test/Applications/MultiVenueDesk/state/hunt48_short/ledger.json"
    f = [x for x in json.load(open(p))["trades"] if x["ticker"] == "FLG" and x["status"] == "FILLED"]
    assert f, "FLG open short expected"
    for x in f: assert abs(float(x["stop"]) - 10.73) < 1e-9
