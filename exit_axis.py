#!/usr/bin/env python3
"""Exit axis — sigma-scaled take-profit / trailing-stop rule, shared by lab and live.

THE HYPOTHESIS UNDER TEST IS THE EXIT, NOT THE ENTRY. The desk's existing
fixed-holding behaviour is the NULL and is preserved exactly: this module ships
DISABLED (reports/exit_axis_config.json, enabled:false, enabled_books:[]) and
enabling it for any pre-registered book/sleeve is a RULE CHANGE requiring a
funder-signed amendment with its own registration (Carry v2 precedent,
2026-08-10). The live call sites below exist so that the rule measured in the
lab is literally the rule the tick would run — never a lab-only cousin.

THE RULE (thresholds in SIGMA, never percent; decisions on DAILY CLOSES only,
at most one evaluation per bar date; the exit is booked at the close of the
triggering session — never the threshold price, never the peak):

  take_profit    (close/entry_px - 1) >= tp_sigma * entry_sigma * sqrt(bars_held)
                 z-score of the cumulative move; sqrt-time scaling keeps the
                 threshold a constant surprise level per instrument.
  trailing_stop  (close/peak_close - 1) <= -(trail_sigma * entry_sigma)
                 daily-scale pullback from the highest CLOSE since entry
                 (peak of closes, never intraday highs, never live marks).

  entry_sigma is FROZEN at entry: std of daily simple returns over the trailing
  ~60 daily closes (61 closes -> 60 returns; minimum 25 returns), computed from
  the SAME settled daily-close series the lab uses — yfinance daily bars for
  the ETF books, Kraken 1440-minute bars for the crypto sleeves. Never from
  intraday marks. With insufficient history sigma is None and the position is
  EXIT-EXEMPT (loudly noted in state) until re-entry with enough history.

PRICE-BASIS DECLARATIONS (trap 5 of the build order, stated once, honoured
everywhere):
  * sigma, peak_close and the deciding close all come from SETTLED daily
    closes of the same source and adjustment basis per book:
      - tournament ETF sleeves + growth_book: yfinance interval="1d",
        auto_adjust=True (identical to the modules' own fetches). The latest
        yfinance daily row is dropped when its date equals the current UTC
        date, because during a live session it is an in-progress bar.
      - tournament crypto sleeves: Kraken public OHLC 1440-min bars via
        kraken_backtest.fetch_ohlc; the last bar is ALWAYS dropped (it is the
        in-progress UTC day).
      - value_book: LOCAL-currency yfinance daily closes (auto_adjust=True),
        matching entry_px / mark_px_local. The basis is LOCAL by declaration:
        a USD-basis peak would entangle FX moves with the price signal.
  * entry_px in the ratio close/entry_px is the book's actual recorded fill
    (s["entry_px"] / b["entry_px"]), i.e. the true position return — declared,
    since that fill may sit on a live mark rather than the settled close of
    the entry date. sigma remains a settled-close quantity regardless.

An "entry" is each scheduled rebalance's position-set: the live modules
rewrite entry_px for kept names on every rebalance, so sigma/peak/bars_held
reset on the same event. Documented, and mirrored exactly by the lab.

ALWAYS-ON BOOKKEEPING (bookkeeping only — not a rule change): even while the
config is disabled, every eligible book's hook freezes entry_sigma per symbol
at entry (from the module's own settled daily-close source), tracks peak_close
per symbol from settled DAILY closes only (never the intraday mark), and
stamps peak_tracking_since (UTC), so a later enablement is honest — the state
a live rule would need has been accruing all along. Exit DECISIONS run only
when the config is enabled AND the book is in enabled_books. Books not in the
allowlist get neither phase — decisions are structurally impossible for them.

SHADOW MODE (enabled: "shadow" — a live FORWARD test, added 2026-08-20 at the
funder's request; the backtest verdict was NO and stands): the rule is
evaluated on every tracked live position exactly as the real path would —
same settled daily closes, same entry-frozen sigma, same once-per-bar-date
rule, same FX refusals — but instead of trading, an event is appended to
state["exit_axis"]["shadow_events"] and the per-position shadow leg goes FLAT
(frozen at hypothetical net proceeds) until the strategy's next ACTUAL
rebalance replaces the position, which re-syncs shadow to actual and books a
completed shadow-vs-actual cycle. Shadow mode is STRUCTURALLY unable to
trade: _shadow_tick receives no state dict, no set_holdings, and only a COPY
of holdings; it writes solely inside the "exit_axis" subtree. Enabling REAL
exits (enabled: true) remains a rule change requiring a funder-signed
amendment. `python3 exit_axis.py --shadow-report` prints shadow-vs-actual
per book (completed cycles only enter the settled totals; everything else is
labelled incomplete/provisional — zero fabrication).

FAILURE PATHS ARE LOUD: every live hook is wrapped so an error prints
"EXIT-AXIS ERROR: ..." to stdout (desk_tick.log captures it), sets an
exit_axis_error field in the state JSON, and CONTINUES — a book tick is never
crashed by this axis, and no exception is ever swallowed silently.

PAPER ONLY. No broker, no credentials, no live order path.

Usage:
    python3 exit_axis.py --selftest    # causality proof (must exit 0)
"""
import json
import math
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "reports", "exit_axis_config.json")

# All new timestamps in this module are epoch time.time() / UTC via
# time.gmtime — the host clock is Asia/Makassar and naive local datetimes on
# this machine have already burned one monitor. Existing fields elsewhere are
# left exactly as they are.


def _utc_now_str():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_today():
    return time.strftime("%Y-%m-%d", time.gmtime())


# --------------------------------------------------------------------------
# The rule itself (pure functions; the lab imports these, the live hooks call
# these — one implementation, two callers)
# --------------------------------------------------------------------------
SIGMA_LOOKBACK_CLOSES = 61      # 61 closes -> 60 daily returns ("trailing ~60")
SIGMA_MIN_RETURNS = 25


def compute_entry_sigma(daily_closes, lookback_closes=SIGMA_LOOKBACK_CLOSES,
                        min_returns=SIGMA_MIN_RETURNS):
    """Frozen-at-entry sigma: std (ddof=1) of daily simple returns over the
    trailing ~60 daily closes ending at the entry bar.

    `daily_closes` must be SETTLED daily closes, oldest first, ending at the
    entry bar. Returns None when fewer than `min_returns` returns exist — the
    position is then exit-exempt until an entry with enough history (callers
    must note that loudly in state).
    """
    c = [float(x) for x in daily_closes
         if x is not None and isinstance(x, (int, float))
         and math.isfinite(float(x)) and float(x) > 0]
    c = c[-int(lookback_closes):]
    if len(c) < min_returns + 1:
        return None
    rets = [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sigma = math.sqrt(var)
    return sigma if sigma > 0 else None


def decide_exit(entry_px, entry_sigma, bars_held, peak_close, close,
                tp_sigma, trail_sigma):
    """None | "take_profit" | "trailing_stop", from information knowable at the
    given daily close only.

    * entry_sigma None -> None (exit-exempt: insufficient history at entry).
    * bars_held < 1 -> None (a position cannot exit on its own entry bar; it
      also keeps sqrt(bars_held) meaningful).
    * tp_sigma / trail_sigma of None disables that leg. Thresholds are in
      SIGMA, never percent.
    * take_profit is checked before trailing_stop when both would fire on the
      same close (fixed, documented ordering).
    The caller books the exit at `close` — the close of the triggering
    session. This function never sees, and can never return, a peak price or
    a threshold price to fill at.
    """
    if entry_sigma is None:
        return None
    try:
        entry_sigma = float(entry_sigma)
        entry_px = float(entry_px)
        peak_close = float(peak_close)
        close = float(close)
        bars_held = int(bars_held)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(entry_sigma) or entry_sigma <= 0
            or entry_px <= 0 or peak_close <= 0 or close <= 0):
        return None
    if bars_held < 1:
        return None
    if tp_sigma is not None:
        if (close / entry_px - 1.0) >= float(tp_sigma) * entry_sigma * math.sqrt(bars_held):
            return "take_profit"
    if trail_sigma is not None:
        if (close / peak_close - 1.0) <= -(float(trail_sigma) * entry_sigma):
            return "trailing_stop"
    return None


# --------------------------------------------------------------------------
# Config — missing file or enabled:false => the axis is OFF and every live
# hook is a no-op (no fetches, no state written, no behaviour change).
# --------------------------------------------------------------------------
_DISABLED = {"enabled": False, "tp_sigma": None, "trail_sigma": None,
             "enabled_books": []}


def load_config(path=None):
    """Read reports/exit_axis_config.json. Missing => disabled (normal state).
    A file that exists but cannot be parsed is a LOUD disabled: the returned
    dict carries config_error for the hooks to surface."""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        return dict(_DISABLED)
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception as e:
        out = dict(_DISABLED)
        out["config_error"] = "exit_axis_config.json unreadable: %s" % e
        return out
    out = dict(_DISABLED)
    out.update({k: cfg.get(k, out.get(k)) for k in
                ("enabled", "tp_sigma", "trail_sigma", "enabled_books")})
    en = out.get("enabled", False)
    if en == "shadow":
        out["enabled"] = "shadow"      # evaluate + record, execute NOTHING
    elif en is True or en is False:
        out["enabled"] = en
    else:
        # Any other value must never arm anything: a typo'd string is truthy,
        # and bool()-ing it would have enabled REAL exits. Loud disabled.
        out["config_error"] = ("exit_axis_config.json enabled=%r is not one "
                               "of false/true/\"shadow\" — treating as "
                               "disabled" % (en,))
        out["enabled"] = False
    out["enabled_books"] = list(out.get("enabled_books") or [])
    return out


