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
STAGING = PKG / "boundary" / "install.py"


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


def _payload_call_path():
    import importlib.util
    import sys
    import types
    desk = types.ModuleType("veteran_desk")
    desk.__path__ = []
    cooldown = types.ModuleType("veteran_desk.ai_cooldown")
    funder = types.ModuleType("veteran_desk.thf_funder")
    funder.L = "/tmp/not-used"
    funder.WITA = __import__("datetime").timezone.utc
    maintenance = types.ModuleType("veteran_desk.bridge_maintenance")

    class _Any:
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, *args, **kwargs):
            return self
        def __getattr__(self, name):
            return _Any()

    maintenance.Tracker = _Any
    maintenance.Recovery = _Any
    mail = types.ModuleType("veteran_desk.seat_mail")
    delivered = []
    mail.deliver = lambda *args, **kwargs: delivered.append(args)
    mail.report_target = lambda seat: "head_trader"
    mail.targets = lambda seat, body: {}
    mail.IDS = {"crypto", "banks", "head_trader"}
    mail.MAX_HOP = 2
    mail.MAXC = 600
    mail.FROM_RX = __import__("re").compile(r"FROM SEAT (\w+) to (\w+)(?: \[hop (\d)\])?")
    sys.modules["veteran_desk"] = desk
    sys.modules["veteran_desk.ai_cooldown"] = cooldown
    sys.modules["veteran_desk.thf_funder"] = funder
    sys.modules["veteran_desk.bridge_maintenance"] = maintenance
    sys.modules["veteran_desk.seat_mail"] = mail
    spec_md = importlib.util.spec_from_file_location("payload_msgdesk", PKG / "payload/veteran_desk/thf_msgdesk.py")
    payload = importlib.util.module_from_spec(spec_md)
    spec_md.loader.exec_module(payload)
    payload.log = lambda *args, **kwargs: None
    cards = []
    payload.tadd = lambda **kwargs: cards.append(kwargs)
    payload._seat_mail("crypto", "owner question", {"reply": "alpha answer", "report_to_marcus": "alpha answer"}, "alpha answer", root="req-1")
    if not delivered or cards:
        fail("identical report card")
    cards.clear()
    payload._seat_mail("crypto", "owner question", {"reply": "alpha answer", "report_to_marcus": "different formal note"}, "alpha answer", root="req-1")
    if not cards or cards[0].get("kind") != "report" or cards[0].get("text") != "different formal note":
        fail("different report dropped")
    if not payload._speaker_line("FROM SEAT crypto to banks [hop 1]: hello").startswith("A desk colleague"):
        fail("colleague audience")
    spec_cl = importlib.util.spec_from_file_location("payload_clarity", PKG / "payload/veteran_desk/thf_chat_clarity.py")
    clarity = importlib.util.module_from_spec(spec_cl)
    spec_cl.loader.exec_module(clarity)
    original = [
        {"id": "q1", "from": "brett", "to": "ALL", "text": "same words", "kind": "request", "root": "one"},
        {"id": "q2", "from": "brett", "to": "ALL", "text": "same words", "kind": "request", "root": "two"},
    ]
    saved = [dict(row) for row in original]
    view = clarity.present(original)
    if original != saved:
        fail("audit rewritten")
    if [row["event_id"] for row in view["owner"]] != ["q1", "q2"]:
        fail("repeated requests collapsed")


def _approval_body():
    return {
        "example": False,
        "authorization_id": "synthetic-not-an-owner-approval",
        "scope_verbatim": release.MANIFEST["scope_verbatim"],
        "host": "tfo-sgp1",
        "roots": list(release.MANIFEST["roots"]),
        "write_set": [item["path"] for item in release.MANIFEST["files"]],
        "targets": {item["path"]: item["target"] for item in release.MANIFEST["files"]},
        "bases": {item["path"]: item["bases"] for item in release.MANIFEST["files"]},
        "service_scope": ["tfo-grok-xvfb.service", "tfo-grok-web.service"],
        "protected_paths": [item["path"] for item in release.MANIFEST["verify_only"]] + ["veteran_desk/book.py"],
        "approval_nonce": "synthetic-nonce-not-a-user-click",
    }


