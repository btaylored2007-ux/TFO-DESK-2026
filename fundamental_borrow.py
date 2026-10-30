#!/usr/bin/env python3
"""fundamental_borrow.py — modelled stock-borrow cost for the short book. PAPER ONLY.

WHY THIS EXISTS
---------------
The book opened with 46 shorts and NO borrow cost modelled, and that is not a
rounding error — it is a hole in the trading rule. The rule refuses any exit
below 5% NET of fill and exit cost. If borrow is missing from "cost", the gate is
measuring the wrong number.

The arithmetic that forced this file:

  * Liquid large-cap shorts borrow around 0.50%/yr. On ~$132k over a 182-day
    clock that is roughly $330. Genuinely noise.
  * FOUR positions are bitcoin miners and crypto-infrastructure names — MARA,
    CLSK, KEEL, BKKT, $10,528 between them. Names of that kind routinely borrow
    at 8-30%/yr. At 15% over 182 days that is ~7.5% — LARGER THAN THE ENTIRE 5%
    GATE.

So on precisely the positions where the thesis is most aggressive, unmodelled
borrow can turn a winner into a loser and the gate would never see it. Modelling
it is what makes the 5% threshold mean what the funder said it means.

WHAT THIS IS NOT
----------------
These are MODELLED tiers, not quotes. No broker is connected, no borrow was
located, and no rate here was sourced from a securities-lending desk. A real
short book must confirm availability and rate per name before it trades. The
tiers are deliberately CONSERVATIVE (erring expensive) because the failure mode
that matters is a gate that is too easy to clear.

DOUBTFUL BORROW IS NOT A COST PROBLEM, IT IS AN EXISTENCE PROBLEM
-----------------------------------------------------------------
A short you cannot borrow is not an expensive position, it is not a position.
Names flagged `borrow_doubtful` are marked in the ledger and surfaced on the
dashboard so the book never quietly reports P&L on a trade that could not have
been put on. They are NOT auto-closed — that is the funder's call — but they are
never presented as executable.

PAPER ONLY.
"""

# Annualised, modelled, charged daily on the SHORT's marked value.
TIER_RATES = {
    "liquid": 0.0050,      # developed large cap, deep lending pool
    "elevated": 0.0300,    # EM, smaller float, or a crowded short
    "hard": 0.1500,        # crypto miners, distressed, heavily shorted
}
DEFAULT_TIER = "elevated"   # unknown => assume the more expensive side

# Explicit classification. A name absent from here gets DEFAULT_TIER, which is
# deliberately not the cheapest tier -- an unclassified short should never be
# assumed easy to borrow.
HARD_TO_BORROW = {
    "MARA": "bitcoin miner; equity-funded capex, crowded short, historically tight",
    "CLSK": "bitcoin miner; funding gap thesis, crowded short",
    "KEEL": "ex-BITF, post-pivot micro cap; thin float after relisting",
    "BKKT": "crypto infrastructure; share count +1,292% since FY2022, micro float",
    "CLF":  "levered steel, debt $7.70bn vs $6.58bn market cap; distressed borrow",
    "STLA": "TTM net income -EUR 19.47bn, dividend suspended; crowded",
}
BORROW_DOUBTFUL = {
    # Availability, not price. Flagged, never auto-closed.
    "KEEL": "micro cap post-relisting (BITF->KEEL 2026-04-06); borrow may not exist at size",
    "BKKT": "micro float after 1,292% dilution; locate is unlikely to be reliable",
}
LIQUID_MARKETS = ("US",)     # inferred from ticker shape below

_LIQUID_NAMES = {
    "VLO", "MPC", "PGR", "TSN", "LULU", "BLDR", "SWKS", "UPS", "SWK", "FIS",
    "ES", "HUM", "ALGN", "AFL", "HRL", "SW", "LEN", "AA", "SCCO", "HAL",
}


def tier_for(sym):
    """Return (tier, rate, why). Explicit rules only -- never a silent default."""
    if sym in HARD_TO_BORROW:
        return "hard", TIER_RATES["hard"], HARD_TO_BORROW[sym]
    if sym in _LIQUID_NAMES:
        return "liquid", TIER_RATES["liquid"], "US developed large cap, deep lending pool"
    if "." not in sym:
        # US-listed but not on the explicit liquid list -- do not assume cheap.
        return DEFAULT_TIER, TIER_RATES[DEFAULT_TIER], "US-listed, not classified liquid"
    return DEFAULT_TIER, TIER_RATES[DEFAULT_TIER], "non-US listing, borrow pool not verified"


def model():
    return {"tier_rates_annual": TIER_RATES, "default_tier": DEFAULT_TIER,
            "charged_on": "short marked value, accrued daily",
            "sourced": False,
            "note": ("MODELLED tiers, not quotes. No borrow was located and no "
                     "securities-lending desk was consulted. Conservative by "
                     "design: a too-cheap assumption makes the 5% gate too easy "
                     "to clear, which is the failure mode that matters.")}


