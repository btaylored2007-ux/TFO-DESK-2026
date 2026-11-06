import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import funder_gate as FG
def test_dedupe():
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "q.json"); old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.posture)
    base = dict(book="veteran", ticker="BAC", side="SHORT", entry=40.0, stop=41.0, target=38.0, state=FG.WAIT, decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
    json.dump(dict(entries={"A": dict(base, id="A")}), open(q, "w"))
    try:
        BK.BOOK = os.path.join(tmp, "b.json"); BK.MARGIN_HEADROOM = 1.0; BK.firm_ok = lambda: (True, "OK"); BK.session_open = lambda v, t: False; BK.posture = lambda: ("calm", "risk-off")
        px = lambda s: dict(last=40.1, hi=40.2, lo=40.05, asof="x")
        D = BK.tick(price_fn=px, au=.65, queue_path=q); assert D["seats"]["banks"]["positions"]["A"]["status"] == "armed"
        Q = json.load(open(q)); Q["entries"]["B"] = dict(base, id="B"); Q["entries"]["C"] = dict(base, id="C", side="LONG", stop=39.0, target=42.0, entry=40.0); json.dump(Q, open(q, "w"))
        D = BK.tick(price_fn=px, au=.65, queue_path=q); Q = json.load(open(q))["entries"]
        assert "B" not in D["seats"]["banks"]["positions"] and Q["B"]["state"] == "expired" and "duplicate" in Q["B"]["why"]
        assert "C" in D["seats"]["banks"]["positions"]   # other side not a duplicate
    finally: BK.MARGIN_HEADROOM = 1.3; BK.BOOK, BK.firm_ok, BK.session_open, BK.posture = old
