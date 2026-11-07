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


def apply_release(approval_path, archives, probe=None, host_name=None, roots=None, restart=None, restarter=None):
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
    originals = {}
    written = []
    try:
        for root in pair:
            receipt = Path(root) / RECEIPT
            if receipt.is_file() and json.loads(receipt.read_text(encoding="utf-8")).get("deployed") is True:
                raise ReleaseError("REFUSED_RERUN")
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
            if file_sha(Path(root) / "veteran_desk/book.py") != ALLOW["book_pin"]:
                raise ReleaseError("REFUSED_BOOK")
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
    receipt = {
        "release": ALLOW["release"], "deployed": True, "production": True, "restored": False,
        "capture_sha256": ALLOW["capture_sha256"], "archive_sha256": spec["sha256"],
        "tick_restarted": False, "engine_restarted": False, "nginx_changed": False,
        "dash_restarted": False, "ledger_rewritten": False, "baseline_written": False,
        "mark": "PENDING", "host": host,
    }
    _write_receipt(pair, receipt)
    if restart == "dash":
        unit = ALLOW["dash_restart_unit"]
        if unit in ALLOW["do_not_restart"]:
            raise ReleaseError("REFUSED_RESTART")
        if restarter is None:
            import subprocess
            subprocess.run(["systemctl", "restart", unit], check=True)
        else:
            restarter(unit)
        receipt["dash_restarted"] = True
        try:
            _write_receipt(pair, receipt)
        except OSError as error:
            raise ReleaseError("RECEIPT_AFTER_RESTART_FAILED") from error
    return receipt


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
    receipts = []
    for root in pair:
        receipt_path = Path(root) / RECEIPT
        if not receipt_path.is_file():
            raise ReleaseError("ROLLBACK_REFUSED")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("restored") is True:
            raise ReleaseError("REFUSED_RERUN")
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
    receipt = receipts[0]
    receipt["deployed"] = False
    receipt["restored"] = True
    receipt["dash_restarted"] = False
    _write_receipt(pair, receipt)
    return True


def socket_host():
    import socket
    return socket.gethostname()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the display-epoch UI archive on tfo-sgp1 only.")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--restart-dash", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = apply_release(
            args.approval,
            {ALLOW["archives"][0]["name"]: args.archive},
            restart="dash" if args.restart_dash else None,
        )
    except ReleaseError as error:
        print(error.code)
        print("NOT_DEPLOYED")
        return 2
    print("BOUND_APPLIED" if receipt.get("deployed") else "NOT_DEPLOYED")
    print(receipt["archive_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