def accrue(ledger, now_epoch, utc):
    """Charge borrow on every short since the last accrual. Returns total USD.

    Idempotent in the sense that it charges only elapsed time: running twice in
    the same second costs ~nothing. It never charges a long, and never charges a
    short that could not be marked (an unpriced position has no known value to
    charge against, and inventing one would be the cost-marking error).
    """
    total = 0.0
    detail = {}
    for sym, p in (ledger.get("positions") or {}).items():
        if p.get("side") != "short":
            continue
        tier, rate, why = tier_for(sym)
        p["borrow_tier"], p["borrow_rate_annual"], p["borrow_tier_reason"] = tier, rate, why
        if sym in BORROW_DOUBTFUL:
            p["borrow_doubtful"] = BORROW_DOUBTFUL[sym]
        # CM-050(d): `opened_epoch` does not exist on this ledger (the key is
        # `epoch`), so this fell through to `now_epoch` and charged ZERO on the
        # first accrual — silently forfeiting every day between the position
        # opening and borrow first running. That gap was real here: the book
        # opened at 12:14Z and this module did not exist until hours later.
        # Fall back to the POSITION's own entry time, which is what a lender
        # would charge from.
        last = (p.get("borrow_last_epoch") or _entry_epoch(p)
                or ledger.get("opened_epoch") or ledger.get("epoch") or now_epoch)
        elapsed_days = max(0.0, (now_epoch - last) / 86400.0)
        # CM-050(c): the clock is advanced ONLY when a charge is actually taken.
        # Advancing it before the unmarked check meant an unmarked position
        # forfeited its borrow permanently — the clock moved, the charge never
        # landed. Borrow does not stop accruing because our price feed blinked.
        value = None
        if p.get("mark_usd"):
            value = p["units"] * p["mark_usd"]
            if p.get("mark_stale"):
                p["borrow_note"] = ("charged against the LAST GOOD mark — the "
                                    "book carries it forward, so the value is "
                                    "known, not unknown")
            else:
                p.pop("borrow_note", None)
        if not value or value <= 0:
            p["borrow_note"] = ("not charged — no mark at all; the clock is HELD "
                                "so this period accrues when pricing resumes")
            continue
        p["borrow_last_epoch"] = now_epoch
        charge = value * rate * (elapsed_days / 365.0)
        if charge <= 0:
            continue
        p["borrow_accrued_usd"] = round(p.get("borrow_accrued_usd", 0.0) + charge, 4)
        total += charge
        detail[sym] = {"tier": tier, "rate": rate, "days": round(elapsed_days, 4),
                       "charge_usd": round(charge, 4),
                       "accrued_usd": p["borrow_accrued_usd"]}
    ledger["borrow_model"] = model()
    ledger["borrow_accrued_total_usd"] = round(
        ledger.get("borrow_accrued_total_usd", 0.0) + total, 4)
    ledger["borrow_last_accrual_utc"] = utc
    ledger["borrow_detail_last_tick"] = detail
    ledger["borrow_doubtful"] = {s: BORROW_DOUBTFUL[s]
                                 for s in BORROW_DOUBTFUL
                                 if s in (ledger.get("positions") or {})}
    return total


def _entry_epoch(position):
    """Position's own entry time as an epoch, or None. CM-050(d)."""
    import calendar
    import time as _t
    v = position.get("entry_utc")
    if not v:
        return None
    try:
        return calendar.timegm(_t.strptime(v, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None


def realised_borrow(position):
    """Borrow already accrued on a position — subtract from any exit's net gain."""
    return float(position.get("borrow_accrued_usd") or 0.0)


def release_borrow(position, units_out, units_before):
    """Move a released tranche's SHARE of accrued borrow off the position.

    CM-050(b). Accrued borrow was held whole on the position while `units`
    shrank with every scale-out, and `net_gain` divides that borrow by the
    REMAINING basis. After banking 75% the runner therefore carried 100% of the
    borrow against 25% of the basis — a drag inflated 4x, which drives the
    runner out early or stops it clearing the gate at all.

    Returns the tranche's borrow in USD, to be charged against that fill's
    REPORTED realised P&L. It is NOT taken from cash again: borrow is already
    debited from cash daily as it accrues, and charging it twice would
    understate equity.
    """
    if units_before <= 0:
        return 0.0
    accrued = float(position.get("borrow_accrued_usd") or 0.0)
    if not accrued:
        return 0.0
    share = accrued * (units_out / units_before)
    position["borrow_accrued_usd"] = round(accrued - share, 4)
    position["borrow_released_usd"] = round(
        float(position.get("borrow_released_usd") or 0.0) + share, 4)
    return share


if __name__ == "__main__":
    import json
    print(__doc__)
    print(json.dumps(model(), indent=2))
    print("\nper-name tiers:")
    for s in sorted(set(list(HARD_TO_BORROW) + list(_LIQUID_NAMES) +
                        ["1088.HK", "CSNA3.SA", "MBG.DE"])):
        t, r, why = tier_for(s)
        print("  %-10s %-9s %5.2f%%/yr   %s" % (s, t, r * 100, why[:52]))
