"""tfo_costmodel_20261030 rulings 2/3/4/5: stale/closed trigger hold, peak re-baseline at cutover, ADV refresh on its own key, one cost source. PAPER."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, cost_tables as CT
from veteran_desk.tests import test_exit_rail_20261029 as XR
import pytest
U = dt.timezone.utc
T_OPEN = XR.T                                          # US session open
T_CLOSED = dt.datetime(2026, 10, 28, 12, 0, tzinfo=U)   # 08:00 New York - closed
@pytest.fixture(autouse=True)
def _adv(monkeypatch): monkeypatch.setattr(BK, "ADV_FN", lambda p, au: 300e6)
def tick(pos, bar, t, age_min=0.0):
    path, q = XR._book(pos)
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN)
    try:
        BK.BOOK = path; BK.now = lambda: t; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: 0.1; BK.BARS_FN = lambda s, c: None; BK.NEWS_FN = lambda p: None
        D = BK.tick(price_fn=lambda s: dict(bar, asof=(t - dt.timedelta(minutes=age_min)).isoformat()), au=0.70, queue_path=q)
    finally: BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN = old
    s = D["seats"]["energy"]; return D["last_events"], s["positions"].get("P1"), s["closed"]
def _names(ev): return [e[1] for e in ev]
def test_giveback_fires_open_held_closed():
    pos = lambda: XR._pos(gb_peak_net_R=2.0, rail_hwp=101.1, initial_stop=98.0, r0=2.0)
    bar = dict(last=101.0, hi=101.1, lo=100.9)          # ~0.4R net, well under half of peak 2R
    ev, p, _ = tick(pos(), bar, T_OPEN); assert "rail_giveback_protect" in _names(ev)
    ev, p, _ = tick(pos(), bar, T_CLOSED); assert "rail_giveback_protect" not in _names(ev) and p["shares"] == 100 and p["status"] == "open"
def test_giveback_held_on_stale_mark_in_session():
    pos = XR._pos(gb_peak_net_R=2.0, rail_hwp=101.1, initial_stop=98.0, r0=2.0)
    ev, p, _ = tick(pos, dict(last=101.0, hi=101.1, lo=100.9), T_OPEN, age_min=240)
    assert "rail_giveback_protect" not in _names(ev) and p["shares"] == 100
def test_fullflat_3R_held_closed_but_fires_open():
    pos = lambda: XR._pos(bank_capped=True, t1_done=True)
    bar = dict(last=107.0, hi=107.0, lo=106.9)          # +3.5R gross
    ev, _, _ = tick(pos(), bar, T_OPEN); assert any(n.startswith("full_flat") for n in _names(ev))
    ev, p, _ = tick(pos(), bar, T_CLOSED); assert not any(n.startswith("full_flat") for n in _names(ev)) and p["status"] == "open" and p["trigger_held"]["why"] == "market_closed"
def test_backstop_and_hard_stop_still_act_when_closed():
    ev, p, c = tick(XR._pos(r=10.0), dict(last=94.5, hi=94.6, lo=94.4), T_CLOSED)   # -5.5%, stop 90 not hit
    assert "hard_backstop_5pct" in _names(ev) and c[-1]["exit_reason"] == "hard_backstop_5pct"
    ev, p, c = tick(XR._pos(), dict(last=97.5, hi=98.5, lo=97.5), T_CLOSED)
    assert "stop" in _names(ev) and c[-1]["exit_reason"] == "stop"
def test_revguard_held_closed():
    pos = lambda: XR._pos(bank_capped=True, revguard_peak_net_R=3.0, rail_hwp=106.0, initial_stop=98.0, r0=2.0)
    bar = dict(last=103.0, hi=103.1, lo=102.9)
    ev, _, _ = tick(pos(), bar, T_OPEN); assert "revguard_alert" in _names(ev)
    ev, _, _ = tick(pos(), bar, T_CLOSED); assert "revguard_alert" not in _names(ev)
def test_rebaseline_once_shifts_peaks_by_exit_cost_delta():
    pos = XR._pos(bank_capped=True, gb_peak_net_R=1.2, ts_peak_net_R=1.2, rail_hwp=103.0, initial_stop=98.0, r0=2.0)
    ev, p, _ = tick(pos, dict(last=102.0, hi=102.1, lo=101.9), T_CLOSED)
    rb = p["cm_rebaseline"]; q = dict(p, adv_aud=300e6)
    d = (BK._legacy_exit_aud(q, 103.0, 0.70) - BK.leg_cost_aud(q, 103.0, 100, 0.70, None, "exit")) / BK.risk0_aud(q, 0.70)
    assert abs(rb["delta_R"] - round(d, 4)) < 1e-4 and rb["gb_peak_net_R_old"] == 1.2 and abs(p["gb_peak_net_R"] - round(1.2 + d, 4)) < 1e-4
    D2 = dict(p); BK.cm_rebaseline(D2, 102.0, 0.70, None, T_CLOSED); assert D2["gb_peak_net_R"] == p["gb_peak_net_R"]   # once only
def test_adv_refreshed_even_when_atr_cached_today():   # cause of the TCL.AX A$339.2 vs A$260.9 mismatch
    dk = T_OPEN.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    ev, p, _ = tick(XR._pos(bank_capped=True, atr14=0.1, atr14_day=dk), dict(last=100.5, hi=100.6, lo=100.4), T_OPEN)
    assert p["adv_aud"] == 300e6 and p["adv_day"] == dk
    tick_exit = BK.leg_cost_aud(p, 100.5, 100, 0.70, None, "exit"); prev = BK.leg_cost_aud(dict(p, adv_aud=BK.ADV_FN(p, 0.70)), 100.5, 100, 0.70, None, "exit")
    assert abs(tick_exit - prev) < 1e-9
def test_one_source_tables():
    from veteran_desk import desk as DK, gate_v2 as G
    assert DK.COST_RT == CT.venue_rt_table() == G.COST_RT == BK.COST_RT
    assert BK.CM_COMM is CT.CM_COMM and BK.CM_TAX is CT.CM_TAX
    assert CT.spread_bps("XLE", "US") == CT.CM_SPREAD_TIER_BPS["US_ETF"] and CT.spread_bps("VEEV", "US") == CT.CM_SPREAD_TIER_BPS["US"]
    p = dict(ticker="ZZZ.AX", venue="ASX", side="LONG", adv_aud=60e6)
    legs = (BK.leg_cost_aud(p, 10.0, 100000, 0.70, None, "entry") + BK.leg_cost_aud(p, 10.0, 100000, 0.70, None, "exit")) / 1e6
    assert abs(CT.est_rt_rate("ZZZ.AX", "ASX", 10.0, 60e6) - legs) < 1e-9
