#!/usr/bin/env python3
"""Hash-bound display-epoch release. Not run by this workspace against a host.

Modeled on the reviewed 19:17 runner's checks. It does not rerun that runner or its archives.
The retail and display-epoch installers still refuse /opt/tfo. This controller is separate.
It writes only the files in ALLOWLIST, on the two named roots, after both match the captured bases.
It does not write thf_home.py, the book, a display baseline, tick, engine, or nginx.
"""
import argparse
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW = json.loads((HERE / "ALLOWLIST.json").read_text(encoding="utf-8"))
RECEIPT = "state/thf/display_release_20261107_receipt.json"
JOURNAL = "state/thf/display_release_20261107_journal.jsonl"
HOST = ALLOW["host"]
LIVE, DASH = ALLOW["roots"]


class ReleaseError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha256(Path(path).read_bytes())


def load_approval(path):
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if body.get("example") is True:
        raise ReleaseError("REFUSED_EXAMPLE_APPROVAL")
    if body.get("authorization_id") in (ALLOW.get("spent_authorization_ids") or []):
        raise ReleaseError("REFUSED_SPENT_AUTHORIZATION")
    if body.get("scope_verbatim") != ALLOW["scope_verbatim"]:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("capture_sha256") != ALLOW["capture_sha256"]:
        raise ReleaseError("REFUSED_APPROVAL")
    got = {(row.get("name"), row.get("sha256")) for row in body.get("archives") or []}
    need = {(row["name"], row["sha256"]) for row in ALLOW["archives"]}
    if got != need:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if body.get("host") != HOST or sorted(body.get("roots") or []) != sorted(ALLOW["roots"]):
        raise ReleaseError("REFUSED_APPROVAL")
    if "veteran_desk/book.py" not in (body.get("excludes") or []):
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("no_tick_restart") is not True or body.get("no_ledger_rewrite") is not True:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("baseline_id") == "owner-display-20261107T121234Z":
        raise ReleaseError("BASELINE_REJECTED")
    return body


def check_archives(paths):
    by_name = {row["name"]: row for row in ALLOW["archives"]}
    if set(paths) != set(by_name):
        raise ReleaseError("REFUSED_ARCHIVE")
    opened = {}
    rejected = set()
    for row in ALLOW["archives"]:
        rejected.update(row.get("rejected_sha256") or [])
    for name, path in paths.items():
        data = Path(path).read_bytes()
        digest = sha256(data)
        if digest in rejected:
            raise ReleaseError("REFUSED_OLD_ARCHIVE")
        if digest != by_name[name]["sha256"]:
            raise ReleaseError("REFUSED_ARCHIVE")
        opened[name] = data
    return opened


def member(blob, rel):
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ReleaseError("REFUSED_PATH")
    if rel in ALLOW["excluded"] or rel == "veteran_desk/thf_home.py":
        raise ReleaseError("REFUSED_PIN")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        try:
            handle = archive.extractfile(rel)
        except KeyError:
            handle = None
        if handle is None:
            raise ReleaseError("REFUSED_MEMBER")
        return handle.read()


def _put(root, rel, data):
    dest = Path(root) / rel
    if dest.is_symlink():
        raise ReleaseError("REFUSED_SYMLINK")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".displayrel.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _journal(root, event):
    path = Path(root) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _events(root):
    path = Path(root) / JOURNAL
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            found.append(json.loads(line)["event"])
    return found


