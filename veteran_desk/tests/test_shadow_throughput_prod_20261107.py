"""The production controller stays unsigned and restores both roots from sealed originals."""
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from deploy.shadow_throughput_20261107 import install as staging
from deploy.shadow_throughput_prod_20261107 import release

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "shadow_throughput_prod_20261107"
PACKAGE = (PKG / "payload.tar.gz").read_bytes()
SAMPLER = "veteran_desk/thf_shadow_sampler.py"
BENCH = "veteran_desk/thf_shadow_bench.py"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _roots(tmp_path):
    roots = []
    for name in ("live", "dash"):
        root = tmp_path / name
        for rel in (SAMPLER, BENCH):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / "deploy" / "shadow_throughput_20261107" / "base" / rel, dest)
            dest.chmod(0o640)
        roots.append(root)
    return roots


def _approval(tmp_path):
    pre = {"status": "captured", "files": release.ALLOW["files"], "protected": release.ALLOW["protected"]}
    raw = json.dumps(pre, sort_keys=True).encode()
    body = json.loads((PKG / "APPROVAL.unsigned.json").read_text())
    body.pop("example")
    body["ready"] = True
    body["signed"] = True
    body["authorization_id"] = "drill-shadow-throughput"
    body["authorization_verbatim"] = "drill record for the recovery tests"
    body["preflight"] = {"status": "captured", "sha256": release.ALLOW["preflight_sha256"]}
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(body))
    return path


def _code(roots):
    found = {}
    for root in roots:
        for rel in (SAMPLER, BENCH):
            path = root / rel
            found[(root.name, rel)] = (_sha(path), path.stat().st_mode & 0o777)
    return found


