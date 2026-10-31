"""VET-001: seat_cli close of a fractional crypto position returns the notional."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC

def test_close_033_units_returns_notional():
    seats = {s: dict(seed_aud=500000.0, cash_aud=500000.0, positions={}, closed=[],
                     commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
             for s in BK.TRADERS}
    pos = dict(id="K1", seat="crypto", ticker="KRAKEN:XBTUSD", side="LONG", entry=150000.0, fill=150000.0,
               stop=140000.0, target=170000.0, shares=0.33, risk_aud=1000.0, status="open", venue="CRYPTO",
               sleeve="core", cost_aud=0.0, filled_utc="2026-10-30T00:00:00+00:00")
    au = 0.70
    notional = BK.aud(pos["ticker"], pos["fill"], au) * 0.33
    seats["crypto"]["positions"]["K1"] = pos
    seats["crypto"]["cash_aud"] = 500000.0 - notional
    D = dict(book="veteran_desk", paper_only=True, audusd=au, seed_aud=500000.0 * len(BK.TRADERS),
             seats=seats, capital_events=[], hedge={"disabled": True}, kill_switch={"state": "OK"})
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "b.json"); json.dump(D, open(path, "w"))
    old = BK.BOOK
    try:
        BK.BOOK = path
        cash_before = json.load(open(path))["seats"]["crypto"]["cash_aud"]
        r = SC.close("crypto", "K1", "flatten", price_fn=lambda s: dict(last=150000.0))
        after = json.load(open(path))["seats"]["crypto"]
        assert r["ok"] and "K1" not in after["positions"]
        closed = after["closed"][-1]
        assert abs(closed["shares"] - 0.33) < 1e-8
        # Same-price close returns the notional minus the exit cost. int(0.33)==0 would leave it out.
        exit_cost = BK.leg_cost_aud(pos, 150000.0, 0.33, au, None, "exit")
        assert abs(after["cash_aud"] - (cash_before + notional - exit_cost)) < 0.05, (after["cash_aud"], cash_before, notional, exit_cost)
        assert closed["net_aud"] > -notional / 2
    finally:
        BK.BOOK = old
