"""Production control for paper_rules_20261107. Does not rerun the three-archive release."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from deploy.paper_rules_release_20261107 import release

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "paper_rules_20261107"
ART = Path("/opt/cursor/artifacts/paper_rules_20261107.tar.gz")
PACKAGE = "f65604a081fb3787e8ef2d57d5117fc7b8d3aeb76103f33ad922cdad13eb257d"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
BOOK_NEW = "a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b"
GATE = "5582d153fa2d4d7270de228dd4ca0df0321c2efc7380b0080edd8af9b6c1dcb0"
GATE_NEW = "3ca464e4d34b93b3def67820c4ce64e9032217f5ea2b71f1f8cd8a062a409335"
SENTENCE = "Explicitly authorisation to deploy and clear the blocks."


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _approval(path):
    body = json.loads((REPO / "deploy" / "paper_rules_release_20261107" / "APPROVAL.example.json").read_text())
    body.pop("example")
    path.write_text(json.dumps(body))
    return path


def _trees(tmp_path):
    live, dash = tmp_path / "live", tmp_path / "dash"
    for root in (live, dash):
        for rel in ("veteran_desk/book.py", "veteran_desk/gate_v2.py"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil_src = PKG / "base" / rel
            dest.write_bytes(shutil_src.read_bytes())
        other = root / "veteran_desk" / "desk.py"
        other.write_text("leave-this-file\n")
        ledger = root / "state" / "thf" / "book_ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"positions": 1}\n')
    return live, dash


def _apply(tmp_path, live, dash, probe=None, package=None):
    return release.apply_release(
        _approval(tmp_path / "approval.json"),
        package or ART,
        probe=probe or (lambda unit: "inactive"),
        host_name="tfo-sgp1",
        roots=(str(live), str(dash)))


def test_quote_and_coordinator_scope_are_separate_from_the_three_archives():
    allow = release.ALLOW
    assert allow["authorization_verbatim"] == SENTENCE
    assert allow["package_sha256"] == PACKAGE
    assert allow["not_in_this_apply"] == ["empty_prompt_20261107", "review_budget_20261107", "retail_dash_20261107"]
    assert [row["path"] for row in allow["files"]] == ["veteran_desk/book.py", "veteran_desk/gate_v2.py"]
    old = json.loads((REPO / "deploy" / "thf_release_20261107" / "ALLOWLIST.json").read_text())
    assert old["book_pin"] == BOOK and "veteran_desk/book.py" in old["excluded"]
    assert "paper_rules_20261107" not in {row["name"] for row in old["archives"]}
    assert "--root" not in (REPO / "deploy" / "paper_rules_release_20261107" / "release.py").read_text().split("add_argument")[1]
    assert "restart" not in (REPO / "deploy" / "paper_rules_release_20261107" / "release.py").read_text().split("add_argument")[-1]


def test_staging_installer_still_refuses_production(capsys):
    spec = importlib.util.spec_from_file_location("paper_stage", PKG / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--root", "/opt/tfo/live/thf", "--staging", "--apply"]) == 2
    assert "REFUSED_ROOT" in capsys.readouterr().out


def test_real_package_writes_only_the_two_files_and_keeps_the_rails(tmp_path):
    if not ART.is_file():
        pytest.skip("package archive is not on this machine")
    assert _sha(ART) == PACKAGE
    live, dash = _trees(tmp_path)
    receipt = _apply(tmp_path, live, dash)
    assert receipt["deployed"] is True and receipt["book_unchanged"] is False
    assert receipt["book_before"] == BOOK and receipt["book_after"] == BOOK_NEW
    assert receipt["loaded_by_running_engine"] is False and receipt["tick_restarted"] is False
    assert receipt["engine_restarted"] is False and receipt["ledger_rewritten"] is False
    for root in (live, dash):
        assert _sha(root / "veteran_desk" / "book.py") == BOOK_NEW
        assert _sha(root / "veteran_desk" / "gate_v2.py") == GATE_NEW
        assert (root / "veteran_desk" / "desk.py").read_text() == "leave-this-file\n"
        assert (root / "state" / "thf" / "book_ledger.json").read_text() == '{"positions": 1}\n'
        original = root / "state" / "thf" / "paper_rules_release_20261107_backups" / receipt["txn"] / "original" / "veteran_desk" / "book.py"
        assert _sha(original) == BOOK
    assert release.refresh_would_defer(live) is False


def test_every_write_failure_restores_both_roots(tmp_path, monkeypatch):
    if not ART.is_file():
        pytest.skip("package archive is not on this machine")
    real = release._put
    for stop in range(1, 5):
        live, dash = _trees(tmp_path / str(stop))
        seen = {"n": 0}

        def boom(root, rel, data, stop=stop):
            seen["n"] += 1
            if seen["n"] == stop:
                raise RuntimeError("injected %s" % stop)
            return real(root, rel, data)

        monkeypatch.setattr(release, "_put", boom)
        with pytest.raises(RuntimeError):
            _apply(tmp_path / str(stop), live, dash)
        for root in (live, dash):
            assert _sha(root / "veteran_desk" / "book.py") == BOOK
            assert _sha(root / "veteran_desk" / "gate_v2.py") == GATE
            assert (root / "state" / "thf" / "book_ledger.json").read_text() == '{"positions": 1}\n'
        failed = json.loads((live / "state" / "thf" / "paper_rules_release_20261107_receipt.json").read_text())
        assert failed["deployed"] is False and failed["restored"] is True and failed["loaded_by_running_engine"] is False


def test_receipt_failure_and_corrupt_backup(tmp_path, monkeypatch):
    if not ART.is_file():
        pytest.skip("package archive is not on this machine")
    live, dash = _trees(tmp_path)
    real_write = Path.write_text

    def fail_success(self, data, *args, **kwargs):
        if self.name == "paper_rules_release_20261107_receipt.json" and '"deployed": true' in data:
            raise OSError("receipt")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_success)
    with pytest.raises(OSError):
        _apply(tmp_path, live, dash)
    assert _sha(live / "veteran_desk" / "book.py") == BOOK
    assert _sha(dash / "veteran_desk" / "gate_v2.py") == GATE
    failed = json.loads((live / "state" / "thf" / "paper_rules_release_20261107_receipt.json").read_text())
    assert failed["deployed"] is False and failed["restored"] is True
    monkeypatch.undo()
    lone = tmp_path / "one"
    dest = lone / "veteran_desk" / "book.py"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"original-book")
    txn = release.Transaction([str(lone), str(tmp_path / "other")])
    txn.seal(lone, "veteran_desk/book.py")
    rec = txn.originals[(str(lone), "veteran_desk/book.py")]
    os.chmod(rec["path"], 0o644)
    rec["path"].write_bytes(b"corrupt")
    dest.write_bytes(b"partial")
    assert txn.restore_all() is False
    assert dest.read_bytes() == b"original-book"
    rec["bytes"] = None
    dest.write_bytes(b"leave")
    with pytest.raises(release.ReleaseError, match="REFUSED_BACKUP"):
        txn.restore_all()
    assert dest.read_bytes() == b"leave"


def test_missing_approval_wrong_archive_busy_and_bad_base(tmp_path):
    example = REPO / "deploy" / "paper_rules_release_20261107" / "APPROVAL.example.json"
    with pytest.raises(release.ReleaseError, match="REFUSED_EXAMPLE_APPROVAL"):
        release.load_approval(example)
    bad = tmp_path / "bad.json"
    body = json.loads(example.read_text())
    body.pop("example")
    body["archives"] = [{"name": "empty_prompt_20261107", "sha256": "abc"}]
    bad.write_text(json.dumps(body))
    with pytest.raises(release.ReleaseError, match="REFUSED_APPROVAL_SCOPE"):
        release.load_approval(bad)
    body.pop("archives")
    body["coordinator_bound"]["package_sha256"] = "0" * 64
    bad.write_text(json.dumps(body))
    with pytest.raises(release.ReleaseError, match="REFUSED_APPROVAL_SCOPE"):
        release.load_approval(bad)
    if ART.is_file():
        other = tmp_path / "nope.gz"
        other.write_bytes(b"not-the-package")
        live, dash = _trees(tmp_path / "arch")
        good = _approval(tmp_path / "arch" / "approval.json")
        with pytest.raises(release.ReleaseError, match="REFUSED_ARCHIVE"):
            release.apply_release(good, other, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
        assert _sha(live / "veteran_desk" / "book.py") == BOOK
        live2, dash2 = _trees(tmp_path / "busy")
        with pytest.raises(release.ReleaseError, match="REFUSED_BUSY"):
            _apply(tmp_path / "busy", live2, dash2, probe=lambda unit: "active")
        assert _sha(live2 / "veteran_desk" / "book.py") == BOOK
        assert not (live2 / "state" / "thf" / "paper_rules_release_20261107_receipt.json").exists()
        live3, dash3 = _trees(tmp_path / "base")
        (live3 / "veteran_desk" / "book.py").write_text("different\n")
        with pytest.raises(release.ReleaseError, match="REFUSED_BASE"):
            _apply(tmp_path / "base", live3, dash3)
        assert (dash3 / "veteran_desk" / "book.py").read_bytes() == (PKG / "base" / "veteran_desk" / "book.py").read_bytes()


def test_payload_needles_and_a_symlink_are_refused(tmp_path):
    book = (PKG / "payload" / "veteran_desk" / "book.py").read_bytes()
    gate = (PKG / "payload" / "veteran_desk" / "gate_v2.py").read_bytes()
    release.invariants("veteran_desk/book.py", book)
    release.invariants("veteran_desk/gate_v2.py", gate)
    with pytest.raises(release.ReleaseError, match="REFUSED_INVARIANT"):
        release.invariants("veteran_desk/book.py", book.replace(b"RAIL_BANK_R = 0.7", b"RAIL_BANK_R = 0.1"))
    with pytest.raises(release.ReleaseError, match="REFUSED_INVARIANT"):
        release.invariants("veteran_desk/gate_v2.py", gate + b"\nhunt48_shorts = True\n")
    if not ART.is_file():
        return
    live, dash = _trees(tmp_path)
    outside = tmp_path / "out"
    outside.mkdir()
    link = live / "veteran_desk" / "linked.py"
    (live / "veteran_desk" / "book.py").unlink()
    (live / "veteran_desk" / "book.py").symlink_to(outside / "book.py")
    with pytest.raises(release.ReleaseError, match="REFUSED_SYMLINK"):
        _apply(tmp_path, live, dash)
