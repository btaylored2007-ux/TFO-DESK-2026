#!/usr/bin/env python3
"""Bind the paper dashboard to loopback unless THF_DASH_BIND is set. Does not touch nginx or the book."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BOOK_SHA = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
PAYLOADS = ("veteran_desk/thf_dash.py",)
BASE_SHA = {
    # Last captured live file, and the repo file before this bind change.
    "veteran_desk/thf_dash.py": (
        "e77d367e97f6a149ef0890f49262d0599753e6fac85efceaeb944158d133e745",
        "310f395d2c8b9440d67be67a38549956a33ba971b4998836511a25ed0a51247e",
    ),
}
_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(src, dest, bases):
    if not src.is_file():
        return "MISSING_PAYLOAD", None
    payload = sha256(src)
    if not dest.exists():
        return "INSTALL", payload
    current = sha256(dest)
    if current == payload:
        return "CURRENT", current
    if current in bases:
        return "REPLACE", current
    return "BLOCKED", current


def plan(live_root):
    live = Path(live_root)
    rows = []
    book = live / BOOK
    if book.is_file():
        book_sha = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if book_sha == BOOK_SHA else "BLOCKED",
                         sha256=book_sha, base_sha256=[BOOK_SHA], payload_sha256=None))
    for rel in PAYLOADS:
        src = ROOT / rel
        state, digest = classify(src, live / rel, BASE_SHA.get(rel, ()))
        rows.append(dict(path=rel, state=state, sha256=digest, base_sha256=list(BASE_SHA.get(rel, ())),
                         payload_sha256=sha256(src) if src.is_file() else None))
    return rows


def apply_plan(live_root, rows):
    live = Path(live_root)
    if any(row["state"] in ("BLOCKED", "MISSING_PAYLOAD") for row in rows):
        return False
    copied = []
    for row in rows:
        if row["state"] not in ("INSTALL", "REPLACE"):
            continue
        src = ROOT / row["path"]
        dest = live / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        backup_rel = None
        if dest.exists():
            backup_rel = "state/thf/dash_bind_20261106_backups/" + row["path"]
            backup = live / backup_rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
        shutil.copy2(src, dest)
        if sha256(dest) != sha256(src):
            return False
        copied.append(dict(path=row["path"], backup=backup_rel, payload_sha256=sha256(dest)))
    receipt = live / "state/thf/dash_bind_20261106_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(copied=copied, book_sha256=BOOK_SHA), indent=2))
    return True


def rollback(live_root):
    live = Path(live_root)
    receipt_path = live / "state/thf/dash_bind_20261106_receipt.json"
    if not receipt_path.is_file():
        print("ROLLBACK_NO_RECEIPT")
        return 2
    receipt = json.loads(receipt_path.read_text())
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        if dest.is_file() and sha256(dest) != row["payload_sha256"]:
            print("ROLLBACK_BLOCKED %s" % row["path"])
            return 2
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        backup = live / row["backup"] if row.get("backup") else None
        if backup and backup.is_file():
            shutil.copy2(backup, dest)
        elif dest.is_file():
            dest.unlink()
    book = live / BOOK
    if book.is_file() and sha256(book) != BOOK_SHA:
        print("ROLLBACK_BOOK_HASH_UNEXPECTED")
        return 2
    print("ROLLBACK_RESTORED")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the loopback dashboard bind.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if args.rollback:
        return rollback(args.root)
    rows = plan(args.root)
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["base_sha256"]:
            print("BASE %s %s" % (row["path"], ",".join(row["base_sha256"])))
        if row["state"] in ("BLOCKED", "MISSING_PAYLOAD"):
            blocked = True
    if args.apply:
        if blocked or not apply_plan(args.root, rows):
            print("APPLY_REFUSED")
            return 2
        print("APPLY_COPIED_INSTALL_ROWS")
        return 0
    print("DRY_RUN")
    print("SERVICE_IMPACT dashboard process only after the operator restarts tfo-dash-thf; nginx and UFW are not changed; 18870 stays the tailnet proxy")
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
