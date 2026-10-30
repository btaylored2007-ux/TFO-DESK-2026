#!/usr/bin/env python3
"""btc_swing_lab.py — the funder's thesis, tested exactly as stated.

THE THESIS (funder, 2026-08-20)
-------------------------------
"BTC swings 1-2%/day; with $500k on that one asset you can manage the swing
multi-directionally, even with costs — it can be micro-managed."

Systematized into the swing-management family and tested exhaustively:

  a. GRID / swing harvest — buy every X% dip from the last fill, sell every X%
     rip, X in {1%, 2%, 4%}; long-only grid around a 50% core position AND a
     symmetric long/short grid (the "multi-directional" version). Daily bars
     (2014->) and hourly bars (~24 months).
  b. SWING-FOLLOW — enter on a Y% continuation move off the rolling extreme,
     manage with a Z% trailing stop (profit-take + stop in one rule), both
     directions. Y in {1%, 2%, 4%}, Z in {1%, 2%}. Hourly and daily.
  c. SWING-FADE — enter AGAINST the Y% move, same management. Same grids.
  d. Baselines — buy-and-hold BTC; the daily +/-10pp band 50/50 BTC/cash
     rebalance (best net rule from research_rebalance_premium.md).

HONESTY NOTES (written before running)
--------------------------------------
* Costs: maker 8 bps/side (best realistic Kraken tier for resting grid limits)
  and taker 30 bps/side (stress; the natural tier for momentum market entries).
  Charged on EVERY fill, on traded notional. The maker tier applied to the
  swing-follow/fade variants is OPTIMISTIC (assumes patient limit fills at the
  signal price with no miss) — stated, not hidden.
* Slippage for $500k clips: measured live from the Kraken XBT/USD order book
  (public Depth endpoint, 500 levels) as the VWAP cost of sweeping $50k-$500k
  vs mid, both sides, and reported alongside. Grid clips here are $3.6k-$14.7k
  (see sizing below) — sweep cost at that size is ~spread only. The full-equity
  swing variants flip $500k per trade and would pay the measured $500k sweep
  cost per side ON TOP of the taker fee; reported, not silently added.
* Grid sizing: levels are compounding X% steps; capital is reserved so the grid
  can absorb a 50% drawdown from the anchor before exhausting (n_levels =
  ceil(ln 0.5 / ln(1-X))), clip = 50% of initial equity / n_levels. Long-only
  mode holds a 50% buy-hold core; symmetric mode runs the same clip ladder
  long AND short around a flat book (bounds +/- n_levels clips). Fixed-$ clips;
  a symmetric grid on a trending asset can therefore lose more than equity —
  if equity hits zero the book is RUINED and stays ruined (no respawn).
* Swing entries are at the NEXT bar's open after the trigger closes (no
  same-bar fills); exits are resting stop orders filled intrabar at the stop
  (or at the open if it gaps through). Intrabar path for grid fills follows the
  bar's direction (up bar: open->low->high->close; down bar: open->high->low->
  close). Re-entry requires a FRESH trigger (signal edge, not level).
* Equity marks use bar closes. Cash earns 0%. No leverage beyond the symmetric
  grid's short side (paper margin, stated).
* Search count: house-documented 1,447 prior trials (1,402 + 45 from the
  rebalance-premium lab) + every variant measured here, counted exactly at
  runtime, baselines included (conservative).
* Data: yfinance BTC-USD daily 2014-09 -> today and hourly (max ~730 days);
  Kraken public daily OHLC (720 bars) as a cross-check on the yfinance series.

READ-ONLY research. No capital, no orders, no engine, no pre-registration,
no secrets. quant_validation.py imported, never modified.
"""
import json
import math
import os
import sys
import urllib.request
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quant_validation as qv  # noqa: E402  (read-only import, never modified)

REPORTS = os.path.join(HERE, "reports")
CACHE_D = os.path.join(REPORTS, "btc_swing_prices_daily.csv")
CACHE_H = os.path.join(REPORTS, "btc_swing_prices_hourly.csv")
RECEIPT = os.path.join(REPORTS, "btc_swing_lab.json")

HOUSE_PRIOR_TRIALS = 1447        # 1,402 + 45 (research_rebalance_premium.md §0)
BPS = 1e-4
MAKER = 8 * BPS                  # per side
TAKER = 30 * BPS                 # per side
NOTIONAL = 500_000.0             # the funder's stated book

