#!/usr/bin/env python3
"""fundamental_spec_book.py — the SPECULATIVE SLEEVE's engine. PAPER ONLY.

$50,000 of NEW capital, funder-directed 2026-08-28 (Amendment 1 to
SPECULATIVE_SLEEVE_PREREGISTRATION.md). Its own ledger, its own equity series,
its own kill. It reads the main book for nothing and writes to it never.

No broker is connected anywhere and none may be added.

WHAT IT REUSES AND WHY
----------------------
FX and price fetching are imported from `fundamental_book`, not copied. That file
already carries the currency fix that cost this operation a rebuilt ledger — the
first build sized positions as `notional_usd / local_price`, treating a yen quote
as dollars, and opened Denso at 21 units for a claimed $40,000 position worth
about $260. Duplicating that logic here would be an invitation to reintroduce the
same bug in a second place. One implementation, imported.

WHAT IT DOES NOT REUSE
----------------------
The exit rule. `fundamental_exits.py` is SEALED and hashed into the main book's
registration; the sleeve's arithmetic lives in `fundamental_spec_exits.py` so
that neither file has to bend to accommodate the other.

THE SIZING RULE
---------------
`units = notional_usd / entry_px_usd` — px_usd, NEVER the native quote. Every
position stores the FX rate used, so any figure can be re-derived later.

Positions are $2,500 each, capped at 20. Below 12 positions the remainder stays
CASH: the rule exists so a thin candidate list cannot be padded into a full
deployment, and it is not waived to put money to work.
"""
import argparse
import csv
import json
import os
import re
import time

import desk_lock
import fundamental_book as fb          # fx_to_usd, fetch_prices — imported, never copied
import fundamental_spec as sp
import fundamental_spec_exits as ex

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "fundamental_spec_book.json")
EQUITY_CSV = os.path.join(BASE, "reports", "fundamental_spec_equity.csv")


def load():
    with open(LEDGER) as f:
        return json.load(f)


def save(d):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, sort_keys=False)
    os.replace(tmp, LEDGER)          # atomic; a half-written ledger is unreadable


def create():
    """Seed the sleeve with $50,000 of NEW capital. Refuses to overwrite."""
    if os.path.exists(LEDGER):
        raise SystemExit("REFUSED: %s already exists. Refusing to overwrite a "
                         "live ledger." % LEDGER)
    d = {
        "book": "TFO Fundamental Desk — SPECULATIVE SLEEVE",
        "registration": "SPECULATIVE_SLEEVE_PREREGISTRATION.md (sealed 2026-08-28) "
                        "+ Amendment 1 (funding source: NEW capital)",
        "paper_only": True,
        "long_only": True,
        "seed_usd": sp.SLEEVE_USD,
        "capital_source": ("NEW capital, funder-directed 2026-08-28. The main "
                           "book was NOT touched: no position trimmed, no "
                           "constant changed, its 2026-08-27 seal stands."),
        "evidence_standing": ("Catalyst-driven speculation. NO backtest, NO "
                             "search-deflation, NO cross-validation, NO "
                             "kill-tests. REGISTERED AS EXPECTED TO LOSE MONEY: "
                             "the firm's record on narrative speculation is "
                             "~1,040 killed hypotheses, 6,533 OANDA trades and "
                             "4,145 Betfair bets with no validated edge."),
        "trade_rule": ex.cost_model(),
        "hard_gates": sp.config()["hard_gates"],
        "opened_utc": fb._utc(),
        "cash": sp.SLEEVE_USD,
        "realised_usd": 0.0,
        "costs_paid_usd": 0.0,
        "positions": {},
        "closed": {},
        "queued": {},
        "sleeve_risk": {
            "rule": ("freeze NEW ENTRIES at -40%% drawdown from the sleeve's own "
                     "peak equity; a single name comes off at -50%%. "
                     "EXITS ARE NEVER BLOCKED — a frozen sleeve can always exit."),
            "kill_drawdown_pct": sp.SLEEVE_KILL_DD_PCT,
            "position_kill_dd_pct": sp.POSITION_KILL_DD_PCT,
            "peak_equity": sp.SLEEVE_USD,
        },
        "updated": fb._utc(),
    }
    save(d)
    return d