# --------------------------------------------------------------------------
# LIVE WIRING — helpers called from the tick paths of tournament.py,
# growth_book.py and value_book.py. Each returns an action string (or None)
# and NEVER raises: any failure prints "EXIT-AXIS ERROR: ..." and sets
# state["exit_axis_error"], then returns None so the book tick continues.
#
# Eligibility is a closed allowlist. Anything not in the table is excluded by
# construction — controls (equal_weight_all + the 4 buy-and-hold probes: an
# exit destroys the benchmark), covered_call_etf (short option legs off the
# set_holdings path), cef_discount and any future auto-probe (dollar-neutral
# long/short: a single-leg long-only exit breaks neutrality and the sealed
# spec), claude_discretionary (registration: the tick never trades), and the
# carry books (funding-based, frozen exit rules, do-not-touch).
# --------------------------------------------------------------------------
SLEEVE_EXIT_TABLE = {
    # sleeve            price source   cost bps (matches the live call-site literal)
    "lowvol_crypto":    {"src": "kraken", "cost_bps": 30.0,
                         "note": "cleanest candidate; weekly EW-5 crypto"},
    "fng_momentum":     {"src": "kraken", "cost_bps": 30.0,
                         "note": "OVERLAP: own F&G<55 exit; its rule re-enters "
                                 "on the next hourly tick while F&G>=55"},
    "spy_trend_cash":   {"src": "yf", "cost_bps": 10.0,
                         "note": "OVERLAP: 200dma rule already exits to cash "
                                 "at its monthly check (mildest overlap)"},
    "vix_regime_switch": {"src": "yf", "cost_bps": 10.0,
                          "note": "OVERLAP: rule IS a switch; after a price "
                                  "exit it re-buys the wanted leg on the very "
                                  "next hourly tick"},
}

_EVENT_CAP = 50


