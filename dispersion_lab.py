#!/usr/bin/env python3
"""dispersion_lab.py — Honest COLD test of DISPERSION / IMPLIED CORRELATION
(queue item #5): index implied vol vs single-name implied vol.

RESEARCH ONLY. Paper desk. No orders, no capital, no secrets. Read-only except
its own JSON receipt + note.

WHY DISTINCT
------------
Every mechanism killed so far reads PRICES (or price-derived signals). This one
reads the OPTIONS market's opinion about CORRELATION: the spread between the
implied vol of an index and the implied vols of its members. Selling that spread
(short index vol / long single-name vol) is the classic dispersion trade and its
driver — the correlation risk premium — is documented as the most robust premium
in the option market (Driessen/Maenhout/Vilkov 2009).

DATA (honest, free — probed 2026-08-17, see reports/dispersion_data_probe.json)
-------------------------------------------------------------------------------
CBOE publishes daily index history as public CSVs at
  https://cdn.cboe.com/api/global/us_indices/daily_prices/<SYM>_History.csv
Confirmed live and long:
  COR1M 2006-01-03.. (implied correlation, 1m)   COR3M 2006-01-03..
  DSPX  2014-06-19.. (S&P 500 Dispersion Index)  VIX 1990-01-02..
  single-name IV: VXAPL/VXAZN/VXGOG/VXIBM/VXGS 2011-01-07..
  ETF IV: VXEEM/VXEWZ/VXFXI/VXSLV/VXGDX/VXTLT 2011-03-16..
(Yahoo carries these symbols but caps them at 1d/5d — the CBOE CSV is the honest
history source. yfinance option chains are a CURRENT SNAPSHOT only: no historical
option prices exist on any free feed.)

THE STRUCTURAL HONESTY PROBLEM — stated up front, not buried
------------------------------------------------------------
A dispersion trade is an OPTIONS trade. Its P&L cannot be reconstructed without
historical option prices, which no free feed provides. Index levels are NOT
tradeable. So this lab refuses to "backtest" a variance-swap book it cannot
price, and instead splits the question in two:

  PART A (descriptive, no trade): is the correlation risk premium REAL and how
    big? Measured as CBOE implied correlation vs the correlation actually
    realized over the following month by a real 30-name basket. Non-overlapping
    windows, so no overlap inflation. This is a measurement, NOT an alpha claim.

  PART B (tradeable, harness-scored): does the dispersion complex predict
    returns on instruments we can ACTUALLY trade at honest cost — VIXY, SPY,
    IEF, and dollar-neutral cross-sections of the ETFs / single names that have
    their own CBOE vol index? Every variant priced with real costs, borrow swept
    where shorts exist, scored through quant_validation on NET returns.

Report real output, win or lose.
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = ("/private/tmp/claude-501/-Users-test/"
         "494c4ec7-f725-4959-b2a2-1fe37c00c37c/scratchpad/disp_cache")
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/dispersion_lab.json"
NOTE = "reports/research_dispersion.md"

HOUSE_PRIOR_TRIALS = 1301          # HUNT_BACKLOG.md as of 2026-08-16
VOL_TARGET = 0.10
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"

# ---- universes -------------------------------------------------------------
# 30 large caps alive across 2006-2026. SURVIVORSHIP-BIASED UPWARD by construction
# (they are today's survivors); noted in the receipt. Part A measures correlation,
# which is far less sensitive to survivorship than returns are.
BASKET30 = ["AAPL", "MSFT", "XOM", "JNJ", "PG", "JPM", "WMT", "KO", "PFE", "INTC",
            "CSCO", "VZ", "T", "MRK", "PEP", "DIS", "MCD", "HD", "IBM", "MMM",
            "CAT", "BA", "CVX", "BAC", "ORCL", "QCOM", "AMGN", "UNH", "LOW", "GS"]

# tradeable ETF <-> its own CBOE implied-vol index (VXXLE excluded: died 2022-02)
ETF_IV = {"SPY": "VIX", "EEM": "VXEEM", "EWZ": "VXEWZ", "FXI": "VXFXI",
          "SLV": "VXSLV", "GDX": "VXGDX", "TLT": "VXTLT"}
# tradeable single name <-> its own CBOE implied-vol index
NAME_IV = {"AAPL": "VXAPL", "AMZN": "VXAZN", "GOOGL": "VXGOG",
           "IBM": "VXIBM", "GS": "VXGS"}

COST_TIERS = [5.0, 25.0]           # bps per side: liquid tier, desk-conservative tier
BORROW_TIERS = [0.01, 0.03]        # annual short borrow sweep


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def cboe(sym):
    """Daily CLOSE of a CBOE index from the public CSV endpoint."""
    path = os.path.join(CACHE, "cboe_%s.csv" % sym)
    if not os.path.exists(path):
        with urllib.request.urlopen(CBOE_URL % sym, timeout=60) as r:
            data = r.read()
        if b"AccessDenied" in data[:400] or len(data) < 200:
            raise RuntimeError("no CBOE history for %s" % sym)
        with open(path, "wb") as f:
            f.write(data)
    df = pd.read_csv(path)
    df.columns = [c.strip().upper() for c in df.columns]
    col = "CLOSE" if "CLOSE" in df.columns else sym.upper()
    out = pd.Series(df[col].astype(float).values,
                    index=pd.to_datetime(df["DATE"], format="mixed")).sort_index()
    return out[~out.index.duplicated(keep="last")].dropna()


def yf_px(ticker):
    """Total-return daily close (auto-adjusted)."""
    path = os.path.join(CACHE, "yf_%s.csv" % ticker)
    if os.path.exists(path):
        s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        return s.dropna()
    import yfinance as yf
    h = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    if h.empty:
        raise RuntimeError("no data %s" % ticker)
    s = h["Close"].copy()
    s.index = s.index.tz_localize(None)
    s.to_frame("close").to_csv(path)
    return s.dropna()


def load_all():
    iv, px, missing = {}, {}, []
    for sym in ["COR1M", "COR3M", "DSPX", "VIX"] + sorted(set(ETF_IV.values()) |
                                                          set(NAME_IV.values())):
        try:
            iv[sym] = cboe(sym)
        except Exception as e:
            missing.append("cboe:%s (%s)" % (sym, str(e)[:40]))
    for t in sorted(set(BASKET30) | set(ETF_IV) | set(NAME_IV) | {"VIXY", "IEF"}):
        try:
            px[t] = yf_px(t)
        except Exception as e:
            missing.append("yf:%s (%s)" % (t, str(e)[:40]))
    return iv, px, missing


# ---------------------------------------------------------------------------
# PART A — the correlation risk premium (descriptive)
# ---------------------------------------------------------------------------
def realized_corr_window(rets):
    """Two realized average-pairwise-correlation measures on one return window."""
    R = rets.dropna(axis=1, how="any")
    if R.shape[1] < 10 or R.shape[0] < 10:
        return None, None
    C = np.corrcoef(R.values.T)
    off = C[~np.eye(C.shape[0], dtype=bool)]
    pairwise = float(np.nanmean(off))
    # variance-ratio form: the correlation an equal-weight basket actually realized
    w = 1.0 / R.shape[1]
    sig = R.std(ddof=1).values
    port_var = float(R.mean(axis=1).var(ddof=1))
    own = float((w ** 2 * sig ** 2).sum())
    cross = float((w * sig).sum() ** 2 - (w ** 2 * sig ** 2).sum())
    vr = (port_var - own) / cross if cross > 0 else np.nan
    return pairwise, float(vr)


def correlation_premium(iv, px, horizon=21):
    """Implied correlation today vs correlation realized over the NEXT `horizon`
    trading days. Non-overlapping windows only -> no overlap inflation."""
    names = [t for t in BASKET30 if t in px]
    P = pd.DataFrame({t: px[t] for t in names}).sort_index()
    RET = P.pct_change()
    out = {}
    for isym, h in [("COR1M", horizon), ("COR3M", horizon * 3)]:
        if isym not in iv:
            continue
        imp = iv[isym]
        common = RET.index.intersection(imp.index)
        RETc = RET.loc[common]
        rows = []
        i = 252                                     # start after basket warm-up
        while i + h < len(common):
            t0 = common[i]
            win = RETc.iloc[i + 1: i + 1 + h]
            pw, vr = realized_corr_window(win)
            if pw is not None and np.isfinite(pw):
                rows.append({"date": t0, "implied": float(imp.loc[t0]) / 100.0,
                             "realized_pairwise": pw, "realized_varratio": vr})
            i += h                                  # NON-OVERLAPPING step
        if not rows:
            continue
        df = pd.DataFrame(rows).set_index("date")
        prem = df["implied"] - df["realized_pairwise"]
        prem_vr = df["implied"] - df["realized_varratio"]
        n = len(prem)
        t = float(prem.mean() / (prem.std(ddof=1) / math.sqrt(n))) if n > 2 else None
        t_vr = (float(prem_vr.mean() / (prem_vr.std(ddof=1) / math.sqrt(n)))
                if n > 2 and prem_vr.std(ddof=1) > 0 else None)
        eras = {}
        for lab, lo, hi in [("2006-2012", "2006-01-01", "2012-12-31"),
                            ("2013-2019", "2013-01-01", "2019-12-31"),
                            ("2020-2026", "2020-01-01", "2026-12-31")]:
            w = prem[(prem.index >= lo) & (prem.index <= hi)]
            if len(w) > 3:
                eras[lab] = {"n": int(len(w)), "mean_premium": round(float(w.mean()), 4),
                             "t": round(float(w.mean() / (w.std(ddof=1) / math.sqrt(len(w)))), 2),
                             "pct_positive": round(float((w > 0).mean()) * 100, 1)}
        out[isym] = {
            "horizon_days": h, "n_nonoverlapping_windows": int(n),
            "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
            "mean_implied": round(float(df["implied"].mean()), 4),
            "mean_realized_pairwise": round(float(df["realized_pairwise"].mean()), 4),
            "mean_realized_varratio": round(float(df["realized_varratio"].mean()), 4),
            "mean_premium_pairwise": round(float(prem.mean()), 4),
            "t_stat_pairwise": round(t, 2) if t else None,
            "mean_premium_varratio": round(float(prem_vr.mean()), 4),
            "t_stat_varratio": round(t_vr, 2) if t_vr else None,
            "pct_windows_premium_positive": round(float((prem > 0).mean()) * 100, 1),
            "by_era": eras,
        }
    return out


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
def zs(s, win=252):
    return (s - s.rolling(win).mean()) / s.rolling(win).std()


def realized_vol(px_series, win=21):
    r = px_series.pct_change()
    return r.rolling(win).std() * math.sqrt(252) * 100.0        # in vol POINTS


def build_signals(iv, px):
    sig = {}
    if "COR1M" in iv:
        sig["corr_z"] = zs(iv["COR1M"])
    if "DSPX" in iv:
        sig["dspx_z"] = zs(iv["DSPX"])
    if "VIX" in iv and "SPY" in px:
        rv = realized_vol(px["SPY"])
        common = iv["VIX"].index.intersection(rv.index)
        sig["vrp_z"] = zs(iv["VIX"].loc[common] - rv.loc[common])
    return sig


# ---------------------------------------------------------------------------
# PART B — tradeable expressions
# ---------------------------------------------------------------------------
def timing_book(price, signal, freq, cost_bps, borrow, short_when_high=True,
                alt_price=None):
    """Weekly/monthly timed exposure to ONE tradeable instrument.

    short_when_high: position -1 when signal z > 0 (index vol rich), else 0/alt.
    alt_price: if given, hold that instrument instead of cash when flat.
    Returns (net, gross, positions).
    """
    p = price.resample(freq).last().dropna()
    s = signal.resample(freq).last().reindex(p.index)
    a = alt_price.resample(freq).last().reindex(p.index) if alt_price is not None else None
    ret = p.pct_change()
    aret = a.pct_change() if a is not None else None
    dates, net, gross, pos = [], [], [], []
    prev_w = 0.0
    per = {"W": 52.0, "ME": 12.0}[freq]
    for i in range(1, len(p)):
        z = s.iloc[i - 1]
        if not np.isfinite(z):
            continue
        if short_when_high:
            w = -1.0 if z > 0 else 0.0
        else:
            w = 1.0 if z < 0 else 0.0
        r = ret.iloc[i]
        if not np.isfinite(r):
            continue
        g = w * r
        if aret is not None and w == 0.0:
            ar = aret.iloc[i]
            g = ar if np.isfinite(ar) else 0.0
        # AUDIT FIX (self, this run): with an alt instrument a switch is TWO
        # trades (sell SPY, buy IEF), not one. Charging |w-prev_w| alone
        # understated the regime family's cost wall by half.
        turn = abs(w - prev_w) * (2.0 if aret is not None else 1.0)
        cost = turn * (cost_bps / 10000.0)
        sc = (abs(w) if w < 0 else 0.0) * (borrow / per)
        dates.append(p.index[i]); gross.append(g); net.append(g - cost - sc)
        pos.append(w); prev_w = w
    di = pd.DatetimeIndex(dates)
    return pd.Series(net, index=di), pd.Series(gross, index=di), pd.Series(pos, index=di)


def xs_book(px, ivmap, iv, freq, cost_bps, borrow, long_cheap=True, ratio_z=False):
    """Dollar-neutral cross-section on option-implied vol RICHNESS.

    richness_i = implied vol / trailing realized vol   (ETF book), or
    richness_i = z-score of (name implied vol / VIX)   (single-name dispersion book)
    long_cheap=True -> long the cheapest-vol half, short the richest half.
    Returns (net, gross, turnover, mean rank autocorrelation).
    """
    tickers = [t for t in ivmap if t in px and ivmap[t] in iv]
    if len(tickers) < 4:
        return None
    P = pd.DataFrame({t: px[t] for t in tickers}).sort_index()
    RICH = {}
    for t in tickers:
        ivs = iv[ivmap[t]]
        if ratio_z:
            base = iv["VIX"]
            common = ivs.index.intersection(base.index)
            RICH[t] = zs(ivs.loc[common] / base.loc[common])
        else:
            rv = realized_vol(P[t])
            common = ivs.index.intersection(rv.index)
            RICH[t] = (ivs.loc[common] / rv.loc[common]).replace([np.inf, -np.inf], np.nan)
    S = pd.DataFrame(RICH).sort_index()
    Pf = P.resample(freq).last()
    Sf = S.resample(freq).last().reindex(Pf.index)
    RET = Pf.pct_change()
    per = {"W": 52.0, "ME": 12.0}[freq]
    dates, net, gross, turns, acs = [], [], [], [], []
    prev_w = pd.Series(0.0, index=Pf.columns)
    rank_prev = None
    for i in range(1, len(Pf)):
        sig = Sf.iloc[i - 1].dropna()
        fr = RET.iloc[i]
        names = [n for n in sig.index if np.isfinite(fr.get(n, np.nan))]
        sig = sig[names]
        if len(sig) < 4:
            continue
        ranks = sig.rank()
        if rank_prev is not None:
            c = ranks.index.intersection(rank_prev.index)
            if len(c) >= 3 and ranks[c].std() > 0 and rank_prev[c].std() > 0:
                acs.append(float(np.corrcoef(ranks[c], rank_prev[c])[0, 1]))
        rank_prev = ranks
        k = max(1, len(sig) // 2)
        order = sig.sort_values()
        cheap, rich = order.index[:k], order.index[-k:]
        w = pd.Series(0.0, index=Pf.columns)
        if long_cheap:
            w[cheap] = 1.0 / k; w[rich] = -1.0 / k
        else:
            w[cheap] = -1.0 / k; w[rich] = 1.0 / k
        g = float((w * fr.reindex(w.index).fillna(0.0)).sum())
        turn = float((w - prev_w).abs().sum())
        cost = turn * (cost_bps / 10000.0)
        sc = float(w[w < 0].abs().sum()) * (borrow / per)
        dates.append(Pf.index[i]); gross.append(g); net.append(g - cost - sc)
        turns.append(turn); prev_w = w
    if len(dates) < 30:
        return None
    ac = float(np.nanmean(acs)) if acs else 0.8
    di = pd.DatetimeIndex(dates)
    return (pd.Series(net, index=di), pd.Series(gross, index=di),
            pd.Series(turns, index=di), ac)


# ---------------------------------------------------------------------------
# scoring helpers
# ---------------------------------------------------------------------------
def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


def beta_spy(s, px, freq):
    if "SPY" not in px:
        return None
    m = px["SPY"].resample(freq).last().pct_change().dropna()
    c = s.index.intersection(m.index)
    if len(c) < 24:
        return None
    x, y = m.reindex(c).values, s.reindex(c).values
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 24 or np.nanstd(x[ok]) == 0:
        return None
    return round(float(np.polyfit(x[ok], y[ok], 1)[0]), 3)


def pos_autocorr(pos):
    p = pos.values
    if len(p) < 5 or np.nanstd(p) == 0:
        return 0.8
    return float(np.corrcoef(p[:-1], p[1:])[0, 1])


# ---------------------------------------------------------------------------
def main():
    iv, px, missing = load_all()
    sig = build_signals(iv, px)

    partA = correlation_premium(iv, px)

    variants, series = [], {}

    def add(name, family, freq, net, gross, ac, extra):
        per = {"W": 52.0, "ME": 12.0}[freq]
        m = qv.moments(net.values)
        series[name] = (net, gross, ac, freq)
        v = {"name": name, "family": family, "freq": freq,
             "n_obs": int(net.size),
             "net_sharpe_ann": round(m["sharpe"] * math.sqrt(per), 3),
             "net_cagr_pct": round(((1 + net).prod() ** (per / max(1, net.size)) - 1) * 100, 2),
             "maxdd_pct": round(maxdd(net) * 100, 2),
             "rank_or_pos_autocorr": round(ac, 3)}
        v.update(extra)
        variants.append(v)

    # ---- B1 vol-carry timing: short VIXY when index vol is RICH ----
    if "VIXY" in px:
        for sname, s in sig.items():
            for cost in COST_TIERS:
                net, gross, pos = timing_book(px["VIXY"], s, "W", cost, 0.03,
                                              short_when_high=True)
                if net.size > 30:
                    add("volcarry_%s_c%d" % (sname, int(cost)), "volcarry", "W",
                        net, gross, pos_autocorr(pos),
                        {"signal": sname, "cost_bps_side": cost, "borrow": 0.03,
                         "pct_time_short": round(float((pos < 0).mean()) * 100, 1)})

    # ---- B2 equity regime switch: SPY when correlation LOW, else IEF ----
    if "SPY" in px and "IEF" in px:
        for sname, s in sig.items():
            for cost in COST_TIERS:
                net, gross, pos = timing_book(px["SPY"], s, "W", cost, 0.0,
                                              short_when_high=False,
                                              alt_price=px["IEF"])
                if net.size > 30:
                    add("regime_%s_c%d" % (sname, int(cost)), "regime", "W",
                        net, gross, pos_autocorr(pos),
                        {"signal": sname, "cost_bps_side": cost, "borrow": 0.0,
                         "pct_time_equity": round(float((pos > 0).mean()) * 100, 1)})

    # ---- B3 ETF vol-richness cross-section (dollar-neutral) ----
    for freq in ["W", "ME"]:
        for cost in COST_TIERS:
            for borrow in BORROW_TIERS:
                for lc in [True, False]:
                    r = xs_book(px, ETF_IV, iv, freq, cost, borrow, long_cheap=lc)
                    if r:
                        net, gross, turn, ac = r
                        add("etfxs_%s_%s_c%d_b%d" % ("cheap" if lc else "rich",
                                                     freq, int(cost), int(borrow * 100)),
                            "etf_vrp_xs", freq, net, gross, ac,
                            {"long_cheap_vol": lc, "cost_bps_side": cost,
                             "borrow": borrow, "avg_turnover": round(float(turn.mean()), 3)})

    # ---- B4 single-name dispersion cross-section (name IV vs VIX) ----
    for freq in ["W", "ME"]:
        for cost in COST_TIERS:
            for borrow in BORROW_TIERS:
                for lc in [True, False]:
                    r = xs_book(px, NAME_IV, iv, freq, cost, borrow,
                                long_cheap=lc, ratio_z=True)
                    if r:
                        net, gross, turn, ac = r
                        add("namexs_%s_%s_c%d_b%d" % ("cheap" if lc else "rich",
                                                      freq, int(cost), int(borrow * 100)),
                            "name_disp_xs", freq, net, gross, ac,
                            {"long_cheap_vol": lc, "cost_bps_side": cost,
                             "borrow": borrow, "avg_turnover": round(float(turn.mean()), 3)})

    n_here = len(variants)
    if not n_here:
        raise RuntimeError("no variants produced — data path failed")

    scored = sorted(variants, key=lambda v: v["net_sharpe_ann"], reverse=True)
    best = scored[0]
    net, gross, ac, freq = series[best["name"]]
    per = {"W": 52.0, "ME": 12.0}[freq]

    # vol-target BOTH legs with the SAME factor (audit catch #5, 2026-08-16)
    v_ann = net.std(ddof=1) * math.sqrt(per)
    k = (VOL_TARGET / v_ann) if v_ann > 0 else 1.0
    net_s, gross_s = net * k, gross.reindex(net.index) * k

    eff = qv.effective_bets(net_s.size, ac)
    trial_sharpes = [v["net_sharpe_ann"] / math.sqrt({"W": 52.0, "ME": 12.0}[v["freq"]])
                     for v in variants]
    ev = qv.evaluate("dispersion_%s" % best["name"], net_s.values,
                     n_trials=HOUSE_PRIOR_TRIALS + n_here, trial_sharpes=trial_sharpes,
                     periods_per_year=per, trades_per_period=1.0,
                     gross_returns=gross_s.values, n_effective=eff["independent_bets"])

    # Buy-and-hold check: the overnight-lab lesson — a long-equity timing rule
    # that cannot beat simply HOLDING the asset is not a strategy. Compared on
    # the UNSCALED net series so it is like-for-like with the benchmark.
    bench = None
    if "SPY" in px:
        b = px["SPY"].resample(freq).last().pct_change().reindex(net.index).dropna()
        c = net.index.intersection(b.index)
        if len(c) > 30:
            sn, sb = net.reindex(c), b.reindex(c)
            ex = sn - sb
            se = ex.std(ddof=1) / math.sqrt(eff["independent_bets"])
            bench = {
                "benchmark": "SPY buy-and-hold, same dates/frequency",
                "n": int(len(c)),
                "strategy_sharpe_ann": round(float(sn.mean() / sn.std(ddof=1) * math.sqrt(per)), 3),
                "benchmark_sharpe_ann": round(float(sb.mean() / sb.std(ddof=1) * math.sqrt(per)), 3),
                "strategy_cagr_pct": round(((1 + sn).prod() ** (per / len(c)) - 1) * 100, 2),
                "benchmark_cagr_pct": round(((1 + sb).prod() ** (per / len(c)) - 1) * 100, 2),
                "excess_bps_per_period": round(float(ex.mean()) * 10000, 2),
                "excess_t_stat_on_n_eff": round(float(ex.mean() / se), 3) if se > 0 else None,
                "beats_buy_and_hold": bool(se > 0 and (ex.mean() / se) > 1.96),
            }

    # WRC within the best variant's frequency family (strategies in ROWS —
    # the 2026-08-14 transposition lesson)
    fam = [nm for nm in series if series[nm][3] == freq]
    common = None
    for nm in fam:
        common = series[nm][0].index if common is None else common.intersection(series[nm][0].index)
    wrc = qv.whites_reality_check(
        np.vstack([series[nm][0].reindex(common).fillna(0.0).values for nm in fam])) \
        if common is not None and len(common) > 30 else None

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": ("dispersion / implied correlation — index implied vol vs "
                      "single-name implied vol (queue #5)"),
        "data_sources": {
            "cboe_index_csv": CBOE_URL % "<SYM>",
            "cboe_symbols_loaded": sorted(iv.keys()),
            "yahoo_prices_loaded": len(px),
            "missing": missing,
            "options_history": ("NONE free — yfinance option_chain is a current "
                                "snapshot only; no historical option prices exist "
                                "on any free feed"),
        },
        "structural_caveat": ("A dispersion trade is an OPTIONS trade. With no free "
                              "historical option prices its P&L cannot be honestly "
                              "reconstructed, so Part A measures the premium "
                              "descriptively and Part B tests only expressions in "
                              "instruments that actually trade."),
        "survivorship_note": ("BASKET30 is today's survivors — biased upward for "
                              "returns; Part A measures CORRELATION, far less "
                              "sensitive to that bias, but the bias is real."),
        "part_A_correlation_risk_premium": partA,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_here,
        "total_deflation_trials": HOUSE_PRIOR_TRIALS + n_here,
        "cost_tiers_bps_side": COST_TIERS,
        "borrow_tiers_annual": BORROW_TIERS,
        "best_variant": best["name"],
        "best_family": best["family"],
        "best_freq": freq,
        "best_vol_target_scalar": round(float(k), 3),
        "best_effective_bets": eff,
        "best_beta_to_spy": beta_spy(net_s, px, freq),
        "crash_2020_pct": crash(net_s, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "crash_2022_pct": crash(net_s, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
        "benchmark_buy_and_hold": bench,
        "barred_family_note": ("volcarry_* variants are SHORT INDEX VOL via VIXY — "
                               "economically the SVXY-style exposure PERMANENTLY BARRED "
                               "by TOURNAMENT_PREREGISTRATION (vol_lab: -95% maxDD). "
                               "Measured here for completeness; NOT deployable whatever "
                               "they score. Their -81% to -92% maxDD re-earns the bar."),
        "variants": scored,
        "whites_reality_check": wrc,
        "best_evaluate": ev,
        "verdict_line": qv.verdict_line(ev),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    # ------------------------------------------------------------------ print
    print("=" * 84)
    print("DISPERSION / IMPLIED-CORRELATION LAB (queue #5) — CBOE free history")
    print("=" * 84)
    print("  CBOE series: %s" % ", ".join(sorted(iv.keys())))
    if missing:
        print("  MISSING    : %s" % "; ".join(missing))
    print("\n  PART A — correlation risk premium (implied now vs realized next window,")
    print("           NON-OVERLAPPING windows, 30-name basket):")
    for k2, a in partA.items():
        print(f"    {k2}: n={a['n_nonoverlapping_windows']} windows  "
              f"{a['start']}..{a['end']}  horizon {a['horizon_days']}d")
        print(f"      implied {a['mean_implied']:.3f}  realized(pairwise) "
              f"{a['mean_realized_pairwise']:.3f}  realized(var-ratio) "
              f"{a['mean_realized_varratio']:.3f}")
        print(f"      PREMIUM {a['mean_premium_pairwise']:+.4f} (t={a['t_stat_pairwise']})"
              f"   var-ratio {a['mean_premium_varratio']:+.4f} (t={a['t_stat_varratio']})"
              f"   positive {a['pct_windows_premium_positive']}% of windows")
        for era, e in a["by_era"].items():
            print(f"        {era}: n={e['n']:>3}  premium {e['mean_premium']:+.4f}  "
                  f"t={e['t']:+.2f}  positive {e['pct_positive']}%")

    print(f"\n  PART B — {n_here} tradeable variants (deflation trials "
          f"{HOUSE_PRIOR_TRIALS}+{n_here}={HOUSE_PRIOR_TRIALS+n_here})")
    print("    top 12 by annualised NET Sharpe:")
    for v in scored[:12]:
        print(f"      {v['name']:<28} {v['family']:<13} netSh {v['net_sharpe_ann']:+.3f}  "
              f"CAGR {v['net_cagr_pct']:+6.2f}%  maxDD {v['maxdd_pct']:7.2f}%  n={v['n_obs']}")
    print("    worst 3:")
    for v in scored[-3:]:
        print(f"      {v['name']:<28} {v['family']:<13} netSh {v['net_sharpe_ann']:+.3f}  "
              f"CAGR {v['net_cagr_pct']:+6.2f}%")

    print(f"\n  BEST: {best['name']}  (vol-target scalar {out['best_vol_target_scalar']})")
    print(f"    beta->SPY {out['best_beta_to_spy']}   crash2020 {out['crash_2020_pct']}%  "
          f"crash2022 {out['crash_2022_pct']}%")
    print(f"    effective bets {eff['independent_bets']} (autocorr {eff['rank_autocorrelation']}) "
          f"— {eff['verdict']}")
    if bench:
        print(f"    vs BUY-AND-HOLD SPY: strat Sharpe {bench['strategy_sharpe_ann']} / "
              f"CAGR {bench['strategy_cagr_pct']}%   SPY {bench['benchmark_sharpe_ann']} / "
              f"{bench['benchmark_cagr_pct']}%")
        print(f"      excess {bench['excess_bps_per_period']}bps/period  "
              f"t={bench['excess_t_stat_on_n_eff']}  beats_buy_and_hold "
              f"{bench['beats_buy_and_hold']}")
    if wrc:
        print(f"    White's reality check p={wrc['p_value']} survives {wrc['survives']} "
              f"({len(fam)} strategies in family)")
    print("\n  HARNESS on best NET variant (vol-targeted):")
    print(f"    n_obs {ev['n_obs']}  n_effective {ev['n_effective']}  t {ev['t_stat']}  p {ev['p_value']}")
    print(f"    gross/trade {ev.get('gross_per_trade_bps')}bps  net/trade {ev['per_trade_bps']}bps  "
          f"drag {ev.get('cost_drag_bps')}bps")
    print(f"    deflated_sharpe {ev['deflated_sharpe']}  survives_deflation {ev['survives_deflation']}")
    print(f"\n  VERDICT: {out['verdict_line']}")

    g = out["best_beta_to_spy"]
    gate = {"survives_deflation": bool(ev["survives_deflation"]),
            "t_stat>=2": abs(ev["t_stat"]) >= 2.0,
            "n_eff>=20": ev["n_effective"] >= 20,
            "net/trade>0": ev["per_trade_bps"] > 0,
            "|beta|<=0.30": (g is not None and abs(g) <= 0.30)}
    print("\n  GATE PRE-CHECK (autonomous_deploy.py re-checks itself):")
    for kk, vv in gate.items():
        print(f"    {'PASS' if vv else 'FAIL'}  {kk}")
    print("    -> %s" % ("ALL PASS — write probe and invoke the gate"
                         if all(gate.values()) else
                         "does NOT clear the gate; no probe written"))
    print(f"\n  receipt -> {RECEIPT}")
    return out


def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return str(o.date())
    return o


if __name__ == "__main__":
    main()
