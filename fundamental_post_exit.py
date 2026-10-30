#!/usr/bin/env python3
"""fundamental_post_exit.py — what happened AFTER we sold. PAPER ONLY.

Funder question, 2026-08-31: *"should we close out positions earlier if they are
showing profit... whilst still keeping an eye on those closed out positions."*

The second half of that question is the one this answers, and it was a real gap:
**when a rung fired we banked the cash and stopped looking.** So the desk had no
way — none — of knowing whether it exits too early or too late. It could have run
for 182 days and still been unable to answer the question.

WHAT IT DOES
------------
Every scale-out, stop and close already records its exit price in `exit_fills`
(`px_usd`) or in a `close_record`. This walks both ledgers, collects every one of
them, re-marks the symbol at the CURRENT price, and computes the only number that
matters:

    FORGONE = what the released units would be worth today
              MINUS what we actually took for them

  * FORGONE POSITIVE -> the price kept going our way. WE EXITED EARLY.
  * FORGONE NEGATIVE -> the price turned after we left. WE EXITED WELL.

Direction is handled per side: a long forgoes gains when the price RISES after the
exit; a short forgoes gains when it FALLS.

WHAT THIS IS NOT — read before acting on it
-------------------------------------------
**This is a MEASUREMENT FOR THE REVIEW DATE, NOT A LIVE SIGNAL.** It must never be
used to tighten or loosen the ladder mid-flight. The registration is explicit that
a constant may only change "before it runs, never after a result is known", and a
running tally of forgone gains is exactly the kind of result that tempts a desk
into refitting its own rule.

Two statistical warnings that belong on the face of it:

1. **A handful of exits is not a distribution.** If this method works at all, it
   plausibly works because a few large winners pay for many small losers — so an
   early sample of "we exited early" tells you almost nothing, and acting on it
   would destroy the very thing being measured.
2. **Forgone gain ignores what the freed capital then did.** Money taken off SOL.JO
   funded something else. A complete answer needs the redeployment, not just the
   counterfactual of holding.

**Read it in February, with the full sample, alongside the redeployment.**

PAPER ONLY. Reads prices, writes one report file. No broker, no order.
"""
import json
import os
import sys

import fundamental_book as fb

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "post_exit_tracker.json")
LEDGERS = (("MAIN BOOK", "reports/fundamental_book.json"),
           ("SLEEVE", "reports/fundamental_spec_book.json"))


def collect_exits():
    """Every recorded exit across both books, open positions and closed alike."""
    out = []
    for book, rel in LEDGERS:
        path = os.path.join(BASE, rel)
        try:
            with open(path) as f:
                d = json.load(f)
        except Exception:                                  # noqa: BLE001
            continue
        buckets = [(d.get("positions") or {}, "open"),
                   (d.get("closed") or {}, "closed")]
        for holdings, state in buckets:
            for sym, p in holdings.items():
                side = p.get("side", "long")
                for fill in (p.get("exit_fills") or []):
                    if not fill.get("px_usd"):
                        continue
                    out.append({
                        "book": book, "sym": sym, "side": side,
                        "position_state": state,
                        "action": fill.get("action"),
                        "utc": fill.get("utc"),
                        "units": fill.get("units"),
                        "exit_px_usd": fill.get("px_usd"),
                        "realised_usd": fill.get("realised_usd"),
                        "net_gain_pct_at_exit": fill.get("net_gain_pct"),
                        "reason": fill.get("reason"),
                    })
                cr = p.get("close_record")
                if cr and cr.get("exit_px_usd"):
                    out.append({
                        "book": book, "sym": sym, "side": side,
                        "position_state": state,
                        "action": cr.get("mechanism") or "close",
                        "utc": cr.get("utc"), "units": cr.get("units"),
                        "exit_px_usd": cr.get("exit_px_usd"),
                        "realised_usd": cr.get("realised_usd"),
                        "net_gain_pct_at_exit": None,
                        "reason": (cr.get("reason") or "")[:300],
                    })
    return out


