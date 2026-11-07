"""Coverage stays first. Stale production prompts are not called again. Failures stay failures."""
import json

from veteran_desk import thf_shadow_bench as BENCH
from veteran_desk import thf_shadow_sampler as SAMPLER

NOW = "2026-11-07T12:00:00+00:00"
START = "2026-11-06T15:36:07+00:00"


def _enable(root, jobs=2):
    path = root / "state" / "thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(json.dumps(dict(
        enabled=True, paper_only=True, provider_timeout_s=5, max_jobs_per_run=jobs, busy_backoff_s=1800)))


def _clients(monkeypatch, answers):
    calls = []

    def make(name):
        def _call(prompt, seat, timeout):
            calls.append((name, prompt, seat))
            value = answers.get(name, "ready")
            if isinstance(value, Exception):
                raise value
            return value, name
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    return calls


def _queue(root, **kwargs):
    SAMPLER.enqueue(root, dict(
        task_id=kwargs["task_id"], seat=kwargs.get("seat", "banks"), role="trader",
        task_class=kwargs.get("task_class", "research_review"), data_asof=kwargs.get("data_asof", NOW),
        prompt=kwargs.get("prompt", "synthetic comparison"), source=kwargs.get("source", "coverage")))
    if kwargs.get("utc"):
        path = SAMPLER.jobs_path(root)
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        rows[-1]["utc"] = kwargs["utc"]
        open(path, "w", encoding="utf-8").write("".join(json.dumps(row) + "\n" for row in rows))


def test_production_queue_does_not_block_or_consume_coverage(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {})
    _enable(tmp_path, jobs=2)
    root = str(tmp_path)
    for index in range(5):
        _queue(root, task_id="old-prod-%d" % index, source="production_prompt",
               prompt="old market note %d" % index, utc="2026-11-06T20:00:00+00:00")
    _queue(root, task_id="fresh-prod", source="production_prompt", prompt="fresh note", utc=NOW)
    _queue(root, task_id="coverage-banks-1", source="coverage", prompt="seat coverage", utc="2026-11-07T11:00:00+00:00")
    _queue(root, task_id="coverage-energy-1", seat="energy", source="coverage", prompt="energy coverage",
           utc="2026-11-07T11:05:00+00:00")
    before = (tmp_path / "state/thf/shadow_jobs.jsonl").read_text()
    status = SAMPLER.once(root, now=NOW)
    assert status["jobs"] == 2 and status["coverage_selected"] == 2 and status["production_sampled"] == 0
    assert {seat for _name, _prompt, seat in calls} == {"banks", "energy"}
    assert status["production_audit_held"] == 5
    assert "old market note" not in (tmp_path / "state/thf/shadow_samples.jsonl").read_text()
    assert before in (tmp_path / "state/thf/shadow_jobs.jsonl").read_text()
    assert all(row.get("ok") is not True or row.get("source") != "production_prompt" for row in SAMPLER._events(root))


def test_a_fresh_production_row_uses_the_spare_slot_only(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=2)
    root = str(tmp_path)
    _queue(root, task_id="coverage-banks-1", prompt="frozen coverage", utc=NOW)
    _queue(root, task_id="fresh-prod", source="production_prompt", prompt="current comparison", utc=NOW)
    _queue(root, task_id="fresh-prod-2", source="production_prompt", prompt="second current", utc=NOW)
    SAMPLER.once(root, now=NOW)
    prompts = {prompt for _name, prompt, _seat in calls}
    assert "frozen coverage" in prompts and "current comparison" in prompts
    assert "second current" not in prompts
    coverage_hashes = {json.loads(line)["prompt_hash"] for line in (tmp_path / "state/thf/shadow_samples.jsonl").read_text().splitlines()
                       if json.loads(line).get("task_id") == "coverage-banks-1"}
    assert coverage_hashes == {BENCH._hash("frozen coverage")}


def test_coverage_resamples_when_only_production_is_newer(tmp_path):
    root = str(tmp_path)
    SAMPLER._append(SAMPLER.jobs_path(root), dict(
        event="finished", task_id="coverage-banks-old", seat="banks", source="coverage", utc="2026-11-07T00:00:00+00:00"))
    SAMPLER._append(SAMPLER.jobs_path(root), dict(
        event="queued", task_id="prod-new", seat="banks", source="production_prompt", prompt="later desk",
        utc="2026-11-07T11:00:00+00:00"))
    added = SAMPLER.ensure_coverage(root, now=NOW, resample_after_s=3600)
    assert "banks" in {row["seat"] for row in added}
    again = SAMPLER.ensure_coverage(root, now=NOW, resample_after_s=3600)
    assert [row["seat"] for row in again] == []


