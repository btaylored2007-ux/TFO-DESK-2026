#!/usr/bin/env python3
"""Production release candidate for the owner-chat and SuperGrok page route.

The staging installer in deploy/owner_chat_20261107 still refuses /opt/tfo and
--activate. This file is the separate path. It writes only its allowlist, on
the two named roots, and it does not start a service unless lifecycle is
asked for by name. This workspace does not run it against a host.
"""
import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
SOURCE = HERE / "payload"
LIVE = "/opt/tfo/live/thf"
DASH = "/opt/tfo/dash/base"
CODEMERGE = "/opt/tfo/dash/codemerge"
BACKUP = Path("state/thf/supergrok_prod_20261107_backup")
RECEIPT = Path("state/thf/supergrok_prod_20261107_receipt.json")
PAGE_UNITS = ("tfo-grok-xvfb.service", "tfo-grok-web.service")
SIGNIN_UNIT = "tfo-grok-signin.service"
FORBIDDEN_UNITS = (
    "tfo-thf-tick.service", "tfo-thf-shadow.service", "tfo-dash-thf.service",
    "nginx.service", "tfo-chatgpt-web.service", "tfo-chatgpt-xvfb.service", "tfo-chatgpt-signin.service",
)


class ReleaseError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _write(dest, data):
    dest.write_bytes(data)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _roots(roots):
    pair = tuple(roots)
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    if not os.environ.get("PYTEST_CURRENT_TEST") and tuple(str(Path(root)) for root in pair) != (LIVE, DASH):
        raise ReleaseError("REFUSED_ROOT")
    return pair


def _classify_one(root):
    rows = []
    for item in MANIFEST["files"]:
        src = SOURCE / item["path"]
        if not src.is_file() or sha256(src) != item["target"]:
            raise ReleaseError("MISSING_PAYLOAD")
        dest = Path(root) / item["path"]
        if item.get("new") and not dest.exists():
            rows.append(("INSTALL_NEW", item))
            continue
        if not dest.is_file():
            raise ReleaseError("REFUSED_BASE")
        current = sha256(dest)
        if current == item["target"]:
            rows.append(("CURRENT", item))
        elif current in item["bases"]:
            rows.append(("REPLACE", item))
        else:
            raise ReleaseError("REFUSED_BASE")
    return rows


def _pins(root):
    found = {}
    book = Path(root) / "veteran_desk/book.py"
    if not book.is_file():
        raise ReleaseError("REFUSED_BOOK")
    found["veteran_desk/book.py"] = book.read_bytes()
    for item in MANIFEST["verify_only"]:
        dest = Path(root) / item["path"]
        if not dest.is_file():
            continue
        if sha256(dest) != item["sha256"]:
            raise ReleaseError("REFUSED_PIN")
        found[item["path"]] = dest.read_bytes()
    return found


def health_status(fetcher=None):
    """Read the local page bridge. An absent port is not a connection."""
    fetch = fetcher or (lambda: urllib.request.urlopen("http://127.0.0.1:18892/health", timeout=2).read())
    try:
        body = json.loads(fetch())
    except Exception:
        return {"connected": False, "state": "unavailable", "note": "127.0.0.1:18892 health is absent"}
    from veteran_desk.supergrok_bridge import classify
    state, note = classify(body if isinstance(body, dict) else {})
    return {"connected": state == "ready", "state": state, "note": note}


def operator_apply_allowed():
    """False flags and a missing accepted approval are a refusal. Editing them is not an approval."""
    return (
        MANIFEST.get("deployment_ready") is True
        and MANIFEST.get("ops_review_ready") is True
        and bool(MANIFEST.get("accepted_approval_sha256"))
    )


def _under_opt_tfo(path):
    text = str(path)
    return text == "/opt/tfo" or text.startswith("/opt/tfo/")


def _exec_fixed(argv):
    """Start one allowlisted unit. No shell and no other command."""
    allowed = (
        ["systemctl", "start", "tfo-grok-xvfb.service"],
        ["systemctl", "start", "tfo-grok-web.service"],
        ["systemctl", "start", "tfo-grok-signin.service"],
    )
    command = list(argv)
    if command not in allowed:
        raise ReleaseError("REFUSED_LIFECYCLE")
    import subprocess
    subprocess.run(command, check=True)
    return command


def lifecycle(action, confirm=None):
    if action == "status":
        return health_status()
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if action not in ("start-page-worker", "signin-window"):
        raise ReleaseError("REFUSED_LIFECYCLE")
    if confirm != "OWNER-PAGE-WORKER":
        raise ReleaseError("REFUSED_CONFIRM")
    units = PAGE_UNITS if action == "start-page-worker" else (SIGNIN_UNIT,)
    if any(unit in FORBIDDEN_UNITS for unit in units):
        raise ReleaseError("REFUSED_LIFECYCLE")
    started = [_exec_fixed(["systemctl", "start", unit]) for unit in units]
    return {"action": action, "units": list(units), "commands": started, "connected": False}


