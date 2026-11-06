"""The live-base package replaces only the captured Singapore bytes, and only in a staging copy."""
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "lesson_provenance_livebase_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
NAMES = ("lesson_trace.py", "seat_brain.py", "thf_round.py", "thf_msgdesk.py")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path):
    root = tmp_path / "stage"
    for name in NAMES:
        dest = root / "veteran_desk" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PKG / "base" / "veteran_desk" / name, dest)
    shutil.copy(REPO / "veteran_desk" / "book.py", root / "veteran_desk" / "book.py")
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    return root


def _run(root, *args):
    return subprocess.run([sys.executable, str(PKG / "install.py"), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_captured_live_links_position_id_and_payload_does_not(tmp_path):
    live = _load(PKG / "base" / "veteran_desk" / "lesson_trace.py", "captured_live_lesson_trace")
    payload = _load(PKG / "payload" / "veteran_desk" / "lesson_trace.py", "payload_lesson_trace")
    assert live.AUTHORITY["rule"] == payload.AUTHORITY["rule"]
    assert payload.GENUINE_VERIFIERS == frozenset()
    live_path = str(tmp_path / "live.jsonl")
    live.record_included("banks", ["VET-1"], "seat_brain.prompt_block", live_path)
    live.append(live_path, dict(live.interpret_reply("ref=VET-1", ["VET-1"]), seat="banks", source="captured"))
    linked = live.observe_book_close("banks", "VET-1", live_path, dict(source="book_close", trade_id="VET-1", net_aud=1))
    assert linked["level"] == "outcome_link" and linked["lesson_id"] == "VET-1" and linked["validated"] is False
    accepted = live.record_validated("banks", "VET-1", live_path, dict(check="book_close"))
    assert accepted["validated"] is True
    new_path = str(tmp_path / "payload.jsonl")
    payload.record_included("banks", ["trade-44"], "seat_brain.prompt_block", new_path, task_id="round-a")
    payload.record_citation("banks", "ref=trade-44", new_path, "round-a")
    refused = payload.observe_book_close("banks", "trade-44", new_path, dict(
        source="book_close", trade_id="VET-1", net_aud=1, closed_utc="2099-01-01T00:00:00+00:00"))
    assert refused["reason"] == "no_later_decision" and refused["validated"] is False
    with pytest.raises(ValueError, match="genuine verifier"):
        payload.record_validated("banks", "trade-44", new_path, dict(check="book_close"))


def test_installer_replaces_only_known_live_bases_and_rolls_back(tmp_path):
    root = _stage(tmp_path)
    dry = _run(root)
    assert dry.returncode == 0
    assert "REPLACE veteran_desk/lesson_trace.py" in dry.stdout
    assert "UNCLOSED_DEPENDENCY veteran_desk/feed_status_facts.py" in dry.stdout
    assert "FEED_NOT_COPIED" in dry.stdout
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    staged = _run(root, "--staging", "--apply")
    assert staged.returncode == 0 and "STAGED" in staged.stdout
    for name in NAMES:
        assert _sha(root / "veteran_desk" / name) == _sha(PKG / "payload" / "veteran_desk" / name)
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    back = _run(root, "--staging", "--rollback")
    assert back.returncode == 0 and "ROLLED_BACK" in back.stdout
    for name in NAMES:
        assert _sha(root / "veteran_desk" / name) == _sha(PKG / "base" / "veteran_desk" / name)
    original = (root / "veteran_desk" / "lesson_trace.py").read_bytes()
    path = root / "veteran_desk" / "lesson_trace.py"
    path.write_bytes(original + b"\n# unknown\n")
    blocked = _run(root, "--staging", "--apply")
    assert blocked.returncode == 2 and "BLOCKED veteran_desk/lesson_trace.py" in blocked.stdout
    assert path.read_bytes() == original + b"\n# unknown\n"
    path.write_bytes(original)
    (root / "veteran_desk" / "thf_round.py").unlink()
    absent = _run(root, "--staging", "--apply")
    assert absent.returncode == 2 and "ABSENT_REFUSED veteran_desk/thf_round.py" in absent.stdout
    assert not (root / "veteran_desk" / "thf_round.py").exists()


def test_production_roots_are_refused(tmp_path):
    refused = _run(Path("/opt/tfo/live/thf"), "--staging", "--apply")
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    assert not Path("/opt/tfo/live/thf/veteran_desk/lesson_trace.py").exists()
    refused_dash = _run(Path("/opt/tfo/dash/base"), "--staging", "--apply")
    assert refused_dash.returncode == 2 and "REFUSED_ROOT" in refused_dash.stdout
    repo = _run(REPO, "--staging", "--apply")
    assert repo.returncode == 2 and "REFUSED_ROOT" in repo.stdout
