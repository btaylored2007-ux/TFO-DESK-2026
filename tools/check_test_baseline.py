#!/usr/bin/env python3
"""Compare a pytest log to tools/test_baseline.json.

Same rule as tools/safe_deploy.sh and .github/workflows/tests.yml:
a FAILED or ERROR node that is not on known_failures fails the run.
A failure that is already on the list does not. A log with no pytest
summary line fails the run, because the suite did not actually finish.

Exit 0: every failure is in the baseline (or there are none).
Exit 1: at least one new failure.
Exit 2: pytest did not produce a summary.
"""
import json
import re
import sys

_SUMMARY = re.compile(r"(passed|failed|error)")
_NODE = re.compile(r"^(FAILED|ERROR) (\S+)")


def compare(known, lines):
    """Return (status_line, new_nodeids, summary_line)."""
    known = set(known or [])
    got = set()
    summary = ""
    for line in lines:
        m = _NODE.match(line)
        if m:
            got.add(m.group(2))
        if _SUMMARY.search(line):
            summary = line.strip()
    new = sorted(got - known)
    head = "OK" if not new else "NEW:" + " ".join(new)
    status = "%s | known still failing: %d | fixed: %d" % (head, len(got & known), len(known - got))
    return status, new, summary


def main(argv):
    if len(argv) != 3:
        print("usage: check_test_baseline.py BASELINE.json PYTEST_LOG", file=sys.stderr)
        return 2
    known = json.load(open(argv[1])).get("known_failures") or []
    lines = open(argv[2]).read().splitlines()
    status, new, summary = compare(known, lines)
    if not summary:
        print("pytest did not run (no summary line)", file=sys.stderr)
        print(status)
        return 2
    print(status)
    if new:
        print("new test failures vs baseline:", file=sys.stderr)
        for node in new:
            print(node, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
