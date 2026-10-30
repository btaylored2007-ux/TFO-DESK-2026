#!/usr/bin/env python3
"""Read-only catalyst lifecycle monitor for TFO discovery and open positions.

It raises evidence flags; it never decides that a thesis is broken and never
writes to a research block or ledger.  A `possible_killed` flag means a person or
research agent must read the cited disclosure, not that a position should exit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fundamental_discovery_common import (
    atomic_json,
    atomic_text,
    file_sha256,
    iso_dates,
    parse_blocks,
    source_looks_verified,
)


KILLED_PATTERNS = (
    r"\b(?:has\s+)?lapsed\b",
    r"\b(?:was|has\s+been)\s+withdrawn\b",
    r"\b(?:was|has\s+been)\s+cancel(?:led|ed)\b",
    r"\bunable\s+to\s+ascertain\s+when\b",
    r"\bno\s+longer\s+(?:expects|plans|targets|intends)\b",
    r"\bwalk(?:ed)?[- ]back\b",
    r"\bdelayed\s+indefinitely\b",
    r"\bdeadline\s+(?:was|has\s+been)\s+(?:moved|extended|pushed)\b",
)
STALE_PATTERNS = (
    r"\bstale\b",
    r"\bold\s+guidance\b",
    r"\brecycled\s+guidance\b",
    r"\bno\s+current\s+basis\b",
    r"\bwindow\s+(?:has\s+)?closed\b",
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def classify_event(
    *,
    symbol: str,
    source_path: str,
    line: int | None,
    catalyst: str,
    event_calendar: str,
    kills_it: str,
    news: str,
    why_cheap: str,
    sources: str,
    as_of: dt.date,
    source_type: str = "research_block",
) -> dict[str, Any]:
    lifecycle_text = " ".join((catalyst, event_calendar, news, why_cheap))
    # KILLS IT dates are invalidation/review deadlines, not proof that the
    # catalyst itself remains future.  Keep them visible but never let them
    # mask a catalyst/event-calendar date that has already passed.
    event_dates = iso_dates(" ".join((catalyst, event_calendar)))
    kill_dates = iso_dates(kills_it)
    dates = sorted(set(event_dates) | set(kill_dates))
    parsed_dates = [value for value in (_date(item) for item in event_dates) if value]
    past = [value.isoformat() for value in parsed_dates if value < as_of]
    due = [value.isoformat() for value in parsed_dates if value == as_of]
    future = [value.isoformat() for value in parsed_dates if value > as_of]
    flags: list[str] = []
    evidence: list[str] = []

    if not event_dates:
        flags.append("undated")
        evidence.append("no exact ISO date in CATALYST / EVENT CALENDAR")
    if not source_looks_verified(sources):
        flags.append("unverified_source")
        evidence.append("no verifiable source/date in SOURCES")
    if past and not future and not due:
        flags.append("past")
        evidence.append(f"latest exact event/review date {max(past)} is before {as_of}")
    elif past:
        flags.append("contains_past_milestone")
        evidence.append(f"past dates retained alongside a future event: {', '.join(past)}")
    if due:
        flags.append("due_today")
        evidence.append(f"event/review date due today: {', '.join(due)}")
    if future:
        flags.append("future")
    killed_hits = [
        pattern for pattern in KILLED_PATTERNS if re.search(pattern, lifecycle_text, re.IGNORECASE)
    ]
    if killed_hits:
        flags.append("possible_killed")
        evidence.append("cancellation/delay/walk-back language in catalyst/news evidence")
    stale_hits = [
        pattern for pattern in STALE_PATTERNS if re.search(pattern, lifecycle_text, re.IGNORECASE)
    ]
    if stale_hits:
        flags.append("stale_claim")
        evidence.append("stale/recycled/closed-window language in catalyst/news evidence")

    needs_review = any(
        flag in flags
        for flag in (
            "undated",
            "unverified_source",
            "past",
            "due_today",
            "possible_killed",
            "stale_claim",
        )
    )
    return {
        "symbol": symbol,
        "source_type": source_type,
        "source_path": source_path,
        "line": line,
        "dates": dates,
        "past_dates": past,
        "due_dates": due,
        "future_dates": future,
        "kill_dates": kill_dates,
        "flags": sorted(set(flags)),
        "needs_review": needs_review,
        "evidence": evidence,
        "catalyst_excerpt": catalyst[:500],
        "event_calendar_excerpt": event_calendar[:500],
        "non_decision": (
            "Alert only. This monitor does not decide whether a thesis failed, "
            "does not size, and does not trade."
        ),
    }


def monitor(
    root: str | Path,
    *,
    as_of: dt.date,
    positions_ledger: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root).resolve()
    events: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    for path in sorted(base.rglob("blocks_*.md")):
        if "strategy_improvements_2026-09-02/deployment" in str(path):
            continue
        blocks, issues = parse_blocks(path)
        source_files.append(
            {
                "path": str(path.resolve()),
                "sha256": file_sha256(path),
                "blocks": len(blocks),
                "parse_issues": issues,
            }
        )
        for block in blocks:
            events.append(
                classify_event(
                    symbol=block.get("_symbol") or "(missing)",
                    source_path=str(path.resolve()),
                    line=block.get("_start_line"),
                    catalyst=str(block.get("CATALYST", "")),
                    event_calendar=str(block.get("EVENT CALENDAR", "")),
                    kills_it=str(block.get("KILLS IT", "")),
                    news=str(block.get("NEWS FLOW 90D", "")),
                    why_cheap=str(block.get("WHY CHEAP", "")),
                    sources=str(block.get("SOURCES", "")),
                    as_of=as_of,
                )
            )

    ledger_record: dict[str, Any] | None = None
    if positions_ledger:
        ledger_path = Path(positions_ledger)
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            positions = ledger.get("positions") or {}
            ledger_record = {
                "path": str(ledger_path.resolve()),
                "sha256": file_sha256(ledger_path),
                "positions": len(positions),
            }
            for symbol, position in positions.items():
                catalyst_date = str(position.get("catalyst_date") or "")
                thesis = str(position.get("thesis") or "")
                events.append(
                    classify_event(
                        symbol=symbol,
                        source_path=str(ledger_path.resolve()),
                        line=None,
                        catalyst=catalyst_date,
                        event_calendar="",
                        kills_it=thesis,
                        news="",
                        why_cheap="",
                        # A ledger thesis is not a primary source receipt. Mark
                        # unverified so the review agent knows it must reopen the
                        # cited disclosure carried in the original research.
                        sources="",
                        as_of=as_of,
                        source_type="open_position",
                    )
                )
        except (OSError, ValueError, TypeError) as exc:
            ledger_record = {
                "path": str(ledger_path),
                "error": f"{type(exc).__name__}: {exc}",
            }

    counts = Counter()
    for event in events:
        counts["events"] += 1
        if event["needs_review"]:
            counts["needs_review"] += 1
        for flag in event["flags"]:
            counts[flag] += 1
    return {
        "schema": "tfo.discovery.catalyst-lifecycle.v1",
        "generated_utc": utc_now(),
        "as_of": as_of.isoformat(),
        "root": str(base),
        "policy": (
            "read-only alerting; no thesis decision, sizing, ledger mutation, or trade"
        ),
        "counts": dict(counts),
        "source_files": source_files,
        "positions_ledger": ledger_record,
        "events": events,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    flagged = [event for event in payload["events"] if event["needs_review"]]
    rows = []
    for event in flagged[:200]:
        rows.append(
            "| {symbol} | {kind} | {flags} | {dates} | {path}:{line} |".format(
                symbol=event["symbol"].replace("|", "\\|"),
                kind=event["source_type"],
                flags=", ".join(event["flags"]) or "none",
                dates=", ".join(event["dates"]) or "none",
                path=event["source_path"].replace("|", "\\|"),
                line=event["line"] or "-",
            )
        )
    return "\n".join(
        [
            "# Catalyst lifecycle alerts",
            "",
            f"As of: {payload['as_of']}",
            "",
            "Read-only alerts only. No item below is an exit or thesis decision.",
            "",
            "| Symbol | Source | Flags | Exact dates | Evidence |",
            "|---|---|---|---|---|",
            *rows,
            "",
            f"Flagged {len(flagged)} of {len(payload['events'])} event records.",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/Users/test/prospect/var")
    parser.add_argument("--as-of", default=dt.date.today().isoformat())
    parser.add_argument("--positions-ledger")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    as_of = dt.date.fromisoformat(args.as_of)
    payload = monitor(args.root, as_of=as_of, positions_ledger=args.positions_ledger)
    atomic_json(args.output, payload)
    if args.report:
        atomic_text(args.report, markdown_report(payload))
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
