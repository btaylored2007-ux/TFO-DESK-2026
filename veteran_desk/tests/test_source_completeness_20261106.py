"""The committed tree must import the runtime modules the dashboard already calls. No network."""
import json
from veteran_desk import ai_router, communication_protocol, llm_backend, thf_chat_tasks, thf_competition, thf_home


def test_dashboard_modules_import_and_do_not_embed_credentials():
    for module in (ai_router, communication_protocol, thf_chat_tasks, thf_competition, thf_home):
        text = open(module.__file__, encoding="utf-8").read()
        assert "BEGIN PRIVATE" not in text and "AKIA" not in text
    assert ai_router.norm("deepseek") == "deepseek_api"
    reply = communication_protocol.chat_reply('{"reply":"paper note"}')
    assert reply["reply"] == "paper note"
    assert thf_chat_tasks.command_mode(dict(command=dict(mode="ask"))) == "ask"
    assert thf_home.aud(12.5) and "127.0.0.1:28870" in open(thf_home.__file__, encoding="utf-8").read()


def test_brain_log_write_stays_on_the_supplied_path(tmp_path, monkeypatch):
    path = tmp_path / "seat_brain.jsonl"
    monkeypatch.setattr(llm_backend, "BRAIN_LOG", str(path))
    llm_backend._brainlog(dict(seat="banks", ai="test", ok=True, note="fixture"))
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["seat"] == "banks" and row["ok"] is True and "utc" in row
