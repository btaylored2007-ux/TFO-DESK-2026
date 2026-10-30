#!/usr/bin/env python3
"""fundamental_watch.py — catalyst calendar for the TFO Fundamental Desk. READ-ONLY.

WHAT PROBLEM THIS SOLVES
------------------------
Every one of the 53 live positions was opened with a dated `KILLS IT` condition —
a specific number or event that would disconfirm the thesis. Those conditions are
worthless if nobody reads them on the day they resolve.

The measurement that shaped this file: only **8 of 53** positions carry a forward
catalyst inside 34 days, about $26k of notional. So this was never a volume
problem — a human can absolutely read eight filings. What was missing was
anything that put the date in front of a human on the right morning. China
Shenhua's H1 lands 2026-08-28, and it is the decisive test of the sharpest short
thesis in the run; without a calendar it would simply pass.

SO: the machine keeps the calendar and raises the item. It does NOT judge the
thesis. Extracting "is HOKA growth negative?" from a filing is a reading task,
and this file will not pretend a regex can do it.

WHAT IT DOES
------------
Parses each research block's EVENT CALENDAR / CATALYST / KILLS IT sections for
forward dates, matches them to live positions, and publishes
`reports/fundamental_watch.json` with anything due. Items DUE TODAY or OVERDUE
are marked `dispatch: true` — that is the signal for the review agent to read the
actual disclosure and report against the pre-registered disconfirming condition.

Dates that were never sourced are reported as UNDATED rather than guessed. A
position whose block says "expect late October on cadence" gets no date here,
because a cadence-inferred date is not a date and acting on one would be acting
on an assumption the analyst explicitly declined to make.

PAPER ONLY. Reads blocks and the ledger; writes one JSON. Trades nothing.
"""
import glob
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
BLOCKS = "/Users/test/prospect/var/global_run/blocks_*.md"
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
OUT = os.path.join(BASE, "reports", "fundamental_watch.json")

SECTION = re.compile(r"^(EVENT CALENDAR|CALENDAR|CATALYST|KILLS IT)\s*:", re.I)
FIELD = re.compile(r"^[A-Z][A-Z0-9 /_&]{2,26}\s*:")
DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def today():
    return time.strftime("%Y-%m-%d")


def parse_blocks():
    """symbol -> {section -> [(date, line)]} for every dated forward reference."""
    found = {}
    for path in sorted(glob.glob(BLOCKS)):
        sym, sect = None, None
        for raw in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^SYMBOL:\s*(\S+)", raw)
            if m:
                sym = re.sub(r"[`*<>]", "", m.group(1)).strip()
                sect = None
                continue
            if SECTION.match(raw):
                sect = SECTION.match(raw).group(1).upper()
            elif FIELD.match(raw):
                sect = None
            if sym and sect:
                for d in DATE.findall(raw):
                    found.setdefault(sym, {}).setdefault(sect, []).append(
                        (d, raw.strip()[:300]))
    return found


