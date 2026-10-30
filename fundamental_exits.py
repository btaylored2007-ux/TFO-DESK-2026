#!/usr/bin/env python3
"""fundamental_exits.py — the TFO Fundamental Desk trading rule. PAPER ONLY.

THE RULE, funder-directed 2026-08-27, verbatim in intent
--------------------------------------------------------
"Exit fill of MIN 5% over fill and exit cost, and then scaled if it is rallying."

Two clauses, and the order matters:

  1. THE 5% NET GATE. No exit is taken unless the fill clears the entry fill
     cost AND the exit cost AND leaves at least 5% on top. The threshold is net,
     not gross. A position up 5% GROSS that nets 4.3% after a round trip does
     NOT qualify — that is the whole point of the funder's phrasing, and it is
     the difference between this and every naive take-profit rule.

  2. SCALE WHILE IT RALLIES. Once the gate is cleared the position is NOT
     closed. It is scaled out in tranches as it keeps going, so a winner is
     allowed to run instead of being cut at the first qualifying print. If it
     stops rallying, scaling stops with it — the ladder only advances on new
     net-gain highs, never on a retrace.

WHY THIS IS A TRADING RULE AND NOT A TAKE-PROFIT
-----------------------------------------------
A take-profit closes a position at a number. This does two things a take-profit
does not: it refuses to transact at all below a net threshold (so cost can never
quietly eat the gain), and it sizes the exit to how far the move has actually
run. The book therefore only ever trades when trading pays for itself.

APPLIES SYMMETRICALLY TO SHORTS
-------------------------------
A short's gain is (entry - mark). The same net-of-cost 5% gate and the same
ladder apply. Note the honest asymmetry recorded in the book: NO BORROW COST IS
MODELLED, so a short's net gain here is flattered by however long it has been
held. That is published, not hidden, and it means the 5% gate is a WEAKER filter
on shorts than on longs until borrow is wired in.

COSTS
-----
Modelled, per side, and published in every decision so the arithmetic can be
checked. Global equities across 19 venues do not share one schedule; these are
deliberately conservative defaults rather than a precision claim, and a
too-generous cost assumption would make the gate too easy to clear — the failure
mode that matters. No broker is connected and no real fill exists, so every
number here is an assumption stated as an assumption.

WHAT THIS FILE CANNOT DO
------------------------
It cannot open a position, cannot resize one upward, and cannot close one on a
thesis break. Thesis exits come from each block's `KILLS IT` condition and are a
SEPARATE mechanism (see `pending_thesis_exits` in the ledger) requiring a human
read — an automated rule must not be the thing that decides a research thesis
has failed. And it never overrides `book_kill.py`: a frozen book still exits.

PAPER ONLY.
"""

# ---- the rule's constants. Changing any of these is a RULE CHANGE and must be
# ---- registered before it runs, never after a result is known.
MIN_NET_GAIN = 0.05          # clause 1: 5% over fill AND exit cost
ENTRY_COST_BPS = 10.0        # 0.10% per side, modelled
EXIT_COST_BPS = 10.0         # 0.10% per side, modelled
SLIPPAGE_BPS = 5.0           # per side, modelled

# clause 2: the ladder. Each rung releases a fraction of the ORIGINAL position,
# and a rung only fires on a NEW net-gain high — never on a retrace back through
# a level already taken.
LADDER = (
    (0.05, 0.25),            # +5%  net -> release 25%
    (0.10, 0.25),            # +10% net -> release a further 25%
    (0.15, 0.25),            # +15% net -> release a further 25%
)
RUNNER_FRACTION = 0.25       # the last quarter runs
# The runner is only given up when the move stops rallying: it exits if net gain
# falls back by this much from its own peak. That is what "scaled if it is
# rallying" means operationally — the runner is a trailing decision, not a level.
RUNNER_GIVEBACK = 0.05       # 5 percentage points of net gain off the peak

# ---- CM-051: per-position STOP, SHORTS ONLY (funder-delegated 2026-08-27) ----
# Registered in reports/research_global_value_screen_2026-08-27.md before it ran.
# A short's loss is UNBOUNDED; a long's is bounded at 100% and the undervaluation
# thesis needs room to be early. Chosen on evidence over 46 names x 10y x 182d
# windows: a 25% stop cuts the worst case from -5608.87% to -32.78% while keeping
# most of the mean improvement, without the win-rate destruction of 15-20%.
# It does NOT create edge — the mean is negative at every stop level. It converts
# an unbounded catastrophe into a bounded loss.
SHORT_STOP_ADVERSE = 0.25    # stop when mark >= 1.25 x entry
LONG_STOP_ADVERSE = 0.15     # 2026-10-28 funder rule (stop on every position), HT-approved: stop when mark <= 0.85 x entry
STOP_APPLIES_TO = ("short", "long")

