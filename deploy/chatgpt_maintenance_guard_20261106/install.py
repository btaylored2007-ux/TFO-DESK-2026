#!/usr/bin/env python3
"""Copy the ChatGPT maintenance-guard client. Does not start or restart any service."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BOOK_SHA = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
REL = "veteran_desk/chatgpt_bridge.py"
BASE_SHA = "26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c"
TARGET_SHA = "c477d99674f5d6dd49bd2be60c21d35f8716fbd1f72e790ad7832c2645920098"
BACKUP = "state/thf/chatgpt_maintenance_guard_20261106_backups/" + REL
RECEIPT = "state/thf/chatgpt_maintenance_guard_20261106_receipt.json"
_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan(live_root):
    live = Path(live_root)
    rows = []
    book = live / BOOK
    if book.is_file():
        book_sha = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if book_sha == BOOK_SHA else "BLOCKED",
                         sha256=book_sha, base_sha256=[BOOK_SHA], payload_sha256=None))
    else:
        rows.append(dict(path=BOOK, state="ABSENT", sha256=None, base_sha256=[BOOK_SHA], payload_sha256=None))
    src = ROOT / REL
    dest = live / REL
    if not src.is_file() or sha256(src) != TARGET_SHA:
        rows.append(dict(path=REL, state="MISSING_PAYLOAD", sha256=sha256(src) if src.is_file() else None,
                         base_sha256=[BASE_SHA], payload_sha256=None))
        return rows
    if not dest.exists():
        state, digest = "INSTALL", None
    else:
        digest = sha256(dest)
        state = "CURRENT" if digest == TARGET_SHA else "REPLACE" if digest == BASE_SHA else "BLOCKED"
    rows.append(dict(path=REL, state=state, sha256=digest, base_sha256=[BASE_SHA], payload_sha256=TARGET_SHA))
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
            backup_rel = BACKUP
            backup = live / backup_rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
            if sha256(backup) != BASE_SHA:
                return False
        shutil.copy2(src, dest)
        if sha256(dest) != TARGET_SHA:
            return False
        copied.append(dict(path=row["path"], backup=backup_rel, base_sha256=BASE_SHA, payload_sha256=TARGET_SHA))
    book = live / BOOK
    if book.is_file() and sha256(book) != BOOK_SHA:
        return False
    receipt = live / RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(copied=copied, book_sha256=BOOK_SHA if book.is_file() else None,
                                       base_sha256=BASE_SHA, payload_sha256=TARGET_SHA), indent=2))
    return True


def rollback(live_root):
    live = Path(live_root)
    receipt_path = live / RECEIPT
    if not receipt_path.is_file():
        print("ROLLBACK_NO_RECEIPT")
        return 2
    receipt = json.loads(receipt_path.read_text())
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        if not dest.is_file() or sha256(dest) != row["payload_sha256"]:
            print("ROLLBACK_BLOCKED %s" % row["path"])
            return 2
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        backup = live / row["backup"] if row.get("backup") else None
        if backup and backup.is_file():
            shutil.copy2(backup, dest)
        elif dest.is_file():
            dest.unlink()
        if row.get("backup") and sha256(dest) != BASE_SHA:
            print("ROLLBACK_BASE_MISMATCH")
            return 2
    book = live / BOOK
    if book.is_file() and sha256(book) != BOOK_SHA:
        print("ROLLBACK_BOOK_HASH_UNEXPECTED")
        return 2
    print("ROLLBACK_RESTORED")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the ChatGPT maintenance-guard client.")
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
    print("NOT_DEPLOYED no service is restarted by this installer")
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
