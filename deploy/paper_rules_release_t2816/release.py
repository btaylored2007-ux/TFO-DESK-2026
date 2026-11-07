#!/usr/bin/env python3
"""Production release for the live-base paper-rules package only.

t2816u "Ok fix these and deploy." is recorded apart from the coordinator-bound
hashes. The rejected package f65604a0 and gate base 5582d153 are refused.
Tick and book flocks are observed and released immediately so a tick is not skipped.
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
HOST = ALLOW["host"]
RECEIPT = "state/thf/paper_rules_release_t2816_receipt.json"
BACKUP = "state/thf/paper_rules_release_t2816_backups"
FILES = {row["path"]: row for row in ALLOW["files"]}


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
    if body.get("example") is True:
        raise ReleaseError("REFUSED_EXAMPLE_APPROVAL")
    if body.get("authorization_verbatim") != ALLOW["authorization_verbatim"]:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("authorization_id") != ALLOW["authorization_id"]:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("authorization_wita") != ALLOW["authorization_wita"]:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("prior_authorization_verbatim") != ALLOW["prior_authorization_verbatim"]:
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("prior_authorization_id") != ALLOW["prior_authorization_id"]:
        raise ReleaseError("REFUSED_APPROVAL")
    bound = body.get("coordinator_bound") or {}
    if bound.get("package") != ALLOW["package"] or bound.get("package_sha256") != ALLOW["package_sha256"]:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    got = {(row.get("path"), row.get("base_sha256"), row.get("target_sha256")) for row in bound.get("files") or []}
    need = {(row["path"], row["base_sha256"], row["target_sha256"]) for row in ALLOW["files"]}
    if got != need:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if sorted(body.get("not_in_this_apply") or []) != sorted(ALLOW["not_in_this_apply"]):
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if body.get("host") != HOST or sorted(body.get("roots") or []) != sorted(ALLOW["roots"]):
        raise ReleaseError("REFUSED_APPROVAL")
    for flag in ("no_tick_restart", "no_engine_restart", "no_nginx_change", "no_ledger_rewrite"):
        if body.get(flag) is not True:
            raise ReleaseError("REFUSED_APPROVAL")
    if body.get("archives"):
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    return body


def check_package(path):
    data = Path(path).read_bytes()
    digest = sha256(data)
    if digest in ALLOW["rejected_package_sha256"]:
        raise ReleaseError("REFUSED_STALE_PACKAGE")
    if digest != ALLOW["package_sha256"]:
        raise ReleaseError("REFUSED_ARCHIVE")
    return data


def member(blob, rel):
    if rel not in FILES or rel.startswith("../") or "/../" in rel:
        raise ReleaseError("REFUSED_PATH")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
        for item in tar.getmembers():
            name = item.name.replace("\\", "/")
            if item.issym() or item.islnk() or name.startswith("/") or ".." in name.split("/"):
                continue
            if name.endswith("payload/" + rel) and item.isfile():
                extracted = tar.extractfile(item)
                return extracted.read() if extracted else b""
    raise ReleaseError("REFUSED_MEMBER")


def invariants(rel, data):
    text = data.decode("utf-8", "replace")
    for needle in ALLOW["needles"][rel]:
        if needle not in text:
            raise ReleaseError("REFUSED_INVARIANT")
    for banned in ALLOW["forbidden"]:
        if banned in text:
            raise ReleaseError("REFUSED_INVARIANT")


def safe_dest(root, rel):
    if rel not in FILES:
        raise ReleaseError("REFUSED_PATH")
    root_path = Path(root).resolve()
    cur = root_path
    for part in Path(rel).parts:
        if part in ("", ".", ".."):
            raise ReleaseError("REFUSED_PATH")
        cur = cur / part
        if cur.is_symlink():
            raise ReleaseError("REFUSED_SYMLINK")
    dest = root_path / rel
    if not str(dest.resolve()).startswith(str(root_path) + os.sep):
        raise ReleaseError("REFUSED_PATH")
    return dest


def systemctl_is_active(unit):
    import subprocess
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return (out.stdout or out.stderr or "unknown").strip().split()[0]


def services_idle(probe):
    for unit in ALLOW["idle_units"]:
        state = probe(unit)
        if state not in ("inactive", "dead", "failed"):
            raise ReleaseError("REFUSED_BUSY %s %s" % (unit, state))


def flock_free(path):
    """True when no process holds the lock. The lock is not kept, so the tick is not skipped."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def writers_idle(live, probe):
    services_idle(probe)
    if not flock_free(Path(live) / ".tick.flock"):
        raise ReleaseError("REFUSED_BUSY tick-flock")
    if not flock_free(Path(live) / "state" / "thf" / ".book.flock"):
        raise ReleaseError("REFUSED_BUSY book-flock")