def test_legacy_terminal_errors_are_not_retried_or_marked_answered(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {name: "should-not-replace" for name in SAMPLER.CALLS})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    root = str(tmp_path)
    _queue(root, task_id="legacy-1", source="coverage", prompt="historical coverage", utc=NOW)
    for name, error in (
        ("grok", "RuntimeError: grok rc=1 Error: No prompt provided for print mode"),
        ("claude", "Input must be provided either through stdin or as a prompt argument when using --print"),
    ):
        SAMPLER._append(SAMPLER.samples_path(root), dict(
            task_id="legacy-1", provider_requested=name, availability="error", ok=False, scored=False,
            error=error, kind="shadow_evaluation", shadow_calls=1, utc="2026-11-07T02:00:00+00:00"))
    before = (tmp_path / "state/thf/shadow_samples.jsonl").read_text()
    SAMPLER.once(root, now=NOW)
    names = {name for name, _prompt, _seat in calls}
    assert "grok" not in names and "claude" not in names
    text = (tmp_path / "state/thf/shadow_samples.jsonl").read_text()
    assert before in text
    legacy = [json.loads(line) for line in text.splitlines() if json.loads(line).get("provider_requested") in ("grok", "claude")]
    assert legacy and all(row["ok"] is False for row in legacy)


def test_chatgpt_busy_does_not_close_a_near_match_or_skip_the_other_seat(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {"chatgpt": RuntimeError("chatgpt busy")})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    root = str(tmp_path)
    _queue(root, task_id="coverage-banks-busy", prompt="banks coverage", utc="2026-11-07T11:00:00+00:00")
    for _ in range(3):
        SAMPLER._append(SAMPLER.jobs_path(root), dict(
            event="deferred", reason="provider_busy", task_id="coverage-banks-busy", utc="2026-11-07T11:30:00+00:00",
            deferred=["chatgpt"]))
    SAMPLER._append(SAMPLER.samples_path(root), dict(
        task_id="coverage-banks-busy", provider_requested="chatgpt", availability="busy", ok=False, scored=False,
        error="RuntimeError: chatgpt busy", latency_ms=10, kind="shadow_evaluation", utc="2026-11-07T11:50:00+00:00"))
    for name in ("claude", "grok", "deepseek_api"):
        SAMPLER._append(SAMPLER.samples_path(root), dict(
            task_id="coverage-banks-busy", provider_requested=name, availability="answered", ok=True, scored=True,
            output_hash="abc", provider_actual=name, shadow_calls=1, kind="shadow_evaluation",
            utc="2026-11-07T11:40:00+00:00"))
    _queue(root, task_id="coverage-energy-open", seat="energy", prompt="energy coverage", utc="2026-11-07T11:10:00+00:00")
    status = SAMPLER.once(root, now=NOW)
    assert {seat for _name, _prompt, seat in calls} == {"energy"}
    assert status["closed_at_defer_limit"] == 0
    assert all(row.get("event") != "incomplete" for row in SAMPLER._events(root) if row.get("task_id") == "coverage-banks-busy")


def test_restart_keeps_the_clock_and_does_not_duplicate_coverage(tmp_path, monkeypatch):
    _clients(monkeypatch, {})
    _enable(tmp_path, jobs=2)
    root = str(tmp_path)
    (tmp_path / "state/thf/shadow_window.json").write_text(json.dumps(dict(started=True, started_utc=START)))
    first = SAMPLER.once(root, now=NOW)
    jobs = SAMPLER._events(root)
    coverage = [row for row in jobs if row.get("event") == "queued" and row.get("source") == "coverage"]
    assert len(coverage) == 18
    second = SAMPLER.once(root, now=NOW)
    coverage_again = [row for row in SAMPLER._events(root) if row.get("event") == "queued" and row.get("source") == "coverage"]
    assert len(coverage_again) == 18
    assert second["window"]["first_row_utc"] == START
    assert second["seven_day"] == "clock_started" and second["seven_day_ready"] is False
    assert second["recommendation"]["review_at"] == first["recommendation"]["review_at"]
    assert second["recommendation"]["review_at"] != BENCH.REVIEW_AT
    assert second["recommendation"]["global_winner"] is None
    assert all(cell["recommendation"] == "gap" for cell in second["recommendation"]["cells"])


