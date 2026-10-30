"""t1sync (TFO ruling 19:11 WITA 29 Oct 2026): a seat_cli partial close sets t1_done; next tick never half-banks the residual again. PAPER."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC
from veteran_desk.tests.test_exit_rail_20261029 import _book, _pos, _tick, _p, _closed
def test_partial_close_sets_t1_and_no_rebank():
    pos = _pos(shares=10, mark=103.0, grade="A")
    path, q = _book(pos); old = BK.BOOK
    try:
        BK.BOOK = path
        r = SC.close("energy", "P1", "manual half", price_fn=lambda s: dict(last=103.0), shares=5); assert r["ok"]
    finally: BK.BOOK = old
    p = _p(json.load(open(path)))
    assert p["t1_done"] is True and p["rail_state"] == "t1_done" and p["initial_stop"] == 98.0 and p["r0"] == 2.0
    assert p["stop"] >= 100.0 and p["t1"] == 110.0 and p["target"] == 120.0   # residual at fee floor/BE; target -> T2
    assert any(e["ev"] == "half_bank" for e in p["rail_log"])
    n0 = len(_closed(json.load(open(path))))
    old_news = BK.NEWS_FN
    D, _ = _tick(path, q, [dict(last=104.5, hi=104.6, lo=104.0)])   # +2.25R peak would trigger a rail half-bank if t1_done were unset
    p = _p(D); assert p is not None and p["shares"] == 5
    assert not [c for c in _closed(D)[n0:] if c["shares"] < 5 or "scale_out" in str(c.get("exit_reason")) or "giveback" in str(c.get("exit_reason"))]
def test_idempotent_keeps_existing_t1():
    p = dict(side="SHORT", fill=1.1168, initial_stop=1.1245, stop=1.116484, target=1.096, venue="FX", t1_done=True, shares=247740)
    import datetime as dt; BK.mark_half_banked(p, dt.datetime.now(dt.timezone.utc), 1.11, 1, "x")
    assert p["stop"] == 1.116484 and p["target"] == 1.096
def test_rule_log():
    assert any(r.get("rule") == "t1sync" and "19:11 WITA 29 Oct 2026" in r["wita"] for r in BK.RULE_LOG)
