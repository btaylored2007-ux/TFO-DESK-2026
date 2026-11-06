"""The shadow sampler calls the four live client functions and does not arm."""
import json
import os
import subprocess
import sys

from veteran_desk import lesson_trace as LT
from veteran_desk import research_mandate as RM
from veteran_desk import seat_brain as SB
from veteran_desk import thf_round as ROUND
from veteran_desk import thf_shadow_bench as BENCH
from veteran_desk import thf_shadow_sampler as SAMPLER

NOW = "2026-11-06T13:00:00+00:00"


def _enable(root, jobs=18):
    path = root / "state" / "thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(json.dumps(dict(
        enabled=True, paper_only=True, provider_timeout_s=5, max_jobs_per_run=jobs)))


def _install_clients(monkeypatch, answers):
    calls = []

    def make(name):
        def _call(prompt, seat, timeout):
            calls.append((name, prompt, seat, timeout))
            value = answers.get(name)
            if isinstance(value, Exception):
                raise value
            return value, name + "-live"
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    return calls


def test_disabled_sampler_does_not_call_providers(tmp_path, monkeypatch):
    calls = _install_clients(monkeypatch, {})
    status = SAMPLER.once(str(tmp_path))
    assert status["enabled"] is False and status["called"] is False and calls == []
    assert json.loads((tmp_path / "state/thf/shadow_sampler_status.json").read_text())["availability"] == "pending_configuration"


def test_matched_calls_use_the_four_client_entrypoints_and_do_not_start_on_failures(tmp_path, monkeypatch):
    calls = _install_clients(monkeypatch, {
        "claude": "alpha",
        "chatgpt": RuntimeError("chatgpt bridge off (down)"),
        "grok": RuntimeError("Authenticated Grok route not successfully verified"),
        "deepseek_api": RuntimeError("DeepSeek live API disabled in tests"),
    })
    _enable(tmp_path, jobs=1)
    SAMPLER.ensure_coverage(str(tmp_path), now=NOW)
    assert {row["seat"] for row in SAMPLER._events(str(tmp_path))} == set(LT.SEATS)
    traders = [row for row in SAMPLER._events(str(tmp_path)) if row.get("role") == "trader"]
    admins = [row for row in SAMPLER._events(str(tmp_path)) if row.get("role") == "admin"]
    assert len(traders) == 11 and len(admins) == 7
    assert all(row["task_class"] == "research_review" for row in traders)
    assert all("Do not place an order" in row["prompt"] for row in traders + admins)
    status = SAMPLER.once(str(tmp_path), now=NOW)
    assert status["called"] is True and status["jobs"] == 1
    assert sorted(name for name, *_rest in calls) == ["chatgpt", "claude", "deepseek_api", "grok"]
    assert len({prompt for _name, prompt, _seat, _timeout in calls}) == 1
    rows = [json.loads(line) for line in (tmp_path / "state/thf/shadow_samples.jsonl").read_text().splitlines()]
    assert any(row["availability"] == "unavailable" and row["scored"] is False for row in rows)
    assert status["window"]["started"] is False
    assert status["recommendation"]["global_winner"] is None


def test_paired_answers_start_the_clock_and_a_restart_does_not_repeat_them(tmp_path, monkeypatch):
    calls = _install_clients(monkeypatch, {name: "ready " + name for name in SAMPLER.CALLS})
    monkeypatch.setattr(SAMPLER, "ensure_coverage", lambda root, now=None, resample_after_s=0: [])
    _enable(tmp_path, jobs=1)
    SAMPLER.enqueue(str(tmp_path), dict(task_id="pair-1", seat="banks", role="trader", task_class="research_review",
                                       data_asof=NOW, prompt="same prompt", source="test"))
    first = SAMPLER.once(str(tmp_path))
    assert first["window"]["started"] is True and first["window"]["paired_tasks"] == 1
    done = len(calls)
    second = SAMPLER.once(str(tmp_path))
    assert second["jobs"] == 0 and len(calls) == done


def test_real_clients_without_credentials_are_unavailable_not_a_score():
    from veteran_desk import chatgpt_bridge, deepseek_bridge, thf_grok
    for name, client in (("chatgpt", chatgpt_bridge), ("deepseek_api", deepseek_bridge), ("grok", thf_grok)):
        if name == "grok" and thf_grok.reason() is None:
            continue
        row = SAMPLER.call_provider(name, "SHADOW ping. Do not place an order.", "banks", 8)
        assert row["ok"] is False and row["scored"] is False
        assert row["availability"] in ("unavailable", "quota", "timeout", "error")
        assert row["text"] == ""
    assert SAMPLER.call_chatgpt.__code__.co_names.count("ask") or "ask" in SAMPLER.call_chatgpt.__code__.co_names
    assert chatgpt_bridge.ask.__module__ == "veteran_desk.chatgpt_bridge"


