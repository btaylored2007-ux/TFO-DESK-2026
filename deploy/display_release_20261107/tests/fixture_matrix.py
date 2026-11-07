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
        try:
            release.rollback_release((live, dash))
        except release.ReleaseError as error:
            if error.code != "REFUSED_ROLLBACK_AFTER_RESTART":
                fail(error.code)
        else:
            fail("rollback after restart")
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
        if saved.get("restart_outcome") != "UNKNOWN" or saved.get("dash_restarted") is not None:
            fail("receipt stayed false after restart")
        report = release.reconcile((live, dash))
        if any(row["state"] != "RESTART_JOURNAL_OK_RECEIPT_STALE" for row in report):
            fail("reconcile " + str(report))
        try:
            apply(tmp, live, dash, restart="dash", restarter=calls.append)
        except release.ReleaseError as error:
            if error.code != "REFUSED_RERUN":
                fail(error.code)
        else:
            fail("apply retried after receipt failure")
        if calls != ["tfo-dash-thf.service"]:
            fail("second restart")
    windows()
    print("FIXTURE_OK")
    return 0


def pins(root):
    found = {}
    for row in ALLOW["verify_only"]:
        found[row["path"]] = (root / row["path"]).read_bytes()
    return found


def same_pins(root, before):
    return pins(root) == before


def base_dash():
    return ALLOW["archives"][0]["files"][0]["bases"][0]


def fresh():
    raw = tempfile.mkdtemp()
    tmp = Path(raw)
    live, dash = tmp / "live", tmp / "dash"
    tree(live)
    tree(dash)
    return tmp, live, dash


def expect(code, fn):
    try:
        fn()
    except release.ReleaseError as error:
        if error.code != code:
            fail(error.code + " wanted " + code)
    else:
        fail("missing " + code)