def open_position(*a, **kw):
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    v1_venues.require_entry_allowed(a[0] if a else kw.get("sym"))
    with desk_lock.ledger_lock("fundamental_spec"):
        return _open_position_locked(*a, **kw)


def _open_position_locked(sym, ccy, notional_usd, thesis, catalyst_date,
                  runway_months, dilution_pct, spread_measured_pct, adv_usd,
                  catalyst_source=None):
    """Open ONE position. Refuses on any gate breach, missing FX, or cap.

    Every refusal below is a rule from the sealed registration, quoted in the
    error so the reason survives into whatever log catches it.
    """
    d = load()
    if sym in d["positions"]:
        raise SystemExit("REFUSED: %s already open." % sym)
    if len(d["positions"]) >= sp.MAX_POSITIONS:
        raise SystemExit("REFUSED: %d positions is the cap." % sp.MAX_POSITIONS)
    if "KILLS IT" not in thesis.upper():
        raise SystemExit("REFUSED: thesis carries no KILLS IT condition. A "
                         "position without a named disconfirming condition is "
                         "not falsifiable and cannot be opened.")
    if not catalyst_date:
        raise SystemExit("REFUSED: no dated catalyst. The sleeve loosened the "
                         "KIND of evidence, not the requirement that it exist.")
    # CM-069 (2026-09-03): PROVENANCE, not just presence. The old check asked
    # only whether the field was non-empty, so a date that traced to nothing
    # passed it — 2105.HK carried "2026-12-31" while its own thesis said in
    # capitals that no decision date exists and "the desk refused to invent
    # one". A fabricated required field is the §4 breach the gate exists to
    # stop: "Missing data is a REFUSAL, not a pass."
    #
    # NEW ENTRIES ONLY. The 11 positions already open are NOT re-gated and are
    # NOT force-closed — TOURNAMENT_PREREGISTRATION.md forbids force-liquidation,
    # and retroactively applying a rule written today to a position opened under
    # the old one is exactly the re-keying this desk refuses. They exit on their
    # registered rules; `--unsourced` reports them.
    _src = (catalyst_source or "").strip()
    if not _src:
        raise SystemExit(
            "REFUSED: no catalyst_source. A DATED catalyst must also be a "
            "SOURCED one (registration \u00a74: 'Still dated. Still sourced.'). "
            "Pass catalyst_source= with a URL or a dated publisher citation "
            "that states the date %s." % catalyst_date)
    _low = _src.lower()
    _junk = ("tbd", "n/a", "na", "none", "unknown", "unclear", "estimate",
             "estimated", "assumed", "assumption", "guess", "approx", "placeholder",
             "internal", "desk estimate", "no source", "-", "?")
    if _low in _junk or len(_src) < 12:
        raise SystemExit(
            "REFUSED: catalyst_source %r is a placeholder, not a source. "
            "'we could not check' must never read as 'fine.'" % catalyst_source)
    if ("http" not in _low) and not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4}", _src):
        raise SystemExit(
            "REFUSED: catalyst_source %r carries neither a URL nor a dated "
            "citation, so the date %s cannot be traced back to anything."
            % (catalyst_source, catalyst_date))

    g = sp.screen(sym, adv_usd=adv_usd, spread_pct=spread_measured_pct,
                  position_usd=notional_usd, cash_runway_months=runway_months,
                  annual_dilution_pct=dilution_pct, has_dated_catalyst=True)
    if not g["ok"]:
        raise SystemExit("REFUSED by the hard gates:\n  - " +
                         "\n  - ".join(g["reasons"]))
    if notional_usd > d["cash"]:
        raise SystemExit("REFUSED: $%s notional exceeds $%s cash."
                         % ("{:,.0f}".format(notional_usd),
                            "{:,.0f}".format(d["cash"])))

    # fetch_prices returns (prices, failures) and already carries the FX and the
    # GBp/100 division. Use ITS px_usd rather than recomputing — the whole point
    # of importing it is that this conversion exists in exactly one place.
    prices, failures = fb.fetch_prices([sym])
    if sym not in prices:
        raise SystemExit("REFUSED: no usable price for %s (%s). Sizing without a "
                         "rate is the bug that opened Denso at 21 units."
                         % (sym, failures.get(sym, "unknown")))
    q = prices[sym]
    px, fx, px_usd = q["px"], q["fx_usd"], q["px_usd"]
    if q["ccy"] != ccy:
        raise SystemExit("REFUSED: %s quotes in %s, not the %s this entry claims. "
                         "A currency the caller did not expect is how a yen price "
                         "gets sized as dollars." % (sym, q["ccy"], ccy))
    units = notional_usd / px_usd            # px_usd, NEVER the native quote
    entry_cost = notional_usd * ((sp.ENTRY_COST_BPS + sp.SLIPPAGE_BPS) / 10000.0)

    d["positions"][sym] = {
        "side": "long",                       # the sleeve is LONG ONLY
        "notional_usd": round(notional_usd, 2),
        "entry_px": px, "entry_ccy": ccy, "entry_fx_usd": fx,
        "entry_px_usd": round(px_usd, 6), "units": units,
        "entry_utc": fb._utc(),
        "thesis": thesis, "catalyst_date": catalyst_date,
        "catalyst_source": _src,          # CM-069: provenance travels with the position
        "catalyst_date_sourced": True,
        "runway_months_at_entry": runway_months,
        "annual_dilution_at_entry": dilution_pct,
        "spread_at_entry_pct": spread_measured_pct,
        "spread_measured_in_session": True,
        "adv_usd_at_entry": adv_usd,
        "mark": px, "mark_usd": round(px_usd, 6), "mark_fx_usd": fx,
        "mark_utc": q.get('quote_utc'), "mark_stale": False, "unrealised_usd": 0.0,
        "entry_price_evidence": {k: q.get(k) for k in ('paper_price_class', 'quote_utc', 'fetched_at', 'paper_price_model')},
        "exit_state": {"released": 0.0, "rungs_hit": [], "peak_net_gain": 0.0,
                       "orig_units": units},
    }
    d["cash"] = round(d["cash"] - notional_usd - entry_cost, 2)
    d["costs_paid_usd"] = round(d.get("costs_paid_usd", 0.0) + entry_cost, 2)
    d["updated"] = fb._utc()
    save(d)
    return d["positions"][sym], entry_cost


