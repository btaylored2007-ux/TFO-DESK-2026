#!/usr/bin/env python3
"""fundamental_spec_exits.py — the SPECULATIVE SLEEVE's exit rule. PAPER ONLY.

Same SHAPE as `fundamental_exits.py`, different ARITHMETIC. It is a separate file
for one reason: `fundamental_exits.py` is SEALED and SHA-256 hashed into the main
book's 2026-08-27 pre-registration. Editing it to add sleeve constants would
break that seal and void the 182-day forward test. So the main book's rule is
never touched and this one stands beside it.

THE RULE (frozen 2026-08-28, SPECULATIVE_SLEEVE_PREREGISTRATION.md §3.3)

  Clause 1 — the 15% NET gate. No exit unless the fill clears entry cost, exit
  cost and slippage and still leaves 15%. Against a 350bps round trip, a penny
  position up 5% GROSS loses money. 15% is not greed; it is 3.5% of cost plus a
  margin that makes the trade worth doing.

  Clause 2 — scale while it rallies. A rung fires ONLY on a NEW net-gain high,
  never on a retrace back through a level already taken. The final 34% runs until
  net gain falls 15 percentage points off its OWN peak.

  LONG ONLY. Nobody lends micro caps, so there is no short side and no borrow
  term — which is why net_gain() here is simpler than the main book's.

WHY THE GIVEBACK IS 15% AND NOT THE MAIN BOOK'S 5%: these names move 8% on no
news. A 5% trail would be stopped out by noise on the way up, converting the
runner from a way to stay in a winner into a way to be shaken out of one.
"""
import fundamental_spec as sp

MIN_NET_GAIN = sp.MIN_NET_GAIN            # 0.15
ROUND_TRIP_BPS = sp.ROUND_TRIP_BPS        # 350
LADDER = sp.LADDER                        # (0.15,0.33), (0.30,0.33)
RUNNER_FRACTION = sp.RUNNER_FRACTION      # 0.34
RUNNER_GIVEBACK = sp.RUNNER_GIVEBACK      # 0.15


# ---- CATALYST EXPIRY ("sell the news"), funder-directed 2026-08-28 -----------
# Registered in SPECULATIVE_SLEEVE_PREREGISTRATION.md Amendment 2 BEFORE it ran.
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
    return {
        "entry_cost_bps": sp.ENTRY_COST_BPS,
        "exit_cost_bps": sp.EXIT_COST_BPS,
        "slippage_bps_per_side": sp.SLIPPAGE_BPS,
        "round_trip_bps": ROUND_TRIP_BPS,
        "min_net_gain_pct": MIN_NET_GAIN * 100,
        "long_only": True,
        "note": ("Penny-tier and deliberately expensive. Measured spreads in this "
                 "tier ran 4.2%% to 53%%. A cost model that flatters the gate is "
                 "the failure mode that matters."),
    }


def net_gain(position):
    """Gross move MINUS the full modelled round trip. Long only.

    Returns None when the position is unmarked — an unmarked position must never
    be tested against the gate, because a missing mark reads as a zero move and
    would silently look like a position that simply has not paid yet.
    """
    px, mk = position.get("entry_px_usd"), position.get("mark_usd")
    if not px or mk is None:
        return None
    gross = (mk - px) / px
    return gross - (ROUND_TRIP_BPS / 10000.0)


