"""Chat learning uses the message id. The sampler is checked and not replaced."""
import hashlib
import importlib.util
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

from veteran_desk import lesson_trace as LT
from veteran_desk import seat_brain as SB
from veteran_desk import thf_chat_tasks as TASKS
from veteran_desk import thf_msgdesk as CHAT

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "lesson_final_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
SAMPLER = "a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef"
CHAT_BASE = "3ebeda8439b66b7eafa28555185670d1b38dfb804202ed4f62399e4252ca581f"
RULE = ("A lesson is included when placed in a prompt, cited only if the reply names that ref, "
        "and validated only by a later book close. Otherwise the state is insufficient_data.")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_learning_prompt_separates_included_cited_and_not_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setattr(SB, "BASE", str(tmp_path))
    path = str(tmp_path / "trace.jsonl")
    monkeypatch.setenv("THF_LESSON_TRACE", path)
    monkeypatch.setattr("veteran_desk.thf_competition.query_context", lambda *a, **k: "")
    SB.lesson("banks", "trade", "The stop held.", ref="trade-44")
    captured = _load(PKG / "base" / "veteran_desk" / "thf_chat_tasks.py", "captured_chat_tasks")
    assert _sha(PKG / "base" / "veteran_desk" / "thf_chat_tasks.py") == CHAT_BASE
    captured.learning_prompt("banks", {"id": "msg-44", "text": "review", "command": {"mode": "learn"}})
    assert not any(row.get("level") == "included" for row in LT.load(path))
    text = TASKS.learning_prompt("banks", {"id": "msg-44", "text": "review", "command": {"mode": "learn"}})
    assert "ref=trade-44" in text
    included = [row for row in LT.load(path) if row.get("level") == "included"]
    assert len(included) == 1 and included[0]["task_id"] == "msg-44" and included[0]["validated"] is False
    before = len(LT.load(path))
    TASKS.learning_prompt("banks", {"text": "review", "command": {"mode": "learn"}})
    assert not any(row.get("level") == "included" for row in LT.load(path)[before:])
    answer = CHAT.mark_chat_answer("banks", "msg-44", "The review uses ref=trade-44.", path)
    rows = LT.load(path)
    assert any(row.get("level") == "cited" and row.get("task_id") == "msg-44" and "trade-44" in (row.get("cited_ids") or []) for row in rows)
    assert answer["level"] == "no_decision" and answer["reason"] == "chat_answer_not_a_ticket"
    assert not any(row.get("level") in ("decision_link", "outcome_link", "validated") for row in rows)
    assert LT.AUTHORITY["rule"] == RULE and LT.GENUINE_VERIFIERS == frozenset()
    assert "does not set validated" in LT.RULE_GAP


def test_captured_sampler_keeps_the_caller_task_id_and_does_not_validate(tmp_path):
    path = PKG / "base" / "veteran_desk" / "thf_shadow_sampler.py"
    assert _sha(path) == SAMPLER
    sampler = _load(path, "captured_shadow_sampler")
    row = sampler.enqueue_production_prompt(str(tmp_path), "banks", "prompt text", "2026-11-06T10:00:00+00:00",
                                            "20261106-1801-banks", role="trader")
    assert row["task_id"] == "20261106-1801-banks" and row["event"] == "queued"
    source = path.read_text()
    assert "record_validated" not in source and "note_accepted_decision" not in source
    assert "LT.publish_authority" in source and "LT.load" in source
    signature = inspect.signature(sampler.enqueue_production_prompt)
    assert list(signature.parameters) == ["root", "seat", "prompt", "data_asof", "task_id", "role", "task_class"]
    round_src = (REPO / "veteran_desk" / "thf_round.py").read_text()
    chat_src = (REPO / "veteran_desk" / "thf_msgdesk.py").read_text()
    assert "enqueue_production_prompt" in round_src and "enqueue_production_prompt" in chat_src


def _stage(tmp_path):
    root = tmp_path / "stage"
    for path in (PKG / "base").rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(PKG / "base")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
    shutil.copy(REPO / "docs" / "live_bank07" / "book.py", root / "veteran_desk" / "book.py")
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    return root


def _run(root, *args):
    return subprocess.run([sys.executable, str(PKG / "install.py"), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_final_package_replaces_chat_tasks_and_leaves_the_sampler(tmp_path):
    root = _stage(tmp_path)
    sampler = (root / "veteran_desk" / "thf_shadow_sampler.py").read_bytes()
    dry = _run(root)
    assert dry.returncode == 0, dry.stdout
    assert "REPLACE veteran_desk/thf_chat_tasks.py" in dry.stdout
    assert "UNCHANGED veteran_desk/thf_shadow_sampler.py" in dry.stdout
    assert "UNCHANGED veteran_desk/feed_health.py" in dry.stdout
    assert "INSTALL_NEW veteran_desk/feed_status_facts.py" in dry.stdout
    staged = _run(root, "--staging", "--apply")
    assert staged.returncode == 0, staged.stdout
    assert _sha(root / "veteran_desk" / "thf_chat_tasks.py") == _sha(REPO / "veteran_desk" / "thf_chat_tasks.py")
    assert (root / "veteran_desk" / "thf_shadow_sampler.py").read_bytes() == sampler
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    back = _run(root, "--staging", "--rollback")
    assert back.returncode == 0 and "ROLLED_BACK" in back.stdout
    assert _sha(root / "veteran_desk" / "thf_chat_tasks.py") == CHAT_BASE
    assert (root / "veteran_desk" / "thf_shadow_sampler.py").read_bytes() == sampler
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    (root / "veteran_desk" / "thf_shadow_sampler.py").write_bytes(sampler + b"\n# unknown\n")
    blocked = _run(root, "--staging", "--apply")
    assert blocked.returncode == 2 and "BLOCKED veteran_desk/thf_shadow_sampler.py" in blocked.stdout
    assert (root / "veteran_desk" / "thf_shadow_sampler.py").read_bytes() == sampler + b"\n# unknown\n"
    refused = _run(Path("/opt/tfo/live/thf"), "--staging", "--apply")
    dash = _run(Path("/opt/tfo/dash/base"), "--staging", "--apply")
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    assert dash.returncode == 2 and "REFUSED_ROOT" in dash.stdout
    assert not Path("/opt/tfo/live/thf/veteran_desk/feed_status_facts.py").exists()
