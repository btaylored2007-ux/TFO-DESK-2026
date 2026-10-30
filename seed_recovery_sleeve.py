#!/usr/bin/env python3
"""seed_recovery_sleeve.py — dual-momentum idle-cash deploy for TFO Fundamental.

PAPER ONLY. Rules frozen in SEED_RECOVERY_PREREGISTRATION.md (2026-09-16).
Changing constants here without a new registration invalidates the seal.

Usage:
  python3 seed_recovery_sleeve.py deploy
  python3 seed_recovery_sleeve.py tick [--force]
  python3 seed_recovery_sleeve.py --status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import desk_lock

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
STATE = os.path.join(BASE, "reports", "seed_recovery_state.json")
LOG = os.path.join(BASE, "reports", "seed_recovery_ledger.jsonl")
PREREG = os.path.join(BASE, "SEED_RECOVERY_PREREGISTRATION.md")

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
            "GLD", "SLV", "DBC", "USO", "IYR", "AGG"]
SAFE = "IEF"
LOOKBACK = 252
TOP = 2
REBALANCE_TD = 21
COST_BPS_SIDE = 10.0
MIN_CASH_BUFFER_USD = 100.0
SLEEVE = "seed_recovery"
SLEEVE_KILL_DD = -0.30
REVIEW_DAYS = 182


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log(ev):
    ev = dict(ev)
    ev["utc"] = _utc()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(ev) + "\n")


def _prereg_hash():
    return hashlib.sha256(open(PREREG, "rb").read()).hexdigest()


def _load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def fetch_prices():
    import yfinance as yf
    raw = yf.download(UNIVERSE, period="2y", interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    return raw["Close"].dropna(how="all")


def choose_targets(px):
    if len(px) < LOOKBACK + 1:
        raise RuntimeError("insufficient history: %d bars" % len(px))
    trail = px.iloc[-1] / px.iloc[-1 - LOOKBACK] - 1.0
    ranked = trail.sort_values(ascending=False)
    picks = [t for t in ranked.index[:TOP] if float(trail[t]) > 0]
    w = {}
    per = 1.0 / TOP
    for t in picks:
        w[t] = per
    safe_w = 1.0 - sum(w.values())
    if safe_w > 1e-9:
        w[SAFE] = w.get(SAFE, 0.0) + safe_w
    top5 = {t: round(float(trail[t]) * 100, 2) for t in list(ranked.index[:5])}
    return w, top5


def trading_days_since(px_index, date_str):
    if not date_str:
        return None
    dates = [d.strftime("%Y-%m-%d") for d in px_index]
    if date_str not in dates:
        return len([d for d in dates if d > date_str])
    return len(dates) - 1 - dates.index(date_str)


def sleeve_positions(led):
    return {s: p for s, p in led.get("positions", {}).items()
            if p.get("sleeve") == SLEEVE}


def sleeve_equity(led, last_px):
    eq = 0.0
    for sym, p in sleeve_positions(led).items():
        px = float(last_px.get(sym, p.get("mark_usd") or p.get("entry_px_usd") or 0))
        eq += p["units"] * px
    # sleeve also owns a share of book cash only via state.cash_buffer tracking;
    # deploy spends book cash directly.
    return eq


def _firm_ok():
    try:
        import book_kill as _kill
        ok, why = _kill.firm_entries_allowed("TFO Fundamental Desk", verbose=False)
        return ok, why
    except Exception as e:
        return True, "firm gate unreadable (%s) — fail OPEN" % e


def _release_ago(led):
    un = led.setdefault("unopened", {})
    ago = un.pop("AGO", None)
    if ago is None:
        return None
    note = {
        "released_utc": _utc(),
        "prior": ago,
        "reason": ("Funder-directed 2026-09-16: release AGO provisional reservation; "
                   "idle cash redirected to Seed Recovery Sleeve "
                   "(SEED_RECOVERY_PREREGISTRATION.md)."),
    }
    led.setdefault("unopened_released", []).append(note)
    led["unopened_released"] = led["unopened_released"][-50:]
    return note


def _mk_position(sym, units, px, notional, thesis, catalyst_date):
    now = _utc()
    return {
        "side": "long",
        "notional_usd": round(notional, 2),
        "entry_px": float(px),
        "entry_ccy": "USD",
        "entry_fx_usd": 1.0,
        "entry_px_usd": float(px),
        "units": float(units),
        "entry_utc": now,
        "mark": float(px),
        "mark_usd": float(px),
        "mark_fx_usd": 1.0,
        "mark_utc": now,
        "mark_stale": False,
        "unrealised_usd": 0.0,
        "sector": "financials",
        "sleeve": SLEEVE,
        "strategy": "dual_momentum_top2",
        "catalyst_date": catalyst_date,
        "thesis": thesis,
        "exit_state": {"released": 0.0, "rungs_hit": [], "peak_net_gain": None,
                       "orig_units": float(units)},
        "registration": "SEED_RECOVERY_PREREGISTRATION.md",
    }


def deploy(dry_run=False):
    with desk_lock.ledger_lock("fundamental", timeout=120):  # 2026-09-26 HT #7: writes fundamental_book.json -> same lock as every other writer
        return _deploy_locked(dry_run)


def _deploy_locked(dry_run):
    led = _load_json(LEDGER)
    if not isinstance(led, dict):
        print("REFUSED: ledger unreadable")
        return 2
    state = _load_json(STATE, {}) or {}
    if state.get("deployed_utc") and sleeve_positions(led):
        print("REFUSED: sleeve already deployed at %s" % state["deployed_utc"])
        return 2

    ok, why = _firm_ok()
    if not ok:
        print("REFUSED: firm entries blocked — %s" % why)
        return 2

    prereg_sha = _prereg_hash()
    px = fetch_prices()
    last = px.iloc[-1]
    targets, top5 = choose_targets(px)
    print("targets:", targets, "top5:", top5)

    cash = float(led.get("cash") or 0)
    deployable = max(0.0, cash - MIN_CASH_BUFFER_USD)
    if deployable < 1000:
        print("REFUSED: deployable cash $%.2f too small" % deployable)
        return 2

    # Refuse colliding symbols already held outside the sleeve
    for sym in targets:
        if sym in led.get("positions", {}) and led["positions"][sym].get("sleeve") != SLEEVE:
            print("REFUSED: %s already held outside sleeve" % sym)
            return 2

    rate = COST_BPS_SIDE / 10000.0
    # budget for entry costs on full notional
    gross = deployable / (1.0 + rate)
    cost = gross * rate

    catalyst = time.strftime("%Y-%m-%d", time.gmtime(time.time() + REVIEW_DAYS * 86400))
    thesis = (
        "SEED RECOVERY SLEEVE (funder-directed 2026-09-16): dual-momentum top-2 "
        "of 15 liquid ETFs, identical shape to Growth Book. Puts idle cash to work "
        "in the firm's highest-certainty risk-on expression; no new alpha claim. "
        "KILLS IT: sleeve peak-to-trough drawdown reaches -30%%, OR 182-day review "
        "on %s finds risk-adjusted return does not beat the T-bill cash benchmark "
        "the idle cash would have earned."
    ) % catalyst

    fills = []
    remaining = gross
    planned = {t: gross * w for t, w in targets.items()}
    new_positions = {}
    for sym, notional in planned.items():
        price = float(last[sym])
        if not (price > 0):
            print("REFUSED: bad price for %s" % sym)
            return 2
        units = notional / price
        new_positions[sym] = _mk_position(sym, units, price, notional, thesis, catalyst)
        fills.append({"symbol": sym, "notional": round(notional, 2),
                      "units": units, "px": price, "weight": targets[sym]})
        remaining -= notional

    ago_note = _release_ago(led)

    if dry_run:
        print("DRY-RUN would deploy $%.2f cost $%.2f into %s" % (gross, cost, fills))
        print("AGO release:", ago_note is not None)
        return 0

    for sym, pos in new_positions.items():
        led.setdefault("positions", {})[sym] = pos

    led["cash"] = round(cash - gross - cost, 2)
    led["costs_paid_usd"] = round(float(led.get("costs_paid_usd") or 0) + cost, 4)
    led.setdefault("capital_events", []).append({
        "type": "authorization",
        "utc": _utc(),
        "note": ("Seed Recovery Sleeve deploy — funder full authorisation 2026-09-16. "
                 "Idle cash → dual-momentum ETF sleeve. PAPER ONLY."),
        "usd": 0.0,
        "prereg_sha256": prereg_sha,
        "deployed_notional_usd": round(gross, 2),
        "entry_cost_usd": round(cost, 2),
    })
    long_mv = short_mv = unreal = 0.0
    for pos in led["positions"].values():
        mv = pos["units"] * float(pos.get("mark_usd") or pos.get("entry_px_usd") or 0)
        if pos["side"] == "long":
            pnl = mv - pos["notional_usd"]
            long_mv += mv
        else:
            pnl = pos["notional_usd"] - mv
            short_mv += mv
        pos["unrealised_usd"] = round(pnl, 2)
        unreal += pnl
    short_notional = sum(v["notional_usd"] for v in led["positions"].values()
                         if v["side"] == "short")
    short_pnl = short_notional - short_mv
    equity = float(led["cash"]) + long_mv + short_notional + short_pnl
    led["long_mv_usd"] = round(long_mv, 2)
    led["short_mv_usd"] = round(short_mv, 2)
    led["short_notional_usd"] = round(short_notional, 2)
    led["unrealised_usd"] = round(unreal, 2)
    led["equity"] = round(equity, 2)
    led["n_long"] = sum(1 for pos in led["positions"].values() if pos["side"] == "long")
    led["n_short"] = sum(1 for pos in led["positions"].values() if pos["side"] == "short")
    led["updated"] = _utc()
    led["seed_recovery"] = {
        "registration": "SEED_RECOVERY_PREREGISTRATION.md",
        "prereg_sha256": prereg_sha,
        "deployed_utc": _utc(),
        "deployed_notional_usd": round(gross, 2),
        "entry_cost_usd": round(cost, 2),
        "targets": targets,
        "top5_trailing_pct": top5,
    }

    state = {
        "deployed_utc": _utc(),
        "prereg_sha256": prereg_sha,
        "last_rebalance_date": px.index[-1].strftime("%Y-%m-%d"),
        "rebalances": 1,
        "peak_equity": round(gross, 2),
        "deployed_notional_usd": round(gross, 2),
        "entry_cost_usd": round(cost, 2),
        "targets_at_deploy": targets,
        "top5_trailing_pct": top5,
        "ago_released": ago_note is not None,
        "review_utc_date": catalyst,
        "paper_only": True,
    }

    _save_json(LEDGER, led)
    _save_json(STATE, state)
    _log({"event": "DEPLOY", "gross": round(gross, 2), "cost": round(cost, 2),
          "fills": fills, "targets": targets, "top5": top5,
          "cash_after": led["cash"], "ago_released": ago_note is not None,
          "prereg_sha256": prereg_sha})
    print("DEPLOYED $%.2f (cost $%.2f) into %s" % (
        gross, cost, ", ".join("%s $%.0f" % (f["symbol"], f["notional"]) for f in fills)))
    print("cash now $%.2f | equity field $%.2f | AGO released=%s" % (
        led["cash"], led.get("equity"), ago_note is not None))
    print("prereg sha256 %s" % prereg_sha)
    return 0


def tick(force=False):
    with desk_lock.ledger_lock("fundamental", timeout=120):  # 2026-09-26 HT #7: writes fundamental_book.json -> same lock as every other writer
        return _tick_locked(force)


def _tick_locked(force):
    import head_trader_gate as _htg
    if _htg.active():
        print('%s: Head Trader only flag set, engine will not ticket' % __name__)
        return None
    led = _load_json(LEDGER)
    state = _load_json(STATE, {}) or {}
    if not state.get("deployed_utc"):
        print("sleeve not deployed")
        return 2
    if state.get("prereg_sha256") != _prereg_hash():
        print("REFUSED: prereg hash drift — registration broken")
        return 2

    px = fetch_prices()
    last = px.iloc[-1]
    # mark sleeve positions
    sleeve_eq = 0.0
    for sym, p in sleeve_positions(led).items():
        price = float(last[sym])
        p["mark"] = price
        p["mark_usd"] = price
        p["mark_fx_usd"] = 1.0
        p["mark_utc"] = _utc()
        p["mark_stale"] = False
        p["unrealised_usd"] = round(p["units"] * price - p["notional_usd"], 2)
        sleeve_eq += p["units"] * price

    peak = float(state.get("peak_equity") or sleeve_eq)
    if sleeve_eq > peak:
        peak = sleeve_eq
    state["peak_equity"] = round(peak, 2)
    dd = (sleeve_eq / peak - 1.0) if peak > 0 else 0.0
    state["drawdown_pct"] = round(dd * 100, 3)

    if dd <= SLEEVE_KILL_DD:
        # flatten sleeve to cash
        proceeds = 0.0
        cost = 0.0
        rate = COST_BPS_SIDE / 10000.0
        for sym in list(sleeve_positions(led)):
            p = led["positions"].pop(sym)
            px_ = float(last[sym])
            gross = p["units"] * px_
            c = gross * rate
            proceeds += gross - c
            cost += c
            led.setdefault("closed", {})[sym] = p
        led["cash"] = round(float(led["cash"]) + proceeds, 2)
        led["costs_paid_usd"] = round(float(led.get("costs_paid_usd") or 0) + cost, 4)
        state["killed_utc"] = _utc()
        state["killed_reason"] = "sleeve DD %.2f%% <= -30%%" % (dd * 100)
        _save_json(LEDGER, led)
        _save_json(STATE, state)
        _log({"event": "SLEEVE_KILL", "dd": dd, "proceeds": proceeds})
        print("SLEEVE KILLED:", state["killed_reason"])
        return 0

    due = force or not state.get("last_rebalance_date") or \
        (trading_days_since(px.index, state.get("last_rebalance_date")) or 0) >= REBALANCE_TD
    if not due:
        _save_json(STATE, state)
        print("marked sleeve equity $%.2f dd %.2f%% — rebalance not due" % (sleeve_eq, dd * 100))
        return 0

    ok, why = _firm_ok()
    targets, top5 = choose_targets(px)
    rate = COST_BPS_SIDE / 10000.0

    # liquidate current sleeve into cash budget then reopen
    old_syms = list(sleeve_positions(led))
    cash_pool = 0.0
    turnover = 0.0
    for sym in old_syms:
        p = led["positions"].pop(sym)
        px_ = float(last[sym])
        gross = p["units"] * px_
        c = gross * rate
        cash_pool += gross - c
        turnover += gross
        led["costs_paid_usd"] = round(float(led.get("costs_paid_usd") or 0) + c, 4)

    if not ok:
        # under halt: park in SAFE only / or leave as cash in book
        print("firm halt — parking sleeve proceeds in cash ($%.2f): %s" % (cash_pool, why))
        led["cash"] = round(float(led["cash"]) + cash_pool, 2)
        state["last_rebalance_date"] = px.index[-1].strftime("%Y-%m-%d")
        state["rebalances"] = int(state.get("rebalances") or 0) + 1
        state["halt_parked_utc"] = _utc()
        _save_json(LEDGER, led)
        _save_json(STATE, state)
        _log({"event": "HALT_PARK", "cash_pool": cash_pool, "why": why})
        return 0

    # reopen to targets from cash_pool
    deployable = cash_pool / (1.0 + rate)
    entry_cost = deployable * rate
    catalyst = state.get("review_utc_date") or time.strftime(
        "%Y-%m-%d", time.gmtime(time.time() + REVIEW_DAYS * 86400))
    thesis = (
        "SEED RECOVERY SLEEVE rebalance. KILLS IT: -30%% sleeve DD or 182-day "
        "review on %s vs T-bill cash benchmark."
    ) % catalyst
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    for sym in targets:  # refuse the whole rebalance before any write (US ETF universe: never trips today)
        v1_venues.require_entry_allowed(sym)
    fills = []
    for sym, w in targets.items():
        notional = deployable * w
        price = float(last[sym])
        units = notional / price
        led["positions"][sym] = _mk_position(sym, units, price, notional, thesis, catalyst)
        fills.append({"symbol": sym, "notional": round(notional, 2), "px": price})
    led["costs_paid_usd"] = round(float(led.get("costs_paid_usd") or 0) + entry_cost, 4)
    # residual to book cash
    residual = cash_pool - deployable - entry_cost
    led["cash"] = round(float(led["cash"]) + residual, 2)
    led["updated"] = _utc()

    state["last_rebalance_date"] = px.index[-1].strftime("%Y-%m-%d")
    state["rebalances"] = int(state.get("rebalances") or 0) + 1
    state["last_targets"] = targets
    _save_json(LEDGER, led)
    _save_json(STATE, state)
    _log({"event": "REBALANCE", "targets": targets, "top5": top5, "fills": fills,
          "sleeve_eq_before": round(sleeve_eq, 2)})
    print("REBALANCED sleeve into %s (turnover~%.0f)" % (targets, turnover))
    return 0


def status():
    led = _load_json(LEDGER, {})
    state = _load_json(STATE, {}) or {}
    sp = sleeve_positions(led or {})
    print(json.dumps({
        "state": state,
        "n_positions": len(sp),
        "symbols": {s: {"notional": p.get("notional_usd"),
                        "unrealised": p.get("unrealised_usd"),
                        "units": p.get("units")} for s, p in sp.items()},
        "book_cash": (led or {}).get("cash"),
        "book_equity": (led or {}).get("equity"),
    }, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["deploy", "tick", "status"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "deploy":
        return deploy(dry_run=args.dry_run)
    if args.cmd == "tick":
        return tick(force=args.force)
    return status()


if __name__ == "__main__":
    sys.exit(main())
