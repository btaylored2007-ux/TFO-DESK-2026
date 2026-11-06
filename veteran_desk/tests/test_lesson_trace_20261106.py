"""Included, cited, and validated lesson evidence stay separate. No model call."""
import json

from veteran_desk.lesson_trace import SEATS, coverage, interpret_reply, load, record_included, record_validated

def test_inclusion_is_not_validation_and_i_learned_is_not_adoption(tmp_path):
    path = tmp_path / "trace.jsonl"
    record_included("banks", ["lesson-7"], "seat_brain.prompt_block", str(path), task_id="round-1")
    claim = interpret_reply("I learned to always buy.", ["lesson-7"])
    assert claim["validated"] is False and claim["claim_not_adoption"] is True and claim["cited_ids"] == []
    cited = interpret_reply("See ref=lesson-7 in the notes.", ["lesson-7"])
    assert cited["cited_ids"] == ["lesson-7"] and cited["validated"] is False
    record_validated("banks", "lesson-7", str(path), dict(check="holdout_shadow", result="no_rule_change"))
    rows = load(str(path))
    assert [row["level"] for row in rows] == ["included", "validated"]
    assert rows[0]["model_adoption"] is False

def test_restart_rereads_the_cursor_and_covers_every_seat(tmp_path):
    path = tmp_path / "trace.jsonl"
    record_included("macro", ["m-1"], "prompt", str(path), task_id="macro-task")
    again = load(str(path))
    assert again[0]["lesson_ids"] == ["m-1"]
    states = {row["seat"]: row["state"] for row in coverage(again)}
    assert len(states) == 18
    assert set(states) == set(SEATS)
    assert states["macro"] == "included_not_validated"
    assert states["head_trader"] == "no_evidence"
    assert states["crypto"] == "no_evidence"

def test_validation_requires_an_explicit_check(tmp_path):
    try:
        record_validated("cfo", "x", str(tmp_path / "t.jsonl"), {})
        assert False
    except ValueError as error:
        assert "explicit check" in str(error)
