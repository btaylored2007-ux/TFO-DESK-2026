import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC, execution_rules as XR
import funder_gate as FG
def test_cancelled_never_rearms():
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "q.json"); old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, XR.BORROW_PATH)
    path = os.path.join(tmp, "ibkr_borrow.json")
    names = {s: dict(fee_pa=0.01, available=5_000_000, ok=True) for s in ("BAC", "JPM", "C")}
    json.dump(dict(updated_utc=dt.datetime.now(dt.timezone.utc).isoformat(), us=dict(names=names), asx=dict(names={})), open(path, "w"))
    XR.BORROW_PATH = path
    base = dict(book="veteran", ticker="BAC", side="SHORT", entry=49.15, stop=50.5, target=46.0, decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
    json.dump(dict(entries={"A": dict(base, id="A", state=FG.WAIT), "X": dict(base, id="X", ticker="JPM", state="cancelled"), "Y": dict(base, id="Y", ticker="C", state="expired")}), open(q, "w"))
    try:
        BK.BOOK = os.path.join(tmp, "b.json"); BK.MARGIN_HEADROOM = 1.0; BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: False; BK.posture = lambda: ("calm", "risk-off")
        px = lambda s: dict(last=49.3, hi=49.4, lo=49.2, asof="x")
        D = BK.tick(price_fn=px, au=.65, queue_path=q); P = D["seats"]["banks"]["positions"]
        assert "A" in P and "X" not in P and "Y" not in P
        assert SC.close("banks", "A", "test disarm")["ok"]
        Q = json.load(open(q)); Q["entries"]["A"]["state"] = FG.WAIT; json.dump(Q, open(q, "w"))   # simulate a queue entry left/reset to WAIT
        for _ in range(2):
            D = BK.tick(price_fn=px, au=.65, queue_path=q)
            assert not D["seats"]["banks"]["positions"], D["seats"]["banks"]["positions"]
            assert all(e[0] != "A" or e[1] != "armed" for e in D["last_events"])
        assert json.load(open(q))["entries"]["A"]["state"] == "cancelled"
    finally: BK.MARGIN_HEADROOM = 1.3; BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, XR.BORROW_PATH = old
