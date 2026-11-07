#!/usr/bin/env python3
"""Review the production candidate on temp roots. Does not touch /opt/tfo."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
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
PREFLIGHT = "5b1c028dda1bc25df1abc9a27ba896735dfd34c6a23e4037cfd85606b6ca3f87"


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


def roots_under(tmp):
    triple = (tmp / "live", tmp / "dash", tmp / "codemerge")
    for root in triple:
        tree(root)
    return triple


def _payload_call_path():
    import sys
    import types
    desk = types.ModuleType("veteran_desk")
    desk.__path__ = []
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
    sys.modules["veteran_desk.ai_cooldown"] = types.ModuleType("veteran_desk.ai_cooldown")
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
    manifest = release.MANIFEST
    return {
        "example": False,
        "authorization_id": "synthetic-not-an-owner-approval",
        "scope_verbatim": manifest["scope_verbatim"],
        "scope_provenance": manifest["scope_provenance"],
        "scope_verbatim_is_owner_quote": False,
        "owner_quotes": manifest["owner_quotes"],
        "host": "tfo-sgp1",
        "roots": list(manifest["roots"]),
        "preflight_sha256": manifest["preflight_sha256"],
        "write_set": [item["path"] for item in manifest["files"]],
        "targets": {item["path"]: item["target"] for item in manifest["files"]},
        "bases": {item["path"]: item["bases"] for item in manifest["files"]},
        "service_scope": list(release.PAGE_UNITS),
        "signin_unit": release.SIGNIN_UNIT,
        "signin_unit_execstart_is_shell": True,
        "controller_uses_shell": False,
        "controller_adds_sudo": False,
        "protected_paths": [item["path"] for item in manifest["verify_only"]] + ["veteran_desk/book.py"],
        "protected_sha256": release.protected_sha256(),
        "phases": manifest["phases"],
        "runtime": manifest["runtime"],
        "outer_package_hash": release.OUTER_PACKAGE_NOTE,
        "approval_nonce": "synthetic-nonce-not-a-user-click",
    }


def _refuse(label, fn, code):
    try:
        fn()
    except release.ReleaseError as error:
        if error.code != code:
            fail("%s got %s" % (label, error.code))
    else:
        fail(label)


def _install_classify():
    import sys
    bridge = PKG / "payload/veteran_desk/supergrok_bridge.py"
    spec_sg = importlib.util.spec_from_file_location("veteran_desk.supergrok_bridge", bridge)
    module = importlib.util.module_from_spec(spec_sg)
    sys.modules["veteran_desk.supergrok_bridge"] = module
    spec_sg.loader.exec_module(module)


def _sums_match():
    sums = (PKG / "SHA256SUMS").read_text(encoding="utf-8")
    rows = {}
    for line in sums.splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        rows[name] = digest
    for path in sorted(PKG.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "SHA256SUMS":
            continue
        rel = path.relative_to(PKG).as_posix()
        if rel not in rows or rows[rel] != hashlib.sha256(path.read_bytes()).hexdigest():
            fail("checksum %s" % rel)
    if set(rows) != {path.relative_to(PKG).as_posix() for path in PKG.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.name != "SHA256SUMS"}:
        fail("checksum inventory")


def _template_contract():
    template = json.loads((PKG / "APPROVAL_TEMPLATE.json").read_text(encoding="utf-8"))
    if template.get("example") is not True or template.get("approval_nonce") != "" or template.get("authorization_id") != "":
        fail("template is not an unsigned example")
    if "codemerge_if_present" in template or template.get("roots") != list(release.PRODUCTION_ROOTS):
        fail("template roots")
    body = _approval_body()
    for key, value in body.items():
        if key in ("example", "authorization_id", "approval_nonce"):
            continue
        if template.get(key) != value:
            fail("template field %s" % key)
    digest = hashlib.sha256((PKG / "APPROVAL_TEMPLATE.json").read_bytes()).hexdigest()
    if digest not in (PKG / "RUNBOOK.md").read_text(encoding="utf-8"):
        fail("runbook omits template sha")
    _refuse("template", lambda: release.review_approval(template, digest), "REFUSED_EXAMPLE_APPROVAL")
    if release.MANIFEST.get("preflight_sha256") != PREFLIGHT:
        fail("preflight binding")
    quote = release.MANIFEST["owner_quotes"][0]["text"]
    if "\u2019" not in quote or "seperate" not in quote or release.MANIFEST.get("scope_verbatim_is_owner_quote") is not False:
        fail("quote provenance")


def _runner(calls, fail_at=None, active=()):
    def run(argv, check=False):
        calls.append(list(argv))
        if fail_at is not None and list(argv) == fail_at:
            raise subprocess.CalledProcessError(1, argv)
        code = 0 if argv[:3] == ["systemctl", "is-active", "--quiet"] and argv[3] in active else 0
        if argv[:3] == ["systemctl", "is-active", "--quiet"] and argv[3] not in active:
            code = 3
        return subprocess.CompletedProcess(argv, code)
    return run


def main():
    if not STAGING.is_file():
        fail("staging installer missing")
    _sums_match()
    _template_contract()
    staging_spec = importlib.util.spec_from_file_location("owner_chat_install", STAGING)
    staging = importlib.util.module_from_spec(staging_spec)
    staging_spec.loader.exec_module(staging)
    if staging.preflight("/opt/tfo/live/thf") != "REFUSED_ROOT":
        fail("staging guard")
    if staging.preflight("/tmp/review", activate=True) != "REFUSED_ACTIVATION":
        fail("staging activate")
    disk = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    if disk.get("deployment_ready") is not False or disk.get("ops_review_ready") is not False or disk.get("accepted_approval_sha256"):
        fail("shipped manifest")
    if disk.get("roots") != list(release.PRODUCTION_ROOTS) or "codemerge_if_present" in disk:
        fail("shipped roots")
    _refuse("book pin", lambda: release._book_digest_ok("0" * 64, "/opt/tfo/live/thf"), "REFUSED_PIN")
    release._book_digest_ok("0" * 64, "/tmp/synthetic-book")
    release._book_digest_ok(release.BOOK_PIN, "/opt/tfo/dash/base")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash, merge = roots_under(tmp)
        other = tmp / "untouched"
        other.mkdir()
        (other / "marker").write_text("stay", encoding="utf-8")
        book = (live / "veteran_desk/book.py").read_bytes()
        chatgpt = (live / "tools/chatgpt_web.py").read_bytes()
        _refuse("restore before apply", lambda: release.restore_files((live, dash, merge)), "REFUSED_RECOVERY")
        if (live / release.RECEIPT).exists():
            fail("restore manufactured a receipt")
        _refuse("direct mutate", lambda: release._mutate((live, dash, merge)), "REFUSED_APPROVAL")
        _refuse("apply while not ready", lambda: release.apply_release((live, dash, merge)), "REFUSED_NOT_READY")
        if release.sha256(live / "veteran_desk/thf_msgdesk.py") != "9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e":
            fail("mutate wrote while refused")
        saved = json.loads(json.dumps(release.MANIFEST))
        release.MANIFEST["deployment_ready"] = True
        release.MANIFEST["ops_review_ready"] = True
        body = _approval_body()
        raw_approval = json.dumps(body).encode()
        approval_sha = hashlib.sha256(raw_approval).hexdigest()
        release.MANIFEST["accepted_approval_sha256"] = approval_sha
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
                release.apply_release((live, dash, merge), approval=body, approval_sha=approval_sha)
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
        _refuse("direct mutate while ready", lambda: release._mutate((live, dash, merge), body, approval_sha), "REFUSED_APPROVAL")
        applied = release.apply_release((live, dash, merge), approval=body, approval_sha=approval_sha)
        if applied.get("applied") is not True or applied.get("activated") is not False or applied.get("connected") is not False:
            fail("apply receipt")
        if applied.get("chat_services_restarted") is not False or applied.get("pages_started_by_apply") is not False:
            fail("apply claimed activation")
        if (live / "veteran_desk/book.py").read_bytes() != book or (live / "tools/chatgpt_web.py").read_bytes() != chatgpt:
            fail("protected bytes")
        for root in (live, dash, merge):
            if release.sha256(root / "tools/grok_web.py") != "b2bc291525a08e370fbf39071f2352afecf5ea77c5405d8dc38666a4ec369e82":
                fail("grok web target")
            if not (root / "veteran_desk/thf_chat_clarity.py").is_file():
                fail("clarity missing")
        if (other / "marker").read_text(encoding="utf-8") != "stay" or (other / release.RECEIPT).exists():
            fail("fourth tree")
        receipt_before = (live / release.RECEIPT).read_bytes()
        _refuse("repeat apply", lambda: release.apply_release((live, dash, merge), approval=body, approval_sha=approval_sha), "REFUSED_RERUN")
        calls = []
        _refuse(
            "start before targets on a base tree",
            lambda: release.lifecycle(
                "start-page-worker",
                confirm="OWNER-PAGE-WORKER",
                approval=body,
                approval_sha=approval_sha,
                roots=roots_under(tmp / "base-only"),
                runner=_runner(calls),
            ),
            "REFUSED_NOT_APPLIED",
        )
        if calls:
            fail("start before apply called systemctl")
        calls.clear()
        started = release.lifecycle(
            "start-page-worker",
            confirm="OWNER-PAGE-WORKER",
            approval=body,
            approval_sha=approval_sha,
            roots=(live, dash, merge),
            runner=_runner(calls),
        )
        if started.get("connected") is not False or started.get("reply_receipt_written") is not False or started.get("sudo_added") is not False:
            fail("lifecycle claimed connection")
        if calls != [
            ["systemctl", "is-active", "--quiet", "tfo-grok-xvfb.service"],
            ["systemctl", "start", "tfo-grok-xvfb.service"],
            ["systemctl", "is-active", "--quiet", "tfo-grok-web.service"],
            ["systemctl", "start", "tfo-grok-web.service"],
        ]:
            fail("units %s" % calls)
        if (live / release.RECEIPT).read_bytes() != receipt_before:
            fail("lifecycle rewrote the apply receipt")
        if (live / release.REPLY_RECEIPT).exists():
            fail("reply receipt invented")
        journal = (live / release.JOURNAL).read_text(encoding="utf-8")
        if '"connected": false' not in journal or '"receipt_kind": "lifecycle"' not in journal:
            fail("lifecycle journal")
        _refuse("restore without approval", lambda: release.restore_files((live, dash, merge)), "REFUSED_APPROVAL")
        if release.sha256(live / "tools/grok_web.py") != "b2bc291525a08e370fbf39071f2352afecf5ea77c5405d8dc38666a4ec369e82":
            fail("refused restore wrote")
        restored = release.restore_files((live, dash, merge), approval=body, approval_sha=approval_sha)
        if restored.get("restored") is not True or restored.get("connected") is not False:
            fail("restore receipt")
        if release.sha256(dash / "veteran_desk/thf_msgdesk.py") != "9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e":
            fail("msgdesk base")
        if (merge / "veteran_desk/thf_chat_clarity.py").exists():
            fail("clarity left behind")
        if (live / "veteran_desk/book.py").read_bytes() != book:
            fail("restore book")
        _refuse("second restore", lambda: release.restore_files((live, dash, merge), approval=body, approval_sha=approval_sha), "REFUSED_RECOVERY")
        release.MANIFEST.clear()
        release.MANIFEST.update(saved)
        if release.operator_apply_allowed():
            fail("flags left true")
    _payload_call_path()
    _install_classify()
    _ready_paths()
    print("FIXTURE_OK")
    return 0


def _ready_paths():
    saved = json.loads(json.dumps(release.MANIFEST))
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live, dash, merge = roots_under(tmp)
        before = (live / "veteran_desk/book.py").read_bytes()
        try:
            release.MANIFEST["deployment_ready"] = True
            release.MANIFEST["ops_review_ready"] = True
            body = _approval_body()
            raw_approval = json.dumps(body).encode()
            approval_sha = hashlib.sha256(raw_approval).hexdigest()
            release.MANIFEST["accepted_approval_sha256"] = approval_sha
            _refuse("wrong scope", lambda: release.review_approval(dict(body, scope_verbatim="wrong"), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("wrong host", lambda: release.review_approval(dict(body, host="other"), approval_sha), "REFUSED_APPROVAL")
            _refuse("two roots", lambda: release.review_approval(dict(body, roots=body["roots"][:2]), approval_sha), "REFUSED_APPROVAL")
            _refuse("optional codemerge", lambda: release.review_approval(dict(body, codemerge_if_present=release.CODEMERGE), approval_sha), "REFUSED_APPROVAL")
            _refuse("hash mismatch", lambda: release.review_approval(body, "0" * 64), "REFUSED_APPROVAL_MISMATCH")
            bad_pins = dict(body["protected_sha256"])
            bad_pins["veteran_desk/book.py"] = "0" * 64
            _refuse("book pin approval", lambda: release.review_approval(dict(body, protected_sha256=bad_pins), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("signin unit", lambda: release.review_approval(dict(body, signin_unit="tfo-dash-thf.service"), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("signin shell claim", lambda: release.review_approval(dict(body, signin_unit_execstart_is_shell=False), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("sudo claim", lambda: release.review_approval(dict(body, controller_adds_sudo=True), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("msgdesk in page scope", lambda: release.review_approval(dict(body, service_scope=list(body["service_scope"]) + ["tfo-thf-msgdesk.service"]), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("quote edited", lambda: release.review_approval(dict(body, owner_quotes=[]), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse("scope labeled as quote", lambda: release.review_approval(dict(body, scope_verbatim_is_owner_quote=True), approval_sha), "REFUSED_APPROVAL_SCOPE")
            phases = json.loads(json.dumps(body["phases"]))
            phases[1]["performed_by_this_controller"] = True
            _refuse("smuggled refresh", lambda: release.review_approval(dict(body, phases=phases), approval_sha), "REFUSED_APPROVAL_SCOPE")
            _refuse(
                "production roots",
                lambda: release.apply_release(release.PRODUCTION_ROOTS, approval=body, approval_sha=approval_sha, allow_production=False),
                "REFUSED_ROOT",
            )
            approval_path = tmp / "approval.json"
            approval_path.write_bytes(raw_approval)
            silent = io.StringIO()
            with contextlib.redirect_stdout(silent):
                refused_root = release.main(["--apply", "--approval", str(approval_path)], roots=release.PRODUCTION_ROOTS)
                missing = release.main(["--apply"], roots=(live, dash, merge))
                missing_restore = release.main(["--restore-files"])
            text = silent.getvalue()
            if refused_root != 2 or "REFUSED_ROOT" not in text:
                fail("cli production roots")
            if missing != 2 or "REFUSED_APPROVAL" not in text or missing_restore != 2:
                fail("cli missing approval")
            release.MANIFEST["deployment_ready"] = False
            silent = io.StringIO()
            with contextlib.redirect_stdout(silent):
                not_ready = release.main(["--lifecycle", "start-page-worker", "--confirm", "OWNER-PAGE-WORKER", "--approval", str(PKG / "APPROVAL_TEMPLATE.json")])
            release.MANIFEST["deployment_ready"] = True
            release.MANIFEST["ops_review_ready"] = True
            if not_ready != 2 or "REFUSED_NOT_READY" not in silent.getvalue():
                fail("template start while not ready")
            fresh = roots_under(tmp / "cli")
            silent = io.StringIO()
            with contextlib.redirect_stdout(silent):
                applied_cli = release.main(["--apply", "--approval", str(approval_path)], roots=fresh)
            if applied_cli != 0 or "APPLIED_NOT_CONNECTED" not in silent.getvalue():
                fail("cli ready apply")
            if (fresh[0] / "veteran_desk/book.py").read_bytes() != before:
                fail("cli apply book")
            if release.sha256(fresh[2] / "tools/grok_web.py") != "b2bc291525a08e370fbf39071f2352afecf5ea77c5405d8dc38666a4ec369e82":
                fail("cli third root")
            calls = []
            _refuse(
                "web failure after new xvfb",
                lambda: release.lifecycle(
                    "start-page-worker",
                    confirm="OWNER-PAGE-WORKER",
                    approval=body,
                    approval_sha=approval_sha,
                    roots=fresh,
                    runner=_runner(calls, fail_at=["systemctl", "start", "tfo-grok-web.service"]),
                ),
                "REFUSED_LIFECYCLE",
            )
            if calls.count(["systemctl", "stop", "tfo-grok-xvfb.service"]) != 1:
                fail("did not stop only the unit this command started")
            if ["systemctl", "stop", "tfo-grok-web.service"] in calls:
                fail("stopped web without a successful start")
            calls.clear()
            _refuse(
                "preexisting xvfb",
                lambda: release.lifecycle(
                    "start-page-worker",
                    confirm="OWNER-PAGE-WORKER",
                    approval=body,
                    approval_sha=approval_sha,
                    roots=fresh,
                    runner=_runner(calls, fail_at=["systemctl", "start", "tfo-grok-web.service"], active={"tfo-grok-xvfb.service"}),
                ),
                "REFUSED_LIFECYCLE",
            )
            if ["systemctl", "stop", "tfo-grok-xvfb.service"] in calls or ["systemctl", "start", "tfo-grok-xvfb.service"] in calls:
                fail("touched xvfb that was already running")
            for action, unit in (
                ("restart", "tfo-grok-web.service"),
                ("start", "nginx.service"),
                ("start", "tfo-dash-thf.service"),
                ("start", "tfo-thf-msgdesk.service"),
                ("start", "tfo-dash-vd.service"),
                ("start", "tfo-thf-engine.service"),
            ):
                _refuse(unit, lambda action=action, unit=unit: release._service_argv(action, unit), "REFUSED_LIFECYCLE")
            probe = []
            _refuse("direct systemctl", lambda: release._exec_fixed(["systemctl", "start", "tfo-grok-web.service"], runner=_runner(probe)), "REFUSED_LIFECYCLE")
            _refuse("sudo argv", lambda: release._exec_fixed(["sudo", "systemctl", "start", "tfo-grok-web.service"], runner=_runner(probe), armed=True), "REFUSED_LIFECYCLE")
            if probe:
                fail("ungated systemctl")
            absent = release.health_status(fetcher=lambda: (_ for _ in ()).throw(OSError("absent")))
            if absent["connected"] is not False or absent["state"] != "unavailable":
                fail("health")
            ready = release.health_status(fetcher=lambda: b'{"state":"logged_in","composer":"ready"}')
            if ready["connected"] is not False or ready["state"] != "ready" or ready["health_is_connection"] is not False:
                fail("health was treated as connection")
            _refuse("example approval", lambda: release.review_approval({"example": True, "authorization_id": "t2809u"}), "REFUSED_EXAMPLE_APPROVAL")
            _refuse("spent approval", lambda: release.review_approval({"authorization_id": "t2809u", "host": "tfo-sgp1"}), "REFUSED_SPENT_AUTHORIZATION")
        finally:
            release.MANIFEST.clear()
            release.MANIFEST.update(saved)
    _refuse("lifecycle while not ready", lambda: release.lifecycle("start-page-worker", confirm="OWNER-PAGE-WORKER"), "REFUSED_NOT_READY")
    if release.operator_apply_allowed():
        fail("flags left true")
    disk = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    if disk.get("deployment_ready") is not False or disk.get("accepted_approval_sha256"):
        fail("shipped manifest was changed")


if __name__ == "__main__":
    raise SystemExit(main())
