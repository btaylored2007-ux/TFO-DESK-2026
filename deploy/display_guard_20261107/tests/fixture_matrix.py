#!/usr/bin/env python3
"""Failure injection for the display guard. Does not touch /opt/tfo."""
import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
os.environ["PYTEST_CURRENT_TEST"] = "display-guard-fixture"
spec = importlib.util.spec_from_file_location("display_guard", PKG / "release_control.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
ALLOW = json.loads((PKG / "ALLOWLIST.json").read_text(encoding="utf-8"))
BASE = PKG / "fixture/captured_base"
ARCHIVE = PKG / "fixture/display-epoch-ui-20261107.tar.gz"
BOOK = b"observed-live-book-not-the-historical-pin\n"


def fail(message):
    print("FIXTURE_FAIL", message)
    raise SystemExit(1)


def tree(root):
    for path in BASE.rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(BASE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    (root / "veteran_desk/book.py").write_bytes(BOOK)
    bridge = root / "veteran_desk/chatgpt_bridge.py"
    bridge.write_text("chatgpt bridge untouched\n", encoding="utf-8")


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
    return release._apply_files(
        approval(tmp),
        {ALLOW["archives"][0]["name"]: ARCHIVE},
        probe=lambda _unit: "inactive",
        host_name="tfo-sgp1",
        roots=(live, dash),
        restart=restart,
        restarter=restarter,
    )


def members():
    blob = ARCHIVE.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {item.name: tar.extractfile(item).read() for item in tar.getmembers() if item.isfile()}


def main():
    if release.sha256(ARCHIVE.read_bytes()) != ALLOW["archives"][0]["sha256"]:
        fail("payload pin")
    payload = members()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        book = (live / "veteran_desk/book.py").read_bytes()
        bridge = (live / "veteran_desk/chatgpt_bridge.py").read_bytes()
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
                fail("interrupted copy was ignored")
        finally:
            release._put = real_put
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("book changed by interrupted copy")
        if (live / "veteran_desk/chatgpt_bridge.py").read_bytes() != bridge:
            fail("chatgpt bridge changed by interrupted copy")
        if release._events(live):
            fail("journal kept after interrupted copy")
        dash_base = ALLOW["archives"][0]["files"][0]["bases"][0]
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("dash not restored after interrupted copy")

        def flip_book(root, rel, data):
            real_put(root, rel, data)
            if rel == "tools/thf_chat_page.html" and "live" in str(root):
                (Path(root) / "veteran_desk/book.py").write_bytes(b"changed during copy\n")

        release._put = flip_book
        try:
            try:
                apply(tmp, live, dash)
            except release.ReleaseError as error:
                if error.code != "REFUSED_BOOK_CHANGED":
                    fail(error.code)
            else:
                fail("book change during copy was accepted")
        finally:
            release._put = real_put
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("ui files kept after book change")
        if (live / "veteran_desk/book.py").read_bytes() != b"changed during copy\n":
            fail("book was rolled back to a stale snapshot")
        (live / "veteran_desk/book.py").write_bytes(book)
        (dash / "veteran_desk/book.py").write_bytes(book)

        calls = []
        receipt = apply(tmp, live, dash, restart="dash", restarter=calls.append)
        if calls != ["tfo-dash-thf.service"] or receipt["dash_restarted"] is not True:
            fail("dash restart")
        if receipt["book_written"] is not False or receipt["book_observed_sha256"] != release.sha256(book):
            fail("book observation")
        if (live / "veteran_desk/book.py").read_bytes() != book or (live / "veteran_desk/chatgpt_bridge.py").read_bytes() != bridge:
            fail("protected file written")
        for item in ALLOW["archives"][0]["files"]:
            if release.file_sha(live / item["path"]) != item["target"]:
                fail("target " + item["path"])
        try:
            apply(tmp, live, dash, restart="dash", restarter=calls.append)
        except release.ReleaseError as error:
            if error.code != "REFUSED_RERUN":
                fail(error.code)
        else:
            fail("repeat apply")
        if calls != ["tfo-dash-thf.service"]:
            fail("repeat apply restarted dash")
        report = release.reconcile((live, dash))
        if any(row["state"] != "RESTART_RECORDED" for row in report):
            fail("reconcile " + str(report))
        for item in ALLOW["archives"][0]["files"]:
            if release.file_sha(live / item["path"]) != item["target"] or release.file_sha(dash / item["path"]) != item["target"]:
                fail("reconcile files drifted")
        restored = release.recover_release((live, dash), "restore-files")
        if restored["book_written"] is not False:
            fail("restore wrote book")
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("book changed by restore")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != dash_base:
            fail("restore missed dash")
        if (live / "veteran_desk/thf_display_epoch.py").exists():
            fail("new module remained")

        apply(tmp, live, dash)
        before = release.recover_release((live, dash), "restore-files")
        if before["action"] != "restore-files" or release.file_sha(dash / "veteran_desk/thf_dash.py") != dash_base:
            fail("restore before restart")
        for root in (live, dash):
            for item in ALLOW["archives"][0]["files"]:
                dest = root / item["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(payload[item["path"]])
            release._journal(root, "files_committed")
            release._journal(root, "restart_failed")
        again = []
        restarted = release.recover_release((live, dash), "restart-dash", restarter=again.append)
        if again != ["tfo-dash-thf.service"] or restarted["book_written"] is not False:
            fail("explicit dash restart")
        if (dash / "veteran_desk/book.py").read_bytes() != book:
            fail("restart changed book")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        (dash / "veteran_desk/book.py").write_bytes(b"other-book\n")
        try:
            apply(tmp, live, dash)
        except release.ReleaseError as error:
            if error.code != "REFUSED_BOOK_DIVERGED":
                fail(error.code)
        else:
            fail("diverged books")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != ALLOW["archives"][0]["files"][0]["bases"][0]:
            fail("diverged books wrote dash")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        book = (live / "veteran_desk/book.py").read_bytes()
        real_receipt = release._write_receipt

        def fail_receipt(pair, body):
            raise OSError("receipt disk full")

        release._write_receipt = fail_receipt
        try:
            try:
                apply(tmp, live, dash, restart="dash", restarter=lambda _unit: None)
            except release.ReleaseError as error:
                if error.code != "RECEIPT_FAILED":
                    fail(error.code)
            else:
                fail("pre-restart receipt failure ignored")
        finally:
            release._write_receipt = real_receipt
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("pre-restart receipt failure changed book")
        if release.file_sha(live / "veteran_desk/thf_dash.py") != ALLOW["archives"][0]["files"][0]["bases"][0]:
            fail("pre-restart receipt failure kept new dash")
        if (live / "veteran_desk/thf_display_epoch.py").exists():
            fail("pre-restart receipt failure kept epoch")
        if release._events(live)[-1] != "restored_before_restart":
            fail("pre-restart journal")
        if any(row["state"] != "RESTORED" for row in release.reconcile((live, dash))):
            fail("pre-restart reconcile")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        book = (live / "veteran_desk/book.py").read_bytes()
        calls = []
        real_receipt = release._write_receipt
        state = {"n": 0}

        def fail_after(pair, body):
            state["n"] += 1
            if state["n"] >= 2:
                raise OSError("receipt disk full")
            return real_receipt(pair, body)

        release._write_receipt = fail_after
        try:
            try:
                apply(tmp, live, dash, restart="dash", restarter=calls.append)
            except release.ReleaseError as error:
                if error.code != "RECEIPT_AFTER_RESTART_FAILED":
                    fail(error.code)
            else:
                fail("post-restart receipt failure ignored")
        finally:
            release._write_receipt = real_receipt
        saved = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("dash_restarted") is False or saved.get("restart_outcome") == "RECORDED":
            fail("stale receipt claimed a recorded restart or false")
        if "restart_ok" not in release._events(live) or calls != ["tfo-dash-thf.service"]:
            fail("post-restart journal")
        for item in ALLOW["archives"][0]["files"]:
            if release.file_sha(live / item["path"]) != item["target"]:
                fail("post-restart files")
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("post-restart book")
        if any(row["state"] != "RESTART_JOURNAL_OK_RECEIPT_STALE" for row in release.reconcile((live, dash))):
            fail("stale reconcile")
        try:
            apply(tmp, live, dash, restart="dash", restarter=calls.append)
        except release.ReleaseError as error:
            if error.code != "REFUSED_RERUN":
                fail(error.code)
        else:
            fail("apply after stale receipt")
        if calls != ["tfo-dash-thf.service"]:
            fail("stale receipt restarted again")
        noted = release.recover_release((live, dash), "note-stale-receipt")
        if noted["dash_restarted"] is not None or calls != ["tfo-dash-thf.service"]:
            fail("note restarted or stored false")
        saved = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
        if saved.get("restart_outcome") != "UNKNOWN" or saved.get("dash_restarted") is not None or saved.get("journal_restart_ok") is not True:
            fail("noted receipt")
        if any(row["state"] != "RESTART_JOURNAL_OK_RECEIPT_UNKNOWN" for row in release.reconcile((live, dash))):
            fail("unknown reconcile")
        for item in ALLOW["archives"][0]["files"]:
            if release.file_sha(dash / item["path"]) != item["target"]:
                fail("note changed files")
        if (dash / "veteran_desk/book.py").read_bytes() != book:
            fail("note changed book")
    if release.operator_apply_allowed():
        fail("readiness flags allow apply")
    if ARCHIVE.stat().st_size != 49168 or release.sha256(ARCHIVE.read_bytes()) != "9d91e5fc5c5f84c4b98832389f5cba2be2a4daa1ccff4715677e01c7c1699abb":
        fail("bound payload identity")
    if "c7102844b6522a68d87b5ad3cbbb790e062728029ee97f8bdccc047a6c7d7e76" not in ALLOW["archives"][0]["rejected_sha256"]:
        fail("unbound archive not rejected")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        before = (live / "veteran_desk/thf_dash.py").read_bytes()
        try:
            release.apply_release(approval(tmp), {ALLOW["archives"][0]["name"]: ARCHIVE}, probe=lambda _u: "inactive", host_name="tfo-sgp1", roots=(live, dash))
        except release.ReleaseError as error:
            if error.code != "REFUSED_NOT_READY":
                fail(error.code)
        else:
            fail("direct apply_release wrote while not ready")
        if (live / "veteran_desk/thf_dash.py").read_bytes() != before:
            fail("direct apply_release changed a file")
        try:
            release._apply_files(approval(tmp), {ALLOW["archives"][0]["name"]: ARCHIVE}, probe=lambda _u: "inactive", host_name="tfo-sgp1", roots=("/opt/tfo/live/thf", "/opt/tfo/dash/base"))
        except release.ReleaseError as error:
            if error.code != "REFUSED_NOT_READY":
                fail("prod " + error.code)
        else:
            fail("exercise path accepted production roots")
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
