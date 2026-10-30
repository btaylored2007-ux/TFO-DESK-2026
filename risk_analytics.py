"""Pure, signed USD exposure analytics shared by the risk report and dial.

The denominator is current canonical firm equity, including idle cash. Missing
history is reported as unknown risk and closes the dial; it is never silently
renormalised into the measured names. This module cannot trade or write files.
"""
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ANNUAL_DAYS = 252
BOOK_MAX_AGE_SEC = 6 * 3600
EPS = 1e-8


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def inventory_status(coverage, unclaimed=(), now=None, check_files=True):
    """Check the canonical inventory before using its equity denominator."""
    now = now or datetime.now(timezone.utc)
    errors = list(coverage.get("errors") or [])
    books = coverage.get("books") or []
    if coverage.get("n_books") != len(books) or coverage.get("n_books_ok") != len(books):
        errors.append("canonical inventory book counts do not reconcile")
    keys = [b.get("key") for b in books]
    if len(set(keys)) != len(keys):
        errors.append("duplicate canonical book keys")
    position_count = sum(len(b.get("positions") or []) for b in books)
    if coverage.get("n_positions") != len(coverage.get("positions") or []) or position_count != coverage.get("n_positions"):
        errors.append("canonical position counts do not reconcile")
    total = 0.0
    freshness = []
    for b in books:
        eq = b.get("equity_usd")
        if not finite(eq) or eq < 0:
            errors.append("%s has invalid or negative equity" % b.get("key"))
            continue
        total += eq
        active = eq > EPS or bool(b.get("positions"))
        age = None
        live = "live engine HTTP" in (b.get("equity_basis") or "")
        if check_files and active and not live:
            try:
                age = now.timestamp() - os.path.getmtime(b["path"])
                if age < -60 or age > BOOK_MAX_AGE_SEC:
                    errors.append("%s ledger freshness failed (%.0fs)" % (b.get("key"), age))
            except (OSError, KeyError, TypeError):
                errors.append("%s ledger freshness unavailable" % b.get("key"))
        if active and b.get("stale"):
            errors.append("%s canonical valuation is stale" % b.get("key"))
        freshness.append({"book": b.get("key"), "asof": b.get("asof"),
                          "ledger_age_sec": round(age, 1) if age is not None else None,
                          "live_http": live, "stale": bool(b.get("stale"))})
    eq = coverage.get("firm_equity_usd")
    if not finite(eq) or eq <= 0:
        errors.append("firm equity denominator is nonpositive or invalid")
    elif not math.isclose(eq, total, abs_tol=0.05, rel_tol=1e-9):
        errors.append("firm equity differs from canonical book sum")
    if unclaimed:
        errors.append("unclaimed ledger-shaped files in canonical coverage audit")
    return {"complete": not errors, "errors": sorted(set(errors)),
            "firm_equity_usd": eq if finite(eq) else None,
            "n_books": len(books), "n_books_readable": coverage.get("n_books_ok"),
            "unclaimed_ledgers": [x.get("path", str(x)) if isinstance(x, dict) else str(x)
                                  for x in unclaimed],
            "book_freshness": freshness, "book_max_age_sec": BOOK_MAX_AGE_SEC}


def prepare_panel(raw, now=None):
    """Settled daily closes, weekdays only; Monday captures the weekend move.

Forward-fill closed-market dates only AFTER retaining original last-quote dates.
Using 252 on a seven-day crypto panel understates volatility. Today's incomplete
bar is never included in either the estimate or a last-close revaluation.
"""
    now = now or datetime.now(timezone.utc)
    frame = raw.copy()
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(None).normalize()
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.loc[frame.index < pd.Timestamp(now.date())]
    frame = frame.replace([np.inf, -np.inf], np.nan).where(frame > 0)
    last = {t: frame[t].last_valid_index() for t in frame}
    if not frame.empty:
        frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D"))
    frame = frame.ffill()
    return frame.loc[frame.index.dayofweek < 5], last