# ---- CM-051 decision 1: DIRECTED CLOSE of the hard-tier short book ----------
# Funder-delegated 2026-08-27, registered before it ran. The reason is the BORROW
# ARITHMETIC, not the sector: a 15%/yr tier costs 7.5% over a 182-day window, so
# the name must fall 12.78% merely to clear the 5% gate. Hard-tier mean over 10y
# is -78.98% unstopped and still -4.25% at the tightest stop tested. A signal
# fighting a 7.5% carry before it is right about anything is not an edge.
# (Four of the six are crypto miners/infrastructure, which is also the opposite
# of a thesis about undervaluation; CLF and STLA are levered/distressed, and are
# closed on the borrow arithmetic alone.)
# Kept in CODE rather than applied as a one-off hand edit, so the decision is
# auditable, repeatable and visible on the dashboard.
DIRECTED_CLOSE = {
    "MARA": "hard-tier borrow 15%/yr — 12.78% required fall; crypto miner",
    "CLSK": "hard-tier borrow 15%/yr — 12.78% required fall; crypto miner",
    "KEEL": "hard-tier borrow 15%/yr — 12.78% required fall; thin-float micro cap",
    "BKKT": "hard-tier borrow 15%/yr — 12.78% required fall; crypto infrastructure",
    "CLF":  "hard-tier borrow 15%/yr — 12.78% required fall; levered/distressed",
    "STLA": "hard-tier borrow 15%/yr — 12.78% required fall; levered/distressed",
}

ROUND_TRIP_BPS = ENTRY_COST_BPS + EXIT_COST_BPS + (2 * SLIPPAGE_BPS)


# ---- CATALYST EXPIRY ("sell the news"), funder-directed 2026-08-28 -----------
# Registered in GLOBAL_VALUE_BOOK_PREREGISTRATION.md Amendment 3 BEFORE it ran.
#
# A position whose pre-registered DATED catalyst has passed, plus a grace period,
# is CLOSED IN FULL unless re-underwritten on a NEW dated catalyst.
#
# WHY IT IS TESTED BEFORE THE NET GATE: this is a REASON-EXPIRED exit, not a
# profit exit. The gate governs the profit ladder and must never trap a position
# whose reason for existing has expired — the same logic that puts the stop and
# the thesis break ahead of it. A close at a loss here is CORRECT.
#
# The evidence: every desk that examined a post-event situation on 2026-08-28
# found the move already gone (index deletions "the forced selling is over",
# CCP.AX "the dislocation HAS ALREADY CLOSED", JDO +16.3% and EQT +21.5% before
# being screened, ENG.MC +13.92% ON THE DAY). And three desks independently found
# the catalyst and the damage can be the SAME EVENT (GL1's FID, 2517's dilution
# EGM, Clover's readout-into-a-placing).
#
# GRACE: an event may report after the close, land intraday, or slip a session.
# Five trading days separates a slip from a failure.
CATALYST_GRACE_TRADING_DAYS = 5


def _trading_days_since(date_iso, today_iso):
    """Approximate trading days elapsed. Calendar days x 5/7, floored.

    Deliberately APPROXIMATE and deliberately GENEROUS to the position: it
    understates elapsed trading days around holidays, so the grace period errs
    toward holding a day longer rather than closing a day early.
    """
    import datetime
    try:
        d0 = datetime.date.fromisoformat(str(date_iso)[:10])
        d1 = datetime.date.fromisoformat(str(today_iso)[:10])
    except Exception:                                  # noqa: BLE001
        return None
    return int((d1 - d0).days * 5 / 7)


