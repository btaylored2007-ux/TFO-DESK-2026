#!/usr/bin/env python3
"""Review-only production controller for the shadow-throughput pair.

The unsigned template is not an approval. Staging install.py is a different tool
and still refuses /opt/tfo. This file does not restart a service or call a provider.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
import secrets
import socket
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW = json.loads((HERE / "ALLOWLIST.json").read_text())
LIVE = "/opt/tfo/live/thf"
DASH = "/opt/tfo/dash/base"
FILES = {row["path"]: row for row in ALLOW["files"]}
PROTECTED = {row["path"]: row["sha256"] for row in ALLOW["protected"]}
RECEIPT = "state/thf/shadow_throughput_prod_20261107_receipt.json"
BACKUP = "state/thf/shadow_throughput_prod_20261107_backups"


class ReleaseError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha256(Path(path).read_bytes())


def load_approval(path):
    body = json.loads(Path(path).read_text())
    if body.get("example") is True or body.get("signed") is not True or body.get("ready") is not True:
        raise ReleaseError("REFUSED_UNSIGNED")
    ident = str(body.get("authorization_id") or "")
    words = str(body.get("authorization_verbatim") or "")
    if not ident or not words:
        raise ReleaseError("REFUSED_UNSIGNED")
    if ident in ALLOW["rejected_authorization_ids"] or "supergrok" in ident.lower() or "supergrok" in words.lower():
        raise ReleaseError("REFUSED_REUSED_APPROVAL")
    pre = body.get("preflight") or {}
    digest = str(pre.get("sha256") or "")
    if digest != ALLOW["preflight_sha256"] or pre.get("status") != "captured":
        raise ReleaseError("REFUSED_PREFLIGHT")
    bound = body.get("coordinator_bound") or {}
    if bound.get("package_sha256") != ALLOW["package_sha256"] or bound.get("package") != ALLOW["package"]:
        raise ReleaseError("REFUSED_PACKAGE")
    got = {(row.get("path"), row.get("base_sha256"), row.get("target_sha256")) for row in bound.get("files") or []}
    need = {(row["path"], row["base_sha256"], row["target_sha256"]) for row in ALLOW["files"]}
    if got != need or list(bound.get("roots") or []) != list(ALLOW["roots"]):
        raise ReleaseError("REFUSED_SCOPE")
    for flag in ALLOW["flags"]:
        if body.get(flag) is not True:
            raise ReleaseError("REFUSED_SCOPE")
    if body.get("archives") or body.get("restart"):
        raise ReleaseError("REFUSED_SCOPE")
    return body


def check_package(data):
    digest = sha256(data)
    if digest in ALLOW["rejected_archive_sha256"]:
        raise ReleaseError("REFUSED_STALE_PACKAGE")
    if digest != ALLOW["package_sha256"]:
        raise ReleaseError("REFUSED_ARCHIVE")
    return digest


def members(data):
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for item in tar.getmembers():
            name = item.name.replace("\\", "/")
            if item.issym() or item.islnk() or name.startswith("/") or ".." in name.split("/"):
                raise ReleaseError("REFUSED_PATH")
            for rel in FILES:
                if name.endswith("payload/" + rel) and item.isfile():
                    extracted = tar.extractfile(item)
                    found[rel] = extracted.read() if extracted else b""
    if set(found) != set(FILES):
        raise ReleaseError("REFUSED_MEMBER")
    for rel, blob in found.items():
        if sha256(blob) != FILES[rel]["target_sha256"]:
            raise ReleaseError("REFUSED_MEMBER")
    return found


def _mode(path):
    return Path(path).stat().st_mode & 0o777


def _read_pair(root, rel):
    path = Path(root) / rel
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("REFUSED_BASE")
    data = path.read_bytes()
    digest = sha256(data)
    row = FILES[rel]
    if digest not in (row["base_sha256"], row["target_sha256"]):
        raise ReleaseError("REFUSED_BASE")
    return data, digest, _mode(path)


def _expect_modes(root):
    for rel, text in ALLOW["expected_modes"].items():
        path = Path(root) / rel
        if _mode(path) != int(text, 8):
            raise ReleaseError("REFUSED_MODE")


def _expect_preserved(root):
    for rel, digest in ALLOW["preserve_bytes"].items():
        path = Path(root) / rel
        if path.is_symlink() or not path.is_file() or file_sha(path) != digest:
            raise ReleaseError("REFUSED_PRESERVE")


def verify_protected(root):
    for rel, digest in PROTECTED.items():
        path = Path(root) / rel
        if path.is_symlink() or not path.is_file() or file_sha(path) != digest:
            raise ReleaseError("REFUSED_PROTECTED")


def _hold(live):
    tools = Path(live) / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    release = tools / ".code_release.lock"
    fd = os.open(release, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ReleaseError("REFUSED_LOCK")
    pid_path = tools / "deploy.lock"
    if pid_path.exists():
        try:
            owner = int(pid_path.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            owner = 0
        if owner > 1 and owner != os.getpid():
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                owner = 0
            except PermissionError:
                owner = owner
            if owner > 1:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                raise ReleaseError("REFUSED_LOCK")
    pid_path.write_text("%s shadow_throughput_prod_20261107\n" % os.getpid())
    return fd, pid_path


def _unlock(fd, pid_path):
    try:
        if pid_path.exists() and pid_path.read_text().startswith(str(os.getpid()) + " "):
            pid_path.unlink()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _seal(root, rel, data, mode, txn):
    path = Path(root) / BACKUP / txn / "original" / rel
    if path.exists():
        raise ReleaseError("REFUSED_BACKUP")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".seal.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, 0o444)
    if temporary.read_bytes() != data:
        temporary.unlink()
        raise ReleaseError("REFUSED_BACKUP")
    os.replace(temporary, path)
    os.chmod(path, 0o444)
    if path.read_bytes() != data:
        raise ReleaseError("REFUSED_BACKUP")
    return path


def _replace(dest, data, mode):
    temporary = dest.with_name(dest.name + ".apply.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, mode)
    if temporary.read_bytes() != data:
        temporary.unlink()
        raise ReleaseError("REFUSED_WRITE")
    os.replace(temporary, dest)
    os.chmod(dest, mode)
    if dest.read_bytes() != data:
        raise ReleaseError("REFUSED_WRITE")


def _restore_one(dest, backup, expected, mode):
    if not backup.is_file() or backup.read_bytes() != expected:
        return False
    temporary = dest.with_name(dest.name + ".restore.tmp")
    temporary.write_bytes(expected)
    os.replace(temporary, dest)
    os.chmod(dest, mode)
    return dest.read_bytes() == expected and (_mode(dest) == mode)


def apply_bound(approval, package, roots, probe, drill=False):
    """Replace both roots, or restore every root. A drill is not a production deploy."""
    if drill and not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_DRILL")
    record = load_approval(approval)
    check_package(package)
    blobs = members(package)
    if list(roots) != list(ALLOW["roots"]) and not drill:
        raise ReleaseError("REFUSED_ROOT")
    if not drill and socket.gethostname() != ALLOW["host"]:
        raise ReleaseError("REFUSED_HOST")
    if probe(ALLOW["idle_unit"]) not in ("inactive", "dead"):
        raise ReleaseError("REFUSED_SHADOW_ACTIVE")
    live = roots[0]
    if not drill:
        for root in roots:
            verify_protected(root)
            _expect_modes(root)
            _expect_preserved(root)
    fd, pid_path = _hold(live)
    txn = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
    sealed = []
    try:
        if probe(ALLOW["idle_unit"]) not in ("inactive", "dead"):
            raise ReleaseError("REFUSED_SHADOW_ACTIVE")
        originals = {}
        for root in roots:
            for rel in FILES:
                data, digest, mode = _read_pair(root, rel)
                if digest != FILES[rel]["base_sha256"]:
                    raise ReleaseError("REFUSED_BASE")
                originals[(root, rel)] = (data, mode)
        for root in roots:
            for rel in FILES:
                data, mode = originals[(root, rel)]
                path = _seal(root, rel, data, mode, txn)
                sealed.append((root, rel, path, data, mode))
        for root in roots:
            if probe(ALLOW["idle_unit"]) not in ("inactive", "dead"):
                raise ReleaseError("REFUSED_SHADOW_ACTIVE")
            for rel in FILES:
                data, mode = originals[(root, rel)]
                dest = Path(root) / rel
                _replace(dest, blobs[rel], mode)
                if file_sha(dest) != FILES[rel]["target_sha256"]:
                    raise ReleaseError("REFUSED_WRITE")
        for _root, _rel, path, data, _mode in sealed:
            if path.read_bytes() != data:
                raise ReleaseError("REFUSED_BACKUP")
        receipt = dict(release=ALLOW["release"], production=False, deployed=False,
                       targets_match=True, backups_verified=True, rollback_failed=False,
                       txn=txn, roots=[str(root) for root in roots],
                       modes={str(root): {rel: mode for (sealed_root, rel, _path, _data, mode) in sealed if sealed_root == root} for root in roots},
                       preflight_sha256=record["preflight"]["sha256"],
                       package_sha256=ALLOW["package_sha256"])
        receipt["explicit_service_action"] = False
        receipt["dash_thf_restart_possible_via_code_md5_watcher"] = True
        receipt["loaded_sha_proven"] = False
        receipt["review_only"] = True
        receipt["loaded_sha_note"] = "status version is not a file hash; a new sampler sha loads only on a later natural oneshot"
        if not drill:
            for root in roots:
                _expect_preserved(root)
        text = json.dumps(receipt, indent=2) + "\n"
        for root in roots:
            _publish_receipt(Path(root) / RECEIPT, text)
        return receipt
    except Exception as exc:
        failed = []
        for root, rel, path, data, mode in reversed(sealed):
            dest = Path(root) / rel
            if not _restore_one(dest, path, data, mode):
                failed.append(str(dest))
        for root in roots:
            receipt_path = Path(root) / RECEIPT
            if receipt_path.exists():
                try:
                    body = json.loads(receipt_path.read_text())
                except (OSError, ValueError):
                    body = {}
                if body.get("txn") == txn:
                    receipt_path.unlink()
        if failed:
            raise ReleaseError("ROLLBACK_FAILED") from exc
        raise
    finally:
        _unlock(fd, pid_path)


def _publish_receipt(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def rollback_txn(roots, txn):
    """Restore one transaction from its sealed originals. A bad backup is not copied."""
    planned = []
    for root in roots:
        receipt_path = Path(root) / RECEIPT
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError):
            raise ReleaseError("REFUSED_ROLLBACK")
        if receipt.get("txn") != txn or receipt.get("backups_verified") is not True:
            raise ReleaseError("REFUSED_ROLLBACK")
        for rel, row in FILES.items():
            dest = Path(root) / rel
            backup = Path(root) / BACKUP / txn / "original" / rel
            before = dest.read_bytes() if dest.is_file() else None
            expected = backup.read_bytes() if backup.is_file() else b""
            mode = int((receipt.get("modes") or {}).get(str(root), {}).get(rel, 0o644))
            if sha256(expected) != row["base_sha256"]:
                raise ReleaseError("ROLLBACK_FAILED")
            planned.append((dest, backup, expected, mode, before))
    restored = []
    for dest, backup, expected, mode, before in planned:
        if not _restore_one(dest, backup, expected, mode):
            raise ReleaseError("ROLLBACK_FAILED")
        if dest.read_bytes() != expected:
            raise ReleaseError("ROLLBACK_FAILED")
        restored.append(str(dest))
        del before
    return dict(rolled_back=True, production=False, deployed=False, rollback_failed=False, files=restored)


def plan_roots(roots):
    rows = []
    for root in roots:
        for rel, row in FILES.items():
            path = Path(root) / rel
            if not path.is_file() or path.is_symlink():
                rows.append(dict(root=str(root), path=rel, state="ABSENT"))
                continue
            digest = file_sha(path)
            if digest == row["target_sha256"]:
                state = "CURRENT"
            elif digest == row["base_sha256"]:
                state = "REPLACE"
            else:
                state = "BLOCKED"
            rows.append(dict(root=str(root), path=rel, state=state, sha256=digest))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval")
    parser.add_argument("--package")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(dict(ready=False, production=False, deployed=False, rows=plan_roots(ALLOW["roots"]))))
        return 0
    if not args.approval or not args.package:
        print(json.dumps(dict(ready=False, production=False, deployed=False, error="REFUSED_UNSIGNED")))
        return 2
    try:
        load_approval(args.approval)
        check_package(Path(args.package).read_bytes())
    except ReleaseError as exc:
        print(json.dumps(dict(ready=False, production=False, deployed=False, error=exc.code)))
        return 2
    print(json.dumps(dict(ready=False, production=False, deployed=False, error="REFUSED_PREFLIGHT_NOT_BOUND")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