def evaluate(position, sym):
    """Decide what, if anything, comes off. Never mutates the position."""
    st = position.setdefault("exit_state", {
        "released": 0.0, "rungs_hit": [], "peak_net_gain": 0.0,
        "orig_units": position.get("units", 0.0)})
    ng = net_gain(position)
    if ng is None:
        return {"action": "hold", "sym": sym, "reason":
                "unmarked — the gate is not testable without a mark"}

    # The peak is the RUNNER's reference and must advance before any rung logic,
    # so a rung and the trail always read the same high.
    if ng > st.get("peak_net_gain", 0.0):
        st["peak_net_gain"] = ng

    # CATALYST EXPIRY is tested BEFORE the gate. A reason-expired exit must never
    # be blocked by a gate that exists to govern PROFIT-taking.
    import datetime
    _today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    _exp, _why = catalyst_expired(position, _today)
    if _exp:
        return {"action": "catalyst_expired", "sym": sym, "fraction": 1.0,
                "net_gain_pct": ng * 100, "catalyst_date": position.get("catalyst_date"),
                "reason": _why}

    if ng < MIN_NET_GAIN - 1e-9:
        return {"action": "hold", "sym": sym, "net_gain_pct": ng * 100,
                "catalyst_note": _why,
                "gate_pct": MIN_NET_GAIN * 100,
                "reason": ("net %.2f%% has not cleared the %.0f%% gate after a "
                           "%.0f bps round trip" % (ng * 100, MIN_NET_GAIN * 100,
                                                    ROUND_TRIP_BPS))}

    # Rungs: only on a NEW high, never on a retrace through a level already taken.
    for level, frac in LADDER:
        if level in st["rungs_hit"]:
            continue
        if ng >= level - 1e-9:
            return {"action": "scale_out", "sym": sym, "rung": level,
                    "fraction": frac, "net_gain_pct": ng * 100,
                    "reason": ("net %.2f%% cleared the +%.0f%% rung — releasing "
                               "%.0f%%" % (ng * 100, level * 100, frac * 100))}

    # Runner: the last 34% comes off only on a giveback from its own peak.
    if st["released"] >= sum(f for _, f in LADDER) - 1e-9:
        peak = st.get("peak_net_gain", ng)
        if ng <= peak - RUNNER_GIVEBACK:
            return {"action": "close_runner", "sym": sym,
                    "fraction": RUNNER_FRACTION, "net_gain_pct": ng * 100,
                    "peak_pct": peak * 100,
                    "reason": ("runner gave back %.1fpp from its own peak of "
                               "%.2f%% — closing" % ((peak - ng) * 100, peak * 100))}
        return {"action": "hold", "sym": sym, "net_gain_pct": ng * 100,
                "peak_pct": peak * 100,
                "reason": ("runner still rallying — %.2f%% net, peak %.2f%%, "
                           "trails at %.1fpp" % (ng * 100, peak * 100,
                                                 RUNNER_GIVEBACK * 100))}
    return {"action": "hold", "sym": sym, "net_gain_pct": ng * 100,
            "reason": "all rungs taken; awaiting runner giveback"}


def apply(position, decision):
    """Mutate the position for a fill. Returns (realised_usd, cash_delta_usd).

    LONG ONLY, so cash_delta is simply the proceeds: units_out * mark. The main
    book's short branch (release notional + realised) has no analogue here and is
    deliberately absent rather than carried over unused.

    ACCOUNTING INVARIANT — this is the bug the main book shipped and had to fix:
    a scale-out must credit PROCEEDS, not just profit. Crediting only the P&L
    silently destroys the capital that was in the position.
    """
    act = decision.get("action")
    if act not in ("scale_out", "close_runner", "catalyst_expired"):
        return 0.0, 0.0
    st = position["exit_state"]
    orig = st.get("orig_units") or position.get("units", 0.0)
    frac = decision["fraction"]
    units_out = min(orig * frac, position.get("units", 0.0))
    if units_out <= 0:
        return 0.0, 0.0
    mk = position["mark_usd"]
    px = position["entry_px_usd"]
    gross_proceeds = units_out * mk
    # Exit-side cost only: the entry side was already paid when the position opened.
    exit_cost = gross_proceeds * ((sp.EXIT_COST_BPS + sp.SLIPPAGE_BPS) / 10000.0)
    cash_delta = gross_proceeds - exit_cost
    realised = cash_delta - (units_out * px)

    position["units"] = position.get("units", 0.0) - units_out
    st["released"] = st.get("released", 0.0) + frac
    if act == "scale_out":
        st.setdefault("rungs_hit", []).append(decision["rung"])
    return round(realised, 2), round(cash_delta, 2)


if __name__ == "__main__":
    import json
    print(__doc__)
    print(json.dumps(cost_model(), indent=2))
    print("ladder:", LADDER, "runner:", RUNNER_FRACTION,
          "giveback:", RUNNER_GIVEBACK)
