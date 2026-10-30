"""2026-10-29 withdraw-fix (VET-4568T-20261029-050101): a withdrawn queue entry is cancelled in the book and never (re-)armed. Temp files only."""
import json, os, sys, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, desk as D, seat_cli as SC, pit as P, payouts as PY
import funder_gate as FG
from veteran_desk import learning as LN
import pytest
@pytest.fixture(autouse=True)
def _legacy_cash_rule(monkeypatch): monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)   # tfo_review_20261030: withdraw logic test; 30% headroom tested in test_review_20261030

def _setup():
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "state", "funder_queue.json"); os.makedirs(os.path.dirname(q))
    keep = dict(P=(PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH), so=BK.session_open, now=BK.now, b=(BK.BOOK, BK.firm_ok, BK.posture), upd=FG.update, ca=LN.critique_async); LN.critique_async = lambda *a, **k: None   # never write live learning files
    PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = (os.path.join(tmp, x) for x in ("payouts.json", "fwd.json", "score.json"))
    BK.session_open = lambda v, t: True; BK.now = lambda: dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)
    BK.BOOK = os.path.join(tmp, "book.json"); BK.firm_ok = lambda: (True, "OK"); BK.posture = lambda: ("calm", "risk-off")
    return tmp, q, keep
def _restore(k):
    PY.DEFAULT_OUT_PATH, PY.DEFAULT_FWD_PATH, PY.DEFAULT_SCORE_PATH = k["P"]; BK.session_open = k["so"]; BK.now = k["now"]; BK.BOOK, BK.firm_ok, BK.posture = k["b"]; FG.update = k["upd"]; LN.critique_async = k["ca"]
def _propose(q):
    c = P.con(); f = D.feats(c, "XOM", D.now()); e = f["close"]; a = f["atr14"]
    r = SC.propose("energy", dict(ticker="XOM", side="LONG", entry=round(e, 2), stop=round(e - 2 * a, 2), target=round(e + 4 * a, 2), grade="A+", thesis="VDTEST withdraw"), "LONG", queue_path=q, env=dict(book=([], "OK"), firm="OK"))
    assert r["passed"] and r["queued"], r
    px = dict(XOM=dict(last=e * .999, hi=e * .999, lo=e * .998)); return r["id"], (lambda s: px.get(s))
def _setq(q, i, **kw):
    Q = json.load(open(q)); Q["entries"][i].update(kw); json.dump(Q, open(q, "w"))
def test_withdrawn_after_arm_is_cancelled_and_never_rearmed():
    tmp, q, k = _setup()
    try:
        i, fn = _propose(q); D1 = BK.tick(price_fn=fn, au=.65, queue_path=q); assert D1["seats"]["energy"]["positions"][i]["status"] == "armed"
        _setq(q, i, state="withdrawn")   # the 13:03 shape: queue withdrawn, book still ARMED
        D2 = BK.tick(price_fn=fn, au=.65, queue_path=q); assert i not in D2["seats"]["energy"]["positions"], D2["seats"]["energy"]["positions"]
        assert json.load(open(q))["entries"][i]["state"] == "withdrawn"
        D3 = BK.tick(price_fn=fn, au=.65, queue_path=q); assert i not in D3["seats"]["energy"]["positions"]
    finally: _restore(k)
def test_withdraw_landing_mid_tick_is_not_clobbered():
    tmp, q, k = _setup()
    try:
        i, fn = _propose(q); real = FG.update
        def racing(f, path=None):   # a seat_cli withdraw lands between the tick's queue read and its write-back
            _setq(q, i, state="withdrawn"); return real(f, path)
        FG.update = racing
        D1 = BK.tick(price_fn=fn, au=.65, queue_path=q); FG.update = real
        assert json.load(open(q))["entries"][i]["state"] == "withdrawn"
        assert i not in D1["seats"]["energy"]["positions"], D1["seats"]["energy"]["positions"]
        D2 = BK.tick(price_fn=fn, au=.65, queue_path=q); assert i not in D2["seats"]["energy"]["positions"]
    finally: _restore(k)
