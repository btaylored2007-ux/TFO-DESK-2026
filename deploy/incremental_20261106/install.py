#!/usr/bin/env python3
"""Incremental, base-hash-guarded copy for the 2026-11-06 paper upgrade.

Dry-run is the default. It never talks to a broker and never restarts a service.
The live book is special: sha256 b4453b50… is ALREADY_PROTECTED and is not rewritten.
Any other hash on that path is BLOCKED. Nothing is copied when the book is blocked.

Other files copy only when the destination is missing or already byte-identical.
A different destination hash is BLOCKED so a newer droplet file is not replaced.
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BOOK_SHA = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
PAYLOADS = (
    "veteran_desk/research_mandate.py",
    "veteran_desk/thf_shadow_bench.py",
    "veteran_desk/lesson_trace.py",
    "veteran_desk/feed_health.py",
    "veteran_desk/seat_brain.py",
    "veteran_desk/thf_round.py",
    "veteran_desk/thf_msgdesk.py",
    "veteran_desk/session_expiry.py",
    "veteran_desk/b_tight.py",
    "veteran_desk/bridge_maintenance.py",
    "veteran_desk/thf_http.py",
    "veteran_desk/seat_risk.py",
    "veteran_desk/pit_fx_sgp.py",
    "asia_intraday_sgp.py",
    "thf_feedkeeper.py",
    "tools/intraday_bars_pull.py",
    "tools/thf_chat.js",
    "tools/thf_chat_page.html",
    "tools/thf_chat.html",
    "assets/brand/mark.svg",
    "assets/brand/wordmark.svg",
    "assets/brand/favicon.svg",
    "assets/brand/tokens.css",
)

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(src, dest, book=False):
    if not src.is_file():
        return "MISSING_PAYLOAD", None
    payload = sha256(src)
    if book and payload != BOOK_SHA:
        return "PAYLOAD_HASH_MISMATCH", payload
    if not dest.exists():
        return "INSTALL", payload
    current = sha256(dest)
    if book:
        if current == BOOK_SHA:
            return "ALREADY_PROTECTED", current
        return "BLOCKED", current
    if current == payload:
        return "CURRENT", current
    return "BLOCKED", current


def plan(live_root):
    live = Path(live_root)
    rows = []
    src = ROOT / BOOK
    dest = live / BOOK
    state, digest = classify(src, dest, book=True)
    rows.append(dict(path=BOOK, state=state, sha256=digest))
    for rel in PAYLOADS:
        state, digest = classify(ROOT / rel, live / rel, book=False)
        rows.append(dict(path=rel, state=state, sha256=digest))
    return rows


def apply_plan(live_root, rows):
    live = Path(live_root)
    if any(row["state"] == "BLOCKED" or row["state"].startswith("PAYLOAD") or row["state"] == "MISSING_PAYLOAD" for row in rows):
        return False
    for row in rows:
        if row["state"] != "INSTALL":
            continue
        src = ROOT / row["path"]
        dest = live / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        backup = dest.with_suffix(dest.suffix + ".bak-20261106")
        if dest.exists():
            shutil.copy2(dest, backup)
        shutil.copy2(src, dest)
        if sha256(dest) != sha256(src):
            return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the guarded 2026-11-06 increment.")
    parser.add_argument("--root", required=True, help="Destination tree. Not used against production from this workspace.")
    parser.add_argument("--apply", action="store_true", help="Copy INSTALL rows. Refused when any row is BLOCKED.")
    args = parser.parse_args(argv)
    rows = plan(args.root)
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["state"] in ("BLOCKED", "PAYLOAD_HASH_MISMATCH", "MISSING_PAYLOAD"):
            blocked = True
    if args.apply:
        if blocked or not apply_plan(args.root, rows):
            print("APPLY_REFUSED")
            return 2
        print("APPLY_COPIED_INSTALL_ROWS")
        return 0
    print("DRY_RUN")
    print("BOOK_RUNTIME %s" % next(row["state"] for row in rows if row["path"] == BOOK))
    ready = [row["path"] for row in rows if row["state"] == "INSTALL"]
    print("INSTALL_READY %d" % len(ready))
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
