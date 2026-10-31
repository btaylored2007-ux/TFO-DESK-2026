#!/usr/bin/env python3
"""Compare installed LaunchAgents with the plists tracked in this repo.

Exit 0 when this host has no ~/Library/LaunchAgents (not the desk Mac) or the
installed com.tfo.* / com.taylor.* jobs match the repo copies.
Exit 1 when an installed job is missing from the repo or its bytes differ.
This check never refuses a trade. A tooling failure here is a report, not a halt.
"""
import glob
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED = os.path.expanduser("~/Library/LaunchAgents")


def _labels(paths):
    return {os.path.basename(p): p for p in paths}


def main():
    repo = []
    for folder in ("launchd", "ops"):
        repo.extend(glob.glob(os.path.join(ROOT, folder, "*.plist")))
    repo_labels = _labels(repo)
    if not os.path.isdir(INSTALLED):
        print("launchd reconcile: no installed LaunchAgents on this host (%d plists in repo)" % len(repo_labels))
        return 0
    installed = _labels(
        glob.glob(os.path.join(INSTALLED, "com.tfo.*.plist"))
        + glob.glob(os.path.join(INSTALLED, "com.taylor.*.plist")))
    missing = sorted(set(installed) - set(repo_labels))
    differ = []
    for name in sorted(set(installed) & set(repo_labels)):
        with open(installed[name], "rb") as fh:
            ih = hashlib.sha256(fh.read()).hexdigest()
        with open(repo_labels[name], "rb") as fh:
            rh = hashlib.sha256(fh.read()).hexdigest()
        if ih != rh:
            differ.append(name)
    if not missing and not differ:
        print("launchd reconcile: installed com.tfo/com.taylor plists match the repo (%d)" % len(installed))
        return 0
    if missing:
        print("launchd reconcile: installed jobs missing from the repo: %s" % ", ".join(missing[:40]))
    if differ:
        print("launchd reconcile: installed plist differs from the repo: %s" % ", ".join(differ[:40]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
