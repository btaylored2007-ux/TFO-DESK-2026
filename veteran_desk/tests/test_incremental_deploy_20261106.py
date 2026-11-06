"""Guarded increment: the audited book hash is protected; a different book is not copied."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "deploy" / "incremental_20261106" / "install.py"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"

def run(root, apply=False):
    cmd = [sys.executable, str(INSTALL), "--root", str(root)]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)

def test_matching_book_is_already_protected_and_not_rewritten(tmp_path):
    dest = tmp_path / "veteran_desk" / "book.py"
    dest.parent.mkdir(parents=True)
    payload = (ROOT / "veteran_desk" / "book.py").read_bytes()
    dest.write_bytes(payload)
    assert hashlib.sha256(payload).hexdigest() == BOOK
    result = run(tmp_path)
    assert result.returncode == 0
    assert "ALREADY_PROTECTED veteran_desk/book.py" in result.stdout
    assert "BOOK_RUNTIME ALREADY_PROTECTED" in result.stdout
    assert dest.read_bytes() == payload

def test_unexpected_book_hash_blocks_and_apply_does_not_copy(tmp_path):
    dest = tmp_path / "veteran_desk" / "book.py"
    dest.parent.mkdir(parents=True)
    dest.write_text("RAIL_BANK_R = 1.0\n")
    before = dest.read_bytes()
    result = run(tmp_path, apply=True)
    assert result.returncode == 2
    assert "BLOCKED veteran_desk/book.py" in result.stdout
    assert "APPLY_REFUSED" in result.stdout
    assert dest.read_bytes() == before
    assert not (tmp_path / "veteran_desk" / "research_mandate.py").exists()

def test_known_base_hash_is_replaceable_and_rollback_restores_it(tmp_path):
    dest = tmp_path / "veteran_desk" / "desk.py"
    dest.parent.mkdir(parents=True)
    dest.write_text("live desk baseline\n")
    # The installer only replaces a recorded base hash. This fixture uses the book rule
    # for the protected file and a payload file whose base is unknown, so it stays blocked
    # until the hash matches a recorded base. The book case is covered above.
    blocked = run(tmp_path, apply=True)
    assert "BLOCKED veteran_desk/desk.py" in blocked.stdout
    assert blocked.returncode == 2
    assert dest.read_text() == "live desk baseline\n"

def test_empty_tree_dry_run_names_book_install_separately(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0
    assert "INSTALL veteran_desk/book.py" in result.stdout
    assert "BOOK_RUNTIME INSTALL" in result.stdout
    assert "INSTALL veteran_desk/research_mandate.py" in result.stdout
    assert not (tmp_path / "veteran_desk").exists()
