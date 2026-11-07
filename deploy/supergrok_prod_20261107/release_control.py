#!/usr/bin/env python3
"""Production release candidate for the owner-chat and SuperGrok page route.

Writes the eight allowlisted files on the three bound roots and does not start
a service unless lifecycle is asked for by name. Page-worker start is refused
until those files already match their targets. Chat-service restarts are not
part of that allowlist. This workspace does not run it against a host.
"""
import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
SOURCE = HERE / "payload"
LIVE = "/opt/tfo/live/thf"
DASH = "/opt/tfo/dash/base"
CODEMERGE = "/opt/tfo/dash/codemerge"
PRODUCTION_ROOTS = (LIVE, DASH, CODEMERGE)
BOOK_REL = "veteran_desk/book.py"
BOOK_PIN = "a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b"
BACKUP = Path("state/thf/supergrok_prod_20261107_backup")
RECEIPT = Path("state/thf/supergrok_prod_20261107_receipt.json")
JOURNAL = Path("state/thf/supergrok_prod_20261107_lifecycle.jsonl")
REPLY_RECEIPT = Path("state/thf/supergrok_prod_20261107_reply.json")
PAGE_UNITS = ("tfo-grok-xvfb.service", "tfo-grok-web.service")
SIGNIN_UNIT = "tfo-grok-signin.service"
OUTER_PACKAGE_NOTE = "checked separately; do not put the archive sha in this file"
FORBIDDEN_UNITS = (
    "tfo-thf-tick.service",
    "tfo-thf-shadow.service",
    "tfo-thf-engine.service",
    "tfo-thf-msgdesk.service",
    "tfo-dash-thf.service",
    "tfo-dash-vd.service",
    "nginx.service",
    "tfo-chatgpt-web.service",
    "tfo-chatgpt-xvfb.service",
    "tfo-chatgpt-signin.service",
)


class _Gate:
    pass


_APPLY_GATE = _Gate()


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


def protected_sha256():
    """Pins the controller enforces. Book is the module file, not ledger data."""
    if MANIFEST.get("book_pin", {}).get("path") != BOOK_REL or MANIFEST.get("book_pin", {}).get("sha256") != BOOK_PIN:
        raise ReleaseError("REFUSED_PIN")
    pins = {item["path"]: item["sha256"] for item in MANIFEST["verify_only"]}
    pins[BOOK_REL] = BOOK_PIN
    return pins


def _roots(roots):
    if tuple(MANIFEST.get("roots") or ()) != PRODUCTION_ROOTS:
        raise ReleaseError("REFUSED_ROOT")
    triple = tuple(roots)
    if len(triple) != 3 or len({str(Path(root)) for root in triple}) != 3:
        raise ReleaseError("REFUSED_ROOT")
    normalized = tuple(str(Path(root)) for root in triple)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        if any(_under_opt_tfo(root) for root in normalized):
            raise ReleaseError("REFUSED_ROOT")
        return triple
    if normalized != PRODUCTION_ROOTS:
        raise ReleaseError("REFUSED_ROOT")
    return triple


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


def _book_digest_ok(digest, root):
    """Production trees must still be the observed book module. No ledger read."""
    protected_sha256()
    if _under_opt_tfo(root) and digest != BOOK_PIN:
        raise ReleaseError("REFUSED_PIN")


def _pins(root):
    found = {}
    book = Path(root) / BOOK_REL
    if not book.is_file():
        raise ReleaseError("REFUSED_BOOK")
    digest = sha256(book)
    _book_digest_ok(digest, root)
    found[BOOK_REL] = book.read_bytes()
    for item in MANIFEST["verify_only"]:
        dest = Path(root) / item["path"]
        if not dest.is_file():
            continue
        if sha256(dest) != item["sha256"]:
            raise ReleaseError("REFUSED_PIN")
        found[item["path"]] = dest.read_bytes()
    return found