def catalyst_expired(position, today_iso):
    """(expired, detail). Never raises; an unparseable date NEVER expires a name."""
    cat = position.get("catalyst_date")
    if not cat:
        return False, None
    n = _trading_days_since(cat, today_iso)
    if n is None:
        return False, "catalyst_date %r unparseable — NOT expiring on a bad date" % cat
    if n < CATALYST_GRACE_TRADING_DAYS:
        return False, ("catalyst %s: ~%d of %d grace trading days elapsed"
                       % (cat, max(n, 0), CATALYST_GRACE_TRADING_DAYS))
    return True, ("CATALYST EXPIRED: %s passed ~%d trading days ago (grace %d). "
                  "The reason for holding has RESOLVED. Closing IN FULL unless "
                  "re-underwritten on a NEW dated catalyst — 'it should keep "
                  "going' is not a catalyst."
                  % (cat, n, CATALYST_GRACE_TRADING_DAYS))


def cost_model():
    """Published with every decision so the gate arithmetic is checkable."""
    return {
        "entry_cost_bps": ENTRY_COST_BPS, "exit_cost_bps": EXIT_COST_BPS,
        "slippage_bps_per_side": SLIPPAGE_BPS,
        "round_trip_bps": ROUND_TRIP_BPS,
        "min_net_gain_pct": MIN_NET_GAIN * 100,
        "borrow_cost_modelled": True,           # CM-050(a)
        "borrow_source": "fundamental_borrow.py (modelled tiers, not quoted)",
        "note": ("Modelled, not quoted. No broker is connected and no real fill "
                 "exists. Borrow IS now modelled per name and is subtracted "
                 "inside the gate for shorts, so the 5% threshold means the same "
                 "thing on both sides of the book. The tiers are modelled, "
                 "deliberately conservative, and were never quoted by a "
                 "securities-lending desk."),
    }


def net_gain(position):
    """Net fractional gain after the FULL modelled cost of the position.

    Long : (mark - entry) / entry, less round-trip cost.
    Short: (entry - mark) / entry, less round-trip cost, LESS ACCRUED BORROW.

    Borrow is subtracted here and not merely reported elsewhere, because this is
    the number the 5% gate tests. A short carrying 7% of accrued borrow that
    shows 6% gross has LOST money, and a gate that cannot see the borrow would
    happily book it as a winner. Borrow is expressed as a fraction of the
    position's own cost basis so it is directly comparable to the gross move.

    Returns None when the position cannot be marked — an unmarked position is
    never eligible to trade, because its gain is unknown rather than zero.
    """
    if position.get("mark_stale"):
        return None
    e = position.get("entry_px_usd")
    m = position.get("mark_usd")
    if not e or not m:
        return None
    gross = (m - e) / e if position.get("side") == "long" else (e - m) / e
    net = gross - (ROUND_TRIP_BPS / 10_000.0)
    if position.get("side") == "short":
        basis = (position.get("units") or 0.0) * e
        borrow = float(position.get("borrow_accrued_usd") or 0.0)
        if basis > 0 and borrow:
            net -= borrow / basis
    return net