def review_approval(body):
    """Check a proposed approval. This package accepts none."""
    if not isinstance(body, dict):
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("example") is True:
        raise ReleaseError("REFUSED_EXAMPLE_APPROVAL")
    if body.get("authorization_id") in (MANIFEST.get("spent_authorization_ids") or []):
        raise ReleaseError("REFUSED_SPENT_AUTHORIZATION")
    if body.get("host") != "tfo-sgp1" or body.get("roots") != MANIFEST.get("roots"):
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("write_set") != [item["path"] for item in MANIFEST["files"]]:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if body.get("service_scope") != list(PAGE_UNITS):
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    protected = [item["path"] for item in MANIFEST["verify_only"]] + ["veteran_desk/book.py"]
    if sorted(body.get("protected_paths") or []) != sorted(protected):
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    return body


def apply_release(roots, codemerge=None, approval=None):
    """Operator mutation. Refuses while this package is not an accepted approval."""
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    review_approval(approval or {})
    return _mutate(roots, codemerge)


def _mutate(roots, codemerge=None):
    pair = _roots(roots)
    if any(_under_opt_tfo(root) for root in pair) and not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if codemerge is not None and not os.environ.get("PYTEST_CURRENT_TEST") and str(Path(codemerge)) != CODEMERGE:
        raise ReleaseError("REFUSED_ROOT")
    trees = list(pair) + ([codemerge] if codemerge is not None else [])
    plans = [_classify_one(root) for root in trees]
    if any(plan != plans[0] for plan in plans[1:]):
        raise ReleaseError("REFUSED_DIVERGED")
    pins = [_pins(root) for root in trees]
    if any(item != pins[0] for item in pins[1:]):
        raise ReleaseError("REFUSED_BOOK_DIVERGED")
    receipt = Path(pair[0]) / RECEIPT
    if receipt.is_file() and json.loads(receipt.read_text(encoding="utf-8")).get("restored") is not True:
        raise ReleaseError("REFUSED_RERUN")
    written = []
    try:
        for state, item in plans[0]:
            if state == "CURRENT":
                continue
            for root in trees:
                dest = Path(root) / item["path"]
                if dest.is_file():
                    backup = Path(root) / BACKUP / item["path"]
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(dest.read_bytes())
                dest.parent.mkdir(parents=True, exist_ok=True)
                _write(dest, (SOURCE / item["path"]).read_bytes())
                written.append((root, item["path"], state))
                if sha256(dest) != item["target"]:
                    raise ReleaseError("REFUSED_VERIFY")
        if [_pins(root) for root in trees] != pins:
            raise ReleaseError("REFUSED_PIN")
    except Exception:
        for root, rel, state in reversed(written):
            dest = Path(root) / rel
            backup = Path(root) / BACKUP / rel
            if backup.is_file():
                dest.write_bytes(backup.read_bytes())
            elif state == "INSTALL_NEW" and dest.is_file():
                dest.unlink()
        raise
    body = {"release": MANIFEST["release"], "applied": True, "activated": False, "connected": False, "restored": False, "book_written": False}
    for root in trees:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def restore_files(roots, codemerge=None):
    pair = _roots(roots)
    trees = list(pair) + ([codemerge] if codemerge is not None else [])
    before = [_pins(root) for root in trees]
    for root in trees:
        for item in MANIFEST["files"]:
            dest = Path(root) / item["path"]
            backup = Path(root) / BACKUP / item["path"]
            if backup.is_file():
                dest.write_bytes(backup.read_bytes())
            elif item.get("new") and dest.is_file() and sha256(dest) == item["target"]:
                dest.unlink()
    if [_pins(root) for root in trees] != before:
        raise ReleaseError("REFUSED_PIN")
    body = {"release": MANIFEST["release"], "applied": False, "activated": False, "connected": False, "restored": True, "book_written": False}
    for root in trees:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description="SuperGrok production release candidate. Not run by this workspace.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-files", action="store_true")
    parser.add_argument("--lifecycle", choices=("status", "start-page-worker", "signin-window"))
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.lifecycle:
            result = lifecycle(args.lifecycle, confirm=args.confirm)
            print(json.dumps(result))
            return 0
        if args.restore_files:
            restore_files((LIVE, DASH), CODEMERGE if Path(CODEMERGE).exists() else None)
            print("FILES_RESTORED")
            return 0
        if args.apply:
            apply_release((LIVE, DASH), CODEMERGE if Path(CODEMERGE).exists() else None)
            print("APPLIED_NOT_CONNECTED")
            return 0
    except ReleaseError as error:
        print(error.code)
        print("NOT_DEPLOYED")
        return 2
    print("REFUSED_ACTION")
    print("NOT_DEPLOYED")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
