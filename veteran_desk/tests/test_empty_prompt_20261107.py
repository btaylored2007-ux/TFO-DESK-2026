"""An empty prompt is rejected before Claude or Grok is started."""
import json

from veteran_desk import claude_brain as CB
from veteran_desk import thf_grok as GK
from veteran_desk import thf_shadow_sampler as SAMPLER


def test_claude_and_grok_reject_an_empty_prompt_before_the_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(CB, "LOG", str(tmp_path / "claude.jsonl"))
    monkeypatch.setattr(CB, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("claude cli")))
    monkeypatch.setattr(GK, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("grok cli")))
    for caller in (CB.ask, GK.ask):
        try:
            caller("", seat=None, kind="review") if caller is CB.ask else caller("", seat=None)
            raise AssertionError("empty prompt was accepted")
        except RuntimeError as error:
            assert "empty prompt rejected before provider call" in str(error)
        except TypeError:
            caller("   ")
            raise AssertionError("blank prompt was accepted")
    row = json.loads((tmp_path / "claude.jsonl").read_text().splitlines()[-1])
    assert row["prompt_chars"] == 0 and row["ok"] is False and row["kind"] == "review" and row["seat"] is None


def test_blank_sampler_job_is_dead_lettered_without_a_provider_or_a_new_clock(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(SAMPLER.CALLS, "claude", lambda *a: calls.append(a) or ("x", "claude"))
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    root = tmp_path / "state" / "thf"
    root.mkdir(parents=True)
    (root / "shadow_sampler.json").write_text(json.dumps(dict(enabled=True, paper_only=True, max_jobs_per_run=2, provider_timeout_s=5)))
    refused = SAMPLER.enqueue_production_prompt(str(tmp_path), None, "  ", "2026-11-07T02:00:00+00:00", "blank-prod")
    assert refused["queued"] is False and refused["reason"] == "empty_prompt"
    SAMPLER._append(SAMPLER.jobs_path(str(tmp_path)), dict(event="queued", task_id="blank-old", seat=None, prompt="", utc="2026-11-07T02:06:00+00:00"))
    status = SAMPLER.once(str(tmp_path))
    assert calls == []
    assert status["invalid_empty_prompt"] == 1 and status["jobs"] == 0
    assert status["window"]["started"] is False
    events = SAMPLER._events(str(tmp_path))
    assert any(row.get("event") == "invalid" and row.get("reason") == "empty_prompt" for row in events)
    assert SAMPLER.pending_jobs(str(tmp_path)) == []
    again = SAMPLER.once(str(tmp_path))
    assert again["invalid_empty_prompt"] == 0 and calls == []


def test_a_deferred_status_row_keeps_the_queued_prompt(tmp_path, monkeypatch):
    calls = []

    def _claude(prompt, seat, timeout):
        calls.append((prompt, seat))
        return "kept", "claude-live"

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, _claude if name == "claude" else (lambda *a: ("kept", "other")))
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    root = tmp_path / "state" / "thf"
    root.mkdir(parents=True)
    (root / "shadow_sampler.json").write_text(json.dumps(dict(enabled=True, paper_only=True, max_jobs_per_run=1, provider_timeout_s=5)))
    SAMPLER.enqueue(str(tmp_path), dict(task_id="retry-1", seat="banks", role="trader", task_class="research_review",
                                        data_asof="2026-11-07T03:00:00+00:00", prompt="the real review", source="test"))
    SAMPLER._append(SAMPLER.jobs_path(str(tmp_path)), dict(event="deferred", task_id="retry-1", utc="2026-11-07T03:06:00+00:00", deferred=["claude"]))
    SAMPLER.once(str(tmp_path))
    assert calls[0] == ("the real review", "banks")


def test_live_deferred_schema_keeps_the_prompt_and_the_first_clock(tmp_path, monkeypatch):
    calls = []

    def _grok(prompt, seat, timeout):
        calls.append((prompt, seat))
        return "kept", "grok"

    def _fail(prompt, seat, timeout):
        raise RuntimeError("timeout")

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, _grok if name == "grok" else _fail)
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    root = tmp_path / "state" / "thf"
    root.mkdir(parents=True)
    (root / "shadow_sampler.json").write_text(json.dumps(dict(enabled=True, paper_only=True, max_jobs_per_run=1, provider_timeout_s=90)))
    (root / "shadow_window.json").write_text(json.dumps(dict(started=True, started_utc="2026-11-06T15:36:07+00:00")))
    (root / "shadow_samples.jsonl").write_text(json.dumps(dict(
        task_id="older-pair", kind="shadow_evaluation", ok=False, error="RuntimeError: chatgpt busy", scored=False)) + "\n")
    SAMPLER.enqueue(str(tmp_path), dict(
        task_id="20261106-1457-trend", seat="trend", role="trader", task_class="production_proposal",
        data_asof="2026-11-06T06:57:00+00:00", prompt="the production review", source="production_prompt"))
    SAMPLER._append(SAMPLER.jobs_path(str(tmp_path)), dict(
        deferred=["claude", "grok"], event="deferred", providers=["claude"],
        task_id="20261106-1457-trend", utc="2026-11-07T03:06:00+00:00"))
    before = (root / "shadow_jobs.jsonl").read_text()
    status = SAMPLER.once(str(tmp_path))
    assert ("the production review", "trend") in calls
    assert status["window"]["first_row_utc"] == "2026-11-06T15:36:07+00:00"
    assert status["window"]["paired_tasks"] == 0
    assert before in (root / "shadow_jobs.jsonl").read_text()
    assert "chatgpt busy" in (root / "shadow_samples.jsonl").read_text()


def test_three_existing_defers_close_without_another_provider_call(tmp_path, monkeypatch):
    calls = []
    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, lambda *a: calls.append(a) or ("x", name))
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    root = tmp_path / "state" / "thf"
    root.mkdir(parents=True)
    (root / "shadow_sampler.json").write_text(json.dumps(dict(enabled=True, paper_only=True, max_jobs_per_run=1, provider_timeout_s=90)))
    SAMPLER.enqueue(str(tmp_path), dict(task_id="spent", seat="banks", prompt="still the original", source="test"))
    for _ in range(SAMPLER.DEFER_LIMIT):
        SAMPLER._append(SAMPLER.jobs_path(str(tmp_path)), dict(
            event="deferred", deferred=["claude"], providers=["claude"], task_id="spent", utc="2026-11-07T03:06:00+00:00"))
    lines_before = (root / "shadow_jobs.jsonl").read_text().count("\n")
    status = SAMPLER.once(str(tmp_path))
    assert calls == [] and status["jobs"] == 0 and status["closed_at_defer_limit"] == 1
    rows = SAMPLER._events(str(tmp_path))
    assert sum(1 for row in rows if row.get("event") == "deferred") == SAMPLER.DEFER_LIMIT
    assert rows[-1]["event"] == "incomplete" and rows[-1]["reason"] == "defer_limit"
    assert len(rows) == lines_before + 1
