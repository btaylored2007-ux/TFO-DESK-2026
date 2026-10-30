#!/usr/bin/env python3
"""Atomic hunt-to-entry handoff for the TFO Fundamental Desk. PAPER ONLY.

Research files are written by several workers and can be parseable before the
hunt is actually finished.  The entry agent must therefore never infer
completeness from a readable ``blocks_*.md`` file.  The standing hunt calls
``finalize`` once, after every worker has returned, deterministic aggregation has
finished, and coverage has been updated.  That call writes one immutable READY
receipt with the exact block and aggregate hashes.  Entry-session runs consume
only those receipts and leave a separate append-only attempt log.

This tool does not allocate capital or submit any order.  It only coordinates
research handoff and records entry-session outcomes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path


DEFAULT_ROOT = Path("/Users/test/prospect/var")
READY_NAME = ".tfo_batch_ready.json"
ATTEMPTS_NAME = ".tfo_entry_attempts.jsonl"
ALLOWED_OUTCOMES = {"deferred", "refused", "held", "opened"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_ready(run: Path) -> dict:
    path = run / READY_NAME
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def summary(aggregate: dict) -> dict:
    return {
        "blocks_parsed": aggregate.get("blocks_parsed"),
        "unique_symbols": aggregate.get("unique_symbols"),
        "parse_failures": aggregate.get("parse_failures", []),
        "trade": aggregate.get("trade", {}),
        "tally": aggregate.get("tally", {}),
        "long_alloc_symbols": sorted((aggregate.get("long_alloc") or {}).keys()),
        "short_alloc_symbols": sorted((aggregate.get("short_alloc") or {}).keys()),
        "undervalued_not_marked_long": aggregate.get("undervalued_not_marked_long", []),
        "long_blocked_no_us_line": aggregate.get("long_blocked_no_us_line", []),
        "short_blocked_no_us_line": aggregate.get("short_blocked_no_us_line", []),
    }


def finalize(run: Path) -> dict:
    run = run.resolve()
    if not run.is_dir():
        raise ValueError("run directory is unavailable: %s" % run)
    blocks = sorted(run.glob("blocks_*.md"))
    if not blocks:
        raise ValueError("no blocks_*.md files in %s" % run)
    empty = [p.name for p in blocks if p.stat().st_size == 0]
    if empty:
        raise ValueError("empty block files: %s" % ", ".join(empty))
    aggregate_path = run / "aggregate.json"
    if not aggregate_path.is_file():
        raise ValueError("aggregate.json missing; aggregate before finalizing")
    with aggregate_path.open(encoding="utf-8") as f:
        aggregate = json.load(f)
    if not isinstance(aggregate.get("blocks_parsed"), int):
        raise ValueError("aggregate.json lacks a deterministic blocks_parsed count")
    ready_path = run / READY_NAME
    candidate = {
        "schema": 1,
        "status": "READY",
        "paper_only": True,
        "finalized_utc": utc_now(),
        "run_dir": str(run),
        "blocks": [{"name": p.name, "sha256": digest(p), "bytes": p.stat().st_size} for p in blocks],
        "aggregate": {"name": aggregate_path.name, "sha256": digest(aggregate_path), "summary": summary(aggregate)},
        "entry_attempt_log": str(run / ATTEMPTS_NAME),
        "rule": ("Only this receipt makes a hunt entry-eligible. A later edit to a block or "
                 "aggregate invalidates the receipt and requires a new deterministic aggregation "
                 "and finalization before an entry session may act."),
    }
    if ready_path.exists():
        prior = load_ready(run)
        immutable = {k: v for k, v in prior.items() if k != "finalized_utc"}
        next_immutable = {k: v for k, v in candidate.items() if k != "finalized_utc"}
        if immutable == next_immutable:
            return prior
        raise ValueError("READY receipt exists but current files differ; do not overwrite it. "
                         "Create a new run directory for amended research.")
    atomic_json(ready_path, candidate)
    return candidate


def verify(run: Path) -> dict:
    receipt = load_ready(run)
    mismatches = []
    for item in receipt.get("blocks", []):
        path = run / item["name"]
        if not path.is_file() or digest(path) != item["sha256"]:
            mismatches.append(item["name"])
    aggregate = receipt.get("aggregate") or {}
    ap = run / aggregate.get("name", "aggregate.json")
    if not ap.is_file() or digest(ap) != aggregate.get("sha256"):
        mismatches.append(aggregate.get("name", "aggregate.json"))
    return {"run_dir": str(run.resolve()), "ready": not mismatches,
            "mismatches": mismatches, "receipt": receipt}


def scan(root: Path) -> list[dict]:
    rows = []
    for ready_path in sorted(root.glob("*/" + READY_NAME)):
        result = verify(ready_path.parent)
        rows.append({"run_dir": result["run_dir"], "ready": result["ready"],
                     "mismatches": result["mismatches"],
                     "summary": result["receipt"].get("aggregate", {}).get("summary", {})})
    return rows


def record(run: Path, outcome: str, symbol: str, reason: str) -> dict:
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError("outcome must be one of %s" % ", ".join(sorted(ALLOWED_OUTCOMES)))
    check = verify(run)
    if not check["ready"]:
        raise ValueError("cannot record an entry attempt for an invalidated receipt: %s" %
                         ", ".join(check["mismatches"]))
    row = {"utc": utc_now(), "paper_only": True, "outcome": outcome,
           "symbol": symbol, "reason": reason[:600], "run_dir": str(run.resolve())}
    with (run / ATTEMPTS_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p_fin = sub.add_parser("finalize")
    p_fin.add_argument("--run", required=True)
    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--run", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--root", default=str(DEFAULT_ROOT))
    p_rec = sub.add_parser("record")
    p_rec.add_argument("--run", required=True)
    p_rec.add_argument("--outcome", required=True)
    p_rec.add_argument("--symbol", required=True)
    p_rec.add_argument("--reason", required=True)
    args = ap.parse_args()
    try:
        if args.command == "finalize":
            out = finalize(Path(args.run))
        elif args.command == "verify":
            out = verify(Path(args.run))
        elif args.command == "scan":
            out = scan(Path(args.root))
        else:
            out = record(Path(args.run), args.outcome, args.symbol, args.reason)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    # Verification is an execution handoff gate.  Printing ``ready: false``
    # while returning success lets schedulers that correctly rely on exit status
    # advance into an invalidated batch.  Fail loudly on the exact receipt that
    # was asked for; callers can still inspect the structured mismatch list.
    if args.command == "verify" and out.get("ready") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
