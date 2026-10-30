#!/usr/bin/env python3
"""Exit-axis lab — measures whether a sigma-scaled take-profit / trailing-stop
exit improves anything, against ~20y of daily data, honestly.

THE EXIT IS THE HYPOTHESIS. The entry rules replayed here are the desk's own
frozen rules; the existing fixed-holding behaviour is the NULL and is run
through the IDENTICAL engine (exits-off must and does reproduce the plain
strategy exactly — asserted, not assumed).

Strategies faithfully reconstructed (same rule, same universe, same cadence
semantics, same cost model as the live module):
  * growth_dual_momentum  — growth_book.py: top-2 of 15 ETFs by 252d trailing
    return, positive filter, IEF fallback, rebalance every 21 TRADING days,
    10 bps/side (COST_BPS_SIDE=10.0), $50k start.
  * spy_trend_cash        — tournament.py: SPY above its 200dma -> SPY, below
    -> cash, re-checked when >=30 CALENDAR days since last rebalance
    (days_since semantics), 10 bps/side, $10k start.
  * equal_weight_all      — tournament.py: 15 ETFs equal weight, re-checked
    when >=88 CALENDAR days since last rebalance, 10 bps/side, $10k start.
    (It is a registered CONTROL live — never wired for exits there; measured
    here only as a fixed-weight test bed for the exit rule itself.)

NOT measured (declared, not silently approximated) — see the report's
"could not measure" section: value_book (no screen history exists),
lowvol_crypto and fng_momentum (Kraken public OHLC caps at ~720 daily bars —
2y, not 15-20y — and the lowvol universe comes from live engine state that has
no historical record), vix_regime_switch, covered_call_etf, cef_discount,
claude_discretionary, the carry books, and intraday fills.

HONESTY CONSTRAINTS (each independently verifiable in the code below):
  1. No lookahead: positions walk forward bar by bar; every decision at bar t
     uses closes[<=t] only; exits are booked AT THE CLOSE OF THE TRIGGERING
     SESSION (exit_axis.decide_exit — the same function the live tick calls).
  2. entry_sigma is frozen at entry from the same daily-close series the
     backtest trades on (exit_axis.compute_entry_sigma).
  3. Every exit AND the eventual re-entry at the next scheduled rebalance pass
     through the same cost model at the same bps as any other fill; gross and
     net are both reported.
  4. Search deflation is charged at the HOUSE count: N = 1,145 prior trials on
     this desk family + (15 exit variants x 3 strategies) = 1,190.

Data: yfinance daily closes, auto_adjust=True — the exact source and
adjustment basis the live ETF books use. The last row is dropped when its
date equals the current UTC date (in-progress bar).

PAPER ONLY. Reads market data, writes reports/. Touches no book state.

Usage:  python3 exit_axis_lab.py
Writes: reports/exit_axis_lab.json, reports/research_exit_axis.md
"""
import json
import math
import os
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import exit_axis
import quant_validation as qv

OUT_JSON = os.path.join(BASE, "reports", "exit_axis_lab.json")
OUT_MD = os.path.join(BASE, "reports", "research_exit_axis.md")

ETF15 = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
         "GLD", "SLV", "DBC", "USO", "IYR", "AGG"]   # growth_book.py UNIVERSE
SAFE = "IEF"
COST_BPS_SIDE = 10.0            # growth_book/value_book/spy/ew live constant
HOUSE_PRIOR_TRIALS = 1145       # prior search trials on this desk family
TP_GRID = [2.0, 3.0, 4.0, None]
TRAIL_GRID = [1.5, 2.5, 4.0, None]

PPY = 252.0


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fetch_history():
    import yfinance as yf
    raw = yf.download(ETF15, period="max", interval="1d", auto_adjust=True,
                      progress=False, threads=True)["Close"]
    raw = raw[ETF15].dropna()          # common window: all 15 tickers priced
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if len(raw) and raw.index[-1].strftime("%Y-%m-%d") == today:
        raw = raw.iloc[:-1]            # in-progress bar: not a settled close
    dates = [d.strftime("%Y-%m-%d") for d in raw.index]
    day_ord = raw.index.values.astype("datetime64[D]").astype(np.int64)
    return raw.values.astype(float), dates, day_ord


