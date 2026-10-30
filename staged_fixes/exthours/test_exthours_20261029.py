"""2026-10-29 exthours: gate_v2 / arrival check judge US names on the freshest print (regular / pre / post <= 30 min). PAPER."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import feeds as FD, gate_v2 as G, desk as D
from veteran_desk.tests.test_gate_v2 import HIST, ENV, CH, DA, asof, P0
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 29, 8, 12, tzinfo=UTC)           # 04:12 ET Thu = US pre-market (16:12 WITA)
REG_T = dt.datetime(2026, 10, 28, 20, 0, tzinfo=UTC)          # 16:00 ET regular close
def info(pre=None, pre_t=None, post=None, post_t=None, reg=181.43, state="PRE"):
    ts = lambda d: d.timestamp() if d else None
    return lambda s: dict(marketState=state, regularMarketPrice=reg, regularMarketTime=ts(REG_T), preMarketPrice=pre, preMarketTime=ts(pre_t), postMarketPrice=post, postMarketTime=ts(post_t))
NOIB = lambda s: None
def test_premarket_gap_up_uses_pre_print_and_passes_stop():
    q = FD.ext_quote("GKOS", NOW, info(pre=212.5, pre_t=NOW - dt.timedelta(minutes=3), post=213.38, post_t=NOW - dt.timedelta(hours=8)), NOIB)
    assert q["session"] == "PRE" and q["last"] == 212.5 and q["fresh"] and q["fallback"] is None
    p = dict(ticker="GKOS", side="LONG", entry=210.0, stop=197.5, target=240.0)
    assert D.live_check(p, px_fn=lambda s: FD.fresh_px(s, now_utc=NOW, info_fn=info(pre=212.5, pre_t=NOW - dt.timedelta(minutes=3)), ibkr_fn=NOIB)) is None
    # old behaviour (regular close) would have rejected it
    assert "already through stop" in D.live_check(p, px_fn=lambda s: dict(last=181.43))
def test_premarket_gap_down_through_stop_rejected_with_session():
    q = FD.ext_quote("GKOS", NOW, info(reg=205.0, pre=190.0, pre_t=NOW - dt.timedelta(minutes=1)), NOIB)
    assert q["session"] == "PRE" and q["last"] == 190.0
    c = HIST[0][-1][4]; p = P0(ticker="GKOS"); qq = dict(q, last=p["stop"] * 0.97)
    g = G.gate(p, None, CH, DA, asof(), 0, hist=HIST, env=ENV, quote=qq)
    assert not g["passed"] and any("already through stop" in r and "PRE print" in r for r in g["reasons"])
    assert g["quote_session"] == "PRE" and g["live_quote"]["session"] == "PRE"
    pd = dict(ticker="GKOS", side="LONG", entry=210.0, stop=197.5, target=240.0)
    assert "already through stop" in D.live_check(pd, px_fn=lambda s: FD.fresh_px(s, now_utc=NOW, info_fn=info(reg=205.0, pre=190.0, pre_t=NOW - dt.timedelta(minutes=1)), ibkr_fn=NOIB))
def test_stale_extended_print_falls_back_to_regular():
    q = FD.ext_quote("GKOS", NOW, info(post=213.38, post_t=NOW - dt.timedelta(minutes=45)), NOIB)
    assert q["session"] == "REGULAR" and q["last"] == 181.43 and not q["fresh"] and "regular" in q["fallback"]
    assert any(c["session"] == "POST" for c in q["candidates"])
def test_ibkr_backup_when_yahoo_has_no_fresh_print():
    ib = lambda s: dict(last=211.0, asof=(NOW - dt.timedelta(minutes=2)).isoformat())
    q = FD.ext_quote("GKOS", NOW, info(), ib)
    assert q["session"] == "PRE" and q["last"] == 211.0 and q["src"].startswith("ibkr") and q["fresh"]
def test_gate_passes_on_fresh_premarket_print_and_records_session():
    p = P0(ticker="GKOS"); q = dict(last=p["entry"], session="PRE", asof=NOW.isoformat(), src="yahoo:preMarketPrice", fresh=True, fallback=None)
    g = G.gate(p, None, CH, DA, asof(), 0, hist=HIST, env=ENV, quote=q)
    assert not any("through stop" in r for r in g["reasons"]) and g["quote_session"] == "PRE" and g["quote_last"] == p["entry"]
def test_non_us_name_unaffected():
    assert FD.ext_quote("BHP.AX", NOW, info(pre=1.0, pre_t=NOW)) is None
    assert FD.ext_quote("KRAKEN:XBTUSD", NOW) is None and FD.ext_quote("BTC-USD", NOW) is None and FD.ext_quote("7203.T", NOW) is None
    base = dict(last=57.52, asof="x")
    assert FD.fresh_px("BHP.AX", base_fn=lambda s: base) is base
    g0 = G.gate(P0(ticker="BHP.AX"), None, CH, DA, asof(), 0, hist=HIST, env=ENV)
    assert g0["live_quote"] is None
