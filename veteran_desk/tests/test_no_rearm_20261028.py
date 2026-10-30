import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC
import funder_gate as FG
def test_cancelled_never_rearms():
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "q.json"); old = (BK.BOOK, BK.firm_ok, BK.session_open)
    base = dict(book="veteran", ticker="BAC", side="SHORT", entry=49.15, stop=50.5, target=46.0, decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
    json.dump(dict(entries={"A": dict(base, id="A", state=FG.WAIT), "X": dict(base, id="X", ticker="JPM", state="cancelled"), "Y": dict(base, id="Y", ticker="C", state="expired")}), open(q, "w"))
    try:
        BK.BOOK = os.path.join(tmp, "b.json"); BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: False
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
    finally: BK.BOOK, BK.firm_ok, BK.session_open = old
