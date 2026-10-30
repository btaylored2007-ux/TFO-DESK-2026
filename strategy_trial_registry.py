#!/usr/bin/env python3
"""Append-only house trial counter and closed-hypothesis guard. PAPER research."""
import argparse
import fcntl
import json
import os
import time


BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "strategy_trial_ledger.jsonl")
CLOSED = os.path.join(BASE, "reports", "closed_hypotheses.json")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rows(path=LEDGER):
    out = []
    with open(path) as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid ledger row %d: %s" % (line_no, exc))
            out.append(row)
    if not out:
        raise ValueError("trial ledger is empty")
    return out


def _floor_from_history(history):
    if not history:
        raise ValueError("trial ledger is empty")
    floor = history[0].get("count_before")
    if not isinstance(floor, int) or floor < 1:
        raise ValueError("invalid initial trial floor")
    seen = set()
    for row in history:
        mechanism = row.get("mechanism_id")
        before, variants, after = (row.get("count_before"), row.get("variants"),
                                   row.get("count_after"))
        if not mechanism or mechanism in seen:
            raise ValueError("missing/duplicate mechanism_id %r" % mechanism)
        if not all(isinstance(v, int) for v in (before, variants, after)):
            raise ValueError("non-integer count in %s" % mechanism)
        if variants < 0 or before != floor or after != before + variants:
            raise ValueError("non-contiguous trial receipt in %s" % mechanism)
        seen.add(mechanism)
        floor = after
    return floor


def current_floor(path=LEDGER):
    return _floor_from_history(rows(path))


def closed_hypotheses(path=CLOSED):
    with open(path) as source:
        payload = json.load(source)
    closed = payload.get("closed")
    if not isinstance(closed, dict):
        raise ValueError("closed-hypothesis registry is invalid")
    return closed


def check(mechanism_id, closed_path=CLOSED):
    return closed_hypotheses(closed_path).get(mechanism_id)


def reserve(mechanism_id, variants, rationale, ledger_path=LEDGER,
            closed_path=CLOSED):
    if not mechanism_id or not mechanism_id.strip():
        raise ValueError("mechanism_id is required")
    if not isinstance(variants, int) or variants <= 0:
        raise ValueError("variants must be a positive integer")
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required")
    if check(mechanism_id, closed_path):
        raise ValueError("mechanism is closed: %s" % mechanism_id)
    # Reserve under one exclusive lock so parallel labs cannot claim the same
    # count range. The existing bytes are read and validated before the append.
    with open(ledger_path, "a+") as target:
        fcntl.flock(target.fileno(), fcntl.LOCK_EX)
        target.seek(0)
        history = []
        for line_no, line in enumerate(target, 1):
            if not line.strip():
                continue
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid ledger row %d: %s" % (line_no, exc))
        if any(row.get("mechanism_id") == mechanism_id for row in history):
            raise ValueError("mechanism_id already reserved: %s" % mechanism_id)
        before = _floor_from_history(history)
        receipt = {
            "utc": _utc(), "mechanism_id": mechanism_id, "status": "reserved",
            "count_before": before, "variants": variants,
            "count_after": before + variants, "rationale": rationale.strip(),
        }
        target.seek(0, os.SEEK_END)
        target.write(json.dumps(receipt, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
        fcntl.flock(target.fileno(), fcntl.LOCK_UN)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    p_check = sub.add_parser("check")
    p_check.add_argument("--mechanism", required=True)
    p_reserve = sub.add_parser("reserve")
    p_reserve.add_argument("--mechanism", required=True)
    p_reserve.add_argument("--variants", required=True, type=int)
    p_reserve.add_argument("--rationale", required=True)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps({"paper_only": True,
                          "n_trials_floor": current_floor(),
                          "closed_mechanisms": sorted(closed_hypotheses())},
                         indent=2))
        return 0
    if args.command == "check":
        closure = check(args.mechanism)
        print(json.dumps({"mechanism_id": args.mechanism,
                          "closed": bool(closure),
                          "detail": closure}, indent=2))
        return 2 if closure else 0
    try:
        receipt = reserve(args.mechanism, args.variants, args.rationale)
    except ValueError as exc:
        print("REFUSED:", exc)
        return 2
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
