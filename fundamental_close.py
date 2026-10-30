#!/usr/bin/env python3
"""fundamental_close.py — thesis-break exit for the TFO Fundamental Desk. PAPER ONLY.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
----------------------------------------
There are three ways a position leaves this book, and they are deliberately
separate mechanisms:

  1. THE TRADING RULE (`fundamental_exits.py`) — mechanical, automatic. Scales out
     at +5%/+10%/+15% NET of fill, exit and borrow cost, and runs the last quarter
     on a trailing giveback. No judgement, no human, no override.
  2. THE BOOK KILL (`book_kill.py`) — mechanical, automatic. Freezes NEW entries at
     the desk-wide book-kill drawdown (-18% since KILL_THRESHOLD_AMENDMENT.md,
     2026-08-28). It never force-closes anything.
  3. THIS FILE — a THESIS BREAK. The pre-registered `KILLS IT` condition written
     into a research block BEFORE the position opened has been met.

This file exists because (3) cannot be automated honestly. "HOKA net sales growth
turning negative" or "gross margin stabilising at or above 15.5%" are reading
tasks against a filing, not expressions a regex can evaluate. So the calendar is
machine-kept (`fundamental_watch.py`) and the JUDGEMENT is made by a reviewer who
reads the disclosure.

THE EVIDENCE REQUIREMENT IS THE POINT
-------------------------------------
A close requires a reason, and the reason must carry a QUOTED FIGURE and a DATED
SOURCE. This file enforces the shape of that — it refuses a reason that is too
short to contain evidence, and refuses one with no digits in it, because
"the thesis looks broken" is an opinion and a fabricated exit is worse than a
late one. It cannot check that the quoted number is TRUE; that is the reviewer's
duty and the reason the whole trail is logged permanently.

An exit written here is irreversible in the record. `--dry-run` shows the effect
first and changes nothing.

PAPER ONLY. No broker, no order, no credential.

Usage:
  python3 fundamental_close.py --symbol LULU \\
      --reason "Q2 print 2026-09-03: NA comps -4%, FY EPS guided to 10.20 from 10.95-11.15"
  python3 fundamental_close.py --symbol LULU --reason "..." --dry-run
"""
import argparse
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
EXIT_LOG = os.path.join(BASE, "reports", "fundamental_thesis_exits.jsonl")

MIN_REASON_CHARS = 40


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_reason(reason):
    """Refuse a reason that cannot possibly contain evidence.

    Not a truth test — nothing here can verify the quoted figure. It is a shape
    test, and it exists because the failure mode is a confident sentence with no
    number in it.
    """
    r = (reason or "").strip()
    if len(r) < MIN_REASON_CHARS:
        return (False, "reason is %d chars; a thesis break must quote the figure "
                       "and name the dated source (min %d)"
                       % (len(r), MIN_REASON_CHARS))
    if not re.search(r"\d", r):
        return (False, "reason contains no digits. A pre-registered KILLS IT "
                       "condition is a NUMBER or a DATED EVENT — 'the thesis "
                       "looks broken' is an opinion, not evidence.")
    return (True, "")


def close(symbol, reason, dry_run=False):
    ok, why = validate_reason(reason)
    if not ok:
        print("!! REFUSED: %s" % why)
        return 2

    try:
        with open(LEDGER) as f:
            led = json.load(f)
    except OSError as e:
        print("!! ledger unreadable (%s) — refusing to act blind" % e)
        return 1

    pos = (led.get("positions") or {}).get(symbol)
    if not pos:
        print("!! %s is not an open position in this book." % symbol)
        opencount = len(led.get("positions") or {})
        print("   %d positions are open. Closed positions are under `closed`." % opencount)
        return 1

    if pos.get("mark_stale"):
        print("!! %s is UNMARKED (%s)." % (symbol, pos.get("mark_error", "unknown")))
        print("   Refusing to close at an unknown price. Re-run fundamental_book.py "
              "to mark it, then retry. Closing an unmarked position would book a "
              "realised P&L that no price supports.")
        return 2

    units, e, m = pos["units"], pos["entry_px_usd"], pos["mark_usd"]
    gross = (m - e) * units if pos["side"] == "long" else (e - m) * units
    try:
        import fundamental_exits as fx
        exit_cost = (units * m) * (fx.EXIT_COST_BPS + fx.SLIPPAGE_BPS) / 10_000.0
    except Exception:                          # noqa: BLE001
        exit_cost = (units * m) * 15.0 / 10_000.0
    borrow = float(pos.get("borrow_accrued_usd") or 0.0)
    realised = gross - exit_cost - borrow
    cash_delta = ((units * m) - exit_cost if pos["side"] == "long"
                  else pos["notional_usd"] + realised)

    print("=" * 74)
    print("THESIS-BREAK CLOSE%s — %s (%s)"
          % (" [DRY RUN]" if dry_run else "", symbol, pos["side"]))
    print("=" * 74)
    print("  units %.4f @ entry $%.4f -> mark $%.4f" % (units, e, m))
    print("  gross P&L      $%10.2f" % gross)
    print("  exit cost      $%10.2f" % -exit_cost)
    print("  borrow accrued $%10.2f" % -borrow)
    print("  realised       $%10.2f" % realised)
    print("  cash delta     $%10.2f" % cash_delta)
    print("  reason: %s" % reason)
    if dry_run:
        print("\n  DRY RUN — nothing was changed.")
        return 0

    rec = {
        "utc": _utc(), "symbol": symbol, "side": pos["side"],
        "units": units, "entry_px_usd": e, "exit_px_usd": m,
        "gross_usd": round(gross, 2), "exit_cost_usd": round(exit_cost, 2),
        "borrow_accrued_usd": round(borrow, 2),
        "realised_usd": round(realised, 2), "cash_delta_usd": round(cash_delta, 2),
        "reason": reason, "mechanism": "thesis break (pre-registered KILLS IT)",
        "entry_utc": pos.get("entry_utc"),
    }
    if pos.get("research_provenance"):
        rec["research_provenance"] = pos["research_provenance"]
    led["cash"] = round(led["cash"] + cash_delta, 2)
    led["realised_usd"] = round(led.get("realised_usd", 0.0) + realised, 2)
    led.setdefault("closed", {})[symbol] = {**pos, "close_record": rec}
    led["positions"].pop(symbol)
    led.setdefault("thesis_exits", []).insert(0, rec)

    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(led, f, indent=2, default=str)
    os.replace(tmp, LEDGER)
    try:
        with open(EXIT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print("!! could not append the permanent exit log (%s) — the ledger was "
              "still updated; fix the log before the next close" % e)

    print("\n  CLOSED. %d positions remain. Cash now $%s."
          % (len(led["positions"]), "{:,.2f}".format(led["cash"])))
    print("  logged -> %s" % EXIT_LOG)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--reason", required=True,
                    help="MUST quote the figure and name the dated source")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return close(a.symbol, a.reason, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
