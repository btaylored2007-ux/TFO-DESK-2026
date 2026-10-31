"""2026-10-29 PAPER: insufficient_cash check in book.tick - arm, fill and off-hours rebook paths (bak .bak-20261029-1338-cashcheck).
A paper order may never take seat cash below zero: rejected / cancelled, never resized. FX is unchanged (only the cost leaves cash).
Fixtures are temp books built from a copy of the live book; nothing live is written (conftest guard)."""
import copy, datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk import execution_rules as XR
import funder_gate as FG
from veteran_desk import payouts_bridge as PB
import pytest
@pytest.fixture(autouse=True)
def _legacy_cash_rule(monkeypatch): monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)   # tfo_review_20261030: these test the raw cash rule; 30% headroom tested in test_review_20261030
U = dt.timezone.utc; T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=U)   # Wed 14:45 New York: US session open, past the open window
AU = 0.65; C_US = BK.COST_RT.get("US", .002) / 2; C_LEG = BK.COST_RT_LEGACY_20261029["US"] / 2   # costmodel 20261030: new fills cost = leg_cost_aud entry; pre-cutover audits = legacy half-RT
_PATCH = ("BOOK", "now", "fallback_quote", "posture", "firm_ok", "first_print_after_open", "session_open")
def _book(seat, cash, positions):
    D = copy.deepcopy(json.load(open(BK._REAL_BOOK)))
    for B in D["seats"].values(): B["positions"] = {}
    D["hedge"] = {}; D["kill_switch"] = {}; D["peak_equity_aud"] = 1.0   # peak = this temp book's own equity: stripping live positions must not trip the -12% kill-switch FLAT
    D["seats"][seat]["cash_aud"] = float(cash); D["seats"][seat]["positions"] = positions
    return D
def _tick(D, entries=None, marks=None, session=None, fp=None):
    tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "b.json"); q = os.path.join(tmp, "q.json")
    json.dump(D, open(p, "w")); json.dump({"entries": entries or {}}, open(q, "w"))
    old = {k: getattr(BK, k) for k in _PATCH}; old_pt = XR.pre_trade; old_bc = PB.book_commissions; marks = marks or {}
    try:
        BK.BOOK = p; BK.now = lambda: T; BK.fallback_quote = lambda s: None; BK.posture = lambda: ("calm", "risk-off"); BK.firm_ok = lambda: (True, "OK")
        if session is not None: BK.session_open = session
        if fp is not None: BK.first_print_after_open = fp
        XR.pre_trade = lambda *a, **k: {"ok": True}
        PB.book_commissions = lambda *a, **k: None   # desk commission layer is order-dependent across the suite; these tests check cash vs notional + half-RT cost only
        out = BK.tick(price_fn=lambda s: dict(marks[s], asof=T.isoformat()) if s in marks else None, au=AU, queue_path=q)
        return out, json.load(open(q))["entries"]
    finally:
        for k, v in old.items(): setattr(BK, k, v)
        XR.pre_trade = old_pt; PB.book_commissions = old_bc
# ---------- arm ----------
def _entry(eid, side="LONG"):
    L = side == "LONG"
    return dict(id=eid, book="veteran", ticker="BAC", side=side, entry=40.0, stop=39.0 if L else 41.0, target=42.0 if L else 38.0, state=FG.WAIT, decision="approve",
                grade="A", seat_risk_pct=.02, tags=dict(strategy="veteran:banks grade A"), order_type="limit")   # tfo_review_fix_20261031: A now 1%; use B for 2% mechanics
def _reserved_long():   # an armed LONG already reserves 100 x 50 USD = A$7,692 (+ half RT cost) of seat cash
    return {"RES1": dict(id="RES1", seat="banks", ticker="WFC", side="LONG", entry=50.0, stop=48.0, target=55.0, shares=100, risk_aud=100.0, status="armed",
                         armed_utc=T.isoformat(), venue="US", sleeve="core", order_type="limit")}
