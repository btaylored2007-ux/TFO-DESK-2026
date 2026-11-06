"""Calls the round, chat, brain, and feed functions that production imports."""
import datetime as dt
import json
import os

from veteran_desk import lesson_trace as LT
from veteran_desk import research_mandate as RM
from veteran_desk import seat_brain as SB
from veteran_desk import thf_round as ROUND
from veteran_desk import thf_shadow_bench as SHADOW
import thf_feedkeeper as FK

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

def _candidate(**extra):
    base = dict(ticker="HSBA.L", side="LONG", asof=NOW.isoformat(), source="fixture", unit="GBP",
                entry=100, stop=90, target=120, catalyst="filing", fee_bps=8, slippage_bps=4,
                grade="A", disconfirming="spread", duplicate_exposure="none", test_evidence="fixture",
                blocked_by=None, next_action="review")
    base.update(extra)
    return base

class Desk:
    def apply_marcus_limits(self, *args, **kwargs):
        return None
    def enqueue(self, rows, *args, **kwargs):
        self.enqueued = rows
        return [rows[0]["id"]]

def test_round_records_research_and_refuses_a_bad_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("THF_SHADOW_LOG", str(tmp_path / "shadow.jsonl"))
    seat = "banks"
    reply = dict(action="propose", research_candidate=_candidate(target=101.9, grade="A"))
    entry = {}
    # Same fields admit() writes after a parsed model reply.
    from veteran_desk import research_mandate as mandate
    entry["research"] = mandate.public_view(mandate.assess(reply, now=NOW, seat=seat))
    assert entry["research"]["hunt_satisfied"] is False
    record = dict(id="VET-FIXTURE", gate=dict(passed=True, reasons=[]), proposal=reply)
    desk = Desk()
    assert ROUND.submit_candidate(desk, [record], record, set(), 0.7, True, None) == []
    assert record["research_shadow"] == "net_r_below_quality"
    assert not hasattr(desk, "enqueued")
    good = dict(action="propose", research_candidate=_candidate())
    good_record = dict(id="VET-GOOD", gate=dict(passed=True, reasons=[]), proposal=good)
    assert ROUND.submit_candidate(desk, [good_record], good_record, set(), 0.7, True, None) == ["VET-GOOD"]
    SHADOW.note_production(dict(task_id="round", seat=seat, role="trader", task_class="hunt",
                                prompt="Schema: {}", data_asof=NOW.isoformat(), text='{"action":"stand_aside"}',
                                provider="fixture"), os.environ["THF_SHADOW_LOG"])
    row = json.loads((tmp_path / "shadow.jsonl").read_text().splitlines()[-1])
    assert row["shadow_calls"] == 0 and row["cost_usd"] == "unknown"

def test_brain_then_chat_then_validated_check(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setenv("THF_LESSON_TRACE", str(tmp_path / "trace.jsonl"))
    SB.lesson("banks", "trade", "Stop was respected.", ref="lesson-banks-1")
    text = SB.prompt_block("banks", 1100)
    assert "ref=lesson-banks-1" in text
    SB.after_chat("banks", "task", "Noted ref=lesson-banks-1. I learned nothing new.", brain="fixture")
    LT.record_validated("banks", "lesson-banks-1", os.environ["THF_LESSON_TRACE"],
                       dict(check="holdout_no_rule_change", result="rules unchanged"))
    rows = LT.load(os.environ["THF_LESSON_TRACE"])
    levels = [row["level"] for row in rows]
    assert levels[0] == "included" and "lesson-banks-1" in rows[0]["lesson_ids"]
    assert any(row.get("cited_ids") == ["lesson-banks-1"] and row.get("validated") is False for row in rows)
    assert rows[-1]["level"] == "validated" and rows[-1]["evidence"]["check"] == "holdout_no_rule_change"
    states = {item["seat"]: item["state"] for item in LT.coverage(rows)}
    assert len(states) == 18 and states["banks"] == "validated_impact_recorded"
    assert states["head_of_desk"] == "waiting_for_work"

def test_brain_records_outside_pytest_without_an_env_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setattr(SB, "BASE", str(tmp_path))
    monkeypatch.delenv("THF_LESSON_TRACE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    SB.lesson("macro", "trade", "Stop was respected.", ref="lesson-macro-1")
    assert "ref=lesson-macro-1" in SB.prompt_block("macro", 1100)
    SB.after_chat("macro", "task", "Noted ref=lesson-macro-1.", brain="fixture")
    path = tmp_path / "state" / "thf" / "lesson_trace.jsonl"
    rows = LT.load(str(path))
    assert rows[0]["level"] == "included"
    assert any(row.get("cited_ids") == ["lesson-macro-1"] for row in rows)

def test_eleven_research_seats_and_feed_writer_status(tmp_path):
    assert len(RM.TRADERS) == 11
    for seat in RM.TRADERS:
        view = RM.assess(dict(action="stand_aside", reason="none"), now=NOW, seat=seat)
        assert view["hunt_satisfied"] is False
    path = tmp_path / "asia.json"
    path.write_text(json.dumps(dict(markets_in_session=["ASX"], last_bar_utc=None, syms_with_bars_8h=0,
                                    data_status="stale_underlying")))
    report, _snap = FK.check("asia_intraday", ("asia", str(path), None, 60, 10**9, "json", None), None)
    assert report["status"] == "degraded"
    from tools import intraday_bars_pull as pull
    pull.universe(extra=["ASC.L"], rotate=0, entitled=["HSBA.L"], held=["HSBA.L"])
    plan = pull.universe.last_plan
    assert plan["quote_stream"] is False and plan["entitlement"] == "listed"
    assert "ASC.L" in plan["not_entitled"] and "ASC.L" not in plan["subscribe"]
