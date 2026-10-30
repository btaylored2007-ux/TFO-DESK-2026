#!/usr/bin/env python3
"""VENUE PAPER REPLICA DESK — simulation only, no broker connection of any kind.

A structural replica of the Kraken multi-desk floor (`kraken_floor.py`), running the
same validated strategies on the same signals, but with three things swapped for
the target venue's reality:

  1. the venue's cost schedule instead of Kraken's maker fees   (venue_costs)
  2. the venue's instrument listings instead of Kraken's ~250 USD pairs (venue_universe)
  3. the venue's treatment of shorts -- financing accrues daily where it applies

Everything else -- entry/exit logic, stops, hold limits, sizing, isolation between
desks -- is deliberately identical, so any difference in outcome is attributable to
venue rather than strategy.

WHAT THIS IS NOT
----------------
This places no orders anywhere. It holds NO broker credentials and makes NO
authenticated broker calls. It is a simulation whose purpose is to answer:
*what would these strategies do at the target venue's costs?* -- before, not
after, any real money is involved.

PRICE DATA: public Kraken OHLC is used as the price proxy. The venue's quoted prices
differ slightly, but that difference is second-order against a cost gap of ~12x --
and the cost gap is what this desk exists to measure.

LIQUIDITY SCREEN AMENDMENT (funder-directed 2026-08-25) -- see
LIQUIDITY_SCREEN_AMENDMENT.md, and the identical wiring in kraken_floor.py (the
screen is applied to BOTH floors so the venue comparison stays strategy-for-
strategy). A NEW entry is refused when the candidate's measured 24h USD volume
is below $5,000,000, or cannot be measured at all (fail-closed). Entries ONLY:
no existing position is closed, resized or altered by it -- every open position
still exits by the rules above, unchanged. Refusals are recorded under the
ledger's `liquidity_screen` subtree and printed.

Usage:
    python3 etoro_floor.py             # tick the desks + mark to market
    python3 etoro_floor.py --status    # read the ledger, no tick
    python3 etoro_floor.py --coverage  # universe coverage report only
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
import json
import math
import os
import sys
import time

import venue_costs
import venue_universe

# Reuse the Kraken desk's public-data fetchers. Read-only: no Kraken state is written.
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
if KRAKEN_DESK not in sys.path:
    sys.path.insert(0, KRAKEN_DESK)
from kraken_backtest import fetch_ohlc, sym_to_pair  # noqa: E402

# Liquidity screen (amendment 2026-08-25) -- the SAME module the Kraken floor
# uses, so both floors screen identically. Imported defensively so a broken or
# missing screen FAILS CLOSED (entries refused) while exits on existing
# positions keep running; a hard crash here would trap open positions, which is
# the one outcome the amendment must not cause.
try:
    import liquidity_screen as _liq       # noqa: E402
    _LIQ_ERR = None
except Exception as _e:                   # noqa: BLE001 - reported, fail-closed
    _liq, _LIQ_ERR = None, "%s: %s" % (type(_e).__name__, _e)
    print("   LIQUIDITY SCREEN UNAVAILABLE (%s) -- every NEW entry will be REFUSED"
          % _LIQ_ERR)

# ---- BOOK-LEVEL KILL RULE (funder-directed 2026-08-27) --------------------
# See FIRM_RISK_AMENDMENT.md. This floor had NO book-level kill: no peak, no
# drawdown, no freeze. It now runs the SAME -30% rule the tournament sleeves
# have run since 2026-08-06, imported from `book_kill` (never copied) so the
# Kraken floor, this replica and the tournament cannot drift on what "killed"
# means. Fail-closed on NEW ENTRIES, exits always unaffected.
try:
    import book_kill as _kill             # noqa: E402
    _KILL_ERR = None
except Exception as _e:                   # noqa: BLE001 - reported, fail-closed
    _kill, _KILL_ERR = None, "%s: %s" % (type(_e).__name__, _e)
    print("   BOOK KILL RULE UNAVAILABLE (%s) -- every NEW entry will be REFUSED; "
          "exits are unaffected" % _KILL_ERR)

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "replica_floor.json")
EQUITY_CSV = os.path.join(BASE, "replica_equity.csv")

START_CASH, MAX_POS, ALLOC = 40000.0, 8, 0.10
INTERVAL, BARS = 1440, 220

# Identical to kraken_floor.DESKS so the comparison is strategy-for-strategy.
DESKS = {
    "Mean-Reversion": {"strat": "meanrev", "z_window": 20, "z_entry": 2.0, "z_exit": 0.5,
                       "stop": 8.0, "max_hold_days": 15},
    "Breakout":       {"strat": "breakout", "n": 20, "m": 10, "stop": 8.0,
                       "max_hold_days": 40},
    "Micro-Cap Momentum": {"strat": "breakout", "n": 10, "m": 5, "stop": 14.0,
                           "max_hold_days": 12, "universe": "micro", "alloc": 0.05},
}


# --------------------------------------------------------------------------
# Signals -- byte-for-byte the same logic as kraken_floor, so venue is the only
# variable. Any edit here must be mirrored there, or the comparison is invalid.
# --------------------------------------------------------------------------
def _ms(xs):
    n = len(xs)
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0)


def zscore(c, w):
    if len(c) < w + 1:
        return None
    m, sd = _ms(c[-w:])
    return None if sd <= 0 else (c[-1] - m) / sd


# ---------------------------------------------------------------------------
# CM-066 (2026-09-02): NO SHORTS AT A VENUE THAT CANNOT SHORT.
# This floor's entire purpose is to answer "what would these strategies do at the
# TARGET VENUE'S reality?" -- and the target venue's reality includes what it
# will not let you do. `venue_costs` models ACTIVE_VENUE='robinhood' with
# max_leverage 1.0 and no borrow facility: crypto shorting is IMPOSSIBLE there.
# Yet the floor was carrying two live Mean-Reversion shorts (SOL/USD $3,393.77,
# UNI/USD $3,226.21). Those positions could not exist at the venue being
# simulated, so every number they contributed was fiction.
#
# This is the same class of error as the fundamental desk's unborrowable shorts:
# AVAILABILITY IS AN EXISTENCE QUESTION, NOT A COST ONE. The sibling
# coinspot_floor.py already got this right ("SPOT ONLY -- NO SHORTING"); this
# floor did not.
#
# Venue-driven, not hardcoded: if the active venue ever supports leverage, shorts
# resume automatically. The gate reads the venue profile, so it can never drift
# out of step with the costs the same file already models.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CM-067 (2026-09-03): the edge-halt was applied to ONE floor, not the strategy.
# CM-059 halted Mean-Reversion on kraken_floor.py because its registered OOS edge
# (+14 bp) does not survive the MEASURED 25 bps maker fee (-30 bp round trip).
# `EDGE_HALTED_DESKS` was defined in kraken_floor.py ONLY. This floor runs the
# IDENTICAL strategy on the identical signals — and it opened a NEW UNI/USD short
# on 2026-09-02, three days after the halt, because the halt could not see it.
#
# The arithmetic that halted it does not care which floor it runs on: a +14 bp
# edge cannot pay a 30 bp correction. A halt attached to a FILE instead of a
# STRATEGY is not a halt.
#
# ENTRIES ONLY. Exits, stops and marking are untouched; nothing is liquidated.
EDGE_HALTED_DESKS = {
    "Mean-Reversion": ("OOS edge +14 bp does not survive the measured 25 bps maker "
                       "fee (-30 bp round trip) — halted on kraken_floor by CM-059; "
                       "this floor runs the same strategy and was missed"),
}


def venue_allows_short(asset_class="crypto"):
    """True only if the MODELLED venue can actually be short. Fails CLOSED."""
    try:
        cls = venue_costs.classes() or {}
        c = cls.get(asset_class) or cls.get("crypto") or {}
        lev = c.get("max_leverage")
        return bool(lev and float(lev) > 1.0)
    except Exception as e:                          # noqa: BLE001 - fail closed
        print("   venue short-capability unknown (%s) — refusing shorts"
              % type(e).__name__)
        return False


def entry_signal(cfg, H, L, C):
    if cfg["strat"] == "meanrev":
        z = zscore(C, cfg["z_window"])
        if z is None:
            return None, {}
        if z <= -cfg["z_entry"]:
            return "long", {"z": round(z, 2)}
        if z >= cfg["z_entry"]:
            return "short", {"z": round(z, 2)}
        return None, {}
    if cfg["strat"] == "breakout":
        n = cfg["n"]
        if len(C) < n + 1:
            return None, {}
        hi, lo = max(H[-n - 1:-1]), min(L[-n - 1:-1])
        if C[-1] > hi:
            return "long", {"brk": round(hi, 6)}
        if C[-1] < lo:
            return "short", {"brk": round(lo, 6)}
        return None, {}
    return None, {}


def exit_signal(cfg, pos, H, L, C):
    px = C[-1]
    side = pos["side"]
    move = (px - pos["entry"]) / pos["entry"] if side == "long" else (pos["entry"] - px) / pos["entry"]
    held = (time.time() - pos["entry_epoch"]) / 86400.0
    if move <= -cfg["stop"] / 100.0:
        return "stop"
    if cfg["strat"] == "meanrev":
        z = zscore(C, cfg["z_window"])
        if z is not None and abs(z) <= cfg["z_exit"]:
            return "reverted"
    if cfg["strat"] == "breakout":
        m = cfg["m"]
        if side == "long" and px < min(L[-m - 1:-1]):
            return "channel-exit"
        if side == "short" and px > max(H[-m - 1:-1]):
            return "channel-exit"
    if held >= cfg["max_hold_days"]:
        return "max-hold"
    return None


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------
def load():
    d = None
    if os.path.exists(LEDGER):
        try:
            d = json.load(open(LEDGER))
        except Exception:
            d = None
    if not d or "desks" not in d:
        d = {"desks": {}, "venue": "venue-paper-replica"}
    for name, cfg in DESKS.items():
        d["desks"].setdefault(name, {
            "cash": START_CASH, "realized_pnl": 0.0, "positions": {},
            "closed_trades": [], "strat": cfg["strat"],
            "costs_paid": 0.0, "financing_paid": 0.0, "rejected_not_listed": 0,
            "spread_paid": 0.0, "commission_paid": 0.0,
        })
    return d


def log_equity(d):
    """Append a per-tick equity snapshot so the replica builds a real forward record.

    Written from the first tick onward — nothing is backfilled or synthesised, so an
    empty chart means the desk genuinely has no history yet.
    """
    combined = 0.0
    per_desk = {}
    for name, desk in d["desks"].items():
        eq = desk["cash"] + sum(p["cost"] for p in desk["positions"].values()) \
            + desk.get("unrealized", 0.0)
        per_desk[name] = eq
        combined += eq
    costs = sum(dk.get("costs_paid", 0.0) + dk.get("financing_paid", 0.0)
                for dk in d["desks"].values())
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a") as f:
        if new:
            f.write("timestamp,combined_equity,cumulative_costs,"
                    + ",".join(sorted(per_desk)) + "\n")
        f.write("%s,%.2f,%.2f,%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), combined, costs,
            ",".join("%.2f" % per_desk[k] for k in sorted(per_desk))))
    return combined


def save(d):
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["costs_verified"] = venue_costs.VERIFIED_AGAINST_LIVE_ACCOUNT
    d["universe_verified"] = venue_universe.VERIFIED_AGAINST_LIVE_ACCOUNT
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, LEDGER)


# --------------------------------------------------------------------------
# Universe -- same source as the Kraken floor, then filtered to eToro listings
# --------------------------------------------------------------------------
def kraken_liquid_universe():
    try:
        import urllib.request
        s = json.load(urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=6))
        u = [(x["symbol"], float(x.get("vol_musd", 0) or 0)) for x in (s.get("universe") or [])
             if isinstance(x, dict) and x.get("symbol")]
        u.sort(key=lambda kv: -kv[1])
        return [sym for sym, v in u[:14] if v >= 1.0]
    except Exception:
        return ["XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "XLM/USD",
                "LTC/USD", "LINK/USD"]


def kraken_micro_universe():
    """The Kraken micro-cap desk's hunting ground ($0.5-5M/day)."""
    try:
        import urllib.request
        ap = json.load(urllib.request.urlopen(
            "https://api.kraken.com/0/public/AssetPairs", timeout=15))["result"]
        pairs = [k for k, v in ap.items() if v.get("quote") in ("ZUSD", "USD")][:250]
        out = []
        for i in range(0, len(pairs), 40):
            try:
                t = json.load(urllib.request.urlopen(
                    "https://api.kraken.com/0/public/Ticker?pair=" + ",".join(pairs[i:i + 40]),
                    timeout=15))["result"]
                for k, v in t.items():
                    last = float(v["c"][0])
                    uv = float(v["v"][1]) * last / 1e6
                    ws = ap.get(k, {}).get("wsname", "")
                    if 0.5 <= uv <= 5.0 and last > 0 and ws.endswith("/USD"):
                        out.append((ws, uv))
            except Exception:
                pass
        out.sort(key=lambda kv: kv[1])
        return [ws for ws, uv in out[:12]]
    except Exception:
        return ["COTI/USD", "AKT/USD", "BICO/USD", "FET/USD", "CRV/USD"]


