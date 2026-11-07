#!/usr/bin/env python3
"""Display-mark read model. Dry-run unless --staging --apply.

Refuses /opt/tfo and any git root. Does not start a service, ssh, or write the book.
Does not create the display mark. That remains the local owner command.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SOURCE = _HERE / "payload"
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text(encoding="utf-8"))
BOOK = "veteran_desk/book.py"
BOOK_SHA = MANIFEST["book_must_remain"]
BACKUP = "state/thf/display_epoch_20261107_backups"
RECEIPT = "state/thf/display_epoch_20261107_receipt.json"
ROLLBACK_RECEIPT = "state/thf/display_epoch_20261107_rollback.json"
BLOCKING = {"BLOCKED", "BASE_MISMATCH", "MISSING_PAYLOAD", "BOOK_MISMATCH", "BOOK_ABSENT", "REFUSED_ROOT", "PIN_MISMATCH"}


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


def classify(live):
    rows = []
    book = live / BOOK
    if not book.is_file():
        rows.append(dict(path=BOOK, state="BOOK_ABSENT", sha256=None))
    else:
        digest = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if digest == BOOK_SHA else "BOOK_MISMATCH", sha256=digest))
    for item in MANIFEST["replace"]:
        dest = live / item["path"]
        src = SOURCE / item["path"]
        if not src.is_file() or sha256(src) != item["target_sha256"]:
            rows.append(dict(path=item["path"], state="MISSING_PAYLOAD", sha256=None))
            continue
        if not dest.is_file():
            rows.append(dict(path=item["path"], state="BASE_MISMATCH", sha256=None))
            continue
        current = sha256(dest)
        if current == item["target_sha256"]:
            state = "CURRENT"
        elif current == item["base_sha256"]:
            state = "REPLACE"
        else:
            state = "BASE_MISMATCH"
        rows.append(dict(path=item["path"], state=state, sha256=current))
    for item in MANIFEST["new_files"]:
        dest = live / item["path"]
        src = SOURCE / item["path"]
        if not src.is_file() or sha256(src) != item["target_sha256"]:
            rows.append(dict(path=item["path"], state="MISSING_PAYLOAD", sha256=None))
            continue
        if dest.exists():
            current = sha256(dest)
            state = "CURRENT" if current == item["target_sha256"] else "BASE_MISMATCH"
            rows.append(dict(path=item["path"], state=state, sha256=current))
        else:
            rows.append(dict(path=item["path"], state="INSTALL_NEW", sha256=None))
    return rows


def _undo(live, done):
    for kind, rel in reversed(done):
        dest = live / rel
        backup = live / BACKUP / rel
        if kind == "replace" and backup.is_file():
            shutil.copy2(backup, dest)
        elif kind == "new" and dest.is_file():
            dest.unlink()


def apply_plan(live, rows):
    if any(row["state"] in BLOCKING for row in rows):
        return False
    before, after, done = {}, {}, []
    try:
        for row in rows:
            if row["state"] not in ("REPLACE", "INSTALL_NEW"):
                continue
            rel = row["path"]
            item = next(entry for group in ("replace", "new_files") for entry in MANIFEST[group] if entry["path"] == rel)
            dest = live / rel
            src = SOURCE / rel
            if row["state"] == "REPLACE":
                before[rel] = sha256(dest)
                backup = live / BACKUP / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
                if sha256(backup) != before[rel]:
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
                if sha256(dest) != item["target_sha256"]:
                    raise RuntimeError("new file mismatch")
                after[rel] = sha256(dest)
                done.append(("new", rel))
    except Exception:
        _undo(live, done)
        return False
    receipt = dict(
        phase="staged", status="APPLIED_NOT_DEPLOYED", verified_live=False,
        singapore_deployed=False, restart="not performed", production=False,
        ledger_rewritten=False, before=before, after=after,
        replaced=[rel for kind, rel in done if kind == "replace"],
        installed_new=[rel for kind, rel in done if kind == "new"],
    )
    path = live / RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return True


def rollback(live):
    if production_root(live):
        return False
    receipt_path = live / RECEIPT
    if not receipt_path.is_file():
        return False
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("verified_live") or receipt.get("singapore_deployed"):
        return False
    book = live / BOOK
    if not book.is_file() or sha256(book) != BOOK_SHA:
        return False
    for rel in receipt.get("replaced") or []:
        item = MANIFEST["replace"][0]
        dest = live / rel
        backup = live / BACKUP / rel
        recorded = (receipt.get("before") or {}).get(rel)
        if not dest.is_file() or sha256(dest) != item["target_sha256"]:
            return False
        if not backup.is_file() or not recorded or sha256(backup) != recorded:
            return False
    for rel in receipt.get("installed_new") or []:
        dest = live / rel
        if not dest.is_file() or sha256(dest) != MANIFEST["new_files"][0]["target_sha256"]:
            return False
    after = {}
    for rel in receipt.get("installed_new") or []:
        (live / rel).unlink()
        after[rel] = None
    for rel in receipt.get("replaced") or []:
        recorded = receipt["before"][rel]
        shutil.copy2(live / BACKUP / rel, live / rel)
        after[rel] = sha256(live / rel)
        if after[rel] != recorded:
            return False
    note = dict(phase="rolled_back", status="ROLLED_BACK_NOT_DEPLOYED", verified_live=False, singapore_deployed=False, restart="not performed", after=after)
    (live / ROLLBACK_RECEIPT).write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage the display-mark read model. Production roots are refused.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--record-capture", action="store_true")
    parser.add_argument("--pin")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if production_root(args.root):
        print("REFUSED_ROOT")
        print("NOT_DEPLOYED")
        return 2
    live = Path(args.root)
    if args.record_capture:
        if not args.staging or not args.out:
            print("STAGING_REQUIRED")
            print("NOT_DEPLOYED")
            return 2
        rows = classify(live)
        applyable = not any(row["state"] in BLOCKING for row in rows)
        pin = dict(applyable=applyable, verified_live=False, singapore_deployed=False, states={row["path"]: row["state"] for row in rows}, files={row["path"]: row.get("sha256") for row in rows})
        Path(args.out).write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
        print("PINNED" if applyable else "BASE_OR_BOOK_MISMATCH")
        print("NOT_DEPLOYED")
        return 0 if applyable else 2
    if args.rollback:
        if not args.staging:
            print("STAGING_REQUIRED")
            print("NOT_DEPLOYED")
            return 2
        print("ROLLED_BACK" if rollback(live) else "ROLLBACK_REFUSED")
        print("NOT_DEPLOYED")
        return 0 if (live / ROLLBACK_RECEIPT).is_file() else 3
    if not args.pin:
        print("WAITING_CAPTURE")
        print("NOT_DEPLOYED")
        return 2
    pin = json.loads(Path(args.pin).read_text(encoding="utf-8"))
    rows = classify(live)
    if not pin.get("applyable") or any(pin.get("files", {}).get(row["path"]) != row.get("sha256") for row in rows):
        print("PIN_MISMATCH")
        print("NOT_DEPLOYED")
        return 2
    if args.apply:
        if not args.staging or not apply_plan(live, rows):
            print("APPLY_REFUSED")
            print("NOT_DEPLOYED")
            return 2
        print("STAGED")
    else:
        for row in rows:
            print("%s %s" % (row["state"], row["path"]))
        print("DRY_RUN")
    print("NOT_DEPLOYED")
    return 2 if any(row["state"] in BLOCKING for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
