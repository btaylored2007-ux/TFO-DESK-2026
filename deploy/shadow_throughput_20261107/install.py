#!/usr/bin/env python3
"""Stage the shadow-throughput scheduler. Production roots stay refused."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE
MANIFEST = json.loads((_HERE / "MANIFEST.json").read_text())
BACKUP = "state/thf/shadow_throughput_20261107_backups"
RECEIPT = "state/thf/shadow_throughput_20261107_receipt.json"
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
    for item in MANIFEST["payloads"]:
        rel = item["path"]
        src = ROOT / "payload" / rel
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
    return rows


def _restore(live, rel):
    dest = live / rel
    backup = live / BACKUP / rel
    if not backup.is_file() or sha256(backup) != _row(rel)["base_sha256"]:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, dest)
    return sha256(dest) == _row(rel)["base_sha256"]


def apply_plan(live_root, rows):
    live = Path(live_root)
    if any(row["state"] in BLOCKING for row in rows):
        return False
    done = []
    try:
        for row in rows:
            if row["state"] != "REPLACE":
                continue
            rel = row["path"]
            item = _row(rel)
            dest = live / rel
            backup = live / BACKUP / rel
            if backup.exists():
                raise RuntimeError("backup exists")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
            if sha256(backup) != item["base_sha256"]:
                raise RuntimeError("backup mismatch")
            shutil.copy2(ROOT / "payload" / rel, dest)
            if sha256(dest) != item["target_sha256"]:
                raise RuntimeError("target mismatch")
            done.append(rel)
        receipt = dict(release=MANIFEST["release"], production=False, deployed=False,
                       replaced=done, write_set=list(MANIFEST["write_set"]))
        path = live / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        return True
    except Exception:
        for rel in reversed(done):
            _restore(live, rel)
        return False


def rollback(live_root):
    live = Path(live_root)
    if production_root(live):
        return False
    ok = True
    for item in MANIFEST["payloads"]:
        ok = _restore(live, item["path"]) and ok
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if production_root(args.root):
        print(json.dumps([dict(path=args.root, state="REFUSED_ROOT")]))
        return 2
    if args.rollback:
        print(json.dumps(dict(rolled_back=rollback(args.root), production=False, deployed=False)))
        return 0
    rows = plan(args.root)
    if args.apply:
        if not args.staging:
            print(json.dumps(dict(applied=False, reason="staging_required")))
            return 2
        print(json.dumps(dict(applied=apply_plan(args.root, rows), rows=rows, production=False, deployed=False)))
        return 0
    print(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
