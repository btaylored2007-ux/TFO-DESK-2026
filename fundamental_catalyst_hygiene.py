#!/usr/bin/env python3
"""Safely recover sourced catalyst dates from the exact historical hunt.

PAPER ONLY.  Legacy positions predate the mandatory ``--catalyst-date`` entry
argument.  This utility narrows each lookup to the hunt named in that position's
own thesis, extracts only an explicit ISO date from EVENT CALENDAR / CATALYST /
KILLS IT text, and writes a date only when the source is unambiguous and still
future-dated.  It never guesses an earnings date, silently chooses another hunt,
or changes a trade.  Past dates are reported for human review, not backfilled
into a position where they would trigger an immediate automated expiry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import desk_lock


BASE = Path(__file__).resolve().parent
LEDGER = BASE / "reports" / "fundamental_book.json"
OUT = BASE / "reports" / "fundamental_catalyst_hygiene.json"
VAR = Path("/Users/test/prospect/var")
SECTION = re.compile(r"^(EVENT CALENDAR|CALENDAR|CATALYST|KILLS IT)\s*:", re.I)
FIELD = re.compile(r"^[A-Z][A-Z0-9 /_&]{2,26}\s*:")
DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
SYMBOL = re.compile(r"^SYMBOL:\s*(\S+)")
HUNT = re.compile(r"\bhunt[ _-]?(\d{1,4}|20\d{2}-\d{2}-\d{2})\b", re.I)


def today() -> str:
    return dt.date.today().isoformat()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def thesis_roots(thesis: str) -> list[Path]:
    matches = HUNT.findall(thesis or "")
    roots = []
    for part in matches:
        name = "hunt_" + part if part.startswith("20") else "hunt" + part
        p = VAR / name
        if p.is_dir() and p not in roots:
            roots.append(p)
    # The original book was assembled from global_run and often only says
    # "hunt" generically.  It is a bounded fallback, never a whole-var search.
    if not roots:
        roots.append(VAR / "global_run")
    return roots


def block_for(path: Path, wanted: str) -> list[str] | None:
    active = False
    lines: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw in raw_lines:
        match = SYMBOL.match(raw)
        if match:
            if active:
                break
            active = match.group(1).strip("`*<>") == wanted
            if active:
                lines = [raw]
            continue
        if active:
            lines.append(raw)
    return lines if active else None


def dated_context(lines: list[str]) -> list[dict]:
    section = None
    found = []
    for raw in lines:
        hit = SECTION.match(raw)
        if hit:
            section = hit.group(1).upper()
        elif FIELD.match(raw):
            section = None
        if not section:
            continue
        for date in DATE.findall(raw):
            found.append({"date": date, "section": section, "line": raw.strip()[:500]})
    return found


def proposal(symbol: str, position: dict) -> dict:
    if position.get("catalyst_date"):
        return {"symbol": symbol, "status": "already_dated", "date": position["catalyst_date"]}
    roots = thesis_roots(str(position.get("thesis") or ""))
    blocks = []
    for root in roots:
        for path in sorted(root.glob("blocks_*.md")):
            block = block_for(path, symbol)
            if block:
                blocks.append((path, block))
    if len(blocks) != 1:
        return {"symbol": symbol, "status": "unresolved_block", "roots": [str(p) for p in roots],
                "matches": [str(path) for path, _ in blocks]}
    path, block = blocks[0]
    contexts = dated_context(block)
    # A generic CATALYST paragraph can name a fiscal year-end while the actual
    # disclosure arrives months later.  Only an explicit calendar or the KILLS
    # IT condition itself is safe to convert into an expiry date.
    future = [x for x in contexts if x["date"] > today()
              and x["section"] in {"KILLS IT", "EVENT CALENDAR", "CALENDAR"}]
    if future:
        priority = {"KILLS IT": 0, "EVENT CALENDAR": 1, "CALENDAR": 2}
        future.sort(key=lambda x: (priority.get(x["section"], 9), x["date"]))
        hit = future[0]
        return {"symbol": symbol, "status": "ready_to_backfill", "date": hit["date"],
                "block": str(path), "section": hit["section"], "line": hit["line"]}
    past = sorted({x["date"] for x in contexts if x["date"] <= today()})
    return {"symbol": symbol, "status": "no_future_date", "block": str(path),
            "latest_past_date": past[-1] if past else None}


def run(apply: bool) -> dict:
    try:
        with LEDGER.open(encoding="utf-8") as f:
            ledger = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ledger unreadable: %s" % exc) from exc
    results = [proposal(symbol, pos) for symbol, pos in sorted((ledger.get("positions") or {}).items())]
    proposed = [r for r in results if r["status"] == "ready_to_backfill"]
    applied = []
    if apply and proposed:
        with desk_lock.ledger_lock("fundamental"):
            with LEDGER.open(encoding="utf-8") as f:
                live = json.load(f)
            for item in proposed:
                pos = (live.get("positions") or {}).get(item["symbol"])
                if not pos or pos.get("catalyst_date"):
                    continue
                pos["catalyst_date"] = item["date"]
                pos["catalyst_source"] = {
                    "from": "exact historical hunt block, catalyst hygiene",
                    "block": item["block"], "section": item["section"],
                    "text": item["line"], "backfilled_utc": utc_now(),
                }
                pos.pop("no_sourced_catalyst_date", None)
                applied.append(item["symbol"])
            coverage = live.get("catalyst_coverage") or {}
            positions = live.get("positions") or {}
            coverage.update({"checked_utc": today(), "n_positions": len(positions),
                             "n_dated": sum(bool(p.get("catalyst_date")) for p in positions.values()),
                             "n_undated": sum(not bool(p.get("catalyst_date")) for p in positions.values()),
                             "note": "Dates are backfilled only from exact historical blocks; no date is guessed."})
            live["catalyst_coverage"] = coverage
            tmp = LEDGER.with_name(LEDGER.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(live, f, indent=1)
            os.replace(tmp, LEDGER)
        for item in results:
            if item["symbol"] in applied:
                item["status"] = "backfilled"
    report = {"generated_utc": utc_now(), "paper_only": True, "apply": apply,
              "results": results,
              "counts": {key: sum(r["status"] == key for r in results)
                         for key in sorted({r["status"] for r in results})},
              "proposed_symbols": [r["symbol"] for r in proposed],
              "backfilled_symbols": applied,
              "rule": "Only a future ISO date from the position's exact historical hunt is written. Past, missing and ambiguous dates remain review flags."}
    tmp = OUT.with_name(OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, OUT)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        report = run(args.apply)
    except RuntimeError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    print("CATALYST HYGIENE — %s" % ", ".join("%s=%d" % item for item in report["counts"].items()))
    if report["backfilled_symbols"]:
        print("  backfilled: %s" % ", ".join(report["backfilled_symbols"]))
    elif report["proposed_symbols"]:
        print("  dry-run candidates: %s" % ", ".join(report["proposed_symbols"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
