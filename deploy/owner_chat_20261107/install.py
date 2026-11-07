#!/usr/bin/env python3
"""Owner-chat review installer.

Refuses /opt/tfo and refuses every activation request. --apply writes only the
allowlisted payload onto a non-production review tree whose bases match the
Singapore capture pins. It does not start a service.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
SOURCE = HERE / "payload"
BACKUP = Path("state/thf/owner_chat_20261107_backup")
RECEIPT = Path("state/thf/owner_chat_20261107_receipt.json")
VERIFY = (
    ("tools/chatgpt_web.py", "cc86f0afeef4e26ca609b669356c95c4f4009b9d571dbd710303d8a4a73779a4"),
    ("veteran_desk/chatgpt_bridge.py", "26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c"),
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production(path):
    text = str(Path(path).resolve())
    return text == "/opt/tfo" or text.startswith("/opt/tfo/")


def _rows(root):
    if production(root):
        return ["REFUSED_ROOT"]
    rows = []
    for item in MANIFEST["files"]:
        src = SOURCE / item["path"]
        if not src.is_file() or sha256(src) != item["target"]:
            return ["MISSING_PAYLOAD"]
        dest = Path(root) / item["path"]
        if item.get("new") and not dest.exists():
            rows.append(("INSTALL_NEW", item))
            continue
        if not dest.is_file():
            return ["REFUSED_BASE " + item["path"]]
        current = sha256(dest)
        if current == item["target"]:
            rows.append(("CURRENT", item))
        elif current in item["bases"]:
            rows.append(("REPLACE", item))
        else:
            return ["REFUSED_BASE " + item["path"]]
    for blocked in MANIFEST["do_not_write"]:
        if (SOURCE / blocked).exists():
            return ["REFUSED_SCOPE"]
    for rel, digest in VERIFY:
        dest = Path(root) / rel
        if dest.is_file() and sha256(dest) != digest:
            return ["REFUSED_PIN " + rel]
    return rows


def preflight(root, activate=False):
    if activate:
        return "REFUSED_ACTIVATION"
    rows = _rows(root)
    if rows and isinstance(rows[0], str):
        return rows[0]
    lines = ["%s %s" % (state, item["path"]) for state, item in rows]
    return "\n".join(lines + ["NOT_ACTIVATED", "NOT_DEPLOYED"])


def _protected(root):
    found = {}
    book = Path(root) / "veteran_desk/book.py"
    if book.is_file():
        found["veteran_desk/book.py"] = book.read_bytes()
    for rel, _digest in VERIFY:
        dest = Path(root) / rel
        if dest.is_file():
            found[rel] = dest.read_bytes()
    return found


def apply_tree(root, activate=False):
    if activate:
        return "REFUSED_ACTIVATION"
    if production(root):
        return "REFUSED_ROOT"
    receipt_path = Path(root) / RECEIPT
    if receipt_path.is_file() and json.loads(receipt_path.read_text(encoding="utf-8")).get("restored") is not True:
        return "REFUSED_RERUN"
    rows = _rows(root)
    if rows and isinstance(rows[0], str):
        return rows[0]
    protected = _protected(root)
    written = []
    try:
        for state, item in rows:
            if state == "CURRENT":
                continue
            rel = item["path"]
            dest = Path(root) / rel
            if dest.is_file():
                backup = Path(root) / BACKUP / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(dest.read_bytes())
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((SOURCE / rel).read_bytes())
            written.append(rel)
            if sha256(dest) != item["target"]:
                raise RuntimeError("REFUSED_VERIFY")
        after = _protected(root)
        if after != protected:
            raise RuntimeError("REFUSED_PIN")
    except Exception:
        for rel in reversed(written):
            dest = Path(root) / rel
            backup = Path(root) / BACKUP / rel
            if backup.is_file():
                dest.write_bytes(backup.read_bytes())
            elif dest.is_file():
                dest.unlink()
        return "REFUSED_VERIFY"
    receipt = {
        "release": MANIFEST["release"],
        "applied": True,
        "activated": False,
        "connected": False,
        "restored": False,
        "files": [item["path"] for _state, item in rows],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return "APPLIED_NOT_ACTIVATED"


def rollback_tree(root):
    if production(root):
        return "REFUSED_ROOT"
    protected = _protected(root)
    for item in MANIFEST["files"]:
        rel = item["path"]
        dest = Path(root) / rel
        backup = Path(root) / BACKUP / rel
        if backup.is_file():
            dest.write_bytes(backup.read_bytes())
        elif item.get("new") and dest.is_file() and sha256(dest) == item["target"]:
            dest.unlink()
    if _protected(root) != protected:
        return "REFUSED_PIN"
    receipt_path = Path(root) / RECEIPT
    receipt = {"release": MANIFEST["release"], "applied": False, "activated": False, "restored": True, "connected": False}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return "ROLLED_BACK_NOT_ACTIVATED"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preflight or review-apply the owner-chat package.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if args.rollback:
        print(rollback_tree(args.root))
        return 0 if not production(args.root) else 2
    if args.apply:
        result = apply_tree(args.root, activate=args.activate)
        print(result)
        return 0 if result == "APPLIED_NOT_ACTIVATED" else 2
    result = preflight(args.root, activate=args.activate)
    print(result)
    return 0 if "REFUSED" not in result else 2


if __name__ == "__main__":
    sys.exit(main())