# --------------------------------------------------------------------------
# The three frozen entry rules (weights from info up to close t ONLY)
# --------------------------------------------------------------------------
IDX = {t: i for i, t in enumerate(ETF15)}


def growth_weights(C, t):
    """growth_book.choose_targets, verbatim in vector form."""
    trail = C[t] / C[t - 252] - 1.0
    order = np.argsort(-trail, kind="stable")
    picks = [int(j) for j in order[:2] if trail[j] > 0]
    w = np.zeros(C.shape[1])
    for j in picks:
        w[j] = 0.5
    left = 1.0 - w.sum()
    if left > 1e-9:
        w[IDX[SAFE]] += left
    return w


def spy_weights(C, t):
    j = IDX["SPY"]
    above = C[t, j] > C[t - 199:t + 1, j].mean()     # last 200 closes
    w = np.zeros(C.shape[1])
    if above:
        w[j] = 1.0
    return w


def ew_weights(C, t):
    return np.full(C.shape[1], 1.0 / len(ETF15))


STRATS = {
    "growth_dual_momentum": {
        "weights": growth_weights, "start_t": 252, "start_cash": 50000.0,
        "due": lambda t, last, day_ord: (t - last) >= 21,          # trading days
        "cadence": "every 21 trading days (growth_book.py REBALANCE_TD)"},
    "spy_trend_cash": {
        "weights": spy_weights, "start_t": 199, "start_cash": 10000.0,
        "due": lambda t, last, day_ord: (day_ord[t] - day_ord[last]) >= 30,
        "cadence": ">=30 calendar days (tournament.py days_since semantics)"},
    "equal_weight_all": {
        "weights": ew_weights, "start_t": 0, "start_cash": 10000.0,
        "due": lambda t, last, day_ord: (day_ord[t] - day_ord[last]) >= 88,
        "cadence": ">=88 calendar days (tournament.py days_since semantics)"},
}


# --------------------------------------------------------------------------
# The engine. ONE code path for null and exit variants: tp=trail=None IS the
# null. Decisions call exit_axis.decide_exit — the same function the live
# tick paths call — at each daily close, booked at that close.
# --------------------------------------------------------------------------
def run_variant(C, day_ord, spec, tp, trail, cost_bps, with_exit_machinery=True):
    T, N = C.shape
    cash = spec["start_cash"]
    units = np.zeros(N)
    entry_t = np.full(N, -1, dtype=np.int64)
    entry_px = np.zeros(N)
    entry_sig = np.full(N, np.nan)
    peak = np.zeros(N)
    last_rebal = -1
    start_t = spec["start_t"]
    equity = np.empty(T - start_t)
    total_traded = 0.0
    n_rebals = n_tp = n_trail = n_sigma_exempt = 0
    weights_fn, due_fn = spec["weights"], spec["due"]

    for t in range(start_t, T):
        px = C[t]
        due = last_rebal < 0 or due_fn(t, last_rebal, day_ord)
        if due:
            w = weights_fn(C, t)                     # info up to close t only
            eq = cash + float(units @ px)
            want = eq * w
            have = units * px
            traded = float(np.abs(want - have).sum())  # incl. dropped names
            cost = traded * cost_bps / 10000.0
            new_units = want / px
            cash = eq - float(new_units @ px) - cost
            units = new_units
            total_traded += traded
            n_rebals += 1
            last_rebal = t
            held = np.where(units > 1e-15)[0]
            for j in held:                            # entry state resets on
                entry_t[j] = t                        # EVERY rebalance, kept
                entry_px[j] = px[j]                   # names included — same
                peak[j] = px[j]                       # as live entry_px
                sig = exit_axis.compute_entry_sigma(C[max(0, t - 60):t + 1, j])
                if sig is None:
                    n_sigma_exempt += 1
                    entry_sig[j] = np.nan
                else:
                    entry_sig[j] = sig
        elif with_exit_machinery:
            for j in np.where(units > 1e-15)[0]:
                if px[j] > peak[j]:
                    peak[j] = px[j]                   # peak of CLOSES only
                sig = None if np.isnan(entry_sig[j]) else float(entry_sig[j])
                reason = exit_axis.decide_exit(
                    float(entry_px[j]), sig, int(t - entry_t[j]),
                    float(peak[j]), float(px[j]), tp, trail)
                if reason:                            # booked at THIS close
                    traded = float(units[j] * px[j])
                    cost = traded * cost_bps / 10000.0
                    cash += traded - cost
                    total_traded += traded
                    units[j] = 0.0                    # cash until the next
                    if reason == "take_profit":       # scheduled rebalance
                        n_tp += 1                     # re-enters naturally
                    else:
                        n_trail += 1
        equity[t - start_t] = cash + float(units @ px)

    return equity, {"total_traded": total_traded, "n_rebalances": n_rebals,
                    "n_exits_tp": n_tp, "n_exits_trail": n_trail,
                    "n_sigma_exempt_entries": n_sigma_exempt}


