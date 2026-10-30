#!/usr/bin/env python3
"""Seal one catalyst-lifecycle alert as point-in-time research evidence.

PAPER research only.  This turns a verified future event already present in the
lifecycle report into a stable receipt that a strict research block can cite.
It never chooses a verdict, direction, notional or trade.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from fundamental_discovery_common import atomic_json, file_sha256


SCHEMA = "tfo.discovery.event-evidence.v1"
LIFECYCLE_SCHEMA = "tfo.discovery.catalyst-lifecycle.v1"
REFUSAL_FLAGS = frozenset({"past", "possible_killed", "stale_claim", "unverified_source"})


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_receipt(
    lifecycle_path: str | Path,
    *,
    symbol: str,
    stable_issuer_id: str,
    event_date: str,
    as_of: dt.date | None = None,
) -> dict[str, Any]:
    as_of = as_of or dt.date.today()
    path = Path(lifecycle_path).resolve()
    lifecycle = json.loads(path.read_text(encoding="utf-8"))
    if lifecycle.get("schema") != LIFECYCLE_SCHEMA:
        raise ValueError("unsupported catalyst-lifecycle schema")
    try:
        report_date = dt.date.fromisoformat(str(lifecycle.get("as_of") or ""))
        selected_date = dt.date.fromisoformat(event_date)
    except ValueError as exc:
        raise ValueError("lifecycle as_of and event date must be exact ISO dates") from exc
    if report_date > as_of:
        raise ValueError("lifecycle report is future-known")
    if selected_date <= as_of:
        raise ValueError("event date is not future")
    wanted = symbol.strip().upper()
    matches = [
        event
        for event in lifecycle.get("events") or []
        if isinstance(event, dict)
        and str(event.get("symbol") or "").strip().upper() == wanted
        and event_date in (event.get("future_dates") or [])
    ]
    if len(matches) != 1:
        raise ValueError("no unique lifecycle event matches symbol and future date")
    event = matches[0]
    flags = set(str(flag) for flag in event.get("flags") or [])
    bad = sorted(flags & REFUSAL_FLAGS)
    if event.get("needs_review") is not False or "future" not in flags or bad:
        raise ValueError("event is not clean future evidence: %s" % (", ".join(bad) or "review"))
    source_path = Path(str(event.get("source_path") or "")).resolve()
    line = event.get("line")
    if not source_path.is_file() or not isinstance(line, int) or line <= 0:
        raise ValueError("event source file or line is invalid")
    stable_id = stable_issuer_id.strip()
    if not stable_id:
        raise ValueError("stable_issuer_id is required")
    return {
        "schema": SCHEMA,
        "status": "EVIDENCE_READY",
        "paper_only": True,
        "non_signal": True,
        "stable_issuer_id": stable_id,
        "symbol": wanted,
        "event_date": selected_date.isoformat(),
        "as_of_date": report_date.isoformat(),
        "recorded_utc": lifecycle.get("generated_utc"),
        "lifecycle": {"path": str(path), "sha256": file_sha256(path)},
        "source": {
            "path": str(source_path),
            "sha256": file_sha256(source_path),
            "line": line,
        },
        "event_sha256": _json_sha256(event),
        "flags": sorted(flags),
        "rule": "Evidence receipt only; no verdict, direction, sizing or PAPER authority.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--stable-issuer-id", required=True)
    parser.add_argument("--event-date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        receipt = build_receipt(
            args.lifecycle,
            symbol=args.symbol,
            stable_issuer_id=args.stable_issuer_id,
            event_date=args.event_date,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED:", exc)
        return 2
    atomic_json(args.output, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
