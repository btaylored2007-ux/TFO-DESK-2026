#!/usr/bin/env python3
"""Growth Book — the pre-registered maximum-defensible-growth paper portfolio.

Strategy, rationale, claims and kill-criteria are FROZEN in
GROWTH_BOOK_PREREGISTRATION.md (2026-08-06). Read that first. Changing the rules
here invalidates the registration — log a new registration instead of editing.

Dual momentum, top-2 of 15 asset-class ETFs, monthly rebalance, positive-return
filter with IEF fallback, 10 bps per side, no leverage, $50k paper start.

PAPER ONLY. No broker, no credentials, no real orders.

Usage:
    python3 growth_book.py            # tick (rebalances only when 21 trading days due)
    python3 growth_book.py --status
    python3 growth_book.py --force    # force a rebalance check (used at first tick)
"""
import argparse
import json
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "growth_book.json")
LEDGER = os.path.join(BASE, "reports", "growth_book_ledger.jsonl")
EQUITY_CSV = os.path.join(BASE, "reports", "growth_book_equity.csv")

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
            "GLD", "SLV", "DBC", "USO", "IYR", "AGG"]
SAFE = "IEF"
LOOKBACK = 252
TOP = 2
REBALANCE_TD = 21          # trading days
COST_BPS_SIDE = 10.0
START_CASH = 50000.0


