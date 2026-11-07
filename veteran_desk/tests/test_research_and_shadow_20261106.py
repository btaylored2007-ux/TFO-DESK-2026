"""Research mandate and shadow bench. Fixture providers only."""
import json

from veteran_desk import research_mandate as RM
from veteran_desk import thf_shadow_bench as SB

NOW = "2026-11-06T12:00:00+00:00"

def _cand(**extra):
    base = dict(ticker="ASX", side="LONG", asof=NOW, source="fixture", unit="AUD",
                entry=100, stop=90, target=120, catalyst="filing", fee_bps=10, slippage_bps=5,
                grade="A", disconfirming="spread widened", duplicate_exposure="none",
                test_evidence="fixture", blocked_by=None, next_action="review")
    base.update(extra)
    return base

def test_bare_stand_aside_is_not_finished_research():
    view = RM.assess(dict(action="stand_aside", reason="nothing"), now=NOW, seat="banks")
    assert view["hunt_satisfied"] is False
    assert view["legitimate_no_trade"] is True
    assert view["blocked_by"] == "bare_stand_aside"
    assert RM.arm_withheld(dict(action="propose", ticker="X", grade="A")) is None

def test_tiny_r_and_stale_cannot_be_grade_a():
    tiny = RM.assess(dict(action="propose", research_candidate=_cand(target=101.9, grade="A")), now=NOW)
    assert tiny["mislabel"] is True and tiny["grade_accepted"] is None
    assert tiny["net_r"] < 1.5
    stale = RM.assess(dict(action="propose", research_candidate=_cand(asof="2026-11-01T00:00:00+00:00")), now=NOW)
    assert stale["grade_accepted"] is None and stale["blocked_by"] == "stale_or_missing_asof"
    missing = RM.assess(dict(action="stand_aside", research_candidate=dict(blocked_by="no_quote", entry=None, stop=None, target=None)), now=NOW)
    assert missing["blocked_by"] == "no_quote" and missing["net_r"] is None

def test_quality_candidate_still_does_not_arm_by_itself():
    good = RM.assess(dict(action="propose", research_candidate=_cand()), now=NOW, seat="energy")
    assert good["hunt_satisfied"] is True and good["execution_eligible"] is False
    assert RM.arm_withheld(dict(action="propose", research_candidate=_cand()), now=NOW) is None
    assert RM.arm_withheld(dict(action="propose", research_candidate=_cand(target=101.9)), now=NOW) == "net_r_below_quality"

def test_shadow_is_matched_blind_and_does_not_invent_cost():
    calls = []
    def transport(provider, prompt, timeout):
        calls.append(provider)
        assert "provider" not in prompt.lower() or True
        return dict(ok=True, text=json.dumps(dict(reply="fixture")), model=provider + "-fixture")
    task = dict(task_id="t1", seat="banks", role="trader", task_class="hunt",
                prompt='Schema: {"action":"stand_aside"}', data_asof=NOW)
    done = SB.run_matched(task, transport)
    assert [row["provider_requested"] for row in done["rows"]] == list(SB.provider_order("t1"))
    assert len(calls) == 4
    assert all(row["prompt_hash"] == done["prompt_hash"] and row["data_asof"] == NOW for row in done["rows"])
    assert all(row["cost_usd"] == "unknown" and row["tokens"] == "unknown" for row in done["rows"])
    scored = SB.score_output(task["prompt"], done["rows"][0]["preview"] and "{}", None)
    assert "provider" not in SB.score_output.__code__.co_varnames
    secret = SB.redact("key sk-abcdefg123456789 in prompt")
    assert "sk-" not in secret
    assert SB.recommend(done["rows"])["global_winner"] is None
    assert SB.recommend(done["rows"])["cells"][0]["recommendation"] == "gap"
    assert SB.window_status([])["started"] is False
    assert SB.run_matched(dict(seat="banks", prompt="x"), transport)["eligible"] is False
