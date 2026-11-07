"""The paper-rule package is separate from the three archives being released."""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "paper_rules_20261107"
PIN = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load():
    spec = importlib.util.spec_from_file_location("paper_rules_install", PKG / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path):
    root = tmp_path / "stage"
    for path in (PKG / "base").rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(PKG / "base")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
    return root


def test_package_matches_its_manifest_and_is_not_in_the_release():
    manifest = json.loads((PKG / "MANIFEST.json").read_text())
    assert manifest["live_book_pin"] == PIN
    assert manifest["excluded_from_release"] == ["empty_prompt_20261107", "review_budget_20261107", "retail_dash_20261107"]
    for item in manifest["payloads"]:
        assert _sha(PKG / "payload" / item["path"]) == item["target_sha256"]
        assert _sha(PKG / "base" / item["path"]) == item["base_sha256"]
    assert _sha(PKG / "base" / "veteran_desk" / "book.py") == PIN
    assert _sha(PKG / "payload" / "veteran_desk" / "book.py") != PIN
    allow = json.loads((REPO / "deploy" / "thf_release_20261107" / "ALLOWLIST.json").read_text())
    assert allow["book_pin"] == PIN and "veteran_desk/book.py" in allow["excluded"]
    names = {row["name"] for row in allow["archives"]}
    assert "paper_rules_20261107" not in names
    assert len(json.loads((PKG / "RESOLUTION.json").read_text())["nodes"]) == 19


def test_staging_apply_rolls_back_and_production_is_refused(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    assert install.main(["--root", str(root)]) == 0
    assert "REPLACE veteran_desk/book.py" in capsys.readouterr().out
    assert install.main(["--root", str(root), "--staging", "--apply"]) == 0
    assert _sha(root / "veteran_desk" / "book.py") != PIN
    receipt = json.loads((root / "state" / "thf" / "paper_rules_20261107_receipt.json").read_text())
    assert receipt["production"] is False and receipt["deployed"] is False
    assert install.main(["--root", str(root), "--staging", "--rollback"]) == 0
    assert _sha(root / "veteran_desk" / "book.py") == PIN
    assert _sha(root / "veteran_desk" / "gate_v2.py") == json.loads((PKG / "MANIFEST.json").read_text())["payloads"][1]["base_sha256"]
    assert install.main(["--root", "/opt/tfo/live/thf", "--staging", "--apply"]) == 2
    assert "REFUSED_ROOT" in capsys.readouterr().out
