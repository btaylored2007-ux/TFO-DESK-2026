#!/usr/bin/env python3
"""Stage the t2808u paper rules. Production roots stay refused. Not one of the three release archives."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text())
BACKUP = "state/thf/paper_rules_20261107_backups"
RECEIPT = "state/thf/paper_rules_20261107_receipt.json"
BLOCKING = {"BLOCKED", "MISSING_PAYLOAD", "ABSENT_REFUSED", "REFUSED_ROOT"}


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
    for banned in ("/opt/tfo/live", "/opt/tfo/dash"):
        if text == banned or text.startswith(banned + "/"):
            return True
    return False


def _row(rel):
    for item in MANIFEST["payloads"]:
        if item["path"] == rel:
            return item
    return None


def plan(live_root):
    live = Path(live_root)
    if production_root(live):
        return [dict(path=str(live), state="REFUSED_ROOT", sha256=None)]
    rows = []
    allowed = {item["path"] for item in MANIFEST["payloads"]}
    if allowed != {"veteran_desk/book.py", "veteran_desk/gate_v2.py"}:
        return [dict(path="manifest", state="BLOCKED", sha256=None)]
    for item in MANIFEST["payloads"]:
        rel = item["path"]
        src = ROOT / rel
        payload = sha256(src) if src.is_file() else None
        if payload != item["target_sha256"]:
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=payload))
            continue
        if b"RAIL_BANK_R = 0.7" not in (ROOT / "veteran_desk/book.py").read_bytes():
            rows.append(dict(path=rel, state="BLOCKED", sha256=payload))
            continue
        dest = live / rel
        if not dest.is_file():
            rows.append(dict(path=rel, state="ABSENT_REFUSED", sha256=None))
            continue
        current = sha256(dest)
        if current == payload:
            state = "CURRENT"
        elif current == item["base_sha256"]:
            state = "REPLACE"
        else:
            state = "BLOCKED"
        rows.append(dict(path=rel, state=state, sha256=current))
    return rows


def _restore(live, rel):
    dest = live / rel
    backup = live / BACKUP / rel
    if not backup.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, dest)
    return True


def apply_plan(live_root, rows):
    live = Path(live_root)
    apply_plan.last_error = None
    if any(row["state"] in BLOCKING for row in rows):
        return False
    done = []
    replaced = []
    rel = None
    try:
        for row in rows:
            if row["state"] != "REPLACE":
                continue
            rel = row["path"]
            item = _row(rel)
            dest = live / rel
            backup = live / BACKUP / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
            if sha256(backup) != item["base_sha256"]:
                raise RuntimeError("backup mismatch")
            try:
                shutil.copy2(ROOT / rel, dest)
                if sha256(dest) != item["target_sha256"]:
                    raise RuntimeError("target mismatch")
            except Exception:
                if not _restore(live, rel):
                    raise RuntimeError("restore failed")
                raise
            done.append(rel)
            replaced.append(rel)
    except Exception as error:
        for name in reversed(done):
            _restore(live, name)
        apply_plan.last_error = "FAILED_COPY %s: %s" % (rel, error)
        return False
    if not replaced and not any(row["state"] == "CURRENT" for row in rows):
        return False
    receipt = live / RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(
        package="paper_rules_20261107", replaced=replaced, production=False, deployed=False,
        live_book_pin=MANIFEST["live_book_pin"],
        not_in_archives=MANIFEST["excluded_from_release"]), indent=2) + "\n")
    return True


def rollback(live_root):
    live = Path(live_root)
    if production_root(live):
        return False
    receipt_path = live / RECEIPT
    if not receipt_path.is_file():
        return False
    receipt = json.loads(receipt_path.read_text())
    for rel in receipt.get("replaced") or []:
        item = _row(rel)
        backup = live / BACKUP / rel
        if not backup.is_file() or sha256(backup) != item["base_sha256"]:
            return False
    for rel in receipt.get("replaced") or []:
        if not _restore(live, rel) or sha256(live / rel) != _row(rel)["base_sha256"]:
            return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage the paper-rule delta. Refuses production roots.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if production_root(args.root):
        print("REFUSED_ROOT")
        print("NOT_DEPLOYED")
        return 2
    if args.rollback:
        if not args.staging:
            print("STAGING_REQUIRED")
            print("NOT_DEPLOYED")
            return 2
        ok = rollback(args.root)
        print("ROLLED_BACK" if ok else "ROLLBACK_REFUSED")
        print("NOT_DEPLOYED")
        return 0 if ok else 3
    rows = plan(args.root)
    blocked = any(row["state"] in BLOCKING for row in rows)
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
    if args.apply:
        apply_plan.last_error = None
        if not args.staging or blocked or not apply_plan(args.root, rows):
            print("APPLY_REFUSED")
            if apply_plan.last_error:
                print(apply_plan.last_error)
            print("NOT_DEPLOYED")
            return 2
        print("STAGED")
    else:
        print("DRY_RUN")
    print("NOT_DEPLOYED")
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
