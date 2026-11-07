"""Live-base paper-rules release. The rejected f656 package is not applied."""
import fcntl
import hashlib
import json
import os
from pathlib import Path

import pytest

from deploy.paper_rules_release_t2816 import release
from veteran_desk.tests.test_gate_v2 import P0, g

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "paper_rules_livebase_20261107"
ART = Path("/opt/cursor/artifacts/paper_rules_livebase_20261107.tar.gz")
OLD = Path("/opt/cursor/artifacts/paper_rules_20261107.tar.gz")
CAPTURE = Path("/home/ubuntu/.cursor/projects/agent-repos-TFO-DESK-2026/uploads/tfo-sgp1-actual-base-gate-2326feaa-20261107T194317WITA.tar_862a.gz")
PACKAGE = "b1b289815776a6d912fa0b43bb8d0b337351d4058ee40eef4a4289f84cbe62ff"
STALE = "f65604a081fb3787e8ef2d57d5117fc7b8d3aeb76103f33ad922cdad13eb257d"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
BOOK_NEW = "a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b"
GATE = "2326feaa522d4c9de014d1523435aeb8ab83fd817e45396448beb50363446d54"
GATE_NEW = "1d1e8c82685e2df775d4543456f19104947fe6b24f567929707848c906f7f2a9"
STALE_GATE = "5582d153fa2d4d7270de228dd4ca0df0321c2efc7380b0080edd8af9b6c1dcb0"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _approval(path):
    body = json.loads((REPO / "deploy" / "paper_rules_release_t2816" / "APPROVAL.example.json").read_text())
    body.pop("example")
    path.write_text(json.dumps(body))
    return path


def _trees(tmp_path):
    live, dash = tmp_path / "live", tmp_path / "dash"
    for root in (live, dash):
        for rel in ("veteran_desk/book.py", "veteran_desk/gate_v2.py"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((PKG / "base" / rel).read_bytes())
        (root / "veteran_desk" / "desk.py").write_text("leave\n")
        ledger = root / "state" / "thf" / "book_ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"positions": 1}\n')
    return live, dash


def _apply(tmp_path, live, dash, probe=None):
    return release.apply_release(
        _approval(tmp_path / "approval.json"), ART,
        probe=probe or (lambda unit: "inactive"),
        host_name="tfo-sgp1", roots=(str(live), str(dash)))


def test_capture_matches_the_package_base_and_not_the_rejected_gate():
    assert _sha(CAPTURE) == "7a2cd5f9cd4d031e80fc141ee94b2b3af5ada5cac61f7fb5ae904ba43fc1175a"
    assert _sha(PKG / "base" / "veteran_desk" / "gate_v2.py") == GATE
    assert _sha(PKG / "base" / "veteran_desk" / "book.py") == BOOK
    assert _sha(PKG / "payload" / "veteran_desk" / "gate_v2.py") == GATE_NEW
    assert b"needs_brett_tick=False" in (PKG / "payload" / "veteran_desk" / "gate_v2.py").read_bytes()
    assert b"needs_brett_tick=True" not in (PKG / "payload" / "veteran_desk" / "gate_v2.py").read_bytes()
    assert _sha(ART) == PACKAGE
    assert _sha(OLD) == STALE


def test_grade_b_warning_matches_the_single_book_half():
    row = g(P0(grade="B"))
    assert row["armed"] is False and row["needs_brett_tick"] is False
    assert any("x1.0" in w and "not a second cut" in w for w in row["warnings"])
    assert not any(w.startswith("sized up") for w in row["warnings"])
    same = g(P0(grade="A"))
    assert abs(row["size_mult"] - same["size_mult"]) < 1e-9
    assert abs(row["risk_pct"] - same["risk_pct"]) < 1e-9


def test_apply_on_the_captured_base_and_restore_on_failure(tmp_path, monkeypatch):
    live, dash = _trees(tmp_path)
    receipt = _apply(tmp_path, live, dash)
    assert receipt["deployed"] is True and receipt["book_before"] == BOOK and receipt["book_after"] == BOOK_NEW
    assert receipt["loaded_by_running_engine"] is False and receipt["tick_restarted"] is False
    for root in (live, dash):
        assert _sha(root / "veteran_desk" / "gate_v2.py") == GATE_NEW
        assert _sha(root / "veteran_desk" / "book.py") == BOOK_NEW
        assert (root / "state" / "thf" / "book_ledger.json").read_text() == '{"positions": 1}\n'
        assert release.flock_free(root / ".tick.flock")
    real = release._put
    live2, dash2 = _trees(tmp_path / "fail")
    seen = {"n": 0}

    def boom(root, rel, data):
        seen["n"] += 1
        if seen["n"] == 3:
            raise RuntimeError("between files")
        return real(root, rel, data)

    monkeypatch.setattr(release, "_put", boom)
    with pytest.raises(RuntimeError):
        _apply(tmp_path / "fail", live2, dash2)
    for root in (live2, dash2):
        assert _sha(root / "veteran_desk" / "book.py") == BOOK
        assert _sha(root / "veteran_desk" / "gate_v2.py") == GATE


def test_tick_or_book_flock_refuses_without_being_held_afterwards(tmp_path):
    live, dash = _trees(tmp_path)
    lock = live / ".tick.flock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(release.ReleaseError, match="tick-flock"):
            _apply(tmp_path, live, dash)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert _sha(live / "veteran_desk" / "book.py") == BOOK
    assert release.flock_free(lock)
    live2, dash2 = _trees(tmp_path / "book")
    book = live2 / "state" / "thf" / ".book.flock"
    book.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(book, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(release.ReleaseError, match="book-flock"):
            _apply(tmp_path / "book", live2, dash2)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert _sha(live2 / "veteran_desk" / "gate_v2.py") == GATE


def test_stale_package_and_stale_gate_base_are_refused(tmp_path):
    example = REPO / "deploy" / "paper_rules_release_t2816" / "APPROVAL.example.json"
    with pytest.raises(release.ReleaseError, match="REFUSED_EXAMPLE_APPROVAL"):
        release.load_approval(example)
    live, dash = _trees(tmp_path)
    good = _approval(tmp_path / "approval.json")
    with pytest.raises(release.ReleaseError, match="REFUSED_STALE_PACKAGE"):
        release.apply_release(good, OLD, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
    assert _sha(live / "veteran_desk" / "gate_v2.py") == GATE
    (live / "veteran_desk" / "gate_v2.py").write_bytes((REPO / "deploy" / "paper_rules_20261107" / "base" / "veteran_desk" / "gate_v2.py").read_bytes())
    (dash / "veteran_desk" / "gate_v2.py").write_bytes((live / "veteran_desk" / "gate_v2.py").read_bytes())
    assert _sha(live / "veteran_desk" / "gate_v2.py") == STALE_GATE
    with pytest.raises(release.ReleaseError, match="REFUSED_STALE_BASE"):
        _apply(tmp_path, live, dash)


def test_staging_installer_still_refuses_production(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("livebase_install", PKG / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--root", "/opt/tfo/dash/base", "--staging", "--apply"]) == 2
    assert "REFUSED_ROOT" in capsys.readouterr().out