def refresh_would_defer(live):
    lock = Path(live) / "tools" / ".code_release.lock"
    try:
        fd = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        fd = None
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
    pid_path = Path(live) / "tools" / "deploy.lock"
    try:
        owner = int(pid_path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    if owner <= 1:
        return False
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                raise ReleaseError("REFUSED_LOCK")
            except ProcessLookupError:
                pass
    pid_path.write_text("%s %s paper_rules_release_t2816\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    return fd, pid_path


def _release(fd, pid_path):
    try:
        if pid_path and pid_path.exists() and pid_path.read_text().startswith(str(os.getpid()) + " "):
            pid_path.unlink()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _txn_id():
    return "%s-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), os.getpid(), secrets.token_hex(4))


class Transaction:
    def __init__(self, roots):
        self.id = _txn_id()
        self.roots = [str(root) for root in roots]
        self.originals = {}
        self.integrity_ok = True
        self.writes = 0

    def _role(self, root):
        return "live" if str(root) == self.roots[0] else "dash"

    def seal(self, root, rel):
        key = (str(root), rel)
        if key in self.originals:
            return self.originals[key]
        src = Path(root) / rel
        if src.is_symlink() or not src.is_file():
            raise ReleaseError("REFUSED_BACKUP")
        data = src.read_bytes()
        digest = sha256(data)
        dest = Path(root) / BACKUP / self.id / "original" / rel
        if dest.exists():
            raise ReleaseError("REFUSED_BACKUP")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        os.chmod(dest, 0o444)
        if dest.read_bytes() != data:
            raise ReleaseError("REFUSED_BACKUP")
        record = dict(root=str(root), rel=rel, role=self._role(root), sha=digest, bytes=data, path=dest)
        self.originals[key] = record
        return record

    def restore_all(self):
        plan = []
        for rec in self.originals.values():
            sealed = rec["bytes"]
            if sealed is None or sha256(sealed) != rec["sha"]:
                raise ReleaseError("REFUSED_BACKUP")
            try:
                disk = rec["path"].read_bytes() if rec["path"].is_file() else None
            except OSError:
                disk = None
            if disk != sealed:
                self.integrity_ok = False
                if rec["path"].exists():
                    os.chmod(rec["path"], 0o644)
                    rec["path"].replace(rec["path"].with_name(rec["path"].name + ".integrity_failed"))
                rec["path"].parent.mkdir(parents=True, exist_ok=True)
                rec["path"].write_bytes(sealed)
                os.chmod(rec["path"], 0o444)
            plan.append(rec)
        for rec in plan:
            dest = Path(rec["root"]) / rec["rel"]
            if dest.is_symlink():
                raise ReleaseError("REFUSED_SYMLINK")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(rec["bytes"])
            if file_sha(dest) != rec["sha"]:
                raise ReleaseError("REFUSED_BACKUP")
        return self.integrity_ok


def _put(root, rel, data):
    dest = safe_dest(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".paperrules.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def apply_release(approval_path, package, probe=None, host_name=None, roots=None):
    if (host_name is not None or roots is not None) and not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_OVERRIDE")
    approval = load_approval(approval_path)
    blob = check_package(package)
    payloads = {}
    for rel, item in FILES.items():
        raw = member(blob, rel)
        if sha256(raw) != item["target_sha256"]:
            raise ReleaseError("REFUSED_PAYLOAD")
        invariants(rel, raw)
        payloads[rel] = raw
    host = host_name if host_name is not None else socket.gethostname()
    if host != HOST:
        raise ReleaseError("REFUSED_HOST")
    pair = tuple(roots) if roots is not None else (LIVE, DASH)
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    if roots is None:
        for root in pair:
            if str(Path(root).resolve()) not in (LIVE, DASH):
                raise ReleaseError("REFUSED_ROOT")
    probe = probe or systemctl_is_active
    writers_idle(pair[0], probe)
    for rel, item in FILES.items():
        hashes = []
        for root in pair:
            dest = safe_dest(root, rel)
            if not dest.is_file():
                raise ReleaseError("REFUSED_BASE")
            current = file_sha(dest)
            hashes.append(current)
            if current in ALLOW["rejected_gate_base_sha256"]:
                raise ReleaseError("REFUSED_STALE_BASE")
            if current != item["target_sha256"] and current != item["base_sha256"]:
                raise ReleaseError("REFUSED_BASE")
        if hashes[0] != hashes[1]:
            raise ReleaseError("REFUSED_DIVERGED")
    live = pair[0]
    fd, pid_path = _hold(live)
    txn = Transaction(pair)
    try:
        writers_idle(live, probe)
        for rel, item in FILES.items():
            if file_sha(Path(pair[0]) / rel) == item["target_sha256"]:
                continue
            for root in pair:
                writers_idle(live, probe)
                rec = txn.seal(root, rel)
                if rec["sha"] != item["base_sha256"]:
                    raise ReleaseError("REFUSED_BASE")
                _put(root, rel, payloads[rel])
                txn.writes += 1
                if file_sha(Path(root) / rel) != item["target_sha256"]:
                    raise ReleaseError("REFUSED_VERIFY")
                invariants(rel, (Path(root) / rel).read_bytes())
        for root in pair:
            for rel, item in FILES.items():
                if file_sha(Path(root) / rel) != item["target_sha256"]:
                    raise ReleaseError("REFUSED_VERIFY")
        receipt = _receipt(approval, host, txn, deployed=True, restored=False)
        _write_receipt(pair, receipt)
        return receipt
    except Exception as exc:
        restored = False
        if txn.originals:
            try:
                txn.restore_all()
                restored = True
            except ReleaseError:
                restored = False
            failed = _receipt(approval, host, txn, deployed=False, restored=restored, error="%s: %s" % (type(exc).__name__, exc))
            try:
                _write_receipt(pair, failed)
            except OSError:
                pass
        raise
    finally:
        _release(fd, pid_path)


def _receipt(approval, host, txn, deployed, restored, error=None):
    body = dict(
        release="paper_rules_release_t2816", deployed=bool(deployed), production=bool(deployed),
        restored=bool(restored), backup_integrity=bool(txn.integrity_ok), txn=txn.id,
        authorization_verbatim=approval["authorization_verbatim"],
        authorization_id=approval["authorization_id"],
        prior_authorization_id=approval["prior_authorization_id"],
        package=ALLOW["package"], package_sha256=ALLOW["package_sha256"],
        book_unchanged=False,
        book_before=FILES["veteran_desk/book.py"]["base_sha256"],
        book_after=FILES["veteran_desk/book.py"]["target_sha256"],
        loaded_by_running_engine=False, tick_restarted=False, engine_restarted=False,
        dash_restarted=False, nginx_changed=False, ledger_rewritten=False, host=host,
        originals=[{"role": rec["role"], "path": rec["rel"], "sha256": rec["sha"]} for rec in txn.originals.values()])
    if error:
        body["error"] = error
    return body


def _write_receipt(pair, receipt):
    encoded = json.dumps(receipt, indent=2) + "\n"
    for root in pair:
        path = Path(root) / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply paper_rules_20261107 on tfo-sgp1 only.")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = apply_release(args.approval, args.package)
    except ReleaseError as error:
        print(error.code)
        print("NOT_DEPLOYED")
        return 2
    print("RELEASED")
    print(receipt["book_after"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