def _require_targets(roots):
    """Page units execute the live file. A stub must not be started."""
    for root in _roots(roots):
        for item in MANIFEST["files"]:
            dest = Path(root) / item["path"]
            if not dest.is_file() or sha256(dest) != item["target"]:
                raise ReleaseError("REFUSED_NOT_APPLIED")
        _pins(root)


def health_status(fetcher=None):
    """Read the local page bridge. Health, including a ready composer, is not a connection."""
    fetch = fetcher or (lambda: urllib.request.urlopen("http://127.0.0.1:18892/health", timeout=2).read())
    try:
        body = json.loads(fetch())
    except Exception:
        return {
            "receipt_kind": "health",
            "connected": False,
            "state": "unavailable",
            "note": "127.0.0.1:18892 health is absent",
            "connection_proof": "none",
            "health_is_connection": False,
        }
    from veteran_desk.supergrok_bridge import classify
    state, note = classify(body if isinstance(body, dict) else {})
    return {
        "receipt_kind": "health",
        "connected": False,
        "state": state,
        "note": note,
        "connection_proof": "none",
        "health_is_connection": False,
    }


def operator_apply_allowed():
    """False flags are a refusal. A nonempty hash field is not, by itself, an approval."""
    return MANIFEST.get("deployment_ready") is True and MANIFEST.get("ops_review_ready") is True


def _under_opt_tfo(path):
    text = str(path)
    return text == "/opt/tfo" or text.startswith("/opt/tfo/")


def _separate_phase_units():
    """Units named as later or out-of-scope work. They are not page-worker commands."""
    names = set(FORBIDDEN_UNITS)
    for phase in MANIFEST.get("phases") or []:
        for row in phase.get("if_parent_authorizes") or []:
            names.add(row["unit"])
        for row in phase.get("leave_alone") or []:
            names.add(row["unit"])
        for name in phase.get("unaffected") or []:
            if str(name).endswith(".service"):
                names.add(name)
    return names


def _service_argv(action, unit):
    if unit in _separate_phase_units() or action not in ("start", "stop", "is-active") or unit not in PAGE_UNITS + (SIGNIN_UNIT,):
        raise ReleaseError("REFUSED_LIFECYCLE")
    if action == "is-active":
        return ["systemctl", "is-active", "--quiet", unit]
    return ["systemctl", action, unit]


def _exec_fixed(argv, runner=None, armed=False):
    """One allowlisted systemctl argv. No shell, no sudo, and no direct call."""
    if armed is not True:
        raise ReleaseError("REFUSED_LIFECYCLE")
    command = list(argv)
    allowed = [_service_argv(action, unit) for unit in PAGE_UNITS + (SIGNIN_UNIT,) for action in ("start", "stop", "is-active")]
    if command not in allowed or command[0] != "systemctl":
        raise ReleaseError("REFUSED_LIFECYCLE")
    if runner is None:
        import subprocess
        runner = subprocess.run
    return runner(command, check=command[1] != "is-active")


def _unit_was_active(unit, runner):
    result = _exec_fixed(_service_argv("is-active", unit), runner=runner, armed=True)
    return result.returncode == 0


