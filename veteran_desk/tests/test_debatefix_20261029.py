import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk.tests.test_gate_v2 import g, P0
def test_unavailable_no_penalty():   # debatefix 2026-10-29 (Brett 08:33)
    for ch, da in [(None, None), (dict(status="RETIRED"), None), (dict(call="RETIRED"), dict(error="timeout")), (dict(status="UNAVAILABLE", call="SHORT"), None)]:
        r = g(ch=ch, da=da); assert r["passed"] and r["size_mult"] == 1.0, (ch, da, r["size_mult"])
        assert any("debate_unavailable" in x for x in r["warnings"])
def test_real_stand_aside_advisory_only():   # TFO 19:18 WITA 29 Oct: challenger advisory, never blocks/resizes
    r = g(P0(grade="A+"), ch=dict(call="STAND_ASIDE")); assert r["size_mult"] == 1.0 and any("STAND_ASIDE" in x for x in r["warnings"])
def test_opposite_and_kill_advisory_only():
    for ch, da in [(dict(call="SHORT"), None), (None, dict(factual_kill=True, fact="f")), (dict(call="SHORT"), dict(factual_kill=True, fact="f"))]:
        r = g(ch=ch, da=da); assert r["passed"] and r["size_mult"] == 1.0 and any("ADVISORY" in x for x in r["warnings"]), (ch, da)
def test_rule_log():
    from veteran_desk import book as BK; assert any(r.get("id") == "tfo_debate_advisory_20261029_1918" for r in BK.RULE_LOG)
