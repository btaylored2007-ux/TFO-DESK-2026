"""Prompt inclusion uses the run id the caller already has. A nightly review is not a ticket."""
import hashlib
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from veteran_desk import desk as D
from veteran_desk import lesson_trace as LT
from veteran_desk import seat_brain as SB
from veteran_desk import thf_chat_tasks as TASKS
from veteran_desk import thf_msgdesk as CHAT

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "lesson_caller_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
CAPTURED_DESK = "670a55e65f52e6730561f0bac46248ccdee965680444aaff62384e2252124dd4"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_brain_block_records_the_run_id_and_the_captured_desk_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setattr(SB, "BASE", str(tmp_path))
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("THF_LESSON_TRACE", str(path))
    SB.lesson("banks", "trade", "The stop held.", ref="trade-44")
    text = D._brain_block({"seat": "banks"}, "20261106-1801-banks")
    assert "ref=trade-44" in text
    rows = LT.load(str(path))
    included = [row for row in rows if row.get("level") == "included"]
    assert included and included[0]["task_id"] == "20261106-1801-banks"
    assert "trade-44" in included[0]["lesson_ids"]
    assert not any(row.get("level") == "decision_link" for row in rows)
    captured = (PKG / "base" / "veteran_desk" / "desk.py").read_text()
    assert _sha(PKG / "base" / "veteran_desk" / "desk.py") == CAPTURED_DESK
    assert 'prompt_block(seat["seat"], 1100)' in captured
    assert "task_id" not in captured.split("def _brain_block", 1)[1].split("def _after_round", 1)[0]
    assert 'prop_prompt(s, pk, runid + "-" + seat)' in (REPO / "veteran_desk" / "thf_round.py").read_text()


def test_nightly_cites_the_status_stamp_and_code_hands_is_not_a_ticket(tmp_path, monkeypatch):
    monkeypatch.setattr(SB, "DIR", str(tmp_path / "brains"))
    monkeypatch.setattr(SB, "BASE", str(tmp_path))
    monkeypatch.setattr(SB, "STATUS", str(tmp_path / "nightly.json"))
    path = str(tmp_path / "trace.jsonl")
    monkeypatch.setenv("THF_LESSON_TRACE", path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    SB.lesson("banks", "trade", "The stop held.", ref="trade-44")
    monkeypatch.setattr(SB, "ensure", lambda *a, **k: dict(name="Bank", role="proposer"))
    monkeypatch.setattr(SB, "lessons_from_book", lambda **k: [])
    monkeypatch.setattr(SB, "code_facts", lambda *a, **k: ["kept"])
    monkeypatch.setattr(SB, "review_prompt", lambda *a: "review")
    monkeypatch.setattr(SB, "write_facts", lambda *a, **k: None)
    import veteran_desk.claude_brain as CB
    monkeypatch.setattr(CB, "ask", lambda *a, **k: ('{"facts":["one fact","two facts"],"journal":"Noted ref=trade-44."}', "fixture"))
    result = SB.nightly(["banks"], ai=True, S={"banks": {}})
    task = result["seats"]["banks"]["task_id"]
    assert task == "nightly|%s|banks" % result["wita"]
    assert json.loads(Path(SB.STATUS).read_text())["wita"] == result["wita"]
    rows = LT.load(path)
    assert any(row.get("level") == "included" and row.get("task_id") == task and "trade-44" in (row.get("lesson_ids") or []) for row in rows)
    assert any(row.get("level") == "cited" and row.get("task_id") == task and "trade-44" in (row.get("cited_ids") or []) for row in rows)
    assert not any(row.get("level") in ("decision_link", "validated") for row in rows)
    hands = inspect.getsource(CHAT.code_hands)
    assert "note_accepted_decision" not in hands and "link_later_decision" not in hands
    learning = inspect.getsource(TASKS.learning_prompt)
    assert "prompt_block(target,task_id=task)" in learning
    assert "msg.get(\"id\")" in learning


def _stage(tmp_path):
    root = tmp_path / "stage"
    base = PKG / "base"
    for path in base.rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(base)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
    shutil.copy(REPO / "veteran_desk" / "book.py", root / "veteran_desk" / "book.py")
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    return root


def _run(root, *args):
    return subprocess.run([sys.executable, str(PKG / "install.py"), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_caller_package_replaces_only_the_captured_desk_and_refuses_production(tmp_path):
    root = _stage(tmp_path)
    dry = _run(root)
    assert dry.returncode == 0, dry.stdout
    assert "REPLACE veteran_desk/desk.py" in dry.stdout
    assert "UNCHANGED veteran_desk/feed_health.py" in dry.stdout
    assert "INSTALL_NEW veteran_desk/feed_status_facts.py" in dry.stdout
    staged = _run(root, "--staging", "--apply")
    assert staged.returncode == 0, staged.stdout
    assert _sha(root / "veteran_desk" / "desk.py") == _sha(REPO / "veteran_desk" / "desk.py")
    assert _sha(root / "veteran_desk" / "feed_health.py") == _sha(PKG / "base" / "veteran_desk" / "feed_health.py")
    original = (PKG / "base" / "veteran_desk" / "desk.py").read_bytes()
    back = _run(root, "--staging", "--rollback")
    assert back.returncode == 0 and "ROLLED_BACK" in back.stdout
    assert (root / "veteran_desk" / "desk.py").read_bytes() == original
    (root / "veteran_desk" / "desk.py").write_bytes(original + b"\n# unknown\n")
    blocked = _run(root, "--staging", "--apply")
    assert blocked.returncode == 2 and "BLOCKED veteran_desk/desk.py" in blocked.stdout
    refused = _run(Path("/opt/tfo/live/thf"), "--staging", "--apply")
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    assert not Path("/opt/tfo/live/thf/veteran_desk/feed_status_facts.py").exists()
