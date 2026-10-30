"""TIME STOP 2026-10-29 (Brett 17:44 WITA 'Yes go ahead' via TFO - HEAD TRADER). PAPER only."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
U = dt.timezone.utc
def P(seat, fu="2026-10-20T03:00:00+00:00", fill=100.0, stop=95.0, sh=100, tk="XYZ", venue="US", **kw):
    p = dict(id="T", seat=seat, ticker=tk, side="LONG", fill=fill, initial_stop=stop, stop=stop, shares=sh, venue=venue, filled_utc=fu, cost_aud=0.0, r0=abs(fill - stop), target=120.0)
    p.update(kw); return p
def setup_function(_):
    BK.DMA20_FN = lambda *a: False
    BK.net_R = lambda p, px, au, marks=None: (px - p["fill"]) / p["r0"]
_orig = BK.net_R
def teardown_function(_): BK.net_R = _orig; BK.DMA20_FN = BK._dma20_above_default
def test_rule_log_quotes_brett():
    r = [x for x in BK.RULE_LOG if x.get("rule") == "rail_time_stop"][-1]; assert r["words"] == "Yes go ahead" and "17:44 WITA 29 Oct 2026" in r["wita"]
def test_per_seat_n():
    t = dt.datetime(2026, 10, 20, 4, tzinfo=U)
    for s, n in [("energy", 7), ("banks", 7), ("tech_ai_infra", 7), ("essentials_utilities", 7), ("macro", 10), ("fx_commodities", 10), ("trend", 15), ("news_filings", 3), ("price_action", 7)]:
        p = P(s); BK.time_stop_setup(p, s, t); assert p["time_stop_n"] == n, s
    p = P("crypto"); BK.time_stop_setup(p, "crypto", t); assert p["time_stop_unit"] == "1h" and p["time_stop_deadline_utc"].startswith("2026-10-21T03:00")
def test_trend_above_20dma_gets_20():
    BK.DMA20_FN = lambda *a: True; p = P("trend"); BK.time_stop_setup(p, "trend", dt.datetime(2026, 10, 20, 4, tzinfo=U)); assert p["time_stop_n"] == 20
def test_iris_readout_and_fallback():
    t = dt.datetime(2026, 10, 20, 4, tzinfo=U)
    p = P("healthcare", readout_date="2026-10-22"); BK.time_stop_setup(p, "healthcare", t); assert p["time_stop_deadline_utc"].startswith("2026-10-24")
    p = P("healthcare"); BK.time_stop_setup(p, "healthcare", t); assert p["time_stop_n"] == 7 and "FALLBACK" in p["time_stop_note"]
def test_quinn_event_window():
    p = P("news_filings", event_window_end_utc="2026-10-21T00:00:00+00:00"); BK.time_stop_setup(p, "news_filings", dt.datetime(2026, 10, 20, 4, tzinfo=U))
    assert p["time_stop_deadline_utc"].startswith("2026-10-21T00:00")
def test_fires_after_n_bars_below_half_r():
    p = P("energy"); m = dict(last=101.0)   # +0.2R
    assert BK.time_stop_trigger(p, m, dt.datetime(2026, 10, 28, 12, tzinfo=U), 1.0, {}, "energy") is None   # bar 6
    assert BK.time_stop_trigger(p, m, dt.datetime(2026, 10, 30, 1, tzinfo=U), 1.0, {}, "energy") == "rail_time_stop"
def test_peak_spares_trade():
    p = P("energy", rail_hwp=103.0)   # touched +0.6R
    assert BK.time_stop_trigger(p, dict(last=99.0), dt.datetime(2026, 11, 5, tzinfo=U), 1.0, {}, "energy") is None
def test_never_extended():
    p = P("energy"); t = dt.datetime(2026, 10, 20, 4, tzinfo=U); BK.time_stop_setup(p, "energy", t); d0 = p["time_stop_deadline_utc"]
    BK.TIME_STOP_N["energy"] = (99, "1d")
    try: BK.time_stop_setup(p, "energy", t); assert p["time_stop_deadline_utc"] == d0
    finally: BK.TIME_STOP_N["energy"] = (7, "1d")
def test_missing_mark_holds():
    p = P("energy"); late = dt.datetime(2026, 12, 1, tzinfo=U)
    assert BK.time_stop_trigger(p, None, late, 1.0, {}, "energy") is None
    assert BK.time_stop_trigger(p, dict(last=None), late, 1.0, {}, "energy") is None
def test_no_halfway_stop():
    assert not hasattr(BK, "RAIL_HALFWAY_STOP_R")
    p = P("energy"); assert BK.time_stop_trigger(p, dict(last=96.0), dt.datetime(2026, 10, 21, tzinfo=U), 1.0, {}, "energy") is None   # early, losing: plain stop owns it
