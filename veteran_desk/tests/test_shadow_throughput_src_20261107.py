"""Source candidate: wired CLI, spent nonce, typed receipt, and the live refresh order."""
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from deploy.shadow_throughput_src_20261107 import release

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "shadow_throughput_src_20261107"
PACKAGE = (PKG / "payload.tar.gz").read_bytes()
SAMPLER = "veteran_desk/thf_shadow_sampler.py"
BENCH = "veteran_desk/thf_shadow_bench.py"
BASE = REPO / "deploy" / "shadow_throughput_20261107" / "base"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _roots(tmp_path, mode=0o640):
    roots = []
    for name in ("live", "dash"):
        root = tmp_path / name
        for rel in (SAMPLER, BENCH):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((BASE / rel).read_bytes())
            dest.chmod(mode)
        roots.append(root)
    return roots


def _approval(tmp_path, ident="drill-src", production=False):
    body = json.loads((PKG / "APPROVAL.unsigned.json").read_text())
    body.pop("example")
    body["ready"] = True
    body["signed"] = True
    body["production_approval"] = production
    body["authorization_id"] = ident
    body["authorization_verbatim"] = "synthetic drill record, not a user sentence"
    body["scope_ref"] = "t2856u"
    body["provenance"] = {
        "quote_role": "coordinator_interpretation",
        "coordinator_note": "interpretation for the drill, not a new user approval",
        "not_a_new_user_approval": True,
    }
    body["preflight"] = {"status": "captured", "sha256": release.ALLOW["preflight_sha256"]}
    path = tmp_path / ("%s.json" % ident)
    path.write_text(json.dumps(body))
    return path


def _code(roots):
    found = {}
    for root in roots:
        for rel in (SAMPLER, BENCH):
            path = root / rel
            found[(root.name, rel)] = (_sha(path), path.stat().st_mode & 0o777)
    return found


