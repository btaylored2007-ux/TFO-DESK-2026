"""A spent review budget is not sent again, and a busy bridge does not take the only job slot."""
import json

from veteran_desk import claude_brain as CB
from veteran_desk import thf_shadow_sampler as SAMPLER


def _enable(root, jobs=1):
    path = root / "state" / "thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(json.dumps(dict(
        enabled=True, paper_only=True, provider_timeout_s=90, max_jobs_per_run=jobs)))
    (path / "shadow_window.json").write_text(json.dumps(dict(
        started=True, started_utc="2026-11-06T15:36:07+00:00")))


def _clients(monkeypatch, answers):
    calls = []

    def make(name):
        def _call(prompt, seat, timeout):
            calls.append((name, prompt, seat, timeout))
            value = answers.get(name)
            if isinstance(value, Exception):
                raise value
            return value, name
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    return calls


def test_a_spent_fable_budget_does_not_start_opus(tmp_path, monkeypatch):
    calls = []

    def _run(model, prompt, timeout, **kwargs):
        calls.append((model, kwargs.get("prio"), timeout))
        raise RuntimeError("claude %s timeout after %ss" % (model, int(timeout)))

    monkeypatch.setattr(CB, "LOG", str(tmp_path / "claude.jsonl"))
    monkeypatch.setattr(CB, "primary", lambda: "fable")
    monkeypatch.setattr(CB, "fallback", lambda: "opus")
    monkeypatch.setattr(CB, "_run", _run)
    monkeypatch.setattr(CB, "fable_cooling", lambda: False)
    monkeypatch.setattr(CB, "seat_gate", lambda *a, **k: None)

    class _CD:
        def cooling(self, name):
            return False
        def mark_fail(self, *a, **k):
            return None

    monkeypatch.setattr(CB, "_cd", lambda: _CD())
    try:
        CB.ask("x" * 60000, seat="trend", timeout=90, kind="review", prio="low")
        raise AssertionError("timeout was accepted")
    except RuntimeError as error:
        assert "timeout after" in str(error)
    assert [row[0] for row in calls] == ["fable"]
    assert calls[0][1] == "low"
    assert CB.prio_of("chat") == "high"
    logged = json.loads((tmp_path / "claude.jsonl").read_text().splitlines()[-1])
    assert logged["ok"] is False and logged["prompt_chars"] == 60000


def test_captured_timeout_shape_is_not_sent_again(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {name: "ready" for name in SAMPLER.CALLS})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path)
    SAMPLER.enqueue(str(tmp_path), dict(task_id="20261106-1457-trend", seat="trend", prompt="the long review", source="test"))
    SAMPLER._append(SAMPLER.samples_path(str(tmp_path)), dict(
        task_id="20261106-1457-trend", provider_requested="claude", availability="timeout", ok=False, scored=False,
        error="RuntimeError: claude failed: fable: claude fable timeout after 89s", latency_ms=90100,
        kind="shadow_evaluation"))
    before = (tmp_path / "state" / "thf" / "shadow_samples.jsonl").read_text()
    status = SAMPLER.once(str(tmp_path))
    assert "claude" not in [name for name, *_rest in calls]
    assert before in (tmp_path / "state" / "thf" / "shadow_samples.jsonl").read_text()
    assert status["window"]["first_row_utc"] == "2026-11-06T15:36:07+00:00"
    assert status["window"]["paired_tasks"] == 0


def test_a_short_timeout_can_still_be_retried_once(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {name: "ready" for name in SAMPLER.CALLS})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path)
    SAMPLER.enqueue(str(tmp_path), dict(task_id="short", seat="banks", prompt="short review", source="test"))
    for name in SAMPLER.PROVIDERS:
        if name == "claude":
            continue
        SAMPLER._append(SAMPLER.samples_path(str(tmp_path)), dict(
            task_id="short", provider_requested=name, availability="unavailable", ok=False, scored=False,
            error="unavailable", kind="shadow_evaluation"))
    SAMPLER._append(SAMPLER.samples_path(str(tmp_path)), dict(
        task_id="short", provider_requested="claude", availability="timeout", ok=False, scored=False,
        error="timeout", latency_ms=1000, budget_s=90, kind="shadow_evaluation"))
    SAMPLER.once(str(tmp_path))
    assert [name for name, *_rest in calls] == ["claude"]


def test_busy_chatgpt_stays_inside_the_job_cap_and_does_not_recall_claude(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {"chatgpt": RuntimeError("chatgpt busy"), "claude": "kept", "grok": "kept", "deepseek_api": "kept"})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    SAMPLER.enqueue(str(tmp_path), dict(task_id="busy-old", seat="news", prompt="old", source="test"))
    for name in SAMPLER.PROVIDERS:
        if name == "chatgpt":
            continue
        SAMPLER._append(SAMPLER.samples_path(str(tmp_path)), dict(
            task_id="busy-old", provider_requested=name, availability="unavailable", ok=False, scored=False,
            error="unavailable", kind="shadow_evaluation"))
    SAMPLER.enqueue(str(tmp_path), dict(task_id="next-real", seat="banks", prompt="the next review", source="test"))
    status = SAMPLER.once(str(tmp_path))
    assert [name for name, *_rest in calls] == ["chatgpt"]
    assert status["jobs"] == 1 and status["fast_deferred"] == 1
    text = (tmp_path / "state" / "thf" / "shadow_samples.jsonl").read_text()
    assert "chatgpt busy" in text and "the next review" not in text
    busy = [json.loads(line) for line in text.splitlines() if "chatgpt busy" in line][-1]
    assert busy["availability"] == "busy" and busy["ok"] is False and busy["scored"] is False


def test_a_busy_row_stays_when_chatgpt_later_answers(tmp_path, monkeypatch):
    state = {"busy": True}
    calls = []

    def _chatgpt(prompt, seat, timeout):
        calls.append(("chatgpt", prompt))
        if state["busy"]:
            raise RuntimeError("chatgpt busy")
        return "later answer", "chatgpt"

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, _chatgpt if name == "chatgpt" else (lambda *a: ("kept", "other")))
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    SAMPLER.enqueue(str(tmp_path), dict(task_id="recover", seat="banks", prompt="same prompt", source="test"))
    for name in SAMPLER.PROVIDERS:
        if name == "chatgpt":
            continue
        SAMPLER._append(SAMPLER.samples_path(str(tmp_path)), dict(
            task_id="recover", provider_requested=name, availability="answered", ok=True, scored=True,
            output_hash="abc", provider_actual=name, shadow_calls=1, kind="shadow_evaluation",
            error=None))
    SAMPLER.once(str(tmp_path))
    state["busy"] = False
    SAMPLER.once(str(tmp_path))
    text = (tmp_path / "state" / "thf" / "shadow_samples.jsonl").read_text()
    assert "chatgpt busy" in text and "later answer" in text
    assert text.index("chatgpt busy") < text.index("later answer")