MK = {"BAC": dict(last=40.5, hi=40.6, lo=40.4), "WFC": dict(last=50.5, hi=50.6, lo=50.4)}
CLOSED = lambda v, t: False   # no fills in the arm tick
def test_arm_rejects_insufficient_cash_and_never_resizes():
    # equity A$20k -> 2% risk A$400 / (1 USD stop = A$1.538) = 260 sh = A$16,000 notional (inside the 1x cap); cash 20,000 - 7,692 reserved - 16,016 < 0
    D, Q = _tick(_book("banks", 20000, _reserved_long()), {"CASH1": _entry("CASH1")}, MK, session=CLOSED)
    assert "CASH1" not in D["seats"]["banks"]["positions"]
    assert Q["CASH1"]["state"] == "expired" and Q["CASH1"]["why"] == "insufficient_cash"
    assert ("CASH1", "blocked", "insufficient_cash") in [tuple(e) for e in D["last_events"]]
    assert abs(D["seats"]["banks"]["cash_aud"] - 20000) < 1e-6   # nothing debited
def test_arm_passes_when_cash_covers_notional_plus_cost():
    D, Q = _tick(_book("banks", 20000, {}), {"CASH2": _entry("CASH2")}, MK, session=CLOSED)
    x = D["seats"]["banks"]["positions"]["CASH2"]
    assert x["status"] == "armed" and x["shares"] == 260 and Q["CASH2"]["state"] == "armed"
def test_arm_short_needs_only_the_cost():
    D, Q = _tick(_book("banks", 20000, _reserved_long()), {"CASH3": _entry("CASH3", "SHORT")}, MK, session=CLOSED)
    assert D["seats"]["banks"]["positions"]["CASH3"]["status"] == "armed", Q["CASH3"]
# ---------- fill ----------
def _armed_bac(shares=260):
    return {"F1": dict(id="F1", seat="banks", ticker="BAC", side="LONG", entry=40.0, stop=39.0, target=42.0, shares=shares, risk_aud=400.0, status="armed",
                       armed_utc=(T - dt.timedelta(minutes=30)).isoformat(), venue="US", sleeve="core", order_type="limit")}
FILL_BAR = {"BAC": dict(last=39.9, hi=40.2, lo=39.8)}   # touched the 40.00 limit -> true limit: fill AT 40.00 (railfix 14:19)
def test_fill_rejects_insufficient_cash():
    D, _ = _tick(_book("banks", 10000, _armed_bac()), marks=FILL_BAR)   # needs 260 x 40 / .65 = A$16,000 + cost
    B = D["seats"]["banks"]; assert "F1" not in B["positions"]   # cancelled orders leave the live positions map
    x = B["closed"][-1]
    assert x["id"] == "F1" and x["status"] == "cancelled" and x["why"] == "insufficient_cash" and "fill" not in x
    assert abs(B["cash_aud"] - 10000) < 1e-6
    assert B["closed"][-1]["id"] == "F1" and B["closed"][-1]["exit_reason"] == "insufficient_cash"
    assert ("F1", "blocked", "insufficient_cash") in [tuple(e) for e in D["last_events"]]
def test_fill_books_when_cash_suffices():
    D, _ = _tick(_book("banks", 20000, _armed_bac()), marks=FILL_BAR)
    B = D["seats"]["banks"]; x = B["positions"]["F1"]; n = 40.0 * 260 / AU
    assert x["status"] == "open" and x["fill"] == 40.0 and x["shares"] == 260
    ce = BK.leg_cost_aud(x, 40.0, 260, AU, None, "entry"); assert abs(x["cost_aud"] - round(ce, 2)) < 1e-9
    assert abs(B["cash_aud"] - (20000 - n - ce)) < 0.01
