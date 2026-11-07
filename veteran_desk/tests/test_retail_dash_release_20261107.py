"""Guards for the narrow retail dashboard package. These do not verify Singapore."""
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/retail_dash_20261107"
INSTALL = PACKAGE / "install.py"
MANIFEST = json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8"))
STATUS = json.loads((PACKAGE / "STATUS.json").read_text(encoding="utf-8"))
BASE = "2c45d1a28a894578ca015f06ea7065c2d22f9c6a"
DEPLOYED = "39dcb48"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
UI_DELTA = (
    "tools/thf_eli10.html",
    "tools/thf_eli10.js",
    "tools/thf_chat.js",
    "tools/thf_chat_page.html",
)


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
    for rel in [item["path"] for item in MANIFEST["replace"]]:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git_bytes(rel))
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_bytes((ROOT / "veteran_desk/book.py").read_bytes())
    for item in MANIFEST["identical_or_absent"] + MANIFEST["unchanged"]:
        dest = tmp_path / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        specimen = PACKAGE / "captured_unchanged" / item["path"]
        src = specimen if specimen.is_file() else ROOT / item["path"]
        dest.write_bytes(src.read_bytes())
    return tmp_path


def test_package_is_waiting_and_does_not_claim_live_acceptance():
    assert STATUS["phase"] == "captured" and STATUS["status"] == "STAGED_PACKAGE_NOT_DEPLOYED"
    assert STATUS["singapore_deployed"] is False and STATUS["verified_live"] is False
    assert STATUS["real_device_gui"] == "not verified"
    assert STATUS["restart"] == "not performed"
    assert "not served" in STATUS["acceptance"]["live_pnl_clock"]
    assert STATUS["acceptance"]["singapore_forward_test"] == "not run"
    assert STATUS["acceptance"]["real_device_gui"] == "not verified"
    assert MANIFEST["capture"]["archive_sha256"] == "08501bb2c227db294a9a1e87696426f662593e14e194daeb10afabb0ed09de93"
    assert MANIFEST["capture"]["captured_wita"] == "2026-11-07 07:48:37 WITA"
    frozen = {
        "deploy/lesson_caller_20261107/install.py": "4b16ca6d85ec118f9ce9173b14a9e734492030fe1fc0e1265eadb4dfc6e1c3cb",
        "deploy/lesson_final_20261107/install.py": "c51cfa6ee747e8f32d2f6f8106d075f5ab3c5f6f7d0ac1cf5b5851ddef6943eb",
        "deploy/crypto_prepump_20261107/install.py": "96b6779cd2dfa4230f0e8e15e31beb168a400b8ac43db3e47c2f2837846bafe3",
    }
    for rel, digest in frozen.items():
        assert sha256(ROOT / rel) == digest
    text = INSTALL.read_text(encoding="utf-8")
    assert "systemctl" not in text and "ssh " not in text
    names = [item["path"] for item in MANIFEST["replace"] + MANIFEST["new_files"] + MANIFEST["identical_or_absent"]]
    assert "veteran_desk/book.py" not in names
    assert "veteran_desk/desk.py" not in names
    for item in MANIFEST["replace"]:
        assert sha256(ROOT / item["path"]) == item["target_sha256"]
        assert sha256(PACKAGE / "payload" / item["path"]) == item["target_sha256"]
        assert hashlib.sha256(git_bytes(item["path"])).hexdigest() == item["base_sha256"]
    for item in MANIFEST["unchanged"]:
        specimen = PACKAGE / "captured_unchanged" / item["path"]
        assert sha256(specimen) == item["sha256"]
    nginx = ROOT / "nginx_safe/tfo-dash.conf.excerpt"
    nginx_text = nginx.read_text(encoding="utf-8")
    assert sha256(nginx) == "845f963f0bc23044ec617ddb5cf3cdd223c00a88b3f216b96e9ddfe5feb45c34"
    assert "limit_except GET HEAD { deny all; }" in nginx_text
    assert "location = /api/chat_img" in nginx_text
    assert "/api/chat|" not in nginx_text and "desk/chat" not in nginx_text
    deployed = MANIFEST["deployed_0832"]
    assert deployed["commit"] == DEPLOYED
    assert deployed["replace_when_tree_matches_deployed"] == list(UI_DELTA)
    for rel in UI_DELTA:
        item = next(row for row in MANIFEST["replace"] if row["path"] == rel)
        blob = subprocess.check_output(["git", "show", "%s:%s" % (DEPLOYED, rel)], cwd=ROOT)
        assert hashlib.sha256(blob).hexdigest() == item["also_base_sha256"][0]
        assert item["target_sha256"] != item["also_base_sha256"][0]
    for rel in deployed["already_current"]:
        item = next(row for row in MANIFEST["replace"] if row["path"] == rel)
        blob = subprocess.check_output(["git", "show", "%s:%s" % (DEPLOYED, rel)], cwd=ROOT)
        assert hashlib.sha256(blob).hexdigest() == item["target_sha256"]