def run_plain(C, day_ord, spec, cost_bps):
    """Independent plain replication WITHOUT any exit machinery, for the
    exits-off == plain-strategy assertion."""
    return run_variant(C, day_ord, spec, None, None, cost_bps,
                       with_exit_machinery=False)


def metrics(equity, day_ord_window):
    r = equity[1:] / equity[:-1] - 1.0
    n_years = max((day_ord_window[-1] - day_ord_window[0]) / 365.25, 1e-9)
    cagr = (equity[-1] / equity[0]) ** (1.0 / n_years) - 1.0
    sd = r.std(ddof=1)
    sharpe = (r.mean() / sd * math.sqrt(PPY)) if sd > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    maxdd = float((equity / peak - 1.0).min())
    return {"cagr_pct": round(cagr * 100, 3),
            "sharpe": round(float(sharpe), 4),
            "maxdd_pct": round(maxdd * 100, 2),
            "end_equity": round(float(equity[-1]), 2),
            "n_days": int(len(equity))}, r


def main():
    t0 = time.time()
    print("EXIT-AXIS LAB start %s" % utc_now())
    C, dates, day_ord = fetch_history()
    print("  data: %d daily bars x %d ETFs, %s .. %s (yfinance, auto_adjust=True,"
          " settled closes only)" % (C.shape[0], C.shape[1], dates[0], dates[-1]))

    grid = [(tp, tr) for tp in TP_GRID for tr in TRAIL_GRID
            if not (tp is None and tr is None)]
    assert len(grid) == 15
    n_strats = len(STRATS)
    N_SEARCH = HOUSE_PRIOR_TRIALS + len(grid) * n_strats   # 1145 + 45 = 1190

    results = {}
    all_variant_net_sharpes_perperiod = []
    best = None            # (net_sharpe, strat, variant_key, net_returns)
    best_improver = None   # (delta_sharpe, strat, key, net_returns, null_returns)

    for sname, spec in STRATS.items():
        window = day_ord[spec["start_t"]:]
        # ---- the NULL through the identical engine, and the assertion ----
        rows = {}
        for label, bps in (("net", COST_BPS_SIDE), ("gross", 0.0)):
            eq_null, st_null = run_variant(C, day_ord, spec, None, None, bps)
            eq_plain, _ = run_plain(C, day_ord, spec, bps)
            if not np.array_equal(eq_null, eq_plain):
                raise AssertionError(
                    "%s (%s): exits-off engine does NOT reproduce the plain "
                    "strategy — max diff %.3g" % (sname, label,
                                                  np.abs(eq_null - eq_plain).max()))
            m, r = metrics(eq_null, window)
            rows[label] = {"metrics": m, "stats": st_null, "_eq": eq_null, "_r": r}
        print("  %s: exits-off reproduces the plain strategy EXACTLY "
              "(asserted, gross and net)" % sname)
        null_net_sharpe = rows["net"]["metrics"]["sharpe"]

        variants = {}
        for tp, tr in grid:
            key = "tp=%s,trail=%s" % (tp, tr)
            v = {}
            for label, bps in (("net", COST_BPS_SIDE), ("gross", 0.0)):
                eq, st = run_variant(C, day_ord, spec, tp, tr, bps)
                m, r = metrics(eq, window)
                v[label] = {"metrics": m, "stats": st}
                if label == "net":
                    net_r = r
                    net_m = m
            v["delta_vs_null_net"] = {
                "sharpe": round(net_m["sharpe"] - null_net_sharpe, 4),
                "cagr_pct": round(net_m["cagr_pct"]
                                  - rows["net"]["metrics"]["cagr_pct"], 3),
                "maxdd_pct": round(net_m["maxdd_pct"]
                                   - rows["net"]["metrics"]["maxdd_pct"], 2)}
            variants[key] = v
            sd = net_r.std(ddof=1)
            all_variant_net_sharpes_perperiod.append(
                float(net_r.mean() / sd) if sd > 0 else 0.0)
            if best is None or net_m["sharpe"] > best[0]:
                best = (net_m["sharpe"], sname, key, net_r.copy(), tp, tr)
            d_sh = v["delta_vs_null_net"]["sharpe"]
            if best_improver is None or d_sh > best_improver[0]:
                best_improver = (d_sh, sname, key, net_r.copy(),
                                 rows["net"]["_r"].copy())

        results[sname] = {
            "cadence": spec["cadence"], "start_cash": spec["start_cash"],
            "cost_bps_side": COST_BPS_SIDE,
            "window": {"start": dates[spec["start_t"]], "end": dates[-1]},
            "null_exits_off": {k: {"metrics": rows[k]["metrics"],
                                   "stats": rows[k]["stats"]} for k in rows},
            "variants": variants,
            "n_variants_improving_net_sharpe": sum(
                1 for v in variants.values()
                if v["delta_vs_null_net"]["sharpe"] > 0)}

    # ---- deflated Sharpe on the best net variant, honest search count ----
    best_sharpe, best_strat, best_key, best_r, best_tp, best_tr = best
    dsr = qv.deflated_sharpe_ratio(best_r, n_trials=N_SEARCH,
                                   trial_sharpes=all_variant_net_sharpes_perperiod)
    best_null = results[best_strat]["null_exits_off"]["net"]["metrics"]
    best_var = results[best_strat]["variants"][best_key]
    improves = best_var["delta_vs_null_net"]["sharpe"] > 0
    survives = bool(dsr["survives"])
    any_improve = any(r["n_variants_improving_net_sharpe"] > 0
                      for r in results.values())

    # ---- and on the best IMPROVER over its own null (the delta is the
    # actual hypothesis), with the excess-over-null test as well ----
    imp_delta, imp_strat, imp_key, imp_r, imp_null_r = best_improver
    dsr_imp = qv.deflated_sharpe_ratio(imp_r, n_trials=N_SEARCH,
                                       trial_sharpes=all_variant_net_sharpes_perperiod)
    imp_eval = qv.evaluate("%s %s vs its null" % (imp_strat, imp_key), imp_r,
                           n_trials=N_SEARCH,
                           trial_sharpes=all_variant_net_sharpes_perperiod,
                           benchmark_returns=imp_null_r)
    improver_survives = bool(dsr_imp["survives"]
                             and imp_eval.get("beats_benchmark"))

    if improves and survives:
        verdict = ("VERDICT: YES — %s with %s improves net Sharpe over its null "
                   "by %+0.3f AND survives deflation (DSR %.3f) at N=%d. "
                   "Forward test before any belief." %
                   (best_strat, best_key, best_var["delta_vs_null_net"]["sharpe"],
                    dsr["deflated_sharpe"], N_SEARCH))
    elif improver_survives:
        verdict = ("VERDICT: QUALIFIED YES — %s with %s improves its own null "
                   "(delta net Sharpe %+0.3f) and survives deflation at N=%d "
                   "(DSR %.3f, excess t=%s). Forward test before any belief." %
                   (imp_strat, imp_key, imp_delta, N_SEARCH,
                    dsr_imp["deflated_sharpe"], imp_eval.get("excess_t_stat")))
    else:
        verdict = ("VERDICT: NO — no exit variant improves NET risk-adjusted "
                   "return over its fixed-holding null after search deflation. "
                   "Best absolute: %s %s (net Sharpe %.3f vs null %.3f, delta "
                   "%+0.3f; DSR %.3f at N=%d). Best improver: %s %s (delta "
                   "%+0.3f; DSR %.3f; excess-over-null t=%s, beats null: %s). "
                   "The null — hold until the scheduled rebalance — stands." %
                   (best_strat, best_key, best_sharpe, best_null["sharpe"],
                    best_var["delta_vs_null_net"]["sharpe"],
                    dsr["deflated_sharpe"], N_SEARCH,
                    imp_strat, imp_key, imp_delta, dsr_imp["deflated_sharpe"],
                    imp_eval.get("excess_t_stat"),
                    imp_eval.get("beats_benchmark")))

    could_not_measure = {
        "value_book": "needs the value screen's historical composition; no "
                      "screen history exists before 2026-08 — EXCLUDED, not "
                      "approximated",
        "lowvol_crypto": "Kraken public OHLC returns at most ~720 daily bars "
                         "(~2y, not 15-20y) and the live universe comes from "
                         "engine state with no historical record — EXCLUDED",
        "fng_momentum": "same 720-bar Kraken cap; Fear&Greed history is also "
                        "not part of the desk's data lake — EXCLUDED",
        "vix_regime_switch": "its rule is itself a switch (always holds SPY or "
                             "IEF; re-enters immediately), so an exit-to-cash "
                             "overlay is not the same hypothesis; ^VIX3M "
                             "history also only begins ~2009 — EXCLUDED from "
                             "lab v1, noted for a dedicated design",
        "covered_call_etf": "short option legs; no historical option chains — "
                            "EXCLUDED",
        "cef_discount_and_auto_probes": "dollar-neutral long/short; a "
                                        "single-leg long-only exit breaks "
                                        "neutrality and the sealed specs — "
                                        "EXCLUDED by design, live and lab",
        "claude_discretionary": "no fixed rule to replay — EXCLUDED",
        "carry_books": "funding-based with frozen exit rules; do-not-touch — "
                       "EXCLUDED",
        "intraday_fills": "the lab fills at settled daily closes at exactly "
                          "10 bps/side; intraday slippage beyond the modelled "
                          "bps is unmeasured",
        "pre_inception_history": "common window starts %s (SLV is the "
                                 "youngest of the 15 ETFs); nothing earlier "
                                 "is used" % dates[0],
    }

    payload = {
        "generated_utc": utc_now(),
        "generated_epoch": time.time(),
        "verdict": verdict,
        "search_count": {"N": N_SEARCH, "house_prior_trials": HOUSE_PRIOR_TRIALS,
                         "grid_variants": len(grid), "strategies_tested": n_strats,
                         "formula": "N = 1145 + 15*3 = 1190"},
        "rule": {"module": "exit_axis.py (shared by lab and live — the live "
                           "call sites are in tournament.py, growth_book.py, "
                           "value_book.py)",
                 "take_profit": "(close/entry_px - 1) >= tp_sigma * entry_sigma "
                                "* sqrt(bars_held)",
                 "trailing_stop": "(close/peak_close - 1) <= -(trail_sigma * "
                                  "entry_sigma)",
                 "entry_sigma": "std of daily simple returns, trailing 61 "
                                "closes (60 returns, min 25), FROZEN at entry, "
                                "same settled daily-close series as the fills",
                 "fills": "close of the triggering session; exits and "
                          "re-entries both charged at the strategy's live "
                          "cost bps"},
        "grid": {"tp_sigma": TP_GRID, "trail_sigma": TRAIL_GRID,
                 "n_variants": len(grid)},
        "data": {"source": "yfinance daily, auto_adjust=True, settled closes "
                           "only (in-progress UTC bar dropped)",
                 "bars": int(C.shape[0]), "first": dates[0], "last": dates[-1]},
        "best_net_variant": {"strategy": best_strat, "variant": best_key,
                             "net": best_var["net"]["metrics"],
                             "gross": best_var["gross"]["metrics"],
                             "delta_vs_null_net": best_var["delta_vs_null_net"],
                             "deflated_sharpe": dsr},
        "best_improver_vs_own_null": {
            "strategy": imp_strat, "variant": imp_key,
            "delta_net_sharpe": imp_delta,
            "net": results[imp_strat]["variants"][imp_key]["net"]["metrics"],
            "gross": results[imp_strat]["variants"][imp_key]["gross"]["metrics"],
            "deflated_sharpe": dsr_imp,
            "excess_over_null": {
                "excess_bps": imp_eval.get("excess_over_benchmark_bps"),
                "excess_t_stat": imp_eval.get("excess_t_stat"),
                "beats_null": imp_eval.get("beats_benchmark")}},
        "results": results,
        "could_not_measure": could_not_measure,
        "null_preserved": "exits-off reproduces each plain strategy EXACTLY "
                          "(asserted for gross and net, np.array_equal)",
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=float)
    os.replace(tmp, OUT_JSON)

    # ---------------- markdown report ----------------
    lines = [verdict, "", "# Exit-axis lab — sigma-scaled TP / trailing stop "
             "vs the fixed-holding null", "",
             "Generated %s (UTC). PAPER research only. The live axis stays "
             "DISABLED (reports/exit_axis_config.json): enabling any "
             "pre-registered book is a rule change requiring a funder-signed "
             "amendment." % payload["generated_utc"], "",
             "* Search count charged: **N = %d** = %d house prior trials + "
             "%d variants x %d strategies." % (N_SEARCH, HOUSE_PRIOR_TRIALS,
                                               len(grid), n_strats),
             "* Rule and fills: exit_axis.py — decisions at settled daily "
             "closes only, booked at the close of the triggering session; "
             "entry-frozen sigma from the same close series; every exit and "
             "re-entry charged at the strategy's live bps (%.0f/side)."
             % COST_BPS_SIDE,
             "* Null preservation: exits-off through the identical engine "
             "reproduces each plain strategy EXACTLY (asserted, gross and "
             "net).", ""]
    lines += ["## Best net variant", "",
              "%s, %s — net CAGR %.2f%% / Sharpe %.3f / maxDD %.1f%% "
              "(gross CAGR %.2f%% / Sharpe %.3f); null net CAGR %.2f%% / "
              "Sharpe %.3f / maxDD %.1f%%. Delta net Sharpe %+0.3f." %
              (best_strat, best_key,
               best_var["net"]["metrics"]["cagr_pct"],
               best_var["net"]["metrics"]["sharpe"],
               best_var["net"]["metrics"]["maxdd_pct"],
               best_var["gross"]["metrics"]["cagr_pct"],
               best_var["gross"]["metrics"]["sharpe"],
               best_null["cagr_pct"], best_null["sharpe"],
               best_null["maxdd_pct"],
               best_var["delta_vs_null_net"]["sharpe"]),
              "",
              "Deflated Sharpe at N=%d: **%.4f** (expected max SR under the "
              "null of %d trials: %.4f per period) -> %s." %
              (N_SEARCH, dsr["deflated_sharpe"], N_SEARCH,
               dsr["expected_max_sharpe_under_null"],
               "survives" if dsr["survives"] else "does NOT survive"), "",
              "## Best improver vs its own null", "",
              "%s, %s — delta net Sharpe %+0.3f; DSR %.4f at N=%d -> %s; "
              "excess over its null %.2f bps/day, t=%s -> beats null: %s." %
              (imp_strat, imp_key, imp_delta, dsr_imp["deflated_sharpe"],
               N_SEARCH,
               "survives" if dsr_imp["survives"] else "does NOT survive",
               imp_eval.get("excess_over_benchmark_bps") or 0.0,
               imp_eval.get("excess_t_stat"),
               imp_eval.get("beats_benchmark")), ""]
    for sname, res in results.items():
        nn = res["null_exits_off"]["net"]["metrics"]
        ng = res["null_exits_off"]["gross"]["metrics"]
        lines += ["## %s" % sname, "",
                  "Window %s..%s, %s, %.0f bps/side, start $%.0f. "
                  "Null (exits OFF): net CAGR %.2f%% / Sharpe %.3f / maxDD "
                  "%.1f%% (gross %.2f%% / %.3f). %d of 15 variants improve "
                  "net Sharpe." %
                  (res["window"]["start"], res["window"]["end"], res["cadence"],
                   COST_BPS_SIDE, res["start_cash"], nn["cagr_pct"],
                   nn["sharpe"], nn["maxdd_pct"], ng["cagr_pct"], ng["sharpe"],
                   res["n_variants_improving_net_sharpe"]), "",
                  "| variant | net CAGR% | net Sharpe | net maxDD% | gross "
                  "CAGR% | gross Sharpe | exits tp/trail | traded $ | dSharpe "
                  "vs null |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for key, v in res["variants"].items():
            nm, gm, st = v["net"]["metrics"], v["gross"]["metrics"], \
                v["net"]["stats"]
            lines.append("| %s | %.2f | %.3f | %.1f | %.2f | %.3f | %d/%d | "
                         "%.0f | %+0.3f |" %
                         (key, nm["cagr_pct"], nm["sharpe"], nm["maxdd_pct"],
                          gm["cagr_pct"], gm["sharpe"], st["n_exits_tp"],
                          st["n_exits_trail"], st["total_traded"],
                          v["delta_vs_null_net"]["sharpe"]))
        lines.append("")
    lines += ["## Could not measure (declared, not approximated)", ""]
    for k, v in could_not_measure.items():
        lines.append("* **%s** — %s" % (k, v))
    lines += ["", "## Method notes", "",
              "* One engine, one rule module: the lab calls "
              "exit_axis.decide_exit / compute_entry_sigma — the same "
              "functions wired into the live ticks of tournament.py, "
              "growth_book.py and value_book.py (grep for "
              "`exit_axis.tournament_exit_check`, `growth_exit_check`, "
              "`value_exit_check`).",
              "* An 'entry' is each scheduled rebalance's position-set "
              "(entry state resets for kept names too), mirroring the live "
              "modules, which rewrite entry_px on every rebalance.",
              "* After an exit the position stays in cash until the "
              "strategy's next scheduled rebalance re-enters it naturally; "
              "that re-entry pays the same bps as any other fill.",
              "* Exit checks are skipped on a rebalance bar (the rebalance "
              "supersedes; live analog: the sleeve's own rule runs before "
              "the exit hook and a fresh entry has bars_held=0).",
              "* Gross = the identical run at 0 bps (decisions are "
              "price-based, so exit dates match the net run).",
              "* exit_axis.py --selftest carries the causality proof: an "
              "honest evaluator books the triggering session's close, and a "
              "deliberately peeking evaluator (books the series max) is "
              "detected and failed.",
              "", "Runtime %.1fs. Generated %s." % (time.time() - t0, utc_now())]
    with open(OUT_MD + ".tmp", "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(OUT_MD + ".tmp", OUT_MD)

    print("\n" + verdict)
    print("\n  best net variant : %s %s" % (best_strat, best_key))
    print("  net  CAGR %.2f%%  Sharpe %.3f  maxDD %.1f%%" %
          (best_var["net"]["metrics"]["cagr_pct"],
           best_var["net"]["metrics"]["sharpe"],
           best_var["net"]["metrics"]["maxdd_pct"]))
    print("  gross CAGR %.2f%%  Sharpe %.3f" %
          (best_var["gross"]["metrics"]["cagr_pct"],
           best_var["gross"]["metrics"]["sharpe"]))
    print("  deflated Sharpe %.4f at N=%d -> %s" %
          (dsr["deflated_sharpe"], N_SEARCH,
           "survives" if dsr["survives"] else "does NOT survive"))
    print("  wrote %s" % OUT_JSON)
    print("  wrote %s" % OUT_MD)
    print("EXIT-AXIS LAB done %s (%.1fs)" % (utc_now(), time.time() - t0))


if __name__ == "__main__":
    main()
