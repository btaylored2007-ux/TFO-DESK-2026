#!/usr/bin/env python3
"""Unsigned source controller for the shadow-throughput pair.

The shipped example is not an operator record. A complete record calls
apply_bound; this file does not stop a timer, restart a service, or write
codemerge. deploy/shadow_throughput_prod_20261107 stays the immutable review.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
import secrets
import socket
import subprocess
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW = json.loads((HERE / "ALLOWLIST.json").read_text())
ORDER = tuple(row["path"] for row in ALLOW["files"])
FILES = {row["path"]: row for row in ALLOW["files"]}
PROTECTED = {row["path"]: row["sha256"] for row in ALLOW["protected"]}
OWNER = dict(ALLOW["unchanged_owner_targets"])
BACKUP = "state/thf/shadow_throughput_src_20261107_backups"
RECEIPTS = "state/thf/shadow_throughput_src_20261107_receipts"
QUOTE_ROLES = ("actual_words", "coordinator_interpretation")


class ReleaseError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha256(Path(path).read_bytes())


def validate_contract(body):
    if body.get("example") is not True or body.get("ready") is not False or body.get("signed") is not False:
        raise ReleaseError("REFUSED_CONTRACT")
    if body.get("production_approval") is not False:
        raise ReleaseError("REFUSED_CONTRACT")
    if body.get("signed_means") != "operator_record_not_cryptographic_signature":
        raise ReleaseError("REFUSED_CONTRACT")
    if list(body.get("roots") or []) != list(ALLOW["roots"]):
        raise ReleaseError("REFUSED_CONTRACT")
    if "/opt/tfo/dash/codemerge" not in list(body.get("not_roots") or []):
        raise ReleaseError("REFUSED_CONTRACT")
    got = {(row.get("path"), row.get("base_sha256"), row.get("target_sha256")) for row in body.get("files") or []}
    need = {(row["path"], row["base_sha256"], row["target_sha256"]) for row in ALLOW["files"]}
    if got != need or body.get("package_sha256") != ALLOW["package_sha256"]:
        raise ReleaseError("REFUSED_CONTRACT")
    pre = body.get("preflight") or {}
    if pre.get("sha256") != ALLOW["preflight_sha256"] or pre.get("status") != "captured":
        raise ReleaseError("REFUSED_CONTRACT")
    refresh = body.get("refresh") or {}
    if refresh.get("script_sha256") != ALLOW["refresh_script_sha256"]:
        raise ReleaseError("REFUSED_CONTRACT")
    if refresh.get("report_sha256") != ALLOW["refresh_report_sha256"]:
        raise ReleaseError("REFUSED_CONTRACT")
    if "LIVE -> CODEMERGE" not in str(refresh.get("order")) or "exit 1" not in str(refresh.get("order")):
        raise ReleaseError("REFUSED_CONTRACT")
    if refresh.get("sampler_bench_excluded") is not False:
        raise ReleaseError("REFUSED_CONTRACT")
    race = body.get("race") or {}
    if "lesson_trace" not in str(race.get("import_order")) or "does not install" not in str(race.get("residual_authority")):
        raise ReleaseError("REFUSED_CONTRACT")
    record = body.get("operator_record") or {}
    if record.get("scope_ref") != ALLOW["scope_ref"] or record.get("not_accepted_here") is not True:
        raise ReleaseError("REFUSED_CONTRACT")
    return body


def load_contract():
    return validate_contract(json.loads((HERE / "CONTRACT.json").read_text()))


def load_approval(path):
    load_contract()
    body = json.loads(Path(path).read_text())
    if body.get("example") is True or body.get("signed") is not True or body.get("ready") is not True:
        raise ReleaseError("REFUSED_UNSIGNED")
    if body.get("signed_means") != "operator_record_not_cryptographic_signature":
        raise ReleaseError("REFUSED_UNSIGNED")
    provenance = body.get("provenance") or {}
    role = provenance.get("quote_role")
    if role not in QUOTE_ROLES or provenance.get("not_a_new_user_approval") is not True:
        raise ReleaseError("REFUSED_UNSIGNED")
    if role == "coordinator_interpretation":
        note = str(provenance.get("coordinator_note") or "")
        if "interpretation" not in note.lower():
            raise ReleaseError("REFUSED_UNSIGNED")
    ident = str(body.get("authorization_id") or "")
    words = str(body.get("authorization_verbatim") or "")
    if not ident or not words or body.get("scope_ref") != ALLOW["scope_ref"]:
        raise ReleaseError("REFUSED_UNSIGNED")
    if ident in ALLOW["rejected_authorization_ids"] or "supergrok" in ident.lower() or "supergrok" in words.lower():
        raise ReleaseError("REFUSED_REUSED_APPROVAL")
    pre = body.get("preflight") or {}
    if str(pre.get("sha256") or "") != ALLOW["preflight_sha256"] or pre.get("status") != "captured":
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
            for rel in ORDER:
                if name.endswith("payload/" + rel) and item.isfile():
                    extracted = tar.extractfile(item)
                    found[rel] = extracted.read() if extracted else b""
    if set(found) != set(ORDER):
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
    if digest not in (FILES[rel]["base_sha256"], FILES[rel]["target_sha256"]):
        raise ReleaseError("REFUSED_BASE")
    return data, digest, _mode(path)


def _expect_modes(root):
    for rel, text in ALLOW["expected_modes"].items():
        if _mode(Path(root) / rel) != int(text, 8):
            raise ReleaseError("REFUSED_MODE")


def _expect_preserved(root):
    for rel, digest in ALLOW["preserve_bytes"].items():
        path = Path(root) / rel
        if path.is_symlink() or not path.is_file() or file_sha(path) != digest:
            raise ReleaseError("REFUSED_PRESERVE")


def _expect_hashes(root, wanted):
    for rel, digest in wanted.items():
        path = Path(root) / rel
        if path.is_symlink() or not path.is_file() or file_sha(path) != digest:
            raise ReleaseError("REFUSED_PROTECTED")


def verify_protected(root):
    _expect_hashes(root, PROTECTED)


def verify_owner_targets(root):
    try:
        _expect_hashes(root, OWNER)
    except ReleaseError:
        raise ReleaseError("REFUSED_OWNER_TARGET")


def timer_is_far(show_text, now_us, bound_s=None):
    bound = ALLOW["timer_bound_s"] if bound_s is None else bound_s
    value = ""
    for line in str(show_text).splitlines():
        if line.startswith("NextElapseUSecRealtime="):
            value = line.split("=", 1)[1].strip()
    if not value or value in ("0", "n/a"):
        raise ReleaseError("REFUSED_TIMER_NEAR")
    try:
        due = int(value)
    except ValueError:
        raise ReleaseError("REFUSED_TIMER_NEAR")
    if due - int(now_us) < int(bound) * 1000000:
        raise ReleaseError("REFUSED_TIMER_NEAR")
    return True


def _require_timer_far():
    try:
        show = subprocess.run(
            ["systemctl", "show", ALLOW["idle_timer"], "-p", "NextElapseUSecRealtime", "--no-pager"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError("REFUSED_TIMER_NEAR")
    if show.returncode != 0:
        raise ReleaseError("REFUSED_TIMER_NEAR")
    timer_is_far(show.stdout, int(time.time() * 1000000))


def _sampler_in_proc():
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"thf_shadow_sampler" in command:
            return True
    return False


def shadow_idle(unit):
    """Read-only idle check. This does not start, stop, or restart the unit."""
    try:
        show = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "--no-pager"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "active"
    if show.returncode != 0:
        return "active"
    fields = {}
    for line in (show.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value.strip()
    if fields.get("ActiveState") not in ("inactive", "failed"):
        return "active"
    if fields.get("SubState") not in ("dead", "failed"):
        return "active"
    if fields.get("MainPID") not in ("", "0"):
        return "active"
    if _sampler_in_proc():
        return "active"
    return "inactive"


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
                pass
            if owner > 1:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                raise ReleaseError("REFUSED_LOCK")
    pid_path.write_text("%s shadow_throughput_src_20261107\n" % os.getpid())
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


def _seal(root, rel, data, txn):
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


def _stage(dest, data, mode):
    temporary = dest.with_name(dest.name + ".pub.tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    if temporary.read_bytes() != data:
        temporary.unlink()
        raise ReleaseError("REFUSED_WRITE")
    return temporary


def _commit_pair(pairs):
    os.replace(pairs[0][0], pairs[0][1])
    os.replace(pairs[1][0], pairs[1][1])


def _restore_one(dest, backup, expected, mode):
    if not backup.is_file() or backup.read_bytes() != expected:
        return False
    temporary = dest.with_name(dest.name + ".restore.tmp")
    temporary.write_bytes(expected)
    os.replace(temporary, dest)
    os.chmod(dest, mode)
    return dest.read_bytes() == expected and (_mode(dest) == mode)


def _receipt_path(root, ident):
    name = sha256(ident.encode())[:24] + ".json"
    return Path(root) / RECEIPTS / name


def _claim(path, body):
    if path.exists():
        raise ReleaseError("REFUSED_RERUN")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=2) + "\n"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise ReleaseError("REFUSED_RERUN")
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    if path.read_text() != text:
        raise ReleaseError("REFUSED_RECEIPT")


def _write_json(path, body):
    text = json.dumps(body, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text)
        os.replace(temporary, path)
    except OSError as exc:
        raise ReleaseError("REFUSED_RECEIPT") from exc
    try:
        if path.read_text() != text:
            raise ReleaseError("REFUSED_RECEIPT")
    except OSError as exc:
        raise ReleaseError("REFUSED_RECEIPT") from exc
    return text


def _find_receipt(root, txn):
    folder = Path(root) / RECEIPTS
    if not folder.is_dir():
        return None
    for path in folder.glob("*.json"):
        try:
            body = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if body.get("txn") == txn:
            return path, body
    return None


def _mark(root, txn, **fields):
    found = _find_receipt(root, txn)
    if not found:
        return
    path, body = found
    body.update(fields)
    text = json.dumps(body, indent=2) + "\n"
    temporary = path.with_name(path.name + ".mark.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _idle(probe):
    if probe(ALLOW["idle_unit"]) not in ("inactive", "dead"):
        raise ReleaseError("REFUSED_SHADOW_ACTIVE")


def _publish_root(root, blobs, originals, probe):
    pairs = []
    for rel in ORDER:
        dest = Path(root) / rel
        pairs.append((_stage(dest, blobs[rel], originals[(root, rel)][1]), dest))
    _idle(probe)
    _commit_pair(pairs)
    for rel in ORDER:
        dest = Path(root) / rel
        if file_sha(dest) != FILES[rel]["target_sha256"] or _mode(dest) != originals[(root, rel)][1]:
            raise ReleaseError("REFUSED_WRITE")


def _receipt_body(record, txn, roots, sealed, completed):
    production = completed and record.get("production_approval") is True
    return dict(
        release=ALLOW["release"], txn=txn, authorization_id=record["authorization_id"],
        scope_ref=record["scope_ref"], quote_role=record["provenance"]["quote_role"],
        production=production, deployed=production, review_only=not production,
        transaction_completed=completed, targets_match=completed, backups_verified=True,
        rollback_failed=False, rolled_back=False,
        explicit_service_action=False, timer_stopped=False, codemerge_write=False,
        dash_thf_restart_possible_via_code_md5_watcher=True, loaded_sha_proven=False,
        preflight_sha256=record["preflight"]["sha256"], package_sha256=ALLOW["package_sha256"],
        refresh_script_sha256=ALLOW["refresh_script_sha256"],
        roots=[str(root) for root in roots],
        modes={str(root): {rel: mode for (sealed_root, rel, _path, _data, mode) in sealed if sealed_root == root} for root in roots},
        pair_commit="sampler_then_bench",
    )


def apply_bound(approval, package, roots, probe=None, drill=False):
    """Replace live and dash, or restore both. Codemerge is not a root."""
    if drill and not os.environ.get("PYTEST_CURRENT_TEST"):
        raise ReleaseError("REFUSED_DRILL")
    if probe is None:
        probe = shadow_idle
    record = load_approval(approval)
    check_package(package)
    blobs = members(package)
    if not drill:
        if socket.gethostname() != ALLOW["host"]:
            raise ReleaseError("REFUSED_HOST")
        if record.get("production_approval") is not True:
            raise ReleaseError("REFUSED_APPROVAL")
        if list(roots) != list(ALLOW["roots"]):
            raise ReleaseError("REFUSED_ROOT")
        _require_timer_far()
        for root in roots:
            verify_protected(root)
            verify_owner_targets(root)
            _expect_modes(root)
            _expect_preserved(root)
    _idle(probe)
    for root in roots:
        if _receipt_path(root, record["authorization_id"]).exists():
            raise ReleaseError("REFUSED_RERUN")
    live = roots[0]
    fd, pid_path = _hold(live)
    txn = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
    sealed = []
    claimed = False
    try:
        _idle(probe)
        originals = {}
        for root in roots:
            for rel in ORDER:
                data, digest, mode = _read_pair(root, rel)
                if digest != FILES[rel]["base_sha256"]:
                    raise ReleaseError("REFUSED_BASE")
                originals[(root, rel)] = (data, mode)
        for root in roots:
            for rel in ORDER:
                data, mode = originals[(root, rel)]
                path = _seal(root, rel, data, txn)
                sealed.append((root, rel, path, data, mode))
        started = _receipt_body(record, txn, roots, sealed, False)
        for root in roots:
            _claim(_receipt_path(root, record["authorization_id"]), started)
        claimed = True
        for root in roots:
            _publish_root(root, blobs, originals, probe)
            _idle(probe)
        for _root, _rel, path, data, _mode in sealed:
            if path.read_bytes() != data:
                raise ReleaseError("REFUSED_BACKUP")
        if not drill:
            for root in roots:
                _expect_preserved(root)
        final = _receipt_body(record, txn, roots, sealed, True)
        for root in roots:
            _write_json(_receipt_path(root, record["authorization_id"]), final)
        return final
    except Exception as exc:
        failed = []
        for root, rel, path, data, mode in reversed(sealed):
            if not _restore_one(Path(root) / rel, path, data, mode):
                failed.append("%s/%s" % (root, rel))
        for root in roots:
            for rel in ORDER:
                temporary = (Path(root) / rel).with_name(rel.split("/")[-1] + ".pub.tmp")
                if temporary.exists():
                    temporary.unlink()
        if claimed and not failed:
            for root in roots:
                try:
                    _mark(root, txn, transaction_completed=False, targets_match=False,
                          production=False, deployed=False, review_only=True, rolled_back=True,
                          rollback_failed=False)
                except (OSError, ReleaseError):
                    failed.append("receipt")
        if failed:
            for root in roots:
                try:
                    _mark(root, txn, transaction_completed=False, targets_match=False,
                          production=False, deployed=False, review_only=True,
                          rolled_back=False, rollback_failed=True)
                except (OSError, ReleaseError):
                    pass
            raise ReleaseError("ROLLBACK_FAILED") from exc
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError("REFUSED_RECEIPT") from exc
    finally:
        _unlock(fd, pid_path)


def rollback_txn(roots, txn):
    planned = []
    for root in roots:
        found = _find_receipt(root, txn)
        if not found:
            raise ReleaseError("REFUSED_ROLLBACK")
        _path, receipt = found
        if receipt.get("backups_verified") is not True:
            raise ReleaseError("REFUSED_ROLLBACK")
        for rel, row in FILES.items():
            dest = Path(root) / rel
            backup = Path(root) / BACKUP / txn / "original" / rel
            expected = backup.read_bytes() if backup.is_file() else b""
            mode = int((receipt.get("modes") or {}).get(str(root), {}).get(rel, 0o644))
            if sha256(expected) != row["base_sha256"]:
                raise ReleaseError("ROLLBACK_FAILED")
            planned.append((dest, backup, expected, mode))
    restored = []
    for dest, backup, expected, mode in planned:
        if not _restore_one(dest, backup, expected, mode) or dest.read_bytes() != expected:
            raise ReleaseError("ROLLBACK_FAILED")
        restored.append(str(dest))
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
        record = load_approval(args.approval)
        check_package(Path(args.package).read_bytes())
        if record.get("production_approval") is not True:
            raise ReleaseError("REFUSED_APPROVAL")
        receipt = apply_bound(args.approval, Path(args.package).read_bytes(), [Path(root) for root in ALLOW["roots"]], probe=shadow_idle, drill=False)
    except ReleaseError as exc:
        print(json.dumps(dict(ready=False, production=False, deployed=False, error=exc.code)))
        return 2
    print(json.dumps(dict(ready=True, production=receipt["production"], deployed=receipt["deployed"], txn=receipt["txn"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
