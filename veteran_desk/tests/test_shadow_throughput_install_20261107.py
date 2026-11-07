"""The throughput installer refuses production and restores the 7e2e / 957539 bases."""
import hashlib
import json
import shutil
from pathlib import Path

from deploy.shadow_throughput_20261107 import install

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "shadow_throughput_20261107"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stage(tmp_path):
    root = tmp_path / "stage"
    for rel in ("veteran_desk/thf_shadow_sampler.py", "veteran_desk/thf_shadow_bench.py"):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PKG / "base" / rel, dest)
    return root


def test_payloads_match_the_manifest_and_are_not_the_deployed_bases():
    manifest = json.loads((PKG / "MANIFEST.json").read_text())
    assert manifest["write_set"] == ["veteran_desk/thf_shadow_sampler.py", "veteran_desk/thf_shadow_bench.py"]
    assert manifest["max_jobs_per_run"] == 2 and manifest["min_n"] == 20
    for item in manifest["payloads"]:
        assert _sha(PKG / "base" / item["path"]) == item["base_sha256"]
        assert _sha(PKG / "payload" / item["path"]) == item["target_sha256"]
        assert item["base_sha256"] != item["target_sha256"]
        assert _sha(REPO / item["path"]) == item["target_sha256"]


def test_production_roots_are_refused(tmp_path):
    refused = install.main(["--root", "/opt/tfo/live/thf", "--staging", "--apply"])
    assert refused == 2
    assert install.plan("/opt/tfo/dash/base")[0]["state"] == "REFUSED_ROOT"
    git = tmp_path / "repo"
    git.mkdir()
    (git / ".git").mkdir()
    assert install.plan(git)[0]["state"] == "REFUSED_ROOT"


def test_apply_and_rollback_restore_the_pinned_bases(tmp_path):
    root = _stage(tmp_path)
    rows = install.plan(root)
    assert {row["state"] for row in rows} == {"REPLACE"}
    assert install.main(["--root", str(root), "--apply"]) == 2
    assert install.apply_plan(root, rows) is True
    receipt = json.loads((root / "state/thf/shadow_throughput_20261107_receipt.json").read_text())
    assert receipt["production"] is False and receipt["deployed"] is False
    assert receipt["write_set"] == ["veteran_desk/thf_shadow_sampler.py", "veteran_desk/thf_shadow_bench.py"]
    assert _sha(root / "veteran_desk/thf_shadow_sampler.py").startswith("9dabe184")
    assert install.rollback(root) is True
    assert _sha(root / "veteran_desk/thf_shadow_sampler.py") == "7e2eeb3e544814411f7fb2371b50b232746e2edec9a971197c46b319570de2bf"
    assert _sha(root / "veteran_desk/thf_shadow_bench.py") == "957539fa9d52ac1d63e3fb9c7064e0d54aae6af88f687f01b477afaf3795e3fa"


def test_a_failed_second_write_restores_the_first(tmp_path, monkeypatch):
    root = _stage(tmp_path)
    real = shutil.copy2
    def boom(src, dest, *args, **kwargs):
        if str(dest).endswith("thf_shadow_bench.py") and "payload" in str(src):
            raise RuntimeError("injected")
        return real(src, dest, *args, **kwargs)
    monkeypatch.setattr(install.shutil, "copy2", boom)
    assert install.apply_plan(root, install.plan(root)) is False
    assert _sha(root / "veteran_desk/thf_shadow_sampler.py") == "7e2eeb3e544814411f7fb2371b50b232746e2edec9a971197c46b319570de2bf"
    assert _sha(root / "veteran_desk/thf_shadow_bench.py") == "957539fa9d52ac1d63e3fb9c7064e0d54aae6af88f687f01b477afaf3795e3fa"


def test_a_foreign_hash_is_not_replaced(tmp_path):
    root = _stage(tmp_path)
    target = root / "veteran_desk/thf_shadow_sampler.py"
    target.write_text(target.read_text() + "\n# foreign\n")
    assert install.plan(root)[0]["state"] == "BLOCKED"
    assert install.apply_plan(root, install.plan(root)) is False
    assert "foreign" in target.read_text()