GRID_X = [0.01, 0.02, 0.04]
SWING_Y = [0.01, 0.02, 0.04]
SWING_Z = [0.01, 0.02]
LOOKBACK = {"daily": 5, "hourly": 24}   # bars in the rolling-extreme anchor


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_daily():
    if os.path.exists(CACHE_D):
        return pd.read_csv(CACHE_D, index_col=0, parse_dates=True)
    import yfinance as yf
    d = _flatten(yf.download("BTC-USD", start="2014-01-01", interval="1d",
                             progress=False, auto_adjust=True))
    d = d[["Open", "High", "Low", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    d.to_csv(CACHE_D)
    return d


def fetch_hourly():
    if os.path.exists(CACHE_H):
        return pd.read_csv(CACHE_H, index_col=0, parse_dates=True)
    import yfinance as yf
    d = _flatten(yf.download("BTC-USD", interval="1h", period="730d",
                             progress=False, auto_adjust=True))
    d = d[["Open", "High", "Low", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d.to_csv(CACHE_H)
    return d


def kraken_daily_check(daily):
    """Cross-check yfinance closes against Kraken public daily OHLC."""
    try:
        r = urllib.request.urlopen(
            "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440",
            timeout=20)
        d = json.load(r)
        key = [k for k in d["result"] if k != "last"][0]
        rows = d["result"][key]
        kr = pd.Series({pd.Timestamp(int(x[0]), unit="s"): float(x[4])
                        for x in rows}).sort_index()
        both = pd.DataFrame({"yf": daily["Close"], "kr": kr}).dropna()
        diff_bps = ((both["yf"] - both["kr"]).abs() / both["kr"] * 1e4)
        return {"n_overlap_days": int(len(both)),
                "corr_daily_close": round(float(both["yf"].corr(both["kr"])), 6),
                "median_abs_diff_bps": round(float(diff_bps.median()), 1),
                "p95_abs_diff_bps": round(float(diff_bps.quantile(0.95)), 1)}
    except Exception as e:
        return {"error": str(e)}


def kraken_depth_slippage():
    """Live Kraken XBT/USD book: VWAP sweep cost vs mid for $50k-$500k clips."""
    try:
        r = urllib.request.urlopen(
            "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=500",
            timeout=20)
        d = json.load(r)
        key = list(d["result"])[0]
        ob = d["result"][key]
        asks = [(float(p), float(q)) for p, q, _ in ob["asks"]]
        bids = [(float(p), float(q)) for p, q, _ in ob["bids"]]
        mid = (asks[0][0] + bids[0][0]) / 2.0
        out = {"mid": round(mid, 1),
               "spread_bps": round((asks[0][0] - bids[0][0]) / mid * 1e4, 3),
               "book_depth_usd_500_levels": {
                   "ask_side": round(sum(p * q for p, q in asks)),
                   "bid_side": round(sum(p * q for p, q in bids))}}
        for usd in (50_000, 100_000, 250_000, 500_000):
            row = {}
            for side, book in (("buy", asks), ("sell", bids)):
                need, cost = usd, 0.0
                for p, q in book:
                    take = min(need, p * q)
                    cost += take * (p / mid if side == "buy" else mid / p)
                    need -= take
                    if need <= 0:
                        break
                if need > 0:
                    row[side + "_slip_bps"] = None   # book exhausted
                else:
                    row[side + "_slip_bps"] = round((cost / usd - 1.0) * 1e4, 2)
            out["sweep_%dk" % (usd // 1000)] = row
        return out
    except Exception as e:
        return {"error": str(e),
                "fallback_note": "estimate: Kraken XBT/USD typically ~1 bp "
                                 "spread, $500k sweep ~3-8 bps vs mid"}


# ---------------------------------------------------------------------------
# Strategy engines (each returns equity curve + trade stats at one fee tier)
# ---------------------------------------------------------------------------
def run_grid(bars, X, mode, fee):
    """Grid harvest. mode='core' (50% BH core + long-only clip ladder) or
    'ls' (symmetric long/short ladder around flat)."""
    o = bars["Open"].values
    h = bars["High"].values
    l = bars["Low"].values
    c = bars["Close"].values
    T = len(c)
    n_levels = int(math.ceil(math.log(0.5) / math.log(1.0 - X)))
    clip = 0.5 / n_levels                      # $ clip as fraction of E0 = 1
    units, cash, clips = 0.0, 1.0, 0
    if mode == "core":
        units = 0.5 / o[0]
        cash = 0.5 - fee * 0.5                 # core entry fee charged
        lo_b, hi_b = 0, n_levels
    else:
        lo_b, hi_b = -n_levels, n_levels
    anchor = o[0]
    n_buys = n_sells = 0
    eq = np.empty(T)
    ruined = False
    # LIFO pair matching: a completed dip-buy/rip-sell (or rip-sell/dip-cover)
    # is one ROUND TRIP; its realized P&L is the harvest the funder describes.
    open_longs, open_shorts = [], []
    realized_pairs = 0.0
    n_pairs = 0

    for t in range(T):
        if not ruined:
            def sweep_buys():
                nonlocal units, cash, clips, anchor, n_buys, realized_pairs, n_pairs
                while (l[t] <= anchor * (1.0 - X) and clips < hi_b
                       and (mode == "ls" or cash >= clip * (1.0 + fee))):
                    px = anchor * (1.0 - X)
                    units += clip / px
                    cash -= clip * (1.0 + fee)
                    if clips < 0 and open_shorts:      # covering a short clip
                        sp = open_shorts.pop()
                        realized_pairs += clip * (sp - px) / sp
                        n_pairs += 1
                    else:
                        open_longs.append(px)
                    clips += 1
                    n_buys += 1
                    anchor = px

            def sweep_sells():
                nonlocal units, cash, clips, anchor, n_sells, realized_pairs, n_pairs
                while h[t] >= anchor * (1.0 + X) and clips > lo_b:
                    px = anchor * (1.0 + X)
                    units -= clip / px
                    cash += clip * (1.0 - fee)
                    if clips > 0 and open_longs:       # closing a long clip
                        bp = open_longs.pop()
                        realized_pairs += clip * (px - bp) / bp
                        n_pairs += 1
                    else:
                        open_shorts.append(px)
                    clips -= 1
                    n_sells += 1
                    anchor = px

            if c[t] >= o[t]:                   # up bar: low first, then high
                sweep_buys()
                sweep_sells()
            else:                              # down bar: high first, then low
                sweep_sells()
                sweep_buys()
        eq[t] = cash + units * c[t]
        if eq[t] <= 0.0:
            ruined = True
            eq[t] = 1e-9
            units, cash = 0.0, 1e-9            # book is gone; stop trading
    # core sleeve P&L (mode='core'): what the 50% buy-hold core alone made —
    # subtracting it isolates the grid sleeve's own economics
    core_pnl = 0.5 * (c[-1] / o[0] - 1.0) if mode == "core" else 0.0
    return {"equity": eq, "n_fills": n_buys + n_sells, "n_rt": n_pairs,
            "clip_frac": clip, "n_levels": n_levels, "ruined": ruined,
            "core_pnl": core_pnl, "realized_pairs": realized_pairs,
            "match_rate": (2.0 * n_pairs / (n_buys + n_sells))
            if (n_buys + n_sells) else None, "trade_grosses": None}


def run_swing(bars, Y, Z, style, fee, lookback):
    """Swing-follow ('follow') or swing-fade ('fade'), both directions.
    Trigger: close crosses +/-Y% off the rolling L-bar extreme (fresh edge
    only). Entry next open. Exit: Z% trailing stop from the best favorable
    price, filled intrabar (or at a gap open)."""
    o = bars["Open"].values
    h = bars["High"].values
    l = bars["Low"].values
    c = bars["Close"].values
    T = len(c)
    cs = pd.Series(c)
    roll_min = cs.rolling(lookback).min().shift(1).values
    roll_max = cs.rolling(lookback).max().shift(1).values
    up_sig = c >= roll_min * (1.0 + Y)
    dn_sig = c <= roll_max * (1.0 - Y)
    up_edge = up_sig & ~np.roll(up_sig, 1)
    dn_edge = dn_sig & ~np.roll(dn_sig, 1)
    up_edge[0] = dn_edge[0] = False

    eq = np.empty(T)
    equity = 1.0
    pos = 0                    # +1 long, -1 short, 0 flat
    pend = 0                   # entry queued for next open
    entry_px = best = eq_base = 0.0
    grosses = []               # per-trade gross return (on notional)
    for t in range(T):
        if pend and pos == 0:
            pos, pend = pend, 0
            entry_px = o[t]
            best = entry_px
            eq_base = equity * (1.0 - fee)     # entry fee on full notional
        if pos != 0:
            if pos > 0:
                stop = best * (1.0 - Z)
                if l[t] <= stop:
                    fill = o[t] if o[t] < stop else stop
                    g = fill / entry_px - 1.0
                    equity = eq_base * (1.0 + g) * (1.0 - fee)
                    grosses.append(g)
                    pos = 0
                else:
                    best = max(best, h[t])
            else:
                stop = best * (1.0 + Z)
                if h[t] >= stop:
                    fill = o[t] if o[t] > stop else stop
                    g = (entry_px - fill) / entry_px
                    equity = eq_base * (1.0 + g) * (1.0 - fee)
                    grosses.append(g)
                    pos = 0
                else:
                    best = min(best, l[t])
        if pos == 0 and not pend and not np.isnan(roll_min[t]):
            if up_edge[t]:
                pend = 1 if style == "follow" else -1
            elif dn_edge[t]:
                pend = -1 if style == "follow" else 1
        if pos != 0:
            m = c[t] / entry_px - 1.0
            eq[t] = eq_base * (1.0 + (m if pos > 0 else -m))
        else:
            eq[t] = equity
        if eq[t] <= 0:
            eq[t] = 1e-9
    g = np.array(grosses)
    return {"equity": eq, "n_fills": 2 * len(grosses), "n_rt": len(grosses),
            "trade_grosses": g, "ruined": bool(eq[-1] <= 1e-9),
            "clip_frac": 1.0, "match_rate": None}


def run_band_rebalance(daily, band, fee):
    """Daily-checked +/-band 50/50 BTC/cash (cash 0%) — the prior lab's rule."""
    r = daily["Close"].pct_change().dropna().values
    T = len(r)
    w = 0.5
    eq = np.empty(T)
    W = 1.0
    n_ev = 0
    for t in range(T):
        rp = w * r[t]
        W *= (1.0 + rp)
        w = w * (1.0 + r[t]) / (1.0 + rp)
        if abs(w - 0.5) > band:
            W *= (1.0 - fee * abs(0.5 - w))
            n_ev += 1
            w = 0.5
        eq[t] = W
    return {"equity": eq, "n_fills": n_ev, "n_rt": n_ev / 2.0, "n_rt_int": n_ev,
            "ruined": False, "clip_frac": None, "match_rate": None,
            "trade_grosses": None}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def years_of(index):
    return max((index[-1] - index[0]).days / 365.25, 1e-9)


def cagr(eq, yrs):
    return float(eq[-1]) ** (1.0 / yrs) - 1.0


def max_dd(eq):
    e = np.asarray(eq, float)
    peak = np.maximum.accumulate(e)
    return float((e / peak - 1.0).min())


def bar_returns(eq):
    e = np.concatenate([[1.0], np.asarray(eq, float)])
    return e[1:] / e[:-1] - 1.0


def lag1_autocorr(x):
    x = np.asarray(x, float)
    if x.size < 3 or x.std() == 0:
        return 0.0
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def month_key(ts):
    return ts.year * 100 + ts.month


def trend_chop_split(daily, eq_daily_index, eq_daily, yrs):
    """Monthly efficiency ratio |sum r| / sum |r| on daily log returns;
    median split -> net P&L$ per year in trending vs choppy months."""
    lr = np.log(daily["Close"]).diff().dropna()
    lr = lr[(lr.index >= eq_daily_index[0]) & (lr.index <= eq_daily_index[-1])]
    er = lr.groupby(lr.index.map(month_key)).apply(
        lambda x: abs(x.sum()) / x.abs().sum() if x.abs().sum() > 0 else 0.0)
    med = float(er.median())
    trending = set(er[er > med].index)
    eqs = pd.Series(eq_daily, index=eq_daily_index)
    mret = eqs.groupby(eqs.index.map(month_key)).last().pct_change()
    first_m = eqs.groupby(eqs.index.map(month_key)).last()
    mret.iloc[0] = first_m.iloc[0] / 1.0 - 1.0
    pnl_t = float(sum(mret[m] for m in mret.index if m in trending
                      and np.isfinite(mret[m])))
    pnl_c = float(sum(mret[m] for m in mret.index if m not in trending
                      and np.isfinite(mret[m])))
    n_t = sum(1 for m in mret.index if m in trending)
    n_c = len(mret) - n_t
    return {"er_median": round(med, 3),
            "months_trend": int(n_t), "months_chop": int(n_c),
            "trend_pnl_usd_per_yr": round(pnl_t * NOTIONAL / max(n_t / 12.0, 1e-9)),
            "chop_pnl_usd_per_yr": round(pnl_c * NOTIONAL / max(n_c / 12.0, 1e-9))}


def to_daily(index, eq):
    s = pd.Series(eq, index=index)
    d = s.groupby(s.index.normalize()).last()
    return d.index, d.values


def measure(name, family, timeframe, bars, daily_ohlc, runner, params):
    """Run gross / maker / taker tiers and assemble the full metrics row."""
    tiers = {}
    for label, fee in (("gross", 0.0), ("maker", MAKER), ("taker", TAKER)):
        tiers[label] = runner(fee)
    idx = bars.index if timeframe != "band" else bars.index[1:]
    yrs = years_of(idx)
    ppy = len(tiers["maker"]["equity"]) / yrs
    g, m, k = tiers["gross"], tiers["maker"], tiers["taker"]
    rn = bar_returns(m["equity"])
    n_rt = m["n_rt"]
    rt_yr = n_rt / yrs

    # capture ratio: net P&L (maker) / total daily high-low range traversed,
    # both on the $500k book
    dd = daily_ohlc[(daily_ohlc.index >= idx[0].normalize())
                    & (daily_ohlc.index <= idx[-1])]
    traversed_frac = float(((dd["High"] - dd["Low"])
                            / dd["Close"].shift(1)).dropna().sum())
    pnl_maker = (float(m["equity"][-1]) - 1.0) * NOTIONAL
    capture = pnl_maker / (traversed_frac * NOTIONAL) if traversed_frac else None

    # per-round-trip expectancy in bps of traded (clip) notional.
    # Swing: straight from the trade log (mean gross return per trade; net =
    # gross - 2 x fee). Grid: the SLEEVE's P&L (total minus the buy-hold core)
    # per matched round trip on the fixed-$ clip — clips are fixed fractions of
    # initial equity, so this is dimensionally honest; the core's compounding
    # is excluded rather than credited to the grid.
    clip = m.get("clip_frac") or 1.0
    exp_bps = {}
    if m.get("trade_grosses") is not None and len(m["trade_grosses"]):
        tg_mean = float(np.mean(m["trade_grosses"]))
        exp_bps = {"gross": tg_mean * 1e4,
                   "maker": (tg_mean - 2 * MAKER) * 1e4,
                   "taker": (tg_mean - 2 * TAKER) * 1e4}
    elif family.startswith("grid") and n_rt:
        per_rt_gross = m["realized_pairs"] / (n_rt * clip) * 1e4
        exp_bps = {"gross": per_rt_gross,
                   "maker": per_rt_gross - 2 * MAKER * 1e4,
                   "taker": per_rt_gross - 2 * TAKER * 1e4}
    else:
        exp_bps = {"gross": None, "maker": None, "taker": None}

    # grid economics decomposition: realized pair harvest vs inventory mark
    grid_decomp = None
    if family.startswith("grid"):
        total_pnl = float(m["equity"][-1]) - 1.0
        harvest = m["realized_pairs"]
        inv = total_pnl - m.get("core_pnl", 0.0) - harvest
        grid_decomp = {
            "harvest_usd_per_yr": round(harvest / yrs * NOTIONAL),
            "inventory_mark_usd_per_yr": round(inv / yrs * NOTIONAL),
            "core_usd_per_yr": round(m.get("core_pnl", 0.0) / yrs * NOTIONAL),
            "note": "arithmetic $/yr on the initial $500k. harvest = realized "
                    "LIFO-paired clip P&L (gross); inventory = mark-to-market "
                    "of stranded clips + residual units, net of all fees",
        }

    # directional accuracy (swing variants): share of gross-positive trades;
    # symmetric-payoff needed accuracy = 0.5 + cost_rt/(2*mean|move|); and the
    # payoff-aware version p* = (avg_loss + cost_rt) / (avg_win + avg_loss)
    # (trail management makes wins and losses asymmetric, so both are shown)
    acc = {}
    if m.get("trade_grosses") is not None and len(m["trade_grosses"]):
        tg = m["trade_grosses"]
        mean_abs = float(np.abs(tg).mean())
        wins, losses = tg[tg > 0], tg[tg <= 0]
        aw = float(wins.mean()) if len(wins) else 0.0
        al = float(-losses.mean()) if len(losses) else 0.0
        acc = {"achieved": round(float((tg > 0).mean()), 4),
               "mean_abs_move_per_trade_bps": round(mean_abs * 1e4, 1),
               "avg_win_bps": round(aw * 1e4, 1),
               "avg_loss_bps": round(al * 1e4, 1),
               "needed_maker": round(0.5 + (2 * MAKER) / (2 * mean_abs), 4),
               "needed_taker": round(0.5 + (2 * TAKER) / (2 * mean_abs), 4),
               "payoff_needed_maker": round((al + 2 * MAKER) / (aw + al), 4)
               if (aw + al) > 0 else None,
               "payoff_needed_taker": round((al + 2 * TAKER) / (aw + al), 4)
               if (aw + al) > 0 else None}

    d_idx, d_eq = to_daily(idx, m["equity"])
    tc = trend_chop_split(daily_ohlc, d_idx, d_eq, yrs)

    row = {
        "name": name, "family": family, "timeframe": timeframe,
        "params": params,
        "start": str(idx[0].date()), "end": str(idx[-1].date()),
        "years": round(yrs, 2), "n_bars": int(len(m["equity"])),
        "gross_cagr": round(cagr(g["equity"], yrs), 4),
        "net_cagr_maker": round(cagr(m["equity"], yrs), 4),
        "net_cagr_taker": round(cagr(k["equity"], yrs), 4),
        "sharpe_net_maker_ann": round(qv.sharpe_ratio(rn) * math.sqrt(ppy), 3),
        "maxdd_net_maker": round(max_dd(m["equity"]), 4),
        "fills_per_yr": round(m["n_fills"] / yrs, 1),
        "round_trips_per_yr": round(rt_yr, 1),
        "expectancy_rt_bps": {kk: (round(v, 1) if v is not None else None)
                              for kk, v in exp_bps.items()},
        "capture_ratio_maker": round(capture, 5) if capture is not None else None,
        "range_traversed_usd": round(traversed_frac * NOTIONAL),
        "accuracy": acc or None,
        "match_rate": round(m["match_rate"], 4) if m.get("match_rate") else None,
        "ruined": bool(m.get("ruined")),
        "usd_per_yr_maker": round(cagr(m["equity"], yrs) * NOTIONAL),
        "usd_per_yr_taker": round(cagr(k["equity"], yrs) * NOTIONAL),
        "grid_decomp": grid_decomp,
        "trend_chop": tc,
    }
    return row, rn, ppy, m["equity"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    daily = fetch_daily()
    hourly = fetch_hourly()
    kraken_chk = kraken_daily_check(daily)
    slippage = kraken_depth_slippage()

    # The funder's arithmetic, from the data itself
    dlr = np.log(daily["Close"]).diff().dropna()
    hlr = np.log(hourly["Close"]).diff().dropna()
    mean_abs_daily = float(dlr.abs().mean())
    mean_daily_range = float(((daily["High"] - daily["Low"])
                              / daily["Close"].shift(1)).dropna().mean())
    arithmetic = {
        "mean_abs_daily_move_bps": round(mean_abs_daily * 1e4, 1),
        "median_abs_daily_move_bps": round(float(dlr.abs().median()) * 1e4, 1),
        "mean_daily_highlow_range_bps": round(mean_daily_range * 1e4, 1),
        "mean_abs_hourly_move_bps": round(float(hlr.abs().mean()) * 1e4, 1),
        "pct_days_abs_move_1_2pct": round(
            float(((dlr.abs() >= 0.01) & (dlr.abs() <= 0.02)).mean()), 4),
        "pct_days_abs_move_ge_1pct": round(float((dlr.abs() >= 0.01).mean()), 4),
        "accuracy_needed_daily_1rt": {
            "maker_16bps_rt": round(0.5 + (2 * MAKER) / (2 * mean_abs_daily), 4),
            "taker_60bps_rt": round(0.5 + (2 * TAKER) / (2 * mean_abs_daily), 4),
        },
        "note": "needed accuracy p* = 0.5 + cost_rt/(2*mean|move captured|): "
                "one full-conviction call per day on the mean absolute daily "
                "move; per-variant p* uses that variant's own mean move",
    }

    rows, ret_store = [], {}
    trial_sharpes_ann = []

    def add(name, family, timeframe, bars, runner, params):
        row, rn, ppy, eq = measure(name, family, timeframe, bars, daily,
                                   runner, params)
        rows.append(row)
        ret_store[name] = (bars.index, rn, ppy, eq)
        trial_sharpes_ann.append(row["sharpe_net_maker_ann"])

    # --- a. grids: X x {core, ls} x {daily, hourly} = 12 variants
    for tf, bars in (("daily", daily), ("hourly", hourly)):
        for X in GRID_X:
            for mode in ("core", "ls"):
                name = "grid_%s_%s_X%.0f%%" % (mode, tf, X * 100)
                add(name, "grid_" + mode, tf, bars,
                    lambda fee, b=bars, X=X, m=mode: run_grid(b, X, m, fee),
                    {"X": X, "mode": mode})

    # --- b./c. swing follow + fade: style x tf x Y x Z = 24 variants
    for style in ("follow", "fade"):
        for tf, bars in (("daily", daily), ("hourly", hourly)):
            for Y in SWING_Y:
                for Z in SWING_Z:
                    name = "swing_%s_%s_Y%.0f%%_Z%.0f%%" % (
                        style, tf, Y * 100, Z * 100)
                    add(name, "swing_" + style, tf, bars,
                        lambda fee, b=bars, Y=Y, Z=Z, s=style, L=LOOKBACK[tf]:
                        run_swing(b, Y, Z, s, fee, L),
                        {"Y": Y, "Z": Z})

    n_variants = len(rows)          # searched variants (36)

    # --- d. baselines (measured, counted in N as well — conservative)
    def bh_runner(bars):
        def run(fee):
            c = bars["Close"].values
            eq = c / c[0] * (1.0 - fee)         # one entry fill charged
            return {"equity": eq, "n_fills": 1, "n_rt": 0.5, "ruined": False,
                    "clip_frac": 1.0, "match_rate": None, "trade_grosses": None}
        return run

    add("baseline_buyhold_daily", "baseline", "daily", daily,
        bh_runner(daily), {})
    add("baseline_buyhold_hourlyspan", "baseline", "hourly", hourly,
        bh_runner(hourly), {})
    add("baseline_band10_daily", "baseline", "daily", daily.iloc[1:],
        lambda fee: run_band_rebalance(daily, 0.10, fee), {"band": 0.10})

    n_measured = len(rows)          # 39 incl. baselines
    n_trials = HOUSE_PRIOR_TRIALS + n_measured

    # --- harness: best NET (maker) searched variant, vs buy-and-hold
    searched = rows[:n_variants]
    best = max(searched, key=lambda r: r["net_cagr_maker"])
    b_idx, b_rn, b_ppy, _ = ret_store[best["name"]]
    rho = lag1_autocorr(b_rn)
    n_eff = len(b_rn) * (1.0 - rho) / (1.0 + rho) if rho > 0 else len(b_rn)
    # benchmark: BTC buy-hold bar returns on the same bars
    bh_bars = daily if best["timeframe"] == "daily" else hourly
    bh_rn = bh_bars["Close"].pct_change().dropna().values
    # trial sharpes at the winner's frequency for the DSR variance
    ts_perbar = [s / math.sqrt(b_ppy) for s in trial_sharpes_ann]
    ev = qv.evaluate(
        "btc-swing best net variant: %s" % best["name"], b_rn,
        n_trials=n_trials, trial_sharpes=ts_perbar,
        periods_per_year=b_ppy,
        trades_per_period=max(best["round_trips_per_yr"] / b_ppy, 1e-9),
        n_effective=n_eff, benchmark_returns=bh_rn)
    ev_fallback = qv.deflated_sharpe_ratio(b_rn, n_trials, n_effective=n_eff)

    # best multi-directional variant (ls grid or swing), if different
    multi = [r for r in searched
             if r["family"] in ("grid_ls", "swing_follow", "swing_fade")]
    best_md = max(multi, key=lambda r: r["net_cagr_maker"])
    md_ev = None
    if best_md["name"] != best["name"]:
        m_idx, m_rn, m_ppy, _ = ret_store[best_md["name"]]
        m_rho = lag1_autocorr(m_rn)
        m_neff = (len(m_rn) * (1.0 - m_rho) / (1.0 + m_rho)
                  if m_rho > 0 else len(m_rn))
        md_bh = (daily if best_md["timeframe"] == "daily"
                 else hourly)["Close"].pct_change().dropna().values
        md_ev = qv.evaluate(
            "btc-swing best multi-directional: %s" % best_md["name"], m_rn,
            n_trials=n_trials,
            trial_sharpes=[s / math.sqrt(m_ppy) for s in trial_sharpes_ann],
            periods_per_year=m_ppy,
            trades_per_period=max(best_md["round_trips_per_yr"] / m_ppy, 1e-9),
            n_effective=m_neff, benchmark_returns=md_bh)

    receipt = {
        "date": str(date.today()),
        "thesis": "BTC swings 1-2%/day; $500k on that one asset can be managed "
                  "multi-directionally, even with costs — it can be "
                  "micro-managed. (funder, verbatim)",
        "data": {
            "daily": {"src": "yfinance BTC-USD 1d", "start": str(daily.index[0].date()),
                      "end": str(daily.index[-1].date()), "bars": len(daily)},
            "hourly": {"src": "yfinance BTC-USD 1h (max ~730d)",
                       "start": str(hourly.index[0]), "end": str(hourly.index[-1]),
                       "bars": len(hourly)},
            "kraken_crosscheck": kraken_chk,
        },
        "costs": {"maker_bps_side": 8, "taker_bps_side": 30,
                  "slippage_kraken_book_live": slippage},
        "funder_arithmetic": arithmetic,
        "n_variants_searched": n_variants,
        "n_measured_incl_baselines": n_measured,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "n_trials_total": n_trials,
        "results": rows,
        "harness_best_net": ev,
        "harness_best_net_lag1_autocorr": round(rho, 4),
        "harness_best_net_fallback_dsr": ev_fallback["deflated_sharpe"],
        "harness_best_multidirectional": md_ev,
    }
    with open(RECEIPT, "w") as f:
        json.dump(receipt, f, indent=2, default=str)

    # ---------------- console ----------------
    pc = lambda x: "%+7.2f%%" % (100 * x)
    print("=" * 118)
    print("BTC SWING LAB — the funder's micro-management thesis | maker 8 / taker 30 bps per side, every fill charged")
    print("daily %s->%s (%d bars) | hourly %s->%s (%d bars) | N total %d"
          % (daily.index[0].date(), daily.index[-1].date(), len(daily),
             hourly.index[0].date(), hourly.index[-1].date(), len(hourly),
             n_trials))
    print("=" * 118)
    print("funder arithmetic: mean |daily move| %.0f bps, mean daily H-L range %.0f bps; "
          "days with |move| in [1%%,2%%]: %.0f%%; needed accuracy at 1 RT/day: "
          "maker %.1f%%, taker %.1f%%"
          % (arithmetic["mean_abs_daily_move_bps"],
             arithmetic["mean_daily_highlow_range_bps"],
             100 * arithmetic["pct_days_abs_move_1_2pct"],
             100 * arithmetic["accuracy_needed_daily_1rt"]["maker_16bps_rt"],
             100 * arithmetic["accuracy_needed_daily_1rt"]["taker_60bps_rt"]))
    if "spread_bps" in slippage:
        print("live Kraken book: spread %.2f bps; $500k sweep slip buy/sell %s/%s bps"
              % (slippage["spread_bps"],
                 slippage["sweep_500k"]["buy_slip_bps"],
                 slippage["sweep_500k"]["sell_slip_bps"]))
    print()
    hdr = ("%-28s %6s | %8s %8s %8s | %6s %8s | %6s %8s %8s | %7s %6s %5s"
           % ("variant", "yrs", "grossCAGR", "netMKR", "netTKR", "Sh", "maxDD",
              "RT/yr", "eRT_g", "eRT_m", "capture", "acc", "RUIN"))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        e = r["expectancy_rt_bps"]
        acc = r["accuracy"]["achieved"] if r["accuracy"] else (
            r["match_rate"] if r["match_rate"] is not None else "")
        print("%-28s %6.2f | %8s %8s %8s | %6.2f %8s | %6.0f %8s %8s | %7s %6s %5s"
              % (r["name"], r["years"], pc(r["gross_cagr"]),
                 pc(r["net_cagr_maker"]), pc(r["net_cagr_taker"]),
                 r["sharpe_net_maker_ann"], pc(r["maxdd_net_maker"]),
                 r["round_trips_per_yr"],
                 "" if e["gross"] is None else "%.0f" % e["gross"],
                 "" if e["maker"] is None else "%.0f" % e["maker"],
                 "" if r["capture_ratio_maker"] is None else "%.4f" % r["capture_ratio_maker"],
                 acc if acc == "" else "%.0f%%" % (100 * float(acc)),
                 "RUIN" if r["ruined"] else ""))
    print()
    print("trend/chop split ($/yr on $500k, net maker; monthly efficiency-ratio median split):")
    for r in rows:
        tc = r["trend_chop"]
        print("  %-28s trend %+9.0f   chop %+9.0f   (months %d/%d)"
              % (r["name"], tc["trend_pnl_usd_per_yr"],
                 tc["chop_pnl_usd_per_yr"], tc["months_trend"], tc["months_chop"]))
    print()
    print("grid economics ($/yr on $500k): realized pair harvest vs inventory mark vs core:")
    for r in rows:
        if r["grid_decomp"]:
            gd = r["grid_decomp"]
            print("  %-28s harvest %+9.0f   inventory %+10.0f   core %+9.0f"
                  % (r["name"], gd["harvest_usd_per_yr"],
                     gd["inventory_mark_usd_per_yr"], gd["core_usd_per_yr"]))
    print()
    print("HARNESS — best net (maker) searched variant: %s" % best["name"])
    print("  lag-1 autocorr %.4f -> n_eff %.0f of %d"
          % (rho, ev["n_effective"], ev["n_obs"]))
    for kk in ["sharpe_annualized", "t_stat", "p_value", "n_trials_searched",
               "expected_max_sharpe_under_null", "deflated_sharpe",
               "survives_deflation", "excess_over_benchmark_bps",
               "excess_t_stat", "beats_benchmark"]:
        print("  %-32s %s" % (kk, ev.get(kk)))
    print("  conservative-fallback DSR        %s"
          % round(ev_fallback["deflated_sharpe"], 4))
    print("  " + qv.verdict_line(ev))
    if md_ev:
        print("HARNESS — best multi-directional variant: %s" % best_md["name"])
        for kk in ["sharpe_annualized", "t_stat", "deflated_sharpe",
                   "survives_deflation", "excess_over_benchmark_bps",
                   "excess_t_stat", "beats_benchmark"]:
            print("  %-32s %s" % (kk, md_ev.get(kk)))
        print("  " + qv.verdict_line(md_ev))
    print()
    print("Receipt: %s" % RECEIPT)


if __name__ == "__main__":
    main()