def _log(ev):
    ev["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(ev) + "\n")


def load_book():
    try:
        with open(BOOK) as f:
            return json.load(f)
    except Exception:
        return {"cash": START_CASH, "holdings": {}, "last_rebalance_date": None,
                "rebalances": 0, "costs_paid": 0.0, "peak_equity": START_CASH,
                "registered": "2026-08-06"}


def save_book(b):
    b["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
    os.replace(tmp, BOOK)


def fetch_prices():
    import yfinance as yf
    raw = yf.download(UNIVERSE, period="2y", interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    return raw["Close"].dropna()


def equity_of(b, last_px):
    return b["cash"] + sum(u * float(last_px.get(t, 0.0))
                           for t, u in b["holdings"].items())


def trading_days_since(px_index, date_str):
    if date_str is None:
        return None
    dates = [d.strftime("%Y-%m-%d") for d in px_index]
    if date_str not in dates:
        # last rebalance date may not be a bar date if data revised; count >= it
        return len([d for d in dates if d > date_str])
    return len(dates) - 1 - dates.index(date_str)


def choose_targets(px):
    """Frozen rule: top-2 by 252d trailing return, positive filter, IEF fallback."""
    if len(px) < LOOKBACK + 1:
        raise RuntimeError("insufficient history: %d bars" % len(px))
    trail = px.iloc[-1] / px.iloc[-1 - LOOKBACK] - 1.0
    ranked = trail.sort_values(ascending=False)
    picks = [t for t in ranked.index[:TOP] if trail[t] > 0]
    w = {}
    per = 1.0 / TOP
    for t in picks:
        w[t] = per
    safe_w = 1.0 - sum(w.values())
    if safe_w > 1e-9:
        w[SAFE] = w.get(SAFE, 0.0) + safe_w
    return w, {t: round(float(trail[t]) * 100, 2) for t in ranked.index[:5]}



def _firm_halt_blocks(b, want, have, sym, _seen=None):
    """CM-047 firm halt clamp. Returns the permitted target notional.

    Under a firm-wide HALT_NEW_ENTRIES a target may not EXCEED what is already
    held: new names and add-tos are refused, reductions and full exits pass
    untouched. Fails OPEN — a missing gate never halts trading.
    """
    try:
        import book_kill as _kill
        ok, why = _kill.firm_entries_allowed(b.get("name", ""), verbose=False)
    except Exception:                               # noqa: BLE001 - fail OPEN
        return want
    if ok or want <= have:
        return want
    print("   HALT-REFUSED %s: %s target $%.0f clamped to held $%.0f — %s"
          % (b.get("name", "book"), sym, want, have, why))
    b.setdefault("halt_refusals", []).append({"sym": sym, "want": round(want, 2),
                                              "clamped_to": round(have, 2),
                                              "reason": why})
    b["halt_refusals"] = b["halt_refusals"][-50:]
    return have


def rebalance(b, px):
    last = px.iloc[-1]
    eq = equity_of(b, last)
    targets, top5 = choose_targets(px)

    # Solve the cash budget including turnover fees; preserve target weights.
    old = {t: u * float(last[t]) for t, u in b["holdings"].items()}
    desired = {t: max(0.0, _firm_halt_blocks(b, eq * w, old.get(t, 0), t))
               for t, w in targets.items()}
    rate = COST_BPS_SIDE / 10000.0
    def budget(scale):
        vals = {t: v * scale for t, v in desired.items()}
        turnover = sum(abs(vals.get(t, 0) - old.get(t, 0)) for t in set(vals) | set(old))
        return sum(vals.values()) + turnover * rate, turnover
    lo, hi = 0.0, 1.0
    for _ in range(64):
        mid = (lo + hi) / 2
        if budget(mid)[0] <= eq:
            lo = mid
        else:
            hi = mid
    scale = 1.0 if budget(1.0)[0] <= eq else lo
    new_holdings = {t: v * scale / float(last[t]) for t, v in desired.items() if v > 0}
    _, traded_notional = budget(scale)
    cost = traded_notional * rate
    b["holdings"] = new_holdings
    # bookkeeping only (not a rule change): record entry prices for live P&L
    b["entry_px"] = {t: float(last[t]) for t in new_holdings}
    b["cash"] = eq - sum(u * float(last[t]) for t, u in new_holdings.items()) - cost
    b["costs_paid"] = round(b.get("costs_paid", 0.0) + cost, 2)
    b["last_rebalance_date"] = px.index[-1].strftime("%Y-%m-%d")
    b["rebalances"] = b.get("rebalances", 0) + 1
    _log({"event": "REBALANCE", "targets": {k: round(v, 3) for k, v in targets.items()},
          "top5_trailing_pct": top5, "equity": round(eq, 2),
          "traded_notional": round(traded_notional, 2), "cost": round(cost, 2)})
    return targets, top5, cost


def tick(force=False, verbose=True):
    b = load_book()
    # ---- cash yield amendment (CASH_YIELD_AMENDMENT.md, funder-directed
    # 2026-08-27, "GO FOR IT"). Idle cash accrues the live 13-week T-bill rate
    # (^IRX), prorated by ACTUAL elapsed time, FORWARD ONLY from this book's
    # first accrual — never retroactive. Credited BEFORE the frozen rebalance
    # logic below so equity_of() values it correctly; not one byte of the
    # rebalance rule changed. cash_interest_accrued is kept as a SEPARATE field
    # so the November review can always split STRATEGY return from INTEREST
    # return. Fail-safe: no rate / bad rate / stale >24h accrues ZERO, loudly.
    # Never breaks the tick.
    try:
        import cash_yield
        _cy = cash_yield.accrue(b, b.get("cash", 0.0))
        if _cy:
            b["cash"] = b.get("cash", 0.0) + _cy
    except Exception as e:
        msg = "CASH-YIELD ERROR: growth_book: " + str(e)[:200]
        print(msg)
        b["cash_yield_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    px = fetch_prices()
    last = px.iloc[-1]
    today = px.index[-1].strftime("%Y-%m-%d")

    due = force or b["last_rebalance_date"] is None or \
        (trading_days_since(px.index, b["last_rebalance_date"]) or 0) >= REBALANCE_TD

    action = None
    if due:
        targets, top5, cost = rebalance(b, px)
        action = "REBALANCED into %s (cost $%.2f)" % (
            ", ".join("%s %.0f%%" % (t, w * 100) for t, w in targets.items()), cost)

    # ---- exit axis. While disabled it does bookkeeping only (not a rule
    # change): entry_sigma frozen from this book's own 2y settled daily
    # closes, peak_close per symbol from settled DAILY closes (never the
    # intraday mark), peak_tracking_since (UTC).
    # DECISIONS are a NO-OP unless reports/exit_axis_config.json
    # enables growth_book — that is a rule change needing a funder-signed
    # amendment; the frozen registration above is untouched while disabled).
    # Loud on failure, never breaks the tick.
    try:
        import exit_axis
        xa_act = exit_axis.growth_exit_check(b, px, COST_BPS_SIDE, _log)
        if xa_act:
            action = (action + "; " + xa_act) if action else xa_act
    except Exception as e:
        msg = "EXIT-AXIS ERROR: growth_book: %s" % str(e)[:200]
        print(msg)
        b["exit_axis_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    eq = equity_of(b, last)
    b["peak_equity"] = max(b.get("peak_equity", eq), eq)
    dd = eq / b["peak_equity"] - 1.0
    b["equity"] = round(eq, 2)
    b["drawdown_pct"] = round(dd * 100, 2)
    b["review_flag"] = bool(dd <= -0.35)
    save_book(b)

    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a") as f:
        if new:
            f.write("date,equity,drawdown_pct\n")
        f.write("%s,%.2f,%.2f\n" % (today, eq, dd * 100))

    if verbose:
        status(b, last, action)
    return b, action


def status(b=None, last_px=None, action=None):
    b = b or load_book()
    print("GROWTH BOOK — pre-registered 2026-08-06, dual momentum top-2  [PAPER ONLY]")
    print("  equity      : $%.2f  (start $%.0f)   drawdown %.2f%%%s"
          % (b.get("equity", START_CASH), START_CASH, b.get("drawdown_pct", 0.0),
             "   *** REVIEW FLAG: DD > 35% ***" if b.get("review_flag") else ""))
    if b["holdings"]:
        for t, u in sorted(b["holdings"].items()):
            line = "  holding     : %-5s %.4f units" % (t, u)
            if last_px is not None:
                line += "  ($%.2f)" % (u * float(last_px[t]))
            print(line)
    else:
        print("  holdings    : none yet — first tick pending")
    print("  rebalances  : %d   costs paid $%.2f   last %s"
          % (b.get("rebalances", 0), b.get("costs_paid", 0.0),
             b.get("last_rebalance_date")))
    if action:
        print("  this tick   : " + action)
    print("  registration: GROWTH_BOOK_PREREGISTRATION.md — primary evaluation 2028-08-06")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    else:
        tick(force=args.force)


if __name__ == "__main__":
    main()
