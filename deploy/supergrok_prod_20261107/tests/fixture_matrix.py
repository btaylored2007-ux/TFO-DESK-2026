#!/usr/bin/env python3
"""Review the production candidate on temp roots. Does not touch /opt/tfo."""
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ["PYTEST_CURRENT_TEST"] = "supergrok-prod-fixture"
PKG = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("supergrok_prod", PKG / "release_control.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
BASE = PKG / "fixture_base"
PINNED = PKG / "pinned_untouched"
STAGING = PKG.parent / "owner_chat_20261107" / "install.py"


def fail(message):
    print("FIXTURE_FAIL", message)
    raise SystemExit(1)


def tree(root):
    for folder in (BASE, PINNED):
        for path in folder.rglob("*"):
            if path.is_file():
                dest = root / path.relative_to(folder)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    (root / "veteran_desk/book.py").write_bytes(b"book-bytes-stay\n")


def main():
    if not STAGING.is_file():
        fail("staging installer missing")
    staging_spec = importlib.util.spec_from_file_location("owner_chat_install", STAGING)
    staging = importlib.util.module_from_spec(staging_spec)
    staging_spec.loader.exec_module(staging)
    if staging.preflight("/opt/tfo/live/thf") != "REFUSED_ROOT":
        fail("staging guard")
    if staging.preflight("/tmp/review", activate=True) != "REFUSED_ACTIVATION":
        fail("staging activate")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        book = (live / "veteran_desk/book.py").read_bytes()
        chatgpt = (live / "tools/chatgpt_web.py").read_bytes()
        brain = (live / "veteran_desk/seat_brain.py").read_bytes()
        real = release._write
        state = {"n": 0}

        def fail_mid(dest, data):
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("copy interrupted")
            real(dest, data)

        release._write = fail_mid
        try:
            try:
                release.apply_release((live, dash))
            except OSError:
                pass
            else:
                fail("interrupted copy")
        finally:
            release._write = real
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("book changed by interrupted copy")
        if release.sha256(live / "veteran_desk/thf_msgdesk.py") != "9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e":
            fail("msgdesk not restored")
        body = release.apply_release((live, dash))
        if body["connected"] is not False or body["activated"] is not False or body["book_written"] is not False:
            fail("receipt")
        if (live / "veteran_desk/book.py").read_bytes() != book or (live / "tools/chatgpt_web.py").read_bytes() != chatgpt:
            fail("protected")
        if (live / "veteran_desk/seat_brain.py").read_bytes() != brain:
            fail("seat brain")
        try:
            release.apply_release((live, dash))
        except release.ReleaseError as error:
            if error.code != "REFUSED_RERUN":
                fail(error.code)
        else:
            fail("repeat")
        restored = release.restore_files((live, dash))
        if restored["restored"] is not True or (live / "veteran_desk/book.py").read_bytes() != book:
            fail("restore")
        if release.sha256(dash / "veteran_desk/thf_msgdesk.py") != "9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e":
            fail("msgdesk base")
        absent = release.health_status(fetcher=lambda: (_ for _ in ()).throw(OSError("absent")))
        if absent["connected"] is not False or absent["state"] != "unavailable":
            fail("health")
        calls = []
        try:
            release.lifecycle("start-page-worker", runner=calls.append)
        except release.ReleaseError as error:
            if error.code != "REFUSED_CONFIRM":
                fail(error.code)
        else:
            fail("confirm")
        if calls:
            fail("lifecycle without confirm")
        started = release.lifecycle("start-page-worker", confirm="OWNER-PAGE-WORKER", runner=calls.append)
        if started["connected"] is not False or calls != [["systemctl", "start", "tfo-grok-xvfb.service"], ["systemctl", "start", "tfo-grok-web.service"]]:
            fail("units %s" % calls)
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
