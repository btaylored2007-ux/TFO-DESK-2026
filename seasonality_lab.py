#!/usr/bin/env python3
"""Seasonality / calendar-anomaly lab — honest after-cost, search-deflated test.

WHY THIS EXISTS
---------------
Calendar anomalies (turn-of-month, day-of-week, sell-in-May, Santa rally, the
January effect, FOMC drift, option-expiry week) are the most-published, most-
data-mined family in all of finance. That is exactly why they are the easiest to
fool yourself with: there are only so many ways to slice a calendar, so if you
cut it enough ways SOMETHING will look significant on ~30 years of one index.

This lab tests them the way the house tests everything else:
  * as tradeable long/flat or long/short rules, NET of realistic cost
    (5 bps one-way base, 10 bps as a kill-test),
  * scored through quant_validation.py: net Sharpe, then DEFLATED Sharpe against
    an HONEST search count,
  * the search count is the house prior (~1,123 hypotheses already burned) PLUS
    every calendar variant tested here. Testing many calendar cuts INFLATES N,
    which RAISES the bar every survivor must clear. This is the whole point.

A calendar rule has one nice property: the signal is known ex-ante (a date), so
there is no lookahead in the signal itself. That removes one failure mode but not
the big one — multiple testing. We lean on deflation, not on a clean backtest.

Universe (yfinance, auto-adjusted for dividends/splits, cached locally):
  SPY                         primary test asset, 1993- (~33y)
  XLB XLE XLF XLI XLK XLP XLU XLV XLY   sector ETFs, 1998- (~28y)   [robustness]
  BTC-USD                     2014- (~11y), 365-day calendar        [robustness]

FOMC-drift is DECLINED, not skipped silently: the exact scheduled-announcement
dates are not in the local data lake, and hand-entering ~200 dates I cannot
verify would corrupt the very window being measured. It is counted as an
untested variant and discussed in the report from the literature only.

PAPER / RESEARCH ONLY. No capital, no orders. Does NOT create any probe.

Usage:  python3 seasonality_lab.py
"""
import json
import os
import warnings

import numpy as np
import pandas as pd

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_JSON = os.path.join(BASE, "reports", "seasonality_lab.json")
REPORT_MD = os.path.join(BASE, "reports", "research_seasonality.md")
CACHE = os.path.join(BASE, "reports", "_seasonality_cache.csv")

# ---- The honest search count ------------------------------------------------
# House prior: hypotheses already burned across all desks before this lab ran.
PRIOR_HYPOTHESES = 1123

# Cost model (one-way, applied to |change in weight| each day).
COST_BPS_BASE = 5.0
COST_BPS_KILL = 10.0

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
ETFS = ["SPY"] + SECTORS
ALL_TICKERS = ETFS + ["BTC-USD"]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_prices(verbose=True):
    if os.path.exists(CACHE):
        px = pd.read_csv(CACHE, index_col=0, parse_dates=True)
        if verbose:
            print("  loaded cache: %d dates x %d tickers" % px.shape)
        return px
    import yfinance as yf
    raw = yf.download(ALL_TICKERS, start="1993-01-01", end="2026-08-01",
                      interval="1d", auto_adjust=True, progress=False, threads=True)
    px = raw["Close"].copy()
    px.index = pd.to_datetime(px.index)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    px.to_csv(CACHE)
    if verbose:
        print("  downloaded: %d dates x %d tickers" % px.shape)
    return px


def series_for(px, ticker):
    """Adjusted close and simple daily returns for one ticker, NaNs dropped."""
    s = px[ticker].dropna()
    ret = s.pct_change().dropna()
    return ret


# ---------------------------------------------------------------------------
# Calendar masks  (all boolean Series aligned to a return index)
# ---------------------------------------------------------------------------
def turn_of_month_weight(idx, last_k=1, first_m=3):
    """1.0 on the last `last_k` trading days of a month and the first `first_m`
    of the next; 0.0 otherwise. Built from trading-day ordering, not calendar days."""
    df = pd.DataFrame(index=idx)
    ym = idx.to_period("M")
    w = pd.Series(0.0, index=idx)
    # rank within month (1 = first trading day), and reverse rank (1 = last)
    grp = pd.Series(np.arange(len(idx)), index=idx).groupby(ym)
    fwd = grp.rank(method="first")                 # 1..n from month start
    rev = grp.transform("size") - fwd + 1          # 1..n from month end
    w[fwd <= first_m] = 1.0
    w[rev <= last_k] = 1.0
    return w