def reconcile(roots):
    """Read-only. Does not apply, restore, or restart."""
    report = []
    interesting = (
        "files_committed", "restored_before_restart", "rolled_back",
        "restart_begin", "restart_ok", "restart_failed",
    )
    for root in roots:
        events = [item for item in _events(root) if item in interesting]
        latest = events[-1] if events else None
        receipt_path = Path(root) / RECEIPT
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
        outcome = None if not receipt else receipt.get("restart_outcome")
        if latest == "restart_ok":
            if outcome == "RECORDED":
                state = "RESTART_RECORDED"
            elif outcome == "UNKNOWN":
                state = "RESTART_JOURNAL_OK_RECEIPT_UNKNOWN"
            else:
                state = "RESTART_JOURNAL_OK_RECEIPT_STALE"
        elif latest in ("restart_begin", "restart_failed"):
            state = "RESTART_UNKNOWN" if latest == "restart_begin" else "RESTART_FAILED"
        elif latest in ("restored_before_restart", "rolled_back"):
            state = "RESTORED"
        elif latest == "files_committed":
            state = "FILES_COMMITTED_RESTART_NOT_ATTEMPTED"
        elif latest is None and receipt is None:
            state = "NOT_STARTED"
        else:
            state = "PARTIAL"
        report.append({"root": str(root), "state": state, "restart_outcome": outcome})
    return report


def _blocked(roots):
    return any(row["state"] not in ("NOT_STARTED", "RESTORED") for row in reconcile(roots))


def _under_opt_tfo(path):
    text = str(path)
    return text == "/opt/tfo" or text.startswith("/opt/tfo/")


def apply_release(approval_path, archives, probe=None, host_name=None, roots=None, restart=None, restarter=None):
    """Operator entry. Refuses while the shipped readiness flags are not true."""
    if not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    return _apply_files(approval_path, archives, probe, host_name, roots, restart, restarter)


