#!/usr/bin/env python3
"""ML edge-discovery lab — nonlinear and interaction hypotheses, honestly validated.

WHAT THIS ADDS THAT THE PREVIOUS SWEEPS COULD NOT
-------------------------------------------------
The cross-sectional study tested 360 combinations of LINEAR, univariate rank
signals (momentum, reversal, vol-scaled, idiosyncratic, low-vol, size) and found
nothing. That leaves a genuinely untested hypothesis class: signals that only
exist as INTERACTIONS or NONLINEARITIES — "momentum works, but only in low-vol
names after a volume spike". A tree ensemble can represent that; a rank sort cannot.

So this is not "the same search with a fancier tool". It is a different hypothesis
class, and it deserves an honest test.

WHY ML IS DANGEROUS HERE, AND WHAT STOPS IT
-------------------------------------------
A model with enough capacity fits noise perfectly. Given 133 assets and hundreds of
features, gradient boosting WILL find a beautiful in-sample pattern in pure noise —
that is not a risk, it is a certainty. Everything below exists to stop that:

  * purged K-fold with embargo   training data whose label window overlaps the
                                 test fold is dropped, so the model cannot see
                                 its own answer through serial correlation
  * every config is a trial      each model x horizon x feature-set counts toward
                                 the deflation. Fitting 30 configs and reporting
                                 the best one un-deflated is the classic fraud
  * effective sample size        133 correlated crypto assets are not 133
                                 independent observations
  * cost wall applied            predictions are only interesting if the implied
                                 trade clears ~30 bps round trip
  * buy-and-hold benchmark       a model that just learns "crypto went up" is
                                 beta, not alpha

Usage:
    python3 ml_lab.py                 # full study
    python3 ml_lab.py --symbols 40    # faster
"""
import argparse
import json
import os
import sys
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
if KRAKEN_DESK not in sys.path:
    sys.path.insert(0, KRAKEN_DESK)
from kraken_backtest import fetch_ohlc, sym_to_pair  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "ml_lab.json")

COST_BPS = 30.0            # Kraken round trip — the wall predictions must clear
HORIZONS = [1, 5, 10]      # forward return, in days
BARS = 720