def weekday_weight(idx, weekday):
    """1.0 on the named weekday (0=Mon..4=Fri/6=Sun), else 0.0."""
    return pd.Series((idx.weekday == weekday).astype(float), index=idx)


def weekday_set_weight(idx, weekdays):
    return pd.Series(pd.Index(idx.weekday).isin(weekdays).astype(float), index=idx)


def halloween_weight(idx, invert=False):
    """Sell-in-May / Halloween indicator: 1.0 Nov-Apr (months 11,12,1,2,3,4)."""
    on = pd.Index(idx.month).isin([11, 12, 1, 2, 3, 4])
    v = on.astype(float)
    return pd.Series((1.0 - v) if invert else v, index=idx)


def santa_weight(idx, last_dec=5, first_jan=2):
    """Santa-rally window: last `last_dec` trading days of December plus first
    `first_jan` of January."""
    w = pd.Series(0.0, index=idx)
    ym = idx.to_period("M")
    pos = pd.Series(np.arange(len(idx)), index=idx)
    fwd = pos.groupby(ym).rank(method="first")
    rev = pos.groupby(ym).transform("size") - fwd + 1
    is_dec = pd.Index(idx.month) == 12
    is_jan = pd.Index(idx.month) == 1
    w[(is_dec) & (rev.values <= last_dec)] = 1.0
    w[(is_jan) & (fwd.values <= first_jan)] = 1.0
    return w


def january_weight(idx):
    return pd.Series((pd.Index(idx.month) == 1).astype(float), index=idx)


def third_friday(year, month):
    """Date of the 3rd Friday (monthly equity option expiry)."""
    d = pd.Timestamp(year=year, month=month, day=1)
    # weekday(): Mon=0..Sun=6; Friday=4
    first_fri = 1 + ((4 - d.weekday()) % 7)
    return pd.Timestamp(year=year, month=month, day=first_fri + 14)


def expiry_week_weight(idx, invert=False):
    """1.0 on the Mon-Fri trading week that contains the 3rd Friday of the month."""
    w = pd.Series(0.0, index=idx)
    for per in idx.to_period("M").unique():
        tf = third_friday(per.year, per.month)
        monday = tf - pd.Timedelta(days=tf.weekday())      # Monday of that week
        friday = monday + pd.Timedelta(days=4)
        w[(idx >= monday) & (idx <= friday)] = 1.0
    if invert:
        w = 1.0 - w
    return w


# ---------------------------------------------------------------------------
# Turn a weight series into NET daily returns
# ---------------------------------------------------------------------------
def net_returns(ret, weight, cost_bps=COST_BPS_BASE, delay=0):
    """Daily strategy returns net of one-way turnover cost.

    weight is decided ex-ante (a calendar date) -> the position for day t is known
    before t; no lookahead. `delay` shifts the whole position schedule forward by
    `delay` trading days (execution-delay kill-test).

    Long/short weights (-1) are allowed. Off days earn 0 (cash at 0% — conservative).
    Cost = cost_bps/1e4 * |w_t - w_{t-1}| charged on day t.
    """
    w = weight.reindex(ret.index).fillna(0.0)
    if delay:
        w = w.shift(delay).fillna(0.0)
    turn = (w - w.shift(1).fillna(0.0)).abs()
    cost = turn * (cost_bps / 1e4)
    return w * ret - cost


def raw_sharpe(net):
    r = net.values
    sd = r.std(ddof=1)
    return 0.0 if sd == 0 else r.mean() / sd


