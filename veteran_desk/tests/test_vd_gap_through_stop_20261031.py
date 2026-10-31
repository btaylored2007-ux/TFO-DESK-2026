"""VET-002: a Veteran Desk gap through the stop does not fill, so it cannot book a profit on the stop."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
U = dt.timezone.utc
T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=U)

def test_long_gap_through_stop_does_not_fill():
    seats = {s: dict(seed_aud=500000.0, cash_aud=500000.0, positions={}, closed=[],
                     commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0,
                     pnl_since_inception_aud=0.0, today_pnl_aud=0.0)
             for s in BK.TRADERS}
    pos = dict(id="G1", seat="energy", ticker="XLE", side="LONG", entry=100.0, stop=99.0, target=103.0,
               shares=10, risk_aud=20.0, status="armed", venue="US", sleeve="core", order_type="limit",
               armed_utc=(T - dt.timedelta(minutes=30)).isoformat(), scale_t1=True, grade="A")
    seats["energy"]["positions"]["G1"] = pos
    D = dict(book="veteran_desk", paper_only=True, audusd=0.70, seed_aud=500000.0 * len(BK.TRADERS),
             seats=seats, capital_events=[], hedge={"disabled": True}, kill_switch={"state": "OK"},
             peak_equity_aud=500000.0 * len(BK.TRADERS))
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "b.json"); q = os.path.join(tmp, "q.json")
    json.dump(D, open(path, "w")); json.dump({"entries": {}}, open(q, "w"))
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.us_beta_block)
    try:
        BK.BOOK = path; BK.now = lambda: T; BK.fallback_quote = lambda s: None
        BK.firm_ok = lambda: (True, "OK"); BK.posture = lambda: ("calm", "risk-off"); BK.us_beta_block = lambda *a, **k: None
        out = BK.tick(price_fn=lambda s: dict(last=96.0, hi=100.0, lo=94.0, open=95.0, asof=T.isoformat()), au=0.70, queue_path=q)
    finally:
        BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.us_beta_block = old
    seat = out["seats"]["energy"]
    p = seat["positions"]["G1"]
    assert p["status"] == "armed", p.get("exec_note")
    assert any(e[2] == "fill_through_stop" for e in out["last_events"])
    assert abs(seat["cash_aud"] - 500000.0) < 1e-6
    assert not any(c.get("exit_reason") == "stop" and (c.get("net_aud") or 0) > 0 for c in seat["closed"])
