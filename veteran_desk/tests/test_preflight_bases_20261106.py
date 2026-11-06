"""Staged live bases: main apply, rollback, then the stall bundle, without touching the book."""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "deploy/incremental_20261106/install.py"
STALL = ROOT / "deploy/chatgpt_stall_20261106/install.py"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
LIVE_ROOT = Path("/tmp/b7/thf-upgrade-blocked7-live-20261106/live")
SEVEN = (
    "asia_intraday_sgp.py",
    "thf_feedkeeper.py",
    "tools/intraday_bars_pull.py",
    "tools/thf_eli10.html",
    "tools/thf_eli10.js",
    "tools/thf_chat.html",
    "tools/thf_chat_page.html",
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(script, root, *extra):
    return subprocess.run([sys.executable, str(script), "--root", str(root), *extra],
                          cwd=ROOT, text=True, capture_output=True, check=False)

def stage(tmp_path):
    for rel in SEVEN:
        src = LIVE_ROOT / rel
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "veteran_desk/book.py", book)
    home = tmp_path / "veteran_desk/thf_chat_home.py"
    shutil.copy2(Path("/tmp/run2closure/thf-upgrade-run2-closure-20261106/veteran_desk/thf_chat_home.py"), home)
    web = tmp_path / "tools/chatgpt_web.py"
    web.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path("/tmp/cgstall/thf-upgrade-chatgpt-stall-src-20261106/tools/chatgpt_web.py"), web)
    shutil.copy2(ROOT / "tools/chatgpt_admission.py", tmp_path / "tools/chatgpt_admission.py")
    bridge = tmp_path / "veteran_desk/chatgpt_bridge.py"
    subprocess.check_call(["git", "show", "d601fe4:veteran_desk/chatgpt_bridge.py"], cwd=ROOT, stdout=bridge.open("wb"))
    return {rel: sha(tmp_path / rel) for rel in SEVEN}

def test_live_seven_apply_then_rollback_then_stall(tmp_path):
    before = stage(tmp_path)
    assert sha(tmp_path / "veteran_desk/book.py") == BOOK
    dry = run(MAIN, tmp_path)
    assert dry.returncode == 0, dry.stdout
    assert "BLOCKED" not in dry.stdout
    assert "ALREADY_PROTECTED veteran_desk/book.py" in dry.stdout
    for rel in SEVEN:
        assert ("REPLACE " + rel) in dry.stdout
    applied = run(MAIN, tmp_path, "--apply")
    assert applied.returncode == 0, applied.stdout
    assert sha(tmp_path / "veteran_desk/book.py") == BOOK
    for rel in SEVEN:
        assert sha(tmp_path / rel) == sha(ROOT / rel)
        assert sha(tmp_path / rel) != before[rel]
    rolled = run(MAIN, tmp_path, "--rollback")
    assert rolled.returncode == 0, rolled.stdout
    for rel in SEVEN:
        assert sha(tmp_path / rel) == before[rel]
    assert sha(tmp_path / "veteran_desk/book.py") == BOOK
    again = run(MAIN, tmp_path, "--apply")
    assert again.returncode == 0, again.stdout
    stall = run(STALL, tmp_path)
    assert stall.returncode == 0, stall.stdout
    assert "BLOCKED" not in stall.stdout
    assert "ALREADY_PROTECTED veteran_desk/book.py" in stall.stdout
    assert "CURRENT veteran_desk/thf_chat_home.py" in stall.stdout or "REPLACE veteran_desk/thf_chat_home.py" in stall.stdout
    stall_apply = run(STALL, tmp_path, "--apply")
    assert stall_apply.returncode == 0, stall_apply.stdout
    assert sha(tmp_path / "veteran_desk/book.py") == BOOK
    assert sha(tmp_path / "tools/chatgpt_web.py") == sha(ROOT / "tools/chatgpt_web.py")
    assert sha(tmp_path / "veteran_desk/thf_chat_home.py") == sha(ROOT / "veteran_desk/thf_chat_home.py")