def _err(state, book, msg):
    """The loud failure path: stdout line (desk_tick.log captures it) + state
    field. Never raises."""
    line = "EXIT-AXIS ERROR: %s: %s" % (book, msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        state["exit_axis_error"] = {"msg": line, "at_utc": _utc_now_str(),
                                    "at_epoch": time.time()}
    except Exception:
        pass
    return None


def _settled_yf_daily(symbols, period="1y"):
    """{sym: (dates list 'YYYY-MM-DD', closes list)} of SETTLED yfinance daily
    closes, auto_adjust=True (the ETF books' own basis). The final row is
    dropped when its date equals the current UTC date — during a live session
    it is an in-progress bar, and a settled-close rule must never see it."""
    import yfinance as yf
    raw = yf.download(list(symbols), period=period, interval="1d",
                      auto_adjust=True, progress=False, threads=True)["Close"]
    if not hasattr(raw, "columns"):        # single symbol -> Series
        raw = raw.to_frame(list(symbols)[0])
    today = _utc_today()
    out = {}
    for sym in symbols:
        if sym not in raw.columns:
            continue
        col = raw[sym].dropna()
        dates = [d.strftime("%Y-%m-%d") for d in col.index]
        closes = [float(x) for x in col.values]
        if dates and dates[-1] == today:
            dates, closes = dates[:-1], closes[:-1]
        if dates:
            out[sym] = (dates, closes)
    return out


def _settled_kraken_daily(symbols, n=120):
    """{sym: (dates, closes)} from Kraken public 1440-min OHLC. The last bar is
    ALWAYS the in-progress UTC day and is ALWAYS dropped. Dates are UTC."""
    sys.path.insert(0, "/Users/test/Applications/KrakenQuantDesk")
    from kraken_backtest import fetch_ohlc, sym_to_pair
    out = {}
    for sym in symbols:
        bars = fetch_ohlc(sym_to_pair(sym), 1440, n)
        if not bars or len(bars) < 2:
            continue
        bars = bars[:-1]                   # drop in-progress UTC day
        dates = [time.strftime("%Y-%m-%d", time.gmtime(float(b[0])))
                 for b in bars]
        closes = [float(b[4]) for b in bars]
        out[sym] = (dates, closes)
        time.sleep(0.2)
    return out


def _init_position_state(sym, entry_px, entry_date, dates, closes):
    """Freeze sigma at entry and reconstruct peak/bars from settled closes.
    Uses ONLY closes with date <= entry_date for sigma (knowable at entry) and
    closes with date >= entry_date for the peak (closes, never highs)."""
    upto = [c for d, c in zip(dates, closes) if d <= entry_date]
    since = [c for d, c in zip(dates, closes) if d >= entry_date]
    after = [c for d, c in zip(dates, closes) if d > entry_date]
    sigma = compute_entry_sigma(upto)
    st = {"entry_px": float(entry_px), "entry_date": entry_date,
          "entry_sigma": sigma,
          "peak_close": max(since) if since else float(entry_px),
          "bars_held": len(after),
          "last_bar_date": dates[-1] if dates else entry_date,
          "frozen_at_utc": _utc_now_str(),
          "peak_tracking_since": _utc_now_str()}
    if sigma is None:
        st["sigma_note"] = ("EXIT-EXEMPT: <%d settled daily returns before "
                            "entry — sigma unavailable" % SIGMA_MIN_RETURNS)
    return st


def _advance_position_state(st, dates, closes):
    """Roll the incremental peak/bars state forward over newly settled bars.
    Peak is a max of CLOSES only; sigma stays frozen."""
    last = st.get("last_bar_date", st["entry_date"])
    new = [(d, c) for d, c in zip(dates, closes) if d > last]
    for d, c in new:
        st["bars_held"] = int(st.get("bars_held", 0)) + 1
        st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
        st["last_bar_date"] = d
    return bool(new)


def _refresh_mirrors(xa):
    """bookkeeping only (not a rule change): flat per-symbol mirrors of the
    tracked state, kept in sync with xa['positions'] for easy monitoring."""
    pos = xa.get("positions", {})
    xa["entry_sigma"] = {t: st.get("entry_sigma") for t, st in pos.items()}
    xa["peak_close"] = {t: st.get("peak_close") for t, st in pos.items()}
    xa["peak_tracking_since"] = {t: st.get("peak_tracking_since")
                                 for t, st in pos.items()}


# --------------------------------------------------------------------------
# SHADOW MODE — live forward test on the real books. Evaluates the identical
# rule the real path would run and RECORDS what it would have done; executes
# NOTHING, ever. Structural guarantees: this function receives no book state
# dict, no set_holdings, and only a COPY of holdings; the only structure it
# writes is xa — the "exit_axis" bookkeeping subtree of the state JSON.
# --------------------------------------------------------------------------
_SHADOW_EVENT_CAP = 400

# the six enrolled state files, keyed by the exact names the hooks match on
SHADOW_STATE_PATHS = [
    ("spy_trend_cash", os.path.join(BASE, "reports", "tournament", "spy_trend_cash.json")),
    ("vix_regime_switch", os.path.join(BASE, "reports", "tournament", "vix_regime_switch.json")),
    ("lowvol_crypto", os.path.join(BASE, "reports", "tournament", "lowvol_crypto.json")),
    ("fng_momentum", os.path.join(BASE, "reports", "tournament", "fng_momentum.json")),
    ("growth_book", os.path.join(BASE, "reports", "growth_book.json")),
    ("value_book", os.path.join(BASE, "reports", "value_book.json")),
]


def _shadow_tick(xa, book, positions, holdings, series, cfg, cost_bps,
                 err, rates=None, refused=None, decidable=None):
    """One shadow evaluation for one book. Called from the hooks in place of
    the real phase-2 when mode == "shadow"; the same gates (enabled_books,
    once-per-bar-date, phase-1 bookkeeping) have already been applied by the
    caller, so the decision inputs are byte-for-byte what the real path would
    have seen.

    positions: xa["positions"] (phase-1 bookkeeping, already refreshed)
    holdings:  a COPY of the book's {sym: units} — read only
    series:    {sym: (dates, closes)} settled daily closes (LOCAL for
               value_book, matching entry_px — the declared basis)
    rates:     value_book only — {sym: usd_per_unit} for this tick
    refused:   value_book only — {sym: reason} names whose FX is refused;
               a triggered shadow exit on a refused name is NOT booked,
               mirroring the real path's refusal exactly
    decidable: syms allowed a decision this tick (the caller's once-per-bar
               gate); legs sync/complete regardless.

    Cycle accounting (for --shadow-report): when the strategy's next ACTUAL
    rebalance replaces or drops a leg, a cycle is appended to
    xa["shadow"]["cycles"]. Both sides are charged cost_bps once at their own
    settled close: shadow at its trigger close (already inside
    hypothetical_proceeds_net), actual at the last settled close observed
    before replacement. A leg the shadow never exited is shadow==actual by
    construction (delta 0). Cycles missing units/close/FX data are recorded
    complete:false and never enter the totals — zero fabrication.

    Returns a message string or None. Never trades; never touches holdings
    or cash; writes only inside xa.
    """
    now = _utc_now_str()
    holdings = {t: (None if u is None else float(u))
                for t, u in dict(holdings or {}).items()}
    params = {"tp_sigma": cfg.get("tp_sigma"),
              "trail_sigma": cfg.get("trail_sigma")}
    sh = xa.setdefault("shadow", {})
    if sh.get("params") != params:
        if sh.get("params") is not None:
            sh.setdefault("param_changes", []).append(
                {"from": sh.get("params"), "to": params, "at_utc": now,
                 "note": "parameter change RESTARTS the forward-test clock "
                         "(SHADOW_FORWARD_TEST.md)"})
        sh["params"] = params
        sh["since_utc"] = now
    sh["cost_bps"] = float(cost_bps)
    legs = sh.setdefault("legs", {})
    cycles = sh.setdefault("cycles", [])
    haircut = 1.0 - float(cost_bps) / 10000.0
    msgs = []

    def _same_actual_leg(leg, pos):
        return (pos is not None
                and leg.get("entry_date") == pos.get("entry_date")
                and float(leg.get("entry_px") or 0) == float(pos.get("entry_px") or 0))

    # ---- 1) the actual book replaced/dropped a leg -> complete its cycle ----
    for sym in list(legs):
        leg = legs[sym]
        if _same_actual_leg(leg, positions.get(sym)):
            continue
        u = leg.get("units")
        c = leg.get("last_close_seen")
        rate = leg.get("last_rate_seen") if rates is not None else None
        cyc = {"book": book, "symbol": sym,
               "entry_date": leg.get("entry_date"),
               "entry_px": leg.get("entry_px"), "units": u,
               "cost_bps": float(cost_bps),
               "fired": bool(leg.get("exit")),
               "reason": (leg.get("exit") or {}).get("reason"),
               "shadow_exit": leg.get("exit"),
               "actual_final_close": c,
               "actual_final_close_date": leg.get("last_close_date"),
               "completed_utc": now, "completed_epoch": time.time()}
        if rates is not None:
            cyc["fx_usd_per_unit_final"] = rate
        if (u is None or c is None
                or (rates is not None and (not rate or rate <= 0))):
            cyc["complete"] = False
            cyc["note"] = ("incomplete: missing %s data — excluded from "
                           "totals" % "/".join(
                               k for k, bad in (("units", u is None),
                                                ("close", c is None),
                                                ("fx", rates is not None and (not rate or rate <= 0)))
                               if bad))
        else:
            actual_net = float(u) * float(c) * (float(rate) if rate else 1.0) * haircut
            shadow_net = (float(leg["exit"]["hypothetical_proceeds_net"])
                          if leg.get("exit") else actual_net)
            cyc["complete"] = True
            cyc["shadow_net"] = round(shadow_net, 6)
            cyc["actual_net"] = round(actual_net, 6)
            cyc["delta_net"] = round(shadow_net - actual_net, 6)
            cyc["basis"] = ("both sides charged cost_bps once at their own "
                            "settled close; actual side marked at the last "
                            "settled close observed before replacement")
        cycles.append(cyc)
        del legs[sym]
        msgs.append("SHADOW cycle closed %s (%s%s)"
                    % (sym, "fired" if cyc["fired"] else "no exit",
                       (", delta %+.2f" % cyc["delta_net"])
                       if cyc.get("complete") else ", incomplete"))

    # ---- 2) new actual positions -> shadow re-syncs to actual ----
    for sym, pos in positions.items():
        if sym in legs:
            continue
        legs[sym] = {"entry_date": pos.get("entry_date"),
                     "entry_px": pos.get("entry_px"),
                     "entry_sigma": pos.get("entry_sigma"),
                     "units": holdings.get(sym),
                     "cost_bps": float(cost_bps),
                     "status": "in_sync", "created_utc": now}
        if holdings.get(sym) is None:
            legs[sym]["note"] = ("units unknown at leg creation — cycle will "
                                 "be recorded incomplete")

    # ---- 3) advance observations (settled closes / FX actually seen) ----
    for sym, leg in legs.items():
        if sym in series and series[sym][1]:
            leg["last_close_seen"] = float(series[sym][1][-1])
            leg["last_close_date"] = series[sym][0][-1]
        if rates is not None:
            r = (rates or {}).get(sym)
            if r and float(r) > 0:
                leg["last_rate_seen"] = float(r)

    # ---- 4) decisions — identical inputs to the real path, zero execution ----
    for sym, leg in legs.items():
        if leg.get("status") != "in_sync":
            continue                    # shadow already flat until re-sync
        if decidable is not None and sym not in decidable:
            continue                    # same once-per-bar-date rule as real
        pos = positions.get(sym)
        if pos is None or sym not in series or not series[sym][1]:
            continue
        dates, closes = series[sym]
        reason = decide_exit(pos["entry_px"], pos["entry_sigma"],
                             pos["bars_held"], pos["peak_close"], closes[-1],
                             cfg.get("tp_sigma"), cfg.get("trail_sigma"))
        if not reason:
            continue
        u = leg.get("units")
        if u is None:
            err("%s shadow %s triggered but units are unknown — cannot book "
                "the hypothetical" % (sym, reason))
            continue
        rate = None
        if rates is not None:           # value_book: mirror the FX refusals
            if sym in (refused or {}):
                err("%s shadow %s triggered but FX/price is refused (%s) — "
                    "mirroring the real path: NOT booking at a wrong FX scale"
                    % (sym, reason, (refused or {}).get(sym)))
                continue
            rate = (rates or {}).get(sym)
            if not rate or float(rate) <= 0:
                err("%s shadow %s triggered but no USD/unit rate this tick — "
                    "mirroring the real path: NOT booking" % (sym, reason))
                continue
        trig = float(closes[-1])
        gross = float(u) * trig * (float(rate) if rate else 1.0)
        cost = gross * float(cost_bps) / 10000.0
        net = gross - cost
        ev = {"utc": now, "epoch": time.time(), "book": book, "symbol": sym,
              "reason": reason, "bar_date": dates[-1], "trigger_close": trig,
              "units": float(u), "hypothetical_proceeds_net": round(net, 6),
              "cost_bps": float(cost_bps), "cost": round(cost, 6),
              "entry_px": pos.get("entry_px"),
              "entry_sigma": pos.get("entry_sigma"),
              "bars_held": pos.get("bars_held"),
              "mode": "shadow", "executed": False}
        if rate:
            ev["fx_usd_per_unit"] = float(rate)
            ev["basis"] = "LOCAL settled close x this tick's USD rate"
        xa.setdefault("shadow_events", []).append(ev)
        xa["shadow_events"] = xa["shadow_events"][-_SHADOW_EVENT_CAP:]
        leg["status"] = "shadow_flat"
        leg["exit"] = {"reason": reason, "bar_date": dates[-1],
                       "trigger_close": trig, "fx_usd_per_unit": rate,
                       "hypothetical_proceeds_net": round(net, 6),
                       "cost": round(cost, 6), "at_utc": now}
        line = ("EXIT-AXIS SHADOW %s: %s %s at settled close %.6g (%s) — "
                "NO TRADE (hypothetical net %.2f, cost %.2f at %.0f bps)"
                % (book, reason, sym, trig, dates[-1], net, cost,
                   float(cost_bps)))
        try:
            print(line)
        except Exception:
            pass
        msgs.append("SHADOW %s %s at %.6g (%s) — no trade"
                    % (reason, sym, trig, dates[-1]))

    sh["last_eval_utc"] = now
    return "; ".join(msgs) if msgs else None


def _fmt_money(x):
    return ("%+.2f" % x) if isinstance(x, (int, float)) else "?"


def shadow_report():
    """--shadow-report: per book, completed shadow cycles, shadow vs actual
    net value on the same legs over the same window, and the running delta.
    Only legs with complete data enter the settled totals; everything else is
    printed and labelled incomplete/provisional. Read-only."""
    cfg = load_config()
    print("=" * 74)
    print("EXIT-AXIS SHADOW REPORT  [%s]  (read-only; shadow executes nothing)"
          % _utc_now_str())
    print("  config: enabled=%r tp_sigma=%s trail_sigma=%s books=%s"
          % (cfg.get("enabled"), cfg.get("tp_sigma"), cfg.get("trail_sigma"),
             ",".join(cfg.get("enabled_books", [])) or "-"))
    print("=" * 74)
    tot_completed_delta, tot_prov_delta = 0.0, 0.0
    tot_cycles = tot_fired = tot_incomplete = 0
    for book, path in SHADOW_STATE_PATHS:
        print("\n%s  (%s)" % (book, path))
        try:
            with open(path) as f:
                state = json.load(f)
        except Exception as e:
            print("  state unreadable: %s — incomplete" % e)
            continue
        xa = state.get("exit_axis") or {}
        sh = xa.get("shadow") or {}
        if not sh:
            print("  no shadow state yet (no shadow evaluation has run for "
                  "this book) — incomplete")
            continue
        print("  shadow since %s  params tp=%s trail=%s  cost_bps=%s"
              % (sh.get("since_utc"), (sh.get("params") or {}).get("tp_sigma"),
                 (sh.get("params") or {}).get("trail_sigma"),
                 sh.get("cost_bps")))
        for pc in sh.get("param_changes") or []:
            print("  ! params changed %s -> %s at %s (clock restarted)"
                  % (pc.get("from"), pc.get("to"), pc.get("at_utc")))
        cycles = sh.get("cycles") or []
        comp = [c for c in cycles if c.get("complete")]
        inc = [c for c in cycles if not c.get("complete")]
        fired = [c for c in comp if c.get("fired")]
        s_net = sum(c["shadow_net"] for c in comp)
        a_net = sum(c["actual_net"] for c in comp)
        delta = s_net - a_net
        print("  completed cycles: %d (%d with a shadow exit) | shadow net "
              "%.2f vs actual net %.2f on the same legs | delta %s"
              % (len(comp), len(fired), s_net, a_net, _fmt_money(delta)))
        for c in fired:
            print("    %s %s %s: shadow exit %s @ %.6g -> net %.2f | actual "
                  "held to %s @ %.6g -> net %.2f | delta %s"
                  % (c.get("entry_date"), c["symbol"], c.get("reason"),
                     (c.get("shadow_exit") or {}).get("bar_date"),
                     (c.get("shadow_exit") or {}).get("trigger_close") or 0,
                     c["shadow_net"], c.get("actual_final_close_date"),
                     c.get("actual_final_close") or 0, c["actual_net"],
                     _fmt_money(c["delta_net"])))
        for c in inc:
            print("    incomplete cycle %s (entered %s): %s"
                  % (c["symbol"], c.get("entry_date"),
                     c.get("note", "missing data")))
        prov = 0.0
        prov_ok = True
        legs = sh.get("legs") or {}
        for sym, leg in sorted(legs.items()):
            if leg.get("status") == "in_sync":
                print("    open leg %s: in sync — shadow == actual, delta 0 "
                      "so far [incomplete: open cycle]" % sym)
                continue
            u, c0 = leg.get("units"), leg.get("last_close_seen")
            rate = leg.get("last_rate_seen")
            bps = leg.get("cost_bps", sh.get("cost_bps") or 0.0)
            ex = leg.get("exit") or {}
            needs_fx = ex.get("fx_usd_per_unit") is not None
            if (u is None or c0 is None or (needs_fx and not rate)
                    or ex.get("hypothetical_proceeds_net") is None):
                print("    open leg %s: SHADOW-FLAT since %s but data "
                      "incomplete (units/close/fx) — no provisional delta"
                      % (sym, ex.get("bar_date")))
                prov_ok = False
                continue
            actual_mark = (float(u) * float(c0) * (float(rate) if needs_fx else 1.0)
                           * (1.0 - float(bps) / 10000.0))
            d = float(ex["hypothetical_proceeds_net"]) - actual_mark
            prov += d
            print("    open leg %s: SHADOW-FLAT at net %.2f (%s %s) vs actual "
                  "marked %.2f at settled close %s -> provisional delta %s "
                  "[incomplete: open cycle]"
                  % (sym, ex["hypothetical_proceeds_net"], ex.get("reason"),
                     ex.get("bar_date"), actual_mark,
                     leg.get("last_close_date"), _fmt_money(d)))
        print("  running delta: completed %s%s"
              % (_fmt_money(delta),
                 (" + open provisional %s = %s" % (_fmt_money(prov),
                                                   _fmt_money(delta + prov)))
                 if legs and prov_ok else
                 (" (open provisional unavailable — incomplete legs)"
                  if not prov_ok else "")))
        tot_completed_delta += delta
        tot_prov_delta += prov
        tot_cycles += len(comp)
        tot_fired += len(fired)
        tot_incomplete += len(inc)
    print("\n" + "-" * 74)
    print("ALL BOOKS: %d completed cycles (%d fired, %d incomplete excluded) "
          "| completed delta %s | open provisional %s"
          % (tot_cycles, tot_fired, tot_incomplete,
             _fmt_money(tot_completed_delta), _fmt_money(tot_prov_delta)))
    print("Judgement waits for the pre-registered window "
          "(reports/SHADOW_FORWARD_TEST.md): 60+ days, net delta on completed "
          "cycles. This report can only ever produce evidence, not trades.")
    return 0


def tournament_exit_check(s, set_holdings_fn, cfg=None, fetchers=None):
    """LIVE hook for tournament.py tick(): called after `act = fn(s)`, before
    save_sleeve(s). Returns an action string or None. Never raises.

    TWO PHASES:
      1. bookkeeping only (not a rule change) — ALWAYS ON, even while the
         config is disabled, so a later enablement is honest: freezes
         entry_sigma per symbol from the trailing settled daily closes of the
         sleeve's own price source, tracks peak_close from settled DAILY
         closes (never the intraday mark), stamps peak_tracking_since (UTC).
         At most one refresh per UTC day (settled bars change daily).
      2. exit decisions — ONLY when the config is enabled AND this sleeve is
         in enabled_books. Closes ONE symbol through the sleeve's existing
         cost-charged path (set_holdings) at the settled daily close that
         triggered — kept names are re-targeted at their current weights so
         want==have and only the exited notional trades. PRECONDITION
         enforced first: the px dict must carry a positive settled close for
         EVERY current holding, else set_holdings would silently flatten
         missing names at entry_px with zero cost — we refuse loudly instead.

    Sleeves not in SLEEVE_EXIT_TABLE get neither phase — decisions are
    structurally impossible for the excluded books (controls, covered_call,
    dollar-neutral probes, discretionary, carry).
    """
    name = s.get("name", "?")
    try:
        cfg = cfg if cfg is not None else load_config()
        if cfg.get("config_error"):
            _err(s, "sleeve %s" % name, cfg["config_error"])
            cfg = dict(_DISABLED)      # loud, then behave as disabled
        if name not in SLEEVE_EXIT_TABLE:
            if cfg.get("enabled") and name in cfg.get("enabled_books", []):
                # enabled_books asked for an excluded sleeve: loud no.
                return _err(s, "sleeve %s" % name,
                            "enabled in config but NOT in SLEEVE_EXIT_TABLE "
                            "(excluded by design) — refusing")
            return None
        if s.get("frozen"):
            return None
        xa = s.setdefault("exit_axis", {})
        positions = xa.setdefault("positions", {})
        held = list(s.get("holdings") or {})
        # drop state for names no longer held (the sleeve's own rule exited)
        for gone in [t for t in positions if t not in held]:
            positions.pop(gone, None)
        if not held:
            _refresh_mirrors(xa)
            return None
        # one refresh/evaluation per UTC day: settled bars change at most daily
        if xa.get("last_eval_utc") == _utc_today():
            return None
        tab = SLEEVE_EXIT_TABLE[name]
        fetch = (fetchers or {}).get(tab["src"]) or (
            _settled_kraken_daily if tab["src"] == "kraken" else _settled_yf_daily)
        series = fetch(held)
        missing = [t for t in held
                   if t not in series or not series[t][1] or series[t][1][-1] <= 0]
        if missing:
            return _err(s, "sleeve %s" % name,
                        "no positive settled daily close for %s — refusing to "
                        "evaluate (set_holdings would silently flatten a "
                        "holding missing from px)" % ", ".join(missing))
        xa["last_eval_utc"] = _utc_today()

        # ---- phase 1: bookkeeping only (not a rule change) — always on ----
        entry_date = s.get("last_rebalance") or ""
        tracked = []
        for sym in held:
            dates, closes = series[sym]
            e_px = s.get("entry_px", {}).get(sym)
            if e_px is None or float(e_px) <= 0:
                _err(s, "sleeve %s" % name,
                     "%s has no recorded entry_px — exit-exempt" % sym)
                positions.pop(sym, None)
                continue
            st = positions.get(sym)
            if (st is None or st.get("entry_date") != entry_date
                    or float(st.get("entry_px") or 0) != float(e_px)):
                st = _init_position_state(sym, e_px, entry_date, dates, closes)
                positions[sym] = st
            else:
                _advance_position_state(st, dates, closes)
            tracked.append(sym)
        _refresh_mirrors(xa)

        # ---- phase 2: exit decisions — config-gated ----
        mode = cfg.get("enabled")
        if not mode or name not in cfg.get("enabled_books", []):
            return None
        if mode == "shadow":
            # SHADOW: identical evaluation, zero execution. No state dict,
            # no set_holdings, a COPY of holdings; writes only inside xa.
            return _shadow_tick(
                xa, name, positions, dict(s.get("holdings") or {}),
                {t: series[t] for t in tracked}, cfg, tab["cost_bps"],
                err=lambda m: _err(s, "sleeve %s" % name, m),
                decidable=set(tracked))
        if mode is not True:
            return _err(s, "sleeve %s" % name,
                        "unrecognized exit-axis mode %r — refusing" % (mode,))
        exits = []
        for sym in tracked:
            st = positions[sym]
            dates, closes = series[sym]
            reason = decide_exit(st["entry_px"], st["entry_sigma"],
                                 st["bars_held"], st["peak_close"],
                                 closes[-1], cfg.get("tp_sigma"),
                                 cfg.get("trail_sigma"))
            if reason:
                exits.append((sym, reason, dates[-1], closes[-1]))

        if not exits:
            return None
        # book each exit at the CLOSE of the triggering session through the
        # sleeve's own cost path, one at a time, re-verifying px coverage
        msgs = []
        for sym, reason, bar_date, trig_close in exits:
            if sym not in s["holdings"]:
                continue
            px = {t: series[t][1][-1] for t in s["holdings"]}
            if any(t not in px or px[t] <= 0 for t in s["holdings"]):
                return _err(s, "sleeve %s" % name,
                            "px coverage lost mid-close — refusing")
            eq = s["cash"] + sum(u * px[t] for t, u in s["holdings"].items())
            if eq <= 0:
                return _err(s, "sleeve %s" % name, "non-positive equity")
            targets = {t: (u * px[t]) / eq for t, u in s["holdings"].items()
                       if t != sym}
            cost = set_holdings_fn(s, targets, px, tab["cost_bps"])
            positions.pop(sym, None)
            ev = {"sym": sym, "reason": reason, "bar_date": bar_date,
                  "fill_close": trig_close, "cost": round(float(cost), 4),
                  "at_utc": _utc_now_str(), "at_epoch": time.time()}
            xa.setdefault("events", []).append(ev)
            xa["events"] = xa["events"][-_EVENT_CAP:]
            msgs.append("EXIT-AXIS %s %s at settled close %.6g (%s, cost $%.2f)"
                        % (reason, sym, trig_close, bar_date, cost))
        _refresh_mirrors(xa)
        return "; ".join(msgs) if msgs else None
    except Exception as e:
        return _err(s, "sleeve %s" % name, "%s: %s" % (type(e).__name__, e))


def growth_exit_check(b, px_df, cost_bps_side, log_fn, cfg=None):
    """LIVE hook for growth_book.py tick(): called after the due-branch, before
    equity is computed. px_df is the book's own 2y daily-close DataFrame, so
    sigma/peak/bars are recomputed exactly and statelessly from history each
    tick (immune to missed ticks). PHASE 1 (bookkeeping only — not a rule
    change) runs ALWAYS, even while disabled: entry_sigma freeze, peak_close
    from settled daily closes, peak_tracking_since. PHASE 2 (exit decisions,
    mirroring rebalance()'s cost math on a close) runs only when the config is
    enabled AND growth_book is in enabled_books, at most once per settled bar
    date per symbol. Returns an action string or None. Never raises."""
    try:
        cfg = cfg if cfg is not None else load_config()
        if cfg.get("config_error"):
            _err(b, "growth_book", cfg["config_error"])
            cfg = dict(_DISABLED)      # loud, then behave as disabled
        mode = cfg.get("enabled")
        in_books = "growth_book" in cfg.get("enabled_books", [])
        real = (mode is True) and in_books
        shadow = (mode == "shadow") and in_books
        xa = b.setdefault("exit_axis", {})
        positions = xa.setdefault("positions", {})
        held = list(b.get("holdings") or {})
        for gone in [t for t in positions if t not in held]:
            positions.pop(gone, None)  # the book's own rebalance dropped it
        entry_date = b.get("last_rebalance_date")
        if not held or not entry_date:
            _refresh_mirrors(xa)
            return None

        today = _utc_today()
        msgs = []
        series_map, decidable = {}, set()
        for sym in held:
            if sym not in px_df.columns:
                _err(b, "growth_book", "%s missing from price frame — "
                     "exit-exempt this tick" % sym)
                continue
            col = px_df[sym].dropna()
            dates = [d.strftime("%Y-%m-%d") for d in col.index]
            closes = [float(x) for x in col.values]
            if dates and dates[-1] == today:      # in-progress bar: not settled
                dates, closes = dates[:-1], closes[:-1]
            if not dates:
                continue
            e_px = b.get("entry_px", {}).get(sym)
            if e_px is None or float(e_px) <= 0:
                _err(b, "growth_book", "%s has no entry_px — exit-exempt" % sym)
                continue
            # phase 1: bookkeeping only (not a rule change) — always on,
            # stateless from the full history (peak_tracking_since carried)
            prior = positions.get(sym)
            st = _init_position_state(sym, e_px, entry_date, dates, closes)
            if (prior and prior.get("entry_date") == entry_date
                    and float(prior.get("entry_px") or 0) == float(e_px)
                    and prior.get("peak_tracking_since")):
                st["peak_tracking_since"] = prior["peak_tracking_since"]
            positions[sym] = st
            series_map[sym] = (dates, closes)
            # phase 2: decisions — config-gated, once per settled bar date
            if xa.get("last_eval_date", {}).get(sym) == dates[-1]:
                continue                          # one evaluation per bar date
            xa.setdefault("last_eval_date", {})[sym] = dates[-1]
            decidable.add(sym)
            if not real:
                continue
            reason = decide_exit(st["entry_px"], st["entry_sigma"],
                                 st["bars_held"], st["peak_close"], closes[-1],
                                 cfg.get("tp_sigma"), cfg.get("trail_sigma"))
            if not reason:
                continue
            trig_close = closes[-1]
            u = b["holdings"].pop(sym)
            b.get("entry_px", {}).pop(sym, None)
            traded = u * trig_close
            cost = traded * float(cost_bps_side) / 10000.0
            b["cash"] = b["cash"] + traded - cost
            b["costs_paid"] = round(b.get("costs_paid", 0.0) + cost, 2)
            ev = {"event": "EXIT_AXIS", "sym": sym, "reason": reason,
                  "bar_date": dates[-1], "fill_close": trig_close,
                  "units": u, "traded_notional": round(traded, 2),
                  "cost": round(cost, 2), "at_utc": _utc_now_str(),
                  "at_epoch": time.time()}
            try:
                log_fn(dict(ev))
            except Exception as le:
                _err(b, "growth_book", "ledger write failed: %s" % le)
            xa.setdefault("events", []).append(ev)
            xa["events"] = xa["events"][-_EVENT_CAP:]
            positions.pop(sym, None)
            msgs.append("EXIT-AXIS %s %s at settled close %.4f (%s, cost $%.2f)"
                        % (reason, sym, trig_close, dates[-1], cost))
        if shadow:
            # SHADOW: identical evaluation on the same settled bars, zero
            # execution. COPY of holdings; writes only inside xa.
            sm = _shadow_tick(xa, "growth_book", positions,
                              dict(b.get("holdings") or {}), series_map, cfg,
                              cost_bps_side,
                              err=lambda m: _err(b, "growth_book", m),
                              decidable=decidable)
            if sm:
                msgs.append(sm)
        _refresh_mirrors(xa)
        return "; ".join(msgs) if msgs else None
    except Exception as e:
        return _err(b, "growth_book", "%s: %s" % (type(e).__name__, e))


def value_exit_check(b, pb, cost_bps_side, log_fn, cfg=None, fetch=None):
    """LIVE hook for value_book.py tick(): called once pb (price_book) exists,
    before the valuation block. DECLARED BASIS: sigma/peak/close are LOCAL
    settled daily closes (matching entry_px / mark_px_local); the close is
    then BOOKED in USD via the same fx rate the book uses this tick. A name
    whose price or FX is refused is never exited (never at a wrong FX scale).
    PHASE 1 (bookkeeping only — not a rule change) runs ALWAYS, even while
    disabled: entry_sigma freeze, peak_close from settled LOCAL daily closes,
    peak_tracking_since — at most one refresh per UTC day. PHASE 2 (exit
    decisions) runs only when enabled AND value_book is in enabled_books.
    Does not touch b["equity"] (which can be None). Never raises."""
    try:
        cfg = cfg if cfg is not None else load_config()
        if cfg.get("config_error"):
            _err(b, "value_book", cfg["config_error"])
            cfg = dict(_DISABLED)      # loud, then behave as disabled
        mode = cfg.get("enabled")
        in_books = "value_book" in cfg.get("enabled_books", [])
        real = (mode is True) and in_books
        shadow = (mode == "shadow") and in_books
        xa = b.setdefault("exit_axis", {})
        positions = xa.setdefault("positions", {})
        held = list(b.get("holdings") or {})
        for gone in [t for t in positions if t not in held]:
            positions.pop(gone, None)
        entry_date = b.get("last_rebalance_date")
        if not held or not entry_date:
            _refresh_mirrors(xa)
            return None
        # one refresh/evaluation per UTC day: settled bars change at most daily
        if xa.get("last_eval_utc") == _utc_today():
            return None
        fetch = fetch or _settled_yf_daily      # LOCAL closes, auto_adjust=True
        series = fetch(held)                    # value_book needs its own
        xa["last_eval_utc"] = _utc_today()      # longer history (10d in scope)

        # ---- phase 1: bookkeeping only (not a rule change) — always on ----
        tracked = []
        for sym in held:
            if sym not in series or not series[sym][1]:
                _err(b, "value_book", "%s: no settled local closes — "
                     "exit-exempt this tick" % sym)
                continue
            dates, closes = series[sym]
            e_px = b.get("entry_px", {}).get(sym)     # LOCAL ccy, by contract
            if e_px is None or float(e_px) <= 0:
                _err(b, "value_book", "%s has no local entry_px — exit-exempt" % sym)
                continue
            st = positions.get(sym)
            if (st is None or st.get("entry_date") != entry_date
                    or float(st.get("entry_px") or 0) != float(e_px)):
                st = _init_position_state(sym, e_px, entry_date, dates, closes)
                st["basis"] = "LOCAL settled daily closes (matches entry_px)"
                positions[sym] = st
            else:
                _advance_position_state(st, dates, closes)
            tracked.append(sym)
        _refresh_mirrors(xa)

        # ---- phase 2: exit decisions — config-gated ----
        if shadow:
            # SHADOW: identical evaluation (LOCAL settled closes, this tick's
            # FX, same refusals), zero execution. COPY of holdings; writes
            # only inside xa.
            return _shadow_tick(
                xa, "value_book", positions, dict(b.get("holdings") or {}),
                {t: series[t] for t in tracked if t in series}, cfg,
                cost_bps_side, err=lambda m: _err(b, "value_book", m),
                rates=dict(pb.get("rate") or {}),
                refused=dict(pb.get("refused") or {}),
                decidable=set(tracked))
        if not real:
            return None
        msgs = []
        for sym in tracked:
            if sym not in b["holdings"]:
                continue
            st = positions[sym]
            dates, closes = series[sym]
            reason = decide_exit(st["entry_px"], st["entry_sigma"],
                                 st["bars_held"], st["peak_close"], closes[-1],
                                 cfg.get("tp_sigma"), cfg.get("trail_sigma"))
            if not reason:
                continue
            if sym in (pb.get("refused") or {}):
                _err(b, "value_book", "%s triggered %s but its FX/price is "
                     "refused (%s) — NOT exiting at a wrong FX scale"
                     % (sym, reason, pb["refused"][sym]))
                continue
            rate = (pb.get("rate") or {}).get(sym)
            if not rate or rate <= 0:
                _err(b, "value_book", "%s triggered %s but no USD/unit rate "
                     "this tick — NOT exiting" % (sym, reason))
                continue
            trig_local = closes[-1]
            usd_px = trig_local * float(rate)
            u = b["holdings"].pop(sym)
            b.get("entry_px", {}).pop(sym, None)
            b.get("entry_px_usd", {}).pop(sym, None)
            traded_usd = u * usd_px
            cost = traded_usd * float(cost_bps_side) / 10000.0
            b["cash"] = b["cash"] + traded_usd - cost
            b["costs_paid"] = round(b.get("costs_paid", 0.0) + cost, 2)
            ev = {"event": "EXIT_AXIS", "sym": sym, "reason": reason,
                  "bar_date": dates[-1], "fill_close_local": trig_local,
                  "fx_usd_per_unit": rate, "fill_usd": usd_px, "units": u,
                  "traded_usd": round(traded_usd, 2), "cost": round(cost, 2),
                  "basis": "LOCAL settled close x this tick's USD rate",
                  "at_utc": _utc_now_str(), "at_epoch": time.time()}
            try:
                log_fn(dict(ev))
            except Exception as le:
                _err(b, "value_book", "ledger write failed: %s" % le)
            xa.setdefault("events", []).append(ev)
            xa["events"] = xa["events"][-_EVENT_CAP:]
            positions.pop(sym, None)
            msgs.append("EXIT-AXIS %s %s at settled LOCAL close %.6g -> $%.2f "
                        "(%s, cost $%.2f)" % (reason, sym, trig_local, usd_px,
                                              dates[-1], cost))
        _refresh_mirrors(xa)
        return "; ".join(msgs) if msgs else None
    except Exception as e:
        return _err(b, "value_book", "%s: %s" % (type(e).__name__, e))


# --------------------------------------------------------------------------
# SELFTEST — the causality proof. python3 exit_axis.py --selftest
# --------------------------------------------------------------------------
def _walk_forward_honest(closes, entry_bar, tp_sigma, trail_sigma):
    """The honest evaluator: walk bar by bar; at each daily close decide from
    information knowable at that close only; book the exit AT THAT CLOSE.
    Returns (exit_bar_index, exit_fill_price, reason) or (None, None, None)."""
    entry_px = closes[entry_bar]
    sigma = compute_entry_sigma(closes[:entry_bar + 1])
    peak = entry_px
    for t in range(entry_bar + 1, len(closes)):
        peak = max(peak, closes[t])            # peak of CLOSES up to t only
        reason = decide_exit(entry_px, sigma, t - entry_bar, peak, closes[t],
                             tp_sigma, trail_sigma)
        if reason:
            return t, closes[t], reason        # booked at the triggering close
    return None, None, None


def _walk_forward_peeking(closes, entry_bar, tp_sigma, trail_sigma):
    """The DELIBERATELY BROKEN evaluator: peak harvesting. If any future bar
    crosses the threshold it books the exit at the SERIES MAX — the classic
    lookahead the selftest must be able to catch. Never use this."""
    entry_px = closes[entry_bar]
    sigma = compute_entry_sigma(closes[:entry_bar + 1])
    if sigma is None:
        return None, None, None
    fut = closes[entry_bar + 1:]
    for i, c in enumerate(fut, start=1):
        if tp_sigma is not None and (c / entry_px - 1.0) >= tp_sigma * sigma * math.sqrt(i):
            best = max(fut)                    # books the peak: LOOKAHEAD
            return entry_bar + 1 + fut.index(best), best, "take_profit"
    return None, None, None


def _detect_lookahead(closes, entry_bar, exit_bar, exit_fill, tp_sigma, trail_sigma):
    """An exit is causal iff it is booked at the close of the FIRST bar where
    the rule triggers on information available at that bar. This replays the
    walk honestly and compares: a booking at any other bar or any other price
    (the peak, the threshold, a better later price) is lookahead."""
    hb, hpx, _ = _walk_forward_honest(closes, entry_bar, tp_sigma, trail_sigma)
    if exit_bar is None:
        return hb is not None            # missed a real trigger: also broken
    if hb is None:
        return True                      # exited where no causal trigger exists
    return exit_bar != hb or abs(exit_fill - closes[hb]) > 1e-12


def _selftest():
    fails = []

    def check(cond, label):
        tag = "PASS" if cond else "FAIL"
        print("  [%s] %s" % (tag, label))
        if not cond:
            fails.append(label)

    print("=" * 74)
    print("EXIT-AXIS SELFTEST — causality proof  [%s]" % _utc_now_str())
    print("=" * 74)

    # Synthetic daily series: 40 flat-ish warmup bars (sigma history), then a
    # steady climb of ~+10% over 20 bars, then a crash of ~-25% over 10 bars.
    closes = []
    px = 100.0
    for i in range(40):                       # warmup: small alternating moves
        px *= (1.0 + (0.002 if i % 2 == 0 else -0.002))
        closes.append(px)
    entry_bar = len(closes) - 1               # enter at the last warmup close
    entry_px = closes[entry_bar]
    for _ in range(20):                       # climb ~ +10% by bar 20
        px *= 1.00478
        closes.append(px)
    for _ in range(10):                       # crash ~ -25% by bar 30
        px *= 0.9716
        closes.append(px)

    sigma = compute_entry_sigma(closes[:entry_bar + 1])
    check(sigma is not None and sigma > 0, "entry sigma frozen from warmup closes (sigma=%.6f)" % (sigma or 0))

    tp = 3.0                                  # triggers during the climb
    hb, hpx, hreason = _walk_forward_honest(closes, entry_bar, tp, None)
    series_peak = max(closes[entry_bar + 1:])

    check(hreason == "take_profit", "honest evaluator triggers take_profit during the climb (bar %s)" % hb)
    check(hb is not None and closes[hb] == hpx,
          "honest exit is booked at the CLOSE of the triggering session")
    check(hpx is not None and hpx < series_peak,
          "honest fill %.4f is STRICTLY BELOW the series peak %.4f (no peak harvesting)"
          % (hpx or 0, series_peak))
    if hb is not None:
        thresh_px = entry_px * (1.0 + tp * sigma * math.sqrt(hb - entry_bar))
        check(abs(hpx - thresh_px) > 1e-9,
              "honest fill %.6f differs from the exact threshold price %.6f "
              "(fills happen at closes, not at thresholds)" % (hpx, thresh_px))
        check(hpx >= thresh_px,
              "triggering close overshoots the threshold (discrete bars)")

    # the peeking evaluator books the series max: must be DETECTED and FAILED
    pb, ppx, preason = _walk_forward_peeking(closes, entry_bar, tp, None)
    check(preason == "take_profit" and ppx == series_peak,
          "peeking evaluator (deliberately broken) books the series max %.4f" % (ppx or 0))
    check(ppx is not None and hpx is not None and ppx > hpx,
          "peeking result %.4f > honest result %.4f — the inflation lookahead buys"
          % (ppx or 0, hpx or 0))
    check(_detect_lookahead(closes, entry_bar, pb, ppx, tp, None),
          "detector FLAGS the peeking evaluator as lookahead (selftest exits 1 "
          "if such an evaluator ever goes undetected)")
    check(not _detect_lookahead(closes, entry_bar, hb, hpx, tp, None),
          "detector does NOT flag the honest evaluator")

    # trailing stop causality on the crash leg
    tb, tpx, treason = _walk_forward_honest(closes, entry_bar, None, 2.5)
    check(treason == "trailing_stop", "trailing stop triggers on the crash leg (bar %s)" % tb)
    check(tb is not None and tpx == closes[tb] and tpx < series_peak,
          "trailing-stop fill is the triggering session's close, below the peak")
    if tb is not None:
        peak_at_tb = max(closes[entry_bar:tb + 1])
        stop_px = peak_at_tb * (1.0 - 2.5 * sigma)
        check(abs(tpx - stop_px) > 1e-9,
              "trailing fill differs from the exact stop level (close, not threshold)")

    # guard rails
    check(decide_exit(100, None, 10, 110, 120, 2, 2) is None,
          "sigma=None -> exit-exempt (None)")
    check(decide_exit(100, 0.01, 0, 100, 150, 2, 2) is None,
          "bars_held=0 -> no exit on the entry bar")
    check(decide_exit(100, 0.01, 10, 110, 105, None, None) is None,
          "both legs None -> rule fully disabled")
    check(compute_entry_sigma([100.0] * 20) is None,
          "fewer than %d returns -> sigma None (exit-exempt)" % SIGMA_MIN_RETURNS)
    cfg = load_config()
    check(cfg.get("enabled") in (False, True, "shadow"),
          "load_config returns a normalized enabled mode (false/true/'shadow')")
    if os.path.exists(CONFIG_PATH):
        check(cfg["enabled"] is not True,
              "live config cannot execute REAL exits (enabled=%r; true still "
              "requires a funder-signed amendment)" % (cfg["enabled"],))

    # ---- config mode normalization: "shadow" honoured, junk never arms ----
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pshadow = os.path.join(td, "c1.json")
        with open(pshadow, "w") as f:
            json.dump({"enabled": "shadow", "tp_sigma": 4.0,
                       "trail_sigma": None, "enabled_books": ["x"]}, f)
        c1 = load_config(pshadow)
        check(c1["enabled"] == "shadow" and "config_error" not in c1,
              "load_config normalizes enabled:\"shadow\" to shadow mode")
        pjunk = os.path.join(td, "c2.json")
        with open(pjunk, "w") as f:
            json.dump({"enabled": "SHADOWY-TYPO", "enabled_books": ["x"]}, f)
        c2 = load_config(pjunk)
        check(c2["enabled"] is False and bool(c2.get("config_error")),
              "unknown enabled string -> LOUD disabled (a truthy typo can "
              "never arm real exits)")
        ptrue = os.path.join(td, "c3.json")
        with open(ptrue, "w") as f:
            json.dump({"enabled": True, "tp_sigma": 2.0,
                       "enabled_books": ["y"]}, f)
        c3 = load_config(ptrue)
        check(c3["enabled"] is True and "config_error" not in c3,
              "enabled:true still normalizes to True (real-mode contract "
              "unchanged)")

    # ---- live hooks: always-on bookkeeping while DISABLED, injected data ----
    fdates = ["%05d" % i for i in range(len(closes))]
    fake_fetch = lambda syms: {t: (list(fdates), list(closes)) for t in syms}
    off = dict(_DISABLED)

    s1 = {"name": "spy_trend_cash", "cash": 100.0, "frozen": False,
          "holdings": {"SPY": 2.0}, "entry_px": {"SPY": entry_px},
          "last_rebalance": fdates[entry_bar]}
    r1 = tournament_exit_check(s1, None, cfg=off, fetchers={"yf": fake_fetch})
    st1 = s1.get("exit_axis", {}).get("positions", {}).get("SPY", {})
    check(r1 is None and s1["holdings"] == {"SPY": 2.0}
          and "exit_axis_error" not in s1,
          "DISABLED tournament hook: no action, no trade, no error")
    check(abs((st1.get("entry_sigma") or 0) - sigma) < 1e-12,
          "DISABLED tournament hook still froze entry_sigma (always-on bookkeeping)")
    check(st1.get("peak_close") == max(closes[entry_bar:])
          and bool(st1.get("peak_tracking_since")),
          "DISABLED hook tracks peak_close of settled closes + stamps peak_tracking_since")
    check(s1["exit_axis"]["entry_sigma"].get("SPY") == st1.get("entry_sigma")
          and s1["exit_axis"]["peak_close"].get("SPY") == st1.get("peak_close"),
          "flat bookkeeping mirrors (entry_sigma / peak_close) stay in sync")

    s2 = {"name": "equal_weight_all", "cash": 0.0, "holdings": {"SPY": 1.0},
          "entry_px": {"SPY": 100.0}, "last_rebalance": fdates[entry_bar]}
    on_bad = {"enabled": True, "tp_sigma": 3.0, "trail_sigma": None,
              "enabled_books": ["equal_weight_all"]}
    r2 = tournament_exit_check(s2, None, cfg=on_bad, fetchers={"yf": fake_fetch})
    check(r2 is None and "NOT in SLEEVE_EXIT_TABLE"
          in s2.get("exit_axis_error", {}).get("msg", "")
          and s2["holdings"] == {"SPY": 1.0},
          "excluded sleeve force-enabled in config -> loud refusal, holdings untouched")

    s3 = {"name": "spy_trend_cash", "cash": 100.0, "frozen": False,
          "holdings": {"SPY": 2.0}, "entry_px": {"SPY": entry_px},
          "last_rebalance": fdates[entry_bar], "costs": 0.0}
    calls = []

    def fake_sh(s, targets, px, bps):
        calls.append((dict(targets), dict(px), bps))
        s["holdings"] = {t: s["holdings"][t] for t in targets}
        return 0.31
    on = {"enabled": True, "tp_sigma": None, "trail_sigma": 2.5,
          "enabled_books": ["spy_trend_cash"]}
    r3 = tournament_exit_check(s3, fake_sh, cfg=on, fetchers={"yf": fake_fetch})
    ev3 = (s3.get("exit_axis", {}).get("events") or [{}])[-1]
    check(r3 is not None and "trailing_stop" in (r3 or ""),
          "ENABLED trailing stop fires on the crash leg through the live hook")
    check(bool(calls) and calls[0][0] == {}
          and calls[0][1] == {"SPY": closes[-1]} and calls[0][2] == 10.0,
          "exit routed through set_holdings at the sleeve's own 10 bps; px is the latest settled close")
    check(ev3.get("fill_close") == closes[-1]
          and (ev3.get("fill_close") or 0) < series_peak,
          "live exit booked at the latest SETTLED close, never the peak")

    b4 = {"holdings": {"BARC.L": 10.0}, "entry_px": {"BARC.L": entry_px},
          "last_rebalance_date": fdates[entry_bar], "cash": 0.0,
          "costs_paid": 0.0}
    pb4 = {"refused": {}, "rate": {"BARC.L": 0.0127}}
    r4 = value_exit_check(b4, pb4, 10.0, lambda ev: None, cfg=dict(_DISABLED),
                          fetch=fake_fetch)
    st4 = b4.get("exit_axis", {}).get("positions", {}).get("BARC.L", {})
    check(r4 is None and b4["holdings"] == {"BARC.L": 10.0}
          and abs((st4.get("entry_sigma") or 0) - sigma) < 1e-12
          and st4.get("basis", "").startswith("LOCAL"),
          "DISABLED value hook: bookkeeping on the declared LOCAL basis, no trade")

    try:
        import pandas as pd
        idx = pd.bdate_range("2025-01-01", periods=len(closes))
        pxdf = pd.DataFrame({"SLV": closes}, index=idx)
        b5 = {"holdings": {"SLV": 5.0}, "entry_px": {"SLV": entry_px},
              "last_rebalance_date": idx[entry_bar].strftime("%Y-%m-%d"),
              "cash": 0.0, "costs_paid": 0.0}
        r5 = growth_exit_check(b5, pxdf, 10.0, lambda ev: None,
                               cfg=dict(_DISABLED))
        st5 = b5.get("exit_axis", {}).get("positions", {}).get("SLV", {})
        check(r5 is None and b5["holdings"] == {"SLV": 5.0}
              and abs((st5.get("entry_sigma") or 0) - sigma) < 1e-12
              and st5.get("peak_close") == max(closes[entry_bar:]),
              "DISABLED growth hook: stateless bookkeeping from the 2y frame, no trade")
    except ImportError:
        check(False, "pandas unavailable for the growth-hook bookkeeping check")

    # ---- SHADOW MODE: structurally unable to trade; records everything ----
    def _boobytrap_sh(*a, **k):
        raise AssertionError("set_holdings called from the shadow path")

    class _NoTradeHoldings(dict):
        """holdings that detonate on ANY mutation — proof shadow never trades."""
        def _boom(self, *a, **k):
            raise AssertionError("holdings mutated from the shadow path")
        pop = popitem = update = clear = setdefault = _boom
        __setitem__ = __delitem__ = _boom

    check("shadow" not in s1.get("exit_axis", {})
          and "shadow_events" not in s1.get("exit_axis", {}),
          "mode false unchanged: DISABLED hook writes no shadow state at all")
    check("shadow_events" not in s3.get("exit_axis", {}),
          "mode true unchanged: REAL exits book xa['events'], never shadow_events")

    sh_cfg = {"enabled": "shadow", "tp_sigma": None, "trail_sigma": 2.5,
              "enabled_books": ["spy_trend_cash"]}
    s6 = {"name": "spy_trend_cash", "cash": 100.0, "frozen": False,
          "holdings": _NoTradeHoldings({"SPY": 2.0}),
          "entry_px": {"SPY": entry_px},
          "last_rebalance": fdates[entry_bar], "costs": 0.0}
    r6 = tournament_exit_check(s6, _boobytrap_sh, cfg=sh_cfg,
                               fetchers={"yf": fake_fetch})
    xa6 = s6.get("exit_axis", {})
    ev6 = (xa6.get("shadow_events") or [{}])[-1]
    leg6 = (xa6.get("shadow", {}).get("legs") or {}).get("SPY", {})
    check(r6 is not None and "SHADOW trailing_stop" in (r6 or "")
          and "exit_axis_error" not in s6,
          "SHADOW: threshold fires yet the booby-trapped set_holdings and "
          "booby-trapped holdings were NEVER touched (structurally no trade)")
    check(dict(s6["holdings"]) == {"SPY": 2.0} and s6["cash"] == 100.0
          and s6["costs"] == 0.0 and not xa6.get("events")
          and "SPY" in xa6.get("positions", {}),
          "SHADOW: holdings/cash/costs identical, no real event booked, "
          "position still tracked")
    exp_net6 = 2.0 * closes[-1] * (1.0 - 10.0 / 10000.0)
    check(ev6.get("trigger_close") == closes[-1]
          and abs((ev6.get("hypothetical_proceeds_net") or 0) - exp_net6) < 1e-6
          and abs((ev6.get("cost") or 0)
                  - 2.0 * closes[-1] * 10.0 / 10000.0) < 1e-6,
          "shadow event books at the triggering settled close with the "
          "sleeve's own cost_bps charged")
    _need = ("utc", "book", "symbol", "reason", "trigger_close", "units",
             "hypothetical_proceeds_net", "cost_bps", "entry_px",
             "entry_sigma", "bars_held")
    check(all(k in ev6 for k in _need) and ev6.get("executed") is False
          and ev6.get("book") == "spy_trend_cash" and ev6.get("units") == 2.0,
          "shadow event carries every declared field, plus executed:false")
    check(leg6.get("status") == "shadow_flat"
          and abs((leg6.get("exit", {}).get("hypothetical_proceeds_net") or 0)
                  - exp_net6) < 1e-6,
          "after a shadow exit the shadow leg is FLAT, frozen at net proceeds")

    # flat-until-resync: a further settled bar books NOTHING new
    closes2 = closes + [closes[-1] * 0.99]
    fdates2 = ["%05d" % i for i in range(len(closes2))]
    fetch2 = lambda syms: {t: (list(fdates2), list(closes2)) for t in syms}
    s6["exit_axis"]["last_eval_utc"] = "1970-01-01"   # release the day gate
    tournament_exit_check(s6, _boobytrap_sh, cfg=sh_cfg,
                          fetchers={"yf": fetch2})
    check(len(s6["exit_axis"].get("shadow_events") or []) == 1
          and (s6["exit_axis"]["shadow"]["legs"]["SPY"]["status"]
               == "shadow_flat")
          and "exit_axis_error" not in s6,
          "shadow leg stays FLAT on later bars — no double booking, no trade")

    # the strategy's next ACTUAL rebalance replaces the position -> the cycle
    # completes (shadow-vs-actual, both cost-charged once) and shadow re-syncs
    closes3 = closes2 + [closes2[-1] * 1.001]
    fdates3 = ["%05d" % i for i in range(len(closes3))]
    fetch3 = lambda syms: {t: (list(fdates3), list(closes3)) for t in syms}
    s6["holdings"] = _NoTradeHoldings({"SPY": 3.0})   # actual rebalance resized
    s6["entry_px"] = {"SPY": closes2[-1]}
    s6["last_rebalance"] = fdates2[-1]
    s6["exit_axis"]["last_eval_utc"] = "1970-01-01"
    tournament_exit_check(s6, _boobytrap_sh, cfg=sh_cfg,
                          fetchers={"yf": fetch3})
    cyc6 = (s6["exit_axis"].get("shadow", {}).get("cycles") or [{}])[-1]
    exp_actual6 = 2.0 * closes2[-1] * (1.0 - 10.0 / 10000.0)
    check(cyc6.get("complete") is True and cyc6.get("fired") is True
          and abs((cyc6.get("shadow_net") or 0) - exp_net6) < 1e-6
          and abs((cyc6.get("actual_net") or 0) - exp_actual6) < 1e-6
          and abs((cyc6.get("delta_net") or 0)
                  - (exp_net6 - exp_actual6)) < 1e-6,
          "actual rebalance completes the cycle: shadow net vs actual net at "
          "the last observed settled close, both charged cost_bps once")
    leg6c = s6["exit_axis"]["shadow"]["legs"].get("SPY", {})
    check(leg6c.get("status") == "in_sync"
          and float(leg6c.get("entry_px") or 0) == closes2[-1]
          and leg6c.get("units") == 3.0 and "exit_axis_error" not in s6,
          "shadow re-syncs to the actual book's new position after the rebalance")

    # zero fabrication: nothing fires on a series that never triggers
    closesf, pf = [], 100.0
    for i in range(50):
        pf *= (1.0 + (0.001 if i % 2 == 0 else -0.001))
        closesf.append(pf)
    fdatesf = ["%05d" % i for i in range(len(closesf))]
    fetchf = lambda syms: {t: (list(fdatesf), list(closesf)) for t in syms}
    s7 = {"name": "spy_trend_cash", "cash": 0.0, "frozen": False,
          "holdings": _NoTradeHoldings({"SPY": 1.0}),
          "entry_px": {"SPY": closesf[40]}, "last_rebalance": fdatesf[40],
          "costs": 0.0}
    declared = {"enabled": "shadow", "tp_sigma": 4.0, "trail_sigma": None,
                "enabled_books": ["spy_trend_cash"]}
    tournament_exit_check(s7, _boobytrap_sh, cfg=declared,
                          fetchers={"yf": fetchf})
    leg7 = (s7.get("exit_axis", {}).get("shadow", {}).get("legs")
            or {}).get("SPY", {})
    check(not s7.get("exit_axis", {}).get("shadow_events")
          and leg7.get("status") == "in_sync" and "exit_axis_error" not in s7,
          "declared variant (tp=4.0, trail=None) books NOTHING when no "
          "threshold genuinely fires — zero fabrication")

    try:
        import pandas as pd
        idx3 = pd.bdate_range("2025-01-01", periods=len(closes))
        pxdf3 = pd.DataFrame({"SLV": closes}, index=idx3)
        b6 = {"holdings": _NoTradeHoldings({"SLV": 5.0}),
              "entry_px": {"SLV": entry_px},
              "last_rebalance_date": idx3[entry_bar].strftime("%Y-%m-%d"),
              "cash": 123.0, "costs_paid": 7.0}
        g_cfg = {"enabled": "shadow", "tp_sigma": None, "trail_sigma": 2.5,
                 "enabled_books": ["growth_book"]}
        r6g = growth_exit_check(b6, pxdf3, 10.0, lambda ev: None, cfg=g_cfg)
        evg = (b6.get("exit_axis", {}).get("shadow_events") or [{}])[-1]
        check(r6g is not None and "SHADOW trailing_stop" in (r6g or "")
              and dict(b6["holdings"]) == {"SLV": 5.0}
              and b6["cash"] == 123.0 and b6["costs_paid"] == 7.0
              and "exit_axis_error" not in b6,
              "growth SHADOW: fires with booby-trapped holdings untouched; "
              "cash/costs identical (no trade)")
        check(evg.get("units") == 5.0
              and evg.get("trigger_close") == closes[-1]
              and abs((evg.get("hypothetical_proceeds_net") or 0)
                      - 5.0 * closes[-1] * (1.0 - 10.0 / 10000.0)) < 1e-6,
              "growth shadow event: units, triggering settled close and net "
              "proceeds exact")
    except ImportError:
        check(False, "pandas unavailable for the growth shadow check")
        check(False, "pandas unavailable for the growth shadow event check")

    v_cfg = {"enabled": "shadow", "tp_sigma": None, "trail_sigma": 2.5,
             "enabled_books": ["value_book"]}
    b7 = {"holdings": _NoTradeHoldings({"BARC.L": 10.0}),
          "entry_px": {"BARC.L": entry_px},
          "last_rebalance_date": fdates[entry_bar], "cash": 5.0,
          "costs_paid": 0.0}
    pb7 = {"refused": {}, "rate": {"BARC.L": 0.0127}}
    r7v = value_exit_check(b7, pb7, 10.0, lambda ev: None, cfg=v_cfg,
                           fetch=fake_fetch)
    evv = (b7.get("exit_axis", {}).get("shadow_events") or [{}])[-1]
    exp_netv = 10.0 * closes[-1] * 0.0127 * (1.0 - 10.0 / 10000.0)
    check(r7v is not None and "SHADOW trailing_stop" in (r7v or "")
          and dict(b7["holdings"]) == {"BARC.L": 10.0} and b7["cash"] == 5.0
          and evv.get("fx_usd_per_unit") == 0.0127
          and abs((evv.get("hypothetical_proceeds_net") or 0) - exp_netv) < 1e-6
          and "exit_axis_error" not in b7,
          "value SHADOW: LOCAL close x tick FX -> exact USD net; no trade, "
          "holdings/cash untouched")
    b8 = {"holdings": _NoTradeHoldings({"BARC.L": 10.0}),
          "entry_px": {"BARC.L": entry_px},
          "last_rebalance_date": fdates[entry_bar], "cash": 5.0,
          "costs_paid": 0.0}
    pb8 = {"refused": {"BARC.L": "stale FX"}, "rate": {"BARC.L": 0.0127}}
    value_exit_check(b8, pb8, 10.0, lambda ev: None, cfg=v_cfg,
                     fetch=fake_fetch)
    leg8 = (b8.get("exit_axis", {}).get("shadow", {}).get("legs")
            or {}).get("BARC.L", {})
    check(not b8.get("exit_axis", {}).get("shadow_events")
          and leg8.get("status") == "in_sync"
          and "NOT booking" in b8.get("exit_axis_error", {}).get("msg", ""),
          "value SHADOW mirrors the real FX refusal: a triggered name with "
          "refused FX books NOTHING, loudly")

    print("-" * 74)
    if fails:
        print("SELFTEST FAILED (%d):" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASSED — %s" % _utc_now_str())
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--shadow-report" in sys.argv:
        sys.exit(shadow_report())
    print(__doc__)
    print("config: %s" % json.dumps(load_config(), indent=2))
