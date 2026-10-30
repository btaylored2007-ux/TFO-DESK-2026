#!/usr/bin/env python3
"""tape_scalp_lab.py — "sit on the tape, feel its movement, scalp the finest margins."

RESEARCH ONLY. PAPER. No orders. DB opened read-only (sqlite URI mode=ro).

THE QUESTION (funder): does ANY short-horizon edge exist in the desk's OWN tick
data — and does it survive real transaction costs?

DESIGN
  Layer 1 (descriptive, before any strategy): tick-rule signing, 1min/5min bars,
    lag-1 return autocorrelation, Spearman IC of signed-flow imbalance -> next-bar
    return, run-length statistics vs the random-walk null (Wald-Wolfowitz, summed
    across contiguous bar segments so gaps never fake runs).
  Layer 2 (strategy, honestly costed): {flow-follow, flow-fade, momentum,
    reversal} x {1min, 5min}, hold next bar, long AND short, cross-symbol
    equal-weight where signals fire. Turnover-based costing so runs live: a held
    direction pays nothing until it exits or flips. Three tiers per side:
    0 bps (fantasy ceiling), 2 bps (institutional maker), 15 bps (Kraken retail
    taker — this desk's real wall). 8 x 3 = 24 scored variants, ALL counted in
    deflation on top of the house's 1263 prior trials.

HONESTY NOTES (verified against the DB before writing this lab)
  - trade_tape holds only THREE symbols, not the ~8 hoped for:
      XBT/USD 2013-10..2016-07, ADA/USD 2018-09..2019-08, SOL/USD 2021-06..2022-04.
    The eras are pairwise DISJOINT: cross-symbol correlation is structurally zero,
    so the "portfolio" is a concatenation of eras, and the effective-N discount
    comes from serial autocorrelation of the strategy returns (measured, via
    qv.effective_bets), not from cross-sectional pooling.
  - candles cover 2024-2026 only — zero overlap with the tape — so benchmark
    alignment uses yfinance SPY dailies instead.
  - Benchmark for evaluate() is FLAT (0): a dollar-neutral long/short scalper's
    null is cash, not buy-and-hold. Stated, not hidden.
  - Nothing here is synthesized. Every bar comes from the tape.

Creates ONLY: reports/tape_scalp_lab.json (receipt), stdout tables.
The research note reports/research_tape_scalp.md is written by the researcher.
"""

import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_validation as qv  # noqa: E402

DB = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
RECEIPT = "reports/tape_scalp_lab.json"

HOUSE_PRIOR_TRIALS = 1263          # cumulative desk search prior to this lab
HORIZONS = {"1min": 60, "5min": 300}
BARS_PER_YEAR = {"1min": 525600, "5min": 105120}
TIERS_BPS_SIDE = [0.0, 2.0, 15.0]  # fantasy / institutional maker / Kraken retail taker
SIGNALS = ["flow_follow", "flow_fade", "momentum", "reversal"]


# --------------------------------------------------------------------------
# JSON-safe serializer (desk-standard _j, as used across the labs)
# --------------------------------------------------------------------------
def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, np.floating):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    return o


