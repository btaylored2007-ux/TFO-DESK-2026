#!/usr/bin/env python3
"""Run from an unpacked kit. Uses only files in that kit."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
os.environ["PYTEST_CURRENT_TEST"] = "kit-fixture"
spec = importlib.util.spec_from_file_location("release_control", KIT / "release_control.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
ALLOW = json.loads((KIT / "ALLOWLIST.json").read_text(encoding="utf-8"))
ARCHIVE = KIT / "display-epoch-ui-20261107.tar.gz"
BASE = KIT / "captured_base"
BOOK = KIT / "fixture_support/veteran_desk/book.py"


def fail(message):
    print("FIXTURE_FAIL", message)
    raise SystemExit(1)


def tree(root):
    for path in BASE.rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(BASE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    dest = root / "veteran_desk/book.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BOOK, dest)


def approval(directory):
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
    path = directory / "approval.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def apply(tmp, live, dash, restart=None, restarter=None):
    return release.apply_release(
        approval(tmp),
        {ALLOW["archives"][0]["name"]: ARCHIVE},
        probe=lambda _unit: "inactive",
        host_name="tfo-sgp1",
        roots=(live, dash),
        restart=restart,
        restarter=restarter,
    )


def main():
    if release.file_sha(BOOK) != ALLOW["book_pin"]:
        fail("book pin")
    if release.file_sha(BASE / "veteran_desk/thf_home.py") != "50d8b3dcab66bf154f2ce21433a84f7147ed446193e6c12f74731e15ed26c1ed":
        fail("home pin")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        home = (live / "veteran_desk/thf_home.py").read_bytes()
        book = (live / "veteran_desk/book.py").read_bytes()
        calls = []
        receipt = apply(tmp, live, dash, restart="dash", restarter=calls.append)
        if calls != ["tfo-dash-thf.service"]:
            fail("restart unit %s" % calls)
        if receipt["dash_restarted"] is not True or receipt["tick_restarted"] or receipt["nginx_changed"]:
            fail("restart flags")
        if receipt["mark"] != "PENDING" or receipt["baseline_written"]:
            fail("mark")
        for root in (live, dash):
            if (root / "veteran_desk/thf_home.py").read_bytes() != home:
                fail("home changed")
            if (root / "veteran_desk/book.py").read_bytes() != book:
                fail("book changed")
            if not (root / "veteran_desk/thf_display_epoch.py").is_file():
                fail("module missing")
        try:
            apply(tmp, live, dash)
        except release.ReleaseError as error:
            if error.code != "REFUSED_RERUN":
                fail(error.code)
        else:
            fail("rerun allowed")
        try:
            release.apply_release(approval(tmp), {ALLOW["archives"][0]["name"]: ARCHIVE}, probe=lambda _u: "inactive", host_name="tfo-sgp1", roots=(live, dash), restart="tick")
        except release.ReleaseError as error:
            if error.code != "REFUSED_RESTART":
                fail("tick " + error.code)
        else:
            fail("tick restart allowed")
        if release.rollback_release((live, dash)) is not True:
            fail("rollback")
        if (live / "veteran_desk/thf_display_epoch.py").exists():
            fail("module remained")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != "4414a765d4a19491cff739fdc7cee5dbfeb77403902ad7f62ad3a5895eabe8ba":
            fail("dash not restored")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        calls = []
        real = release._write_receipt
        state = {"n": 0}

        def fail_second(pair, body):
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("receipt disk full")
            return real(pair, body)

        release._write_receipt = fail_second
        try:
            apply(tmp, live, dash, restart="dash", restarter=calls.append)
        except release.ReleaseError as error:
            if error.code != "RECEIPT_AFTER_RESTART_FAILED":
                fail(error.code)
        else:
            fail("receipt failure was ignored")
        finally:
            release._write_receipt = real
        if calls != ["tfo-dash-thf.service"]:
            fail("restart during receipt failure")
        target = ALLOW["archives"][0]["files"][0]["target"]
        if release.file_sha(live / "veteran_desk/thf_dash.py") != target:
            fail("files rolled back after restart")
        saved = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("dash_restarted") is not False or saved.get("deployed") is not True:
            fail("receipt lied about restart")
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
