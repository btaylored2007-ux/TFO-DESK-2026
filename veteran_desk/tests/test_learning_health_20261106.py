"""Lesson outcomes stay observational, and a Grok timeout is not an account pause."""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from veteran_desk import ai_cooldown as CD
from veteran_desk import learning
from veteran_desk import lesson_trace as LT
from veteran_desk import seat_brain as SB
from veteran_desk import thf_grok
from veteran_desk import thf_shadow_sampler as SAMPLER


def test_uncited_close_stays_insufficient_and_a_cited_close_keeps_the_book_net(tmp_path):
    path = tmp_path / "trace.jsonl"
    LT.record_included("banks", ["trade-1"], "seat_brain.prompt_block", str(path))
    uncited = LT.interpret_reply("I learned to press the trade.", ["trade-1"])
    LT.append(str(path), dict(uncited, seat="banks", source="seat_brain.after_chat"))
    first = LT.observe_book_close("banks", "trade-1", str(path), dict(source="book_close", trade_id="trade-1", net_aud=-4.5))
    assert first["level"] == "insufficient_data" and first["validated"] is False and first["reason"] == "included_not_cited"
    LT.append(str(path), dict(LT.interpret_reply("See ref=trade-1.", ["trade-1"]), seat="banks", source="seat_brain.after_chat"))
    second = LT.observe_book_close("banks", "trade-1", str(path), dict(source="book_close", trade_id="trade-1", net_aud=-4.5))
    assert second["level"] == "outcome_link" and second["validated"] is False and second["causal_edge"] is False
    assert second["evidence"]["net_aud"] == -4.5 and second["evidence"]["link"] == "outcome"
    states = {row["seat"]: row for row in LT.coverage(LT.load(str(path)))}
    assert states["banks"]["state"] != "validated_impact_recorded"
    assert states["cfo"]["state"] == "waiting_for_work" and states["software_engineer"]["state"] == "waiting_for_work"
    assert states["healthcare"]["state"] == "no_evidence" and states["healthcare"]["outcome"] is None
    published = LT.publish_authority(str(tmp_path / "learning"), LT.load(str(path)))
    assert published["decisions"].startswith("state/thf/seat_brain")
    assert published["validated_count"] == 0
    assert "promotable" in learning.promote.__code__.co_consts or "needs >=" in open(learning.__file__, encoding="utf-8").read()


def test_a_close_that_was_never_in_the_prompt_is_not_validated(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("THF_LESSON_TRACE", str(trace))
    monkeypatch.setattr(SB, "lesson", lambda *args, **kwargs: {"ref": kwargs.get("ref")})
    monkeypatch.setattr(SB, "_book", lambda book=None: book)
    SB.lessons_from_book({"seats": {"energy": {"closed": [dict(
        id="e1", status="closed", fill=1, net_aud=3, ticker="ABC", side="LONG", grade="A", exit_reason="target")]}}})
    rows = LT.load(str(trace))
    assert rows and rows[0]["level"] == "insufficient_data" and rows[0]["validated"] is False


def test_timeout_cooldown_is_not_an_account_pause_and_success_keeps_the_quota_event(tmp_path, monkeypatch):
    monkeypatch.setattr(CD, "F", str(tmp_path / "cool.json"))
    monkeypatch.setattr(thf_grok, "STATE", str(tmp_path / "grok.json"))
    CD.mark_fail("grok", "grok timeout; process group stopped; no late result", secs=300, kind="timeout")
    assert CD.cooling("grok") is True
    text = thf_grok.reason()
    assert "timed out" in text and "account paused" not in text and "usage limit" not in text
    CD.mark_ok("grok")
    assert CD.cooling("grok") is False
    saved = json.loads((tmp_path / "cool.json").read_text())
    assert saved["grok"]["history"][0]["kind"] == "timeout"
    failed_at = time.time()
    thf_grok._save_result(False, "ActionRequiredError: You've reached your $160 monthly additional usage limit", failed_at, "grok")
    assert json.loads((tmp_path / "grok.json").read_text())["quota"] is True
    assert "usage limit" in thf_grok.reason() and "bypass" in thf_grok.reason()
    done = thf_grok._save_result(True, "Model response succeeded", failed_at + 1, "grok")
    assert done["quota"] is False and done["quota_events"][0]["detail"].startswith("ActionRequiredError")
    assert CD.cooling("grok") is False
    assert json.loads((tmp_path / "cool.json").read_text())["grok"]["history"][-1]["kind"] == "quota"


def test_a_busy_or_cooling_provider_does_not_finish_the_pair(tmp_path, monkeypatch):
    calls = []

    def make(name):
        def _call(prompt, seat, timeout):
            calls.append((name, seat))
            if name == "chatgpt":
                raise RuntimeError("chatgpt busy")
            if name == "grok" and seat == "trend":
                raise RuntimeError("grok deferred; local timeout cooldown until 23:34")
            return "answer " + name, name
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    path = tmp_path / "state/thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(json.dumps(dict(
        enabled=True, paper_only=True, provider_timeout_s=5, max_jobs_per_run=2)))
    SAMPLER.enqueue(str(tmp_path), dict(task_id="pair-tech", seat="tech_ai_infra", role="trader",
                                       task_class="research_review", data_asof="2026-11-06T15:27:47+00:00",
                                       prompt="same", source="test"))
    SAMPLER.enqueue(str(tmp_path), dict(task_id="pair-trend", seat="trend", role="trader",
                                       task_class="research_review", data_asof="2026-11-06T15:27:47+00:00",
                                       prompt="same", source="test"))
    first = SAMPLER.once(str(tmp_path))
    assert first["window"]["started"] is False and first["seven_day"] == "not_started"
    events = [row.get("event") for row in SAMPLER._events(str(tmp_path))]
    assert "finished" not in events and events.count("deferred") == 2
    before = len(calls)
    SAMPLER.once(str(tmp_path))
    again = calls[before:]
    assert again
    assert all(name in ("chatgpt", "grok") for name, _seat in again)
    assert not any(name == "claude" for name, _seat in again)


