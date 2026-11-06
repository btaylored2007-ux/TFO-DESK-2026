"""Guards for the narrow retail dashboard package. These do not verify Singapore."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/retail_dash_20261107"
INSTALL = PACKAGE / "install.py"
MANIFEST = json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8"))
STATUS = json.loads((PACKAGE / "STATUS.json").read_text(encoding="utf-8"))
BASE = "2c45d1a28a894578ca015f06ea7065c2d22f9c6a"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_bytes(rel):
    return subprocess.check_output(["git", "show", "%s:%s" % (BASE, rel)], cwd=ROOT)


def run(*args):
    return subprocess.run([sys.executable, str(INSTALL), *args], cwd=ROOT, text=True, capture_output=True, check=False)


def stage(tmp_path):
    for rel in [item["path"] for item in MANIFEST["replace"]] + ["veteran_desk/thf_home.py", "veteran_desk/book.py"]:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel == "veteran_desk/book.py":
            dest.write_bytes((ROOT / rel).read_bytes())
        elif rel == "veteran_desk/thf_home.py":
            dest.write_bytes(git_bytes(rel))
        else:
            dest.write_bytes(git_bytes(rel))
    for item in MANIFEST["identical_or_absent"]:
        dest = tmp_path / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / item["path"]).read_bytes())
    return tmp_path


def test_package_is_waiting_and_does_not_claim_live_acceptance():
    assert STATUS["phase"] == "prepared" and STATUS["status"] == "WAITING_CAPTURE"
    assert STATUS["singapore_deployed"] is False and STATUS["verified_live"] is False
    assert STATUS["restart"] == "not performed"
    assert STATUS["acceptance"]["live_pnl_clock"] == "not verified"
    assert STATUS["acceptance"]["singapore_forward_test"] == "not run"
    text = INSTALL.read_text(encoding="utf-8")
    assert "systemctl" not in text and "ssh " not in text
    names = [item["path"] for item in MANIFEST["replace"] + MANIFEST["new_files"] + MANIFEST["identical_or_absent"]]
    assert "veteran_desk/book.py" not in names
    assert "veteran_desk/desk.py" not in names
    for item in MANIFEST["replace"]:
        assert sha256(ROOT / item["path"]) == item["target_sha256"]
        assert hashlib.sha256(git_bytes(item["path"])).hexdigest() == item["base_sha256"]


def test_source_checks_are_not_a_live_pass():
    eli = (ROOT / "tools/thf_eli10.js").read_text(encoding="utf-8")
    chat = (ROOT / "tools/thf_chat.js").read_text(encoding="utf-8")
    html = (ROOT / "tools/thf_eli10.html").read_text(encoding="utf-8")
    assert 'TABS=["home","trades","chat","seats","health"]' in eli.replace(" ", "")
    assert "Since 06:00 WITA" in eli and "Since midnight WITA" in eli
    assert "The AI brains" not in eli and "/api/ai_status" not in chat
    assert "Urgent" in eli and "Figures are " in eli
    assert "id=dc-send" in chat and "id=dc-tools" in chat and "id=dc-to" in chat
    assert "/api/chat_img" in chat and "From " in chat
    assert "Not a live broker" in html and "PAPER" in html
    assert "min-height:0" in chat


def test_production_and_unpinned_apply_stop():
    refused = run("--root", "/opt/tfo/live/thf")
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    repo = run("--root", str(ROOT))
    assert repo.returncode == 2 and "REFUSED_ROOT" in repo.stdout
    waiting = run("--root", "/tmp")
    assert waiting.returncode == 2 and "WAITING_CAPTURE" in waiting.stdout and "NOT_DEPLOYED" in waiting.stdout


def test_known_base_can_be_pinned_applied_and_rolled_back(tmp_path):
    live = stage(tmp_path)
    pin = tmp_path / "pin.json"
    recorded = run("--root", str(live), "--staging", "--record-capture", "--out", str(pin))
    assert recorded.returncode == 0, recorded.stdout
    assert "PINNED" in recorded.stdout and "NOT_DEPLOYED" in recorded.stdout
    body = json.loads(pin.read_text(encoding="utf-8"))
    assert body["applyable"] is True and body["verified_live"] is False
    dry = run("--root", str(live), "--pin", str(pin))
    assert dry.returncode == 0, dry.stdout
    assert "DRY_RUN" in dry.stdout and "REPLACE tools/thf_eli10.html" in dry.stdout
    assert "NOT_DEPLOYED" in dry.stdout
    bare = run("--root", str(live), "--pin", str(pin), "--apply")
    assert bare.returncode == 2 and "APPLY_REFUSED" in bare.stdout and "NOT_DEPLOYED" in bare.stdout
    assert sha256(live / "tools/thf_chat.js") == MANIFEST["replace"][2]["base_sha256"]
    applied = run("--root", str(live), "--pin", str(pin), "--staging", "--apply")
    assert applied.returncode == 0, applied.stdout
    assert "STAGED" in applied.stdout and "NOT_DEPLOYED" in applied.stdout
    receipt = json.loads((live / "state/thf/retail_dash_20261107_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "APPLIED_NOT_DEPLOYED" and receipt["restart"] == "not performed"
    assert receipt["verified_live"] is False
    assert sha256(live / "veteran_desk/book.py") == BOOK
    assert sha256(live / "tools/thf_chat.js") == MANIFEST["replace"][2]["target_sha256"]
    assert (live / "assets/brand/apple-touch-icon.png").is_file()
    assert receipt["before"]["veteran_desk/thf_dash.py"] == MANIFEST["replace"][4]["base_sha256"]
    assert receipt["after"]["veteran_desk/thf_dash.py"] == MANIFEST["replace"][4]["target_sha256"]
    rolled = run("--root", str(live), "--staging", "--rollback")
    assert rolled.returncode == 0, rolled.stdout
    note = json.loads((live / "state/thf/retail_dash_20261107_rollback.json").read_text(encoding="utf-8"))
    assert note["status"] == "ROLLED_BACK_NOT_DEPLOYED"
    assert sha256(live / "tools/thf_chat.js") == MANIFEST["replace"][2]["base_sha256"]
    assert sha256(live / "veteran_desk/book.py") == BOOK
    assert not (live / "assets/brand/apple-touch-icon.png").exists()


def test_unknown_base_and_broken_anchor_stop(tmp_path):
    live = stage(tmp_path)
    html = live / "tools/thf_eli10.html"
    html.write_bytes(html.read_bytes() + b"\n")
    pin = tmp_path / "bad.json"
    recorded = run("--root", str(live), "--staging", "--record-capture", "--out", str(pin))
    assert recorded.returncode == 2
    assert json.loads(pin.read_text(encoding="utf-8"))["applyable"] is False
    other = stage(tmp_path / "anchor")
    (other / "veteran_desk/thf_home.py").write_text("no dashboard hooks\n", encoding="utf-8")
    anchor_pin = tmp_path / "anchor.json"
    failed = run("--root", str(other), "--staging", "--record-capture", "--out", str(anchor_pin))
    assert failed.returncode == 2 and "BASE_OR_DEPTEST_MISMATCH" in failed.stdout
