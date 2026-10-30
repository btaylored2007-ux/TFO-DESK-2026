#!/usr/bin/env python3
"""cash_yield_books.py — accrue T-bill yield on idle cash in the NON-sleeve books.

WHY
---
Nine tournament sleeves have accrued cash yield since 2026-08-27. Eight other
books never did, and between them they hold **$325,464 of idle cash earning
exactly zero** — the floors, the two fundamental books, the carry probe.

A capital-allocation study measured what that costs: roughly **$12,238/yr** of
certain money, against $0 of defensible expected return from any resize toward a
so-called winner (attribution found ZERO skill books in 32). This is the highest-
certainty money on the desk precisely because it requires no edge, no forecast
and no new risk.

WHAT IT DOES NOT DO
-------------------
It does not invent money. `cash_yield.accrue` refuses a rate older than 24h,
refuses an absurd rate, and accrues ZERO — loudly — on any failure. The
cumulative figure is kept in its own field so it is separable from trading P&L
forever: a reader can always ask "what did this book earn from RATES rather than
from SKILL?" and get an exact answer.

CASH SHAPES
-----------
Books store cash three ways, so this handles all three and accrues PER CONTAINER
(each keeps its own accrual clock, so a desk added later does not inherit another
desk's elapsed time):
  * top level              d["cash"]                    fundamental books, probes
  * per desk               d["desks"][name]["cash"]     the four floors
  * per band               d["bands"][name]["cash"]     crypto rumour

THE ENGINE IS EXCLUDED. `kraken_paper_bot.py` holds $40,119 of idle cash and is a
PROTECTED file — it is never edited without explicit funder authorisation. That
cash is named in the report as still uncovered rather than quietly swept in.
"""
import time


def _accrue_one(container, label, verbose=True):
    """Accrue on one cash-holding dict. Returns interest added (0.0 on any failure)."""
    try:
        import cash_yield
    except Exception as e:                              # noqa: BLE001
        if verbose:
            print("   CASH-YIELD unavailable (%s) — accrued ZERO" % type(e).__name__)
        return 0.0
    cash = container.get("cash")
    if not isinstance(cash, (int, float)) or cash <= 0:
        return 0.0
    try:
        # A process that missed many scheduled ticks cannot reconstruct historical
        # cash balances. Start a new accrual interval instead of back-crediting.
        prior = container.get("cash_yield_last_epoch")
        if isinstance(prior, (int, float)) and time.time() - prior > 7200:
            container.pop("cash_yield_last_epoch", None)
            container["cash_yield_gap_skipped"] = {"since_epoch": prior, "resumed_epoch": time.time()}
        got = cash_yield.accrue(container, cash)
    except Exception as e:                              # noqa: BLE001 - never break a tick
        msg = "CASH-YIELD ERROR: %s: %s" % (label, str(e)[:180])
        print("   " + msg)
        container["cash_yield_error"] = {
            "msg": msg,
            "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        return 0.0
    if got:
        container["cash"] = round(cash + got, 8)
    return got or 0.0


def accrue_book(d, label="book", verbose=True):
    """Accrue across every cash container in a ledger. Returns total added.

    Safe to call on any book: a ledger with no cash anywhere returns 0.0 and
    changes nothing.
    """
    total = 0.0
    total += _accrue_one(d, label, verbose)
    for group in ("desks", "bands"):
        for name, sub in (d.get(group) or {}).items():
            if isinstance(sub, dict):
                total += _accrue_one(sub, "%s/%s" % (label, name), verbose)
    if total:
        d["cash_yield_total_accrued_usd"] = round(
            float(d.get("cash_yield_total_accrued_usd") or 0.0) + total, 6)
        d["cash_yield_last_book_utc"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if verbose:
            print("   cash yield: +$%.4f accrued on idle cash (%s)" % (total, label))
    return total


def yield_eligible_cash(state):
    """CM-070: cash that has ACTUALLY been lent out, not cash inflated by shorts.

    `set_holdings` computes  cash = equity - sum(units * px) - cost.  For a
    DOLLAR-NEUTRAL long/short sleeve the longs and shorts cancel in that sum, so
    `cash` comes out at roughly full equity even though half the book is a short
    position. Paying T-bill on that figure credits interest on SHORT PROCEEDS.

    That is free money in the flattering direction: the sleeve collects the
    interest benefit of the short leg while charging no borrow on it. Measured
    on cef_discount (the only sleeve currently short): $5,000 of proceeds =
    $188.60/yr, which together with the omitted borrow is ~3.4%/yr against a
    claimed ~7%/yr edge — roughly half the "edge" was bookkeeping.

    So: subtract the absolute market value of short holdings, floor at zero.
    This can only ever REDUCE credited interest. It is a tightening.

    Returns the eligible figure; falls back to plain cash when a sleeve has no
    holdings/entry_px structure (the non-tournament books, which hold no shorts
    in their `cash` line).
    """
    cash = state.get("cash")
    if not isinstance(cash, (int, float)) or cash <= 0:
        return 0.0
    h = state.get("holdings")
    px = state.get("entry_px") or {}
    if not isinstance(h, dict) or not h:
        return cash
    short_mv = 0.0
    for t, u in h.items():
        try:
            if u < 0:
                short_mv += abs(float(u) * float(px.get(t, 0.0)))
        except (TypeError, ValueError):
            continue
    return max(0.0, cash - short_mv)