def test_unsigned_template_and_reused_ids_do_not_write(tmp_path):
    roots = _roots(tmp_path)
    before = _code(roots)
    assert release.main(["--approval", str(PKG / "APPROVAL.unsigned.json"), "--package", str(PKG / "payload.tar.gz")]) == 2
    assert _code(roots) == before
    approval = _approval(tmp_path)
    body = json.loads(approval.read_text())
    body["authorization_id"] = "t2809u"
    approval.write_text(json.dumps(body))
    with pytest.raises(release.ReleaseError, match="REFUSED_REUSED_APPROVAL"):
        release.apply_bound(approval, PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _code(roots) == before
    assert "systemctl" not in (PKG / "release.py").read_text()


def test_drill_seals_backups_before_writes_and_is_not_production(tmp_path, monkeypatch):
    roots = _roots(tmp_path)
    order = []
    real_seal = release._seal
    real_replace = release._replace

    def seal(root, rel, data, mode, txn):
        order.append(("seal", rel))
        return real_seal(root, rel, data, mode, txn)

    def replace(dest, data, mode):
        order.append(("write", dest.name))
        assert order[0][0] == "seal"
        assert sum(1 for kind, _name in order if kind == "seal") == 4
        return real_replace(dest, data, mode)

    monkeypatch.setattr(release, "_seal", seal)
    monkeypatch.setattr(release, "_replace", replace)
    receipt = release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert receipt["production"] is False and receipt["deployed"] is False and receipt["review_only"] is True
    assert receipt["explicit_service_action"] is False
    assert receipt["dash_thf_restart_possible_via_code_md5_watcher"] is True
    assert receipt["loaded_sha_proven"] is False
    assert receipt["targets_match"] is True and receipt["backups_verified"] is True
    assert receipt["preflight_sha256"] == "7bcfc0f3ba1af32374a41acd391212563f0315c74f757dd5de5f54c65b9ea4bd"
    assert _sha(roots[0] / SAMPLER).startswith("d10f5cb6")
    assert (roots[0] / SAMPLER).stat().st_mode & 0o777 == 0o640
    backup = roots[0] / release.BACKUP / receipt["txn"] / "original" / SAMPLER
    assert _sha(backup).startswith("7e2eeb3e")
    assert order[0][0] == "seal" and "write" not in [kind for kind, _name in order[:4]]


def test_second_root_and_receipt_failures_restore_both_roots(tmp_path, monkeypatch):
    roots = _roots(tmp_path)
    before = _code(roots)
    real = release._replace
    count = {"n": 0}

    def boom(dest, data, mode):
        count["n"] += 1
        if count["n"] == 3:
            dest.write_bytes(b"PARTIAL")
            raise release.ReleaseError("REFUSED_WRITE")
        return real(dest, data, mode)

    monkeypatch.setattr(release, "_replace", boom)
    with pytest.raises(release.ReleaseError, match="REFUSED_WRITE"):
        release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _code(roots) == before
    assert not (roots[0] / release.RECEIPT).exists()
    monkeypatch.setattr(release, "_replace", real)

    def late(path, text):
        if str(path).startswith(str(roots[1])):
            raise OSError("receipt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    monkeypatch.setattr(release, "_publish_receipt", late)
    with pytest.raises(OSError, match="receipt"):
        release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _code(roots) == before
    assert not (roots[0] / release.RECEIPT).exists()
    assert not (roots[1] / release.RECEIPT).exists()


def test_a_bad_backup_is_not_copied_and_rollback_is_not_claimed(tmp_path):
    roots = _roots(tmp_path)
    receipt = release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    target = _sha(roots[1] / BENCH)
    backup = roots[1] / release.BACKUP / receipt["txn"] / "original" / BENCH
    os.chmod(backup, 0o644)
    backup.write_bytes(b"invented-from-new")
    with pytest.raises(release.ReleaseError, match="ROLLBACK_FAILED"):
        release.rollback_txn(roots, receipt["txn"])
    assert _sha(roots[0] / SAMPLER).startswith("d10f5cb6")
    assert _sha(roots[1] / BENCH) == target
    assert _sha(roots[1] / SAMPLER).startswith("d10f5cb6")


def test_shadow_lock_and_foreign_bytes_block_before_writes(tmp_path):
    roots = _roots(tmp_path)
    before = _code(roots)
    with pytest.raises(release.ReleaseError, match="REFUSED_SHADOW_ACTIVE"):
        release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "active", drill=True)
    assert _code(roots) == before
    (roots[0] / SAMPLER).write_bytes(b"foreign")
    with pytest.raises(release.ReleaseError, match="REFUSED_BASE"):
        release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _sha(roots[0] / SAMPLER) == hashlib.sha256(b"foreign").hexdigest()


def test_clock_and_budget_bytes_are_not_rewritten(tmp_path):
    roots = _roots(tmp_path)
    payload = b"clock-bytes"
    config = b"budget-bytes"
    for root in roots:
        window = root / "state/thf/shadow_window.json"
        budget = root / "state/thf/shadow_sampler.json"
        window.parent.mkdir(parents=True, exist_ok=True)
        window.write_bytes(payload)
        budget.write_bytes(config)
    release.apply_bound(_approval(tmp_path), PACKAGE, roots, lambda unit: "inactive", drill=True)
    for root in roots:
        assert (root / "state/thf/shadow_window.json").read_bytes() == payload
        assert (root / "state/thf/shadow_sampler.json").read_bytes() == config


def test_staging_guard_and_third_root_stay_closed():
    assert staging.plan("/opt/tfo/live/thf")[0]["state"] == "REFUSED_ROOT"
    assert staging.plan("/opt/tfo/dash/base")[0]["state"] == "REFUSED_ROOT"
    assert "/opt/tfo/dash/codemerge" in release.ALLOW["not_roots"]
    assert release.ALLOW["rejected_archive_sha256"] == ["3a12ff433c1d0c5fbe8d47c612899225c9dfcd78584162e57539e67e1b98d9c8"]
    stale = b"rejected-archive"
    assert release.sha256(stale) != release.ALLOW["package_sha256"]
    with pytest.raises(release.ReleaseError, match="REFUSED_ARCHIVE"):
        release.check_package(stale)
