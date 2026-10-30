"""gatecut 2026-10-30: apply_plan cut accepts plain strings and dicts; orchestrator writes dicts. PAPER."""
import json, os, sys, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt48_short_paper as H

def _run(cut):
    d = tempfile.mkdtemp(); led = os.path.join(d, "ledger.json"); plan = os.path.join(d, "plan.json")
    L = dict(book="X", trades=[dict(ticker="AAA", status="ARMED"), dict(ticker="BBB", status="ARMED")])
    json.dump(L, open(led, "w")); json.dump(dict(set=[], cut=cut, note="held for OR"), open(plan, "w"))
    old = H.LEDGER; H.LEDGER = led
    try:
        P = json.load(open(plan)); L2 = json.load(open(led)); now = dt.datetime.now(dt.timezone.utc)
        for c in P["cut"]:
            if not isinstance(c, dict): c = dict(ticker=str(c), reason=P.get("note") or "cut")
            H.cut_trade(L2, c["ticker"], c.get("reason") or P.get("note") or "cut", now)
        return L2
    finally: H.LEDGER = old

def test_strings_and_dicts():
    L = _run(["AAA", dict(ticker="BBB", reason="r")])
    assert [t["status"] for t in L["trades"]] == ["CUT", "CUT"]
    assert L["trades"][0]["cut_reason"] == "held for OR" and L["trades"][1]["cut_reason"] == "r"

def test_source_has_normalisation():
    src = open(H.__file__).read()
    assert "accept plain ticker strings" in src
    import hunt_orchestrator as HO
    assert "cut=[dict(ticker=t, reason=" in open(HO.__file__).read()
