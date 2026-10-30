import os
from veteran_desk import book as BK
def test_a_default_1pct():
    r = BK.risk_plan(500000, "A"); assert abs(r["pct"] - .01) < 1e-9 and not r["reject"]
def test_a_seat_choice_kept_not_forced_up():
    assert abs(BK.risk_plan(500000, "A", seat_pct=.01)["pct"] - .01) < 1e-9
    assert abs(BK.risk_plan(500000, "A", seat_pct=.02)["pct"] - .02) < 1e-9
def test_non_a_2pct_and_aplus_4pct():
    assert abs(BK.risk_plan(500000, "B")["pct"] - .02) < 1e-9
    assert abs(BK.risk_plan(500000, "A+")["pct"] - .04) < 1e-9
def test_capbind_constant():
    assert BK.RISK_A_CAPBIND_MIN == .015 and BK.RISK_NON_A_MIN == .02 and BK.RISK_MIN == .01
def test_enqueue_has_ack_flag():
    import inspect
    from veteran_desk import desk as D
    assert "ack_required" in inspect.signature(D.enqueue).parameters

def test_deskrun_ack_wired():
    import inspect
    from veteran_desk import desk as D, seat_cli as SC
    assert "tfo_deskrun_ack_20261031" in inspect.getsource(D.run if hasattr(D,"run") else D)
    assert '"confirm"' in inspect.getsource(SC.main)
# ---- tfo_review_fix_20261031 ----
import datetime as _dt, json as _json, tempfile as _tf
def test_cut_a_sizes_to_1pct_not_rejected():
    r = BK.risk_plan(500000, "A", mult=.5); assert r["pct"] == .01 and r["reject"] is None
def test_cut_non_a_below_2pct_rejected_never_1pct():
    r = BK.risk_plan(500000, "B", mult=.5); assert r["pct"] == 0 and r["reject"] == "below_2pct_nonA_floor"
    assert BK.risk_plan(500000, "B", mult=1.0)["pct"] == .02
def _ack_entry(eid, mins, **k):
    from veteran_desk.tests import test_insufficient_cash_20261029 as IC
    e = IC._entry(eid); e.update(grade="A", ack_required=True, acked=False, queued_utc=(IC.T - _dt.timedelta(minutes=mins)).isoformat()); e.update(k); return e
def test_unacked_never_arms_then_expires(monkeypatch):
    monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)
    from veteran_desk.tests import test_insufficient_cash_20261029 as IC
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"AK1": _ack_entry("AK1", 2), "AK2": _ack_entry("AK2", 11)}, IC.MK, session=IC.CLOSED)
    P = D["seats"]["banks"]["positions"]
    assert "AK1" not in P and Q["AK1"]["state"] == "awaiting_funder"
    assert "AK2" not in P and Q["AK2"]["state"] == "expired" and "not acked" in Q["AK2"]["why"]
def test_acked_arms(monkeypatch):
    monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)
    from veteran_desk.tests import test_insufficient_cash_20261029 as IC
    D, Q = IC._tick(IC._book("banks", 500000, {}), {"AK3": _ack_entry("AK3", 2, acked=True)}, IC.MK, session=IC.CLOSED)
    assert D["seats"]["banks"]["positions"]["AK3"]["status"] == "armed"
def test_seat_cli_confirm_temp_queue():
    from veteran_desk import seat_cli as SC
    q = _tf.mktemp(suffix=".json"); _json.dump({"entries": {"VET-X-1-banks": dict(id="VET-X-1-banks", seat="banks", state="awaiting_funder", ack_required=True, acked=False)}}, open(q, "w"))
    assert SC.confirm("energy", "VET-X-1-banks", q)["ok"] is False
    r = SC.confirm("banks", "VET-X-1-banks", q); assert r["ok"] and _json.load(open(q))["entries"]["VET-X-1-banks"]["acked"] is True
    assert SC.confirm("banks", "nope", q)["ok"] is False
def test_propose_no_auto_ack_on_flush():
    import inspect
    from veteran_desk import seat_cli as SC, desk as D
    src = inspect.getsource(SC.main); assert "FG.update(_ack)" not in src and "confirm_required" in src
    assert 'p.get("confirm") is True and r.get("seat_reply_ok") is True' in inspect.getsource(D.enqueue)
def test_theme_oil_one_trade_across_seats(monkeypatch):   # tfo_theme_20261031: XOM LONG blocked while energy holds XLE LONG
    from veteran_desk.tests import test_insufficient_cash_20261029 as IC
    monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)
    assert BK.theme_of("XOM") == BK.theme_of("XLE") == "oil" and BK.theme_of("BAC") is None
    D0 = IC._book("banks", 500000, {})
    D0["seats"]["energy"]["positions"] = {"XLE1": dict(id="XLE1", seat="energy", ticker="XLE", side="LONG", entry=90.0, stop=86.0, target=98.0, shares=10, risk_aud=40.0, status="open", venue="US", sleeve="core", fill=90.0)}
    e = IC._entry("XOM1"); e.update(ticker="XOM", entry=40.0, stop=39.0, target=42.0, grade="A")
    D, Q = IC._tick(D0, {"XOM1": e}, dict(IC.MK, XOM=dict(last=40.5, hi=40.6, lo=40.4), XLE=dict(last=90.5, hi=90.6, lo=90.4)), session=IC.CLOSED)
    assert "XOM1" not in D["seats"]["banks"]["positions"] and "firm_one_trade_per_theme_oil" in Q["XOM1"]["why"]