def build(verbose=True):
    exits = collect_exits()
    if not exits:
        report = {"generated_utc": fb._utc(), "paper_only": True,
                  "n_exits": 0,
                  "note": "no exits recorded yet — nothing has been sold"}
        with open(OUT, "w") as f:
            json.dump(report, f, indent=1)
        if verbose:
            print("no exits recorded yet")
        return report

    syms = sorted({e["sym"] for e in exits})
    prices, failures = fb.fetch_prices(syms)

    rows, total_forgone, priced = [], 0.0, 0
    for e in exits:
        q = prices.get(e["sym"])
        row = dict(e)
        if not q or not q.get("px_usd"):
            # An unpriceable symbol is reported as unpriceable. It is NOT counted
            # as zero forgone — that would quietly flatter the tally toward
            # "we exited perfectly".
            row["current_px_usd"] = None
            row["forgone_usd"] = None
            row["verdict"] = "NOT PRICED — %s" % failures.get(e["sym"], "no quote")
            rows.append(row)
            continue
        cur = q["px_usd"]
        units = e["units"] or 0.0
        move = (cur - e["exit_px_usd"]) if e["side"] == "long" else (e["exit_px_usd"] - cur)
        forgone = move * units
        pct = (move / e["exit_px_usd"] * 100) if e["exit_px_usd"] else None
        row.update({
            "current_px_usd": round(cur, 6),
            "move_since_exit_pct": None if pct is None else round(pct, 3),
            "forgone_usd": round(forgone, 2),
            "verdict": ("EXITED EARLY — it kept going our way"
                        if forgone > 0 else
                        "EXITED WELL — it turned after we left"),
        })
        rows.append(row)
        total_forgone += forgone
        priced += 1

    early = [r for r in rows if (r.get("forgone_usd") or 0) > 0]
    well = [r for r in rows if r.get("forgone_usd") is not None
            and r["forgone_usd"] <= 0]
    realised = sum((r.get("realised_usd") or 0) for r in rows)

    report = {
        "generated_utc": fb._utc(), "paper_only": True,
        "n_exits": len(rows), "n_priced": priced,
        "realised_taken_usd": round(realised, 2),
        "total_forgone_usd": round(total_forgone, 2),
        "n_exited_early": len(early), "n_exited_well": len(well),
        "reading": ("FORGONE POSITIVE = the price kept going our way after we "
                    "sold, i.e. we exited EARLY. NEGATIVE = it turned after we "
                    "left, i.e. we exited WELL."),
        "DO_NOT_ACT_ON_THIS_YET": (
            "MEASUREMENT FOR THE 2027-02-25 REVIEW, NOT A LIVE SIGNAL. The "
            "registration allows a constant to change only BEFORE it runs, never "
            "after a result is known — and this IS a result. Two caveats belong "
            "with every reading of it: (1) a handful of exits is not a "
            "distribution, and if this method works it plausibly works because a "
            "few large winners pay for many small losers, so an early 'we exited "
            "early' tally is close to meaningless; (2) forgone gain ignores what "
            "the freed capital then earned — the money taken off one name funded "
            "another, and a complete answer needs the redeployment."),
        "exits": sorted(rows, key=lambda r: -(r.get("forgone_usd") or 0)),
    }
    with open(OUT, "w") as f:
        json.dump(report, f, indent=1)

    if verbose:
        print("POST-EXIT TRACKER — %d exits, %d priced" % (len(rows), priced))
        print("  realised taken   $%s" % "{:,.2f}".format(realised))
        print("  total forgone    $%s   (positive = exited early)"
              % "{:,.2f}".format(total_forgone))
        print("  early %d / well %d" % (len(early), len(well)))
        for r in report["exits"][:8]:
            f_ = r.get("forgone_usd")
            print("   %-9s %-5s %-14s exit $%-9.4f now %-9s forgone %s"
                  % (r["sym"], r["side"], (r["action"] or "")[:14],
                     r["exit_px_usd"],
                     ("$%.4f" % r["current_px_usd"]) if r.get("current_px_usd") else "n/a",
                     ("{:+,.2f}".format(f_)) if f_ is not None else "NOT PRICED"))
    return report


if __name__ == "__main__":
    build(verbose="--quiet" not in sys.argv)
