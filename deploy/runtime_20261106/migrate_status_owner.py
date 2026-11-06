#!/usr/bin/env python3
"""Give tfo ownership of status files a root shadow run created.

Only uid 0 entries on the allowlist are changed, and only when this process is
root. Modes stay as they are, so a 0600 file stays 0600 after the owner is tfo.
Symlinks are skipped. The book, queues, and provider sessions are not touched.
"""
import argparse
import os
import pwd
import stat
import sys

FILES = (
    "state/thf/deepseek_api/status.json",
    "state/thf/deepseek_api/status.lock",
    "state/thf/shadow_sampler_status.json",
    "state/thf/shadow_samples.jsonl",
    "state/thf/shadow_jobs.jsonl",
    "state/thf/grok_provider_status.json",
)
DIRECTORY = "state/thf/deepseek_api"


def decide(uid, is_link):
    if is_link:
        return "skip_symlink"
    if uid != 0:
        return "skip_not_root"
    return "chown"


def targets(root):
    found = []
    for rel in FILES:
        found.append(os.path.join(root, rel))
    folder = os.path.join(root, DIRECTORY)
    found.append(folder)
    if os.path.isdir(folder) and not os.path.islink(folder):
        for name in sorted(os.listdir(folder)):
            if name in ("", ".", "..") or "/" in name:
                continue
            found.append(os.path.join(folder, name))
    unique = []
    seen = set()
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def plan(root):
    rows = []
    for path in targets(root):
        rel = os.path.relpath(path, root)
        if not os.path.lexists(path):
            rows.append(dict(path=rel, action="missing"))
            continue
        info = os.lstat(path)
        action = decide(info.st_uid, stat.S_ISLNK(info.st_mode))
        rows.append(dict(path=rel, action=action, uid=info.st_uid, mode=stat.S_IMODE(info.st_mode)))
    return rows


def apply_plan(root, rows):
    chosen = [row for row in rows if row["action"] == "chown"]
    if not chosen:
        return True
    if os.geteuid() != 0:
        return False
    user = pwd.getpwnam("tfo")
    for row in chosen:
        os.lchown(os.path.join(root, row["path"]), user.pw_uid, user.pw_gid)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chown root-owned THF status files to tfo.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    rows = plan(args.root)
    needs_root = False
    for row in rows:
        print("%s %s" % (row["action"].upper(), row["path"]))
        if row["action"] == "chown":
            needs_root = True
    if not args.apply:
        print("DRY_RUN")
        return 0
    if needs_root and os.geteuid() != 0:
        print("NEED_ROOT")
        return 2
    if not apply_plan(args.root, rows):
        print("NEED_ROOT")
        return 2
    print("MIGRATION_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