def build():
    try:
        led = json.load(open(LEDGER))
    except OSError as e:
        return {"error": "ledger unreadable: %s" % e, "items": []}
    live = led.get("positions") or {}
    blocks = parse_blocks()
    t = today()
    items, undated = [], []

    for sym, pos in live.items():
        secs = blocks.get(sym) or {}
        ledger_date = str(pos.get("catalyst_date") or "")[:10]
        ledger_source = pos.get("catalyst_source") or {}
        all_block_dates = sorted({d for lst in secs.values() for d, _ in lst})
        if ledger_date and DATE.fullmatch(ledger_date):
            nxt = ledger_date
            ctx = [{"section": "LEDGER CATALYST_DATE",
                    "line": (ledger_source.get("text") if isinstance(ledger_source, dict)
                             else None) or "sourced date recorded on the position"}]
            source_kind = "ledger"
        elif all_block_dates:
            # Raw historical blocks contain publication and comparison dates in
            # their calendar prose.  Only an explicitly stored catalyst_date is
            # safe to treat as overdue.  For an unbackfilled legacy block, retain
            # the original conservative behaviour: use a future date only.
            future = [d for d in all_block_dates if d >= t]
            if not future:
                undated.append({
                    "symbol": sym, "side": pos.get("side"),
                    "notional_usd": pos.get("notional_usd"),
                    "reason": ("no future date sourced in the block — historical dates are not "
                               "promoted to an expiry trigger")})
                continue
            nxt = future[0]
            ctx = []
            for sect, lst in secs.items():
                for d, line in lst:
                    if d == nxt:
                        ctx.append({"section": sect, "line": line})
            source_kind = "research_block"
        else:
            undated.append({
                "symbol": sym, "side": pos.get("side"),
                "notional_usd": pos.get("notional_usd"),
                "reason": ("no forward date sourced in the block — the analyst "
                           "did not date one, and a cadence-inferred date is not "
                           "a date")})
            continue
        days = (time.mktime(time.strptime(nxt, "%Y-%m-%d"))
                - time.mktime(time.strptime(t, "%Y-%m-%d"))) / 86400.0
        items.append({
            "symbol": sym, "side": pos.get("side"),
            "notional_usd": pos.get("notional_usd"),
            "net_gain_pct": None,
            "next_date": nxt, "days_away": int(round(days)),
            "dispatch": days <= 0,
            "context": ctx[:4], "source_kind": source_kind,
            "all_forward_dates": [d for d in all_block_dates if d >= t][:6],
        })

    # attach the trading rule's current read so a reviewer sees both at once
    for it in items:
        d = next((x for x in (led.get("trade_decisions") or [])
                  if x.get("symbol") == it["symbol"]), None)
        if d:
            it["net_gain_pct"] = d.get("net_gain_pct")
            it["rule_action"] = d.get("action")

    items.sort(key=lambda x: (x["next_date"], -(x["notional_usd"] or 0)))
    due = [i for i in items if i["dispatch"]]
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "as_of_date": t,
        "book": "TFO Fundamental Desk",
        "n_live": len(live), "n_dated": len(items), "n_undated": len(undated),
        "n_due_now": len(due),
        "due_now": due,
        "next_14_days": [i for i in items if 0 < i["days_away"] <= 14],
        "all_dated": items,
        "undated": undated,
        "note": ("The calendar is machine-kept. The JUDGEMENT is not: an item "
                 "marked dispatch:true means a human or a review agent must read "
                 "the actual disclosure against the block's pre-registered "
                 "KILLS IT text. This file never decides a thesis has failed."),
    }


def main():
    w = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(w, f, indent=2)
    os.replace(tmp, OUT)
    if w.get("error"):
        print("!! fundamental_watch:", w["error"])
        return 1
    print("=" * 74)
    print("TFO FUNDAMENTAL DESK — CATALYST WATCH   [%s]" % w["as_of_date"])
    print("=" * 74)
    print("  live %d · dated %d · undated %d · DUE NOW %d"
          % (w["n_live"], w["n_dated"], w["n_undated"], w["n_due_now"]))
    if w["due_now"]:
        print("\n  !! DUE NOW — read the disclosure against the block's KILLS IT text:")
        for i in w["due_now"]:
            print("    %s  %-10s %-5s $%-8s  net %s"
                  % (i["next_date"], i["symbol"], i["side"],
                     "{:,.0f}".format(i["notional_usd"] or 0),
                     ("%.2f%%" % i["net_gain_pct"]) if i["net_gain_pct"] is not None else "n/a"))
    if w["next_14_days"]:
        print("\n  next 14 days:")
        for i in w["next_14_days"]:
            print("    %s  %-10s %-5s $%-8s  (%d days)"
                  % (i["next_date"], i["symbol"], i["side"],
                     "{:,.0f}".format(i["notional_usd"] or 0), i["days_away"]))
    print("\n  %d position(s) carry NO sourced forward date — listed in the JSON."
          % w["n_undated"])
    print("  watch -> %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
