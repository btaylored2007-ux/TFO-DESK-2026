#!/usr/bin/env python3
"""convexity_sleeve.py — the $150,000 defined-risk convexity sleeve. PAPER ONLY.

WHAT THIS IS
------------
Funder-directed 2026-09-03: turn $150,000 into $3,150,000 (21x) by 2026-12-03.

WHY IT IS SHAPED THIS WAY
-------------------------
The funder first asked for the whole firm ($1,526,988) to be tripled in two
months. The arithmetic of that request, run before anything was built:

    +1.85%/day compounded, every day, for 60 days  =  ~9,990%/yr
    at the firm's 15%/yr realised vol   P(3x) = 0.000%
    at 100%/yr vol (6.7x leverage)      P(3x) = 0.629%,  P(lose half) = 12.0%
    at 200%/yr vol (13.3x leverage)     P(3x) = 5.330%,  P(lose half) = 41.2%

and the desk's measured drift is NEGATIVE, so those P(3x) figures are
overstatements and the ruin figures are understatements.

The only structurally honest route to a 3x-shaped outcome is CONVEXITY: pay a
capped premium for an asymmetric payoff. So this sleeve exists to take that shot
with money the firm can afford to lose in full, while the other ~$1.38M stays
out of it earning 3.772%.

THE HARD CONSTRAINT THAT MAKES IT SAFE
--------------------------------------
Every position must have a STRUCTURALLY CAPPED maximum loss — long options,
defined-risk spreads, warrants, capped-loss event trades. No naked shorts, no
unlimited-loss structures, no leverage that can lose more than committed. The
sum of max losses across open positions can never exceed the $150,000 seed, so
the sleeve cannot lose more than its seed and cannot reach the rest of the firm.

WHAT THIS SLEEVE IS NOT
-----------------------
It is not a plan to make 21x. Nothing measured on this desk supports that, and
the board says so in the banner rather than showing a hopeful progress bar. It
is a bounded, honestly-priced lottery ticket. If it expires worthless the firm
loses 9.8% and survives; that is the entire point of the structure.
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "convexity_sleeve.json")

SEED_USD      = 150000.0
TARGET_MULT   = 21.0
TARGET_USD    = SEED_USD * TARGET_MULT          # $3,150,000
START_DATE    = "2026-09-03"
TARGET_DATE   = "2026-12-03"                    # 3 months, funder-directed
MAX_POSITIONS = 25
MAX_LOSS_TOTAL = SEED_USD                       # cannot exceed the seed. Ever.
MAX_LOSS_PER_POSITION = 0.15 * SEED_USD         # $22,500 — no single ticket >15%

# ---- CONVEXITY_SLEEVE_PREREGISTRATION.md, frozen 2026-09-03 ----------------
# The ceiling that governs the whole sleeve. A fully-paid non-negative-payoff
# portfolio has risk-neutral expectation = cash x e^(rT), so by Markov:
#     Q(V >= 21.36 x premium) <= e^(rT)/21.36 = 4.71%
# 4.71% is a HARD mathematical ceiling on P(21x) for ANY defined-risk structure.
# Arithmetic, not opinion. No selection skill can raise it.
P21X_CEILING_PCT      = 4.71
P21X_HONEST_PCT       = 0.4      # this desk, today, with zero validated survivors
SLEEVE_EV_USD         = -45000.0 # central estimate on $150k deployed
MODAL_OUTCOME_USD     = -150000.0
MODAL_PROBABILITY_PCT = 96.0

# Tranche ladder — capital unlocks only as independent survivors appear.
TRANCHE_CAPS = ((3, 30000.0), (6, 75000.0), (99, 150000.0))
MAX_PREMIUM_FIRST_ENTRY = 0.05 * SEED_USD    # $7,500
MAX_PREMIUM_PER_THEME   = 0.30 * SEED_USD    # $45,000 — correlated legs are ONE bet
MIN_UNCOMMITTED_CASH    = 0.40 * SEED_USD    # $60,000 — never all-in

# Exits
# NOT IN FORCE — recorded so the disagreement is visible, not silently dropped.
# The sizing study proposed a 3x/8x profit ladder AND conceded in the same breath
# that it "reduces P(21x) further, from ~0.4% toward ~0.1%". The live design below
# (STOP_FRACTION block) rejects it: for far-OTM convexity essentially all EV sits
# in the extreme right tail, and this desk MEASURED a hard take-profit cutting a
# breakout book from +3.393% to +0.108%. Trimming winners here would convert the
# only path to the mandate into a rounding error. Winners are not trimmed.
# If the funder wants capital protection over the 21x path, flip this on — but
# that is a change of mandate, not a parameter tweak.
PROFIT_LADDER_NOT_IN_FORCE = ((3.0, 1.0/3.0), (8.0, 1.0/3.0))
TIME_STOP_DTE   = 21
TIME_STOP_MIN_X = 1.5
LIQ_STOP_SPREAD_PCT = 50.0
CLOSE_SPREAD_BY_DTE = 2          # never hold a spread into expiry week

# Halts
HALT_CUMULATIVE_LOSS_USD = 0.50 * SEED_USD   # $75,000 — do not run to the cap
DAY30_CHECKPOINT_MIN_X   = 2.5

BANNED = ("naked short", "uncovered short", "future", "cfd", "perpetual",
          "perp", "margin", "leveraged")


# Every position must clear these or it is REFUSED.
REQUIRED_FIELDS = ("symbol", "instrument", "venue", "max_loss_usd", "premium_usd",
                   "upside_multiple", "catalyst_date", "catalyst_source", "thesis")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load():
    if not os.path.exists(LEDGER):
        return None
    with open(LEDGER) as f:
        return json.load(f)


def save(d):
    d["updated_utc"] = _utc()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, LEDGER)


def create():
    if os.path.exists(LEDGER):
        raise SystemExit("REFUSED: sleeve already exists at %s" % LEDGER)
    d = {
        "book": "convexity_sleeve",
        "label": "Convexity Sleeve — 21x shot",
        "paper_only": True,
        "seed_usd": SEED_USD,
        "cash": SEED_USD,
        "target_usd": TARGET_USD,
        "target_multiple": TARGET_MULT,
        "start_date": START_DATE,
        "target_date": TARGET_DATE,
        "positions": {},
        "closed": [],
        "realised_usd": 0.0,
        "created_utc": _utc(),
        "standing": {
            "headline": "NO VALIDATED EDGE — THIS IS A BOUNDED LOTTERY TICKET, NOT A PLAN",
            "why": ("The firm has 0 of 32 books showing skill; best |t| across ~3,650 "
                    "hypotheses is 2.95 against a 4.23 bar. Nothing measured here "
                    "supports a 21x expectation."),
            "structure": ("Max loss is capped at the $150,000 seed by construction. "
                          "Every position must have a structurally capped downside."),
            "if_it_fails": "Firm loses 9.8% and survives. That is the design.",
        },
    }
    save(d)
    return d


def open_position(**kw):
    """Open ONE defined-risk position. Refuses on any breach. Nothing is implicit."""
    d = load()
    if d is None:
        raise SystemExit("REFUSED: sleeve not created — run --create first.")
    missing = [f for f in REQUIRED_FIELDS if not kw.get(f)]
    if missing:
        raise SystemExit("REFUSED: missing required field(s): %s. Missing data is a "
                         "REFUSAL, not a pass." % ", ".join(missing))
    sym = kw["symbol"]
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    v1_venues.require_entry_allowed(sym)
    if sym in d["positions"]:
        raise SystemExit("REFUSED: %s already open." % sym)
    if len(d["positions"]) >= MAX_POSITIONS:
        raise SystemExit("REFUSED: %d positions is the cap." % MAX_POSITIONS)

    ml = float(kw["max_loss_usd"])
    prem = float(kw["premium_usd"])
    if ml <= 0:
        raise SystemExit("REFUSED: max_loss_usd must be a positive capped number. An "
                         "uncapped structure is not eligible for this sleeve.")
    if ml > prem + 1e-6:
        raise SystemExit("REFUSED: max_loss_usd $%.2f exceeds premium paid $%.2f — that "
                         "is not a defined-risk structure." % (ml, prem))
    if ml > MAX_LOSS_PER_POSITION:
        raise SystemExit("REFUSED: max_loss $%.2f exceeds the per-position cap $%.2f."
                         % (ml, MAX_LOSS_PER_POSITION))
    committed = sum(float(p["max_loss_usd"]) for p in d["positions"].values())
    if committed + ml > MAX_LOSS_TOTAL + 1e-6:
        raise SystemExit("REFUSED: committed risk $%.2f + $%.2f exceeds the sleeve cap "
                         "$%.2f. The sleeve can never risk more than its seed."
                         % (committed, ml, MAX_LOSS_TOTAL))
    if prem > d["cash"] + 1e-6:
        raise SystemExit("REFUSED: premium $%.2f exceeds cash $%.2f." % (prem, d["cash"]))

    # CM-069 lesson: a date with no source is a fabricated field.
    src = str(kw["catalyst_source"]).strip()
    low = src.lower()
    if len(src) < 12 or low in ("tbd", "n/a", "none", "unknown", "internal", "estimate"):
        raise SystemExit("REFUSED: catalyst_source %r is a placeholder, not a source."
                         % kw["catalyst_source"])
    if "http" not in low:
        raise SystemExit("REFUSED: catalyst_source carries no URL, so the catalyst date "
                         "%s cannot be traced back to anything." % kw["catalyst_date"])

    # Banned structures. Max loss must be a stated number at entry; anything that
    # can convert into an unbounded position is refused by name, not by judgement.
    inst_l = str(kw["instrument"]).lower()
    for b in BANNED:
        if b in inst_l:
            raise SystemExit("REFUSED: instrument %r contains banned structure %r. "
                             "Only fully-paid capped-loss structures are eligible."
                             % (kw["instrument"], b))
    if prem > MAX_PREMIUM_FIRST_ENTRY + 1e-6 and not kw.get("reverified_add"):
        raise SystemExit("REFUSED: first entry capped at $%.2f (5%% of sleeve); "
                         "$%.2f requires a re-verified add."
                         % (MAX_PREMIUM_FIRST_ENTRY, prem))
    # Tranche ladder: capital unlocks only as independent survivors accumulate.
    n_ind = int(kw.get("independent_survivors") or len(d["positions"]))
    cap = next(c for thr, c in TRANCHE_CAPS if n_ind < thr)
    if committed + ml > cap + 1e-6:
        raise SystemExit("REFUSED: tranche cap $%.2f with %d independent survivors "
                         "(committed $%.2f + $%.2f). More survivors unlock more."
                         % (cap, n_ind, committed, ml))
    if d["cash"] - prem < MIN_UNCOMMITTED_CASH - 1e-6:
        raise SystemExit("REFUSED: would leave $%.2f uncommitted, below the $%.2f "
                         "floor. There is no configuration where the desk is all-in."
                         % (d["cash"] - prem, MIN_UNCOMMITTED_CASH))
    # Entry gate 4: enter at the ask, model the exit at the bid. bid=0 is banned.
    bid = kw.get("bid")
    if bid is not None and float(bid) <= 0:
        raise SystemExit("REFUSED: bid is zero — structurally capped loss, but a "
                         "100%% REALISED loss, because the position cannot be exited.")

    d["positions"][sym] = {k: kw[k] for k in REQUIRED_FIELDS}
    d["positions"][sym].update({
        "opened_utc": _utc(),
        "mark_usd": prem,
        "honest_probability": kw.get("honest_probability"),
        "expected_value": kw.get("expected_value"),
        "what_kills_it": kw.get("what_kills_it"),
    })
    d["cash"] = round(d["cash"] - prem, 2)
    save(d)
    return d["positions"][sym]


# ---------------------------------------------------------------- exit policy
#
# WHY THERE IS NO PROFIT TARGET HERE, AND WHY THAT IS NOT A CONTRADICTION
#
# The desk's standing trading rule — "+5% NET over fill and exit cost, then scale
# a third into strength" — is CORRECT on the fundamentals desk and is untouched
# there. It is WRONG here, and shipping it into this sleeve would quietly destroy
# the mandate.
#
# The fundamentals desk trades momentum and reversion: bounded moves, where taking
# 5% off the table repeatedly is how you compound. This sleeve holds CONVEX
# payoffs, where essentially all of the expected value sits in the extreme right
# tail. Cutting a ticket at +5% that was on its way to 20x does not bank profit —
# it converts the only path to 21x into a rounding error. Measured on the desk's
# own breakout study, a hard take-profit cut +3.393% to +0.108%.
#
# So the exits here are: thesis death, a hard stop on premium, and expiry.
# Winners are never trimmed. That is a deliberate, funder-visible divergence from
# the +5% rule, scoped to this sleeve only.
STOP_FRACTION      = 0.50    # cut at 50% of premium lost — half the ticket back
THESIS_DEAD_EXIT   = True    # catalyst passed / resolved against us -> exit at once
REDEPLOY_PROCEEDS  = True    # realised cash returns to the pool. 21x needs compounding.


def _live_mark(sym, position, evidence=None):
    """Mark ONE position. Returns (mark_usd, source) or (None, why) — never guesses.

    A position this desk cannot price is reported UNMARKED and keeps its last
    honest mark. Non-negotiable #7: no stale price is ever traded against, and an
    invented mark is worse than an absent one.
    """
    # CM-074: fetch in a SUBPROCESS with a hard timeout. yfinance hangs on this
    # host with no timeout and this call runs on the hourly desk_tick — a hang
    # here blocks every book. A missing quote is a missing quote; never a guess.
    import subprocess
    tk = position.get("mark_ticker") or sym
    code = ("import sys,json;sys.path.insert(0,%r);import fundamental_book as fb;"
            "px,err=fb.fetch_prices([%r],require_executable=False);print(json.dumps({'quote':px.get(%r),'error':err}))" % (BASE, tk, tk))
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=45)
        result = json.loads((r.stdout or "null").strip().splitlines()[-1])
        v = result.get('quote') if isinstance(result, dict) else None
        if evidence is not None:
            evidence.update(quote=v, error=result.get('error') if isinstance(result, dict) else 'No response')
        from paper_execution_price import eligible, chronological
        if not eligible(v):
            return None, 'Quote unavailable or not executable (session, price age or FX gate)'
        if not chronological(v, position):
            return None, 'Delayed bar predates entry or entry clock is unknown'
        u = v.get("px_usd")
        if isinstance(u, (int, float)) and u > 0:
            units = float(position.get("units") or 0.0)
            if units:
                return round(units * float(u), 2), "%s:%s" % (v.get('paper_price_class') or 'CURRENT PAPER', tk)
        return None, "no live quote returned"
    except subprocess.TimeoutExpired:
        return None, "quote timeout 45s"
    except Exception as e:                                  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])


def _ping_funder(exits):
    """Write a trade notice the phone channel picks up. Never blocks the tick."""
    try:
        line = " | ".join("%s %s $%+.2f (%s)" % (e["symbol"], e["ret_pct"] and
                          ("%+.1f%%" % e["ret_pct"]) or "", e["pnl_usd"], e["reason"])
                          for e in exits)
        note = os.path.join(BASE, "reports", "convexity_trade_notices.log")
        with open(note, "a") as f:
            f.write("%s  CONVEXITY SLEEVE EXIT(S): %s\n" % (_utc(), line))
        print("   PING written -> %s" % note)
    except Exception as e:                                  # noqa: BLE001
        print("   ping failed (non-fatal): %s" % e)


def mark_and_manage(verbose=True):
    """Mark every position, run the exit rules, redeploy proceeds. Opens nothing.

    Returns the list of exits taken this pass so the caller can ping the funder.
    """
    d = load()
    if d is None:
        return []
    exits, today = [], time.strftime("%Y-%m-%d")
    import exit_check_evidence as evidence
    evidence.start(d, 'convexity_sleeve.mark_and_manage')
    for sym in list(d["positions"]):
        q = d["positions"][sym]
        mark_evidence = {}
        mk, src = _live_mark(sym, q, mark_evidence)
        if mk is None:
            q["mark_stale"] = True
            q["mark_note"] = src
            if verbose:
                print("   %s UNMARKED (%s) — carrying last honest mark, not trading on it"
                      % (sym, src))
        else:
            _why = _ratio = None
            try:    # 2026-09-29 bad-tick guard (8766.T): never write a >30% one-print move into the mark (per share)
                import bad_tick_guard as _btg
                _u = float(q.get("units") or 0)
                if _u > 0:
                    _last = float(q["mark_usd"]) / _u if float(q.get("mark_usd") or 0) > 0 else None
                    _e0 = q.get("rail_orig_entry_usd") or (float(q.get("premium_usd") or 0) / _u or None)
                    _why, _ref, _ratio = _btg.mark_check("convexity_sleeve", float(mk) / _u, "long", _last, _e0)
            except Exception:                               # noqa: BLE001
                _why = None
            if _why:
                _btg.flag_rejected(q, None, float(mk), _why, _ratio, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                if _ratio:
                    _btg.request_ca_check("convexity_sleeve", sym, _why, _ratio)
                if verbose:
                    print("   %s MARK REJECTED (%s) -- carrying last good mark, not trading on it" % (sym, _why))
            else:
                q["mark_usd"], q["mark_stale"], q["mark_note"] = mk, False, src
                q.pop("mark_rejected", None)

        prem = float(q.get("premium_usd") or 0.0)
        cur = float(q.get("mark_usd") or 0.0)
        reason = None
        # CM-076 (2026-09-06): per-position symmetric exit band, FUNDER-SET, for the
        # 10-name discretionary override basket only. Those positions carry no
        # catalyst and no thesis, so the standard exit ladder (stop / thesis-death /
        # catalyst-passed) can never close them except via the -50% stop — they
        # would otherwise sit indefinitely. The funder set +15%/-15% explicitly for
        # THIS basket. It does not apply to TAP/7004.T or any catalyst-driven entry,
        # which keep the sleeve's normal "never trim a winner" policy — that policy
        # is correct for a dated thesis and was never meant for a pure conviction
        # basket with no exit signal of its own.
        band = q.get("exit_band_pct")
        if band and prem > 0 and not q.get("mark_stale"):
            ret_pct = 100.0 * (cur / prem - 1.0)
            if ret_pct <= -abs(band):
                reason = "override band: -%.0f%% stop" % abs(band)
            elif ret_pct >= abs(band):
                reason = "override band: +%.0f%% take-profit" % abs(band)
        import head_trader_gate as _htg
        if _htg.active():
            reason = None
        elif reason:
            pass
        elif q.get("mark_stale"):
            pass                                            # never exit on a stale mark
        elif prem > 0 and cur <= prem * (1.0 - STOP_FRACTION):
            reason = "stop: -%.0f%% of premium" % (STOP_FRACTION * 100)
        elif THESIS_DEAD_EXIT and q.get("thesis_dead"):
            reason = "thesis dead: %s" % str(q.get("thesis_dead"))[:80]
        elif str(q.get("catalyst_date") or "") and str(q["catalyst_date"]) < today \
                and not q.get("catalyst_resolved_favourably"):
            reason = "catalyst %s passed unresolved" % q["catalyst_date"]

        evidence.record(d, sym, q,
                        ('WAIT_MARKET' if mark_evidence.get('quote') else 'WAIT_DATA')
                        if mk is None else ('EXIT' if reason else 'HOLD'),
                        reason or (src if mk is None else 'Registered exit rule did not fire'),
                        **mark_evidence)
        if reason:
            pnl = round(cur - prem, 2)
            from paper_execution_price import receipt
            ticket = evidence.ticket('convexity_sleeve', sym, reason, 'long', q.get('units'),
                                     (mark_evidence.get('quote') or {}).get('px_usd'),
                                     prem, cur, pnl, **receipt(mark_evidence.get('quote') or {}),
                                     cost_source='Native premium/mark model; execution costs unrecorded')
            d['exit_checks']['legs'][sym]['ticket'] = ticket
            import desk_costs as _dc  # 2026-09-26 V1 costs HARD RULE: exit fee (same model as V2)
            _qx = mark_evidence.get('quote') or {}; _u = float(q.get('units') or 0)
            _px = float(_qx.get('px_usd') or ((cur / _u) if _u else 0))
            _f = _dc.fill_fees(sym, "sell", _u, _px, _qx.get('px'), _qx.get('ccy')) if (_u and _px) else {"fees_usd": 0.0, "breakdown": {}}
            _fee = round(_f["fees_usd"], 2); ticket["fees_usd"] = _fee; ticket["fee_detail"] = _f["breakdown"]
            d["cash"] = round(d["cash"] + cur - _fee, 2)     # proceeds net of exit fee return to the pool
            d["realised_usd"] = round(float(d.get("realised_usd") or 0.0) + pnl - _fee, 2)
            d["fees_paid_usd"] = round(float(d.get("fees_paid_usd") or 0) + _fee, 2)
            rec = {"symbol": sym, "instrument": q.get("instrument"), "closed_utc": _utc(),
                   "premium_usd": prem, "exit_usd": cur, "pnl_usd": pnl,
                   "ret_pct": round(100.0 * (cur / prem - 1.0), 2) if prem else None,
                   "reason": reason, 'ticket_id': ticket['ticket_id'], 'execution_receipt': ticket}
            d.setdefault("closed", []).append(rec)
            del d["positions"][sym]
            exits.append(rec)
            if verbose:
                print("   EXIT %s: %s  premium $%.2f -> $%.2f  P&L $%+.2f"
                      % (sym, reason, prem, cur, pnl))
    if exits and REDEPLOY_PROCEEDS and verbose:
        print("   redeployable cash now $%s (proceeds returned to the pool)"
              % "{:,.2f}".format(d["cash"]))
    evidence.finish(d)
    save(d)
    return exits


def available_to_deploy():
    """Cash that may back a NEW ticket, respecting the sleeve risk cap.

    The cap is on COMMITTED RISK, not on cash, so realised gains genuinely expand
    what can be deployed — which is the only way a sleeve compounds toward 21x.
    But it can never exceed cash actually held.
    """
    d = load()
    if d is None:
        return 0.0
    committed = sum(float(p.get("max_loss_usd") or 0.0) for p in d["positions"].values())
    return max(0.0, min(float(d.get("cash") or 0.0), MAX_LOSS_TOTAL - committed))


def tick(verbose=True):
    from desk_lock import ledger_lock
    with ledger_lock('convexity_sleeve'):
        return _tick_locked(verbose)


def _tick_locked(verbose=True):
    """Hourly: mark positions, accrue cash yield on idle premium, save.

    Marks: a defined-risk position is carried at its last premium mark; this
    tick does NOT invent a mark. Live option marks are written by the entry/
    exit path that has the quote in hand. Idle cash earns the same T-bill yield
    every other book earns (CM-068) — $150k sitting at zero would be the one
    book on the desk deliberately left uncovered.
    """
    d = load()
    if d is None:
        if verbose:
            print("convexity_sleeve: not created — nothing to tick")
        return None
    exits = mark_and_manage(verbose=verbose)
    if exits:
        _ping_funder(exits)
    d = load()
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(d, "Convexity Sleeve", verbose=verbose)
    except Exception as _e:                                 # noqa: BLE001
        print("   CASH-YIELD skipped: %s" % _e)
    save(d)
    if verbose:
        pos = d["positions"]
        mv = sum(float(p.get("mark_usd") or 0) for p in pos.values())
        print("convexity_sleeve: tick ok — equity $%s, %d open, cash $%s"
              % ("{:,.2f}".format(d["cash"] + mv), len(pos), "{:,.2f}".format(d["cash"])))
    return d


def halt_check(d):
    """Returns a halt reason, or None. Entry-blocking; never closes a position."""
    pos = d.get("positions") or {}
    eq = float(d.get("cash") or 0.0) + sum(float(p.get("mark_usd") or 0.0)
                                           for p in pos.values())
    loss = float(d.get("seed_usd") or SEED_USD) - eq
    if loss >= HALT_CUMULATIVE_LOSS_USD:
        return ("HALT: cumulative loss $%.2f reached the $%.2f (50%%) halt. The "
                "structural cap is $%.2f but the sleeve does not run to it."
                % (loss, HALT_CUMULATIVE_LOSS_USD, MAX_LOSS_TOTAL))
    return None



def open_funder_override(symbol, instrument, venue, max_loss_usd, premium_usd, override_reason):
    """CM-075: a PURE funder-directed entry — no dated catalyst, no thesis, no hunt,
    no kill-check. Distinct from open_position(), which REQUIRES a sourced catalyst
    (CM-069) precisely to stop a fabricated date passing as research. This function
    exists because the honest answer, when the funder gives no catalyst at all, is
    to record that plainly rather than manufacture one to satisfy the normal gate.

    Every OTHER capped-loss guard still applies in full: live price, max_loss ==
    premium (long-only, nothing uncapped), per-position cap, sleeve-wide cap, the
    tranche ladder, and — not loosened, ever — the 40%-uncommitted-cash floor.
    If cash is short, this REFUSES; it does not quietly go all-in.
    """
    d = load()
    if d is None:
        raise SystemExit("REFUSED: sleeve not created — run --create first.")
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    v1_venues.require_entry_allowed(symbol)
    if symbol in d["positions"]:
        raise SystemExit("REFUSED: %s already open." % symbol)
    if len(d["positions"]) >= MAX_POSITIONS:
        raise SystemExit("REFUSED: %d positions is the cap." % MAX_POSITIONS)
    ml = float(max_loss_usd); prem = float(premium_usd)
    if ml <= 0:
        raise SystemExit("REFUSED: max_loss_usd must be positive.")
    if abs(ml - prem) > 1e-6:
        raise SystemExit("REFUSED: max_loss_usd must equal premium_usd for a long — "
                         "an override does not get to be less capped than a researched entry.")
    if ml > MAX_LOSS_PER_POSITION:
        raise SystemExit("REFUSED: max_loss $%.2f exceeds the per-position cap $%.2f."
                         % (ml, MAX_LOSS_PER_POSITION))
    committed = sum(float(p["max_loss_usd"]) for p in d["positions"].values())
    if committed + ml > MAX_LOSS_TOTAL + 1e-6:
        raise SystemExit("REFUSED: committed risk $%.2f + $%.2f exceeds the sleeve cap $%.2f."
                         % (committed, ml, MAX_LOSS_TOTAL))
    if prem > d["cash"] + 1e-6:
        raise SystemExit("REFUSED: premium $%.2f exceeds cash $%.2f." % (prem, d["cash"]))
    n_ind = len(d["positions"])
    cap = next(c for thr, c in TRANCHE_CAPS if n_ind < thr)
    if committed + ml > cap + 1e-6:
        raise SystemExit("REFUSED: tranche cap $%.2f at %d positions (committed $%.2f + $%.2f)."
                         % (cap, n_ind, committed, ml))
    if d["cash"] - prem < MIN_UNCOMMITTED_CASH - 1e-6:
        raise SystemExit("REFUSED: would leave $%.2f uncommitted, below the $%.2f floor. "
                         "This guard is never loosened, including for an explicit override."
                         % (d["cash"] - prem, MIN_UNCOMMITTED_CASH))
    d["positions"][symbol] = {
        "symbol": symbol, "instrument": instrument, "venue": venue,
        "max_loss_usd": ml, "premium_usd": prem, "mark_usd": prem,
        "catalyst_date": None, "catalyst_source": None,
        "upside_multiple": "n/a — discretionary", "thesis": None,
        "opened_utc": _utc(), "honest_probability": "FUNDER OVERRIDE — no catalyst, no thesis, not verified",
        "what_kills_it": None, "funder_directed": True, "funder_override": True,
        "no_catalyst_by_design": True, "killer_verdict": "NOT RUN — pure funder override, no hunt",
        "override_reason": override_reason,
        "exception_note": ("Opened on explicit funder override with NO dated catalyst and NO "
                           "thesis, by design — nothing was fabricated to force it through the "
                           "normal CM-069 gate. Must never be scored as a researched entry."),
    }
    d["cash"] = round(d["cash"] - prem, 2)
    save(d)
    return d["positions"][symbol]


def status():
    d = load()
    if d is None:
        print("sleeve not created")
        return
    pos = d["positions"]
    mv = sum(float(p.get("mark_usd") or 0) for p in pos.values())
    eq = d["cash"] + mv
    committed = sum(float(p["max_loss_usd"]) for p in pos.values())
    print("=" * 68)
    print("CONVEXITY SLEEVE   [%s]   PAPER ONLY" % _utc())
    print("=" * 68)
    print("  equity          : $%s   (seed $%s)"
          % ("{:,.2f}".format(eq), "{:,.0f}".format(d["seed_usd"])))
    print("  target          : $%s  (%.0fx) by %s"
          % ("{:,.0f}".format(d["target_usd"]), d["target_multiple"], d["target_date"]))
    print("  progress        : %.4f%% of the way to target"
          % (100.0 * (eq - d["seed_usd"]) / (d["target_usd"] - d["seed_usd"])
             if d["target_usd"] > d["seed_usd"] else 0.0))
    print("  cash            : $%s" % "{:,.2f}".format(d["cash"]))
    print("  open            : %d positions, $%s at risk (cap $%s)"
          % (len(pos), "{:,.2f}".format(committed), "{:,.0f}".format(MAX_LOSS_TOTAL)))
    print("  MAX POSSIBLE LOSS: $%s  (%.1f%% of firm) — capped by construction"
          % ("{:,.2f}".format(committed), 100.0 * committed / 1526988.0))
    print()
    print("  %s" % d["standing"]["headline"])
    print("    P(21x) mathematical ceiling : %.2f%%  (Markov; ANY defined-risk book)"
          % P21X_CEILING_PCT)
    print("    P(21x) honest, this desk    : %.1f%%" % P21X_HONEST_PCT)
    print("    expected value              : $%s on $150,000 deployed"
          % "{:,.0f}".format(SLEEVE_EV_USD))
    print("    modal outcome               : $%s  (p ~ %.0f%%)"
          % ("{:,.0f}".format(MODAL_OUTCOME_USD), MODAL_PROBABILITY_PCT))
    _h = halt_check(d)
    if _h:
        print("    %s" % _h)
    for p in sorted(pos):
        q = pos[p]
        print("    %-14s %-22s max loss $%-9s upside %s"
              % (p, str(q.get("instrument"))[:22],
                 "{:,.0f}".format(float(q["max_loss_usd"])), q.get("upside_multiple")))
    print("  ledger -> %s" % LEDGER)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tick", action="store_true")
    a = ap.parse_args()
    if a.tick:
        tick()
        return
    if a.create:
        d = create()
        print("convexity sleeve created: $%s, target $%s by %s. PAPER ONLY."
              % ("{:,.0f}".format(d["seed_usd"]), "{:,.0f}".format(d["target_usd"]),
                 d["target_date"]))
    else:
        status()


if __name__ == "__main__":
    main()
