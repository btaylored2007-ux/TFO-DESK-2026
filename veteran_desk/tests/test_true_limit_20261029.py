import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.synthetic_book import skeleton
U = dt.timezone.utc; T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=U)   # US + FX open, past open window
def _run(side, entry, bar, ot=None, tk="EURUSD=X"):
    tmp = tempfile.mkdtemp(); old = (BK.BOOK, BK.now, BK.fallback_quote)
    D = copy.deepcopy(skeleton())
    for B in D["seats"].values(): B["positions"] = {}
    D["hedge"] = {}; D["peak_equity_aud"] = 1.0; D["kill_switch"] = dict(state="OK")   # fixer 2026-10-29: clearing live positions must not trip the -12% kill switch
    pos = dict(id="T1", seat="macro", ticker=tk, side=side, entry=entry, stop=entry * (1.01 if side == "SHORT" else .99), target=entry * (.97 if side == "SHORT" else 1.03), shares=1000, risk_aud=10.0,
               status="armed", armed_utc=(T - dt.timedelta(minutes=30)).isoformat(), venue=BK.P.venue_of(tk), sleeve="core")
    if ot: pos["order_type"] = ot
    D["seats"]["macro"]["positions"]["T1"] = pos
    p = os.path.join(tmp, "b.json"); json.dump(D, open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    try:
        BK.BOOK = p; BK.now = lambda: T; BK.fallback_quote = lambda s: None
        D = BK.tick(price_fn=lambda s: dict(bar, asof=T.isoformat()) if s == tk else None, au=0.70, queue_path=q)
    finally: BK.BOOK, BK.now, BK.fallback_quote = old
    return D["seats"]["macro"]["positions"].get("T1") or {}
def test_sell_limit_never_below_limit():
    x = _run("SHORT", 1.1174, dict(last=1.11682, hi=1.1176, lo=1.1160))      # Theo's bar: touched 1.1174 then traded lower
    assert x["status"] == "open" and x["fill"] == 1.1174 and x["order_type"] == "limit" and x["slippage_bps"] <= 0
    x = _run("SHORT", 1.1174, dict(last=1.1190, hi=1.1195, lo=1.1180, open=1.1192))   # railfix 14:19: bar OPENED above limit (gap) -> fill at open (better)
    assert x["fill"] == 1.1192
    x = _run("SHORT", 1.1174, dict(last=1.1190, hi=1.1195, lo=1.1180)); assert x["fill"] == 1.1174   # no open -> at the limit, never chased
    x = _run("SHORT", 1.1174, dict(last=1.1160, hi=1.1170, lo=1.1150))       # never reached limit -> no fill
    assert x["status"] == "armed"
def test_buy_limit_never_above_limit():
    x = _run("LONG", 1.1100, dict(last=1.1120, hi=1.1125, lo=1.1098)); assert x["status"] == "open" and x["fill"] == 1.1100
    x = _run("LONG", 1.1100, dict(last=1.1090, hi=1.1095, lo=1.1085, open=1.1093)); assert x["fill"] == 1.1093   # gap open below limit -> open
    x = _run("LONG", 1.1100, dict(last=1.1090, hi=1.1095, lo=1.1085)); assert x["fill"] == 1.1100
    x = _run("LONG", 1.1100, dict(last=1.1120, hi=1.1130, lo=1.1101)); assert x["status"] == "armed"
def test_stop_limit_explicit():
    x = _run("SHORT", 1.1174, dict(last=1.11682, hi=1.1176, lo=1.1160), ot="stop_limit")
    assert x["status"] == "open" and x["order_type"] == "stop_limit" and x["fill"] == 1.11682
    x = _run("SHORT", 1.1174, dict(last=1.1150, hi=1.1176, lo=1.1140), ot="stop_limit"); assert x["status"] == "armed"   # beyond protective limit
