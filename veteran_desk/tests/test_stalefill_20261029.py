"""stalefill (TFO 19:21 WITA 29 Oct 2026): no fill on a stale price / closed session; no fill when room to target from fill < 1.0R. PAPER."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.test_exit_rail_20261029 import _book
U = dt.timezone.utc
def _arm(tk, venue, entry, stop, target, seat="macro", side="LONG", shares=100):
    return dict(id="A1", seat=seat, ticker=tk, side=side, entry=entry, stop=stop, target=target, shares=shares, risk_aud=100.0, status="armed", venue=venue,
                order_type="limit", armed_utc="2026-10-29T11:00:00+00:00", scale_t1=True, grade="A", sleeve="core")
def _run(pos, seat, T, bar):
    path, q = _book(pos, seat)
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.in_open_window)
    try:
        BK.BOOK = path; BK.now = lambda: T; BK.fallback_quote = lambda s: None; BK.firm_ok = lambda: (True, ""); BK.posture = lambda: (False, "risk-off"); BK.in_open_window = lambda v, t: False
        D = BK.tick(price_fn=lambda s: dict(bar), au=0.70, queue_path=q)
    finally: BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.in_open_window = old
    return D["seats"][seat]["positions"].get("A1")
def test_tse_closed_no_fill():   # the 6098.T case: 19:20 WITA = 20:20 JST, price from 15:20 JST
    T = dt.datetime(2026, 10, 29, 11, 20, tzinfo=U)
    p = _run(_arm("6098.T", "TSE", 18955, 18106, 20652), "macro", T, dict(last=18955.0, hi=18975.0, lo=18870.0, asof="2026-10-29 15:20:00+09:00"))
    assert p["status"] == "armed" and "session closed" in p["exec_note"]
def test_stale_price_no_fill_in_session():
    T = dt.datetime(2026, 10, 29, 1, 0, tzinfo=U)   # 10:00 JST, session open
    p = _run(_arm("6098.T", "TSE", 18955, 18106, 20652), "macro", T, dict(last=18955.0, hi=18975.0, lo=18870.0, asof="2026-10-28 15:20:00+09:00"))
    assert p["status"] == "armed" and "stale" in p["exec_note"]
def test_fresh_price_fills_in_session():
    T = dt.datetime(2026, 10, 29, 1, 0, tzinfo=U)
    p = _run(_arm("6098.T", "TSE", 18955, 18106, 20652, shares=10), "macro", T, dict(last=18950.0, hi=18975.0, lo=18900.0, asof=(T - dt.timedelta(minutes=2)).isoformat()))
    assert p["status"] == "open" and p["fill"] == 18955
def test_room_under_1R_no_fill():
    T = dt.datetime(2026, 10, 29, 15, 0, tzinfo=U)   # 11:00 New York
    p = _run(_arm("ISRG", "US", 436, 421.5, 448, seat="healthcare"), "healthcare", T, dict(last=435.0, hi=437.0, lo=434.0, asof=T.isoformat()))
    assert p["status"] == "armed" and "room to target" in p["exec_note"]
    p = _run(_arm("ISRG", "US", 436, 421.5, 468, seat="healthcare", shares=10), "healthcare", T, dict(last=435.0, hi=437.0, lo=434.0, asof=T.isoformat()))
    assert p["status"] == "open"
def test_rule_log():
    assert any(r.get("id") == "tfo_stalefill_20261029_1921" for r in BK.RULE_LOG)