def windows():
    dash_base = base_dash()
    target = ALLOW["archives"][0]["files"][0]["target"]
    tmp, live, dash = fresh()
    try:
        before = pins(live)
        junk = tmp / "junk.tar.gz"
        junk.write_bytes(b"not an archive")
        expect("REFUSED_ARCHIVE", lambda: release.apply_release(
            approval(tmp), {ALLOW["archives"][0]["name"]: junk},
            probe=lambda _u: "inactive", host_name="tfo-sgp1", roots=(live, dash),
        ))
        if release._events(live) or (live / "state/thf/display_release_20261107_receipt.json").exists():
            fail("journal before writes")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base or not same_pins(live, before):
            fail("protected files changed before writes")
        real_put = release._put
        state = {"n": 0}

        def fail_mid(root, rel, data):
            state["n"] += 1
            if state["n"] == 3:
                raise OSError("copy interrupted")
            return real_put(root, rel, data)

        release._put = fail_mid
        try:
            try:
                apply(tmp, live, dash)
            except OSError as error:
                if "copy interrupted" not in str(error):
                    fail(str(error))
            else:
                fail("mid-write was ignored")
        finally:
            release._put = real_put
        if release._events(live) or release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("mid-write was not restored")
        if not same_pins(live, before) or not same_pins(dash, before):
            fail("protected files changed mid-write")

        real_receipt = release._write_receipt

        def fail_first(pair, body):
            raise OSError("receipt disk full")

        calls = []
        release._write_receipt = fail_first
        try:
            expect("RECEIPT_FAILED", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        finally:
            release._write_receipt = real_receipt
        if calls:
            fail("restart before receipt")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("files stayed after pre-restart receipt failure")
        report = release.reconcile((live, dash))
        if any(row["state"] != "RESTORED" for row in report):
            fail("pre-restart reconcile " + str(report))
        again = apply(tmp, live, dash)
        if again["restart_outcome"] != "NOT_ATTEMPTED" or again["dash_restarted"] is not None:
            fail("reapply after restore")
        expect("REFUSED_RERUN", lambda: apply(tmp, live, dash))
        if release.rollback_release((live, dash)) is not True:
            fail("rollback before restart")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("rollback missed dash")
        if (live / "veteran_desk/thf_display_epoch.py").exists():
            fail("rollback left module")
        if not same_pins(live, before):
            fail("rollback touched pins")
        if any(row["state"] != "RESTORED" for row in release.reconcile((live, dash))):
            fail("rollback reconcile")

        receipt = apply(tmp, live, dash)
        for root in (live, dash):
            (root / "state/thf/display_release_20261107_receipt.json").unlink()
        if any(row["state"] != "FILES_COMMITTED_RESTART_NOT_ATTEMPTED" for row in release.reconcile((live, dash))):
            fail("crash after commit")
        expect("REFUSED_RERUN", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        if calls:
            fail("apply restarted after commit crash")
        if release.rollback_release((live, dash)) is not True:
            fail("rollback without receipt")
        if release.file_sha(dash / "veteran_desk/thf_dash.py") != dash_base:
            fail("backup restore")

        calls = []

        def boom(_unit):
            calls.append("once")
            raise OSError("systemctl failed")

        expect("RESTART_FAILED", lambda: apply(tmp, live, dash, restart="dash", restarter=boom))
        saved = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("dash_restarted") is not None or saved.get("restart_outcome") != "UNKNOWN":
            fail("restart command failure stored false")
        if calls != ["once"]:
            fail("restart retried after command failure")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != target:
            fail("files lost after restart command failure")
        expect("REFUSED_RERUN", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        expect("REFUSED_ROLLBACK_AFTER_RESTART", lambda: release.rollback_release((live, dash)))
        if calls != ["once"]:
            fail("second restart after command failure")
    finally:
        shutil.rmtree(tmp)

    tmp, live, dash = fresh()
    try:
        before = pins(live)
        apply(tmp, live, dash)
        for root in (live, dash):
            with (root / "state/thf/display_release_20261107_journal.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": "restart_begin"}) + "\n")
        report = release.reconcile((live, dash))
        if any(row["state"] != "RESTART_UNKNOWN" for row in report):
            fail("restart_begin reconcile " + str(report))
        journal = (live / "state/thf/display_release_20261107_journal.jsonl").read_bytes()
        receipt_bytes = (live / "state/thf/display_release_20261107_receipt.json").read_bytes()
        release.reconcile((live, dash))
        if (live / "state/thf/display_release_20261107_journal.jsonl").read_bytes() != journal:
            fail("reconcile wrote the journal")
        if (live / "state/thf/display_release_20261107_receipt.json").read_bytes() != receipt_bytes:
            fail("reconcile wrote the receipt")
        calls = []
        expect("REFUSED_RERUN", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        expect("REFUSED_ROLLBACK_AFTER_RESTART", lambda: release.rollback_release((live, dash)))
        if calls or not same_pins(live, before):
            fail("unknown restart changed pins or restarted")
    finally:
        shutil.rmtree(tmp)

    tmp, live, dash = fresh()
    try:
        before = pins(live)
        real_journal = release._journal

        def fail_ok(root, event):
            if event == "restart_ok":
                raise OSError("journal disk full")
            return real_journal(root, event)

        calls = []
        release._journal = fail_ok
        try:
            expect("RECEIPT_AFTER_RESTART_FAILED", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        finally:
            release._journal = real_journal
        saved = json.loads((dash / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("dash_restarted") is False:
            fail("journal failure stored false")
        if any(row["state"] != "RESTART_UNKNOWN" for row in release.reconcile((live, dash))):
            fail("missing restart_ok")
        if calls != ["tfo-dash-thf.service"]:
            fail("journal failure restarted twice")
    finally:
        shutil.rmtree(tmp)

    tmp, live, dash = fresh()
    try:
        before = pins(live)
        real_receipt = release._write_receipt
        state = {"n": 0}

        def fail_after(pair, body):
            state["n"] += 1
            if state["n"] >= 2:
                raise OSError("receipt disk full")
            return real_receipt(pair, body)

        calls = []
        release._write_receipt = fail_after
        try:
            expect("RECEIPT_AFTER_RESTART_FAILED", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        finally:
            release._write_receipt = real_receipt
        saved = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("dash_restarted") is False or saved.get("restart_outcome") == "RECORDED":
            fail("unwritable receipt stored success or false")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != target or not same_pins(live, before):
            fail("protected files after unwritable receipt")
        if any(row["state"] != "RESTART_JOURNAL_OK_RECEIPT_STALE" for row in release.reconcile((live, dash))):
            fail("stale receipt reconcile")
        expect("REFUSED_RERUN", lambda: apply(tmp, live, dash, restart="dash", restarter=calls.append))
        if calls != ["tfo-dash-thf.service"]:
            fail("unwritable receipt restarted twice")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    raise SystemExit(main())