def _apply_files(approval_path, archives, probe=None, host_name=None, roots=None, restart=None, restarter=None):
    if (host_name is not None or roots is not None) and not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_OVERRIDE")
    approval = load_approval(approval_path)
    blobs = check_archives(archives)
    host = socket_host() if host_name is None else host_name
    if host != HOST:
        raise ReleaseError("REFUSED_HOST")
    if restart not in (None, "dash"):
        raise ReleaseError("REFUSED_RESTART")
    pair = tuple(roots) if roots is not None else (LIVE, DASH)
    if any(_under_opt_tfo(root) for root in pair) and not operator_apply_allowed():
        raise ReleaseError("REFUSED_NOT_READY")
    if roots is None and pair != (LIVE, DASH):
        raise ReleaseError("REFUSED_ROOT")
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    if roots is None:
        for root in pair:
            if str(Path(root).resolve()) not in (LIVE, DASH):
                raise ReleaseError("REFUSED_ROOT")
    probe = probe or (lambda _unit: "inactive")
    for unit in ALLOW["idle_units"]:
        if probe(unit) not in ("inactive", "failed"):
            raise ReleaseError("REFUSED_BUSY")
    if _blocked(pair):
        raise ReleaseError("REFUSED_RERUN")
    books = _observe_books(pair)
    originals = {}
    written = []
    try:
        spec = ALLOW["archives"][0]
        blob = blobs[spec["name"]]
        for item in spec["files"]:
            rel = item["path"]
            hashes = []
            for root in pair:
                dest = Path(root) / rel
                if dest.is_symlink():
                    raise ReleaseError("REFUSED_SYMLINK")
                if item.get("new"):
                    if dest.exists():
                        current = file_sha(dest)
                        if current != item["target"]:
                            raise ReleaseError("REFUSED_BASE")
                        hashes.append(current)
                    else:
                        hashes.append(None)
                else:
                    if not dest.is_file():
                        raise ReleaseError("REFUSED_BASE")
                    current = file_sha(dest)
                    hashes.append(current)
                    if current != item["target"] and current not in item["bases"]:
                        raise ReleaseError("REFUSED_BASE")
            if hashes[0] != hashes[1]:
                raise ReleaseError("REFUSED_DIVERGED")
            if hashes[0] == item["target"]:
                continue
            raw = member(blob, rel)
            if sha256(raw) != item["target"]:
                raise ReleaseError("REFUSED_PAYLOAD")
            for root in pair:
                dest = Path(root) / rel
                if dest.is_file():
                    prior = dest.read_bytes()
                    originals[(root, rel)] = prior
                    backup = Path(root) / "state/thf/display_release_20261107_backup" / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(prior)
                _put(root, rel, raw)
                written.append((root, rel))
                if file_sha(dest) != item["target"]:
                    raise ReleaseError("REFUSED_VERIFY")
        for pin in ALLOW["verify_only"]:
            for root in pair:
                dest = Path(root) / pin["path"]
                if dest.is_symlink() or not dest.is_file() or file_sha(dest) != pin["sha256"]:
                    raise ReleaseError("REFUSED_PIN")
        for root in pair:
            if (Path(root) / "state/thf/display_epoch.json").exists():
                raise ReleaseError("BASELINE_REJECTED")
    except Exception:
        for root, rel in reversed(written):
            dest = Path(root) / rel
            prior = originals.get((root, rel))
            if prior is None:
                if dest.is_file():
                    dest.unlink()
            else:
                dest.write_bytes(prior)
        raise
    try:
        _same_books(pair, books)
    except ReleaseError:
        _restore(pair, written, originals)
        raise
    for root in pair:
        _journal(root, "files_committed")
    receipt = {
        "release": ALLOW["release"], "deployed": True, "production": True, "restored": False,
        "capture_sha256": ALLOW["capture_sha256"], "archive_sha256": spec["sha256"],
        "tick_restarted": False, "engine_restarted": False, "nginx_changed": False,
        "restart_outcome": "NOT_ATTEMPTED", "dash_restarted": None,
        "ledger_rewritten": False, "baseline_written": False, "book_written": False,
        "book_observed_sha256": sha256(books[pair[0]]),
        "mark": "PENDING", "host": host,
    }
    try:
        _write_receipt(pair, receipt)
    except OSError as error:
        _restore(pair, written, originals)
        for root in pair:
            _journal(root, "restored_before_restart")
        raise ReleaseError("RECEIPT_FAILED") from error
    if restart != "dash":
        return receipt
    unit = ALLOW["dash_restart_unit"]
    if unit in ALLOW["do_not_restart"]:
        raise ReleaseError("REFUSED_RESTART")
    for root in pair:
        _journal(root, "restart_begin")
    try:
        if restarter is None:
            import subprocess
            subprocess.run(["systemctl", "restart", unit], check=True)
        else:
            restarter(unit)
    except Exception as error:
        for root in pair:
            _journal(root, "restart_failed")
        receipt["restart_outcome"] = "UNKNOWN"
        receipt["dash_restarted"] = None
        try:
            _write_receipt(pair, receipt)
        except OSError:
            pass
        raise ReleaseError("RESTART_FAILED") from error
    try:
        for root in pair:
            _journal(root, "restart_ok")
    except OSError as error:
        _mark_unknown(pair, receipt)
        raise ReleaseError("RECEIPT_AFTER_RESTART_FAILED") from error
    receipt["restart_outcome"] = "UNKNOWN"
    receipt["dash_restarted"] = None
    try:
        _write_receipt(pair, receipt)
    except OSError as error:
        _mark_unknown(pair, receipt)
        raise ReleaseError("RECEIPT_AFTER_RESTART_FAILED") from error
    receipt["restart_outcome"] = "RECORDED"
    receipt["dash_restarted"] = True
    try:
        _write_receipt(pair, receipt)
    except OSError as error:
        _mark_unknown(pair, receipt)
        raise ReleaseError("RECEIPT_AFTER_RESTART_FAILED") from error
    return receipt


def _book_path(root):
    return Path(root) / "veteran_desk/book.py"


def _observe_books(pair):
    found = {}
    for root in pair:
        path = _book_path(root)
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("REFUSED_BOOK")
        found[root] = path.read_bytes()
    if found[pair[0]] != found[pair[1]]:
        raise ReleaseError("REFUSED_BOOK_DIVERGED")
    return found


def _same_books(pair, books):
    for root in pair:
        if _book_path(root).read_bytes() != books[root]:
            raise ReleaseError("REFUSED_BOOK_CHANGED")


def _restore(pair, written, originals):
    for root, rel in reversed(written):
        dest = Path(root) / rel
        prior = originals.get((root, rel))
        if prior is None:
            if dest.is_file():
                dest.unlink()
        else:
            dest.write_bytes(prior)