def test_checksum_file_matches_the_package_bytes():
    listing = (PACKAGE / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
    assert len(listing) >= 10
    seen = set()
    for line in listing:
        digest, rel = line.split("  ", 1)
        seen.add(rel)
        assert sha256(PACKAGE / rel) == digest
    assert {"install.py", "MANIFEST.json", "ROLLBACK.txt", "STATUS.json", "payload/tools/thf_eli10.html"} <= seen
    assert "CHECKSUMS.sha256" not in seen
    archive = ROOT / "deploy/retail-dash-20261107.tar.gz"
    recorded = (archive.parent / (archive.name + ".sha256")).read_text(encoding="utf-8").split()[0]
    assert sha256(archive) == recorded
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert "deploy/retail_dash_20261107/CHECKSUMS.sha256" in names
    assert "deploy/retail_dash_20261107/install.py" in names
    assert not any(name.endswith(".pyc") for name in names)


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
    dash = run("--root", "/opt/tfo/dash/base")
    assert dash.returncode == 2 and "REFUSED_ROOT" in dash.stdout
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
    assert sha256(live / "veteran_desk/thf_home.py") == MANIFEST["unchanged"][0]["sha256"]
    assert sha256(live / "tools/thf_chat.js") == MANIFEST["replace"][2]["target_sha256"]
    subprocess.check_call([sys.executable, "-m", "py_compile", str(live / "veteran_desk/thf_dash.py"), str(live / "veteran_desk/thf_chat_home.py")])
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


def test_0832_baseline_replaces_only_the_four_ui_files(tmp_path):
    live = stage(tmp_path)
    for item in MANIFEST["replace"] + MANIFEST["identical_or_absent"] + MANIFEST["new_files"]:
        dest = live / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", "%s:%s" % (DEPLOYED, item["path"])], cwd=ROOT))
    pin = tmp_path / "pin0832.json"
    recorded = run("--root", str(live), "--staging", "--record-capture", "--out", str(pin))
    assert recorded.returncode == 0, recorded.stdout
    assert "PINNED" in recorded.stdout and "NOT_DEPLOYED" in recorded.stdout
    dry = run("--root", str(live), "--pin", str(pin))
    assert dry.returncode == 0, dry.stdout
    for rel in UI_DELTA:
        assert "REPLACE %s" % rel in dry.stdout
    for rel in MANIFEST["deployed_0832"]["already_current"]:
        assert "CURRENT %s" % rel in dry.stdout
        assert "REPLACE %s" % rel not in dry.stdout
    assert "INSTALL_NEW assets/brand/apple-touch-icon.png" not in dry.stdout
    applied = run("--root", str(live), "--pin", str(pin), "--staging", "--apply")
    assert applied.returncode == 0, applied.stdout
    assert "STAGED" in applied.stdout and "NOT_DEPLOYED" in applied.stdout
    receipt = json.loads((live / "state/thf/retail_dash_20261107_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "APPLIED_NOT_DEPLOYED"
    assert receipt["replaced"] == list(UI_DELTA)
    assert receipt["installed_new"] == []
    for rel in UI_DELTA:
        item = next(row for row in MANIFEST["replace"] if row["path"] == rel)
        assert receipt["before"][rel] == item["also_base_sha256"][0]
        assert receipt["after"][rel] == item["target_sha256"]
        assert sha256(live / rel) == item["target_sha256"]
    for rel in MANIFEST["deployed_0832"]["already_current"]:
        item = next(row for row in MANIFEST["replace"] if row["path"] == rel)
        assert sha256(live / rel) == item["target_sha256"]
    assert sha256(live / "veteran_desk/book.py") == BOOK
    assert sha256(live / "veteran_desk/thf_home.py") == MANIFEST["unchanged"][0]["sha256"]
    rolled = run("--root", str(live), "--staging", "--rollback")
    assert rolled.returncode == 0, rolled.stdout
    note = json.loads((live / "state/thf/retail_dash_20261107_rollback.json").read_text(encoding="utf-8"))
    assert note["status"] == "ROLLED_BACK_NOT_DEPLOYED" and note["verified_live"] is False
    for rel in UI_DELTA:
        item = next(row for row in MANIFEST["replace"] if row["path"] == rel)
        assert note["after"][rel] == item["also_base_sha256"][0]
        assert sha256(live / rel) == item["also_base_sha256"][0]
    assert sha256(live / "veteran_desk/book.py") == BOOK


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


def test_attached_capture_matches_the_pinned_bases():
    tar_path = Path("/home/ubuntu/.cursor/projects/agent-repos-TFO-DESK-2026/uploads/thf-fast-dashboard-rollout-20261107.tar_0d3f.gz")
    if not tar_path.is_file():
        pytest.skip("capture archive is not in this workspace")
    assert sha256(tar_path) == MANIFEST["capture"]["archive_sha256"]
    wanted = {item["path"]: item["base_sha256"] for item in MANIFEST["replace"]}
    wanted.update({item["path"]: item["sha256"] for item in MANIFEST["unchanged"]})
    with tarfile.open(tar_path, "r:gz") as archive:
        for rel, digest in wanted.items():
            member = archive.extractfile("capture/roots/dash_base/" + rel)
            assert member is not None
            assert hashlib.sha256(member.read()).hexdigest() == digest
            live = archive.extractfile("capture/roots/live_thf/" + rel)
            assert live is not None
            assert hashlib.sha256(live.read()).hexdigest() == digest


def test_partial_copy_failure_restores_the_captured_tree(tmp_path):
    import importlib.util
    live = stage(tmp_path)
    pin_path = tmp_path / "pin.json"
    recorded = run("--root", str(live), "--staging", "--record-capture", "--out", str(pin_path))
    assert recorded.returncode == 0, recorded.stdout
    spec = importlib.util.spec_from_file_location("retail_dash_install", INSTALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    rows = module.classify(live)
    calls = {"n": 0}
    real = module.shutil.copy2

    def fail_on_third(src, dest, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected copy failure")
        return real(src, dest, *args, **kwargs)

    module.shutil.copy2 = fail_on_third
    assert module.apply_plan(live, rows, pin) is False
    assert not (live / "state/thf/retail_dash_20261107_receipt.json").exists()
    for item in MANIFEST["replace"]:
        assert sha256(live / item["path"]) == item["base_sha256"]
    assert not (live / "assets/brand/apple-touch-icon.png").exists()
    assert sha256(live / "veteran_desk/thf_home.py") == MANIFEST["unchanged"][0]["sha256"]
    assert sha256(live / "veteran_desk/book.py") == BOOK