def risk_statistics(series, equity):
    """All percentage measures refer to firm equity; dollars use that same base."""
    s = series.dropna()
    if len(s) < 2:
        return {"available": False, "reason": "fewer than two return observations"}
    sigma = float(s.std(ddof=1))
    curve = pd.concat([pd.Series([1.0]), (1 + s).cumprod().reset_index(drop=True)], ignore_index=True)
    var95, var99 = float(np.percentile(s, 5)), float(np.percentile(s, 1))
    return {"available": True, "sample_days": len(s),
            "ann_vol_pct": sigma * np.sqrt(ANNUAL_DAYS) * 100,
            "daily_sigma_usd": sigma * equity,
            "var95_1d_pct": var95 * 100, "var95_1d_usd": max(0, -var95) * equity,
            "var99_1d_pct": var99 * 100, "var99_1d_usd": max(0, -var99) * equity,
            "hist_maxdd_pct": float((curve / curve.cummax() - 1).min()) * 100,
            "worst5_pct": float((curve / curve.shift(5) - 1).min()) * 100
            if len(s) >= 5 else None}


def correlation_metrics(returns, weights):
    """HHI measures holdings. Covariance participation measures risk dimensions.

Effective risk dimensions = tr(C)^2 / tr(C^2), where C is the covariance of
signed per-instrument equity-return contributions. It is not a count of proven
independent bets. Equal independent contributions give N; perfect correlation
gives 1. Diversification ratio separately captures hedging/cancellation.
"""
    active = {t: w for t, w in weights.items() if abs(w) > EPS}
    total = sum(abs(w) for w in active.values())
    hhi = sum((abs(w) / total) ** 2 for w in active.values()) if total else None
    out = {"effective_holdings": 1 / hhi if hhi else None,
           "holdings_basis": "inverse HHI of absolute net-by-ticker USD exposures; ignores correlation",
           "effective_risk_dimensions": None, "diversification_ratio": None,
           "risk_dimensions_definition": "tr(C)^2 / tr(C^2), covariance of signed instrument contributions; not independent bets"}
    if not active or len(returns) < 2:
        return out
    columns = list(active)
    w = np.array([active[t] for t in columns])
    cov = returns[columns].cov().to_numpy()
    contribution_cov = cov * np.outer(w, w)
    trace = float(np.trace(contribution_cov))
    denom = float(np.square(contribution_cov).sum())
    if denom > 1e-24:
        out["effective_risk_dimensions"] = trace * trace / denom
    variance = max(0.0, float(w @ cov @ w))
    if variance > 1e-24:
        out["diversification_ratio"] = float(np.abs(w) @ np.sqrt(np.maximum(0, np.diag(cov))) / np.sqrt(variance))
    return out


