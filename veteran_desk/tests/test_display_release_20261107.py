"""Fail-closed release control. These tests do not deploy."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("display_release_control", ROOT / "deploy/display_release_20261107/control.py")
control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(control)


def _capture(**extra):
    body = {
        "sanitized": True,
        "runner_sha256": "0" * 64,
        "roots": [{"label": "dash-root"}, {"label": "live-root"}],
        "files": {
            "veteran_desk/thf_home.py": "a" * 64,
            "veteran_desk/thf_dash.py": "b" * 64,
            "tools/thf_eli10.html": "c" * 64,
            "tools/thf_eli10.js": "d" * 64,
            "tools/thf_chat.js": "e" * 64,
            "tools/thf_chat_page.html": "f" * 64,
        },
    }
    body.update(extra)
    return body


def test_capture_is_bound_and_not_applied():
    assert control.evaluate(None) == "BOUND_NOT_APPLIED"
    assert control.evaluate(_capture()) == "BOUND_NOT_APPLIED"
    text = (ROOT / "deploy/display_release_20261107/control.py").read_text(encoding="utf-8")
    assert "systemctl" not in text and "ssh " not in text
    allow = json.loads((ROOT / "deploy/display_release_20261107/ALLOWLIST.json").read_text(encoding="utf-8"))
    assert allow["bound"] is True and allow["applied"] is False
    assert allow["capture_sha256"] == "94c0f2dd4133dac72c506e383f733bb761d15a725364d9484344eab24f8265ec"


def test_withdrawn_baseline_and_production_roots_are_rejected():
    assert control.evaluate(_capture(baseline_id="owner-display-20261107T121234Z")) == "BASELINE_REJECTED"
    assert control.evaluate(_capture(reset_now=True)) == "BASELINE_REJECTED"
    pinned = _capture(roots=[{"label": "a", "path": "/opt/tfo/dash/base"}, {"label": "b", "path": "/tmp/other"}])
    assert control.evaluate(pinned) == "ROOTS_NOT_CONFIRMED"
    assert control.evaluate(_capture()) == "BOUND_NOT_APPLIED"


def test_fault_undoes_and_a_deployed_receipt_does_not_run_again(tmp_path):
    root = tmp_path / "tree"
    original = root / "veteran_desk/thf_dash.py"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"base-dash")
    failed = control.exercise(root, [("veteran_desk/thf_dash.py", b"new-dash"), ("veteran_desk/thf_display_epoch.py", b"new-mod")], fail_on=1)
    assert failed["status"] == "WRITE_FAILED_UNDONE" and failed["receipt"] is False
    assert original.read_bytes() == b"base-dash"
    assert not (root / "veteran_desk/thf_display_epoch.py").exists()
    assert not (root / "state/thf/display_release_receipt.json").exists()
    ok = control.exercise(root, [("veteran_desk/thf_dash.py", b"new-dash"), ("veteran_desk/thf_display_epoch.py", b"new-mod")])
    assert ok["status"] == "EXERCISED_NOT_DEPLOYED" and ok["restart_executed"] is False
    assert (root / "veteran_desk/thf_display_epoch.py").read_bytes() == b"new-mod"
    assert control.rollback(root) is True
    assert original.read_bytes() == b"base-dash"
    assert not (root / "veteran_desk/thf_display_epoch.py").exists()
    receipt = root / "state/thf/display_release_receipt.json"
    receipt.write_text(json.dumps({"singapore_deployed": True, "verified_live": True, "files": {}}), encoding="utf-8")
    assert control.exercise(root, [("veteran_desk/thf_dash.py", b"again")])["status"] == "NO_RERUN"
    assert control.exercise("/opt/tfo/dash/base", [("veteran_desk/thf_dash.py", b"x")])["status"] == "REFUSED_ROOT"
    assert "thf_home.py" not in " ".join(path.name for path in (ROOT / "deploy/display_epoch_20261107/payload").rglob("*"))
