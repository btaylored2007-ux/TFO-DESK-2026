#!/usr/bin/env python3
"""Primary-source budget broker for global fundamental research.

PAPER RESEARCH ONLY.  Search calls are reserved for locating and adversarially
verifying events; direct issuer/exchange/regulator retrieval is counted
separately and does not consume the scarce discovery allowance.  Exhaustion is
recorded as ``UNRESEARCHED`` and can never be collapsed into ``nothing_found``.

The ledger is append-only and safe for parallel research workers.  It contains
no API keys, broker credentials, orders or trading decisions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path


BASE = Path(__file__).resolve().parent
DEFAULT_LEDGER = BASE / "reports" / "fundamental_source_budget.jsonl"
SEARCH_STAGES = ("event_discovery", "catalyst_verification", "adjudication")
DIRECT_STAGE = "primary_direct"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rows_from_locked_file(handle) -> list[dict]:
    handle.seek(0)
    return [json.loads(line) for line in handle if line.strip()]


def _append_to_locked_file(handle, row: dict) -> None:
    handle.seek(0, os.SEEK_END)
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def append_locked(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        _append_to_locked_file(f, row)


def initialise(path: Path, batch_id: str, total_search_budget: int = 200) -> dict:
    if total_search_budget < 12:
        raise ValueError("total_search_budget must be at least 12")
    quotas = {
        "event_discovery": total_search_budget * 25 // 100,
        "catalyst_verification": total_search_budget * 55 // 100,
    }
    quotas["adjudication"] = total_search_budget - sum(quotas.values())
    row = {"kind": "init", "utc": utc_now(), "batch_id": batch_id,
           "paper_only": True, "search_quotas": quotas,
           "rule": "Exhaustion means UNRESEARCHED, never nothing_found. Primary direct fetches do not consume search quota."}
    # Duplicate detection and append are one interprocess critical section.
    # Otherwise two workers can both observe an absent batch and create two
    # incompatible init rows before either append becomes visible.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = _rows_from_locked_file(handle)
        if any(existing.get("kind") == "init"
               and existing.get("batch_id") == batch_id for existing in rows):
            raise ValueError("batch_id already initialised")
        _append_to_locked_file(handle, row)
        return row


def _batch(path: Path, batch_id: str) -> tuple[dict, list[dict]]:
    rows = [r for r in read_rows(path) if r.get("batch_id") == batch_id]
    init = next((r for r in rows if r.get("kind") == "init"), None)
    if init is None:
        raise ValueError("batch_id is not initialised")
    return init, rows


def reserve(path: Path, batch_id: str, stage: str, units: int, subject: str) -> dict:
    if stage not in SEARCH_STAGES and stage != DIRECT_STAGE:
        raise ValueError("unknown stage")
    if units <= 0:
        raise ValueError("units must be positive")
    # The read/check/append is one critical section.  Locking only the append
    # permits two workers to observe the same used total and both oversubscribe.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = [row for row in _rows_from_locked_file(handle)
                if row.get("batch_id") == batch_id]
        init = next((row for row in rows if row.get("kind") == "init"), None)
        if init is None:
            raise ValueError("batch_id is not initialised")
        used = sum(int(row.get("units") or 0) for row in rows
                   if row.get("kind") == "reserve" and row.get("stage") == stage)
        quota = None if stage == DIRECT_STAGE else int(init["search_quotas"][stage])
        allowed = quota is None or used + units <= quota
        row = {"kind": "reserve" if allowed else "unresearched", "utc": utc_now(),
               "batch_id": batch_id, "paper_only": True, "stage": stage,
               "units": units if allowed else 0, "requested_units": units,
               "subject": subject[:240], "used_before": used, "quota": quota,
               "status": "RESERVED" if allowed else "UNRESEARCHED",
               "reason": None if allowed else "%s quota exhausted" % stage}
        _append_to_locked_file(handle, row)
        return row


def control_check(path: Path, batch_id: str, checkpoint: str, ok: bool, detail: str) -> dict:
    if checkpoint not in ("start", "midpoint"):
        raise ValueError("checkpoint must be start or midpoint")
    _batch(path, batch_id)
    row = {"kind": "control", "utc": utc_now(), "batch_id": batch_id,
           "paper_only": True, "checkpoint": checkpoint, "ok": bool(ok),
           "detail": detail[:300]}
    append_locked(path, row)
    return row


def status(path: Path, batch_id: str) -> dict:
    init, rows = _batch(path, batch_id)
    usage = {stage: sum(int(r.get("units") or 0) for r in rows
                        if r.get("kind") == "reserve" and r.get("stage") == stage)
             for stage in (*SEARCH_STAGES, DIRECT_STAGE)}
    controls = {r.get("checkpoint"): r for r in rows if r.get("kind") == "control"}
    provider_ok = bool(controls.get("start", {}).get("ok")) and bool(controls.get("midpoint", {}).get("ok"))
    unresearched = [r for r in rows if r.get("kind") == "unresearched"]
    return {
        "batch_id": batch_id,
        "paper_only": True,
        "usage": usage,
        "search_quotas": init["search_quotas"],
        "control_checks": controls,
        "provider_usable": provider_ok,
        "coverage_status": "ACTIVE" if provider_ok else "COVERAGE_FAILURE",
        "unresearched": unresearched,
        "nothing_found_count": 0,
        "note": "Only a completed researched record may later be classified nothing_found; this broker never makes that classification.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--total-search-budget", type=int, default=200)
    p = sub.add_parser("reserve")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--stage", required=True, choices=(*SEARCH_STAGES, DIRECT_STAGE))
    p.add_argument("--units", type=int, required=True)
    p.add_argument("--subject", required=True)
    p = sub.add_parser("control")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--checkpoint", required=True, choices=("start", "midpoint"))
    p.add_argument("--ok", required=True, choices=("yes", "no"))
    p.add_argument("--detail", required=True)
    p = sub.add_parser("status")
    p.add_argument("--batch-id", required=True)
    args = ap.parse_args()
    path = Path(args.ledger)
    try:
        if args.command == "init":
            out = initialise(path, args.batch_id, args.total_search_budget)
        elif args.command == "reserve":
            out = reserve(path, args.batch_id, args.stage, args.units, args.subject)
        elif args.command == "control":
            out = control_check(path, args.batch_id, args.checkpoint, args.ok == "yes", args.detail)
        else:
            out = status(path, args.batch_id)
        print(json.dumps(out, indent=2, sort_keys=True))
        failed = (
            out.get("status") == "UNRESEARCHED"
            or out.get("coverage_status") == "COVERAGE_FAILURE"
        )
        return 2 if failed else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