def evaluate(position, sym):
    """Decide what, if anything, this position should trade right now.

    Returns a dict describing the decision. It NEVER mutates the position — the
    caller applies it — so the rule can be dry-run against the live book without
    touching a single holding.
    """
    ng = net_gain(position)
    state = position.get("exit_state") or {"released": 0.0, "rungs_hit": [],
                                           "peak_net_gain": None}
    base = {"symbol": sym, "side": position.get("side"),
            "net_gain_pct": None if ng is None else round(ng * 100, 3),
            "gate_pct": MIN_NET_GAIN * 100,
            "released_fraction": state["released"], "action": "hold",
            "reason": "", "release_fraction": 0.0}

    if ng is None:
        base["reason"] = ("UNMARKED — gain unknown, not zero. A position that "
                          "cannot be priced is never eligible to trade.")
        return base

    peak = state.get("peak_net_gain")
    peak = ng if peak is None else max(peak, ng)
    base["peak_net_gain_pct"] = round(peak * 100, 3)

    # ---- CM-051: a DIRECTED CLOSE outranks everything except being unmarked --
    # It is a funder decision, so it is not subject to the profit gate. It still
    # cannot act on a position that cannot be priced — closing at an unknown
    # price would be marking a fill we did not observe.
    if sym in DIRECTED_CLOSE and position.get("units"):
        base.update({
            "action": "stop_out", "release_fraction": 1.0,
            "directed": True,
            "reason": ("DIRECTED CLOSE (funder-delegated 2026-08-27): %s — %s. "
                       "Closed IN FULL to cash." % (sym, DIRECTED_CLOSE[sym])),
        })
        return base

    # ---- CATALYST EXPIRY: tested alongside the STOP, and for the same reason --
    # Both are REASON exits, not PROFIT exits, so both sit ahead of the net gate.
    # Registered 2026-08-28, Amendment 3, BEFORE it ran.
    import datetime as _dt
    _today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    _exp, _why = catalyst_expired(position, _today)
    if _exp and position.get("units"):
        base.update({
            "action": "catalyst_expired", "release_fraction": 1.0,
            "catalyst_date": position.get("catalyst_date"), "reason": _why})
        return base

    # ---- CM-051: the STOP is tested FIRST and on the GROSS price move --------
    # It is a RISK rule, not a profit rule. Testing it after the 5% gate would
    # let "below the gate — refusing to transact" block the one exit that exists
    # to cap an unbounded loss. Triggering on gross (not net) is deliberate:
    # accrued borrow must never fire a stop on a position that has not moved.
    if position.get("side") in STOP_APPLIES_TO:
        e = position.get("entry_px_usd")
        m = position.get("mark_usd")
        if e and m:
            is_long = position.get("side") == "long"
            adverse = (e - m) / e if is_long else (m - e) / e   # loss-direction move
            lim = LONG_STOP_ADVERSE if is_long else SHORT_STOP_ADVERSE
            base["adverse_pct"] = round(adverse * 100, 3)
            base["stop_at_pct"] = lim * 100
            base["stop_px_usd"] = round(e * (1 - lim) if is_long else e * (1 + lim), 6)
            if adverse >= lim - 1e-9:
                base.update({
                    "action": "stop_out", "release_fraction": 1.0,
                    "reason": ("STOP: %s is %.2f%% %s entry (stop %.0f%%) — the "
                               "position is closed IN FULL. This is a risk "
                               "rule and is never blocked by the profit gate."
                               % (sym, adverse * 100, "below" if is_long else "above", lim * 100)),
                })
                return base

    if ng < MIN_NET_GAIN - 1e-9:                  # CM-049(b): MIN means inclusive
        base["reason"] = (
            "below the gate: %.2f%% net after %.0fbps round trip, needs %.2f%%. "
            "Refusing to transact — cost would eat the move."
            % (ng * 100, ROUND_TRIP_BPS, MIN_NET_GAIN * 100))
        return base

    # Clause 2. Advance the ladder only on a NEW net-gain high, so a position
    # that pops through a rung and falls back does not keep releasing stock.
    for level, frac in LADDER:
        # CM-049(b): compare with a tolerance. `ng` is computed by subtraction
        # (gross - cost), so a position sitting EXACTLY on a rung lands at
        # 0.049999999999999996 and the rung silently did not fire — the funder's
        # rule is "MIN 5%", so 5.000% must trade.
        if (ng >= level - 1e-9 and level not in state["rungs_hit"]
                and ng >= peak - 1e-9):
            base.update({
                "action": "scale_out", "release_fraction": frac,
                "rung": level,
                "reason": ("net gain %.2f%% cleared the %.0f%% rung on a new high "
                           "— releasing %.0f%% of the original position, letting "
                           "the rest run" % (ng * 100, level * 100, frac * 100)),
            })
            return base

    # The runner: given up only when the rally stops, measured off its own peak.
    if state["released"] >= sum(f for _, f in LADDER) - 1e-9:
        if (peak - ng) >= RUNNER_GIVEBACK:
            base.update({
                "action": "close_runner", "release_fraction": RUNNER_FRACTION,
                "reason": ("runner: net gain fell %.2fpp from its %.2f%% peak to "
                           "%.2f%% — the rally stopped, so the last quarter comes "
                           "off" % ((peak - ng) * 100, peak * 100, ng * 100)),
            })
            return base
        base["reason"] = ("runner held: %.2f%% net, %.2fpp off peak, still "
                          "rallying" % (ng * 100, (peak - ng) * 100))
        return base

    base["reason"] = ("above the gate at %.2f%% net but no new rung on a new high "
                      "— holding, the ladder does not advance on a retrace"
                      % (ng * 100))
    return base


