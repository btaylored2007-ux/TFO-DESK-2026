"""End-to-end PAPER: seat_cli propose -> auto-approved queue -> book.py arm -> fill -> stop -> close -> commission/payout bridge. Temp files only."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, desk as D, seat_cli as SC, pit as P, payouts as PY
from veteran_desk.tests.isolated_pit import open_pit, seed_bars
def test_e2e(monkeypatch):
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "state", "funder_queue.json"); os.makedirs(os.path.dirname(q))
    c = open_pit(tmp, monkeypatch); seed_bars(c, "XOM", close=100.0, spread=1.0); seed_bars(c, "AUDUSD=X", close=0.65, spread=0.001)
    keepP = (PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH); real = open(keepP[0]).read()
    PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = (os.path.join(tmp, x) for x in ("payouts.json", "fwd.json", "score.json"))   # 19:17 leak fix: never touch the real payouts ledger
    from veteran_desk import learning as LN; keep_ca = LN.critique_async; LN.critique_async = lambda recs, runid="run": dict(spawned=False, n=len(recs or []), test_stub=True)   # fixer 2026-10-29: never write live state/learning/pending_critique
    keep_so = BK.session_open; BK.session_open = lambda v, t: True; keep_now = BK.now; BK.now = lambda: __import__("datetime").datetime(2026, 10, 28, 15, 0, tzinfo=__import__("datetime").timezone.utc); keep = (BK.BOOK, BK.firm_ok, BK.posture); BK.BOOK = os.path.join(tmp, "book.json"); BK.MARGIN_HEADROOM = 1.0; BK.firm_ok = lambda: (True, "OK"); BK.posture = lambda: ("calm", "risk-off")
    try:
        c = P.con(); f = D.feats(c, "XOM", D.now()); e = f["close"]; a = f["atr14"]
        setup = dict(ticker="XOM", side="LONG", entry=round(e, 2), stop=round(e - 2 * a, 2), target=round(e + 4 * a, 2), grade="A+", thesis="VDTEST end-to-end paper")   # 2026-10-29: A x0.75 no-debate now rejects below floor
        r = SC.propose("energy", setup, "LONG", queue_path=q, env=dict(book=([], "OK"), firm="OK"))
        assert r["passed"] and r["queued"], r
        E = json.load(open(q))["entries"][r["id"]]; assert E["decision"] == "approve" and E["decided_by"].startswith("auto"), E
        px = dict(XOM=dict(last=e * .999, hi=e * .999, lo=e * .998)); fn = lambda s: px.get(s)
        D1 = BK.tick(price_fn=fn, au=.65, queue_path=q); pos = D1["seats"]["energy"]["positions"][r["id"]]
        assert pos["status"] == "armed" and .02 <= pos["risk_pct"] <= .05 and abs(pos["risk_aud"] - r["risk_aud"]) <= max(5, .01 * r["risk_aud"]), (pos, r)
        px["XOM"] = dict(last=e, hi=e * 1.0005, lo=e * .999); D2 = BK.tick(price_fn=fn, au=.65, queue_path=q); assert D2["seats"]["energy"]["positions"][r["id"]]["status"] == "open"
        s = setup["stop"]; px["XOM"] = dict(last=s * .99, hi=e, lo=s * .99); D3 = BK.tick(price_fn=fn, au=.65, queue_path=q)
        cl = D3["seats"]["energy"]["closed"][-1]; assert cl["exit_reason"] == "stop" and cl["net_aud"] < 0 and r["id"] not in D3["seats"]["energy"]["positions"]
        assert -cl["net_aud"] <= .05 * 500000 * 1.02
        assert "commission_expense_aud" in D3["seats"]["energy"]; print("E2E", r["id"], "risk", r["risk_aud"], "net", cl["net_aud"], "lessons", SC.lessons("energy")["stops"] if False else "")
        assert json.load(open(PY.DEFAULT_OUT_PATH))["seats"]["energy"]["closed_trades"] == 1
    finally:
        BK.MARGIN_HEADROOM = 1.3
        LN.critique_async = keep_ca
        BK.BOOK, BK.firm_ok, BK.posture = keep; BK.session_open = keep_so; BK.now = keep_now; PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = keepP
    assert open(keepP[0]).read() == real, "test touched the real payouts ledger"
