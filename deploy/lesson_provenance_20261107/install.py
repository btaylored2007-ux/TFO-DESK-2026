#!/usr/bin/env python3
"""Review-only copy of the lesson provenance files. Does not start a service."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BOOK_SHA = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text())


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
        digest = sha256(book)
        rows.append(dict(path=BOOK, state="ALREADY_PROTECTED" if digest == BOOK_SHA else "BLOCKED", sha256=digest))
    for rel, digest in ((row["path"], row["sha256"]) for row in MANIFEST["unchanged_requirements"]):
        dest = live / rel
        if not dest.is_file():
            rows.append(dict(path=rel, state="ABSENT", sha256=None))
            continue
        current = sha256(dest)
        rows.append(dict(path=rel, state="UNCHANGED" if current == digest else "BLOCKED", sha256=current))
    for item in MANIFEST["payloads"]:
        rel = item["path"]
        src = ROOT / rel
        payload = sha256(src) if src.is_file() else None
        if payload != item["target_sha256"]:
            rows.append(dict(path=rel, state="MISSING_PAYLOAD", sha256=payload))
            continue
        dest = live / rel
        if not dest.exists():
            rows.append(dict(path=rel, state="INSTALL", sha256=None))
            continue
        current = sha256(dest)
        state = "CURRENT" if current == payload else "REPLACE" if current == item["base_sha256"] else "BLOCKED"
        rows.append(dict(path=rel, state=state, sha256=current))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the lesson provenance review package.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    rows = plan(args.root)
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["state"] in ("BLOCKED", "MISSING_PAYLOAD"):
            blocked = True
    if args.apply:
        print("APPLY_REFUSED review package is not an activation")
        return 2
    print("DRY_RUN")
    print("NOT_DEPLOYED")
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
