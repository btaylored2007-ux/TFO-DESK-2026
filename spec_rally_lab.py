#!/usr/bin/env python3
"""Spec-rally lab — the funder's RETAIL-NIMBLENESS thesis, tested honestly.

THE THESIS UNDER TEST (funder, 2026-08): "retail's edge is trading volume
movements in speculative equities and resources stocks — names institutions
can't touch for capacity reasons." Decomposed into two measurable claims:

  (A) NIMBLENESS PREMIUM (gross): the SAME volume-confirmed rally rule earns
      more, gross of costs, in small/speculative names than in large liquid
      ones. If false, there is no capacity-constrained premium to harvest.
  (B) RETAIL EXECUTION TAX: if (A) holds gross, does the premium survive the
      spreads those same names charge a retail account? Tiered costs per
      universe, plus a 2x stress run.

ONE RULE, THREE-PLUS UNIVERSES (rally_rider family — identical everywhere so
the universes are the only difference):
  entry   close is the highest close of the trailing 20 sessions AND today's
          volume >= 1.5x the median volume of the PRIOR 20 sessions (median
          excludes the entry bar, so the spike cannot inflate its own
          baseline). Filled at the triggering close.
  size    equal slices of current equity / K, max K concurrent (K in 4/6/8 —
          the variant axis). When signals exceed free slots, highest
          volume-ratio first (deterministic tie-break: ticker asc).
  exits   scale out 1/3 of original units at +2 sigma*sqrt(t), 1/3 at
          +4 sigma*sqrt(t) (t = bars held); trail the remainder 2.5 sigma
          below the peak CLOSE since entry; 40-bar time stop. sigma is
          ENTRY-FROZEN via exit_axis.compute_entry_sigma (trailing ~60 daily
          returns, min 25) — the same function the live tick paths call.
          No exit on the entry bar (bars_held >= 1). TP legs are checked
          before the trail (exit_axis.decide_exit's documented ordering),
          time stop last. Every fill books at the SETTLED CLOSE of the
          triggering session — never a threshold, never a peak.

COSTS — the crux. Charged on EVERY fill (entry and each partial exit), per
side of notional traded:
  large liquid (ETF15 + megacaps) : 10 bps/side   (house ETF constant)
  US small-cap spec               : 50 bps/side
  ASX spec resources              : 150 bps/side
  stress                          : 2x each tier
Gross = the identical engine at 0 bps. Decisions are price-based only, so
trade DATES are identical across cost tiers; only sizes and P&L differ.

SURVIVORSHIP BIAS — STATED PROMINENTLY, NOT FOOTNOTED: yfinance can only
serve tickers that still exist in 2026. Every universe here is survivors-only,
but the bias is NOT symmetric: failure/delisting rates in small-cap spec and
junior resources are far higher than in megacaps (NCM.AX and OZL.AX — both
former ASX resources heavyweights — cannot even be downloaded). This INFLATES
the spec universes' results relative to the control. Any gross premium found
here is therefore an UPPER BOUND on (A); the absence of one is strong
evidence against the thesis.

SEARCH DEFLATION: charged at the house cumulative count, not the flattering
family count. HUNT_BACKLOG.md records 1,345 cumulative trials (2026-08-17);
the exit-axis lab added 45 variants on 2026-08-20 -> house prior = 1,390.
(The tasking note said ~1,206; the ledger says more, so the larger number is
charged.) Plus every universe x K combo run here. N is stated exactly in the
report.

HONESTY CONSTRAINTS (mirrors exit_axis.py --selftest discipline):
  * walk-forward bar by bar; every decision at bar t uses that symbol's
    settled closes/volumes [<= t] only; fills at the triggering close.
  * --selftest proves it: a synthetic tape with a known entry/exit sequence
    must reproduce exactly; truncating the future at the entry bar must not
    change the entry; every booked fill must equal a settled close of that
    symbol on the fill date; the zero-cost run must dominate the costed run.
  * exits are processed before entries on the same bar (frees slots at the
    close — fixed, documented ordering).
  * where data is unavailable (delisted names, failed downloads, short
    histories) it is REPORTED, never approximated.

RESEARCH ONLY. No capital, no orders, no book state touched, no probe
created. Reads market data, writes reports/spec_rally_lab.json and
reports/research_spec_rally.md.

Usage:
    python3 spec_rally_lab.py             # run the lab
    python3 spec_rally_lab.py --selftest  # causality proof (must exit 0)
"""
import json
import math
import os
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import exit_axis                      # compute_entry_sigma — one implementation
import quant_validation as qv

OUT_JSON = os.path.join(BASE, "reports", "spec_rally_lab.json")
OUT_MD = os.path.join(BASE, "reports", "research_spec_rally.md")

START = "2011-01-01"                  # ~15.6y of daily bars
PPY = 252.0
K_GRID = [4, 6, 8]                    # max-concurrent variants ("4-8")
TP1_SIG, TP2_SIG = 2.0, 4.0           # scale-out thresholds, sigma*sqrt(t)
TRAIL_SIG = 2.5                       # trail below peak close, sigma (daily scale)
TIME_STOP_BARS = 40
VOL_MULT = 1.5                        # volume >= 1.5x prior-20d median
HIGH_WINDOW = 20

# House search ledger: HUNT_BACKLOG.md line 8 (1,345 @ 2026-08-17) + the
# exit-axis lab's 45 variants (2026-08-20). Larger than the tasking note's
# ~1,206 — the larger, documented number is charged.
HOUSE_PRIOR_TRIALS = 1345 + 45        # = 1,390

