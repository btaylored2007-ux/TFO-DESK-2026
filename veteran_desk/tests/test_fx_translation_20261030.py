"""PAPER. FX translation on the close, and no heartbeat exit booked outside the session.
Legacy closed rows stay as written. seat_pnl_identity restates them so
seed + sum(net) + MTM + flows + restatement = equity within A$1."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
U = dt.timezone.utc
_REAL_SESSION = BK.session_open   # captured at import, before other tests replace BK.session_open
SEED = 500000.0

def _old_gross(p, x, h, au):
    sg = 1 if p["side"] == "LONG" else -1
    return sg * (BK.aud(p["ticker"], x, au) - BK.aud(p["ticker"], p["fill"], au)) * h

def test_same_rate_gross_matches_the_old_formula():
    p = dict(ticker="GC=F", side="LONG", fill=10.0, shares=10, venue="FUT", fill_audusd=0.70, fill_notional_aud=10.0 / 0.70 * 10, cost_aud=0.0)
    B = dict(cash_aud=SEED)
    gross, cc = BK.realise(B, p, 12.0, 10, 0.70)
    assert gross == _old_gross(p, 12.0, 10, 0.70) and p["_fx_translation_aud"] == 0.0 and p["_price_pnl_aud"] == gross

def test_two_rate_gross_is_the_cash_result_split_into_price_and_fx():
    au_f, au_x, fill, ex, sh = 0.65, 0.70, 10.0, 12.0, 10
    fill_n = fill / au_f * sh; exit_n = ex / au_x * sh
    p = dict(ticker="GC=F", side="LONG", fill=fill, shares=sh, venue="FUT", fill_audusd=au_f, fill_notional_aud=fill_n, cost_aud=0.0, status="open")
    B = dict(seed_aud=SEED, cash_aud=SEED - fill_n, positions={"g": p}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    gross, cc = BK.realise(B, p, ex, sh, au_x)
    price, fx = p["_price_pnl_aud"], p["_fx_translation_aud"]
    assert abs(gross - (exit_n - fill_n)) < 1e-6
    assert abs(price - (ex - fill) / au_f * sh) < 1e-6
    assert abs(fx - ex * (1 / au_x - 1 / au_f) * sh) < 1e-6
    assert abs(price + fx - gross) < 1e-6
    assert abs(B["cash_aud"] - (SEED - fill_n + exit_n - cc)) < 1e-6
    pnl = BK._close_pnl(p, gross, cc, 0)
    assert abs(pnl["price_pnl_aud"] + pnl["fx_translation_aud"] - pnl["gross_aud"]) < 0.02
    B["positions"] = {}; B["closed"] = [dict(status="closed", net_aud=pnl["net_aud"], gross_aud=pnl["gross_aud"])]; B["cash_aud"] = SEED - fill_n + exit_n - cc
    ident = BK.seat_pnl_identity(B, {}, au_x)
    assert ident["ok"] and abs(ident["fx_translation_gap_aud"]) < 0.05 and ident["adjustments"] == []

def test_legacy_overstated_net_is_not_rewritten_and_the_restatement_ties():
    au_f, au_x, fill, ex, sh = 0.65, 0.70, 10.0, 12.0, 10
    fill_n = fill / au_f * sh; exit_n = ex / au_x * sh
    old = (ex - fill) / au_x * sh
    net = round(old, 2)
    cash = SEED - fill_n + exit_n
    row = dict(id="GC1", ticker="GC=F", side="LONG", status="closed", net_aud=net, gross_aud=round(old, 2), shares=sh, fill=fill, exit=ex)
    B = dict(seed_aud=SEED, cash_aud=cash, positions={}, closed=[row], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    before = json.dumps(B["closed"])
    ident = BK.seat_pnl_identity(B, {}, au_x)
    assert json.dumps(B["closed"]) == before
    assert ident["ok"]
    assert ident["fx_translation_gap_aud"] < -10
    assert abs(ident["identity_aud"] - cash) <= 1.0
    assert ident["adjustments"][0]["kind"] == "fx_translation_restatement"
    assert abs(ident["adjustments"][0]["amount_aud"] - ident["fx_translation_gap_aud"]) < 0.02
    B["fx_identity"] = ident
    assert B["fx_identity"]["ok"] and row["net_aud"] == net

def test_open_lot_at_a_new_rate_ties_without_a_plug():
    au_f, au_x, fill, mark, sh = 0.65, 0.70, 10.0, 11.0, 10
    fill_n = fill / au_f * sh
    p = dict(ticker="VEEV", side="LONG", fill=fill, shares=sh, venue="US", status="open", fill_audusd=au_f, fill_notional_aud=fill_n, cost_aud=1.25)
    cash = SEED - fill_n - 1.25
    B = dict(seed_aud=SEED, cash_aud=cash, positions={"v": p}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    ident = BK.seat_pnl_identity(B, {"VEEV": dict(last=mark)}, au_x)
    assert ident["ok"] and abs(ident["fx_translation_gap_aud"]) < 0.05 and ident["adjustments"] == []

def test_fx_pair_gross_is_unchanged():
    p = dict(ticker="EURUSD=X", side="SHORT", fill=1.10, shares=100000, venue="FX", cost_aud=2.0)
    B = dict(cash_aud=SEED); gross, cc = BK.realise(B, p, 1.09, 100000, 0.70)
    assert abs(gross - (1.10 - 1.09) * 100000 / 0.70) < 1e-6 and p["_fx_translation_aud"] == 0.0

def _book(pos, seat="healthcare"):
    tmp = tempfile.mkdtemp(); seat_l = lambda: dict(seed_aud=SEED, cash_aud=SEED, positions={}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    D = dict(seats={s: seat_l() for s in BK.TRADERS}, hedge={"disabled": True}, kill_switch={"state": "OK"}, peak_equity_aud=SEED * len(BK.TRADERS), seed_aud=SEED * len(BK.TRADERS))
    B = D["seats"][seat]; B["positions"][pos["id"]] = pos
    if pos.get("fill_notional_aud") is not None: B["cash_aud"] = SEED - float(pos["fill_notional_aud"]) - float(pos.get("cost_aud") or 0)
    path = os.path.join(tmp, "b.json"); json.dump(D, open(path, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    return path, q

def _veev(filled):
    fill, r = 300.0, 5.5
    return dict(id="VEEV1", seat="healthcare", ticker="VEEV", side="LONG", entry=fill, fill=fill, stop=fill - r, target=360.0,
                shares=100, risk_aud=100.0, status="open", venue="US", sleeve="core", cost_aud=0.0, scale_t1=True, grade="A",
                filled_utc=filled, off_hours_checked=True, fill_audusd=0.65, fill_notional_aud=fill / 0.65 * 100)

def _tick(path, q, when, bar, au=0.65):
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN, BK.ADV_FN)
    try:
        BK.BOOK = path; BK.now = lambda: when; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: 0.1
        BK.BARS_FN = lambda s, c: None; BK.NEWS_FN = lambda p: None; BK.ADV_FN = lambda p, au: None
        return BK.tick(price_fn=lambda s: dict(bar, asof=when.isoformat()), au=au, queue_path=q)
    finally:
        BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN, BK.ADV_FN = old

def test_in_session_half_still_books_the_2r_price():
    open_us = dt.datetime(2026, 10, 30, 15, 0, tzinfo=U)   # 11:00 EDT, NYSE open
    saved = BK.session_open; BK.session_open = _REAL_SESSION
    try:
        assert BK.session_open("US", open_us)
        path, q = _book(_veev((open_us - dt.timedelta(hours=2)).isoformat()))
        D = _tick(path, q, open_us, dict(last=313.0, hi=313.0, lo=312.0))
        half = [c for c in D["seats"]["healthcare"]["closed"] if c["id"] == "VEEV1-T1"]
        assert half and half[0]["exit"] == 311.0 and half[0]["exit_reason"] == "rail_2R_scale_out"
        assert "exit_queued" not in D["seats"]["healthcare"]["positions"]["VEEV1"]
    finally:
        BK.session_open = saved

def test_after_close_half_is_queued_and_books_the_next_opens_last():
    shut = dt.datetime(2026, 10, 30, 21, 30, tzinfo=U)   # 17:30 EDT, after the NYSE close
    nxt = dt.datetime(2026, 11, 2, 15, 0, tzinfo=U)      # 10:00 EST Monday
    saved = BK.session_open; BK.session_open = _REAL_SESSION
    try:
        assert not BK.session_open("US", shut) and BK.session_open("US", nxt)
        assert BK._session_closed_for_exit(dict(ticker="VEEV", venue="US"), shut)
        assert not BK._session_closed_for_exit(dict(ticker="BTC-USD", venue="CRYPTO"), shut)
        assert not BK._session_closed_for_exit(dict(ticker="EURUSD=X", venue="FX"), shut)
        path, q = _book(_veev((shut - dt.timedelta(hours=8)).isoformat()))
        D = _tick(path, q, shut, dict(last=313.0, hi=313.0, lo=312.0))
        p = D["seats"]["healthcare"]["positions"]["VEEV1"]
        assert p["status"] == "open" and p["shares"] == 100 and not D["seats"]["healthcare"]["closed"]
        assert p["exit_queued"]["reason"] == "rail_2R_scale_out" and p["exit_queued"]["px"] == 311.0 and p["exit_queued"]["kind"] == "partial"
        D = _tick(path, q, nxt, dict(last=313.0, hi=313.2, lo=312.5), au=0.66)
        half = [c for c in D["seats"]["healthcare"]["closed"] if str(c["id"]).startswith("VEEV1")]
        assert half and half[0]["exit"] == 313.0 and half[0]["exit"] != 311.0
        assert D["seats"]["healthcare"]["positions"]["VEEV1"]["shares"] == 50
        assert D["fx_identity_ok"] is True
        for B in D["seats"].values():
            gap = abs(B["fx_identity"]["equity_aud"] - B["fx_identity"]["identity_aud"])
            assert gap <= 1.0
    finally:
        BK.session_open = saved
