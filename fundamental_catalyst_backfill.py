#!/usr/bin/env python3
"""fundamental_catalyst_backfill.py — put a DATE on every position, or say why not.

PAPER ONLY.

THE GAP THIS CLOSES
-------------------
On 2026-08-31 the catalyst watch reported **47 of 60 main-book positions carrying
no sourced forward date**. The catalyst-expiry rule ("sell the news", registered
2026-08-28) can only act on a position that HAS a date — so those 47 were
**permanently immune**: they could never expire, never be forced to a decision,
and would have drifted to the review date unexamined while the rule quietly did
nothing. A rule that silently applies to 13 of 60 positions is not a rule.

The dates were never missing from the RESEARCH — `fundamental_watch.py` already
parses EVENT CALENDAR / CATALYST / KILLS IT out of the blocks and knew about 13 of
them. They were simply never written onto the positions the engine actually reads.

WHAT IT DOES, AND WHAT IT REFUSES TO DO
---------------------------------------
* Where a block carries a **SOURCED, PARSEABLE forward date**, it is written to
  `catalyst_date` on the position, with `catalyst_source` recording where it came
  from. The position becomes expirable.
* Where **no date exists**, it writes `catalyst_date: null` AND an explicit
  `no_sourced_catalyst_date` flag naming the consequence.

**IT NEVER INVENTS A DATE.** A guessed date would make a position expirable on a
fiction — worse than leaving it immune, because the close would look principled.
The three ways a sourced catalyst turns out false (fabricated / stale / later
killed) are already in the record; a fourth, "assigned by a backfill script for
tidiness", is not being added.

The undated remainder is therefore made LOUD rather than fixed: visible in the
ledger, countable, and reportable — so the gap is a known quantity instead of an
invisible one.
"""
import json
import os
import sys

import desk_lock
import fundamental_watch as fw

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")


def backfill(dry_run=False, verbose=True):
    r = fw.build()
    dated = {i["symbol"]: i for i in (r.get("all_dated") or []) if i.get("next_date")}
    undated = {i["symbol"] if isinstance(i, dict) else i
               for i in (r.get("undated") or [])}

    if verbose:
        print("watch reports: %s live | %s dated | %s undated"
              % (r.get("n_live"), r.get("n_dated"), r.get("n_undated")))

    with desk_lock.ledger_lock("fundamental"):
        with open(LEDGER) as f:
            d = json.load(f)
        wrote, flagged, already = [], [], []
        for sym, p in d["positions"].items():
            if p.get("catalyst_date"):
                already.append(sym)
                continue
            hit = dated.get(sym)
            if hit and hit.get("next_date"):
                if not dry_run:
                    p["catalyst_date"] = hit["next_date"]
                    ctx = hit.get("context") or []
                    p["catalyst_source"] = {
                        "from": "research block EVENT CALENDAR/CATALYST, parsed by "
                                "fundamental_watch",
                        "text": (json.dumps(ctx)[:400] if ctx else None),
                        "backfilled_utc": fw.today(),
                    }
                wrote.append((sym, hit["next_date"]))
            else:
                if not dry_run:
                    p["catalyst_date"] = None
                    p["no_sourced_catalyst_date"] = (
                        "NO DATED CATALYST could be parsed from this position's "
                        "research block. CONSEQUENCE: the catalyst-expiry rule "
                        "CANNOT act on it — it will never be forced to a decision "
                        "and will drift to the review date unless a desk gives it "
                        "a sourced date. NOT auto-assigned: a guessed date would "
                        "make the position expirable on a fiction, and a close "
                        "that looks principled but rests on an invented date is "
                        "worse than an open position that is honestly flagged.")
                flagged.append(sym)
        if not dry_run:
            d["catalyst_coverage"] = {
                "checked_utc": fw.today(),
                "n_positions": len(d["positions"]),
                "n_dated": len(wrote) + len(already),
                "n_undated": len(flagged),
                "note": ("A position with no catalyst_date is IMMUNE to the "
                         "catalyst-expiry rule. This count is the size of that "
                         "blind spot and must be driven down by sourcing dates, "
                         "never by inventing them."),
            }
            tmp = LEDGER + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f, indent=1)
            os.replace(tmp, LEDGER)

    if verbose:
        print("\n  already dated : %d" % len(already))
        print("  BACKFILLED    : %d" % len(wrote))
        for s, dt in sorted(wrote, key=lambda x: x[1]):
            print("     %-9s -> %s" % (s, dt))
        print("  NO SOURCED DATE (flagged, NOT invented): %d" % len(flagged))
        print("     %s" % ", ".join(sorted(flagged)[:18]))
        if len(flagged) > 18:
            print("     ... and %d more" % (len(flagged) - 18))
    return {"backfilled": wrote, "flagged": flagged, "already": already}


if __name__ == "__main__":
    backfill(dry_run="--dry-run" in sys.argv)
