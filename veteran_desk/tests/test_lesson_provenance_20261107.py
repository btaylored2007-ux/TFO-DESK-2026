"""A lesson is linked only when each id and citation is present. No model call and no book edit."""
import json

import pytest

from veteran_desk import lesson_trace as LT


def test_missing_task_id_and_a_reply_without_inclusion_are_rejected(tmp_path):
    path = tmp_path / "trace.jsonl"
    with pytest.raises(ValueError):
        LT.record_included("banks", ["lesson-7"], "seat_brain.prompt_block", str(path))
    rejected = LT.record_citation("banks", "See ref=lesson-7. secret reply text", str(path), None)
    assert rejected["level"] == "insufficient_data" and rejected["reason"] == "missing_task_id"
    assert rejected["validated"] is False and "secret reply text" not in json.dumps(rejected)
    with pytest.raises(ValueError):
        LT.record_admin_available("banks", ["lesson-7"], str(path))


def test_true_linkage_is_not_validation_and_a_restart_does_not_duplicate(tmp_path):
    path = str(tmp_path / "trace.jsonl")
    first = LT.record_included("energy", ["trade-9"], "seat_brain.prompt_block", path, task_id="inject-9")
    again = LT.record_included("energy", ["trade-9"], "seat_brain.prompt_block", path, task_id="inject-9")
    assert first["task_id"] == "inject-9" and again["utc"] == first["utc"]
    assert len(LT.load(path)) == 1
    close = dict(source="book_close", trade_id="trade-9", net_aud=12, closed_utc="2099-01-01T00:00:00+00:00")
    alone = LT.observe_book_close("energy", "trade-9", path, close)
    assert alone["reason"] == "included_not_cited" and alone["validated"] is False
    cited = LT.record_citation("energy", "Applied ref=trade-9 on the next order.", path, "inject-9")
    assert cited["cited_ids"] == ["trade-9"] and "Applied ref" not in json.dumps(cited)
    held = LT.observe_book_close("energy", "trade-9", path, close)
    assert held["reason"] == "no_later_decision" and held["validated"] is False
    link = LT.link_later_decision("energy", "trade-9", path, "decide-10", "inject-9", trade_id="trade-9")
    assert link["level"] == "decision_link" and link["validated"] is False and link["causal_edge"] is False
    outcome = LT.observe_book_close("energy", "trade-9", path, close)
    assert outcome["level"] == "outcome_link" and outcome["validated"] is False
    assert outcome["evidence"]["net_aud"] == 12 and outcome["evidence"]["causal_edge"] is False
    assert not any(row.get("level") == "validated" for row in LT.load(path))
    LT.observe_book_close("energy", "trade-9", path, close)
    assert [row["level"] for row in LT.load(path)].count("outcome_link") == 1


def test_admin_availability_has_no_trade_outcome_and_the_configured_rule_stays(tmp_path):
    path = str(tmp_path / "trace.jsonl")
    row = LT.record_admin_available("cfo", ["cfo-note-1"], path)
    assert row["level"] == "lessons_available" and "net_aud" not in json.dumps(row)
    states = {item["seat"]: item for item in LT.coverage(LT.load(path))}
    assert states["cfo"]["state"] == "lessons_available_no_trace"
    assert states["cfo"]["outcome"] is None
    assert states["fixer"]["state"] == "waiting_for_work"
    assert states["healthcare"]["state"] == "no_evidence"
    assert LT.AUTHORITY["rule"].endswith("Otherwise the state is insufficient_data.")
    assert "does not set validated" in LT.RULE_GAP