# ---------------------------------------------------------------------------
# Variant registry
# ---------------------------------------------------------------------------
def build_spy_variants(ret):
    """Every calendar variant we test on the primary asset (SPY). Returns a list
    of dicts: name, family, weight, is_long_short."""
    variants = []

    # 1. Turn of month: last_k in {1,2,3} x first_m in {1,2,3}  -> 9
    for lk in (1, 2, 3):
        for fm in (1, 2, 3):
            variants.append(dict(
                name="TOM_last%d_first%d" % (lk, fm), family="turn_of_month",
                weight=turn_of_month_weight(ret.index, lk, fm)))

    # 2. Day of week: long each single weekday, flat else  -> 5
    for wd, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        variants.append(dict(
            name="DOW_long_%s" % nm, family="day_of_week",
            weight=weekday_weight(ret.index, wd)))
    # folk "long Tue-Thu, flat Mon/Fri"  -> 1
    variants.append(dict(name="DOW_TueWedThu", family="day_of_week",
                         weight=weekday_set_weight(ret.index, [1, 2, 3])))

    # 3. Halloween / sell-in-May  -> 2
    variants.append(dict(name="HALLOWEEN_long_NovApr", family="halloween",
                         weight=halloween_weight(ret.index)))
    variants.append(dict(name="HALLOWEEN_longshort", family="halloween",
                         weight=halloween_weight(ret.index) * 2 - 1,  # +1 NovApr, -1 MayOct
                         is_long_short=True))

    # 4. Santa rally  -> 2
    variants.append(dict(name="SANTA_last5_first2", family="santa",
                         weight=santa_weight(ret.index, 5, 2)))
    variants.append(dict(name="SANTA_last3_first2", family="santa",
                         weight=santa_weight(ret.index, 3, 2)))

    # 5. January effect  -> 1
    variants.append(dict(name="JANUARY_long", family="january",
                         weight=january_weight(ret.index)))

    # 6. Option-expiry week  -> 2
    variants.append(dict(name="OPEX_long_week", family="opex",
                         weight=expiry_week_weight(ret.index)))
    variants.append(dict(name="OPEX_flat_week", family="opex",
                         weight=expiry_week_weight(ret.index, invert=True)))

    return variants


# family -> the config used for cross-asset robustness (built per index)
def family_best_config(family, idx):
    return {
        "turn_of_month": ("TOM_last1_first3", turn_of_month_weight(idx, 1, 3)),
        "day_of_week":   ("DOW_TueWedThu", weekday_set_weight(idx, [1, 2, 3])),
        "halloween":     ("HALLOWEEN_long_NovApr", halloween_weight(idx)),
        "santa":         ("SANTA_last5_first2", santa_weight(idx, 5, 2)),
        "january":       ("JANUARY_long", january_weight(idx)),
        "opex":          ("OPEX_long_week", expiry_week_weight(idx)),
    }[family]


