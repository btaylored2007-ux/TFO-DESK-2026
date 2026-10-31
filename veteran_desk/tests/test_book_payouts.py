import datetime as dt
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, payouts_bridge as PB
import funder_gate as FG
def test_seat_books_and_commission_expense():
    tmp = tempfile.mkdtemp(); kw = dict(out_path=os.path.join(tmp, "p.json"), fwd_path=os.path.join(tmp, "f.json"), score_path=os.path.join(tmp, "s.json"))
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}); assert sum(v["seed_aud"] for v in D["seats"].values()) == 5.5e6
    D["seats"]["energy"]["closed"].append(dict(id="t1", net_aud=1000.0, closed_utc="2026-10-20T00:00:00+00:00"))
    PB.book_commissions(D, **kw); e = D["seats"]["energy"]; assert abs(e["commission_expense_aud"] - 150) < .01 and abs(e["cash_aud"] - 499850) < .01, e
    PB.book_commissions(D, **kw); assert abs(D["seats"]["energy"]["cash_aud"] - 499850) < .01   # idempotent
    D["seats"]["energy"]["closed"].append(dict(id="t2", net_aud=-500.0, closed_utc="2026-10-21T00:00:00+00:00")); D["seats"]["energy"]["closed"].append(dict(id="t3", net_aud=400.0, closed_utc="2026-10-22T00:00:00+00:00"))
    PB.book_commissions(D, **kw); assert abs(D["seats"]["energy"]["commission_expense_aud"] - 150) < .01   # clawback: still below prior peak
def test_tick_arms_only_ticked_with_stop_and_2to5pct_risk():
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "state", "funder_queue.json"); os.makedirs(os.path.dirname(q))
    base = dict(book="veteran", ticker="XOM", side="LONG", entry=100.0, stop=96.0, target=108.0, state=FG.WAIT, grade="A", seat_risk_pct=.02, tags=dict(strategy="veteran:energy grade A"))   # tfo_review_fix_20261031: A now 1%; use B for 2% mechanics
    json.dump(dict(entries={"A": dict(base, id="A", decision="approve"), "B": dict(base, id="B", decision=None), "C": dict(base, id="C", decision="approve", stop=0)}), open(q, "w"))
    BK.BOOK = os.path.join(tmp, "book.json"); _saved = (BK.firm_ok, BK.session_open, BK.now, BK.posture)
    BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: True   # fix 2/7: session gate pinned open in unit test
    BK.posture = lambda: ("calm", "risk-off")   # unknown regime pauses arms; this test is the arm/stop rail, not the posture rail
    BK.now = lambda: dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)   # clock pinned 23:00 WITA (outside open-avoid window)
    px = lambda s: dict(last=99.9, hi=100.2, lo=99.5)
    import veteran_desk.payouts as PY; PY.DEFAULT_OUT_PATH = os.path.join(tmp, "p.json")
    try:
        D = BK.tick(price_fn=px, au=0.65, queue_path=q); Q = json.load(open(q))["entries"]
        assert Q["A"]["state"] == "armed" and Q["B"]["state"] == FG.WAIT and Q["C"]["state"] == "expired"
        p = D["seats"]["energy"]["positions"]["A"]; assert .02 * 500000 * .98 <= p["risk_aud"] <= .05 * 500000 and p["stop"] == 96.0
        D = BK.tick(price_fn=px, au=0.65, queue_path=q); assert D["seats"]["energy"]["positions"]["A"]["status"] == "open"
        D = BK.tick(price_fn=lambda s: dict(last=95, hi=96, lo=95), au=.65, queue_path=q); assert D["seats"]["energy"]["closed"][-1]["exit_reason"] == "stop"
        assert D["seats"]["energy"]["closed"][-1]["net_aud"] < 0
    finally:
        BK.firm_ok, BK.session_open, BK.now, BK.posture = _saved
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
