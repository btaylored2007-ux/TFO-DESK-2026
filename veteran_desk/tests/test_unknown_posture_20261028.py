import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import funder_gate as FG
T0 = dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)   # 23:00 WITA, US session open, outside open-avoid window
def _setup(tmp):
    q = os.path.join(tmp, "state", "funder_queue.json"); os.makedirs(os.path.dirname(q))
    base = dict(book="veteran", ticker="XOM", side="LONG", entry=100.0, stop=96.0, target=108.0, state=FG.WAIT, grade="A", seat_risk_pct=.02, tags=dict(strategy="veteran:energy grade A"))   # tfo_review_fix_20261031: A now 1%; use B for 2% mechanics
    json.dump(dict(entries={"A": dict(base, id="A", decision="approve")}), open(q, "w")); return q
def test_unknown_pauses_arms_holds_fills_keeps_stops():
    tmp = tempfile.mkdtemp(); q = _setup(tmp)
    old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.now, BK.posture)
    BK.BOOK = os.path.join(tmp, "book.json"); BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: True; BK.now = lambda: T0
    import veteran_desk.payouts as PY; PY.DEFAULT_OUT_PATH = os.path.join(tmp, "p.json")
    px = lambda s: dict(last=100.05, hi=100.3, lo=99.9, asof=T0.isoformat())
    try:
        BK.posture = lambda: ("calm", "risk-off"); D = BK.tick(price_fn=lambda s_: dict(last=99.4, hi=99.5, lo=99.2, asof=T0.isoformat()), au=0.65, queue_path=q)       # arm A while known
        a = D["seats"]["energy"]["positions"]["A"]; sh = a["shares"]; assert a["status"] == "armed" and a["risk_pct"] >= .02
        J = json.load(open(q)); J["entries"]["B"] = dict(J["entries"]["A"], id="B", ticker="PG", state=FG.WAIT, decision="approve"); json.dump(J, open(q, "w"))   # tfo_theme_20261031: CVX would now be blocked by one-trade-per-theme (oil) vs XOM
        BK.posture = lambda: ("unknown", "unknown"); D = BK.tick(price_fn=px, au=0.65, queue_path=q)     # unknown: B not armed, A HELD (no fill)
        Q = json.load(open(q))["entries"]; assert Q["B"]["state"] == FG.WAIT and "B" not in D["seats"]["energy"]["positions"], Q["B"]
        a = D["seats"]["energy"]["positions"]["A"]; assert a["status"] == "armed" and a["shares"] == sh and "HELD" in a["exec_note"], a
        J = json.load(open(BK.BOOK)); J["seats"]["energy"]["positions"]["S"] = dict(a, id="S", ticker="CVX", status="open", fill=100.0, filled_utc=T0.isoformat(), cost_aud=10.0, exec_note=None); json.dump(J, open(BK.BOOK, "w"))
        D = BK.tick(price_fn=lambda s_: dict(last=95, hi=96, lo=95, asof=T0.isoformat()) if s_ == "CVX" else px(s_), au=.65, queue_path=q)   # stop still exits under unknown
        assert [c for c in D["seats"]["energy"]["closed"] if c.get("id") == "S"][-1]["exit_reason"] == "stop"
        assert D["seats"]["energy"]["positions"]["A"]["status"] == "armed"   # still held
        BK.posture = lambda: ("choppy", "risk-off"); D = BK.tick(price_fn=px, au=0.65, queue_path=q)    # inputs back -> A fills full size, B arms
        a = D["seats"]["energy"]["positions"]["A"]; assert a["status"] == "open" and a["shares"] == sh and a["risk_pct"] >= .02, a
        assert json.load(open(q))["entries"]["B"]["state"] in ("armed", "open")
    finally: BK.BOOK, BK.firm_ok, BK.session_open, BK.now, BK.posture = old