def test_eleven_seat_rounds_record_empty_output_without_a_trade(tmp_path):
    path = tmp_path / "research.jsonl"
    for seat in RM.TRADERS:
        view = RM.record_round(seat, None, NOW, "thf_round.admit", str(path))
        assert view["hunt_satisfied"] is False and view["forced_trade"] is False
        assert view["blocked_by"] == "empty_output" and view["execution_eligible"] is False
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 11
    text = open(ROUND.__file__, encoding="utf-8").read()
    assert "record_round(" in text and "enqueue_production_prompt(" in text
    assert "trade_reply" not in open(SAMPLER.__file__, encoding="utf-8").read()


def test_lesson_trace_survives_a_new_process(tmp_path):
    brains = tmp_path / "brains"
    trace = tmp_path / "trace.jsonl"
    script = tmp_path / "write_trace.py"
    script.write_text(
        "import os\n"
        "os.environ['THF_LESSON_TRACE']=%r\n"
        "from veteran_desk import seat_brain as SB\n"
        "SB.DIR=%r\n"
        "SB.BASE=%r\n"
        "SB.lesson('banks','trade','Stop was respected.', ref='lesson-banks-9')\n"
        "print(SB.prompt_block('banks', 900, task_id='banks-round'))\n"
        "SB.after_chat('banks','round','Noted ref=lesson-banks-9.', brain='fixture', task_id='banks-round')\n" % (str(trace), str(brains), str(tmp_path))
    )
    env = dict(os.environ, PYTHONPATH="/agent/repos/TFO-DESK-2026")
    subprocess.check_call([sys.executable, str(script)], cwd="/agent/repos/TFO-DESK-2026", env=env)
    rows = LT.load(str(trace))
    assert rows[0]["level"] == "included" and "lesson-banks-9" in rows[0]["lesson_ids"]
    assert any(row.get("cited_ids") == ["lesson-banks-9"] for row in rows)
    reader = tmp_path / "read_trace.py"
    reader.write_text(
        "import json\n"
        "from veteran_desk import lesson_trace as LT\n"
        "print(json.dumps(LT.coverage(LT.load(%r))))\n" % str(trace)
    )
    out = subprocess.check_output([sys.executable, str(reader)], cwd="/agent/repos/TFO-DESK-2026", text=True, env=env)
    states = {item["seat"]: item["state"] for item in json.loads(out)}
    assert states["banks"] != "no_evidence" and len(states) == 18


def test_finished_seats_are_sampled_again_after_the_interval(tmp_path):
    root = str(tmp_path)
    SAMPLER._append(SAMPLER.jobs_path(root), dict(event="finished", task_id="old", seat="banks",
                                                utc="2026-11-06T00:00:00+00:00"))
    added = SAMPLER.ensure_coverage(root, now="2026-11-06T12:00:00+00:00", resample_after_s=3600)
    seats = {row["seat"] for row in added}
    assert "banks" in seats and len(seats) == 18
    recent = SAMPLER.ensure_coverage(root, now="2026-11-06T12:00:00+00:00", resample_after_s=3600)
    assert recent == []


def test_production_log_does_not_start_the_comparison(tmp_path):
    path = tmp_path / "production.jsonl"
    BENCH.note_production(dict(task_id="p", seat="banks", role="trader", task_class="hunt",
                               prompt="Schema: {}", data_asof=NOW, text="stand aside", provider="claude"), str(path))
    row = json.loads(path.read_text().splitlines()[-1])
    assert row["shadow_calls"] == 0 and row["kind"] == "production_inference"
    assert BENCH.window_status([row])["started"] is False


def test_fixture_rows_do_not_open_the_window():
    rows = []
    for name in BENCH.PROVIDERS:
        rows.append(dict(task_id="t", provider_requested=name, provider_actual=name + "-fixture",
                         ok=True, output_hash="abc", prompt_hash="p", data_asof=NOW))
    assert BENCH.window_status(rows)["started"] is False
    assert BENCH.recommend(rows)["global_winner"] is None