def test_fill_fx_exempt_only_cost_leaves_cash():
    pos = {"FX1": dict(id="FX1", seat="macro", ticker="EURUSD=X", side="LONG", entry=1.1100, stop=1.0989, target=1.1433, shares=1000, risk_aud=10.0, status="armed",
                       armed_utc=(T - dt.timedelta(minutes=30)).isoformat(), venue="FX", sleeve="core", order_type="limit")}
    D, _ = _tick(_book("macro", 500, pos), marks={"EURUSD=X": dict(last=1.1090, hi=1.1095, lo=1.1085)})   # notional still above the A$500 cash; FX debits cost only. Size stays inside the 5% fill-time cap.
    x = D["seats"]["macro"]["positions"]["FX1"]
    assert x["status"] == "open" and x["fill"] == 1.1100 and D["seats"]["macro"]["cash_aud"] > 0
# ---------- off-hours rebook ----------
def _rebook_book(cash_before_unwind):
    n0 = 68.23 * 1000 / AU; cost0 = n0 * C_LEG   # unrounded so the book's n0 = cost/c unwind is exact
    pos = {"O1": dict(id="O1", seat="energy", ticker="SPY", side="LONG", entry=400.0, fill=400.0, stop=300.0, target=500.0, shares=1000, risk_aud=1000.0, status="open",
                      filled_utc="2026-10-28T14:00:00+00:00", cost_aud=600.0, venue="US", sleeve="core", off_hours_checked=True, scale_t1=False),
           "R1": dict(id="R1", seat="energy", ticker="XLE", side="LONG", entry=68.23, fill=68.23, stop=50.0, target=90.0, shares=1000, risk_aud=28046.0, status="open",
                      filled_utc="2026-10-28T12:00:00+00:00", cost_aud=cost0, venue="US", sleeve="core", off_hours_checked=True, off_hours_fill=True, rebook_pending=True, scale_t1=False,
                      off_hours_fill_audit=dict(fill=68.23, filled_utc="2026-10-28T12:00:00+00:00", cost_aud=cost0, shares=1000, risk_aud=28046.0))}
    return _book("energy", cash_before_unwind, pos), n0, cost0
RB_MARKS = {"SPY": dict(last=400.0, hi=401.0, lo=399.0), "XLE": dict(last=66.2, hi=66.5, lo=65.9)}
FP = lambda s, t: dict(px=66.0, asof="2026-10-28 09:30:00-04:00") if s == "XLE" else None
# first print 66.00 inside the 68.23 limit (railfix: never rebook through the limit); per = 16/.65 = A$24.6k on ~A$710-760k eqx -> ~3.3% risk:
# inside the 2% floor (railfix reject) and the 5% cap, under the 1x notional cap - only the cash check decides
def test_rebook_rejects_insufficient_cash():
    Dx, n0, cost0 = _rebook_book(-10000)   # the unchecked off-hours fill had already taken seat cash negative
    D, _ = _tick(Dx, marks=RB_MARKS, fp=FP)   # unwound cash ~A$95.1k < 1000 x 66 / .65 = A$101,538 + cost
    B = D["seats"]["energy"]; assert "R1" not in B["positions"]; x = B["closed"][-1]
    assert x["status"] == "cancelled" and x["why"] == "insufficient_cash" and x["rebook_pending"] is False
    assert B["closed"][-1]["id"] == "R1" and B["closed"][-1]["exit_reason"] == "insufficient_cash"
    assert abs(B["cash_aud"] - (-10000 + n0 + cost0)) < 0.05   # the off-hours booking stays unwound; nothing re-debited
    assert ("R1", "blocked", "insufficient_cash") in [tuple(e) for e in D["last_events"]]
def test_rebook_books_when_cash_suffices():
    Dx, n0, cost0 = _rebook_book(50000)
    D, _ = _tick(Dx, marks=RB_MARKS, fp=FP)
    B = D["seats"]["energy"]; x = B["positions"]["R1"]; n = 66.0 * 1000 / AU
    assert x["status"] == "open" and x["fill"] == 66.0 and x["rebook_pending"] is False and x["shares"] == 1000
    ce = BK.leg_cost_aud(x, 66.0, 1000, AU, None, "entry"); assert abs(B["cash_aud"] - (50000 + n0 + cost0 - n - ce)) < 0.05
