import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def _seat(pos): B = BK._seat_ledger(); B["cash_aud"] = 500000.0 - pos.get("cost_aud", 0); B["positions"]["x"] = pos; return B
FX = dict(ticker="EURUSD=X", side="SHORT", shares=990959, fill=1.11682, status="open", venue="FX", cost_aud=235.17)
def test_audusd_move_changes_only_cost_scale():
    B = _seat(dict(FX)); m = {"EURUSD=X": dict(last=1.11682)}
    assert abs(BK.equity_aud(B, m, 0.70) - (500000 - 235.17)) < 1e-6 and abs(BK.equity_aud(B, m, 0.66) - (500000 - 235.17)) < 1e-6   # EURUSD flat: AUDUSD move changes nothing (cost already sunk)
def test_pip_pnl():
    B = _seat(dict(FX)); m = {"EURUSD=X": dict(last=1.11582)}   # 10 pips in favour of the short
    assert abs(BK.equity_aud(B, m, 0.70) - (500000 - 235.17 + 990959 * 0.001 / 0.70)) < 1e-6
def test_usdjpy_fixture():
    assert BK.quote_to_aud("USDJPY=X", 0.70, {"USDJPY=X": dict(last=150.0)}) == 0.70 * 150.0
    assert BK.quote_to_aud("EURJPY=X", 0.70, {"USDJPY=X": dict(last=150.0)}) == 0.70 * 150.0
    assert BK.quote_to_aud("EURAUD=X", 0.70) == 1.0 and BK.quote_to_aud("EURUSD=X", 0.70) == 0.70
    B = _seat(dict(FX, ticker="USDJPY=X", side="LONG", shares=100000, fill=150.0)); m = {"USDJPY=X": dict(last=151.0)}
    assert abs(BK.equity_aud(B, m, 0.70) - (500000 - 235.17 + 100000 * 1.0 / (0.70 * 151.0))) < 1e-6
def test_xle_unchanged():
    B = BK._seat_ledger(); B["cash_aud"] = 500000 - 3199 * 68.48 / 0.70; B["positions"]["e"] = dict(ticker="XLE", side="LONG", shares=3199, fill=68.48, status="open", venue="US")
    assert abs(BK.equity_aud(B, {"XLE": dict(last=69.0)}, 0.70) - (500000 - 3199 * 68.48 / 0.70 + 3199 * 69.0 / 0.70)) < 1e-6
def test_round_trip_cash_via_tick():
    import tempfile, json
    tmp = tempfile.mkdtemp(); old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.in_open_window, BK.posture)
    try:
        BK.BOOK = os.path.join(tmp, "b.json"); BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: True; BK.in_open_window = lambda v, t: False; BK.posture = lambda: ("calm", "risk-off")
        D = BK.load(0.70); D["hedge"] = {}; S = D["seats"]["macro"]
        S["positions"]["f"] = dict(id="f", seat="macro", ticker="EURUSD=X", side="SHORT", entry=1.1174, stop=1.1245, target=1.0960, shares=1000000, risk_aud=1, status="armed", scale_t1=False, order_type="limit", armed_utc=BK.now().isoformat(), venue="FX")
        json.dump(D, open(BK.BOOK, "w")); q = os.path.join(tmp, "q.json"); json.dump(dict(entries={}), open(q, "w"))
        D = BK.tick(price_fn=lambda s: dict(last=1.1174, hi=1.1180, lo=1.1170, asof="x"), au=0.70, queue_path=q); S = D["seats"]["macro"]; p = S["positions"]["f"]
        c = BK.COST_RT["FX"] / 2; n = 1.1174 * 1e6 / 0.70
        assert p["status"] == "open" and abs(S["cash_aud"] - (500000 - n * c)) < 0.01   # only the cost left cash
        D = BK.tick(price_fn=lambda s: dict(last=1.0965, hi=1.0970, lo=1.0955, asof="x"), au=0.70, queue_path=q); S = D["seats"]["macro"]
        g = (1.1174 - 1.0960) * 1e6 / 0.70; n2 = 1.0960 * 1e6 / 0.70
        row = S["closed"][-1]
        # FX cash credit is the pip P&L only. fill_audusd is not applied (the notional never left cash), so gross stays the old formula and fx_translation is 0.
        # The old expression omitted the short borrow that borrow_accrue takes out of cash when the WITA date is ahead of the UTC fill date.
        borrow = float(row.get("borrow_accrued_aud") or 0) + float(row.get("borrow_min_charge_aud_est") or 0)
        assert not S["positions"] and abs(row["gross_aud"] - g) < 0.01 and row.get("fx_translation_aud", 0) == 0.0
        assert abs(S["cash_aud"] + S.get("commission_expense_aud", 0) - S.get("commission_forfeit_credit_aud", 0) - (500000 - n * c + g - n2 * c - borrow)) < 0.01
    finally: BK.BOOK, BK.firm_ok, BK.session_open, BK.in_open_window, BK.posture = old
