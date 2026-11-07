"""Proofs for the packaged maintenance-guard client. No live root is used."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent
REPO = PKG.parents[1]
sys.path.insert(0, str(REPO))
BASE = "26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c"
TARGET = "c477d99674f5d6dd49bd2be60c21d35f8716fbd1f72e790ad7832c2645920098"
DIFF = "53d01dfca611b62762e1d84dcd87eb5f6d1e961cdef9a18b3c5751823509329e"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
from veteran_desk import bridge_maintenance as B
from veteran_desk import chatgpt_bridge as CG


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hold(path):
    msg = dict(id="fm-original", text="Read-only question", seat="macro", command={"mode": "ask"})
    value = dict(version=B.VERSION, generation="generation-1", phase="hold", created_at=time.time() - 1,
                 deadline=time.time() + 599, protected=[dict(key="fm-original|macro", original_sha256=B.original_hash(msg),
                                                             replay="safe", review_id="audit-1")])
    Path(path).write_text(json.dumps(value))


def test_package_bytes_match_the_repo_target():
    assert sha(PKG / "base/veteran_desk/chatgpt_bridge.py") == BASE
    assert sha(PKG / "payload/veteran_desk/chatgpt_bridge.py") == TARGET
    assert sha(PKG / "chatgpt_bridge.diff") == DIFF
    assert sha(REPO / "veteran_desk/chatgpt_bridge.py") == TARGET
    assert sha(REPO / "veteran_desk/book.py") == BOOK


def test_hold_before_probe_opens_nothing(tmp_path, monkeypatch):
    control = tmp_path / "control.json"
    monkeypatch.setattr(B, "CONTROL", str(control))
    monkeypatch.setattr(CG, "enabled", lambda: True)
    monkeypatch.setattr(CG, "busy", lambda *a, **k: False)
    CG._T[:] = []
    calls = []
    real = B.client_guard
    def guard():
        calls.append(1)
        if len(calls) == 2:
            hold(control)
        return real()
    monkeypatch.setattr(B, "client_guard", guard)
    marked = []
    monkeypatch.setattr(B, "mark_submission", lambda: marked.append(1))
    opened = []
    monkeypatch.setattr(CG.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    with pytest.raises(B.MaintenanceHold):
        CG.ask("prompt", timeout=90)
    assert len(calls) == 2 and CG._T == [] and opened == [] and marked == []


def test_hold_before_post_blocks_the_submission_and_rolls_back(tmp_path, monkeypatch):
    control = tmp_path / "control.json"
    monkeypatch.setattr(B, "CONTROL", str(control))
    monkeypatch.setattr(CG, "enabled", lambda: True)
    monkeypatch.setattr(CG, "busy", lambda *a, **k: False)
    CG._T[:] = []
    calls = []
    real = B.client_guard
    def guard():
        calls.append(1)
        if len(calls) == 3:
            hold(control)
        return real()
    monkeypatch.setattr(B, "client_guard", guard)
    marked = []
    monkeypatch.setattr(B, "mark_submission", lambda: marked.append(1))
    urls = []
    def urlopen(request, timeout=None):
        urls.append(request if isinstance(request, str) else request.full_url)
        return io.BytesIO(b'{"delivery":"ack_poll_v1","state":"logged_in"}')
    monkeypatch.setattr(CG.urllib.request, "urlopen", urlopen)
    with pytest.raises(B.MaintenanceHold):
        CG.ask("prompt", timeout=90)
    assert len(calls) == 3 and CG._T == [] and marked == []
    assert urls == ["http://127.0.0.1:18891/health"]


def test_ack_poll_still_posts_once_and_polls_the_same_id(monkeypatch):
    monkeypatch.setattr(CG, "enabled", lambda: True)
    monkeypatch.setattr(CG, "busy", lambda *a, **k: False)
    CG._T[:] = []
    calls = []
    def target(request):
        return request if isinstance(request, str) else request.full_url
    def urlopen(request, timeout=None):
        calls.append((target(request), getattr(request, "data", None)))
        url = target(request)
        if url.endswith("/health"):
            return io.BytesIO(b'{"delivery":"ack_poll_v1","state":"logged_in"}')
        if url.endswith("/ask"):
            return io.BytesIO(b'{"ok":true,"accepted":true,"state":"pending"}')
        return io.BytesIO(b'{"ok":true,"state":"done","text":"polled"}')
    monkeypatch.setattr(CG.urllib.request, "urlopen", urlopen)
    assert CG.ask("no order", timeout=90, request_id="same-id") == "polled"
    posts = [json.loads(body) for url, body in calls if url.endswith("/ask")]
    assert len(posts) == 1 and posts[0]["ack"] is True and posts[0]["request_id"] == "same-id"
    assert [url for url, _ in calls] == [
        "http://127.0.0.1:18891/health",
        "http://127.0.0.1:18891/ask",
        "http://127.0.0.1:18891/result?request_id=same-id",
    ]
    assert len(CG._T) == 1


def test_without_ack_poll_there_is_one_blocking_post(monkeypatch):
    monkeypatch.setattr(CG, "enabled", lambda: True)
    monkeypatch.setattr(CG, "busy", lambda *a, **k: False)
    CG._T[:] = []
    calls = []
    def target(request):
        return request if isinstance(request, str) else request.full_url
    def urlopen(request, timeout=None):
        url = target(request)
        calls.append(url)
        if url.endswith("/health"):
            return io.BytesIO(b'{"delivery":"sync","state":"logged_in"}')
        return io.BytesIO(b'{"ok":true,"state":"done","text":"direct"}')
    monkeypatch.setattr(CG.urllib.request, "urlopen", urlopen)
    assert CG.ask("no order", timeout=90, request_id="plain") == "direct"
    assert calls == ["http://127.0.0.1:18891/health", "http://127.0.0.1:18891/ask"]
    assert len(CG._T) == 1


def _tree(tmp_path, bridge_bytes):
    root = tmp_path / "root"
    desk = root / "veteran_desk"
    desk.mkdir(parents=True)
    (desk / "chatgpt_bridge.py").write_bytes(bridge_bytes)
    shutil.copy(REPO / "veteran_desk/book.py", desk / "book.py")
    return root


def test_installer_replaces_only_the_base_and_rollback_restores_it(tmp_path):
    root = _tree(tmp_path, (PKG / "base/veteran_desk/chatgpt_bridge.py").read_bytes())
    install = str(PKG / "install.py")
    dry = subprocess.run([sys.executable, install, "--root", str(root)], capture_output=True, text=True)
    assert dry.returncode == 0 and "REPLACE veteran_desk/chatgpt_bridge.py" in dry.stdout
    assert "ALREADY_PROTECTED veteran_desk/book.py" in dry.stdout
    assert sha(root / "veteran_desk/chatgpt_bridge.py") == BASE
    applied = subprocess.run([sys.executable, install, "--root", str(root), "--apply"], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert sha(root / "veteran_desk/chatgpt_bridge.py") == TARGET
    assert sha(root / "veteran_desk/book.py") == BOOK
    rolled = subprocess.run([sys.executable, install, "--root", str(root), "--rollback"], capture_output=True, text=True)
    assert rolled.returncode == 0 and "ROLLBACK_RESTORED" in rolled.stdout
    assert sha(root / "veteran_desk/chatgpt_bridge.py") == BASE
    assert sha(root / "veteran_desk/book.py") == BOOK


def test_installer_refuses_a_different_bridge_and_copies_nothing(tmp_path):
    root = _tree(tmp_path, b"not-the-live-base")
    install = str(PKG / "install.py")
    dry = subprocess.run([sys.executable, install, "--root", str(root)], capture_output=True, text=True)
    assert dry.returncode == 3 and "BLOCKED veteran_desk/chatgpt_bridge.py" in dry.stdout
    applied = subprocess.run([sys.executable, install, "--root", str(root), "--apply"], capture_output=True, text=True)
    assert applied.returncode == 2 and "APPLY_REFUSED" in applied.stdout
    assert (root / "veteran_desk/chatgpt_bridge.py").read_bytes() == b"not-the-live-base"
    assert sha(root / "veteran_desk/book.py") == BOOK
    assert not (root / "state").exists()
