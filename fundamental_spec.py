#!/usr/bin/env python3
"""fundamental_spec.py — SPECULATIVE SLEEVE rules. PAPER ONLY.

WHAT THIS IS
------------
A ring-fenced $50,000 sleeve — 10% of the TFO Fundamental Desk's $500,000 —
funder-directed 2026-08-28, for penny stocks and exploration companies where the
edge is TECHNOLOGY AND NEWS rather than intrinsic value.

The funder's instruction was precise and the design follows it exactly:
**loosen the EDGE, not the MONEY.** The evidentiary bar drops; the dollar
discipline does not. Wider net, smaller bets, hard ceiling.

WHY IT IS SEPARATE AND NOT PART OF THE MAIN BOOK
------------------------------------------------
Every rule in the main book is calibrated for liquid equities and would be
actively WRONG applied here:

  * THE 5% NET GATE assumes a 30bps round trip. This operation measured penny
    spreads of 4.20% (SUP.L), 4.17% (SOM.L) and 53.08% (CLSK). A 4% spread is an
    8% round-trip handicap, so the main rule would refuse every exit forever and
    the sleeve would never trade. The gate here is set against a REALISTIC
    penny-tier cost, which is far higher and is published.
  * BORROW TIERS are irrelevant: this sleeve is LONG ONLY. Nobody lends micro
    caps, and a short you cannot borrow is not a position.
  * §4.3's DIRECTION TEST cannot be run on a pre-revenue explorer — there is no
    revenue trend, no margin, no ROE. What CAN be tested is cash runway and share
    count, and those are the two hard gates below.

WHAT "LOOSENED EDGE" MEANS PRECISELY
------------------------------------
The main book requires intrinsic value: a business worth more than it trades at.
§2 of the governing prompt is explicit that this needs earnings, book value or
cash flow — and an explorer has none, so it is not a category error to drop the
requirement here, it is the ONLY honest way to look at these at all.

Accepted instead: a DATED, SOURCED technology or news catalyst — a drill result,
an assay, a resource upgrade, a permit, an offtake, a partnership, a patent
grant, a trial readout, a contract award. Still dated. Still sourced. Still
carrying a named disconfirming condition. **What is loosened is the KIND of
evidence, never whether evidence exists.**

WHAT IS NOT LOOSENED, AND WILL NOT BE
--------------------------------------
1. **Tradability.** A position that cannot be exited is not a speculation, it is
   a donation. The liquidity gate still runs; its thresholds are penny-tier.
2. **Dilution.** ASX and TSXV juniors fund by placement. XPP.L diluted 37.9% in
   three years and that was a MAIN-BOARD company. A junior diluting 40% a year
   destroys per-share value whatever is in the ground.
3. **Runway.** An explorer with six months of cash is a countdown, not an
   opportunity — the same shape the net-net desk used to kill 633 of 638 names.
4. **Honesty.** Every position carries its catalyst, its date, and what would
   disconfirm it. No exceptions for being a small bet.

THE EXPECTATION, STATED BEFORE ANY OUTCOME EXISTS
--------------------------------------------------
This sleeve is expected to LOSE money. The firm's own record on narrative-driven
speculation is 6,533 OANDA trades and 4,145 Betfair bets with no validated edge,
and ~1,040 killed hypotheses across the crypto desks. Penny exploration is the
least evidenced thing this firm has ever deployed into. It is sized at 10% and
capped per name so that being wrong costs 10% and not the book.

PAPER ONLY. No broker, no order, no credential.
"""

# ---- SIZE. Loosen the edge, not the money. -----------------------------------
SLEEVE_USD = 50_000.0            # 10% of the $500k book
MAX_POSITION_USD = 2_500.0       # 5% of the sleeve, 0.5% of the book
MAX_POSITIONS = 20               # many small bets, never a few large ones
MIN_POSITIONS_FOR_FULL_DEPLOY = 12   # below this, leave the rest in cash