def _ready_paths():
    import json as _json
    import subprocess
    saved = _json.loads(_json.dumps(release.MANIFEST))
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash = tmp / "live", tmp / "dash"
        tree(live)
        tree(dash)
        before = (live / "veteran_desk/book.py").read_bytes()
        try:
            release.restore_files((live, dash))
        except release.ReleaseError as error:
            if error.code != "REFUSED_RECOVERY":
                fail(error.code)
        else:
            fail("restore before apply")
        if (live / "state/thf/supergrok_prod_20261107_receipt.json").exists():
            fail("restore manufactured a receipt")
        release.MANIFEST["deployment_ready"] = True
        release.MANIFEST["ops_review_ready"] = True
        body = _approval_body()
        raw_approval = _json.dumps(body).encode()
        approval_sha = __import__("hashlib").sha256(raw_approval).hexdigest()
        release.MANIFEST["accepted_approval_sha256"] = approval_sha
        try:
            release.review_approval(dict(body, scope_verbatim="wrong"), approval_sha)
        except release.ReleaseError as error:
            if error.code != "REFUSED_APPROVAL_SCOPE":
                fail(error.code)
        else:
            fail("wrong scope")
        try:
            release.review_approval(dict(body, host="other"), approval_sha)
        except release.ReleaseError as error:
            if error.code != "REFUSED_APPROVAL":
                fail(error.code)
        else:
            fail("wrong host")
        try:
            release.review_approval(body, "0" * 64)
        except release.ReleaseError as error:
            if error.code != "REFUSED_APPROVAL_MISMATCH":
                fail(error.code)
        else:
            fail("hash mismatch")
        try:
            release.apply_release(("/opt/tfo/live/thf", "/opt/tfo/dash/base"), approval=body, approval_sha=approval_sha, allow_production=False)
        except release.ReleaseError as error:
            if error.code != "REFUSED_ROOT":
                fail(error.code)
        else:
            fail("ready flags wrote production")
        applied = release.apply_release((live, dash), approval=body, approval_sha=approval_sha)
        if applied.get("applied") is not True or (live / "veteran_desk/book.py").read_bytes() != before:
            fail("ready apply")
        fresh_live, fresh_dash = tmp / "cli-live", tmp / "cli-dash"
        tree(fresh_live)
        tree(fresh_dash)
        approval_path = tmp / "approval.json"
        approval_path.write_bytes(raw_approval)
        import contextlib
        import io
        silent = io.StringIO()
        with contextlib.redirect_stdout(silent):
            refused_root = release.main(["--apply", "--approval", str(approval_path)], roots=("/opt/tfo/live/thf", "/opt/tfo/dash/base"))
            missing = release.main(["--apply"], roots=(fresh_live, fresh_dash))
            applied_cli = release.main(["--apply", "--approval", str(approval_path)], roots=(fresh_live, fresh_dash))
        if refused_root != 2 or "REFUSED_ROOT" not in silent.getvalue():
            fail("cli production roots")
        if missing != 2 or "REFUSED_APPROVAL" not in silent.getvalue():
            fail("cli missing approval")
        if applied_cli != 0 or "APPLIED_NOT_CONNECTED" not in silent.getvalue():
            fail("cli ready apply")
        if (fresh_live / "veteran_desk/book.py").read_bytes() != before:
            fail("cli apply book")
        calls = []

        def fake_run(argv, check=False):
            calls.append(list(argv))
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                code = 0 if argv[3] == "tfo-grok-xvfb.service" else 3
                return subprocess.CompletedProcess(argv, code)
            if argv == ["systemctl", "start", "tfo-grok-web.service"]:
                raise subprocess.CalledProcessError(1, argv)
            if argv == ["systemctl", "stop", "tfo-grok-xvfb.service"]:
                raise AssertionError("stopped a unit that was already running")
            return subprocess.CompletedProcess(argv, 0)

        real_run = subprocess.run
        subprocess.run = fake_run
        try:
            try:
                release.lifecycle("start-page-worker", confirm="OWNER-PAGE-WORKER", approval=body, approval_sha=approval_sha)
            except release.ReleaseError as error:
                if error.code != "REFUSED_LIFECYCLE":
                    fail(error.code)
            else:
                fail("web failure was ignored")
            if ["systemctl", "stop", "tfo-grok-xvfb.service"] in calls:
                fail("stopped xvfb that was already running")
            if ["systemctl", "start", "nginx.service"] in calls or ["systemctl", "restart", "tfo-grok-web.service"] in calls:
                fail("broadened systemctl")
        finally:
            subprocess.run = real_run
        calls.clear()

        def fake_run_new(argv, check=False):
            calls.append(list(argv))
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return subprocess.CompletedProcess(argv, 3)
            if argv == ["systemctl", "start", "tfo-grok-web.service"]:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0)

        subprocess.run = fake_run_new
        try:
            try:
                release.lifecycle("start-page-worker", confirm="OWNER-PAGE-WORKER", approval=body, approval_sha=approval_sha)
            except release.ReleaseError as error:
                if error.code != "REFUSED_LIFECYCLE":
                    fail(error.code)
            else:
                fail("partial start ignored")
            if calls.count(["systemctl", "stop", "tfo-grok-xvfb.service"]) != 1:
                fail("did not stop only the unit this command started")
            if ["systemctl", "stop", "tfo-grok-web.service"] in calls:
                fail("stopped web without a successful start")
        finally:
            subprocess.run = real_run
            release.MANIFEST.clear()
            release.MANIFEST.update(saved)
        if release.operator_apply_allowed():
            fail("flags left true")
    disk = _json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    if disk.get("deployment_ready") is not False or disk.get("accepted_approval_sha256"):
        fail("shipped manifest was changed")


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
                release._mutate((live, dash))
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
        try:
            release.apply_release((live, dash))
        except release.ReleaseError as error:
            if error.code != "REFUSED_NOT_READY":
                fail(error.code)
        else:
            fail("apply_release while not ready")
        if release.sha256(live / "veteran_desk/thf_msgdesk.py") != "9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e":
            fail("apply_release wrote msgdesk")
        body = release._mutate((live, dash))
        if body["connected"] is not False or body["activated"] is not False or body["book_written"] is not False:
            fail("receipt")
        if (live / "veteran_desk/book.py").read_bytes() != book or (live / "tools/chatgpt_web.py").read_bytes() != chatgpt:
            fail("protected")
        if (live / "veteran_desk/seat_brain.py").read_bytes() != brain:
            fail("seat brain")
        try:
            release._mutate((live, dash))
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
        import subprocess
        started = []

        def fake_run(argv, check):
            started.append(list(argv))
            if check and argv[0] != "systemctl":
                raise RuntimeError("not systemctl")
            return subprocess.CompletedProcess(argv, 0)

        real_run = subprocess.run
        subprocess.run = fake_run
        try:
            try:
                release.lifecycle("start-page-worker", confirm="OWNER-PAGE-WORKER")
            except release.ReleaseError as error:
                if error.code != "REFUSED_NOT_READY":
                    fail(error.code)
            else:
                fail("lifecycle started while not ready")
            if started:
                fail("lifecycle exec while not ready")
            try:
                release._exec_fixed(["systemctl", "start", "nginx.service"])
            except release.ReleaseError as error:
                if error.code != "REFUSED_LIFECYCLE":
                    fail(error.code)
            else:
                fail("nginx start")
            release._exec_fixed(["systemctl", "start", "tfo-grok-xvfb.service"])
            release._exec_fixed(["systemctl", "start", "tfo-grok-web.service"])
            if started != [
                ["systemctl", "start", "tfo-grok-xvfb.service"],
                ["systemctl", "start", "tfo-grok-web.service"],
            ]:
                fail("units %s" % started)
        finally:
            subprocess.run = real_run
        try:
            release.review_approval({"example": True, "authorization_id": "t2809u"})
        except release.ReleaseError as error:
            if error.code != "REFUSED_EXAMPLE_APPROVAL":
                fail(error.code)
        else:
            fail("example approval")
        try:
            release.review_approval({"authorization_id": "t2809u", "host": "tfo-sgp1"})
        except release.ReleaseError as error:
            if error.code != "REFUSED_SPENT_AUTHORIZATION":
                fail(error.code)
        else:
            fail("spent approval")
    _payload_call_path()
    _ready_paths()
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