def _mark_unknown(pair, receipt):
    """Do not record a successful restart as false. Leave UNKNOWN if the final receipt cannot be written."""
    receipt["restart_outcome"] = "UNKNOWN"
    receipt["dash_restarted"] = None
    try:
        _write_receipt(pair, receipt)
    except OSError:
        pass


def _write_receipt(pair, receipt):
    encoded = json.dumps(receipt, indent=2) + "\n"
    for root in pair:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded, encoding="utf-8")


def rollback_release(roots):
    """Restore the on-disk backups. Refuses a host override outside tests. Does not restart anything."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_OVERRIDE")
    pair = tuple(roots)
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    restart_events = ("restart_begin", "restart_ok", "restart_failed")
    for root in pair:
        if any(item in restart_events for item in _events(root)):
            raise ReleaseError("REFUSED_ROLLBACK_AFTER_RESTART")
    receipts = []
    for root in pair:
        receipt_path = Path(root) / RECEIPT
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
        if receipt and receipt.get("restored") is True:
            raise ReleaseError("REFUSED_RERUN")
        if receipt is None and "files_committed" not in _events(root):
            raise ReleaseError("ROLLBACK_REFUSED")
        receipts.append(receipt)
        for item in ALLOW["archives"][0]["files"]:
            rel = item["path"]
            dest = Path(root) / rel
            backup = Path(root) / "state/thf/display_release_20261107_backup" / rel
            if item.get("new"):
                if dest.is_file():
                    dest.unlink()
            else:
                if not backup.is_file():
                    raise ReleaseError("ROLLBACK_REFUSED")
                dest.write_bytes(backup.read_bytes())
                if file_sha(dest) not in item["bases"]:
                    raise ReleaseError("REFUSED_VERIFY")
    for root in pair:
        _journal(root, "rolled_back")
    receipt = next((item for item in receipts if item), None) or {
        "release": ALLOW["release"], "production": True, "host": HOST,
        "capture_sha256": ALLOW["capture_sha256"], "mark": "PENDING",
        "tick_restarted": False, "engine_restarted": False, "nginx_changed": False,
        "ledger_rewritten": False, "baseline_written": False,
    }
    receipt["deployed"] = False
    receipt["restored"] = True
    receipt["restart_outcome"] = "NOT_ATTEMPTED"
    receipt["dash_restarted"] = None
    _write_receipt(pair, receipt)
    return True


PROTECTED = (
    "veteran_desk/book.py",
    "veteran_desk/chatgpt_bridge.py",
    "tools/chatgpt_web.py",
    "veteran_desk/thf_home.py",
)


def recover_release(roots, action, restarter=None):
    """Operator recovery. Restores only the display write-set, or restarts dash once.
    Does not reapply, does not write the book, and does not restart the engine."""
    if action not in ("restore-files", "restart-dash", "note-stale-receipt"):
        raise ReleaseError("REFUSED_RECOVERY")
    pair = tuple(roots)
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    if not os.environ.get("PYTEST_CURRENT_TEST") and tuple(str(Path(root)) for root in pair) != (LIVE, DASH):
        raise ReleaseError("REFUSED_ROOT")
    books = _observe_books(pair)
    if action == "restart-dash":
        unit = ALLOW["dash_restart_unit"]
        for root in pair:
            events = _events(root)
            if "files_committed" not in events:
                raise ReleaseError("REFUSED_RECOVERY")
            for item in ALLOW["archives"][0]["files"]:
                dest = Path(root) / item["path"]
                if item["path"] in PROTECTED or not dest.is_file() or file_sha(dest) != item["target"]:
                    raise ReleaseError("REFUSED_VERIFY")
        for root in pair:
            _journal(root, "restart_begin")
        try:
            if restarter is None:
                import subprocess
                subprocess.run(["systemctl", "restart", unit], check=True)
            else:
                restarter(unit)
        except Exception as error:
            for root in pair:
                _journal(root, "restart_failed")
            _same_books(pair, books)
            raise ReleaseError("RESTART_FAILED") from error
        for root in pair:
            _journal(root, "restart_ok")
        _same_books(pair, books)
        return {"action": "restart-dash", "unit": unit, "book_written": False}
    if action == "note-stale-receipt":
        for root in pair:
            if "restart_ok" not in _events(root):
                raise ReleaseError("REFUSED_RECOVERY")
            for item in ALLOW["archives"][0]["files"]:
                dest = Path(root) / item["path"]
                if not dest.is_file() or file_sha(dest) != item["target"]:
                    raise ReleaseError("REFUSED_VERIFY")
        receipt = {
            "release": ALLOW["release"], "deployed": True, "restored": False, "production": True,
            "restart_outcome": "UNKNOWN", "dash_restarted": None, "book_written": False,
            "book_observed_sha256": sha256(books[pair[0]]),
            "journal_restart_ok": True, "mark": "PENDING",
        }
        _write_receipt(pair, receipt)
        _same_books(pair, books)
        return {"action": "note-stale-receipt", "book_written": False, "dash_restarted": None}
    for root in pair:
        for item in ALLOW["archives"][0]["files"]:
            rel = item["path"]
            if rel in PROTECTED:
                raise ReleaseError("REFUSED_BOOK")
            dest = Path(root) / rel
            backup = Path(root) / "state/thf/display_release_20261107_backup" / rel
            if item.get("new"):
                if dest.is_file():
                    dest.unlink()
            else:
                if not backup.is_file():
                    raise ReleaseError("ROLLBACK_REFUSED")
                dest.write_bytes(backup.read_bytes())
                if file_sha(dest) not in item["bases"]:
                    raise ReleaseError("REFUSED_VERIFY")
    _same_books(pair, books)
    for root in pair:
        _journal(root, "rolled_back")
    receipt = {
        "release": ALLOW["release"], "deployed": False, "restored": True, "production": True,
        "book_written": False, "book_observed_sha256": sha256(books[pair[0]]),
        "restart_outcome": "NOT_ATTEMPTED", "dash_restarted": None, "mark": "PENDING",
    }
    _write_receipt(pair, receipt)
    return {"action": "restore-files", "book_written": False}


def socket_host():
    import socket
    return socket.gethostname()


def operator_apply_allowed():
    """False flags are a refusal. Editing them in place is not an approval."""
    return ALLOW.get("deployment_ready") is True and ALLOW.get("ops_review_ready") is True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the display-epoch UI archive on tfo-sgp1 only.")
    parser.add_argument("--approval")
    parser.add_argument("--archive")
    parser.add_argument("--restart-dash", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--recover", choices=("restore-files", "restart-dash", "note-stale-receipt"))
    args = parser.parse_args(argv)
    if args.recover:
        try:
            result = recover_release((LIVE, DASH), args.recover)
        except ReleaseError as error:
            print(error.code)
            print("NOT_RECOVERED")
            return 2
        print(result["action"])
        print("BOOK_UNCHANGED")
        return 0
    if args.reconcile:
        print(json.dumps(reconcile((LIVE, DASH))))
        print("READ_ONLY")
        return 0
    if not args.approval or not args.archive:
        print("REFUSED_APPROVAL")
        print("NOT_DEPLOYED")
        return 2
    if not operator_apply_allowed():
        print("REFUSED_NOT_READY")
        print("NOT_DEPLOYED")
        return 2
    try:
        receipt = apply_release(
            args.approval,
            {ALLOW["archives"][0]["name"]: args.archive},
            restart="dash" if args.restart_dash else None,
        )
    except ReleaseError as error:
        print(error.code)
        if error.code == "RECEIPT_AFTER_RESTART_FAILED":
            print("UNKNOWN")
        elif error.code == "RESTART_FAILED":
            print("PARTIAL")
        else:
            print("NOT_DEPLOYED")
        return 2
    print("BOUND_APPLIED" if receipt.get("deployed") else "NOT_DEPLOYED")
    print(receipt["archive_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
