import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.synthetic_book import skeleton
U = dt.timezone.utc
PID = "TEST-XLE-fixture-energy"   # TFO HT 03:28: synthetic fixture position - no dependency on live position IDs
def _fixture():
    D = copy.deepcopy(skeleton())
    for B in D["seats"].values(): B["positions"] = {}
    D["hedge"] = {}; D.pop("hedge_realised_aud", None); D["peak_equity_aud"] = 1.0; D["kill_switch"] = dict(state="OK")   # fixer 2026-10-29: clearing live positions must not trip the -12% kill switch
    D["seats"]["energy"]["positions"][PID] = dict(id=PID, seat="energy", ticker="XLE", side="LONG", entry=68.0, stop=66.0, target=72.6, shares=100, risk_aud=300.0, status="open",
        fill=68.0, filled_utc="2026-10-28T13:35:00+00:00", armed_utc="2026-10-28T12:15:30+00:00", venue=BK.P.venue_of("XLE"), cost_aud=5.0, sleeve="core", order_type="limit")
    return D
def test_fallback_and_stale():
    tmp = tempfile.mkdtemp(); old = (BK.BOOK, BK.now, BK.fallback_quote, BK._price_alert); al = []
    try:
        p = os.path.join(tmp, "b.json"); json.dump(_fixture(), open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
        BK.BOOK = p; BK.now = lambda: dt.datetime(2026, 10, 28, 13, 5, tzinfo=U); BK.fallback_quote = lambda s: dict(last=68.1, asof="2026-10-27", src="test_fb") if s == "XLE" else None; BK._price_alert = al.append
        D = BK.tick(price_fn=lambda s: None, au=0.65, queue_path=q); x = D["seats"]["energy"]["positions"][PID]
        assert x["mark_missing"] and x["mark"] == 68.1 and x["mark_src"] == "test_fb" and any("fallback" in str(a) for a in al)
        BK.now = lambda: dt.datetime(2026, 10, 28, 14, 0, tzinfo=U); al.clear()
        D = BK.tick(price_fn=lambda s: dict(last=68.3, hi=68.4, lo=68.2, asof="2026-10-28 09:30:00-04:00"), au=0.65, queue_path=q)
        assert any(a.get("age_min", 0) > 15 and a["level"] == "ALERT" for a in al), al
    finally: BK.BOOK, BK.now, BK.fallback_quote, BK._price_alert = old