def apply(position, decision):
    """Apply a decision to a position.

    Returns (realised_usd, cash_delta_usd). The two are DIFFERENT and conflating
    them double-counts equity:

      * LONG  — selling a tranche returns the stock's full market value to cash
                (proceeds), of which only part is profit. cash_delta = units_out
                * mark - exit_cost.
      * SHORT — the collateral for the released tranche comes back AND the P&L
                settles. Add back borrow already debited from cash when deriving
                cash_delta from realised P&L; do not charge that borrow twice.

    Crediting only the profit would leave the released capital nowhere, and the
    book's equity would silently fall by the amount of stock sold.
    """
    state = position.setdefault("exit_state", {"released": 0.0, "rungs_hit": [],
                                              "peak_net_gain": None,
                                              "orig_units": position["units"]})
    # CM-049(a): the rule says each rung releases a fraction of the ORIGINAL
    # position. The code took a fraction of what REMAINED, so the rungs decayed
    # 25% -> 18.8% -> 14.1% of original: only 57.8% was ever banked and the
    # "last quarter" runner was really 42.2% of the book. Anchor to the size the
    # position opened at. (setdefault covers positions opened before this fix:
    # their first release is still a true 25% of what they then held.)
    orig = state.setdefault("orig_units", position["units"])
    ng = net_gain(position)
    if ng is not None:
        p = state.get("peak_net_gain")
        state["peak_net_gain"] = ng if p is None else max(p, ng)
    if decision["action"] not in ("scale_out", "close_runner", "stop_out",
                                  "catalyst_expired"):
        return 0.0, 0.0
    frac = decision["release_fraction"]
    # catalyst_expired releases the FULL remaining position, like a stop.
    if decision["action"] in ("close_runner", "stop_out", "catalyst_expired"):
        units_out = position["units"]            # comes off in FULL
    else:
        units_out = min(orig * frac, position["units"])
    if units_out <= 0:
        return 0.0, 0.0
    e, m = position["entry_px_usd"], position["mark_usd"]
    pnl = (m - e) * units_out if position["side"] == "long" else (e - m) * units_out
    cost = (units_out * m) * (EXIT_COST_BPS + SLIPPAGE_BPS) / 10_000.0
    realised = pnl - cost
    # CM-050(b): a released short tranche takes its SHARE of accrued borrow with
    # it. Without this the borrow stayed whole on a shrinking position and
    # net_gain's `borrow / basis` inflated by the reciprocal of what was left —
    # after banking 75% the runner carried a 4x-overstated drag. Charged to the
    # fill's REPORTED P&L only: borrow is already debited from cash as it
    # accrues, so taking it from cash again would understate equity.
    borrow_share = 0.0
    if position["side"] == "short":
        try:
            import fundamental_borrow as _b
            borrow_share = _b.release_borrow(position, units_out, position["units"])
        except Exception:                        # noqa: BLE001 - reported below
            borrow_share = 0.0
        realised -= borrow_share
    # CM-049(a): notional and the short's released collateral must both scale by
    # the UNITS actually released, not by the requested fraction — otherwise a
    # capped or full-runner release returns the wrong collateral to cash.
    units_share = units_out / position["units"] if position["units"] else 0.0
    if position["side"] == "long":
        cash_delta = (units_out * m) - cost
    else:
        released_notional = position["notional_usd"] * units_share
        cash_delta = released_notional + realised + borrow_share
        position['borrow_released_cash_correct_usd'] = round(
            float(position.get('borrow_released_cash_correct_usd') or 0.0) + borrow_share, 4)
    position["units"] -= units_out
    position["notional_usd"] = round(position["notional_usd"] * (1 - units_share), 2)
    state["released"] = round(state["released"] + (units_out / orig if orig else 0.0), 4)
    if decision.get("rung") is not None:
        state["rungs_hit"].append(decision["rung"])
    position.setdefault("exit_fills", []).append({
        "utc_field": "set_by_caller", "action": decision["action"],
        "fraction": frac, "units": units_out, "px_usd": m,
        "net_gain_pct": decision["net_gain_pct"],
        "realised_usd": round(realised, 2), "exit_cost_usd": round(cost, 2),
        "borrow_charged_usd": round(borrow_share, 2),
        "borrow_already_debited_from_cash": position['side'] == 'short',
        "reason": decision["reason"],
        "cash_delta_usd": round(cash_delta, 2),
    })
    return realised, cash_delta


if __name__ == "__main__":
    print(__doc__)
    import json
    print(json.dumps(cost_model(), indent=2))
    print("\nladder:", LADDER, "runner:", RUNNER_FRACTION,
          "giveback:", RUNNER_GIVEBACK)
