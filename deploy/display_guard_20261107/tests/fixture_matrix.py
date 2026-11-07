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
    return release.apply_release(
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
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
