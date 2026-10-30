import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import funder_gate as FG
def _setup(cfg):
    b = tempfile.mkdtemp(); os.makedirs(os.path.join(b, "state"))
    json.dump(cfg, open(os.path.join(b, "state", "funder_gate.json"), "w")); return b
def _L(): return dict(trades=[dict(id="1", sym="TST", status="ARMED", trigger=10.0, max_fill=10.05, stop=9.5, t1=11.0, side="LONG", shares=100, armed_utc="2026-10-28T00:00:00+00:00")])
def test_auto_approve_arms_same_tick_and_records():
    b = _setup(dict(enabled=True, auto_approve=True)); L = _L(); now = dt.datetime.now(dt.timezone.utc)
    acts = FG.gate_tick("asx", L, now, lambda t: 9.9, lambda t, n: False, base=b)
    assert ("TST", "armed") in acts and L["trades"][0]["status"] == "ARMED" and L["trades"][0]["funder_approved"], acts
    e = list(json.load(open(os.path.join(b, "state", "funder_queue.json")))["entries"].values())[0]
    assert e["decision"] == "approve" and e["decided_by"].startswith("auto") and e["state"] == "armed"
def test_auto_approve_still_refuses_chase():
    b = _setup(dict(enabled=True, auto_approve=True)); L = _L(); now = dt.datetime.now(dt.timezone.utc)
    FG.gate_tick("asx", L, now, lambda t: 10.5, lambda t, n: False, base=b); assert L["trades"][0]["status"] == "EXPIRED"
def test_without_auto_waits_for_tick():
    b = _setup(dict(enabled=True)); L = _L(); now = dt.datetime.now(dt.timezone.utc)
    FG.gate_tick("asx", L, now, lambda t: 9.9, lambda t, n: False, base=b); assert L["trades"][0]["status"] == FG.WAIT