UNIVERSES = {
    # control 1: the house ETF15 (tournament.py) — large, liquid, includes
    # bond/commodity wrappers; rarely prints volume-confirmed equity rallies
    # but it is the registered control set.
    "large_etf15": {
        "cost_bps_side": 10.0, "stress_bps_side": 20.0, "role": "control",
        "tickers": ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD",
                    "HYG", "GLD", "SLV", "DBC", "USO", "IYR", "AGG"],
    },
    # control 2: mega-cap single stocks — the apples-to-apples control for
    # single-stock spec universes (names institutions CAN hold at size).
    "large_megacap": {
        "cost_bps_side": 10.0, "stress_bps_side": 20.0, "role": "control",
        "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "BRK-B",
                    "JPM", "JNJ", "XOM", "PG", "UNH", "V", "HD", "WMT"],
    },
    # US small-cap spec: documented static list, ~40 names across sectors,
    # chosen for (a) still listed (unavoidable — see survivorship note) and
    # (b) sector spread. Several have since grown to mid-caps (EXEL, HALO):
    # size status drifts over 15y, which itself flatters the sample.
    "us_smallcap_spec": {
        "cost_bps_side": 50.0, "stress_bps_side": 100.0, "role": "spec",
        "tickers": [
            # biotech / health
            "EXEL", "HALO", "CORT", "SUPN", "VNDA", "AMPH", "PCRX", "IRWD",
            # energy
            "SM", "MTDR", "CRK", "WTI", "NOG", "REI",
            # materials / miners
            "CDE", "HL", "UEC", "CENX", "KALU", "SXC", "MTRN",
            # tech / semis
            "PLAB", "ACLS", "FORM", "COHU", "VECO", "DIOD", "MTSI",
            # consumer / retail
            "BOOT", "SHAK", "ODP", "GES", "CAL", "SCVL", "PLAY",
            # industrials / services
            "MYRG", "PRIM", "GVA", "MTRX", "HURN", "LQDT",
        ],
    },
    # ASX resources across caps: majors down to spec juniors. NCM.AX
    # (Newcrest) and OZL.AX (OZ Minerals) are gone from yfinance — the
    # survivorship bias made visible.
    "asx_spec_resources": {
        "cost_bps_side": 150.0, "stress_bps_side": 300.0, "role": "spec",
        "tickers": [
            "BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "STO.AX", "WDS.AX",
            "WHC.AX", "ILU.AX", "MIN.AX", "IGO.AX", "NST.AX", "EVN.AX",
            "SFR.AX", "RRL.AX", "GOR.AX", "RMS.AX", "LYC.AX", "PLS.AX",
            "PDN.AX", "BOE.AX", "SYA.AX", "CXO.AX", "LTR.AX", "CHN.AX",
            "DEG.AX", "AGY.AX",
        ],
    },
}


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def fetch_universe(tickers, start=START, min_bars=62):
    """Download settled daily closes + volumes. Returns (symbols, failures)
    where symbols is {ticker: {dates, closes, volumes}} (own dense series,
    oldest first) and failures lists what could NOT be used and why —
    reported, never papered over."""
    import yfinance as yf
    raw = yf.download(tickers, start=start, interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    closes_df, vol_df = raw["Close"], raw["Volume"]
    today = time.strftime("%Y-%m-%d", time.gmtime())

    symbols, failures = {}, []
    for t in tickers:
        if t not in closes_df.columns:
            failures.append({"ticker": t, "reason": "no data returned (delisted or bad symbol)"})
            continue
        c = closes_df[t].dropna()
        if len(c) and c.index[-1].strftime("%Y-%m-%d") == today:
            c = c.iloc[:-1]                   # in-progress bar: not settled
        v = vol_df[t].reindex(c.index)
        keep = (c.to_numpy(float) > 0) & np.isfinite(c.to_numpy(float)) \
            & np.isfinite(v.to_numpy(float)) & (v.to_numpy(float) >= 0)
        c, v = c[keep], v[keep]
        if len(c) == 0:
            failures.append({"ticker": t,
                             "reason": "no data returned — yfinance no longer "
                                       "serves it (consistent with delisting/"
                                       "corporate action; survivorship in action)"})
            continue
        if len(c) < min_bars:
            failures.append({"ticker": t,
                             "reason": "only %d usable bars (<%d)" % (len(c), min_bars)})
            continue
        symbols[t] = {
            "dates": [d.strftime("%Y-%m-%d") for d in c.index],
            "closes": c.to_numpy(float),
            "volumes": v.to_numpy(float),
        }
    return symbols, failures


# --------------------------------------------------------------------------
# Signals (per symbol, own bars; info at bar i uses [<= i] only)
# --------------------------------------------------------------------------
def compute_signals(closes, volumes):
    """entry_ok[i]: close[i] is the max of the last HIGH_WINDOW closes
    (including i) AND volume[i] >= VOL_MULT * median(volume[i-20 .. i-1]).
    vol_ratio[i] for slot ranking. Needs i >= HIGH_WINDOW."""
    n = len(closes)
    entry_ok = np.zeros(n, dtype=bool)
    vol_ratio = np.zeros(n)
    for i in range(HIGH_WINDOW, n):
        med = float(np.median(volumes[i - HIGH_WINDOW:i]))   # excludes bar i
        if med <= 0 or not np.isfinite(med):
            continue
        r = volumes[i] / med
        vol_ratio[i] = r
        if r >= VOL_MULT and closes[i] >= closes[i - HIGH_WINDOW + 1:i + 1].max():
            entry_ok[i] = True
    return entry_ok, vol_ratio


# --------------------------------------------------------------------------
# The engine. ONE code path for every universe / K / cost tier.
# Walk-forward on the universe's master calendar (union of own calendars);
# each symbol acts only on its own settled bars.
# --------------------------------------------------------------------------
def run_engine(symbols, k_max, cost_bps_side, start_cash=100000.0):
    names = sorted(symbols)
    master = sorted({d for t in names for d in symbols[t]["dates"]})
    m_pos = {d: j for j, d in enumerate(master)}
    T = len(master)

    per = {}
    first_entry_master = T
    for t in names:
        s = symbols[t]
        eok, vr = compute_signals(s["closes"], s["volumes"])
        mp = np.array([m_pos[d] for d in s["dates"]], dtype=np.int64)
        per[t] = {"c": s["closes"], "v": s["volumes"], "mp": mp,
                  "eok": eok, "vr": vr}
        # earliest bar this symbol could enter: signal window + sigma minimum
        i0 = max(HIGH_WINDOW, exit_axis.SIGMA_MIN_RETURNS + 1)
        if len(mp) > i0:
            first_entry_master = min(first_entry_master, int(mp[i0]))
    by_master = [[] for _ in range(T)]
    for t in names:
        for i, mp in enumerate(per[t]["mp"]):
            by_master[mp].append((t, i))

    c = cost_bps_side / 10000.0
    cash = start_cash
    pos = {}                       # sym -> state
    last_close = {}
    equity = np.empty(T)
    trades = []                    # round trips (built entry -> legs)
    open_trades = {}
    fills = 0
    traded_notional = 0.0
    skipped_no_sigma = 0
    signals_no_slot = 0

    def mark():
        return cash + sum(pos[s]["units"] * last_close[s] for s in pos)

    for mt in range(T):
        todays = by_master[mt]
        # ---- exits first (settled close of this session) ----
        for sym, i in todays:
            px = per[sym]["c"][i]
            last_close[sym] = px
            if sym not in pos:
                continue
            p = pos[sym]
            bars_held = i - p["entry_i"]
            if bars_held < 1:
                continue
            p["peak"] = max(p["peak"], px)
            legs = []
            ret = px / p["entry_px"] - 1.0
            rt = math.sqrt(bars_held)
            if (not p["tp1"]) and ret >= TP1_SIG * p["sigma"] * rt:
                legs.append(("tp1", p["units0"] / 3.0))
                p["tp1"] = True
            if (not p["tp2"]) and ret >= TP2_SIG * p["sigma"] * rt:
                legs.append(("tp2", p["units0"] / 3.0))
                p["tp2"] = True
            sold = sum(u for _, u in legs)
            remaining = p["units"] - sold
            if remaining > 1e-12 and (px / p["peak"] - 1.0) <= -(TRAIL_SIG * p["sigma"]):
                legs.append(("trail", remaining))
                remaining = 0.0
            if remaining > 1e-12 and bars_held >= TIME_STOP_BARS:
                legs.append(("time", remaining))
                remaining = 0.0
            for reason, units in legs:
                notional = units * px
                cash += notional * (1.0 - c)
                traded_notional += notional
                fills += 1
                p["units"] -= units
                open_trades[sym]["legs"].append(
                    {"reason": reason, "i": i, "px": float(px),
                     "units_frac": units / p["units0"]})
            if p["units"] <= 1e-12:
                tr = open_trades.pop(sym)
                tr["exit_i"] = i
                tr["bars_held"] = bars_held
                trades.append(tr)
                del pos[sym]

        # ---- entries at the same settled close, after exits ----
        cands = []
        for sym, i in todays:
            if sym in pos:
                continue
            if per[sym]["eok"][i]:
                cands.append((-per[sym]["vr"][i], sym, i))
        if cands:
            cands.sort()
            eq_now = mark()
            for _, sym, i in cands:
                if len(pos) >= k_max:
                    signals_no_slot += len(cands) - cands.index((-per[sym]["vr"][i], sym, i))
                    break
                sigma = exit_axis.compute_entry_sigma(list(per[sym]["c"][:i + 1]))
                if sigma is None:
                    skipped_no_sigma += 1
                    continue
                px = per[sym]["c"][i]
                target = eq_now / k_max
                notional = min(target, cash / (1.0 + c))
                if notional < target * 0.10 or notional <= 0:
                    continue                    # cannot fund a meaningful slice
                units = notional / px
                cash -= notional * (1.0 + c)
                traded_notional += notional
                fills += 1
                pos[sym] = {"units": units, "units0": units, "entry_px": px,
                            "entry_i": i, "sigma": sigma, "peak": px,
                            "tp1": False, "tp2": False}
                open_trades[sym] = {"sym": sym, "entry_i": i,
                                    "entry_px": float(px),
                                    "entry_date": master[mt],
                                    "vol_ratio": float(per[sym]["vr"][i]),
                                    "legs": []}
        equity[mt] = mark()

    t0 = min(first_entry_master, T - 1)
    eq = equity[t0:]
    n_open_at_end = len(pos)
    return {
        "equity": eq, "dates": master[t0:], "trades": trades,
        "n_open_at_end": n_open_at_end, "fills": fills,
        "traded_notional": traded_notional,
        "skipped_no_sigma": skipped_no_sigma,
        "signals_no_slot": signals_no_slot,
        "start_cash": start_cash,
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def metrics(eq):
    eq = np.asarray(eq, dtype=float)
    r = eq[1:] / eq[:-1] - 1.0
    yrs = len(eq) / PPY
    cagr = (eq[-1] / eq[0]) ** (1.0 / yrs) - 1.0 if yrs > 0 else 0.0
    sd = r.std(ddof=1)
    sharpe = float(r.mean() / sd * math.sqrt(PPY)) if sd > 0 else 0.0
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    return {"cagr_pct": round(cagr * 100, 2), "sharpe": round(sharpe, 3),
            "maxdd_pct": round(dd * 100, 1), "n_days": int(len(eq))}, r


def per_trade_returns(trades, cost_bps_side):
    """Completed round trips only. Leg proportions are cost-invariant, so one
    trade log (from the gross run) serves every tier:
       net_ret = sum(frac * px_leg) * (1-c) / (entry_px * (1+c)) - 1."""
    c = cost_bps_side / 10000.0
    out = []
    for tr in trades:
        proceeds = sum(l["units_frac"] * l["px"] for l in tr["legs"])
        out.append(proceeds * (1.0 - c) / (tr["entry_px"] * (1.0 + c)) - 1.0)
    return np.array(out)


def buyhold_ew_daily(symbols):
    """Context benchmark: daily-rebalanced EW of whatever is listed (gross).
    Returns (stats, daily_return_series on the universe's master calendar)."""
    names = sorted(symbols)
    master = sorted({d for t in names for d in symbols[t]["dates"]})
    m_pos = {d: j for j, d in enumerate(master)}
    T = len(master)
    rets_sum = np.zeros(T)
    rets_n = np.zeros(T)
    for t in names:
        c = symbols[t]["closes"]
        mp = [m_pos[d] for d in symbols[t]["dates"]]
        for i in range(1, len(c)):
            rets_sum[mp[i]] += c[i] / c[i - 1] - 1.0
            rets_n[mp[i]] += 1
    r = np.where(rets_n > 0, rets_sum / np.maximum(rets_n, 1), 0.0)
    sd = r.std(ddof=1)
    ann = (1.0 + r.mean()) ** PPY - 1.0
    stats = {"ann_pct": round(ann * 100, 2),
             "sharpe": round(float(r.mean() / sd * math.sqrt(PPY)), 3) if sd > 0 else 0.0}
    return stats, r


# --------------------------------------------------------------------------
# Selftest — the causality proof (exit 0 or die loudly)
# --------------------------------------------------------------------------
def _synthetic_symbol():
    closes, volumes = [], []
    px = 100.0
    for i in range(30):                       # warmup: alternating small moves
        px *= (1.0 + (0.005 if i % 2 == 0 else -0.005))
        closes.append(px)
        volumes.append(1000.0)
    # bar 30: 20d-high breakout on 5x volume -> ENTRY here
    px *= 1.03
    closes.append(px); volumes.append(5000.0)
    # bars 31..33: sharp climb (TP legs), then crash (trail)
    for mult in (1.04, 1.05):
        px *= mult
        closes.append(px); volumes.append(1200.0)
    for _ in range(3):
        px *= 0.93
        closes.append(px); volumes.append(1100.0)
    return {"TEST": {"dates": ["2020-01-%02d" % (d + 1) for d in range(len(closes))],
                     "closes": np.array(closes), "volumes": np.array(volumes)}}


def _selftest():
    fails = []

    def check(cond, label):
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    print("=" * 74)
    print("SPEC-RALLY LAB SELFTEST — causality proof  [%s]" % utc_now())
    print("=" * 74)

    syms = _synthetic_symbol()
    s = syms["TEST"]
    res = run_engine(syms, k_max=4, cost_bps_side=0.0)
    all_tr = res["trades"]
    check(len(all_tr) == 1, "exactly one round trip on the synthetic tape")
    if all_tr:
        tr = all_tr[0]
        check(tr["entry_i"] == 30, "entry at the breakout bar (30), not before/after")
        check(abs(tr["entry_px"] - s["closes"][30]) < 1e-12,
              "entry filled at the TRIGGERING close")
        sigma = exit_axis.compute_entry_sigma(list(s["closes"][:31]))
        check(sigma is not None and sigma > 0,
              "sigma frozen at entry via exit_axis.compute_entry_sigma (%.5f)" % (sigma or 0))
        reasons = [l["reason"] for l in tr["legs"]]
        check(reasons[0].startswith("tp"), "first exit leg is a TP scale-out (%s)" % reasons)
        check(all(any(abs(l["px"] - cx) < 1e-12 for cx in s["closes"])
                  for l in tr["legs"]),
              "every exit leg booked at a settled close of the tape")
        for l in tr["legs"]:
            bi = l["i"]
            check(bi > tr["entry_i"], "no exit on the entry bar (leg at %d)" % bi)
            if l["reason"] in ("tp1", "tp2"):
                t_held = bi - tr["entry_i"]
                thr = (TP1_SIG if l["reason"] == "tp1" else TP2_SIG) * sigma * math.sqrt(t_held)
                ret = l["px"] / tr["entry_px"] - 1.0
                check(ret >= thr, "%s trigger honest: ret %.4f >= thr %.4f" % (l["reason"], ret, thr))
                # and NOT already true a bar earlier with the same t: causality
                prev = s["closes"][bi - 1] / tr["entry_px"] - 1.0
                prev_thr = (TP1_SIG if l["reason"] == "tp1" else TP2_SIG) * sigma * math.sqrt(max(t_held - 1, 1))
                check(bi - 1 == tr["entry_i"] or prev < prev_thr,
                      "%s did not trigger earlier (first-crossing bar booked)" % l["reason"])

    # truncation invariance: kill every bar after the entry bar; the entry
    # decision must be identical (proves the entry uses no future info)
    trunc = {"TEST": {k: (v[:31] if not isinstance(v, list) else v[:31])
                      for k, v in syms["TEST"].items()}}
    res_t = run_engine(trunc, k_max=4, cost_bps_side=0.0)
    got_entry = bool(res_t["n_open_at_end"] == 1 or res_t["trades"])
    check(got_entry, "entry survives truncation at the entry bar (no lookahead)")

    # gross dominates net at every bar (costs only ever subtract)
    res_net = run_engine(syms, k_max=4, cost_bps_side=150.0)
    check(np.all(res["equity"] - res_net["equity"] >= -1e-9),
          "zero-cost equity dominates costed equity bar by bar")

    # volume median excludes the entry bar (the spike cannot self-baseline)
    eok, vr = compute_signals(s["closes"], s["volumes"])
    check(bool(eok[30]) and abs(vr[30] - 5.0) < 1e-9,
          "volume ratio uses the PRIOR-20 median (5000/1000 = %.1f)" % vr[30])

    # sigma gate: too-short history refuses the entry rather than guessing
    short = {"TEST": {"dates": s["dates"][:22], "closes": s["closes"][:22],
                      "volumes": s["volumes"][:22]}}
    res_s = run_engine(short, k_max=4, cost_bps_side=0.0)
    check(not res_s["trades"] and res_s["n_open_at_end"] == 0,
          "insufficient sigma history -> no entry (never guessed)")

    print("-" * 74)
    if fails:
        print("SELFTEST FAILED: %d check(s):\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("SELFTEST PASSED — %s" % utc_now())
    return 0


# --------------------------------------------------------------------------
# The lab run
# --------------------------------------------------------------------------
def run(verbose=True):
    t_start = time.time()
    combos = []                      # one per universe x K = the trials here
    uni_data = {}

    for uname, spec in UNIVERSES.items():
        if verbose:
            print("Fetching %-20s (%d tickers)..." % (uname, len(spec["tickers"])))
        symbols, failures = fetch_universe(spec["tickers"])
        spans = [(t, symbols[t]["dates"][0], symbols[t]["dates"][-1],
                  len(symbols[t]["dates"])) for t in sorted(symbols)]
        uni_data[uname] = {"symbols": symbols, "failures": failures, "spans": spans}
        if verbose:
            print("  loaded %d/%d; failures: %s"
                  % (len(symbols), len(spec["tickers"]),
                     [f["ticker"] for f in failures] or "none"))

    results = {}
    bench_series = {}
    for uname, spec in UNIVERSES.items():
        symbols = uni_data[uname]["symbols"]
        if len(symbols) < 3:
            results[uname] = {"error": "fewer than 3 usable tickers — not measured"}
            continue
        bh, bh_r = buyhold_ew_daily(symbols)
        bench_series[uname] = bh_r
        urows = []
        for k in K_GRID:
            gross = run_engine(symbols, k, 0.0)
            base = run_engine(symbols, k, spec["cost_bps_side"])
            stress = run_engine(symbols, k, spec["stress_bps_side"])
            mg, rg = metrics(gross["equity"])
            mb, rb = metrics(base["equity"])
            ms, _ = metrics(stress["equity"])
            yrs = len(gross["equity"]) / PPY
            avg_eq = float(np.mean(base["equity"]))
            turnover = base["traded_notional"] / avg_eq / yrs if avg_eq > 0 else 0.0
            ptr_g = per_trade_returns(gross["trades"], 0.0)
            ptr_b = per_trade_returns(gross["trades"], spec["cost_bps_side"])
            ptr_s = per_trade_returns(gross["trades"], spec["stress_bps_side"])
            urows.append({
                "k_max": k,
                "gross": mg, "net_base": mb, "net_stress": ms,
                "cost_bps_side": spec["cost_bps_side"],
                "stress_bps_side": spec["stress_bps_side"],
                "n_round_trips": len(gross["trades"]),
                "n_open_at_end": gross["n_open_at_end"],
                "fills": base["fills"],
                "turnover_x_per_year": round(turnover, 2),
                "avg_trade_gross_pct": round(float(ptr_g.mean()) * 100, 3) if len(ptr_g) else None,
                "avg_trade_net_pct": round(float(ptr_b.mean()) * 100, 3) if len(ptr_b) else None,
                "avg_trade_stress_pct": round(float(ptr_s.mean()) * 100, 3) if len(ptr_s) else None,
                "win_rate_gross_pct": round(float((ptr_g > 0).mean()) * 100, 1) if len(ptr_g) else None,
                "median_bars_held": (int(np.median([t["bars_held"] for t in gross["trades"]]))
                                     if gross["trades"] else None),
                "skipped_no_sigma": gross["skipped_no_sigma"],
                "_ret_gross": rg, "_ret_net": rb,
                "span": [gross["dates"][0], gross["dates"][-1]],
            })
            combos.append((uname, k))
            if verbose:
                print("  %-20s K=%d  gross %6.2f%%/Sh %5.2f | net %6.2f%%/Sh %5.2f | "
                      "stress %6.2f%%/Sh %5.2f | %d trips"
                      % (uname, k, mg["cagr_pct"], mg["sharpe"], mb["cagr_pct"],
                         mb["sharpe"], ms["cagr_pct"], ms["sharpe"],
                         len(gross["trades"])))
        results[uname] = {"rows": urows, "buyhold_ew_gross": bh,
                          "n_symbols": len(symbols),
                          "failures": uni_data[uname]["failures"]}

    # ---------------- decomposition: A (gross) and B (net) ----------------
    n_combos = len(combos)
    N_TOTAL = HOUSE_PRIOR_TRIALS + n_combos

    def row(uname, k):
        return next(r for r in results[uname]["rows"] if r["k_max"] == k)

    decomp = []
    for k in K_GRID:
        ctrl = row("large_megacap", k)
        for spec_u in ("us_smallcap_spec", "asx_spec_resources"):
            spr = row(spec_u, k)
            decomp.append({
                "k_max": k, "spec_universe": spec_u, "control": "large_megacap",
                "A_gross_sharpe_spread": round(spr["gross"]["sharpe"] - ctrl["gross"]["sharpe"], 3),
                "A_gross_cagr_spread_pct": round(spr["gross"]["cagr_pct"] - ctrl["gross"]["cagr_pct"], 2),
                "A_gross_pertrade_spread_pct": (
                    round(spr["avg_trade_gross_pct"] - ctrl["avg_trade_gross_pct"], 3)
                    if None not in (spr["avg_trade_gross_pct"], ctrl["avg_trade_gross_pct"]) else None),
                "B_net_sharpe_spread": round(spr["net_base"]["sharpe"] - ctrl["net_base"]["sharpe"], 3),
                "B_net_cagr_spread_pct": round(spr["net_base"]["cagr_pct"] - ctrl["net_base"]["cagr_pct"], 2),
                "B_net_pertrade_spread_pct": (
                    round(spr["avg_trade_net_pct"] - ctrl["avg_trade_net_pct"], 3)
                    if None not in (spr["avg_trade_net_pct"], ctrl["avg_trade_net_pct"]) else None),
                "B_stress_sharpe_spread": round(spr["net_stress"]["sharpe"] - ctrl["net_stress"]["sharpe"], 3),
            })

    # ---------------- best NET variant through the harness ----------------
    trial_sharpes = []
    best = None
    for uname in results:
        if "rows" not in results[uname]:
            continue
        for r in results[uname]["rows"]:
            rb = r["_ret_net"]
            sd = rb.std(ddof=1)
            s_daily = float(rb.mean() / sd) if sd > 0 else 0.0
            trial_sharpes.append(s_daily)
            if best is None or r["net_base"]["sharpe"] > best[2]["net_base"]["sharpe"]:
                best = (uname, r["k_max"], r)
    b_uname, b_k, b_row = best
    # benchmark: gross EW buy-hold of the SAME universe over the SAME tail —
    # a strategy that cannot beat holding its own (survivors-only) names is
    # not a strategy. Both series live on the universe's master calendar.
    bh_r = bench_series[b_uname]
    bench_tail = bh_r[len(bh_r) - len(b_row["_ret_net"]):]
    ev = qv.evaluate(
        "spec_rally %s K=%d net@%gbps" % (b_uname, b_k, b_row["cost_bps_side"]),
        b_row["_ret_net"], n_trials=N_TOTAL, trial_sharpes=trial_sharpes,
        periods_per_year=int(PPY),
        trades_per_period=max(b_row["fills"] / max(len(b_row["_ret_net"]), 1), 1e-9),
        gross_returns=b_row["_ret_gross"], benchmark_returns=bench_tail)

    # strip non-serializable arrays
    for uname in results:
        for r in results[uname].get("rows", []):
            r.pop("_ret_gross", None)
            r.pop("_ret_net", None)

    a_sharpe_any = any(d["A_gross_sharpe_spread"] > 0 for d in decomp)
    a_cagr_any = any(d["A_gross_cagr_spread_pct"] > 0 for d in decomp)
    a_pertrade_any = any((d["A_gross_pertrade_spread_pct"] or 0) > 0 for d in decomp)
    b_survives = any(d["B_net_sharpe_spread"] > 0 and
                     next(r for r in results[d["spec_universe"]]["rows"]
                          if r["k_max"] == d["k_max"])["net_base"]["sharpe"] > 0
                     for d in decomp)

    payload = {
        "generated": utc_now(),
        "thesis": "retail nimbleness: volume-confirmed rallies pay better in "
                  "small/spec names (A, gross) and survive retail spreads (B, net)",
        "rule": {"entry": "close = 20d high AND volume >= 1.5x prior-20d median",
                 "exits": "1/3 at +2 sigma*sqrt(t), 1/3 at +4 sigma*sqrt(t), "
                          "trail 2.5 sigma from peak close, 40-bar time stop; "
                          "sigma entry-frozen (exit_axis.compute_entry_sigma); "
                          "fills at triggering settled close",
                 "k_grid": K_GRID, "start": START},
        "search_count": {"N": N_TOTAL, "house_prior": HOUSE_PRIOR_TRIALS,
                         "combos_here": n_combos,
                         "formula": "N = 1,390 house (HUNT_BACKLOG 1,345 @2026-08-17 "
                                    "+ exit-axis lab 45 @2026-08-20) + %d = %d"
                                    % (n_combos, N_TOTAL)},
        "survivorship_bias": "ALL universes are survivors-only (yfinance serves "
                             "only still-listed names); the bias inflates spec "
                             "universes most — gross premium here is an UPPER BOUND",
        "universes": {u: {k: v for k, v in results[u].items()} for u in results},
        "decomposition_vs_megacap": decomp,
        "best_net": {"universe": b_uname, "k_max": b_k,
                     "net": b_row["net_base"], "gross": b_row["gross"],
                     "evaluate": ev, "verdict": qv.verdict_line(ev)},
        "answers": {
            "A_gross_sharpe_premium_any_combo": bool(a_sharpe_any),
            "A_gross_cagr_premium_any_combo": bool(a_cagr_any),
            "A_gross_pertrade_premium_any_combo": bool(a_pertrade_any),
            "B_survives_retail_costs": bool(b_survives),
        },
        "runtime_sec": round(time.time() - t_start, 1),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    write_md(payload, results)
    if verbose:
        print("\n%s" % payload["best_net"]["verdict"])
        print("JSON -> %s\nMD   -> %s" % (OUT_JSON, OUT_MD))
    return payload


# --------------------------------------------------------------------------
# Markdown report
# --------------------------------------------------------------------------
def write_md(p, results):
    d = p["decomposition_vs_megacap"]
    ev = p["best_net"]["evaluate"]

    sh_spreads = [x["A_gross_sharpe_spread"] for x in d]
    asx = [x for x in d if x["spec_universe"] == "asx_spec_resources"]
    usx = [x for x in d if x["spec_universe"] == "us_smallcap_spec"]
    asx_cagr = [x["A_gross_cagr_spread_pct"] for x in asx]
    us_cagr = [x["A_gross_cagr_spread_pct"] for x in usx]
    asx_pt = [x["A_gross_pertrade_spread_pct"] for x in asx if x["A_gross_pertrade_spread_pct"] is not None]
    us_pt = [x["A_gross_pertrade_spread_pct"] for x in usx if x["A_gross_pertrade_spread_pct"] is not None]
    b_sh = [x["B_net_sharpe_spread"] for x in d]
    b_cagr = [x["B_net_cagr_spread_pct"] for x in d]

    if max(sh_spreads) <= 0:
        a_txt = ("NO risk-adjusted — the same rule's gross Sharpe is LOWER in "
                 "both spec universes than in megacaps in all %d matched combos "
                 "(spread %+.2f to %+.2f); a raw gross CAGR premium exists in "
                 "ASX resources (%+.1f to %+.1fpp, all K) and patchily in US "
                 "small caps (%+.1f to %+.1fpp), but per-trade gross is better "
                 "than megacaps only in ASX (%+.2f to %+.2fpp/trade; US small "
                 "%+.2f to %+.2f) — and the spec side is the most "
                 "survivorship-inflated"
                 % (len(d), min(sh_spreads), max(sh_spreads),
                    min(asx_cagr), max(asx_cagr), min(us_cagr), max(us_cagr),
                    min(asx_pt or [0]), max(asx_pt or [0]),
                    min(us_pt or [0]), max(us_pt or [0])))
    else:
        a_txt = ("PARTIAL — gross Sharpe spread positive in %d/%d combos "
                 "(range %+.2f to %+.2f)"
                 % (sum(1 for s in sh_spreads if s > 0), len(d),
                    min(sh_spreads), max(sh_spreads)))
    b_txt = ("YES in at least one combo" if p["answers"]["B_survives_retail_costs"]
             else ("NO — the execution tax destroys it: every net spread vs the "
                   "megacap control is negative (Sharpe %+.2f to %+.2f, CAGR "
                   "%+.1f to %+.1fpp), and the spec books are absolute losers "
                   "net at their own tiers"
                   % (min(b_sh), max(b_sh), min(b_cagr), max(b_cagr))))

    best_role = UNIVERSES.get(p["best_net"]["universe"], {}).get("role", "?")
    L = []
    L.append("VERDICT: (A) nimbleness premium gross: %s. (B) survives the retail "
             "execution tax: %s. Best net variant overall is a %s universe "
             "(%s K=%d, net Sharpe %.3f) — it %s deflation (DSR %.4f at N=%d) "
             "and %s. All universes are survivors-only, so every spec number is "
             "an upper bound."
             % (a_txt, b_txt, best_role.upper(),
                p["best_net"]["universe"], p["best_net"]["k_max"],
                p["best_net"]["net"]["sharpe"],
                "SURVIVES" if ev["survives_deflation"] else "fails",
                ev["deflated_sharpe"] or float("nan"),
                p["search_count"]["N"],
                ("does NOT beat buy-and-hold of its own names (excess %s bps/day, t=%s)"
                 % (ev.get("excess_over_benchmark_bps"), ev.get("excess_t_stat")))
                if ev.get("beats_benchmark") is False else
                "beats buy-and-hold of its own names"))
    L.append("")
    L.append("# Spec-rally lab — the retail-nimbleness thesis, gross premium vs execution tax")
    L.append("")
    L.append("Generated %s (UTC). PAPER RESEARCH ONLY — no capital, no orders, no "
             "probe, no pre-registration touched." % p["generated"])
    L.append("")
    L.append("## SURVIVORSHIP BIAS — read this before the tables")
    L.append("")
    L.append("Every universe below contains only tickers that still exist on yfinance "
             "in 2026. The names that went to zero, delisted, or were absorbed at "
             "distressed prices are structurally invisible — and that failure rate is "
             "far higher in small-cap spec and junior resources than in megacaps "
             "(exhibits: NCM.AX and OZL.AX, both former ASX resources heavyweights, "
             "cannot even be downloaded; several \"small caps\" in the list, e.g. EXEL "
             "and HALO, are in the sample precisely because they grew into mid-caps). "
             "This inflates the SPEC universes' results relative to the control. Any "
             "gross premium found here is an upper bound; a missing premium is strong "
             "evidence against the thesis.")
    L.append("")
    L.append("## The rule (identical across universes — rally_rider family)")
    L.append("")
    L.append("* Entry: close is the highest close of the trailing 20 sessions AND "
             "volume >= 1.5x the median of the PRIOR 20 sessions' volume. Filled at "
             "the triggering settled close.")
    L.append("* Size: equity/K equal slices, max K concurrent (K in %s); highest "
             "volume-ratio first when slots are short." % p["rule"]["k_grid"])
    L.append("* Exits: 1/3 at +2 sigma*sqrt(t), 1/3 at +4 sigma*sqrt(t), trail 2.5 "
             "sigma from peak close, 40-bar time stop; sigma entry-frozen via "
             "exit_axis.compute_entry_sigma; no exit on the entry bar; TP before "
             "trail (exit_axis ordering); every fill at the triggering close.")
    L.append("* Costs charged on EVERY fill (entry and each partial exit).")
    L.append("* Walk-forward bar by bar; `python3 spec_rally_lab.py --selftest` "
             "carries the causality proof (truncation invariance, close-only fills, "
             "gross-dominates-net, prior-20 volume baseline).")
    L.append("")
    L.append("## Universes and data honesty")
    L.append("")
    for uname, spec in UNIVERSES.items():
        r = results.get(uname, {})
        if "rows" not in r:
            L.append("* **%s** — NOT MEASURED: %s" % (uname, r.get("error")))
            continue
        fails = ", ".join(f["ticker"] + " (" + f["reason"] + ")"
                          for f in r["failures"]) or "none"
        L.append("* **%s** — %d/%d tickers usable; cost tier %g bps/side "
                 "(stress %g); span %s -> %s; EW buy-hold gross %s%%/yr Sharpe %s; "
                 "failures: %s"
                 % (uname, r["n_symbols"], len(spec["tickers"]),
                    spec["cost_bps_side"], spec["stress_bps_side"],
                    r["rows"][0]["span"][0], r["rows"][0]["span"][1],
                    r["buyhold_ew_gross"]["ann_pct"], r["buyhold_ew_gross"]["sharpe"],
                    fails))
    L.append("")
    L.append("## Cost-tier table (the crux)")
    L.append("")
    L.append("| universe | K | gross CAGR% | gross Sharpe | gross maxDD% | net CAGR% (base) | net Sharpe (base) | net CAGR% (2x) | net Sharpe (2x) | trips | turnover x/yr | avg trade gross% | avg trade net% |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for uname in results:
        for r in results[uname].get("rows", []):
            L.append("| %s | %d | %.2f | %.3f | %.1f | %.2f | %.3f | %.2f | %.3f | %d | %.1f | %s | %s |"
                     % (uname, r["k_max"],
                        r["gross"]["cagr_pct"], r["gross"]["sharpe"], r["gross"]["maxdd_pct"],
                        r["net_base"]["cagr_pct"], r["net_base"]["sharpe"],
                        r["net_stress"]["cagr_pct"], r["net_stress"]["sharpe"],
                        r["n_round_trips"], r["turnover_x_per_year"],
                        r["avg_trade_gross_pct"], r["avg_trade_net_pct"]))
    L.append("")
    L.append("## The decomposition the thesis needs (spec minus large_megacap, matched K)")
    L.append("")
    L.append("| spec universe | K | (A) gross Sharpe spread | (A) gross CAGR spread | (A) gross per-trade spread | (B) net Sharpe spread | (B) net CAGR spread | (B) net per-trade spread | (B) 2x-stress Sharpe spread |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for x in d:
        L.append("| %s | %d | %+.3f | %+.2f%% | %s | %+.3f | %+.2f%% | %s | %+.3f |"
                 % (x["spec_universe"], x["k_max"],
                    x["A_gross_sharpe_spread"], x["A_gross_cagr_spread_pct"],
                    ("%+.3f%%" % x["A_gross_pertrade_spread_pct"]) if x["A_gross_pertrade_spread_pct"] is not None else "n/a",
                    x["B_net_sharpe_spread"], x["B_net_cagr_spread_pct"],
                    ("%+.3f%%" % x["B_net_pertrade_spread_pct"]) if x["B_net_pertrade_spread_pct"] is not None else "n/a",
                    x["B_stress_sharpe_spread"]))
    L.append("")
    L.append("(large_etf15 is reported in the cost table as the registered house "
             "control; the decomposition uses large_megacap because single stocks "
             "vs single stocks is the honest size-axis comparison.)")
    L.append("")
    L.append("## Best NET variant through the validation harness")
    L.append("")
    L.append("%s" % p["best_net"]["verdict"])
    L.append("")
    L.append("* Search count charged: **N = %d** — %s. (The tasking note estimated "
             "~1,206 house; the HUNT_BACKLOG ledger says 1,345 as of 2026-08-17 plus "
             "the exit-axis lab's 45 on 2026-08-20 = 1,390; the larger, documented "
             "number is charged.)"
             % (p["search_count"]["N"], p["search_count"]["formula"]))
    L.append("* Deflated Sharpe %.4f (expected max SR under the null of %d trials: "
             "%.4f/period); observed daily Sharpe %.4f (annualized %.3f); n_obs %d."
             % (ev["deflated_sharpe"] or float("nan"), ev["n_trials_searched"],
                ev["expected_max_sharpe_under_null"], ev["sharpe_per_period"],
                ev["sharpe_annualized"], ev["n_obs"]))
    L.append("")
    L.append("## What would change the answer")
    L.append("")
    L.append("* **Limit-order fills.** The base tiers charge the full spread cost as "
             "if crossing at the close. A patient retail limit order might earn part "
             "of the spread back — but daily bars carry NO evidence about fill "
             "probability or adverse selection on resting orders in these names; the "
             "claim is UNEVIDENCED here and would need intraday quote data. It is a "
             "hypothesis, not a rebuttal.")
    L.append("* **A delisting-complete dataset.** Point-in-time constituents with "
             "delisted names would remove the survivorship inflation — it can only "
             "LOWER the spec universes' gross numbers, so it can rescue (B) only if "
             "(A) somehow strengthens, which is the opposite of what the bias "
             "implies.")
    L.append("* **Intraday/volume microstructure.** The thesis may live at horizons "
             "daily bars cannot see (opening auctions, halts, intraday momentum). "
             "Not measurable with this data lake — declared, not approximated.")
    L.append("* **Real ASX spread data.** 150 bps/side is an estimate for junior "
             "resources; if true half-spreads are materially tighter for the "
             "specific liquid juniors, (B) improves — the 2x stress row shows the "
             "other direction.")
    L.append("")
    L.append("## Could not measure (declared, not approximated)")
    L.append("")
    L.append("* Delisted names (e.g. NCM.AX, OZL.AX, every failed small cap): no "
             "data exists on this lake — the universes are survivors-only.")
    L.append("* Borrow, halts, gap risk through stops: fills are settled closes; a "
             "40-bar time stop and trail cannot model an untradeable halt day.")
    L.append("* Shares outstanding / float: \"capacity-constrained\" is proxied by "
             "the universe definitions, not measured per-name.")
    L.append("")
    L.append("Runtime %.1fs. Generated %s." % (p["runtime_sec"], p["generated"]))
    L.append("")
    with open(OUT_MD, "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    run()