def analyze(coverage, raw, *, window=None, min_return_days=63, factors=None,
            now=None, unclaimed=(), check_files=True):
    """Measure the covered positions without renormalizing away idle cash/errors.

Every nonzero missing exposure (even one dollar) makes risk incomplete. Covered
estimates remain useful but are explicitly partial, and cannot open the dial.
"""
    now = now or datetime.now(timezone.utc)
    inventory = inventory_status(coverage, unclaimed, now, check_files)
    equity = inventory["firm_equity_usd"]
    panel, last_quotes = prepare_panel(raw, now)
    net, gross, by_book = {}, {}, {}
    errors = list(inventory["errors"])
    unvaluable = []
    missing = []
    positions = [dict(p) for p in (coverage.get("positions") or [])]
    remark_count, remark_delta, remaining_cost = 0, 0.0, 0.0
    remark_refusals = []
    for p in positions:
        value = p.get("fallback_usd")
        label = p.get("label", "unnamed")
        if not finite(value):
            unvaluable.append(label)
            continue
        if abs(value) <= EPS:
            # Nonzero units with no dollar value are unknown risk, not cash.
            if abs(p.get("units") or 0) > EPS:
                unvaluable.append(label)
            continue
        t = p.get("ticker")
        # Keep authoritative book marks. Only genuine entry-cost fallbacks need
        # a price-history revaluation; otherwise the historical close would
        # overwrite a newer book mark. Equity remains the canonical marked NAV.
        if "entry cost" in (p.get("fallback_basis") or "").lower():
            fx, units = p.get("mark_fx_usd"), p.get("units")
            last = last_quotes.get(t)
            max_age = 2 if t and (t.startswith(("KRK:", "BINANCE:")) or t.endswith("-USD")) else 4
            fresh = last is not None and (pd.Timestamp(now.date()) - last).days <= max_age
            if t in panel and panel[t].notna().any() and fresh and finite(fx) and fx > 0 and finite(units) and units > 0:
                candidate = math.copysign(units * float(panel[t].dropna().iloc[-1]) * fx, value)
                if abs(candidate) <= 25 * abs(value):
                    remark_delta += abs(candidate) - abs(value)
                    value = candidate
                    p["fallback_usd"] = value
                    remark_count += 1
                else:
                    remark_refusals.append(label + ": entry-cost revaluation exceeded 25x sanity guard")
                    remaining_cost += abs(value)
            else:
                remaining_cost += abs(value)
                if t in panel and fresh and (not finite(fx) or fx <= 0):
                    remark_refusals.append(label + ": entry-cost revaluation has no valid FX conversion")
        if not t or t == "KRK:":
            t = "UNPRICED:" + label
        net[t] = net.get(t, 0.0) + value
        gross[t] = gross.get(t, 0.0) + abs(value)
        b = p.get("book", "unknown")
        by_book.setdefault(b, {})[t] = by_book.setdefault(b, {}).get(t, 0.0) + value
    quote_status = {}
    for t in gross:
        last = last_quotes.get(t)
        age = (pd.Timestamp(now.date()) - last).days if last is not None else None
        # Four calendar days tolerates weekends plus a normal exchange holiday.
        max_age = 2 if t.startswith(("KRK:", "BINANCE:")) or t.endswith("-USD") else 4
        count = int(panel[t].notna().sum()) - 1 if t in panel else 0
        reasons = []
        if last is None:
            reasons.append("no price history")
        elif age > max_age:
            reasons.append("last settled quote is stale")
        if count < min_return_days:
            reasons.append("insufficient return history")
        quote_status[t] = {"last_settled_quote": str(last.date()) if last is not None else None,
                           "age_calendar_days": age, "max_age_calendar_days": max_age,
                           "return_observations": max(0, count), "reasons": reasons}
        if reasons:
            missing.append(t)
    covered = [t for t in gross if t not in missing]
    gross_usd = sum(gross.values())
    missing_usd = sum(gross[t] for t in missing)
    if missing:
        errors.append("unpriced, stale or short-history exposure")
    if unvaluable:
        errors.append("positions with unknown USD value")
    if remark_refusals:
        errors.append("entry-cost exposure revaluation refused")
    mapped_pct = max(0.0, 100 * (1 - missing_usd / equity)) if equity and equity > 0 else 0.0
    valid_equity = finite(equity) and equity > 0
    weights = {t: net[t] / equity for t in covered} if valid_equity else {}
    needed_factors = [t for t in (factors or {}) if t in panel]
    returns = panel.pct_change(fill_method=None)
    if covered:
        measured = returns[covered].dropna()
    else:
        measured = pd.DataFrame(index=returns.index[1:])
    if window:
        measured = measured.tail(window)
    if not gross and valid_equity and len(measured) < min_return_days:
        measured = pd.DataFrame(index=pd.bdate_range(end=pd.Timestamp(now.date()) - pd.Timedelta(days=1),
                                                     periods=window or min_return_days))
    if gross and len(measured) < min_return_days:
        errors.append("common return window shorter than required")
    # An all-cash firm has known zero market exposure. A wholly-unpriced firm
    # must never get a synthetic zero-volatility series.
    if valid_equity and (covered or not gross) and len(measured) >= 2:
        firm = pd.Series(0.0, index=measured.index)
        for t, w in weights.items():
            firm = firm + measured[t] * w
        stats = risk_statistics(firm, equity)
    else:
        firm = pd.Series(dtype=float)
        stats = {"available": False, "reason": "no measurable exposure sample or invalid equity"}
    betas, multivariate, book_stats, overlaps = {}, {}, [], []
    book_returns = pd.DataFrame(index=firm.index)
    for book, exposures in by_book.items():
        if not valid_equity:
            continue
        series = pd.Series(0.0, index=firm.index)
        for t, v in exposures.items():
            if t in measured:
                series = series + measured[t].reindex(firm.index) * (v / equity)
        book_returns[book] = series
        contribution = float(series.cov(firm) / firm.var() * 100) if len(firm) > 1 and firm.var() > 1e-24 else None
        book_stats.append({"book": book,
                           "gross_usd": sum(abs(p.get("fallback_usd") or 0) for p in positions if p.get("book") == book),
                           "net_usd": sum(exposures.values()),
                           "variance_contribution_pct": contribution,
                           "unpriced_gross_usd": sum(abs(p.get("fallback_usd") or 0) for p in positions
                                                     if p.get("book") == book and p.get("ticker") in missing)})
    factor_frame = returns[needed_factors].reindex(firm.index).dropna() if needed_factors else pd.DataFrame()
    if len(factor_frame) >= 3:
        y = firm.reindex(factor_frame.index)
        for t in needed_factors:
            f = factor_frame[t]
            if f.var() > 1e-24:
                betas[factors[t]] = float(y.cov(f) / f.var())
        active_f = [t for t in needed_factors if factor_frame[t].var() > 1e-24]
        if active_f and y.var() > 1e-24:
            x = np.column_stack([np.ones(len(y)), factor_frame[active_f].values])
            coef = np.linalg.lstsq(x, y.values, rcond=None)[0]
            multivariate = {"betas": {factors[t]: float(coef[i + 1]) for i, t in enumerate(active_f)},
                            "r_squared": float(1 - np.sum((y.values - x @ coef) ** 2) /
                                               np.sum((y.values - y.mean()) ** 2)),
                            "sample_days": len(y), "method": "simultaneous OLS with intercept; descriptive, not causal or alpha evidence"}
    if len(book_returns) >= 3:
        corr = book_returns.corr()
        names = list(corr.columns)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                value = corr.loc[a, b]
                if pd.notna(value) and abs(value) >= 0.7:
                    overlaps.append({"book_a": a, "book_b": b, "correlation": float(value)})
    return {"inventory": inventory, "equity_usd": equity, "complete": not errors,
            "errors": sorted(set(errors)), "statistics": stats,
            "gross_usd": gross_usd, "net_usd": sum(net.values()),
            "netted_gross_usd": sum(abs(v) for v in net.values()),
            "unpriced_gross_usd": missing_usd,
            "covered_gross_usd": gross_usd - missing_usd,
            "mapped_capital_pct": mapped_pct,
            "position_history_coverage_pct": 100 * (1 - missing_usd / gross_usd) if gross_usd else 100.0,
            "missing_instruments": sorted(missing), "unvaluable_positions": unvaluable,
            "price_freshness": quote_status, "weights": weights, "net_exposures_usd": net,
            "valuation": {"positions_remarked": remark_count,
                          "positions_not_remarked": len(positions) - remark_count,
                          "gross_stored_usd": sum(abs(p.get("fallback_usd") or 0) for p in coverage.get("positions", [])
                                                  if finite(p.get("fallback_usd"))),
                          "gross_marked_usd": gross_usd,
                          "mark_vs_cost_delta_usd": remark_delta,
                          "mark_vs_cost_delta_pct": remark_delta / (gross_usd - remark_delta) * 100
                          if gross_usd - remark_delta > EPS else 0.0,
                          "still_at_cost_usd": remaining_cost,
                          "remark_refusals": remark_refusals,
                          "basis": "authoritative book marks retained; genuine entry-cost fallbacks only revalued at fresh settled closes and current FX; denominator remains canonical marked equity"},
            "concentration": correlation_metrics(measured, weights), "factor_betas": betas,
            "common_factor_model": multivariate,
            "book_risk_contributions": sorted(book_stats, key=lambda x: -(x["variance_contribution_pct"] or 0)),
            "book_correlations": sorted(overlaps, key=lambda x: -abs(x["correlation"])),
            "sample_start": str(firm.index.min().date()) if len(firm) else None,
            "sample_end": str(firm.index.max().date()) if len(firm) else None,
            "return_series": firm, "panel": panel,
            "method": "signed current USD position exposure / canonical current firm equity; cash earns zero in price-risk proxy; 252 weekday returns, no missing-risk renormalization; listing FX held at current conversion; same-ticker hedges omit basis/counterparty risk"}