# ---- COST. Penny-tier and honest, not the main book's 30bps. -----------------
ENTRY_COST_BPS = 25.0            # commission + fees, per side
EXIT_COST_BPS = 25.0
SLIPPAGE_BPS = 150.0             # per side. Micro caps move on your own order.
ROUND_TRIP_BPS = ENTRY_COST_BPS + EXIT_COST_BPS + (2 * SLIPPAGE_BPS)   # 350bps

# The exit gate MUST clear a realistic round trip or the sleeve cannot trade.
# 15% net is not greed — it is 3.5% of cost plus a margin that makes the trade
# worth doing at all. A speculation that pays 5% gross LOSES money here.
MIN_NET_GAIN = 0.15

# Scale out faster than the main book: these do not compound, they spike.
LADDER = (
    (0.15, 0.33),                # +15% net -> release a third
    (0.30, 0.33),                # +30% net -> release another third
)
RUNNER_FRACTION = 0.34
RUNNER_GIVEBACK = 0.15           # wider than the main book; these are volatile

# ---- HARD GATES. Not loosened. -----------------------------------------------
MIN_ADV_USD = 150_000.0          # below the main book's 250k, above nothing
MAX_POSITION_PCT_OF_ADV = 0.05   # STRICTER than the main book's 10%
MAX_SPREAD_PCT = 0.040           # 4% quoted; the round trip above assumes ~3%
MIN_CASH_RUNWAY_MONTHS = 12      # a shorter runway is a countdown, not a bet
MAX_ANNUAL_DILUTION_PCT = 0.25   # >25%/yr share growth disqualifies outright

# ---- KILL. Its own, WIDER than the book's. -----------------------------------
# The old comment here read "tighter than the book's", which was never true: -40%
# is a looser threshold than the -30% book kill, not a tighter one. Since
# KILL_THRESHOLD_AMENDMENT.md moved the book kill to -18% (funder-directed
# 2026-08-28) these two are also UNREACHABLE in practice — this sleeve is a
# registered book, so book_kill freezes its new entries at -18% long before a
# -40% sleeve drawdown or a -50% single-name drawdown can be reached. They are
# left at their registered values deliberately: changing them is a separate rule
# change the funder has not directed. Flagged, not silently rewritten.
SLEEVE_KILL_DD_PCT = -40.0       # sleeve-level; the book kill (-18%) binds first
POSITION_KILL_DD_PCT = -50.0     # a single name past this comes off


def config():
    """Published with the sleeve so every number here is checkable."""
    return {
        "sleeve_usd": SLEEVE_USD,
        "max_position_usd": MAX_POSITION_USD,
        "max_positions": MAX_POSITIONS,
        "long_only": True,
        "cost_model": {
            "entry_cost_bps": ENTRY_COST_BPS, "exit_cost_bps": EXIT_COST_BPS,
            "slippage_bps_per_side": SLIPPAGE_BPS,
            "round_trip_bps": ROUND_TRIP_BPS,
            "note": ("Penny-tier and deliberately expensive. Measured spreads in "
                     "this tier ran 4.2% to 53%. A cost model that flatters the "
                     "gate is the failure mode that matters."),
        },
        "exit_gate_net_pct": MIN_NET_GAIN * 100,
        "ladder": LADDER, "runner_fraction": RUNNER_FRACTION,
        "runner_giveback": RUNNER_GIVEBACK,
        "hard_gates": {
            "min_adv_usd": MIN_ADV_USD,
            "max_position_pct_of_adv": MAX_POSITION_PCT_OF_ADV,
            "max_spread_pct": MAX_SPREAD_PCT,
            "min_cash_runway_months": MIN_CASH_RUNWAY_MONTHS,
            "max_annual_dilution_pct": MAX_ANNUAL_DILUTION_PCT,
        },
        "sleeve_kill_dd_pct": SLEEVE_KILL_DD_PCT,
        "position_kill_dd_pct": POSITION_KILL_DD_PCT,
        "edge_accepted": ("DATED, SOURCED technology or news catalyst — drill "
                          "result, assay, resource upgrade, permit, offtake, "
                          "partnership, patent, trial readout, contract award. "
                          "Intrinsic-value evidence is NOT required here."),
        "edge_not_accepted": ("An undated story. 'Exposure to' a theme. A chart. "
                              "A promoter's projection. A resource with no JORC "
                              "or NI 43-101 basis. What is loosened is the KIND "
                              "of evidence, never whether evidence exists."),
        "expectation": ("This sleeve is EXPECTED TO LOSE MONEY. The firm's record "
                        "on narrative-driven speculation is ~1,040 killed "
                        "hypotheses, 6,533 OANDA trades and 4,145 Betfair bets "
                        "with no validated edge. Sized at 10% so being wrong "
                        "costs 10%."),
        "paper_only": True,
    }