def test_cooling_grok_is_not_called(monkeypatch):
    monkeypatch.setattr(CD, "cooling", lambda name: name == "grok")
    monkeypatch.setattr(CD, "_load", lambda: {"grok": {"kind": "timeout", "until_wita": "2026-11-06 23:34 WITA"}})
    asked = []
    monkeypatch.setattr(thf_grok, "ask", lambda *args, **kwargs: asked.append((args, kwargs)))
    try:
        SAMPLER.call_grok("prompt", "trend", 90)
    except RuntimeError as error:
        assert "timeout cooldown" in str(error) and "account paused" not in str(error)
    assert asked == []


def test_matched_start_is_kept_and_the_review_is_14_november(tmp_path):
    started = "2026-11-06T15:36:07+00:00"
    due = SAMPLER.review_due(started)
    assert due.strftime("%Y-%m-%d %H:%M WITA") == "2026-11-14 08:00 WITA"
    first = SAMPLER.lock_window(str(tmp_path), dict(started=True, first_row_utc=started, paired_tasks=2, n=2),
                                now=__import__("datetime").datetime(2026, 11, 6, 16, 0, tzinfo=__import__("datetime").timezone.utc))
    assert first["seven_day"] == "clock_started" and first["seven_day_ready"] is False
    assert first["window"]["started"] is True and first["started_utc"] == started
    later = SAMPLER.lock_window(str(tmp_path), dict(started=True, first_row_utc="2026-11-07T00:00:00+00:00", paired_tasks=3),
                                now=__import__("datetime").datetime(2026, 11, 7, 1, 0, tzinfo=__import__("datetime").timezone.utc))
    assert later["started_utc"] == started
    cleared = SAMPLER.lock_window(str(tmp_path), dict(started=False, first_row_utc=None, paired_tasks=0),
                                  now=__import__("datetime").datetime(2026, 11, 7, 2, 0, tzinfo=__import__("datetime").timezone.utc))
    assert cleared["started"] is True and cleared["started_utc"] == started and cleared["seven_day"] == "clock_started"


def test_installer_replaces_the_recorded_bases_and_rolls_back(tmp_path):
    root = Path(__file__).resolve().parents[2]
    install = root / "deploy/clock_status_20261106/install.py"
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((root / "veteran_desk/book.py").read_bytes())
    rels = ("veteran_desk/lesson_trace.py", "veteran_desk/seat_brain.py", "veteran_desk/thf_shadow_sampler.py")
    for rel in rels:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=root))
    result = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--apply"],
                            cwd=root, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert hashlib.sha256(book.read_bytes()).hexdigest() == "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
    back = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--rollback"],
                          cwd=root, text=True, capture_output=True, check=False)
    assert back.returncode == 0, back.stdout
    assert hashlib.sha256((tmp_path / "veteran_desk/lesson_trace.py").read_bytes()).hexdigest() == "e63ed1fb7463bed9f8c748c5f927fd767e5820837e240f111cdddcf9aec90bac"