# ---------------------------------------------------------------------------
# Features — all strictly backward-looking. Bar i uses bars <= i only.
# ---------------------------------------------------------------------------
def build_features(H, L, C, V):
    """Per-bar feature matrix for one asset. Returns (X, feature_names, valid_from)."""
    n = len(C)
    C = np.asarray(C, float); H = np.asarray(H, float)
    L = np.asarray(L, float); V = np.asarray(V, float)
    rets = np.zeros(n)
    rets[1:] = np.diff(C) / np.maximum(C[:-1], 1e-12)

    feats, names = [], []

    def add(name, arr):
        feats.append(np.asarray(arr, float)); names.append(name)

    # multi-horizon momentum
    for lb in (2, 5, 10, 20, 60):
        m = np.full(n, np.nan)
        m[lb:] = C[lb:] / np.maximum(C[:-lb], 1e-12) - 1.0
        add("mom_%d" % lb, m)

    # realised volatility
    for w in (5, 20, 60):
        v = np.full(n, np.nan)
        for i in range(w, n):
            v[i] = rets[i - w + 1:i + 1].std(ddof=1)
        add("vol_%d" % w, v)

    # vol-of-vol and vol ratio (regime)
    vr = np.full(n, np.nan)
    v5, v20 = feats[names.index("vol_5")], feats[names.index("vol_20")]
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = v5 / np.where(v20 > 0, v20, np.nan)
    add("vol_ratio_5_20", vr)

    # range / true-range proxies
    hl = np.full(n, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        hl = (H - L) / np.maximum(C, 1e-12)
    add("hl_range", hl)
    for w in (5, 20):
        r = np.full(n, np.nan)
        for i in range(w, n):
            r[i] = np.nanmean(hl[i - w + 1:i + 1])
        add("hl_mean_%d" % w, r)

    # volume dynamics
    for w in (5, 20):
        vv = np.full(n, np.nan)
        for i in range(w, n):
            base = np.nanmean(V[i - w + 1:i + 1])
            vv[i] = V[i] / base if base > 0 else np.nan
        add("vol_spike_%d" % w, vv)

    # z-score of price vs its own recent distribution
    for w in (10, 20, 60):
        z = np.full(n, np.nan)
        for i in range(w, n):
            win = C[i - w + 1:i + 1]
            sd = win.std(ddof=1)
            z[i] = (C[i] - win.mean()) / sd if sd > 0 else np.nan
        add("zscore_%d" % w, z)

    # distance from rolling extremes (breakout proximity)
    for w in (20, 60):
        d = np.full(n, np.nan)
        for i in range(w, n):
            hi, lo = H[i - w + 1:i + 1].max(), L[i - w + 1:i + 1].min()
            d[i] = (C[i] - lo) / (hi - lo) if hi > lo else np.nan
        add("range_pos_%d" % w, d)

    # skew of recent returns
    for w in (20,):
        s = np.full(n, np.nan)
        for i in range(w, n):
            win = rets[i - w + 1:i + 1]
            sd = win.std(ddof=1)
            s[i] = (((win - win.mean()) ** 3).mean() / sd ** 3) if sd > 0 else np.nan
        add("skew_%d" % w, s)

    X = np.column_stack(feats)
    return X, names


def cross_sectional_rank(M):
    """Rank each feature within each date, mapped to [-0.5, 0.5].

    Cross-sectional ranking is what makes assets comparable and removes the market
    factor — without it a model mostly learns "everything went up together".
    """
    out = np.full(M.shape, np.nan)
    for t in range(M.shape[0]):
        row = M[t]
        ok = np.isfinite(row)
        if ok.sum() < 3:
            continue
        vals = row[ok]
        order = vals.argsort().argsort().astype(float)
        out[t, ok] = order / max(1, len(vals) - 1) - 0.5
    return out


def assemble_panel(data, horizon):
    """Build a stacked (samples x features) panel with cross-sectionally ranked
    features and a market-neutral forward-return target."""
    syms = sorted(data)
    per_sym = {}
    names = None
    n_bars = min(len(data[s][2]) for s in syms)

    for s in syms:
        H, L, C, V = data[s]
        X, nm = build_features(H[:n_bars], L[:n_bars], C[:n_bars], V[:n_bars])
        per_sym[s] = X
        names = nm

    n_feat = len(names)
    # (time, asset, feature)
    cube = np.full((n_bars, len(syms), n_feat), np.nan)
    for j, s in enumerate(syms):
        cube[:, j, :] = per_sym[s]

    # rank each feature cross-sectionally at each date
    for f in range(n_feat):
        cube[:, :, f] = cross_sectional_rank(cube[:, :, f])

    # target: forward return, DEMEANED across the cross-section at each date so the
    # model must find relative winners, not just ride the market
    closes = np.column_stack([np.asarray(data[s][2][:n_bars], float) for s in syms])
    fwd = np.full((n_bars, len(syms)), np.nan)
    fwd[:-horizon] = closes[horizon:] / np.maximum(closes[:-horizon], 1e-12) - 1.0
    market = np.nanmean(fwd, axis=1, keepdims=True)
    fwd_rel = fwd - market

    rows_X, rows_y, rows_t, rows_j, rows_raw = [], [], [], [], []
    for t in range(n_bars - horizon):
        for j in range(len(syms)):
            x = cube[t, j, :]
            y = fwd_rel[t, j]
            if not np.isfinite(x).all() or not np.isfinite(y):
                continue
            rows_X.append(x); rows_y.append(y)
            rows_t.append(t); rows_j.append(j); rows_raw.append(fwd[t, j])

    return (np.array(rows_X), np.array(rows_y), np.array(rows_t),
            np.array(rows_j), np.array(rows_raw), names, syms)


# ---------------------------------------------------------------------------
# Study
# ---------------------------------------------------------------------------
def load_universe(symbols, bars=BARS, verbose=True):
    data = {}
    for s in symbols:
        try:
            b = fetch_ohlc(sym_to_pair(s), 1440, bars)
            if len(b) >= 400:
                data[s] = ([x[2] for x in b], [x[3] for x in b],
                           [x[4] for x in b], [x[5] if len(x) > 5 else 1.0 for x in b])
        except Exception:
            pass
        time.sleep(0.2)
    return data


def run(symbols=None, bars=BARS, verbose=True):
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge

    if symbols is None:
        import venue_universe
        symbols = venue_universe.kraken_live_universe() or [
            "XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD"]

    if verbose:
        print("Fetching %d daily bars for %d symbols..." % (bars, len(symbols)))
    data = load_universe(symbols, bars, verbose)
    if len(data) < 8:
        raise RuntimeError("need at least 8 symbols with history, got %d" % len(data))
    if verbose:
        print("  usable: %d symbols\n" % len(data))

    MODELS = {
        "ridge": lambda: Ridge(alpha=1.0),
        "gbm_shallow": lambda: GradientBoostingRegressor(
            n_estimators=120, max_depth=2, learning_rate=0.05, subsample=0.8,
            random_state=0),
        "gbm_deep": lambda: GradientBoostingRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.05, subsample=0.8,
            random_state=0),
        "rf": lambda: RandomForestRegressor(
            n_estimators=200, max_depth=6, min_samples_leaf=50, n_jobs=-1,
            random_state=0),
    }

    results = []
    n_trials = 0

    for horizon in HORIZONS:
        X, y, t_idx, j_idx, y_raw, names, syms = assemble_panel(data, horizon)
        if X.shape[0] < 500:
            continue
        if verbose:
            print("horizon %dd: %d samples x %d features (%d assets)"
                  % (horizon, X.shape[0], X.shape[1], len(syms)))

        n_dates = int(t_idx.max()) + 1
        for model_name, factory in MODELS.items():
            n_trials += 1
            oos_pred, oos_true, oos_asset = [], [], []

            # Purge/embargo operate on DATES, not rows — rows at the same date must
            # never be split across the train/test boundary.
            for tr_dates, te_dates in qv.purged_kfold_splits(
                    n_dates, n_splits=5, embargo_pct=0.02, label_horizon=horizon):
                tr_mask = np.isin(t_idx, tr_dates)
                te_mask = np.isin(t_idx, te_dates)
                if tr_mask.sum() < 200 or te_mask.sum() < 50:
                    continue
                m = factory()
                m.fit(X[tr_mask], y[tr_mask])
                p = m.predict(X[te_mask])
                oos_pred.append(p)
                oos_true.append(y[te_mask])
                oos_asset.append(j_idx[te_mask])

            if not oos_pred:
                continue
            pred = np.concatenate(oos_pred)
            true = np.concatenate(oos_true)
            asset = np.concatenate(oos_asset)

            ic = float(np.corrcoef(pred, true)[0, 1]) if pred.std() > 0 else 0.0

            # Trade the top/bottom decile of predictions. Long-short pays cost twice.
            k = max(1, len(pred) // 10)
            order = pred.argsort()
            longs, shorts = order[-k:], order[:k]
            gross = float(true[longs].mean() - true[shorts].mean())
            net = gross - 2 * (COST_BPS / 10000.0)

            # per-trade series for the harness
            leg = np.concatenate([true[longs], -true[shorts]])
            leg_net = leg - (COST_BPS / 10000.0)

            n_assets_used = len(np.unique(asset))
            # correlation discount: the legs are drawn from correlated assets over
            # overlapping dates, so nominal n badly overstates independence
            n_eff = qv.effective_sample_size(
                len(leg_net), n_series=max(2, n_assets_used), mean_correlation=0.6,
                overlap_factor=float(horizon))

            results.append({
                "horizon_days": horizon,
                "model": model_name,
                "n_samples": int(X.shape[0]),
                "n_features": int(X.shape[1]),
                "oos_ic": round(ic, 4),
                "decile_gross_bps": round(gross * 10000, 2),
                "decile_net_bps": round(net * 10000, 2),
                "clears_cost_wall": bool(net > 0),
                "n_eff": round(n_eff, 1),
                "_leg_net": leg_net,
                "_n_assets": n_assets_used,
            })
            if verbose:
                print("   %-12s IC %+.4f   decile gross %+7.1f bps  net %+7.1f bps"
                      % (model_name, ic, gross * 10000, net * 10000))
        if verbose:
            print()

    if not results:
        raise RuntimeError("no results produced")

    # Deflate the winner against EVERY config tried
    best = max(results, key=lambda r: r["decile_net_bps"])
    trial_sharpes = []
    for r in results:
        leg = r["_leg_net"]
        sd = leg.std(ddof=1)
        trial_sharpes.append(float(leg.mean() / sd) if sd > 0 else 0.0)

    ev = qv.evaluate(
        "%s h=%dd" % (best["model"], best["horizon_days"]),
        best["_leg_net"], n_trials=n_trials, trial_sharpes=trial_sharpes,
        periods_per_year=365 / best["horizon_days"], trades_per_period=1.0,
        n_effective=best["n_eff"])

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_symbols": len(data),
        "bars": bars,
        "cost_bps": COST_BPS,
        "configs_tested": n_trials,
        "results": [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in results],
        "best": {k: v for k, v in best.items() if not k.startswith("_")},
        "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 80)
    print("ML EDGE-DISCOVERY LAB — %d symbols, %d bars, %d configs tested"
          % (p["n_symbols"], p["bars"], p["configs_tested"]))
    print("=" * 80)
    print("  %-6s %-13s %10s %14s %13s %10s"
          % ("horiz", "model", "OOS IC", "gross bps", "net bps", "n_eff"))
    for r in sorted(p["results"], key=lambda r: -r["decile_net_bps"]):
        print("  %-6s %-13s %+10.4f %+14.1f %+13.1f %10.0f"
              % ("%dd" % r["horizon_days"], r["model"], r["oos_ic"],
                 r["decile_gross_bps"], r["decile_net_bps"], r["n_eff"]))

    b, ev = p["best"], p["best_deflated"]
    print("\nBEST CONFIG: %s at %dd horizon" % (b["model"], b["horizon_days"]))
    print("  out-of-sample IC          : %+.4f" % b["oos_ic"])
    print("  decile long-short, gross  : %+.1f bps" % b["decile_gross_bps"])
    print("  after %.0f bps cost wall    : %+.1f bps" % (p["cost_bps"], b["decile_net_bps"]))
    print("\n  HONEST ACCOUNTING")
    print("    configs searched        : %d" % p["configs_tested"])
    print("    nominal observations    : %d" % ev["n_obs"])
    print("    EFFECTIVE observations  : %.0f  (discount %.1fx)"
          % (ev["n_effective"], ev["independence_discount"]))
    print("    corrected t-stat        : %+.2f" % ev["t_stat"])
    print("    expected max SR (null)  : %.4f" % ev["expected_max_sharpe_under_null"])
    print("    DEFLATED Sharpe         : %s" % ev["deflated_sharpe"])
    print("\n  -> %s" % p["verdict"])
    print("\nReport -> %s" % REPORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None)
    ap.add_argument("--bars", type=int, default=BARS)
    args = ap.parse_args()
    import venue_universe
    syms = venue_universe.kraken_live_universe()
    if args.symbols:
        syms = syms[:args.symbols]
    report(run(symbols=syms or None, bars=args.bars))


if __name__ == "__main__":
    main()