# --------------------------------------------------------------------------
# Data loading + characterization
# --------------------------------------------------------------------------
def load_tape(con):
    """Load the whole tape per symbol, sorted by time. Returns dict + stats."""
    syms = pd.read_sql(
        "SELECT symbol, COUNT(*) n FROM trade_tape GROUP BY symbol ORDER BY n DESC",
        con)
    tapes, stats_rows = {}, []
    for sym in syms["symbol"]:
        df = pd.read_sql(
            "SELECT ts_epoch, price, quantity FROM trade_tape "
            "WHERE symbol=? ORDER BY ts_epoch", con, params=(sym,))
        ts = df["ts_epoch"].to_numpy(float)
        span_d = (ts[-1] - ts[0]) / 86400.0
        dt = np.diff(ts)
        minutes = np.unique((ts // 60).astype(np.int64))
        grid_min = int((ts[-1] - ts[0]) // 60) + 1
        stats_rows.append({
            "symbol": sym,
            "n_trades": int(len(df)),
            "first_utc": datetime.fromtimestamp(ts[0], timezone.utc).isoformat(),
            "last_utc": datetime.fromtimestamp(ts[-1], timezone.utc).isoformat(),
            "span_days": round(span_d, 2),
            "median_intertrade_s": round(float(np.median(dt)), 3) if dt.size else None,
            "max_gap_hours": round(float(dt.max()) / 3600.0, 2) if dt.size else None,
            "pct_minutes_with_a_trade": round(100.0 * minutes.size / grid_min, 2),
        })
        tapes[sym] = df
    return tapes, stats_rows


def tick_sign(price):
    """Tick rule: uptick=+1, downtick=-1, unchanged=carry last. First trade 0."""
    s = np.sign(np.diff(price, prepend=price[0]))
    s = pd.Series(s).replace(0, np.nan).ffill().fillna(0.0)
    return s.to_numpy()


def build_bars(df, step):
    """Time bars: close (last trade), volume, tick-signed volume, trade count."""
    ts = df["ts_epoch"].to_numpy(float)
    px = df["price"].to_numpy(float)
    qty = df["quantity"].to_numpy(float)
    sgn = tick_sign(px)
    bt = (ts // step).astype(np.int64) * step
    g = pd.DataFrame({"bt": bt, "px": px, "qty": qty, "sq": sgn * qty}).groupby("bt")
    bars = pd.DataFrame({
        "close": g["px"].last(),
        "vol": g["qty"].sum(),
        "signed": g["sq"].sum(),
        "n": g["px"].count(),
    }).reset_index()
    bt = bars["bt"].to_numpy()
    close = bars["close"].to_numpy()
    adj_prev = np.zeros(len(bars), dtype=bool)
    adj_prev[1:] = (bt[1:] - bt[:-1]) == step
    ret = np.full(len(bars), np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    ret[~adj_prev] = np.nan            # a return only exists across adjacent bars
    with np.errstate(divide="ignore", invalid="ignore"):
        imb = np.where(bars["vol"] > 0, bars["signed"] / bars["vol"], 0.0)
    bars["ret"], bars["imb"], bars["adj_prev"] = ret, imb, adj_prev
    return bars


# --------------------------------------------------------------------------
# Layer 1 — the tape's information content, before any strategy
# --------------------------------------------------------------------------
def runs_test_segmented(ret, adj_prev):
    """Wald-Wolfowitz runs test on return signs, summed across contiguous
    segments (a gap ends a segment; zeros dropped within segments)."""
    n_tot = r_tot = e_tot = v_tot = 0.0
    seg = []

    def flush(seg):
        nonlocal n_tot, r_tot, e_tot, v_tot
        s = np.array([x for x in seg if x != 0])
        if s.size < 2:
            return
        n1 = int((s > 0).sum()); n2 = int((s < 0).sum()); n = n1 + n2
        if n1 == 0 or n2 == 0 or n < 2:
            return
        runs = 1 + int((s[1:] != s[:-1]).sum())
        e = 1.0 + 2.0 * n1 * n2 / n
        v = (2.0 * n1 * n2 * (2.0 * n1 * n2 - n)) / (n * n * (n - 1.0))
        n_tot += n; r_tot += runs; e_tot += e; v_tot += max(v, 0.0)

    for i in range(len(ret)):
        if not adj_prev[i] or not np.isfinite(ret[i]):
            flush(seg); seg = []
            if np.isfinite(ret[i]):
                seg = [np.sign(ret[i])]
            continue
        seg.append(np.sign(ret[i]))
    flush(seg)

    z = (r_tot - e_tot) / math.sqrt(v_tot) if v_tot > 0 else float("nan")
    return {
        "n_signed_returns": int(n_tot),
        "runs_observed": int(r_tot),
        "runs_expected_rw": round(e_tot, 1),
        "mean_run_len_obs": round(n_tot / r_tot, 4) if r_tot else None,
        "mean_run_len_rw": round(n_tot / e_tot, 4) if e_tot else None,
        "z": round(z, 3) if np.isfinite(z) else None,
    }


def layer1(bars):
    ret = bars["ret"].to_numpy()
    imb = bars["imb"].to_numpy()
    adj_prev = bars["adj_prev"].to_numpy()
    bt = bars["bt"].to_numpy()
    step = int(np.min(np.diff(bt))) if len(bt) > 1 else 0

    # lag-1 autocorrelation over pairs of returns on adjacent bars
    adj_next = np.zeros(len(bt), dtype=bool)
    adj_next[:-1] = (bt[1:] - bt[:-1]) == step
    pair = np.isfinite(ret) & adj_next
    pair[:-1] &= np.isfinite(np.roll(ret, -1))[:-1]
    pair[-1] = False
    r0 = ret[pair]; r1 = ret[np.roll(pair, 1)]
    ac1 = float(np.corrcoef(r0, r1)[0, 1]) if r0.size > 2 else float("nan")
    t_ac1 = ac1 * math.sqrt(r0.size) if np.isfinite(ac1) else float("nan")

    # Spearman IC: imbalance at bar t -> return of bar t+1
    ev = np.isfinite(ret) & adj_next
    ev[:-1] &= np.isfinite(np.roll(ret, -1))[:-1]
    ev[-1] = False
    x = imb[ev]; y = np.roll(ret, -1)[ev]
    if x.size > 10:
        ic, ic_p = sps.spearmanr(x, y)
        t_ic = ic * math.sqrt(max(x.size - 2, 1) / max(1e-12, 1 - ic * ic))
    else:
        ic = ic_p = t_ic = float("nan")

    return {
        "n_bars": int(len(bars)),
        "n_ret_pairs": int(r0.size),
        "ac1": round(ac1, 5) if np.isfinite(ac1) else None,
        "t_ac1": round(t_ac1, 2) if np.isfinite(t_ac1) else None,
        "n_ic_events": int(x.size),
        "spearman_ic": round(float(ic), 5) if np.isfinite(ic) else None,
        "t_ic": round(float(t_ic), 2) if np.isfinite(t_ic) else None,
        "ic_p": float(ic_p) if np.isfinite(ic_p) else None,
        "runs": runs_test_segmented(ret, adj_prev),
    }


# --------------------------------------------------------------------------
# Layer 2 — the scalper, honestly costed
# --------------------------------------------------------------------------
def build_events(bars, step):
    """Event = bar t with a valid own-bar return AND a valid next-bar return.
    All 8 variants use the SAME event set at a horizon, so they are comparable
    and White's Reality Check gets equal-length rows."""
    bt = bars["bt"].to_numpy()
    ret = bars["ret"].to_numpy()
    imb = bars["imb"].to_numpy()
    adj_next = np.zeros(len(bt), dtype=bool)
    adj_next[:-1] = (bt[1:] - bt[:-1]) == step
    valid = np.isfinite(ret) & adj_next
    valid[:-1] &= np.isfinite(np.roll(ret, -1))[:-1]
    valid[-1] = False
    idx = np.where(valid)[0]
    if idx.size == 0:
        return None
    ev = pd.DataFrame({
        "bt": bt[idx],
        "hold_ts": bt[idx] + step,        # the bar whose return we hold through
        "ret_t": ret[idx],
        "imb_t": imb[idx],
        "ret_next": ret[idx + 1],
    })
    chain = np.zeros(len(ev), dtype=bool)
    chain[1:] = (ev["bt"].to_numpy()[1:] - ev["bt"].to_numpy()[:-1]) == step
    ev["chain_prev"] = chain              # position can carry from previous event
    return ev


def signal_positions(ev, signal):
    if signal == "flow_follow":
        return np.sign(ev["imb_t"].to_numpy())
    if signal == "flow_fade":
        return -np.sign(ev["imb_t"].to_numpy())
    if signal == "momentum":
        return np.sign(ev["ret_t"].to_numpy())
    if signal == "reversal":
        return -np.sign(ev["ret_t"].to_numpy())
    raise ValueError(signal)


def cost_units(pos, chain_prev):
    """Sides paid per event under turnover costing (runs live).
    Entry: |pos - carried|; carried = prev pos if events chain, else 0.
    Exit: |pos| charged when the chain breaks after this event (or at the end)."""
    prev = np.where(chain_prev, np.roll(pos, 1), 0.0)
    prev[0] = 0.0
    entry = np.abs(pos - prev)
    chain_next = np.roll(chain_prev, -1)
    chain_next[-1] = False
    exit_ = np.where(chain_next, 0.0, np.abs(pos))
    return entry + exit_


def portfolio(events_by_sym, signal, side_bps):
    """Cross-symbol equal-weight where signals fire (eras are disjoint, so this
    is a concatenation in time). Returns per-holding-bar net & gross series."""
    frames = []
    total_sides = 0.0
    held_events = 0
    for sym, ev in events_by_sym.items():
        if ev is None:
            continue
        pos = signal_positions(ev, signal)
        cu = cost_units(pos, ev["chain_prev"].to_numpy())
        gross = pos * ev["ret_next"].to_numpy()
        net = gross - (side_bps / 1e4) * cu
        total_sides += float(cu.sum())
        held_events += int((pos != 0).sum())
        frames.append(pd.DataFrame(
            {"hold_ts": ev["hold_ts"].to_numpy(), "net": net, "gross": gross}))
    if not frames:
        return None
    allv = pd.concat(frames).groupby("hold_ts")[["net", "gross"]].mean().sort_index()
    return {
        "net": allv["net"].to_numpy(),
        "gross": allv["gross"].to_numpy(),
        "hold_ts": allv.index.to_numpy(),
        "round_trips": total_sides / 2.0,
        "held_events": held_events,
        "n_events": int(len(allv)),
    }


def sharpe(x):
    x = np.asarray(x, float)
    s = x.std(ddof=1)
    return float(x.mean() / s) if s > 0 else 0.0


def daily_agg(hold_ts, r):
    d = pd.DataFrame({"day": (np.asarray(hold_ts) // 86400).astype(np.int64), "r": r})
    g = d.groupby("day")["r"].sum()
    return g.index.to_numpy(), g.to_numpy()


# --------------------------------------------------------------------------
def main():
    os.makedirs("reports", exist_ok=True)
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)

    print("=" * 78)
    print("TAPE SCALP LAB — is there ANY short-horizon edge in the desk's own tape?")
    print("=" * 78)

    tapes, tape_stats = load_tape(con)
    con.close()
    total_trades = sum(s["n_trades"] for s in tape_stats)
    union_span = sum(s["span_days"] for s in tape_stats)  # eras are disjoint
    print("\n-- TAPE CHARACTERIZATION (all symbols present; eras are DISJOINT) --")
    for s in tape_stats:
        print("  %-8s %9d trades  %s .. %s  (%.1f d)  med gap %ss  max gap %sh  "
              "%.1f%% of minutes traded"
              % (s["symbol"], s["n_trades"], s["first_utc"][:10], s["last_utc"][:10],
                 s["span_days"], s["median_intertrade_s"], s["max_gap_hours"],
                 s["pct_minutes_with_a_trade"]))
    print("  NOTE: only %d symbols exist in trade_tape (the design hoped for ~8);"
          % len(tape_stats))
    print("  all %d are used. Union of coverage: %.1f days across 2013-2022."
          % (len(tape_stats), union_span))

    # ---- bars + Layer 1 -------------------------------------------------
    bars_all, l1 = {}, {}
    for hz, step in HORIZONS.items():
        bars_all[hz] = {}
        l1[hz] = {}
        for sym, df in tapes.items():
            b = build_bars(df, step)
            bars_all[hz][sym] = b
            l1[hz][sym] = layer1(b)

    print("\n-- LAYER 1: THE TAPE'S INFORMATION CONTENT (before any strategy) --")
    print("%-8s %-5s %8s %9s %7s %9s %7s %8s %10s %10s" % (
        "symbol", "hz", "bars", "ac1", "t", "IC(flow)", "t", "runs_z",
        "runlen", "runlen_rw"))
    for hz in HORIZONS:
        for sym in tapes:
            r = l1[hz][sym]
            print("%-8s %-5s %8d %9s %7s %9s %7s %8s %10s %10s" % (
                sym, hz, r["n_bars"], r["ac1"], r["t_ac1"], r["spearman_ic"],
                r["t_ic"], r["runs"]["z"], r["runs"]["mean_run_len_obs"],
                r["runs"]["mean_run_len_rw"]))

    # ---- Layer 2: 8 signal variants x 3 cost tiers ----------------------
    events = {hz: {sym: build_events(bars_all[hz][sym], HORIZONS[hz])
                   for sym in tapes} for hz in HORIZONS}

    variants = []           # 24 scored variants — every one goes into deflation
    port_cache = {}         # (signal, hz, tier) -> portfolio dict
    for hz in HORIZONS:
        for sig in SIGNALS:
            for tier in TIERS_BPS_SIDE:
                p = portfolio(events[hz], sig, tier)
                name = "%s_%s_%gbps" % (sig, hz, tier)
                port_cache[(sig, hz, tier)] = p
                net = p["net"]
                v = {
                    "name": name, "signal": sig, "horizon": hz,
                    "side_bps": tier,
                    "n_events": p["n_events"],
                    "round_trips": round(p["round_trips"], 1),
                    "avg_hold_bars_per_trip": round(
                        p["held_events"] / p["round_trips"], 3)
                        if p["round_trips"] else None,
                    "gross_bps_per_trade": round(
                        float(p["gross"].sum()) / p["round_trips"] * 1e4, 4)
                        if p["round_trips"] else None,
                    "net_bps_per_trade": round(
                        float(net.sum()) / p["round_trips"] * 1e4, 4)
                        if p["round_trips"] else None,
                    "cost_per_trade_bps": round(2 * tier, 1),
                    "net_sharpe_per_bar": round(sharpe(net), 5),
                    "gross_sharpe_per_bar": round(sharpe(p["gross"]), 5),
                }
                variants.append(v)

    n_lab_variants = len(variants)
    total_trials = HOUSE_PRIOR_TRIALS + n_lab_variants
    trial_sharpes = [v["net_sharpe_per_bar"] for v in variants]

    # ---- THE HEADLINE TABLE: the scalper's wall, shown not asserted -----
    print("\n-- THE SCALPER'S WALL: gross edge per round trip vs cost per round trip --")
    hdr = ("%-13s %-5s %10s %9s %9s | %8s %8s %8s"
           % ("signal", "hz", "trips", "hold/trip", "gross/tr",
              "net@0", "net@4", "net@30"))
    print(hdr); print("-" * len(hdr))
    wall_rows = []
    for hz in HORIZONS:
        for sig in SIGNALS:
            row = {t: next(v for v in variants
                           if v["signal"] == sig and v["horizon"] == hz
                           and v["side_bps"] == t) for t in TIERS_BPS_SIDE}
            r0 = row[0.0]
            wall_rows.append({
                "signal": sig, "horizon": hz,
                "round_trips": r0["round_trips"],
                "avg_hold_bars_per_trip": r0["avg_hold_bars_per_trip"],
                "gross_bps_per_trade": r0["gross_bps_per_trade"],
                "net_bps_at_0": r0["net_bps_per_trade"],
                "net_bps_at_2side": row[2.0]["net_bps_per_trade"],
                "net_bps_at_15side": row[15.0]["net_bps_per_trade"],
            })
            print("%-13s %-5s %10.0f %9s %9s | %8s %8s %8s" % (
                sig, hz, r0["round_trips"], r0["avg_hold_bars_per_trip"],
                r0["gross_bps_per_trade"], r0["net_bps_per_trade"],
                row[2.0]["net_bps_per_trade"], row[15.0]["net_bps_per_trade"]))
    print("  cost per round trip: 0 / 4 / 30 bps (0, 2, 15 bps per side)")

    # ---- best NET variant at the REAL tier (15 bps/side) ----------------
    real = [v for v in variants if v["side_bps"] == 15.0]
    best_net = max(real, key=lambda v: v["net_sharpe_per_bar"])
    bp = port_cache[(best_net["signal"], best_net["horizon"], 15.0)]

    # honest effective-N: eras are disjoint (cross-symbol rho structurally 0);
    # the dependence that remains is serial autocorrelation of the net series.
    rho1 = float(pd.Series(bp["net"]).autocorr(lag=1))
    eb = qv.effective_bets(bp["n_events"], rho1)
    n_eff = min(float(bp["n_events"]), float(eb["independent_bets"]))

    ev_best = qv.evaluate(
        best_net["name"], bp["net"], n_trials=total_trials,
        trial_sharpes=trial_sharpes,
        periods_per_year=BARS_PER_YEAR[best_net["horizon"]],
        trades_per_period=bp["round_trips"] / bp["n_events"],
        gross_returns=bp["gross"], n_effective=n_eff)
    # benchmark: FLAT (0). A dollar-neutral long/short scalper's alternative is
    # cash; buy-and-hold is not the null for a signed book. Stated on record.

    print("\n-- HARNESS VERDICT: best NET variant at the REAL wall (15 bps/side) --")
    print("  " + qv.verdict_line(ev_best))
    print("  serial rho1 %.4f -> effective bets %.0f of %d bars (discount %.1fx)"
          % (rho1, n_eff, bp["n_events"], bp["n_events"] / n_eff))

    # ---- fantasy ceiling on the record ----------------------------------
    fantasy = [v for v in variants if v["side_bps"] == 0.0]
    best_f = max(fantasy, key=lambda v: v["net_sharpe_per_bar"])
    fp = port_cache[(best_f["signal"], best_f["horizon"], 0.0)]
    rho1_f = float(pd.Series(fp["net"]).autocorr(lag=1))
    eb_f = qv.effective_bets(fp["n_events"], rho1_f)
    n_eff_f = min(float(fp["n_events"]), float(eb_f["independent_bets"]))
    ev_fant = qv.evaluate(
        best_f["name"], fp["net"], n_trials=total_trials,
        trial_sharpes=trial_sharpes,
        periods_per_year=BARS_PER_YEAR[best_f["horizon"]],
        trades_per_period=fp["round_trips"] / fp["n_events"],
        n_effective=n_eff_f)
    print("\n-- INFORMATION CEILING: best variant at 0 bps (untradeable fantasy) --")
    print("  " + qv.verdict_line(ev_fant))

    # ---- White's Reality Check across the 8 real-tier variants ----------
    # Daily-aggregated so the bootstrap is tractable and cross-variant
    # correlation is preserved (strategies in ROWS, equal length).
    day_sets = {}
    for v in real:
        p = port_cache[(v["signal"], v["horizon"], 15.0)]
        days, dr = daily_agg(p["hold_ts"], p["net"])
        day_sets[v["name"]] = dict(zip(days.tolist(), dr.tolist()))
    all_days = sorted(set().union(*[set(d) for d in day_sets.values()]))
    M = np.array([[day_sets[v["name"]].get(d, 0.0) for d in all_days]
                  for v in real])
    wrc = qv.whites_reality_check(M, n_boot=500)
    print("\n-- WHITE'S REALITY CHECK (8 net variants @15bps, daily, %d days) --"
          % len(all_days))
    print("  p = %.3f -> %s" % (wrc["p_value"],
          "best-of-family is indistinguishable from a search artefact"
          if not wrc["survives"] else "family best survives"))

    # ---- beta to SPY for the best net variant (daily) --------------------
    beta_to_spy, beta_note = None, ""
    try:
        import yfinance as yf
        days, dr = daily_agg(bp["hold_ts"], bp["net"])
        idx = pd.to_datetime(days * 86400, unit="s", utc=True).tz_localize(None)
        mine = pd.Series(dr, index=idx.normalize())
        spy = yf.download("SPY", start=str(idx.min().date()),
                          end=str(idx.max().date() + pd.Timedelta(days=3)),
                          progress=False, auto_adjust=True)["Close"]
        if isinstance(spy, pd.DataFrame):
            spy = spy.iloc[:, 0]
        spy_r = spy.pct_change().dropna()
        spy_r.index = pd.to_datetime(spy_r.index).normalize()
        joined = pd.concat([mine.rename("s"), spy_r.rename("m")], axis=1).dropna()
        if len(joined) > 30:
            cov = np.cov(joined["s"], joined["m"])
            beta_to_spy = float(cov[0, 1] / cov[1, 1])
            beta_note = ("beta measured on %d overlapping days (crypto weekends "
                         "drop out of the join)" % len(joined))
        else:
            beta_note = "insufficient overlapping days for beta"
    except Exception as e:  # network failure etc. — report, never fabricate
        beta_note = "yfinance unavailable: %s" % e
    print("\n-- BETA TO SPY (best net variant, daily) --")
    print("  beta = %s   (%s)" % (beta_to_spy, beta_note))

    # ---- receipt ---------------------------------------------------------
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "tape scalping — tick-rule flow & short-horizon price signals, "
                     "hold next bar, long/short, turnover-costed (runs live)",
        "db": DB,
        "db_access": "read-only (sqlite URI mode=ro)",
        "beta_to_spy": beta_to_spy,
        "beta_note": beta_note,
        "tape": {
            "n_symbols": len(tape_stats),
            "symbols": tape_stats,
            "total_trades": total_trades,
            "union_span_days": round(union_span, 1),
            "eras_disjoint": True,
            "note": "only 3 symbols exist in trade_tape (design hoped ~8); all used. "
                    "Eras are pairwise disjoint 2013-2022 backfill; candles cover "
                    "2024-2026 only, so SPY via yfinance is the benchmark spine.",
        },
        "layer1_information_content": l1,
        "scalpers_wall_table": wall_rows,
        "cost_tiers_bps_per_side": TIERS_BPS_SIDE,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_lab_variants,
        "total_deflation_trials": total_trials,
        "variants": variants,
        "effective_n_choice": {
            "method": "qv.effective_bets(n_bars, lag-1 autocorr of the net series)"
                      "['independent_bets'], capped at n_bars",
            "rationale": "eras are disjoint so cross-symbol rho is structurally 0; "
                         "holding = next bar so windows do not overlap; the only "
                         "measured dependence is serial autocorrelation.",
            "best_net_rho1": round(rho1, 5),
            "best_net_n_effective": round(n_eff, 1),
        },
        "benchmark": "flat (0) — a dollar-neutral long/short scalper's null is cash; "
                     "buy-and-hold is not the correct null for a signed book",
        "evaluate_best_net_real_tier": ev_best,
        "evaluate_best_fantasy_tier": ev_fant,
        "whites_reality_check_daily_net15": wrc,
        "verdicts": {
            "best_net": qv.verdict_line(ev_best),
            "fantasy_ceiling": qv.verdict_line(ev_fant),
        },
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)
    print("\nreceipt -> %s" % RECEIPT)


if __name__ == "__main__":
    main()
