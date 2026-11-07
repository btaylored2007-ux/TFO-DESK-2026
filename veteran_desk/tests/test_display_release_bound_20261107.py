"""Bound display-epoch release against the captured bases. Does not touch a host."""
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "deploy/display_release_20261107"
_spec = importlib.util.spec_from_file_location("display_release_bound", PKG / "release_control.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)
ALLOW = json.loads((PKG / "ALLOWLIST.json").read_text(encoding="utf-8"))
ARCHIVE = PKG / "display-epoch-ui-20261107.tar.gz"
CAPTURE = Path("/home/ubuntu/.cursor/projects/agent-repos-TFO-DESK-2026/uploads/ui-display-overlay-capture-20261107.tar_ea16.gz")


def _approval(tmp):
    body = {
        "scope_verbatim": ALLOW["scope_verbatim"],
        "capture_sha256": ALLOW["capture_sha256"],
        "host": "tfo-sgp1",
        "roots": ALLOW["roots"],
        "excludes": ["veteran_desk/book.py"],
        "no_tick_restart": True,
        "no_ledger_rewrite": True,
        "archives": [{"name": ALLOW["archives"][0]["name"], "sha256": ALLOW["archives"][0]["sha256"]}],
    }
    path = tmp / "approval.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _tree(tmp, name):
    root = tmp / name
    base = PKG / "captured_base"
    for path in base.rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(base)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    book = root / "veteran_desk/book.py"
    book.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "veteran_desk/book.py", book)
    return root


def _apply(tmp, **kwargs):
    return release.apply_release(
        _approval(tmp),
        {ALLOW["archives"][0]["name"]: ARCHIVE},
        probe=lambda _unit: "inactive",
        host_name="tfo-sgp1",
        roots=(tmp / "live", tmp / "dash"),
        **kwargs,
    )


def test_apply_updates_both_roots_and_leaves_home_and_book(tmp_path):
    live, dash = _tree(tmp_path, "live"), _tree(tmp_path, "dash")
    home = release.file_sha(live / "veteran_desk/thf_home.py")
    called = []
    receipt = _apply(tmp_path)
    assert receipt["deployed"] is True and receipt["dash_restarted"] is False
    assert receipt["tick_restarted"] is False and receipt["ledger_rewritten"] is False
    assert receipt["baseline_written"] is False
    assert not (live / "state/thf/display_epoch.json").exists()
    for root in (live, dash):
        assert release.file_sha(root / "veteran_desk/thf_home.py") == home == ALLOW["verify_only"][1]["sha256"]
        assert release.file_sha(root / "veteran_desk/book.py") == ALLOW["book_pin"]
        assert release.file_sha(root / "veteran_desk/thf_dash.py") == ALLOW["archives"][0]["files"][0]["target"]
        assert (root / "veteran_desk/thf_display_epoch.py").is_file()
    try:
        _apply(tmp_path)
    except release.ReleaseError as error:
        assert error.code == "REFUSED_RERUN"
    else:
        raise AssertionError("second apply must refuse")
    assert called == []


def test_failed_write_restores_and_old_archives_are_refused(tmp_path, monkeypatch):
    live, dash = _tree(tmp_path, "live"), _tree(tmp_path, "dash")
    before = (live / "veteran_desk/thf_dash.py").read_bytes()
    calls = {"n": 0}
    real = release._put

    def fail_second(root, rel, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise release.ReleaseError("REFUSED_VERIFY")
        return real(root, rel, data)

    monkeypatch.setattr(release, "_put", fail_second)
    try:
        _apply(tmp_path)
    except release.ReleaseError as error:
        assert error.code == "REFUSED_VERIFY"
    else:
        raise AssertionError("injected failure must stop the apply")
    assert (live / "veteran_desk/thf_dash.py").read_bytes() == before
    assert (dash / "veteran_desk/thf_dash.py").read_bytes() == before
    assert not (live / "state/thf/display_release_20261107_receipt.json").exists()
    if CAPTURE.is_file():
        try:
            release.apply_release(_approval(tmp_path), {ALLOW["archives"][0]["name"]: CAPTURE}, probe=lambda _u: "inactive", host_name="tfo-sgp1", roots=(live, dash))
        except release.ReleaseError as error:
            assert error.code == "REFUSED_OLD_ARCHIVE"
        else:
            raise AssertionError("the capture archive must not be applied")


def test_example_approval_and_spent_authorization_are_refused(tmp_path):
    body = json.loads(_approval(tmp_path).read_text(encoding="utf-8"))
    body["example"] = True
    path = tmp_path / "example.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    try:
        release.load_approval(path)
    except release.ReleaseError as error:
        assert error.code == "REFUSED_EXAMPLE_APPROVAL"
    else:
        raise AssertionError("example approval must be refused")
    body["example"] = False
    body["authorization_id"] = "t2809u"
    path.write_text(json.dumps(body), encoding="utf-8")
    try:
        release.load_approval(path)
    except release.ReleaseError as error:
        assert error.code == "REFUSED_SPENT_AUTHORIZATION"
    else:
        raise AssertionError("the 19:17 authorization must not authorize this payload")