def screen(name, adv_usd=None, spread_pct=None, position_usd=None,
           cash_runway_months=None, annual_dilution_pct=None,
           has_dated_catalyst=None):
    """Hard gates only. Returns {ok, reasons[]} — the loosening does not reach here.

    Missing data is a REFUSAL, not a pass. On a micro cap an unmeasurable figure
    is far more likely to be bad news than good, and "we could not check" must
    never read as "fine".
    """
    r = []
    pos = position_usd if position_usd is not None else MAX_POSITION_USD
    if pos > MAX_POSITION_USD + 1e-6:
        r.append("position $%s exceeds the $%s per-name cap"
                 % ("{:,.0f}".format(pos), "{:,.0f}".format(MAX_POSITION_USD)))
    if adv_usd is None:
        r.append("ADV unknown — exit impact cannot be bounded")
    else:
        if adv_usd < MIN_ADV_USD:
            r.append("ADV $%s below the $%s floor"
                     % ("{:,.0f}".format(adv_usd), "{:,.0f}".format(MIN_ADV_USD)))
        elif pos / adv_usd > MAX_POSITION_PCT_OF_ADV:
            r.append("position is %.1f%% of a day's traded value (max %.0f%%)"
                     % (pos / adv_usd * 100, MAX_POSITION_PCT_OF_ADV * 100))
    if spread_pct is None:
        r.append("spread UNMEASURED — round-trip cost unknown; re-measure in session")
    elif spread_pct > MAX_SPREAD_PCT:
        r.append("spread %.2f%% exceeds %.1f%% — the round trip alone exceeds the "
                 "%.0f%% exit gate" % (spread_pct * 100, MAX_SPREAD_PCT * 100,
                                       MIN_NET_GAIN * 100))
    if cash_runway_months is None:
        r.append("cash runway UNKNOWN — for a pre-revenue company this is the "
                 "single most important number and its absence is disqualifying")
    elif cash_runway_months < MIN_CASH_RUNWAY_MONTHS:
        r.append("cash runway %.1f months below the %d-month floor — this is a "
                 "countdown, not a bet" % (cash_runway_months, MIN_CASH_RUNWAY_MONTHS))
    if annual_dilution_pct is None:
        r.append("share-count history UNKNOWN — juniors fund by placement and "
                 "dilution is the defining hazard of this tier")
    elif annual_dilution_pct > MAX_ANNUAL_DILUTION_PCT:
        r.append("share count growing %.0f%%/yr (max %.0f%%) — dilution consumes "
                 "any discovery faster than drilling creates it"
                 % (annual_dilution_pct * 100, MAX_ANNUAL_DILUTION_PCT * 100))
    if has_dated_catalyst is False:
        r.append("no DATED catalyst — an undated story is not an edge, it is a "
                 "hope, and this sleeve loosened the KIND of evidence, not the "
                 "requirement that evidence exist")
    elif has_dated_catalyst is None:
        r.append("catalyst not established")
    return {"ok": not r, "reasons": r, "name": name}


if __name__ == "__main__":
    import json
    print(__doc__)
    print(json.dumps(config(), indent=2, default=str))