def _append_jsonl(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def lifecycle(action, confirm=None, approval=None, approval_sha=None, roots=None, runner=None, allow_production=False):
    if action == "status":
        return health_status()
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if action not in ("start-page-worker", "signin-window"):
        raise ReleaseError("REFUSED_LIFECYCLE")
    if confirm != "OWNER-PAGE-WORKER":
        raise ReleaseError("REFUSED_CONFIRM")
    review_approval(approval, approval_sha)
    if any(unit in _separate_phase_units() for unit in PAGE_UNITS + (SIGNIN_UNIT,)):
        raise ReleaseError("REFUSED_LIFECYCLE")
    triple = _roots(PRODUCTION_ROOTS if roots is None else roots)
    production = tuple(str(Path(root)) for root in triple) == PRODUCTION_ROOTS
    if production and not allow_production:
        raise ReleaseError("REFUSED_ROOT")
    if production and runner is not None:
        raise ReleaseError("REFUSED_LIFECYCLE")
    if not production and runner is None:
        raise ReleaseError("REFUSED_LIFECYCLE")
    _require_targets(triple)
    units = PAGE_UNITS if action == "start-page-worker" else (SIGNIN_UNIT,)
    prior = {}
    started = []
    stopped = []
    error = None
    try:
        for unit in units:
            prior[unit] = _unit_was_active(unit, runner)
            if prior[unit]:
                continue
            _exec_fixed(_service_argv("start", unit), runner=runner, armed=True)
            started.append(unit)
    except Exception as exc:
        error = exc
        for unit in reversed(started):
            if not prior.get(unit):
                _exec_fixed(_service_argv("stop", unit), runner=runner, armed=True)
                stopped.append(unit)
    if prior:
        _record_lifecycle(triple[0], action, units, prior, started, stopped, error is None)
    if error is not None:
        if isinstance(error, ReleaseError):
            raise error
        raise ReleaseError("REFUSED_LIFECYCLE") from error
    return _lifecycle_view(action, units, prior, started, stopped, True)


def _lifecycle_view(action, units, prior, started, stopped, ok):
    return {
        "receipt_kind": "lifecycle",
        "action": action,
        "units": list(units),
        "prior_active": {unit: bool(prior.get(unit)) for unit in units},
        "started": list(started),
        "stopped": list(stopped),
        "ok": ok,
        "connected": False,
        "connection_proof": "none",
        "apply_receipt_updated": False,
        "chat_services_restarted": False,
        "sudo_added": False,
        "reply_receipt": str(REPLY_RECEIPT),
        "reply_receipt_written": False,
    }


def _record_lifecycle(root, action, units, prior, started, stopped, ok):
    """Unit truth lives here. The apply receipt is not rewritten and health is not a connection."""
    entry = _lifecycle_view(action, units, prior, started, stopped, ok)
    entry["health_read"] = False
    entry["connection_proof"] = "none"
    _append_jsonl(Path(root) / JOURNAL, entry)


def _same(body, key, expected, code):
    if body.get(key) != expected:
        raise ReleaseError(code)


def _body_contains(value, digest):
    if isinstance(value, str):
        return bool(digest) and value == digest
    if isinstance(value, dict):
        return any(_body_contains(item, digest) for item in value.values())
    if isinstance(value, list):
        return any(_body_contains(item, digest) for item in value)
    return False


def review_approval(body, presented_sha=None):
    """Check a proposed approval. This package's accepted hash is empty, so none match."""
    if not isinstance(body, dict):
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("example") is True:
        raise ReleaseError("REFUSED_EXAMPLE_APPROVAL")
    if body.get("authorization_id") in (MANIFEST.get("spent_authorization_ids") or []):
        raise ReleaseError("REFUSED_SPENT_AUTHORIZATION")
    if "codemerge_if_present" in body:
        raise ReleaseError("REFUSED_APPROVAL")
    _same(body, "scope_verbatim", MANIFEST.get("scope_verbatim"), "REFUSED_APPROVAL_SCOPE")
    _same(body, "scope_provenance", MANIFEST.get("scope_provenance"), "REFUSED_APPROVAL_SCOPE")
    if body.get("scope_verbatim_is_owner_quote") is not False:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    _same(body, "owner_quotes", MANIFEST.get("owner_quotes"), "REFUSED_APPROVAL_SCOPE")
    if body.get("host") != "tfo-sgp1" or body.get("roots") != MANIFEST.get("roots"):
        raise ReleaseError("REFUSED_APPROVAL")
    _same(body, "preflight_sha256", MANIFEST.get("preflight_sha256"), "REFUSED_APPROVAL_SCOPE")
    _same(body, "write_set", [item["path"] for item in MANIFEST["files"]], "REFUSED_APPROVAL_SCOPE")
    _same(body, "targets", {item["path"]: item["target"] for item in MANIFEST["files"]}, "REFUSED_APPROVAL_SCOPE")
    _same(body, "bases", {item["path"]: item["bases"] for item in MANIFEST["files"]}, "REFUSED_APPROVAL_SCOPE")
    _same(body, "service_scope", list(PAGE_UNITS), "REFUSED_APPROVAL_SCOPE")
    _same(body, "signin_unit", SIGNIN_UNIT, "REFUSED_APPROVAL_SCOPE")
    if body.get("signin_unit_execstart_is_shell") is not True or body.get("controller_uses_shell") is not False:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if body.get("controller_adds_sudo") is not False:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    protected = [item["path"] for item in MANIFEST["verify_only"]] + [BOOK_REL]
    if sorted(body.get("protected_paths") or []) != sorted(protected):
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    _same(body, "protected_sha256", protected_sha256(), "REFUSED_APPROVAL_SCOPE")
    _same(body, "phases", MANIFEST.get("phases"), "REFUSED_APPROVAL_SCOPE")
    _same(body, "runtime", MANIFEST.get("runtime"), "REFUSED_APPROVAL_SCOPE")
    if body.get("outer_package_hash") != OUTER_PACKAGE_NOTE:
        raise ReleaseError("REFUSED_APPROVAL")
    if not body.get("approval_nonce"):
        raise ReleaseError("REFUSED_APPROVAL")
    expected = MANIFEST.get("accepted_approval_sha256")
    if not expected or presented_sha != expected or _body_contains(body, presented_sha):
        raise ReleaseError("REFUSED_APPROVAL_MISMATCH")
    return body


def _apply_body(applied, restored):
    return {
        "release": MANIFEST["release"],
        "receipt_kind": "apply",
        "claim_scope": "file-write only; unit state is the lifecycle journal; connection is a routed reply receipt",
        "applied": applied,
        "activated": False,
        "connected": False,
        "restored": restored,
        "book_written": False,
        "connection_proof": "none",
        "pages_started_by_apply": False,
        "chat_services_restarted": False,
    }


def apply_release(roots, approval=None, approval_sha=None, allow_production=False):
    """Operator file write. Refuses while this package is not an accepted approval."""
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if not isinstance(approval, dict) or not approval_sha:
        raise ReleaseError("REFUSED_APPROVAL")
    review_approval(approval, approval_sha)
    if any(_under_opt_tfo(root) for root in roots) and not allow_production:
        raise ReleaseError("REFUSED_ROOT")
    return _mutate(roots, approval, approval_sha, allow_production, _APPLY_GATE)


def _mutate(roots, approval=None, approval_sha=None, allow_production=False, gate=None):
    if gate is not _APPLY_GATE or not isinstance(approval, dict) or not approval_sha:
        raise ReleaseError("REFUSED_APPROVAL")
    review_approval(approval, approval_sha)
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if any(_under_opt_tfo(root) for root in roots) and not allow_production:
        raise ReleaseError("REFUSED_ROOT")
    triple = _roots(roots)
    trees = list(triple)
    plans = [_classify_one(root) for root in trees]
    if any(plan != plans[0] for plan in plans[1:]):
        raise ReleaseError("REFUSED_DIVERGED")
    pins = [_pins(root) for root in trees]
    if any(item != pins[0] for item in pins[1:]):
        raise ReleaseError("REFUSED_BOOK_DIVERGED")
    receipt = Path(triple[0]) / RECEIPT
    if receipt.is_file() and json.loads(receipt.read_text(encoding="utf-8")).get("restored") is not True:
        raise ReleaseError("REFUSED_RERUN")
    written = []
    backed_up = []
    installed_new = []
    try:
        for state, item in plans[0]:
            if state == "CURRENT":
                continue
            if state == "INSTALL_NEW":
                installed_new.append(item["path"])
            else:
                backed_up.append(item["path"])
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
    body = _apply_body(True, False)
    body["backed_up"] = backed_up
    body["installed_new"] = installed_new
    for root in trees:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def _load_receipt(root):
    receipt_path = Path(root) / RECEIPT
    if not receipt_path.is_file():
        raise ReleaseError("REFUSED_RECOVERY")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("release") != MANIFEST["release"] or receipt.get("applied") is not True or receipt.get("restored") is True:
        raise ReleaseError("REFUSED_RECOVERY")
    backed = receipt.get("backed_up")
    installed = receipt.get("installed_new")
    if not isinstance(backed, list) or not isinstance(installed, list):
        raise ReleaseError("REFUSED_RECOVERY")
    allowed = {item["path"] for item in MANIFEST["files"]}
    if any(rel not in allowed for rel in backed + installed):
        raise ReleaseError("REFUSED_RECOVERY")
    for rel in backed:
        if not (Path(root) / BACKUP / rel).is_file():
            raise ReleaseError("REFUSED_RECOVERY")
    return receipt


def restore_files(roots, approval=None, approval_sha=None, allow_production=False):
    triple = _roots(roots)
    receipts = [_load_receipt(root) for root in triple]
    if any(item.get("backed_up") != receipts[0].get("backed_up") or item.get("installed_new") != receipts[0].get("installed_new") for item in receipts[1:]):
        raise ReleaseError("REFUSED_RECOVERY")
    if not isinstance(approval, dict) or not approval_sha:
        raise ReleaseError("REFUSED_APPROVAL")
    review_approval(approval, approval_sha)
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if any(_under_opt_tfo(root) for root in triple) and not allow_production:
        raise ReleaseError("REFUSED_ROOT")
    before = [_pins(root) for root in triple]
    targets = {item["path"]: item["target"] for item in MANIFEST["files"]}
    for root in triple:
        for rel in receipts[0]["backed_up"]:
            dest = Path(root) / rel
            backup = Path(root) / BACKUP / rel
            dest.write_bytes(backup.read_bytes())
        for rel in receipts[0]["installed_new"]:
            dest = Path(root) / rel
            if dest.is_file() and sha256(dest) == targets[rel]:
                dest.unlink()
    if [_pins(root) for root in triple] != before:
        raise ReleaseError("REFUSED_PIN")
    body = _apply_body(False, True)
    for root in triple:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def main(argv=None, roots=None):
    parser = argparse.ArgumentParser(description="SuperGrok production release candidate. Not run by this workspace.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval")
    parser.add_argument("--restore-files", action="store_true")
    parser.add_argument("--lifecycle", choices=("status", "start-page-worker", "signin-window"))
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.lifecycle == "status":
            print(json.dumps(lifecycle("status")))
            return 0
        if args.lifecycle:
            if not args.approval:
                print("REFUSED_APPROVAL")
                print("NOT_DEPLOYED")
                return 2
            raw = Path(args.approval).read_bytes()
            body = json.loads(raw)
            result = lifecycle(
                args.lifecycle,
                confirm=args.confirm,
                approval=body,
                approval_sha=hashlib.sha256(raw).hexdigest(),
                allow_production=True,
            )
            print(json.dumps(result))
            return 0
        if args.restore_files:
            if not args.approval:
                print("REFUSED_APPROVAL")
                print("NOT_DEPLOYED")
                return 2
            raw = Path(args.approval).read_bytes()
            body = json.loads(raw)
            restore_files(PRODUCTION_ROOTS, approval=body, approval_sha=hashlib.sha256(raw).hexdigest(), allow_production=True)
            print("FILES_RESTORED")
            return 0
        if args.apply:
            if not args.approval:
                print("REFUSED_APPROVAL")
                print("NOT_DEPLOYED")
                return 2
            raw = Path(args.approval).read_bytes()
            body = json.loads(raw)
            if roots is None:
                triple = PRODUCTION_ROOTS
                allow_production = True
            else:
                triple = tuple(roots)
                allow_production = False
                if len(triple) != 3 or any(_under_opt_tfo(root) for root in triple):
                    print("REFUSED_ROOT")
                    print("NOT_DEPLOYED")
                    return 2
            apply_release(triple, approval=body, approval_sha=hashlib.sha256(raw).hexdigest(), allow_production=allow_production)
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
