#!/usr/bin/env python3
"""Supported release for three named archives. Does not replace the staging installers.

Those installers still refuse /opt/tfo. This tool is the only production path, and only
when an approval file records Brett's 18:52 WITA sentence and binds the three archive
hashes. It holds tools/.code_release.lock and tools/deploy.lock on the live tree so
tfo-dash-refresh defers, writes live and dash to the same bytes, and rechecks that the
shadow sampler and tick are idle at apply time.

Cloud and any host other than tfo-sgp1 are refused. There is no --root override on the
command line.
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
RECEIPT = "state/thf/thf_release_20261107_receipt.json"
BACKUP = "state/thf/thf_release_20261107_backups"


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
    got = {(row.get("name"), row.get("sha256")) for row in body.get("archives") or []}
    need = {(row["name"], row["sha256"]) for row in ALLOW["archives"]}
    if got != need:
        raise ReleaseError("REFUSED_APPROVAL_SCOPE")
    if body.get("host") != HOST:
        raise ReleaseError("REFUSED_APPROVAL")
    if sorted(body.get("roots") or []) != sorted(ALLOW["roots"]):
        raise ReleaseError("REFUSED_APPROVAL")
    if "veteran_desk/book.py" not in (body.get("excludes") or []):
        raise ReleaseError("REFUSED_APPROVAL")
    if body.get("no_tick_restart") is not True:
        raise ReleaseError("REFUSED_APPROVAL")
    return body


def check_archives(paths):
    by_name = {row["name"]: row for row in ALLOW["archives"]}
    if set(paths) != set(by_name):
        raise ReleaseError("REFUSED_ARCHIVE")
    opened = {}
    for name, path in paths.items():
        data = Path(path).read_bytes()
        digest = sha256(data)
        if digest in (by_name[name].get("rejected_sha256") or []):
            raise ReleaseError("REFUSED_OLD_ARCHIVE")
        if digest != by_name[name]["sha256"]:
            raise ReleaseError("REFUSED_ARCHIVE")
        opened[name] = data
    return opened


def member(blob, rel):
    if rel == "veteran_desk/book.py" or rel.startswith("../") or "/../" in rel:
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


def safe_dest(root, rel):
    if rel in ALLOW["excluded"] or rel == "veteran_desk/book.py":
        raise ReleaseError("REFUSED_BOOK")
    root_path = Path(root).resolve()
    cur = root_path
    for part in Path(rel).parts:
        if part in ("", ".", ".."):
            raise ReleaseError("REFUSED_PATH")
        cur = cur / part
        if cur.is_symlink():
            raise ReleaseError("REFUSED_SYMLINK")
    dest = root_path / rel
    if not str(dest.resolve()).startswith(str(root_path) + os.sep) and dest.resolve() != root_path:
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


def refresh_would_defer(live):
    """Same decision as tools/thf_dash_refresh.sh: flock or a live deploy.lock pid."""
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
    pid_path.write_text("%s %s thf_release_20261107\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
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
    """One apply. Original bytes are sealed once per root and never overwritten."""

    def __init__(self, roots):
        self.id = _txn_id()
        self.roots = [str(root) for root in roots]
        self.originals = {}
        self.stages = []
        self.writes = 0
        self.integrity_ok = True

    def _role(self, root):
        return "live" if str(root) == self.roots[0] else "dash"

    def _dir(self, root):
        return Path(root) / BACKUP / self.id

    def seal(self, root, rel):
        key = (str(root), rel)
        if key in self.originals:
            return self.originals[key]
        src = Path(root) / rel
        if src.is_symlink() or not src.is_file():
            raise ReleaseError("REFUSED_BACKUP")
        data = src.read_bytes()
        digest = sha256(data)
        dest = self._dir(root) / "original" / rel
        if dest.exists():
            raise ReleaseError("REFUSED_BACKUP")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        os.chmod(dest, 0o444)
        if not dest.is_file() or dest.read_bytes() != data or file_sha(dest) != digest:
            raise ReleaseError("REFUSED_BACKUP")
        record = dict(root=str(root), rel=rel, role=self._role(root), sha=digest, bytes=data, path=dest)
        self.originals[key] = record
        return record

    def stage(self, root, rel, archive, order):
        src = Path(root) / rel
        if src.is_symlink() or not src.is_file():
            raise ReleaseError("REFUSED_BACKUP")
        data = src.read_bytes()
        digest = sha256(data)
        dest = self._dir(root) / "stages" / ("%02d-%s" % (order, archive)) / rel
        if dest.exists():
            raise ReleaseError("REFUSED_BACKUP")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        os.chmod(dest, 0o444)
        if dest.read_bytes() != data:
            raise ReleaseError("REFUSED_BACKUP")
        self.stages.append(dict(archive=archive, order=order, role=self._role(root), path=rel, before_sha256=digest, file=str(dest)))
        return digest

    def write_manifest(self, root):
        body = dict(
            txn=self.id,
            originals={rec["rel"]: rec["sha"] for (stored, _rel), rec in self.originals.items() if stored == str(root)},
            stages=[row for row in self.stages if row["role"] == self._role(root)])
        dest = self._dir(root) / "MANIFEST.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(body, indent=2) + "\n")

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
                rec["path"].write_bytes(sealed)
                os.chmod(rec["path"], 0o444)
            plan.append((rec, sealed))
        for rec, sealed in plan:
            dest = Path(rec["root"]) / rec["rel"]
            if dest.is_symlink():
                raise ReleaseError("REFUSED_SYMLINK")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(sealed)
            if file_sha(dest) != rec["sha"]:
                raise ReleaseError("REFUSED_BACKUP")
        return self.integrity_ok


def _put(root, rel, data):
    dest = safe_dest(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".thfrel.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def restart_dash(unit, runner=None):
    if unit != ALLOW["dash_restart_unit"]:
        raise ReleaseError("REFUSED_RESTART")
    if unit in ALLOW["do_not_restart"]:
        raise ReleaseError("REFUSED_RESTART")
    if runner is None:
        import subprocess
        subprocess.run(["systemctl", "restart", unit], check=True)
        return unit
    return runner(unit)


def apply_release(approval_path, archives, probe=None, host_name=None, roots=None, restart=None, restarter=None):
    if (host_name is not None or roots is not None) and not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_OVERRIDE")
    approval = load_approval(approval_path)
    blobs = check_archives(archives)
    host = host_name if host_name is not None else socket.gethostname()
    if host != HOST:
        raise ReleaseError("REFUSED_HOST")
    pair = tuple(roots) if roots is not None else (LIVE, DASH)
    if roots is None and pair != (LIVE, DASH):
        raise ReleaseError("REFUSED_ROOT")
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ReleaseError("REFUSED_ROOT")
    if roots is None:
        for root in pair:
            text = str(Path(root).resolve())
            if text not in (LIVE, DASH) and not (text == LIVE or text == DASH):
                raise ReleaseError("REFUSED_ROOT")
    probe = probe or systemctl_is_active
    services_idle(probe)
    live, dash = pair
    fd, pid_path = _hold(live)
    txn = Transaction(pair)
    try:
        services_idle(probe)
        for spec in sorted(ALLOW["archives"], key=lambda row: row["order"]):
            blob = blobs[spec["name"]]
            for item in spec["files"]:
                rel = item["path"]
                if rel in ALLOW["excluded"]:
                    raise ReleaseError("REFUSED_BOOK")
                hashes = []
                for root in pair:
                    dest = safe_dest(root, rel)
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
                services_idle(probe)
                raw = member(blob, rel)
                if sha256(raw) != item["target"]:
                    raise ReleaseError("REFUSED_PAYLOAD")
                for root in pair:
                    txn.seal(root, rel)
                    txn.stage(root, rel, spec["name"], spec["order"])
                    txn.write_manifest(root)
                    _put(root, rel, raw)
                    txn.writes += 1
                    if file_sha(Path(root) / rel) != item["target"]:
                        raise ReleaseError("REFUSED_VERIFY")
        for pin in ALLOW["verify_only"]:
            for root in pair:
                dest = Path(root) / pin["path"]
                if dest.is_symlink() or not dest.is_file() or file_sha(dest) != pin["sha256"]:
                    raise ReleaseError("REFUSED_PIN")
                cur = Path(root).resolve()
                for part in Path(pin["path"]).parts:
                    cur = cur / part
                    if cur.is_symlink():
                        raise ReleaseError("REFUSED_SYMLINK")
        final = {}
        for spec in sorted(ALLOW["archives"], key=lambda row: row["order"]):
            for item in spec["files"]:
                final[item["path"]] = item["target"]
        for root in pair:
            for rel, digest in final.items():
                if file_sha(Path(root) / rel) != digest:
                    raise ReleaseError("REFUSED_VERIFY")
            if file_sha(Path(root) / "veteran_desk/book.py") != ALLOW["book_pin"]:
                raise ReleaseError("REFUSED_BOOK")
            txn.write_manifest(root)
        receipt = _receipt(approval, host, txn, deployed=True, restored=False)
        _write_receipt(pair, receipt)
        if restart == "dash":
            restart_dash(ALLOW["dash_restart_unit"], restarter)
            receipt["dash_restarted"] = True
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
        release="thf_release_20261107", deployed=bool(deployed), production=bool(deployed),
        restored=bool(restored), backup_integrity=bool(txn.integrity_ok), txn=txn.id,
        authorization_id=approval["authorization_id"],
        authorization_verbatim=approval["authorization_verbatim"],
        archives=[{"name": row["name"], "sha256": row["sha256"]} for row in ALLOW["archives"]],
        originals=[{"role": rec["role"], "path": rec["rel"], "sha256": rec["sha"]} for rec in txn.originals.values()],
        stages=[{k: row[k] for k in ("archive", "order", "role", "path", "before_sha256")} for row in txn.stages],
        book_pin=ALLOW["book_pin"], tick_restarted=False, engine_restarted=False,
        nginx_changed=False, dash_restarted=False, host=host)
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
    parser = argparse.ArgumentParser(description="Apply the three named THF archives on tfo-sgp1 only.")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--empty-prompt", required=True)
    parser.add_argument("--review-budget", required=True)
    parser.add_argument("--retail-dash", required=True)
    parser.add_argument("--restart-dash", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = apply_release(
            args.approval,
            {
                "empty_prompt_20261107": args.empty_prompt,
                "review_budget_20261107": args.review_budget,
                "retail_dash_20261107": args.retail_dash,
            },
            restart="dash" if args.restart_dash else None)
    except ReleaseError as error:
        print(error.code)
        print("NOT_DEPLOYED")
        return 2
    print("RELEASED")
    print(receipt["archives"][-1]["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
