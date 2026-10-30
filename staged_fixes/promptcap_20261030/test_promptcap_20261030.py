"""tfo_promptcap_20261030: codex backend never receives a prompt over the cap; grok_queue is uncapped; seat prompts are not trimmed."""
import subprocess
from veteran_desk import desk as D
def test_codex_oversize_blocked_never_sent(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(1))
    out = D._llm("codex", "x" * (D.CODEX_PROMPT_MAX_CHARS + 1))
    assert out.startswith("__ERR__ prompt blocked") and not called
def test_cap_is_about_10k():
    assert 8000 <= D.CODEX_PROMPT_MAX_CHARS <= 12000
def test_grok_queue_not_capped():
    import inspect; src = inspect.getsource(D._llm)
    assert 'engine != "grok_queue"' in src
def test_prop_prompt_keeps_full_data():
    seat = dict(seat="t", description="d", playbook=["p"])
    big = {"rows": ["y" * 50] * 1000}
    p = D.prop_prompt(seat, dict(big, news_regime="n"))
    assert len(p) > D.CODEX_PROMPT_MAX_CHARS   # full rich prompt preserved; the cap lives only on the codex backend