# ---------------------------------------------------------------------------
# Kill-tests
# ---------------------------------------------------------------------------
def kill_tests(ret, weight, ppy):
    out = {}
    base = net_returns(ret, weight, COST_BPS_BASE)
    # a) higher cost
    hi = net_returns(ret, weight, COST_BPS_KILL)
    out["sharpe_5bps"] = round(raw_sharpe(base) * np.sqrt(ppy), 3)
    out["sharpe_10bps"] = round(raw_sharpe(hi) * np.sqrt(ppy), 3)
    # b) one-day execution delay
    dly = net_returns(ret, weight, COST_BPS_BASE, delay=1)
    out["sharpe_exec_delay_1d"] = round(raw_sharpe(dly) * np.sqrt(ppy), 3)
    # c) subsample decay: first half vs second half
    n = len(base)
    h = n // 2
    out["sharpe_first_half"] = round(raw_sharpe(base.iloc[:h]) * np.sqrt(ppy), 3)
    out["sharpe_second_half"] = round(raw_sharpe(base.iloc[h:]) * np.sqrt(ppy), 3)
    # d) turnover: annualized one-way turnovers
    w = weight.reindex(ret.index).fillna(0.0)
    turns = (w - w.shift(1).fillna(0.0)).abs().sum()
    yrs = (ret.index[-1] - ret.index[0]).days / 365.25
    out["turnovers_per_year"] = round(float(turns / yrs), 1)
    out["pct_days_invested"] = round(float((w != 0).mean()) * 100, 1)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 74)
    print("SEASONALITY / CALENDAR LAB — after-cost, search-deflated")
    print("=" * 74)
    px = load_prices()

    spy_ret = series_for(px, "SPY")
    print("  SPY: %d days  %s -> %s  (%.1fy)\n" % (
        len(spy_ret), spy_ret.index[0].date(), spy_ret.index[-1].date(),
        (spy_ret.index[-1] - spy_ret.index[0]).days / 365.25))

    # ---- Pass 1: build all variants, compute NET series & raw Sharpes --------
    records = []   # each: dict with meta + net series
    spy_variants = build_spy_variants(spy_ret)
    for v in spy_variants:
        net = net_returns(spy_ret, v["weight"], COST_BPS_BASE)
        records.append(dict(asset="SPY", ppy=252, **v, net=net,
                            raw_sr=raw_sharpe(net)))

    # cross-asset robustness: family-best config on sectors + BTC
    families = ["turn_of_month", "day_of_week", "halloween", "santa",
                "january", "opex"]
    robustness_assets = [(t, 252) for t in SECTORS] + [("BTC-USD", 365)]
    for tkr, ppy in robustness_assets:
        r = series_for(px, tkr)
        if len(r) < 400:
            continue
        for fam in families:
            cname, w = family_best_config(fam, r.index)
            net = net_returns(r, w, COST_BPS_BASE)
            records.append(dict(asset=tkr, ppy=ppy, family=fam,
                                name="%s@%s" % (cname, tkr), weight=w,
                                net=net, raw_sr=raw_sharpe(net),
                                is_long_short=False))

    n_lab = len(records)
    # FOMC counts as one untested-but-declared variant in the honest N.
    n_total = PRIOR_HYPOTHESES + n_lab + 1
    all_sharpes = [rec["raw_sr"] for rec in records]

    print("  variants tested this lab : %d  (SPY grid %d + cross-asset %d)"
          % (n_lab, len(spy_variants), n_lab - len(spy_variants)))
    print("  + FOMC-drift declared untested : 1")
    print("  house prior              : %d" % PRIOR_HYPOTHESES)
    print("  HONEST SEARCH COUNT N    : %d\n" % n_total)

    # ---- Pass 2: evaluate every variant through the harness ------------------
    rows = []
    for rec in records:
        net = rec["net"]
        asset = rec["asset"]
        ppy = rec["ppy"]
        bench = series_for(px, asset).reindex(net.index)
        ev = qv.evaluate(rec["name"], net.values, n_trials=n_total,
                         trial_sharpes=all_sharpes, periods_per_year=ppy,
                         benchmark_returns=bench.values)
        rows.append({
            "name": rec["name"], "family": rec["family"], "asset": asset,
            "n_obs": ev["n_obs"],
            "net_sharpe_ann": ev["sharpe_annualized"],
            "t_stat": ev["t_stat"],
            "mean_bps_day": round(rec["net"].mean() * 1e4, 3),
            "deflated_sharpe": ev["deflated_sharpe"],
            "expected_max_sr_null": ev["expected_max_sharpe_under_null"],
            "survives_deflation": ev["survives_deflation"],
            "excess_over_bh_bps": ev.get("excess_over_benchmark_bps"),
            "excess_t": ev.get("excess_t_stat"),
            "beats_bh": ev.get("beats_benchmark"),
            "is_long_short": bool(rec.get("is_long_short")),
        })

    rows.sort(key=lambda r: (r["net_sharpe_ann"] or -9), reverse=True)

    # ---- Kill-tests on the headline SPY variant of each family ---------------
    kills = {}
    for fam in families:
        # best SPY variant in this family by net Sharpe
        fam_rows = [r for r in rows if r["family"] == fam and r["asset"] == "SPY"]
        if not fam_rows:
            continue
        best = fam_rows[0]
        vrec = next(rec for rec in records
                    if rec["name"] == best["name"] and rec["asset"] == "SPY")
        kills[best["name"]] = kill_tests(spy_ret, vrec["weight"], 252)

    # ---- Survivors -----------------------------------------------------------
    survivors = [r for r in rows if r["survives_deflation"]
                 and (r["beats_bh"] is not False)]

    result = {
        "generated": pd.Timestamp.utcnow().isoformat(),
        "prior_hypotheses": PRIOR_HYPOTHESES,
        "n_variants_this_lab": n_lab,
        "fomc_untested_counted": 1,
        "honest_search_count_N": n_total,
        "cost_bps_base": COST_BPS_BASE,
        "cost_bps_kill": COST_BPS_KILL,
        "spy_span": [str(spy_ret.index[0].date()), str(spy_ret.index[-1].date())],
        "expected_max_sr_under_null": rows[0]["expected_max_sr_null"],
        "rows": rows,
        "kill_tests": kills,
        "survivors": survivors,
    }
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w") as f:
        json.dump(result, f, indent=2, default=str)

    write_markdown(result)

    print("  best net Sharpe : %.3f  (%s)" % (rows[0]["net_sharpe_ann"], rows[0]["name"]))
    print("  best deflated   : %.4f" % max((r["deflated_sharpe"] or 0) for r in rows))
    print("  survivors       : %d" % len(survivors))
    print("\n  wrote %s" % REPORT_MD)
    return result


