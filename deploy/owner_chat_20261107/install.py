#!/usr/bin/env python3
"""Stage the owner-chat clarity and SuperGrok files. Refuses production roots and service starts."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
SOURCE = HERE / "payload"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production(path):
    text = str(Path(path).resolve())
    return text == "/opt/tfo" or text.startswith("/opt/tfo/")


def preflight(root, activate=False):
    if activate:
        return "REFUSED_ACTIVATION"
    if production(root):
        return "REFUSED_ROOT"
    rows = []
    for item in MANIFEST["files"]:
        src = SOURCE / item["path"]
        if not src.is_file() or sha256(src) != item["target"]:
            return "MISSING_PAYLOAD"
        dest = Path(root) / item["path"]
        if item.get("new") and not dest.exists():
            rows.append("INSTALL_NEW " + item["path"])
            continue
        if not dest.is_file():
            return "REFUSED_BASE"
        current = sha256(dest)
        if current == item["target"]:
            rows.append("CURRENT " + item["path"])
        elif current in item["bases"]:
            rows.append("REPLACE " + item["path"])
        else:
            return "REFUSED_BASE"
    for blocked in MANIFEST["do_not_write"]:
        if (SOURCE / blocked).exists():
            return "REFUSED_SCOPE"
    return "\n".join(rows + ["NOT_ACTIVATED", "NOT_DEPLOYED"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preflight the owner-chat package.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args(argv)
    print(preflight(args.root, activate=args.activate))
    return 0 if "REFUSED" not in preflight(args.root, activate=args.activate) else 2


if __name__ == "__main__":
    sys.exit(main())
