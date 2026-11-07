#!/usr/bin/env python3
"""Fail-closed release control for the display-mark read model.

This is not a deployment. It does not ssh, start a service, or write a production root.
It does not bind hashes until a sanitized capture is supplied.
It does not accept the withdrawn 2026-11-07 12:12:34 baseline as a reset.
The retail and display-epoch installers keep their own REFUSED_ROOT checks.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BANNED = ("/opt/tfo/live", "/opt/tfo/dash")
REJECTED_BASELINE = "owner-display-20261107T121234Z"
NEEDED = (
    "sanitized capture with sha256 for veteran_desk/thf_home.py, veteran_desk/thf_dash.py, "
    "tools/thf_eli10.html, tools/thf_eli10.js, tools/thf_chat.js, tools/thf_chat_page.html, "
    "named by the operator for each root",
    "source of the reviewed runner used at 19:17 (thf_release_20261107 / c6fab99), if that runner is required",
    "a fresh display baseline taken after the read model is on the dash, not a reused earlier reading",
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_root(path):
    text = str(Path(path).resolve())
    if (Path(path) / ".git").exists():
        return True
    return any(text == banned or text.startswith(banned + "/") for banned in BANNED)


def evaluate(capture):
    """Return a status. Never a permission to deploy."""
    if not isinstance(capture, dict) or not capture.get("sanitized"):
        return "WAITING_CAPTURE"
    if capture.get("baseline_id") == REJECTED_BASELINE or capture.get("reset_now"):
        return "BASELINE_REJECTED"
    files = capture.get("files") or {}
    required = {
        "veteran_desk/thf_home.py", "veteran_desk/thf_dash.py",
        "tools/thf_eli10.html", "tools/thf_eli10.js",
        "tools/thf_chat.js", "tools/thf_chat_page.html",
    }
    if not required <= set(files) or any(not files.get(rel) for rel in required):
        return "WAITING_CAPTURE"
    if not capture.get("runner_sha256"):
        return "TOOL_SOURCE_ABSENT"
    roots = capture.get("roots") or []
    if len(roots) != 2 or any(production_root(item.get("path", "")) for item in roots if isinstance(item, dict) and item.get("path")):
        return "ROOTS_NOT_CONFIRMED"
    return "CAPTURE_ACCEPTED_NOT_DEPLOYED"


def _undo(root, done):
    for kind, rel in reversed(done):
        dest = root / rel
        backup = root / "state/thf/display_release_backup" / rel
        if kind == "replace" and backup.is_file():
            shutil.copy2(backup, dest)
        elif kind == "new" and dest.is_file():
            dest.unlink()


def exercise(root, writes, fail_on=None):
    """Temp-directory exercise used by tests. Production roots are refused. No receipt on a failed write."""
    root = Path(root)
    if production_root(root):
        return {"status": "REFUSED_ROOT", "restart_executed": False}
    receipt_path = root / "state/thf/display_release_receipt.json"
    if receipt_path.is_file():
        prior = json.loads(receipt_path.read_text(encoding="utf-8"))
        if prior.get("singapore_deployed") or prior.get("verified_live"):
            return {"status": "NO_RERUN", "restart_executed": False}
    done = []
    try:
        for index, (rel, content) in enumerate(writes):
            if fail_on is not None and index == fail_on:
                raise OSError("injected write failure")
            dest = root / rel
            if dest.is_file():
                backup = root / "state/thf/display_release_backup" / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
                if sha256(backup) != sha256(dest):
                    raise RuntimeError("backup mismatch")
                dest.write_bytes(content)
                done.append(("replace", rel))
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                done.append(("new", rel))
    except OSError:
        _undo(root, done)
        return {"status": "WRITE_FAILED_UNDONE", "restart_executed": False, "receipt": False}
    receipt = {
        "status": "EXERCISED_NOT_DEPLOYED",
        "singapore_deployed": False,
        "verified_live": False,
        "restart_executed": False,
        "service_if_later_reviewed": "tfo-dash-thf.service",
        "files": {rel: sha256(root / rel) for rel, _content in writes},
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {"status": receipt["status"], "restart_executed": False, "receipt": True}


def rollback(root):
    root = Path(root)
    if production_root(root):
        return False
    receipt_path = root / "state/thf/display_release_receipt.json"
    if not receipt_path.is_file():
        return False
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("singapore_deployed") or receipt.get("verified_live"):
        return False
    for rel, digest in (receipt.get("files") or {}).items():
        dest = root / rel
        backup = root / "state/thf/display_release_backup" / rel
        if not dest.is_file() or sha256(dest) != digest:
            return False
        if backup.is_file():
            shutil.copy2(backup, dest)
        elif dest.is_file():
            dest.unlink()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fail-closed display release control. Not deployment-ready.")
    parser.add_argument("--capture")
    args = parser.parse_args(argv)
    print("NOT_DEPLOYMENT_READY")
    if not args.capture:
        print("WAITING_CAPTURE")
        for item in NEEDED:
            print("NEED " + item)
        return 2
    capture = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    status = evaluate(capture)
    print(status)
    return 0 if status == "CAPTURE_ACCEPTED_NOT_DEPLOYED" else 2


if __name__ == "__main__":
    sys.exit(main())