def venue_filter(symbols):
    """Split a Kraken symbol list into what eToro can and cannot trade."""
    ok, blocked = [], {}
    for s in symbols:
        tick, info = venue_universe.resolve(s)
        if tick:
            ok.append((s, tick, info))
        else:
            blocked[s] = info
    return ok, blocked


# --------------------------------------------------------------------------
# Tick
# --------------------------------------------------------------------------
FLOOR_LABEL = "Venue Replica"


def floor_equity(d):
    """This book's equity: cash + Σ entry cost + marked unrealized, all desks.
    Identical arithmetic to `log_equity`'s `combined`, so the kill rule and the
    equity CSV can never disagree about what this book is worth. Read-only."""
    eq = 0.0
    for desk in (d.get("desks") or {}).values():
        eq += float(desk.get("cash") or 0.0)
        eq += sum(float(p.get("cost") or 0.0) for p in (desk.get("positions") or {}).values())
        eq += float(desk.get("unrealized") or 0.0)
    return eq


def kill_check(d, verbose=True):
    """Update this book's drawdown/freeze state. Touches no position.

    The peak is SEEDED from replica_equity.csv — a real recorded series running
    since 2026-08-06, so this book's drawdown is measured against a peak that
    was actually observed rather than starting flat today."""
    if _kill is None:
        return None
    seed = _kill.seed_peak_from_csv(EQUITY_CSV, "combined_equity")
    return _kill.evaluate(d, floor_equity(d), FLOOR_LABEL, seed_peak=seed,
                          seed_note="seeded from replica_equity.csv (recorded since "
                                    "2026-08-06; not synthesised)", verbose=verbose,
                          net_flow_usd=_kill.net_capital_flow(d))  # flow-adjusted DD, custodian 2026-09-26


