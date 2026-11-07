"""Boundary checks for the t2808u paper decisions. These decisions are effective now. They are not historical quotes."""
import datetime as dt

from veteran_desk import book as BK
from veteran_desk.tests import test_exit_rail_20261029 as XR
from veteran_desk.tests.test_gate_v2 import g, P0
from veteran_desk.tests.test_railfix_20261029 import _book, _offhours, _tick
from veteran_desk.tests.test_stalefill_20261029 import _arm, _run

U = dt.timezone.utc
CLOSED = dt.datetime(2026, 10, 28, 12, 0, tzinfo=U)


def _closed_tick(pos, bar):
    path, q = XR._book(pos)
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN)
    try:
        BK.BOOK = path
        BK.now = lambda: CLOSED
        BK.fallback_quote = lambda s: None
        BK.ATR_FN = lambda s: 0.1
        BK.BARS_FN = lambda s, c: None
        BK.NEWS_FN = lambda p: None
        D = BK.tick(price_fn=lambda s: dict(bar, asof=CLOSED.isoformat()), au=0.70, queue_path=q)
    finally:
        BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN = old
    return D


def test_half_bank_still_books_on_a_fresh_print_while_the_session_is_closed():
    assert BK.RAIL_BANK_R == 0.7
    D = _closed_tick(XR._pos(target=112.0, px_age_min=1), dict(last=101.6, hi=101.7, lo=101.2))
    half = [c for c in D["seats"]["energy"]["closed"] if c["id"] == "P1-T1"]
    assert half and half[0]["exit_reason"] == "rail_0_7R_aud_scale_out" and half[0]["shares"] == 50


def test_giveback_queues_on_a_preopen_us_print_and_does_not_book_it():
    now = dt.datetime(2026, 10, 28, 13, 40, tzinfo=U)   # session open; the print began before 09:30 ET
    pos = XR._pos(gb_peak_net_R=2.0, rail_hwp=101.1, initial_stop=98.0, r0=2.0)
    path, q = XR._book(pos)
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN)
    try:
        BK.BOOK = path
        BK.now = lambda: now
        BK.fallback_quote = lambda s: None
        BK.ATR_FN = lambda s: 0.1
        BK.BARS_FN = lambda s, c: None
        BK.NEWS_FN = lambda p: None
        D = BK.tick(price_fn=lambda s: dict(last=101.0, hi=101.1, lo=100.9, asof="2026-10-28T13:25:00+00:00"), au=0.70, queue_path=q)
    finally:
        BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN = old
    p = D["seats"]["energy"]["positions"]["P1"]
    names = [e[1] for e in D["last_events"]]
    assert "rail_giveback_protect" not in names and p["status"] == "open" and p["shares"] == 100
    assert str(p.get("exit_queued", {}).get("reason") or "").startswith("rail_giveback")


def test_a_print_from_before_the_order_does_not_fill():
    T = dt.datetime(2026, 10, 29, 15, 0, tzinfo=U)
    bar = dict(last=101.0, hi=101.0, lo=99.0, open=100.0, asof=T.isoformat(), window_start="2026-10-29T10:00:00+00:00")
    p = _run(_arm("ISRG", "US", 100, 90, 130, shares=10), "healthcare", T, bar)
    assert p["status"] == "armed"


def test_min_lot_above_the_rebook_budget_names_the_reason():
    path, q = _book(_offhours(68.1, stop=68.2 - 20000))
    D = _tick(path, q, dict(last=68.1, hi=68.2, lo=68.0), fp=dict(px=68.1, asof="2026-10-28 09:30:00-04:00"))
    closed = D["seats"]["energy"]["closed"]
    assert closed and closed[-1]["exit_reason"].startswith("min_lot_exceeds_permitted_risk")
    assert "P1" not in D["seats"]["energy"]["positions"] or D["seats"]["energy"]["positions"]["P1"]["status"] == "cancelled"


def test_firm_halt_and_grade_b_budget_stay_distinct():
    assert not g(env=dict(book=([], "OK"), firm="HALT"))["passed"]
    assert abs(BK.risk_plan(500000, "B", seat_pct=0.02)["pct"] - 0.01) < 1e-9
    assert BK.risk_plan(500000, "A", seat_pct=0.02)["pct"] == 0.02
    assert g(P0(stop=0))["passed"] is False
