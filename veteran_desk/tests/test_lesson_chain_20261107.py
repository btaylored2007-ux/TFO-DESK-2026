"""End-to-end lesson chain and the rejects around it. No model call and no live trace."""
import threading
from pathlib import Path

import pytest

from veteran_desk import lesson_trace as LT
from veteran_desk import seat_brain as SB
from veteran_desk import thf_msgdesk
from veteran_desk import thf_round


def test_round_and_chat_pass_the_real_ids_and_do_not_invent_a_trade_link():
    round_src = Path(thf_round.__file__).read_text(encoding="utf-8")
    chat_src = Path(thf_msgdesk.__file__).read_text(encoding="utf-8")
    assert 'task = runid + "-" + seat' in round_src
    assert "prompt_block(seat, 1100, task_id=task)" in round_src
    assert "after_chat(seat, \"round\", raw, brain=model, task_id=task)" in round_src
    assert "accept_round_ticket(seat, runid, raw or \"\", decision_id, dest)" in round_src
    assert "_brain_block(seat, msg.get(\"id\"))" in chat_src
    assert "task_id=(msg or {}).get(\"id\")" in chat_src
    assert "task_id=msg.get(\"id\")" in chat_src
    assert "link_later_decision" not in chat_src


def test_round_ticket_links_only_the_cited_decision_and_its_close(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setattr(SB, "BASE", str(tmp_path))
    path = str(tmp_path / "trace.jsonl")
    monkeypatch.setenv("THF_LESSON_TRACE", path)
    SB.lesson("banks", "trade", "The stop held.", ref="trade-44")
    SB.prompt_block("banks", 1100, task_id="20261106-1536-banks")
    reply = "Proposal uses ref=trade-44 and does not name any other ticket."
    SB.after_chat("banks", "round", reply, brain="fixture", task_id="20261106-1536-banks")
    silent = thf_round.accept_round_ticket("banks", "20261106-1536", "No citation in this text.", "VET-OTHER-20261106-1536-banks", path)
    assert silent["reason"] == "citation_not_in_decision"
    linked = thf_round.accept_round_ticket("banks", "20261106-1536", reply, "VET-XOM-20261106-1536-banks", path)
    assert linked["level"] == "decision_link" and linked["evidence"]["trade_id"] == "VET-XOM-20261106-1536-banks"
    assert linked["validated"] is False and linked["causal_edge"] is False
    thf_round.accept_round_ticket("banks", "20261106-1536", reply, "VET-XOM-20261106-1536-banks", path)
    assert sum(1 for row in LT.load(path) if row.get("level") == "decision_link") == 1
    SB.lessons_from_book({"seats": {"banks": {"closed": [dict(
        id="VET-NEARBY", status="closed", fill=1, net_aud=9, ticker="NEAR", side="LONG", grade="A",
        closed_utc="2099-01-01T00:00:00+00:00")]}}})
    assert not any(row.get("lesson_id") == "trade-44" and row.get("level") == "outcome_link" for row in LT.load(path))
    nearby = [row for row in LT.load(path) if row.get("lesson_id") == "VET-NEARBY"]
    assert nearby and nearby[0]["reason"] == "no_decision_for_trade" and nearby[0]["validated"] is False
    SB.lessons_from_book({"seats": {"banks": {"closed": [dict(
        id="VET-XOM-20261106-1536-banks", status="closed", fill=1, net_aud=-3, ticker="XOM", side="LONG", grade="A",
        closed_utc="2099-01-01T00:00:00+00:00")]}}})
    outcome = [row for row in LT.load(path) if row.get("level") == "outcome_link"]
    assert len(outcome) == 1 and outcome[0]["lesson_id"] == "trade-44" and outcome[0]["validated"] is False
    assert outcome[0]["evidence"]["trade_id"] == "VET-XOM-20261106-1536-banks" and outcome[0]["causal_edge"] is False
    with pytest.raises(ValueError, match="genuine verifier"):
        LT.record_validated("banks", "trade-44", path, dict(check="holdout_shadow"))


def test_full_chain_stays_unvalidated_without_a_genuine_verifier(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    path = str(tmp_path / "trace.jsonl")
    monkeypatch.setenv("THF_LESSON_TRACE", path)
    SB.lesson("banks", "trade", "The stop held and the fill was not chased.", ref="trade-44")
    injected = SB.prompt_block("banks", 1100, task_id="20261106-1536-banks")
    assert "ref=trade-44" in injected
    reply = "No size change. Prior observation ref=trade-44. I learned nothing that loosens a rail."
    SB.after_chat("banks", "round", reply, brain="fixture", task_id="20261106-1536-banks")
    SB.prompt_block("banks", 1100, task_id="20261106-1606-banks")
    SB.after_chat("banks", "round", "Next order still uses ref=trade-44.", brain="fixture", task_id="20261106-1606-banks")
    linked = LT.link_later_decision("banks", "trade-44", path, "20261106-1606-banks", "20261106-1536-banks", trade_id="trade-44")
    assert linked["level"] == "decision_link" and linked["causal_edge"] is False
    outcome = LT.observe_book_close("banks", "trade-44", path, dict(
        source="book_close", trade_id="trade-44", net_aud=-12.5, closed_utc="2099-01-01T00:00:00+00:00"))
    assert outcome["level"] == "outcome_link" and outcome["validated"] is False and outcome["causal_edge"] is False
    assert outcome["evidence"]["net_aud"] == -12.5
    with pytest.raises(ValueError, match="genuine verifier"):
        LT.record_validated("banks", "trade-44", path, dict(check="holdout_shadow", result="no_rule_change"))
    assert not any(row.get("validated") is True for row in LT.load(path))
    assert "secret" not in Path(path).read_text(encoding="utf-8")


def test_negative_provenance_rejects(tmp_path):
    path = str(tmp_path / "trace.jsonl")
    with pytest.raises(ValueError):
        LT.record_included("banks", ["not a ref"], "seat_brain.prompt_block", path, task_id="inject-1")
    LT.record_included("banks", ["trade-44"], "seat_brain.prompt_block", path, task_id="inject-1", account_id="desk-a")
    cited = LT.record_citation("banks", "See ref=made-up and ref=trade-44.", path, "inject-1")
    assert cited["cited_ids"] == ["trade-44"]
    same = LT.link_later_decision("banks", "trade-44", path, "inject-1", "inject-1", trade_id="trade-44")
    assert same["reason"] == "later_task_is_the_injection"
    other = LT.link_later_decision("energy", "trade-44", path, "decide-2", "inject-1", trade_id="trade-44")
    assert other["reason"] == "no_citation"
    crossed = LT.link_later_decision("banks", "trade-44", path, "decide-2", "inject-1", trade_id="trade-44", account_id="desk-b")
    assert crossed["reason"] == "cross_account"
    good = LT.link_later_decision("banks", "trade-44", path, "decide-2", "inject-1", trade_id="trade-44", account_id="desk-a")
    assert good["level"] == "decision_link"
    forged = LT.observe_book_close("banks", "trade-44", path, dict(
        source="book_close", trade_id="trade-44", net_aud=1, closed_utc="2099-01-01T00:00:00+00:00", account_id="desk-b"))
    assert forged["reason"] == "cross_account" and forged["validated"] is False
    stale = LT.observe_book_close("banks", "trade-44", path, dict(
        source="book_close", trade_id="trade-44", net_aud=1, closed_utc="2020-01-01T00:00:00+00:00", account_id="desk-a"))
    assert stale["reason"] == "stale_close"
    missing_time = LT.observe_book_close("banks", "trade-44", path, dict(
        source="book_close", trade_id="trade-44", net_aud=1, account_id="desk-a"))
    assert missing_time["reason"] == "incomplete_close"
    with pytest.raises(ValueError):
        LT.record_validated("no-such-seat", "trade-44", path, dict(check="holdout_shadow"))


def test_restart_and_concurrent_writers_leave_one_row(tmp_path):
    path = str(tmp_path / "trace.jsonl")
    errors = []

    def write():
        try:
            LT.record_included("macro", ["m-1"], "prompt", path, task_id="macro-task")
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    rows = LT.load(path)
    assert len(rows) == 1 and rows[0]["task_id"] == "macro-task"
    LT.record_included("macro", ["m-1"], "prompt", path, task_id="macro-task")
    assert len(LT.load(path)) == 1