def kill_entries_allowed(d, desk_name):
    """(allowed, reason) for NEW entries on this desk. Never consulted on exits."""
    if _kill is None:
        return False, ("BOOK KILL RULE UNAVAILABLE (%s) -- fail-closed on NEW entries"
                       % _KILL_ERR)
    return _kill.entries_allowed(d, FLOOR_LABEL, verbose=False)


def liquidity_ok(sym, desk_name):
    """Amendment 2026-08-25 -- NEW-ENTRY veto only. Returns (allowed, record).

    Takes a symbol and a desk NAME (a string), never a position, so it cannot
    reach, close or resize anything already open. Any state in which liquidity
    is unknown returns False."""
    if _liq is None:
        return False, {"symbol": sym, "allowed": False, "vol_usd_24h": None,
                       "min_usd_24h": 5000000.0, "floor": FLOOR_LABEL,
                       "desk": desk_name,
                       "reason": "REFUSED: liquidity screen unavailable (%s) "
                                 "-- fail-closed" % _LIQ_ERR,
                       "failure": "SCREEN_UNAVAILABLE",
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        return _liq.check(sym, floor=FLOOR_LABEL, desk=desk_name)
    except Exception as e:                # noqa: BLE001 - reported, fail-closed
        return False, {"symbol": sym, "allowed": False, "vol_usd_24h": None,
                       "min_usd_24h": 5000000.0, "floor": FLOOR_LABEL,
                       "desk": desk_name,
                       "reason": "REFUSED: liquidity screen raised %s: %s "
                                 "-- fail-closed" % (type(e).__name__, str(e)[:160]),
                       "failure": "SCREEN_RAISED",
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def record_refusal(d, rec):
    """Append to the ledger's own `liquidity_screen` subtree. Only that subtree
    is handed to the screen module -- never `d["desks"]`."""
    sub = d.setdefault("liquidity_screen", {})
    if _liq is None:
        sub.setdefault("amendment",
                       "LIQUIDITY_SCREEN_AMENDMENT.md (funder-directed 2026-08-25)")
        sub.setdefault("refusals", []).insert(0, rec)
        del sub["refusals"][500:]
        sub["refusal_count"] = int(sub.get("refusal_count") or 0) + 1
        sub["last_refusal_utc"] = rec.get("utc")
        print("   LIQUIDITY SCREEN REFUSED ENTRY: %s [%s / %s] %s"
              % (rec.get("symbol"), rec.get("floor"), rec.get("desk"),
                 rec.get("reason")))
        return
    _liq.record(sub, rec)


def tick(verbose=True, exits_only=False):
    from desk_lock import ledger_lock
    with ledger_lock('replica_floor'):
        return _tick_locked(verbose, exits_only)


def _tick_locked(verbose=True, exits_only=False):
    d = load()
    import exit_check_evidence as evidence
    evidence.start(d, 'replica_floor.tick')

    # CM-068 (2026-09-03, funder-directed): accrue T-bill yield on idle cash.
    # This book held $85,844.37 earning exactly zero. Forward-only from the first
    # tick, never retroactive; a missing or >24h-stale rate accrues ZERO loudly
    # rather than inventing money, and the cumulative figure is kept in its own
    # field so rate income is never mistaken for trading skill.
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(d, "Venue Replica Floor")
    except Exception as _cy_e:                              # noqa: BLE001
        print("   CASH-YIELD skipped: %s" % _cy_e)
    # Book-level kill, evaluated on the state as it stands BEFORE this tick's
    # entries. Exits below are never gated by it.
    kill_check(d, verbose=verbose)
    liquid_all = [] if exits_only else kraken_liquid_universe()
    micro_all = [] if exits_only else kraken_micro_universe()

    liquid, liquid_blocked = venue_filter(liquid_all)
    micro, micro_blocked = venue_filter(micro_all)

    coverage_note = {
        "liquid": {"kraken": len(liquid_all), "venue": len(liquid), "blocked": liquid_blocked},
        "micro": {"kraken": len(micro_all), "venue": len(micro), "blocked": micro_blocked},
    }
    d["coverage"] = coverage_note

    # CM-041 (2026-08-27, blind-spot audit): union HELD positions into the
    # bar-fetch set. A name that rotates out of the candidate universe was
    # silently dropped from marking AND exit management -- no stop, no TP, no
    # time stop, mark frozen. See kraken_floor.py for the measured damage.
    _liqs = [s for s, _, _ in liquid]; _mics = [s for s, _, _ in micro]
    _held = [x for _dk in (d.get("desks") or {}).values()
             for x in (_dk.get("positions") or {})]
    _rot = [x for x in dict.fromkeys(_held) if x not in _liqs and x not in _mics]
    if _rot:
        print("   HELD-BUT-OUT-OF-UNIVERSE (managed anyway, CM-041): %s" % ", ".join(_rot))
    all_syms = list(dict.fromkeys(_liqs + _mics + _held))
    cls_of = {s: c for s, _, c in liquid + micro}

    # one batched public-ticker round trip for the whole candidate set (cached
    # 300s). Best-effort: whatever it cannot measure is REFUSED below, never
    # assumed liquid.
    if _liq is not None:
        try:
            _liq.prefetch(all_syms)
        except Exception as e:            # noqa: BLE001 - reported, fail-closed
            print("   LIQUIDITY SCREEN prefetch failed (%s: %s) -- affected symbols "
                  "will be REFUSED" % (type(e).__name__, str(e)[:140]))

    bars, bar_times, bar_errors = {}, {}, {}
    for s in all_syms:
        try:
            b = fetch_ohlc(sym_to_pair(s), INTERVAL, BARS)
            if len(b) >= 60 and evidence.current_bar(b, INTERVAL):
                bars[s] = ([x[2] for x in b], [x[3] for x in b], [x[4] for x in b])
                bar_times[s] = b[-1][0]
            else:
                bar_errors[s] = 'Insufficient, stale, future or invalid registered daily bars'
        except Exception as exc:
            bar_errors[s] = str(exc)[:200]
        time.sleep(0.25)

    events = []
    for name, cfg in DESKS.items():
        desk = d["desks"][name]
        pool = micro if cfg.get("universe") == "micro" else liquid
        desk_syms = [s for s, _, _ in pool]
        alloc = cfg.get("alloc", ALLOC)

        # ---- manage open positions ----
        for sym in list(desk["positions"].keys()):
            if sym not in bars:
                evidence.record(d, name + '/' + sym, desk['positions'][sym],
                                'WAIT_DATA', bar_errors.get(sym, 'No bars'))
                continue
            H, L, C = bars[sym]
            p = desk["positions"][sym]
            px = C[-1]
            reason = exit_signal(cfg, p, H, L, C)
            evidence.record(d, name + '/' + sym, p, 'EXIT' if reason else 'HOLD',
                            reason or 'Registered daily-bar exit signal did not fire',
                            bar_epoch=bar_times[sym], interval_minutes=INTERVAL,
                            signal_price=px, price_class='REGISTERED_DAILY_BAR')
            if not reason:
                continue
            icls = p.get("instrument_class", "crypto")
            held_days = (time.time() - p["entry_epoch"]) / 86400.0
            move = (px - p["entry"]) / p["entry"] if p["side"] == "long" else (p["entry"] - px) / p["entry"]

            gross = move * p["cost"]
            ex_items = venue_costs.cost_items(icls, p["units"] * px)
            ex_cost = ex_items["spread"] + ex_items["commission"]
            desk["spread_paid"] = desk.get("spread_paid", 0.0) + ex_items["spread"]
            desk["commission_paid"] = desk.get("commission_paid", 0.0) + ex_items["commission"]
            fin = venue_costs.financing(icls, p["cost"], held_days, p["side"], p.get("leverage", 1.0))
            pnl = gross - ex_cost - p.get("entry_cost", 0.0) - fin
            ticket = evidence.ticket('replica_floor', sym, reason, p['side'], p['units'], px,
                                     p['cost'], p['cost'] + pnl, pnl, desk=name,
                                     cost_source='Registered venue spread, commission and financing model')
            d['exit_checks']['legs'][name + '/' + sym]['ticket'] = ticket

            desk["cash"] += p["cost"] + pnl
            desk["realized_pnl"] += pnl
            # entry_cost was already booked at entry; only add the exit leg here
            desk["costs_paid"] = desk.get("costs_paid", 0.0) + ex_cost
            desk["financing_paid"] = desk.get("financing_paid", 0.0) + fin
            desk["closed_trades"].insert(0, {
                "t": time.strftime("%Y-%m-%d %H:%M"), "sym": sym, "side": p["side"],
                "pnl": round(pnl, 2), "gross_pnl": round(gross, 2),
                "costs": round(ex_cost + p.get("entry_cost", 0.0) + fin, 2),
                "ret_pct": round(move * 100, 2), "held_days": round(held_days, 2),
                "reason": reason,
                "ticket_id": ticket['ticket_id'], 'execution_receipt': ticket,
            })
            del desk["closed_trades"][200:]
            del desk["positions"][sym]
            events.append("%s: EXIT %s %s net $%.2f (gross $%.2f - costs $%.2f) [%s]"
                          % (name, p["side"], sym, pnl, gross,
                             ex_cost + p.get("entry_cost", 0.0) + fin, reason))

        # ---- open new positions ----
        equity = desk["cash"] + sum(pp["cost"] for pp in desk["positions"].values())
        # ---- BOOK KILL + FIRM HALT (amendment 2026-08-27): NEW ENTRIES ONLY ----
        # Evaluated once per desk, above the entry loop. Every exit for this desk
        # has already run; nothing below this line can reach an open position.
        _entries_ok, _entry_why = kill_entries_allowed(d, name)
        if _entries_ok and name in EDGE_HALTED_DESKS:            # CM-067
            _entries_ok = False
            _entry_why = EDGE_HALTED_DESKS[name]
            d.setdefault("edge_halts", {})[name] = {
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reason": _entry_why, "amendment": "CM-067"}
            print("   EDGE-HALT %s: %s — new entries refused, exits unaffected"
                  % (name, _entry_why))
        _entries_ok = _entries_ok and not exits_only
        if not _entries_ok:
            if _kill is not None:
                _kill.record_refusal(d, FLOOR_LABEL, "(all candidates)", name, _entry_why)
            else:
                print("   ENTRY REFUSED (kill rule unavailable) [%s/%s]: %s"
                      % (FLOOR_LABEL, name, _entry_why))
            events.append("%s: NEW ENTRIES REFUSED (%s)" % (name, _entry_why))
        for sym in (desk_syms if _entries_ok else []):
            if len(desk["positions"]) >= MAX_POS:
                break
            if sym in desk["positions"] or sym not in bars:
                continue
            H, L, C = bars[sym]
            side, meta = entry_signal(cfg, H, L, C)
            if side == "short" and not venue_allows_short():   # CM-066
                events.append("%s: SHORT REFUSED %s — %s cannot be short "
                              "(max_leverage 1.0, no borrow); the signal is real, "
                              "the trade is not"
                              % (name, sym, venue_costs.ACTIVE_VENUE))
                side = None
            if side is None:
                continue

            # ---- LIQUIDITY SCREEN (amendment 2026-08-25): NEW ENTRIES ONLY ----
            # The signal fired; the screen may now veto it. Nothing below this
            # line can touch a position that is already open.
            lok, lrec = liquidity_ok(sym, name)
            if not lok:
                record_refusal(d, lrec)
                events.append("%s: ENTRY REFUSED %s %s (%s)"
                              % (name, side, sym, lrec.get("reason")))
                continue

            icls = cls_of.get(sym, "crypto")
            notional = min(equity * alloc, desk["cash"] * 0.95)
            if notional < max(50.0, venue_costs.min_trade_usd(icls)):
                continue

            px = C[-1]
            items = venue_costs.cost_items(icls, notional)
            en_cost = items["spread"] + items["commission"]
            desk["cash"] -= (notional + en_cost)
            desk["costs_paid"] = desk.get("costs_paid", 0.0) + en_cost
            desk["spread_paid"] = desk.get("spread_paid", 0.0) + items["spread"]
            desk["commission_paid"] = desk.get("commission_paid", 0.0) + items["commission"]
            desk["positions"][sym] = {
                "side": side, "entry": px, "units": notional / px, "cost": notional,
                "entry_epoch": time.time(), "entry_cost": en_cost, "meta": meta,
                "instrument_class": icls, "leverage": 1.0,
                "is_cfd": venue_costs.is_cfd(side, 1.0),
            }
            events.append("%s: ENTER %s %s @ %s cost-in $%.2f %s"
                          % (name, side, sym, round(px, 6), en_cost, meta))

        # count what the strategy wanted but eToro could not offer
        blocked = micro_blocked if cfg.get("universe") == "micro" else liquid_blocked
        desk["rejected_not_listed"] = len(blocked)

    # ---- mark to market, net of accrued financing ----
    for nm in DESKS:
        desk = d["desks"][nm]
        u = 0.0
        for sym, p in desk["positions"].items():
            if sym not in bars:
                continue
            px = bars[sym][2][-1]
            move = (px - p["entry"]) / p["entry"] if p["side"] == "long" else (p["entry"] - px) / p["entry"]
            held_days = (time.time() - p["entry_epoch"]) / 86400.0
            accrued = venue_costs.financing(p.get("instrument_class", "crypto"), p["cost"],
                                            held_days, p["side"], p.get("leverage", 1.0))
            exit_est = venue_costs.exit_cost(p.get("instrument_class", "crypto"), p["units"] * px)
            net = move * p["cost"] - accrued - exit_est - p.get("entry_cost", 0.0)
            p["mark"] = round(px, 8)
            p["gross_upnl"] = round(move * p["cost"], 2)
            p["accrued_financing"] = round(accrued, 2)
            p["upnl"] = round(net, 2)   # net of every cost still to be paid
            u += net
        desk["unrealized"] = round(u, 2)

    # re-evaluate on this tick's fresh marks so the published drawdown is current
    kill_check(d, verbose=False)
    evidence.finish(d)
    save(d)
    log_equity(d)
    return d, events


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def desk_summary(name, desk):
    eq = desk["cash"] + sum(p["cost"] for p in desk["positions"].values())
    ct = desk["closed_trades"]
    pnls = [c["pnl"] for c in ct]
    wins = len([x for x in pnls if x > 0])
    wr = round(wins / len(pnls) * 100, 1) if pnls else None
    unreal = desk.get("unrealized", 0.0)
    return {
        "equity": round(eq, 2), "realized_pnl": round(desk["realized_pnl"], 2),
        "unrealized": round(unreal, 2),
        "total_pnl": round(desk["realized_pnl"] + unreal, 2),
        "open": len(desk["positions"]), "closed": len(ct), "win_rate": wr,
        "costs_paid": round(desk.get("costs_paid", 0.0), 2),
        "financing_paid": round(desk.get("financing_paid", 0.0), 2),
    }


def status(d):
    print("%s PAPER REPLICA FLOOR   (simulation only -- no broker connection)"
          % venue_costs.active()["label"].upper())
    print("  " + venue_costs.verification_notice())
    print("  " + venue_universe.verification_notice())
    print()
    tot = 0.0
    tot_costs = 0.0
    for name, desk in d["desks"].items():
        s = desk_summary(name, desk)
        tot += s["total_pnl"]
        tot_costs += s["costs_paid"] + s["financing_paid"]
        print("  %-20s total $%-9.2f (real $%-8.2f + unreal $%-8.2f) open %d closed %d win%% %s"
              % (name, s["total_pnl"], s["realized_pnl"], s["unrealized"],
                 s["open"], s["closed"], s["win_rate"]))
        print("  %-20s costs paid $%.2f  financing $%.2f  symbols %s can't trade: %d"
              % ("", s["costs_paid"], s["financing_paid"],
                 venue_costs.active()["label"], desk.get("rejected_not_listed", 0)))
        for sym, p in desk["positions"].items():
            print("       %-5s %-10s @ %-12s net uPnL $%-9s (gross $%s, financing -$%s)"
                  % (p["side"], sym, round(p["entry"], 6), p.get("upnl", 0),
                     p.get("gross_upnl", 0), p.get("accrued_financing", 0)))
    print()
    print("  ----  REPLICA combined P&L (real+unreal, net of all %s costs): $%.2f"
          % (venue_costs.active()["label"], tot))
    print("  ----  Total %s costs paid so far: $%.2f"
          % (venue_costs.active()["label"], tot_costs))
    br = d.get("book_risk") or {}
    if br:
        print("  ----  BOOK RISK: equity $%s · peak $%s · drawdown %s%% · kill %s%% · %s"
              % (br.get("equity"), br.get("peak_equity"), br.get("drawdown_pct"),
                 br.get("kill_drawdown_pct"),
                 ("FROZEN -- %s (exits still run)" % br.get("frozen_reason"))
                 if br.get("frozen") else "active"))
        if br.get("error"):
            print("  ----  !! BOOK RISK UNMEASURED: %s" % br["error"])

    cov = d.get("coverage") or {}
    if cov:
        lq, mc = cov.get("liquid", {}), cov.get("micro", {})
        print()
        print("  UNIVERSE COVERAGE")
        print("    liquid : %s of %s Kraken symbols exist at the venue"
              % (lq.get("venue"), lq.get("kraken")))
        print("    micro  : %s of %s Kraken symbols exist at the venue"
              % (mc.get("venue"), mc.get("kraken")))


def coverage_report():
    print(venue_universe.verification_notice())
    print()
    for label, syms in (("LIQUID", kraken_liquid_universe()),
                        ("MICRO-CAP", kraken_micro_universe())):
        c = venue_universe.coverage(syms)
        print("%s: %d of %d tradeable on eToro (%.1f%%)"
              % (label, c["tradeable"], c["total"], c["coverage_pct"]))
        for sym, why in sorted(c["missing_symbols"].items()):
            print("    blocked  %-14s %s" % (sym, why))
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--exits-only", action="store_true")
    args = ap.parse_args()
    if args.coverage:
        coverage_report()
        return
    if args.status:
        status(load())
        return
    d, events = tick(exits_only=args.exits_only)
    for e in events:
        print("  ", e)
    if not events:
        print("   (no entries/exits this tick)")
    print()
    status(d)
    print("\n  ledger ->", LEDGER)


if __name__ == "__main__":
    main()
