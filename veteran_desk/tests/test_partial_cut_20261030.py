"""PAPER. A loss-cut partial is not a half-bank, and a rail stop does not book while the venue is closed."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC
U = dt.timezone.utc
_REAL_SESSION = BK.session_open   # captured at import, before other tests replace BK.session_open
SEED = 500000.0
SHUT = dt.datetime(2026, 10, 30, 21, 30, tzinfo=U)   # 17:30 EDT, NYSE closed
OPEN = dt.datetime(2026, 11, 2, 15, 0, tzinfo=U)     # 10:00 EST Monday

def test_loss_cut_keeps_the_stop_and_does_not_set_t1():
    """VEEV: 158 sh cut at 311 while BE+fees is still above the market. Stop must not jump through ~313."""
    t = dt.datetime(2026, 10, 30, 20, tzinfo=U)
    p = dict(ticker="VEEV", side="LONG", venue="US", fill=316.5, stop=290.0, initial_stop=290.0, r0=26.5,
             target=360.0, shares=158, cost_rt_rate=0.00173, status="open")
    be = 316.5 * (1 + 0.00173)
    assert be > 313 and 311 < be
    BK.mark_half_banked(p, t, 311.0, 158, "seat_partial_close")
    assert p["stop"] == 290.0 and p["r0"] == 26.5 and not p.get("t1_done") and p.get("target") == 360.0
    assert p["shares_orig"] == 316
    assert p["rail_log"][-1]["ev"] == "partial_cut" and p["rail_log"][-1]["px"] == 311.0

def test_profit_partial_still_half_banks():
    t = dt.datetime(2026, 10, 30, 15, tzinfo=U)
    p = dict(ticker="VEEV", side="LONG", venue="US", fill=300.0, stop=280.0, initial_stop=280.0, r0=20.0,
             target=360.0, shares=50, cost_rt_rate=0.001, status="open")
    BK.mark_half_banked(p, t, 311.0, 50, "seat_partial_close")
    assert p["t1_done"] is True and p["rail_state"] == "t1_done" and p["stop"] >= 300.0 and p["stop"] < 311.0
    assert any(e["ev"] == "half_bank" for e in p["rail_log"])

def test_short_loss_cut_does_not_move_the_stop():
    t = dt.datetime(2026, 10, 30, 15, tzinfo=U)
    p = dict(ticker="VEEV", side="SHORT", venue="US", fill=300.0, stop=320.0, initial_stop=320.0, r0=20.0,
             target=250.0, shares=40, cost_rt_rate=0.001, status="open")
    BK.mark_half_banked(p, t, 311.0, 40, "seat_partial_close")
    assert p["stop"] == 320.0 and not p.get("t1_done") and p["rail_log"][-1]["ev"] == "partial_cut"

def test_seat_cli_loss_cut_does_not_rewrite_the_residual():
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "b.json")
    seat = lambda: dict(seed_aud=SEED, cash_aud=SEED, positions={}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    p = dict(id="VEEV1", seat="healthcare", ticker="VEEV", side="LONG", venue="US", fill=316.5, entry=316.5, stop=290.0,
             initial_stop=290.0, r0=26.5, target=360.0, shares=316, cost_rt_rate=0.00173, status="open", cost_aud=0.0,
             fill_audusd=0.65, fill_notional_aud=316.5 / 0.65 * 316)
    D = dict(seats={s: seat() for s in BK.TRADERS}, hedge={"disabled": True}, kill_switch={"state": "OK"}, audusd=0.65)
    B = D["seats"]["healthcare"]; B["positions"]["VEEV1"] = p; B["cash_aud"] = SEED - p["fill_notional_aud"]
    json.dump(D, open(path, "w")); old = BK.BOOK
    try:
        BK.BOOK = path
        r = SC.close("healthcare", "VEEV1", "loss cut", price_fn=lambda s: dict(last=311.0), shares=158)
    finally:
        BK.BOOK = old
    assert r["ok"] and r["closed_shares"] == 158 and r["residual_shares"] == 158
    left = json.load(open(path))["seats"]["healthcare"]["positions"]["VEEV1"]
    assert left["stop"] == 290.0 and not left.get("t1_done") and left["shares_orig"] == 316
    assert left["rail_log"][-1]["ev"] == "partial_cut"
    closed = json.load(open(path))["seats"]["healthcare"]["closed"][-1]
    assert closed["exit"] == 311.0 and closed["shares"] == 158 and closed["exit_reason"].startswith("seat_partial_close")

def _book(pos, seat="healthcare", cash=None):
    tmp = tempfile.mkdtemp()
    seat_l = lambda: dict(seed_aud=SEED, cash_aud=SEED, positions={}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    D = dict(seats={s: seat_l() for s in BK.TRADERS}, hedge={"disabled": True}, kill_switch={"state": "OK"},
             peak_equity_aud=SEED * len(BK.TRADERS), seed_aud=SEED * len(BK.TRADERS))
    B = D["seats"][seat]; B["positions"][pos["id"]] = pos
    if cash is not None:
        for S in D["seats"].values(): S["cash_aud"] = cash
    elif pos.get("fill_notional_aud") is not None:
        B["cash_aud"] = SEED - float(pos["fill_notional_aud"]) - float(pos.get("cost_aud") or 0)
    path = os.path.join(tmp, "b.json"); json.dump(D, open(path, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    return path, q

def _rail(venue="US", ticker="VEEV"):
    return dict(id="VEEV1", seat="healthcare", ticker=ticker, side="LONG", venue=venue, entry=100.0, fill=100.0,
                stop=98.0, initial_stop=98.0, target=200.0, shares=50, status="open", cost_aud=0.0, scale_t1=True,
                t1_done=True, t2_done=True, rail_state="t2_done", grade="A", off_hours_checked=True,
                filled_utc=(SHUT - dt.timedelta(hours=6)).isoformat(), fill_audusd=0.65, fill_notional_aud=100.0 / 0.65 * 50,
                cost_rt_rate=0.001)

def _tick(path, q, when, bar, au=0.65):
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN, BK.ADV_FN)
    try:
        BK.BOOK = path; BK.now = lambda: when; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: 0.1
        BK.BARS_FN = lambda s, c: None; BK.NEWS_FN = lambda p: None; BK.ADV_FN = lambda p, au: None
        return BK.tick(price_fn=lambda s: dict(bar, atr14=0.1, asof=when.isoformat()), au=au, queue_path=q)
    finally:
        BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN, BK.ADV_FN = old

_BAR = dict(last=103.15, hi=105.2, lo=103.0)

def test_rail_stop_after_the_close_is_queued_for_the_next_open():
    saved = BK.session_open; BK.session_open = _REAL_SESSION
    try:
        assert not BK.session_open("US", SHUT) and BK.session_open("US", OPEN)
        path, q = _book(_rail())
        D = _tick(path, q, SHUT, _BAR)
        p = D["seats"]["healthcare"]["positions"]["VEEV1"]
        assert p["status"] == "open" and p["shares"] == 50 and not D["seats"]["healthcare"]["closed"]
        assert p["exit_queued"]["reason"] == "rail_stop" and p["exit_queued"]["kind"] == "full"
        D = _tick(path, q, OPEN, dict(last=104.0, hi=104.2, lo=103.8))
        assert "VEEV1" not in D["seats"]["healthcare"]["positions"]
        c = D["seats"]["healthcare"]["closed"][-1]
        assert c["exit_reason"] == "rail_stop" and c["exit"] == 104.0 and c["exit"] != 103.15
    finally:
        BK.session_open = saved

def test_crypto_rail_stop_still_books_when_us_is_closed():
    path, q = _book(_rail("CRYPTO", "BTC-USD"), "crypto")
    D = _tick(path, q, SHUT, _BAR)
    assert "VEEV1" not in D["seats"]["crypto"]["positions"]
    c = D["seats"]["crypto"]["closed"][-1]
    assert c["exit_reason"] == "rail_stop" and c["exit"] == 103.15

def test_kill_switch_still_flattens_a_closed_us_name():
    p = dict(id="VEEV1", seat="healthcare", ticker="VEEV", side="LONG", venue="US", entry=100.0, fill=100.0,
             stop=80.0, initial_stop=80.0, target=140.0, shares=10, status="open", cost_aud=0.0, scale_t1=True,
             off_hours_checked=True, filled_utc=(SHUT - dt.timedelta(hours=6)).isoformat(), fill_audusd=0.65,
             fill_notional_aud=100.0 / 0.65 * 10)
    path, q = _book(p, cash=1000.0)
    D = _tick(path, q, SHUT, dict(last=100.0, hi=100.2, lo=99.8))
    assert D["kill_switch"]["state"] == "FLAT"
    c = D["seats"]["healthcare"]["closed"][-1]
    assert c["exit_reason"] == "kill_switch_flat" and c["exit"] == 100.0 and "VEEV1" not in D["seats"]["healthcare"]["positions"]

def test_hard_stop_and_backstop_book_while_the_venue_is_closed():
    shut = dt.datetime(2026, 10, 28, 12, 0, tzinfo=U)   # 08:00 New York
    saved = BK.session_open; BK.session_open = _REAL_SESSION
    try:
        assert not BK.session_open("US", shut)
        filled = (dt.datetime(2026, 10, 28, 18, 45, tzinfo=U) - dt.timedelta(hours=1)).isoformat()
        def pos(r):
            return dict(id="P1", seat="energy", ticker="XLE", side="LONG", entry=100.0, fill=100.0, stop=100.0 - r,
                        initial_stop=100.0 - r, target=100.0 + 5 * r, shares=100, status="open", venue="US", sleeve="core",
                        cost_aud=0.0, scale_t1=True, off_hours_checked=True, filled_utc=filled, fill_audusd=0.70,
                        fill_notional_aud=100.0 / 0.70 * 100)
        path, q = _book(pos(10.0), "energy")
        D = _tick(path, q, shut, dict(last=94.5, hi=94.6, lo=94.4), au=0.70)
        c = D["seats"]["energy"]["closed"][-1]
        assert c["exit_reason"] == "hard_backstop_5pct" and "P1" not in D["seats"]["energy"]["positions"]
        assert not c.get("exit_queued")
        path, q = _book(pos(2.0), "energy")
        D = _tick(path, q, shut, dict(last=97.5, hi=98.5, lo=97.5), au=0.70)
        c = D["seats"]["energy"]["closed"][-1]
        assert c["exit_reason"] == "stop" and any(e[1] == "stop" for e in D["last_events"])
        assert "P1" not in D["seats"]["energy"]["positions"]
    finally:
        BK.session_open = saved

def test_legacy_close_without_a_stored_rate_matches_the_old_gross():
    p = dict(ticker="VEEV", side="LONG", fill=10.0, shares=10, venue="US", cost_aud=0.0)
    B = dict(cash_aud=SEED)
    gross, _cc = BK.realise(B, p, 12.0, 10, 0.70)
    old = (BK.aud("VEEV", 12.0, 0.70) - BK.aud("VEEV", 10.0, 0.70)) * 10
    assert gross == old and p["_fx_translation_aud"] == 0.0