def mark(verbose=True):
    with desk_lock.ledger_lock("fundamental_spec"):
        return _mark_locked(verbose)


def _mark_locked(verbose=True):
    """Re-mark every position, run the exit rule, write an equity row."""
    d = load()

    # CM-068 (2026-09-03, funder-directed): accrue T-bill yield on idle cash.
    # This book held $22,018.75 earning exactly zero. Forward-only from the first
    # tick, never retroactive; a missing or >24h-stale rate accrues ZERO loudly
    # rather than inventing money, and the cumulative figure is kept in its own
    # field so rate income is never mistaken for trading skill.
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(d, "TFO Fundamental Desk - Speculative")
    except Exception as _cy_e:                              # noqa: BLE001
        print("   CASH-YIELD skipped: %s" % _cy_e)
    pos = d["positions"]
    import exit_check_evidence as evidence
    evidence.start(d, 'fundamental_spec_book._mark_locked')
    if not pos:
        d["equity"] = round(d["cash"], 2)
        evidence.finish(d)
        save(d)  # persist cash-interest clock and balance even while flat
        if verbose:
            print("no open positions")
        return d
    quotes, failures = fb.fetch_prices(list(pos), require_executable=False)
    fills = []
    for sym, p in pos.items():
        q = quotes.get(sym)
        if not q:
            # A stale mark is stated, never silently carried as if it were fresh.
            p["mark_stale"] = True
            p["mark_failure"] = failures.get(sym, "no quote")
            evidence.record(d, sym, p, 'WAIT_DATA', p['mark_failure'])
            continue
        px, fx, px_usd = q["px"], q["fx_usd"], q["px_usd"]
        try:        # 2026-09-29 marker guard (spec sleeve: legacy 2.5x vs last good mark, as on its rail)
            import bad_tick_guard as _btg
            _why, _ref, _ratio = _btg.mark_check("fundamental_spec_book", px_usd, p.get("side", "long"), p.get("mark_usd"),
                                                 p.get("rail_orig_entry_usd") or p.get("entry_px_usd"))
        except Exception:                                   # noqa: BLE001
            _why, _ratio = None, 0
        if _why:
            _btg.flag_rejected(p, px, px_usd, _why, _ratio, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            if _ratio:
                _btg.request_ca_check("fundamental_spec_book", sym, _why, _ratio)
            evidence.record(d, sym, p, 'WAIT_DATA', 'Mark rejected by the bad-tick guard: ' + _why)
            continue
        p.update(mark=px, mark_fx_usd=fx, mark_usd=round(px_usd, 6),
                 **fb.quote_metadata(q))
        p.pop("mark_failure", None)
        p.pop("mark_rejected", None)
        # Closed/delayed/pre/post quotes value the holding but cannot price a
        # fill. The sealed evaluator also updates the runner peak, so defer
        # evaluation itself until the shared quote path confirms execution.
        from paper_execution_price import eligible, chronological, receipt
        if not eligible(q):
            evidence.record(d, sym, p, 'WAIT_MARKET', 'Quote is not executable',
                            quote=fb.quote_metadata(q))
            continue
        if not chronological(q, p):
            evidence.record(d, sym, p, 'WAIT_DATA', 'Delayed bar predates entry, repeats an exit bar, or entry clock is unknown', quote=fb.quote_metadata(q))
            continue
        import head_trader_gate as _htg
        if _htg.active():
            evidence.record(d, sym, p, 'HOLD', 'Head Trader only: engine exits disabled', quote=fb.quote_metadata(q))
            continue
        dec = ex.evaluate(p, sym)
        # Amendment 2 requires a full catalyst-expiry exit even below the
        # profit gate. The frozen exit module already decides and prices it.
        if dec["action"] in ("scale_out", "close_runner", "catalyst_expired"):
            units_before = p['units']
            realised, cash_delta = ex.apply(p, dec)
            d["cash"] = round(d["cash"] + cash_delta, 2)
            d["realised_usd"] = round(d.get("realised_usd", 0.0) + realised, 2)
            fills.append({"sym": sym, **dec, "realised_usd": realised,
                          "cash_delta_usd": cash_delta})
            ticket = evidence.ticket('fundamental_spec_book', sym, dec['action'], 'long',
                                     units_before - p['units'], p['mark_usd'],
                                     (units_before - p['units']) * p['entry_px_usd'],
                                     cash_delta, realised,
                                     **receipt(q),
                                     cost_source='Registered speculative exit cost model')
            p['last_paper_exit_source_utc'] = q.get('quote_utc')
            p.setdefault('exit_fills', []).append({**ticket, 'execution_receipt': ticket})
            fills[-1]['ticket_id'] = ticket['ticket_id']
        evidence.record(d, sym, p, 'HOLD' if dec['action'] == 'hold' else 'EXIT',
                        dec.get('reason', ''), decision=dec, applied=True, quote=fb.quote_metadata(q))
        if dec['action'] in ('scale_out', 'close_runner', 'catalyst_expired'):
            d['exit_checks']['legs'][sym]['ticket'] = ticket

    for sym in [s for s, p in pos.items() if p.get("units", 0) <= 1e-9]:
        d.setdefault("closed", {})[sym] = pos.pop(sym)

    # The released tranche is now realised and in cash; only the remaining
    # units may contribute unrealised P&L. Re-use the stored USD mark used by
    # equity, including the explicitly stale last-good mark when unavailable.
    for p in pos.values():
        if p.get("mark_usd") is not None:
            p["unrealised_usd"] = round(
                (p["mark_usd"] - p["entry_px_usd"]) * p["units"], 2)

    long_mv = sum((p.get("mark_usd") or 0) * p.get("units", 0)
                  for p in pos.values())
    equity = round(d["cash"] + long_mv, 2)
    d["long_mv_usd"] = round(long_mv, 2)
    d["unrealised_usd"] = round(sum(p.get("unrealised_usd") or 0
                                    for p in pos.values()), 2)
    d["equity"] = equity
    d["n_positions"] = len(pos)
    r = d["sleeve_risk"]
    # tfo_specdd_20261030: drawdown on FLOW-ADJUSTED equity. ~US$49.6k was swept out as capital_transfer (Sep 16-25), which the raw
    # equity/peak ratio booked as a -99.51% "drawdown" (and froze entries). Transfers are not P&L: add net transfers back before comparing.
    flows = round(sum(float(e.get("usd") or 0) for e in d.get("capital_events") or [] if e.get("type") == "capital_transfer"), 2)
    adj = round(equity - flows, 2)
    r["peak_equity_flow_adj"] = max(r.get("peak_equity_flow_adj", r.get("peak_equity", adj)), adj)
    r["peak_equity"] = max(r.get("peak_equity", equity), equity)   # raw, kept for reference only
    r["equity"] = equity; r["equity_flow_adj"] = adj; r["net_transfers_usd"] = flows; r["dd_basis"] = "flow_adjusted"
    r["drawdown_pct"] = round((adj / r["peak_equity_flow_adj"] - 1) * 100, 4) if r["peak_equity_flow_adj"] else 0.0
    r["new_entries_frozen"] = r["drawdown_pct"] <= sp.SLEEVE_KILL_DD_PCT
    d["trade_fills_this_tick"] = fills
    d["updated"] = fb._utc()
    evidence.finish(d)
    save(d)

    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "equity_usd", "cash_usd", "long_mv_usd",
                        "unrealised_usd", "n_positions", "drawdown_pct"])
        w.writerow([d["updated"], equity, d["cash"], d["long_mv_usd"],
                    d["unrealised_usd"], len(pos), r["drawdown_pct"]])

    if verbose:
        print("SPEC SLEEVE  equity $%s  cash $%s  %d positions  dd %.2f%%"
              % ("{:,.2f}".format(equity), "{:,.2f}".format(d["cash"]),
                 len(pos), r["drawdown_pct"]))
        for fl in fills:
            print("  FILL %-9s %-13s %s" % (fl["sym"], fl["action"], fl["reason"]))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--unsourced", action="store_true",
                    help="CM-069: list open positions whose catalyst date "
                         "carries no provenance. Reports only — never closes.")
    a = ap.parse_args()
    if a.unsourced:
        d = load()
        rows = [(k, v.get("catalyst_date"), v.get("catalyst_source"))
                for k, v in d["positions"].items()]
        bad = [r for r in rows if not r[2]]
        print("CM-069 PROVENANCE AUDIT — %d of %d open positions carry no "
              "sourced catalyst date." % (len(bad), len(rows)))
        for k, cd, _ in sorted(bad):
            print("   %-10s catalyst_date=%s   source=MISSING" % (k, cd))
        if bad:
            print("\nThese pre-date the CM-069 gate and are NOT force-closed "
                  "(TOURNAMENT_PREREGISTRATION.md: never force-liquidate).\n"
                  "They exit on their registered rules. New entries are refused "
                  "without provenance.")
        return
    if a.create:
        d = create()
        print("sleeve created: $%s cash, PAPER ONLY"
              % "{:,.2f}".format(d["cash"]))
    elif a.mark:
        mark()
    else:
        d = load()
        print(json.dumps({k: v for k, v in d.items()
                          if not isinstance(v, (dict, list))}, indent=1))
        for s, p in d["positions"].items():
            print("  %-9s %6.0f u  entry $%.4f  mark $%.4f  unreal $%s"
                  % (s, p["units"], p["entry_px_usd"], p["mark_usd"],
                     "{:,.2f}".format(p.get("unrealised_usd") or 0)))


if __name__ == "__main__":
    main()
