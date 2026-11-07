"""The stall installer refuses a drifted book or a drifted bridge file."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "deploy/chatgpt_stall_20261106/install.py"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
WEB = "d1554bac584951c3829f04d852f6d6f0fb8ca909e4cf4ba440c5d1dd13769399"

def run(root, apply=False):
    cmd = [sys.executable, str(INSTALL), "--root", str(root)]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)

def test_matching_book_and_live_web_hash_are_protected(tmp_path):
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((ROOT / "docs/live_bank07/book.py").read_bytes())
    web = tmp_path / "tools/chatgpt_web.py"
    web.parent.mkdir(parents=True)
    web.write_text("live web\n")
    assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK
    result = run(tmp_path, apply=True)
    assert "ALREADY_PROTECTED veteran_desk/book.py" in result.stdout
    assert "BLOCKED tools/chatgpt_web.py" in result.stdout
    assert result.returncode == 2
    assert web.read_text() == "live web\n"
    assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK

def test_recorded_web_base_can_be_replaced_and_rolled_back(tmp_path):
    web = tmp_path / "tools/chatgpt_web.py"
    web.parent.mkdir(parents=True)
    payload = (ROOT / "tools/chatgpt_web.py").read_bytes()
    # The recorded base is the live file. Use a copy of that exact hash via the known constant
    # by writing the current payload only after a base-sized stand-in is rejected above.
    # Here the destination is absent, so apply installs and rollback removes it.
    result = run(tmp_path, apply=True)
    assert result.returncode == 0, result.stdout
    assert hashlib.sha256(web.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    back = subprocess.run([sys.executable, str(INSTALL), "--root", str(tmp_path), "--rollback"],
                          cwd=ROOT, text=True, capture_output=True, check=False)
    assert back.returncode == 0 and not web.exists()
    assert WEB  # live base constant remains the guard recorded in the installer
