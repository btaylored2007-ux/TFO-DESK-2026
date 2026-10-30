import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, payouts as PY, execution_rules as XR
def _run(entry, ptfn):
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "q.json"); json.dump(dict(entries={entry["id"]: entry}), open(q, "w"))
    keep = (BK.BOOK, BK.firm_ok, BK.posture, XR.pre_trade, PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH)
    BK.BOOK = os.path.join(tmp, "b.json"); BK.firm_ok = lambda: (True, "OK"); BK.posture = lambda: ("calm", "risk-off"); XR.pre_trade = ptfn
    PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = (os.path.join(tmp, x) for x in ("p.json", "f.json", "s.json"))
    try:
        D = BK.tick(price_fn=lambda s: dict(last=99.9, hi=99.9, lo=99.8), au=.65, queue_path=q); D["_tmp"] = tmp; return D
    finally: BK.BOOK, BK.firm_ok, BK.posture, XR.pre_trade, PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = keep
E = dict(id="T1", book="veteran", ticker="XOM", side="LONG", entry=100.0, stop=96.0, target=108.0, state="awaiting_funder", decision="approve", grade="A", seat_risk_pct=.02, tags=dict(strategy="veteran:energy grade A sleeve core"), gate_size_mult=1.0)
def test_blocked_by_rules():
    D = _run(dict(E), lambda *a, **k: dict(ok=False, why="no borrow")); assert "T1" not in D["seats"]["energy"]["positions"] and "no borrow" in str(D["last_events"])
def test_passes_and_records():
    D = _run(dict(E), lambda *a, **k: dict(ok=True, why="cash long")); p = D["seats"]["energy"]["positions"]["T1"]; assert p["pre_trade"]["why"] == "cash long" and p["risk_pct"] == .02
def test_real_rules_cash_long(): assert XR.pre_trade("XOM", "LONG", 100, 100.0)["ok"]
