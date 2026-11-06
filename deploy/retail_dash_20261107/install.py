#!/usr/bin/env python3
"""Narrow retail-dashboard release. Dry-run unless --staging --apply.

Does not start a service, does not ssh, and refuses /opt/tfo and any git root.
A capture pin is required before apply. Unknown base hashes stop the run.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BACKUP = "state/thf/retail_dash_20261107_backups"
RECEIPT = "state/thf/retail_dash_20261107_receipt.json"
ROLLBACK_RECEIPT = "state/thf/retail_dash_20261107_rollback.json"
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text(encoding="utf-8"))
BOOK_SHA = MANIFEST["book_must_remain"]
BLOCKING = {
    "BLOCKED", "BASE_MISMATCH", "MISSING_PAYLOAD", "ABSENT_REFUSED",
    "BOOK_MISMATCH", "BOOK_ABSENT", "REFUSED_ROOT", "NEW_FILE_CONFLICT",
    "DEPTEST_MISMATCH", "WAITING_CAPTURE", "PIN_MISMATCH",
}


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


def _item(rel):
    for key in ("replace", "identical_or_absent", "new_files"):
        for item in MANIFEST[key]:
            if item["path"] == rel:
                return item
    return None


def _source_ok(item, field="target_sha256"):
    src = REPO / item["path"]
    if not src.is_file():
        return False
    expect = item.get(field) or item.get("sha256")
    return sha256(src) == expect


def deptest(live):
    rows = []
    for anchor in MANIFEST["anchors"]:
        dest = live / anchor["path"]
        if not dest.is_file():
            rows.append(dict(path=anchor["path"], state="DEPTEST_MISMATCH", sha256=None))
            continue
        text = dest.read_text(encoding="utf-8", errors="replace")
        missing = [needle for needle in anchor["needles"] if needle not in text]
        if missing:
            rows.append(dict(path=anchor["path"], state="DEPTEST_MISMATCH", sha256=sha256(dest), missing=missing))
    return rows


def classify(live):
    rows = []
    book = live / BOOK
    if not book.is_file():
        rows.append(dict(path=BOOK, state="BOOK_ABSENT", sha256=None))
    else:
        digest = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if digest == BOOK_SHA else "BOOK_MISMATCH", sha256=digest))
    rows.extend(deptest(live))
    for item in MANIFEST["replace"]:
        rel = item["path"]
        if not _source_ok(item):
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=None))
            continue
        dest = live / rel
        if not dest.is_file():
            rows.append(dict(path=rel, state="ABSENT_REFUSED", sha256=None))
            continue
        current = sha256(dest)
        if current == item["target_sha256"]:
            state = "CURRENT"
        elif current == item["base_sha256"]:
            state = "REPLACE"
        else:
            state = "BASE_MISMATCH"
        rows.append(dict(path=rel, state=state, sha256=current))
    for item in MANIFEST["identical_or_absent"]:
        rel = item["path"]
        if not _source_ok(item, "sha256"):
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=None))
            continue
        dest = live / rel
        if not dest.exists():
            rows.append(dict(path=rel, state="INSTALL_NEW", sha256=None))
            continue
        current = sha256(dest)
        state = "CURRENT" if current == item["sha256"] else "BLOCKED"
        rows.append(dict(path=rel, state=state, sha256=current))
    for item in MANIFEST["new_files"]:
        rel = item["path"]
        if not _source_ok(item):
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=None))
            continue
        dest = live / rel
        if not dest.exists():
            rows.append(dict(path=rel, state="INSTALL_NEW", sha256=None))
            continue
        current = sha256(dest)
        state = "CURRENT" if current == item["target_sha256"] else "NEW_FILE_CONFLICT"
        rows.append(dict(path=rel, state=state, sha256=current))
    return rows


def capture_id(files):
    body = "\n".join("%s %s" % (path, files[path] or "ABSENT") for path in sorted(files))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def record_capture(live_root, out_path):
    live = Path(live_root)
    rows = classify(live)
    files = {row["path"]: row.get("sha256") for row in rows}
    applyable = not any(row["state"] in BLOCKING for row in rows)
    pin = dict(
        phase="captured",
        status="PINNED" if applyable else "BASE_OR_DEPTEST_MISMATCH",
        applyable=applyable,
        verified_live=False,
        singapore_deployed=False,
        restart="not performed",
        capture_id=capture_id(files),
        files=files,
        states={row["path"]: row["state"] for row in rows},
    )
    dest = Path(out_path)
    if production_root(dest.parent):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    return pin


def pin_matches(live, pin, rows):
    if not pin.get("applyable"):
        return False
    files = pin.get("files") or {}
    for row in rows:
        if row["path"] not in files or files[row["path"]] != row.get("sha256"):
            return False
    book = files.get(BOOK)
    return book == BOOK_SHA and sha256(live / BOOK) == BOOK_SHA


def _undo(live, done):
    for kind, rel in reversed(done):
        dest = live / rel
        backup = live / BACKUP / rel
        if kind == "replace" and backup.is_file():
            shutil.copy2(backup, dest)
        elif kind == "new" and dest.is_file():
            dest.unlink()


def apply_plan(live_root, rows, pin):
    live = Path(live_root)
    if any(row["state"] in BLOCKING for row in rows):
        return False
    if not pin_matches(live, pin, rows):
        return False
    before = {}
    after = {}
    done = []
    try:
        for row in rows:
            if row["state"] not in ("REPLACE", "INSTALL_NEW"):
                continue
            rel = row["path"]
            item = _item(rel)
            dest = live / rel
            src = REPO / rel
            if row["state"] == "REPLACE":
                before[rel] = sha256(dest)
                backup = live / BACKUP / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
                if sha256(backup) != item["base_sha256"]:
                    raise RuntimeError("backup mismatch")
                shutil.copy2(src, dest)
                if sha256(dest) != item["target_sha256"]:
                    raise RuntimeError("target mismatch")
                after[rel] = sha256(dest)
                done.append(("replace", rel))
            else:
                if dest.exists():
                    raise RuntimeError("new file appeared")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                expect = item.get("target_sha256") or item.get("sha256")
                if sha256(dest) != expect:
                    raise RuntimeError("new file mismatch")
                after[rel] = sha256(dest)
                done.append(("new", rel))
    except Exception:
        _undo(live, done)
        return False
    receipt = dict(
        phase="staged",
        status="APPLIED_NOT_DEPLOYED",
        verified_live=False,
        singapore_deployed=False,
        restart="not performed",
        production=False,
        capture_id=pin.get("capture_id"),
        before=before,
        after=after,
        replaced=[rel for kind, rel in done if kind == "replace"],
        installed_new=[rel for kind, rel in done if kind == "new"],
    )
    path = live / RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return True


def rollback(live_root):
    live = Path(live_root)
    if production_root(live):
        return False
    receipt_path = live / RECEIPT
    if not receipt_path.is_file():
        return False
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("verified_live") or receipt.get("singapore_deployed"):
        return False
    before = {}
    after = {}
    for rel in receipt.get("installed_new") or []:
        item = _item(rel)
        dest = live / rel
        expect = item.get("target_sha256") or item.get("sha256")
        if not dest.is_file() or sha256(dest) != expect:
            return False
        before[rel] = sha256(dest)
    for rel in receipt.get("replaced") or []:
        item = _item(rel)
        dest = live / rel
        backup = live / BACKUP / rel
        if not dest.is_file() or sha256(dest) != item["target_sha256"]:
            return False
        if not backup.is_file() or sha256(backup) != item["base_sha256"]:
            return False
        before[rel] = sha256(dest)
    for rel in receipt.get("installed_new") or []:
        (live / rel).unlink()
        after[rel] = None
    for rel in receipt.get("replaced") or []:
        shutil.copy2(live / BACKUP / rel, live / rel)
        after[rel] = sha256(live / rel)
        if after[rel] != _item(rel)["base_sha256"]:
            return False
    note = dict(
        phase="rolled_back",
        status="ROLLED_BACK_NOT_DEPLOYED",
        verified_live=False,
        singapore_deployed=False,
        restart="not performed",
        production=False,
        capture_id=receipt.get("capture_id"),
        before=before,
        after=after,
    )
    (live / ROLLBACK_RECEIPT).write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the narrow retail dashboard package.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--record-capture", action="store_true")
    parser.add_argument("--pin")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.apply and args.rollback:
        print("REFUSED")
        print("NOT_DEPLOYED")
        return 2
    if production_root(args.root):
        print("REFUSED_ROOT")
        print("NOT_DEPLOYED")
        return 2
    if args.record_capture:
        if not args.staging or not args.out:
            print("STAGING_REQUIRED")
            print("NOT_DEPLOYED")
            return 2
        pin = record_capture(args.root, args.out)
        if pin is None:
            print("REFUSED_ROOT")
            print("NOT_DEPLOYED")
            return 2
        print(pin["status"])
        print("CAPTURE %s" % pin["capture_id"])
        print("NOT_DEPLOYED")
        return 0 if pin["applyable"] else 2
    if args.rollback:
        if not args.staging:
            print("STAGING_REQUIRED")
            print("NOT_DEPLOYED")
            return 2
        ok = rollback(args.root)
        print("ROLLED_BACK" if ok else "ROLLBACK_REFUSED")
        print("NOT_DEPLOYED")
        return 0 if ok else 3
    if not args.pin:
        print("WAITING_CAPTURE")
        print("NOT_DEPLOYED")
        return 2
    pin = json.loads(Path(args.pin).read_text(encoding="utf-8"))
    rows = classify(Path(args.root))
    if not pin_matches(Path(args.root), pin, rows):
        rows.append(dict(path=args.pin, state="PIN_MISMATCH", sha256=None))
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["state"] in BLOCKING:
            blocked = True
    if args.apply:
        if not args.staging or blocked or not apply_plan(args.root, rows, pin):
            print("APPLY_REFUSED")
            print("NOT_DEPLOYED")
            return 2
        print("STAGED")
    else:
        print("DRY_RUN")
    print("NOT_DEPLOYED")
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