def test_a_busy_seat_yields_at_the_exact_timer_boundary(tmp_path, monkeypatch):
    calls = _clients(monkeypatch, {})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    root = str(tmp_path)
    _queue(root, task_id="coverage-banks-b", prompt="banks", utc="2026-11-07T12:00:00+00:00")
    _queue(root, task_id="coverage-energy-e", seat="energy", prompt="energy", utc="2026-11-07T12:00:01+00:00")
    for name in SAMPLER.PROVIDERS:
        SAMPLER._append(SAMPLER.samples_path(root), dict(
            task_id="coverage-banks-b", provider_requested=name, availability="busy", ok=False, scored=False,
            error="RuntimeError: provider busy", latency_ms=5, kind="shadow_evaluation",
            utc="2026-11-07T12:00:00+00:00"))
    SAMPLER.once(root, now="2026-11-07T12:30:00+00:00")
    assert {seat for _name, _prompt, seat in calls} == {"energy"}


def test_a_permanent_busy_seat_stays_waiting(tmp_path, monkeypatch):
    _clients(monkeypatch, {name: RuntimeError("provider busy lock held") for name in SAMPLER.CALLS})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda *a, **k: [])
    _enable(tmp_path, jobs=1)
    root = str(tmp_path)
    _queue(root, task_id="coverage-banks-z", prompt="banks", utc=NOW)
    status = SAMPLER.once(root, now=NOW)
    last = [row for row in SAMPLER._events(root) if row.get("task_id") == "coverage-banks-z"][-1]
    assert last["event"] == "deferred" and last["reason"] == "provider_busy"
    assert status["waiting_unmatched"] == [dict(task_id="coverage-banks-z", seat="banks", reason="provider_busy", matched=False)]
    assert status["throughput"] == "waiting_unmatched"
    assert status["seven_day_ready"] is False
    text = (tmp_path / "state/thf/shadow_samples.jsonl").read_text()
    assert all(json.loads(line)["ok"] is False for line in text.splitlines())


def test_once_uses_the_supplied_clock_for_readiness(tmp_path, monkeypatch):
    _clients(monkeypatch, {})
    _enable(tmp_path, jobs=1)
    root = tmp_path / "state" / "thf"
    (root / "shadow_window.json").write_text(json.dumps(dict(started=True, started_utc=START)))

    class Later(SAMPLER.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 1, 1, tzinfo=SAMPLER.dt.timezone.utc)

    monkeypatch.setattr(SAMPLER.dt, "datetime", Later)
    early = SAMPLER.once(str(tmp_path), now="2026-11-13T16:00:00+00:00")
    assert early["seven_day"] == "clock_started" and early["seven_day_ready"] is False
    assert early["window"]["first_row_utc"] == START
    due = SAMPLER.once(str(tmp_path), now="2026-11-14T00:00:01+00:00")
    assert due["seven_day"] == "review_due" and due["seven_day_ready"] is True
    assert due["window"]["first_row_utc"] == START


def test_seven_days_before_the_morning_review_is_not_ready(tmp_path):
    started = "2026-11-06T15:36:07+00:00"
    due = SAMPLER.review_due(started)
    assert due.strftime("%Y-%m-%d %H:%M WITA") == "2026-11-14 08:00 WITA"
    early = __import__("datetime").datetime(2026, 11, 13, 16, 0, tzinfo=__import__("datetime").timezone.utc)
    assert early < due.astimezone(early.tzinfo)
    body = SAMPLER.lock_window(str(tmp_path), dict(started=True, first_row_utc=started), now=early)
    assert body["started_utc"] == started and body["seven_day_ready"] is False
    advice = BENCH.recommend(
        [dict(role="trader", task_class="research_review", provider_requested="claude")] * 4,
        review_at=body["review_due_utc"])
    assert advice["cells"][0]["n"] == 4 and advice["cells"][0]["recommendation"] == "gap"
    assert advice["review_at"] == body["review_due_utc"]
    assert BENCH.MIN_N == 20
