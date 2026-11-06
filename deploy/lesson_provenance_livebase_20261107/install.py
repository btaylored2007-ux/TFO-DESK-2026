#!/usr/bin/env python3
"""Stage the live-based lesson provenance files. Does not start a service or write production trees."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BACKUP = "state/thf/lesson_provenance_livebase_20261107_backups"
RECEIPT = "state/thf/lesson_provenance_livebase_20261107_receipt.json"
_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text())
BOOK_SHA = MANIFEST["book_must_remain"]
BLOCKING = {"BLOCKED", "MISSING_PAYLOAD", "ABSENT_REFUSED", "BOOK_MISMATCH", "BOOK_ABSENT", "REFUSED_ROOT"}


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


def plan(live_root):
    live = Path(live_root)
    rows = []
    if production_root(live):
        rows.append(dict(path=str(live), state="REFUSED_ROOT", sha256=None))
        return rows
    book = live / BOOK
    if not book.is_file():
        rows.append(dict(path=BOOK, state="BOOK_ABSENT", sha256=None))
    else:
        digest = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if digest == BOOK_SHA else "BOOK_MISMATCH", sha256=digest))
    for item in MANIFEST["payloads"]:
        rel = item["path"]
        src = ROOT / rel
        payload = sha256(src) if src.is_file() else None
        if payload != item["target_sha256"]:
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=payload))
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
    for item in MANIFEST["not_copied"]:
        rows.append(dict(path=item["path"], state="UNCLOSED_DEPENDENCY", sha256=None))
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
    if any(row["state"] in BLOCKING for row in rows):
        return False
    copied = []
    for row in rows:
        if row["state"] != "REPLACE":
            continue
        rel = row["path"]
        item = next(entry for entry in MANIFEST["payloads"] if entry["path"] == rel)
        dest = live / rel
        backup = live / BACKUP / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, backup)
        if sha256(backup) != item["base_sha256"]:
            return False
        shutil.copy2(ROOT / rel, dest)
        if sha256(dest) != item["target_sha256"]:
            _restore(live, rel)
            return False
        copied.append(rel)
    if not copied and not any(row["state"] == "CURRENT" for row in rows):
        return False
    receipt = live / RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(copied=copied, feed_copied=False, production=False), indent=2) + "\n")
    return True


def rollback(live_root):
    live = Path(live_root)
    if production_root(live):
        return False
    restored = []
    for item in MANIFEST["payloads"]:
        rel = item["path"]
        dest = live / rel
        backup = live / BACKUP / rel
        if not dest.is_file() or not backup.is_file():
            return False
        if sha256(backup) != item["base_sha256"]:
            return False
        shutil.copy2(backup, dest)
        if sha256(dest) != item["base_sha256"]:
            return False
        restored.append(rel)
    feed = live / MANIFEST["not_copied"][0]["path"]
    if feed.exists():
        return False
    return len(restored) == len(MANIFEST["payloads"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the live-based lesson provenance package.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.rollback:
        print("REFUSED")
        return 2
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
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["state"] in BLOCKING:
            blocked = True
    if args.apply:
        if not args.staging or blocked or not apply_plan(args.root, rows):
            print("APPLY_REFUSED")
            print("NOT_DEPLOYED")
            return 2
        print("STAGED")
    else:
        print("DRY_RUN")
    print("FEED_NOT_COPIED")
    print("NOT_DEPLOYED")
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