def _receipts(root, ident):
    path = release._receipt_path(root, ident)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def test_shipped_example_does_not_call_apply(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(release, "apply_bound", lambda *args, **kwargs: called.__setitem__("n", 1))
    assert release.main(["--approval", str(PKG / "APPROVAL.unsigned.json"), "--package", str(PKG / "payload.tar.gz")]) == 2
    assert called["n"] == 0
    text = (PKG / "release.py").read_text()
    assert "systemctl start" not in text and "systemctl restart" not in text and "systemctl stop" not in text
    body = text[text.find("def apply_bound"):text.find("def rollback_txn")]
    assert "/opt/tfo/dash/codemerge" not in body


def test_complete_record_wires_the_real_probe_and_this_host_refuses(tmp_path, monkeypatch):
    seen = {}

    def fake(approval, package, roots, probe=None, drill=False):
        seen["probe"] = probe
        seen["drill"] = drill
        return {"production": True, "deployed": True, "txn": "unused"}

    approval = _approval(tmp_path, "wire", production=True)
    monkeypatch.setattr(release, "apply_bound", fake)
    assert release.main(["--approval", str(approval), "--package", str(PKG / "payload.tar.gz")]) == 0
    assert seen["probe"] is release.shadow_idle and seen["drill"] is False
    monkeypatch.undo()
    monkeypatch.setattr(release.socket, "gethostname", lambda: "not-the-host")
    assert release.main(["--approval", str(approval), "--package", str(PKG / "payload.tar.gz")]) == 2


def test_missing_production_approval_does_not_apply(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(release, "apply_bound", lambda *args, **kwargs: called.__setitem__("n", 1))
    approval = _approval(tmp_path, "noprod", production=False)
    assert release.main(["--approval", str(approval), "--package", str(PKG / "payload.tar.gz")]) == 2
    assert called["n"] == 0


def test_reused_scope_and_contract_flip_are_refused(tmp_path):
    approval = _approval(tmp_path, "t2809u")
    with pytest.raises(release.ReleaseError, match="REFUSED_REUSED_APPROVAL"):
        release.load_approval(approval)
    body = json.loads((PKG / "CONTRACT.json").read_text())
    body["ready"] = True
    with pytest.raises(release.ReleaseError, match="REFUSED_CONTRACT"):
        release.validate_contract(body)
    body = json.loads((PKG / "CONTRACT.json").read_text())
    body["not_roots"] = []
    with pytest.raises(release.ReleaseError, match="REFUSED_CONTRACT"):
        release.validate_contract(body)


def test_drill_seals_before_write_and_spends_the_nonce(tmp_path, monkeypatch):
    roots = _roots(tmp_path)
    order = []
    real_seal = release._seal
    real_commit = release._commit_pair

    def seal(root, rel, data, txn):
        order.append(("seal", rel))
        return real_seal(root, rel, data, txn)

    def commit(pairs):
        order.append(("commit", pairs[0][1].name))
        return real_commit(pairs)

    monkeypatch.setattr(release, "_seal", seal)
    monkeypatch.setattr(release, "_commit_pair", commit)
    approval = _approval(tmp_path)
    receipt = release.apply_bound(approval, PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert receipt["production"] is False and receipt["deployed"] is False and receipt["review_only"] is True
    assert receipt["transaction_completed"] is True
    assert receipt["explicit_service_action"] is False and receipt["timer_stopped"] is False
    assert receipt["codemerge_write"] is False and receipt["loaded_sha_proven"] is False
    assert receipt["dash_thf_restart_possible_via_code_md5_watcher"] is True
    assert order[0][0] == "seal" and order[4][0] == "commit"
    assert _sha(roots[0] / SAMPLER).startswith("d10f5cb6")
    assert (roots[0] / SAMPLER).stat().st_mode & 0o777 == 0o640
    before = release._receipt_path(roots[0], "drill-src").read_bytes()
    with pytest.raises(release.ReleaseError, match="REFUSED_RERUN"):
        release.apply_bound(approval, PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert release._receipt_path(roots[0], "drill-src").read_bytes() == before


def test_publish_and_partial_failures_are_typed_and_restore(tmp_path, monkeypatch):
    roots = _roots(tmp_path)
    before = _code(roots)
    real = release._commit_pair
    count = {"n": 0}

    def boom(pairs):
        count["n"] += 1
        if count["n"] == 2:
            pairs[0][1].write_bytes(b"PARTIAL")
            raise release.ReleaseError("REFUSED_WRITE")
        return real(pairs)

    monkeypatch.setattr(release, "_commit_pair", boom)
    with pytest.raises(release.ReleaseError, match="REFUSED_WRITE"):
        release.apply_bound(_approval(tmp_path, "partial"), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _code(roots) == before
    partial = _receipts(roots[0], "partial")
    assert partial["transaction_completed"] is False and partial["production"] is False and partial["rolled_back"] is True

    def late(path, body):
        raise OSError("receipt")

    monkeypatch.setattr(release, "_commit_pair", real)
    monkeypatch.setattr(release, "_write_json", late)
    with pytest.raises(release.ReleaseError, match="REFUSED_RECEIPT"):
        release.apply_bound(_approval(tmp_path, "receipt"), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert _code(roots) == before
    saved = _receipts(roots[0], "receipt")
    assert saved["transaction_completed"] is False and saved["deployed"] is False


def test_bad_backup_is_not_copied(tmp_path):
    roots = _roots(tmp_path)
    receipt = release.apply_bound(_approval(tmp_path, "rollback"), PACKAGE, roots, lambda unit: "inactive", drill=True)
    target = _sha(roots[1] / BENCH)
    backup = roots[1] / release.BACKUP / receipt["txn"] / "original" / BENCH
    os.chmod(backup, 0o644)
    backup.write_bytes(b"invented-from-new")
    with pytest.raises(release.ReleaseError, match="ROLLBACK_FAILED"):
        release.rollback_txn(roots, receipt["txn"])
    assert _sha(roots[1] / BENCH) == target
    assert _sha(roots[0] / SAMPLER).startswith("d10f5cb6")


def test_active_probe_and_post_commit_race_restore(tmp_path):
    roots = _roots(tmp_path)
    before = _code(roots)
    with pytest.raises(release.ReleaseError, match="REFUSED_SHADOW_ACTIVE"):
        release.apply_bound(_approval(tmp_path, "busy"), PACKAGE, roots, lambda unit: "active", drill=True)
    assert _code(roots) == before
    calls = {"n": 0}

    def flip(unit):
        calls["n"] += 1
        return "active" if calls["n"] >= 4 else "inactive"

    with pytest.raises(release.ReleaseError, match="REFUSED_SHADOW_ACTIVE"):
        release.apply_bound(_approval(tmp_path, "race"), PACKAGE, roots, flip, drill=True)
    assert _code(roots) == before
    assert _receipts(roots[0], "race")["transaction_completed"] is False


def test_clock_bytes_and_owner_pin_are_checked(tmp_path):
    roots = _roots(tmp_path)
    payload = b"clock-bytes"
    for root in roots:
        window = root / "state/thf/shadow_window.json"
        window.parent.mkdir(parents=True, exist_ok=True)
        window.write_bytes(payload)
        (root / "state/thf/shadow_sampler.json").write_bytes(b"budget-bytes")
    release.apply_bound(_approval(tmp_path, "clock"), PACKAGE, roots, lambda unit: "inactive", drill=True)
    assert (roots[0] / "state/thf/shadow_window.json").read_bytes() == payload
    with pytest.raises(release.ReleaseError, match="REFUSED_PRESERVE"):
        release._expect_preserved(roots[0])
    with pytest.raises(release.ReleaseError, match="REFUSED_OWNER_TARGET"):
        release.verify_owner_targets(roots[0])


def test_pair_commit_is_sampler_then_bench_with_nothing_between():
    lines = [line.strip() for line in inspect.getsource(release._commit_pair).splitlines() if line.strip()]
    replaces = [line for line in lines if line.startswith("os.replace")]
    assert replaces == ["os.replace(pairs[0][0], pairs[0][1])", "os.replace(pairs[1][0], pairs[1][1])"]
    assert lines.index(replaces[1]) == lines.index(replaces[0]) + 1
    text = (PKG / "payload/veteran_desk/thf_shadow_sampler.py").read_text()
    assert text.index("from veteran_desk import lesson_trace as LT") < text.index("from veteran_desk import thf_shadow_bench as BENCH")
    assert _sha(PKG / "payload/veteran_desk/thf_shadow_sampler.py").startswith("d10f5cb6")
    assert _sha(PKG / "payload/veteran_desk/thf_shadow_bench.py").startswith("96e084dc")


def test_timer_near_refuses_and_far_parses():
    now = 1_000_000_000_000_000
    with pytest.raises(release.ReleaseError, match="REFUSED_TIMER_NEAR"):
        release.timer_is_far("NextElapseUSecRealtime=%s" % (now + 10_000_000), now)
    with pytest.raises(release.ReleaseError, match="REFUSED_TIMER_NEAR"):
        release.timer_is_far("NextElapseUSecRealtime=n/a", now)
    assert release.timer_is_far("NextElapseUSecRealtime=%s" % (now + 181 * 1000000), now) is True


def test_refresh_script_copies_live_before_dash_and_codemerge_is_not_a_root():
    path = REPO / "tools/thf_dash_refresh.sh"
    assert _sha(path) == "530d15d937295d81ab2065e801b47b46dd2a24bcee5a707c72eb15b4881ecf19"
    lines = path.read_text().splitlines()
    assert lines[72].endswith("$LT/ $M/ || exit 1")
    assert lines[73].endswith("$M/ $B/ || exit 1")
    assert "thf_shadow_sampler.py" not in lines[72]
    assert "--exclude='state/'" in lines[72] and "--delete-excluded" in lines[72]
    assert release.ALLOW["roots"] == ["/opt/tfo/live/thf", "/opt/tfo/dash/base"]
    assert release.ALLOW["not_roots"] == ["/opt/tfo/dash/codemerge"]
    live = {"sampler": "new", "bench": "new"}
    merge = {"sampler": "old", "bench": "old"}
    dash = {"sampler": "applied", "bench": "applied"}
    failed = dict(dash)
    merge = dict(live)
    dash = dict(merge)
    assert dash == {"sampler": "new", "bench": "new"}
    assert failed == {"sampler": "applied", "bench": "applied"}