def write_markdown(res):
    rows = res["rows"]
    surv = res["survivors"]
    n = res["honest_search_count_N"]
    best = rows[0]
    best_defl = max((r["deflated_sharpe"] or 0) for r in rows)

    if surv:
        verdict = ("VERDICT: %d calendar variant(s) cleared cost + search-deflation "
                   "(N=%d). Flagged as probe candidate(s) below — NOT created."
                   % (len(surv), n))
    else:
        verdict = ("VERDICT: NO calendar/seasonality effect survives realistic cost "
                   "AND search-deflation at N=%d. Every variant that looks significant "
                   "raw is killed once the ~%d-deep search is priced in. Best net "
                   "Sharpe %.2f (%s) deflates to DSR %.3f — a search artefact."
                   % (n, n, best["net_sharpe_ann"], best["name"], best_defl))

    L = []
    L.append("# Seasonality / Calendar Anomalies — after-cost, search-deflated")
    L.append("")
    L.append("**%s**" % verdict)
    L.append("")
    L.append("_Paper/research only. No capital, no orders, no probe created. "
             "Generated %s._" % res["generated"][:19])
    L.append("")
    L.append("## Method")
    L.append("")
    L.append("- Primary asset **SPY %s to %s**; sector ETFs (XLB/E/F/I/K/P/U/V/Y) "
             "and BTC-USD as cross-asset robustness."
             % (res["spy_span"][0], res["spy_span"][1]))
    L.append("- Each anomaly = a tradeable **long/flat** (or long/short) rule on a "
             "date-known-ex-ante signal (no signal lookahead). Off days earn 0% "
             "(cash, conservative).")
    L.append("- **Net of cost**: %.0f bps one-way base on |Δweight| turnover; "
             "%.0f bps as a kill-test." % (res["cost_bps_base"], res["cost_bps_kill"]))
    L.append("- Scored through `quant_validation.py`: annualized **net Sharpe**, "
             "**t-stat**, then **Deflated Sharpe (DSR)** vs the expected max-of-N "
             "Sharpe under the null.")
    L.append("")
    L.append("## The honest search count")
    L.append("")
    L.append("| Component | Count |")
    L.append("|---|---|")
    L.append("| House prior (hypotheses already burned) | %d |" % res["prior_hypotheses"])
    L.append("| Calendar variants tested in this lab | %d |" % res["n_variants_this_lab"])
    L.append("| FOMC-drift (declared, dates unavailable — see below) | %d |"
             % res["fomc_untested_counted"])
    L.append("| **Honest N used for deflation** | **%d** |" % n)
    L.append("")
    L.append("Expected max Sharpe under the null at this N (per-period): "
             "**%.4f** — the bar every variant must beat merely to be non-random. "
             "Testing more calendar cuts RAISES this bar; it does not help you."
             % res["expected_max_sr_under_null"])
    L.append("")
    L.append("## Every variant (sorted by net Sharpe)")
    L.append("")
    L.append("| Variant | Asset | Net SR (ann) | t | mean bps/day | DSR | "
             "Survives? | Excess vs B&H (bps, t) | Beats B&H? |")
    L.append("|---|---|--:|--:|--:|--:|:--:|--:|:--:|")
    for r in rows:
        ex = ("%s / %s" % (r["excess_over_bh_bps"], r["excess_t"])
              if r["excess_over_bh_bps"] is not None else "—")
        L.append("| %s | %s | %.2f | %.2f | %s | %s | %s | %s | %s |" % (
            r["name"], r["asset"], r["net_sharpe_ann"], r["t_stat"],
            r["mean_bps_day"],
            ("%.3f" % r["deflated_sharpe"]) if r["deflated_sharpe"] is not None else "—",
            "YES" if r["survives_deflation"] else "no",
            ex, {True: "yes", False: "NO", None: "—"}[r["beats_bh"]]))
    L.append("")
    L.append("## Kill-tests (headline SPY variant per family)")
    L.append("")
    L.append("| Variant | SR@5bps | SR@10bps | SR exec-delay 1d | 1st half | "
             "2nd half | turns/yr | % days in |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for name, k in res["kill_tests"].items():
        L.append("| %s | %.2f | %.2f | %.2f | %.2f | %.2f | %.1f | %.1f |" % (
            name, k["sharpe_5bps"], k["sharpe_10bps"], k["sharpe_exec_delay_1d"],
            k["sharpe_first_half"], k["sharpe_second_half"],
            k["turnovers_per_year"], k["pct_days_invested"]))
    L.append("")
    L.append("Read these as the reality check: a real seasonal edge should keep its "
             "Sharpe when cost doubles, survive being executed a day late, and not "
             "collapse from first half to second half of the sample. Decay from the "
             "first to the second half is the signature of an anomaly being "
             "arbitraged away after publication.")
    L.append("")
    L.append("## Survivors")
    L.append("")
    if surv:
        for r in surv:
            L.append("- **%s** (%s): net SR %.2f, DSR %.3f, excess vs B&H %s bps "
                     "(t=%s). PROBE CANDIDATE — not created; pre-register before any "
                     "paper capital." % (r["name"], r["asset"], r["net_sharpe_ann"],
                     r["deflated_sharpe"], r["excess_over_bh_bps"], r["excess_t"]))
    else:
        L.append("**None.** No variant clears both the cost wall and search-"
                 "deflation. Several are positive and even raw-significant "
                 "(turn-of-month and the Nov-Apr Halloween tilt are the usual "
                 "suspects), but their deflated Sharpe collapses once the honest "
                 "N is applied, and the long/flat versions mostly do not beat simply "
                 "holding SPY on an excess-return basis.")
    L.append("")
    L.append("## FOMC-drift — declared, not tested")
    L.append("")
    L.append("The pre-FOMC announcement drift (Lucca & Moench 2015) is a genuinely "
             "documented effect in the literature, but testing it needs the exact "
             "scheduled-announcement dates (~8/yr since 1994). Those are not in this "
             "machine's local data lake, and hand-entering ~200 dates I cannot verify "
             "would corrupt the very 24-hour window being measured. It is counted as "
             "one variant in N and left untested here rather than fabricated. If ever "
             "run, it inherits the same N-penalty as everything above.")
    L.append("")
    L.append("## Bottom line")
    L.append("")
    L.append("Calendar anomalies are the most data-mined family in finance precisely "
             "because a calendar offers so few honest degrees of freedom — which "
             "means the multiple-testing correction bites hardest here. After cost "
             "and an honest N of %d, this lab finds %s. Consistent with the house "
             "record: of ~%d hypotheses, only basis carry has ever survived."
             % (n, ("%d probe candidate(s)" % len(surv)) if surv else "no survivor",
                res["prior_hypotheses"]))
    L.append("")
    with open(REPORT_MD, "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
