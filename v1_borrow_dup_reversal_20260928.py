#!/usr/bin/env python3
"""One-off PAPER ledger correction: reverse released borrow charged to cash twice.

Before the 2026-09-24 fix in fundamental_exits.py, an engine exit on a short took
the tranche's released borrow out of the fill's cash_delta, although
fundamental_book.mark had ALREADY debited that borrow from cash as it accrued
(costs_paid_usd).  Fills since the fix carry borrow_already_debited_from_cash=True,
add the borrow back to cash and record borrow_released_cash_correct_usd, which the
equity identity adds back.  Legacy fills were never reversed:
profitability_controls reports the difference as RELEASED_BORROW_DOUBLE_CHARGE.

This script puts each legacy position on the forward-fixed basis:
  * cash += the duplicate (borrow_released_usd - borrow_released_cash_correct_usd);
  * borrow_released_cash_correct_usd := borrow_released_usd, so the equity identity
    adds it back exactly as for forward fills (borrow stays charged ONCE, through
    costs_paid_usd; realised_usd keeps its per-fill borrow disclosure);
  * a per-position `borrow_dup_reversal` record and a ledger-level receipt.
Idempotent: a second run finds nothing.  No trade, no position size, no rule changes.
Dry-run by default; --apply writes under the fundamental ledger lock.
"""
import argparse
import datetime as dt
import json
import os

import desk_lock

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "fundamental_book.json")
TAG = "V1_BORROW_DUP_REVERSAL_20260928"
EPS = 0.00005


def reverse(ledger, utc, tag=TAG):
    """Mutate `ledger`; return (cash_credit_usd, rows).  Pure apart from the dict."""
    rows = []
    for group in ("positions", "closed"):
        for sym, p in (ledger.get(group) or {}).items():
            released = float(p.get("borrow_released_usd") or 0.0)
            correct = float(p.get("borrow_released_cash_correct_usd") or 0.0)
            dup = round(released - correct, 4)
            if dup <= EPS:
                continue
            legacy = [f.get("utc") for f in p.get("exit_fills") or []
                      if f.get("borrow_charged_usd") and not f.get("borrow_already_debited_from_cash")]
            p["borrow_released_cash_correct_usd"] = round(released, 4)
            p["borrow_dup_reversal"] = {"tag": tag, "utc": utc, "usd": dup, "legacy_fill_utcs": legacy,
                                        "note": "legacy short exit took released borrow from cash after daily "
                                                "accrual had already debited it; cash credited once. PAPER"}
            rows.append({"book_group": group, "symbol": sym, "usd": dup, "legacy_fill_utcs": legacy})
    total = round(sum(r["usd"] for r in rows), 4)
    if not rows:
        return 0.0, rows
    credit = round(total, 2)
    before = float(ledger["cash"])
    ledger["cash"] = round(before + credit, 2)
    # Keep the published equity and its identity moving together until the next mark.
    if isinstance(ledger.get("equity"), (int, float)):
        ledger["equity"] = round(float(ledger["equity"]) + credit, 2)
    if isinstance(ledger.get("equity_identity_usd"), (int, float)):
        ledger["equity_identity_usd"] = round(float(ledger["equity_identity_usd"]) + total, 2)
        if isinstance(ledger.get("equity"), (int, float)):
            ledger["equity_identity_gap_usd"] = round(ledger["equity"] - ledger["equity_identity_usd"], 2)
    ledger.setdefault("ledger_corrections", []).append({
        "tag": tag, "utc": utc, "kind": "released_borrow_duplicate_cash_debit_reversal",
        "cash_before": before, "cash_after": ledger["cash"], "usd": credit, "positions": len(rows),
        "paper_only": True,
        "note": "Borrow stays charged once via costs_paid_usd; not a trading gain."})
    return credit, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with desk_lock.ledger_lock("fundamental"):
        with open(LEDGER) as f:
            d = json.load(f)
        credit, rows = reverse(d, utc)
        for r in rows:
            print("  %-12s %-9s +%.4f  legacy fills %s" % (r["symbol"], r["book_group"], r["usd"], r["legacy_fill_utcs"]))
        print("%s %d positions, cash credit $%.2f" % ("APPLY" if a.apply else "DRY-RUN", len(rows), credit))
        if a.apply and rows:
            tmp = LEDGER + ".borrowrev.tmp"
            with open(tmp, "w") as f:
                json.dump(d, f, indent=2, default=str)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, LEDGER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
