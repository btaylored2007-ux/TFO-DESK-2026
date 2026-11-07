#!/usr/bin/env python3
"""Reproducible SYNTHETIC mini-backtest used by The Transparent Trading School v1.2.0.

This file generates a labelled synthetic price series and demonstrates, with
transparent arithmetic:

  - honest lagged signals versus same-bar look-ahead
  - transaction costs
  - missing / halted prints
  - a 2-for-1 split (raw vs split-adjusted)
  - in-sample parameter search versus a frozen out-of-sample window
  - a simple illustration of overfitting (picking the best in-sample lookback)

It does NOT use market data, an order book, or any future-looking input column.
It does NOT claim an edge, alpha, or expected profit. Results are an educational
calculation on invented numbers.

Seed: 20261107
Timezone of the date column: Australia/Sydney calendar dates (no clock times).
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

SEED = 20261107
N_DAYS = 180
IS_DAYS = 120  # first 120 tradable rows are in-sample after we drop non-tradable
OOS_AFTER_IS = 60
START_PRICE = 50.0
SPLIT_EVENT_INDEX = 80  # raw close is halved after this generated step (2-for-1)
HALT_INDEX = 45
MISSING_INDEX = 112
COST_BPS_EACH_WAY = 10  # 0.10%
LOOKBACKS = [5, 10, 15, 20, 30]

ROOT = Path(__file__).resolve().parent
PRICE_CSV = ROOT / "synthetic_mini_backtest.csv"
CA_CSV = ROOT / "synthetic_corporate_actions.csv"
RESULTS = ROOT / "results.json"


def _weekday_dates(n: int) -> list[str]:
    """Invent a weekday date sequence starting 2024-01-02. Labels are SYNTHETIC."""
    # 2024-01-02 was a Tuesday; step forward skipping weekends.
    # We do not claim these prices occurred on those calendar days.
    y, m, d = 2024, 1, 2
    dates = []

    def is_leap(yy: int) -> bool:
        return yy % 4 == 0 and (yy % 100 != 0 or yy % 400 == 0)

    mdays = [0, 31, 29 if is_leap(y) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    # 2024-01-02 = Tuesday -> weekday 1 (Mon=0)
    wd = 1
    while len(dates) < n:
        if wd < 5:
            dates.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        wd = (wd + 1) % 7
        if d > mdays[m]:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
                mdays[2] = 29 if is_leap(y) else 28
    return dates


def generate_raw_path(rng: random.Random) -> list[dict]:
    """Two-regime random walk, then apply a 2-for-1 split to RAW prices."""
    dates = _weekday_dates(N_DAYS)
    rows = []
    px = START_PRICE
    split_factor_running = 1.0
    for i, dt in enumerate(dates):
        # Regime: calmer then choppier. This is invented, not a market claim.
        vol = 0.008 if i < 90 else 0.018
        shock = rng.gauss(0.00015, vol)
        px = max(1.0, px * math.exp(shock))
        if i == SPLIT_EVENT_INDEX:
            # 2-for-1 split: raw price halves; adjustment factor becomes 2 thereafter
            px = px / 2.0
            split_factor_running = 2.0
        high = px * (1 + abs(rng.gauss(0, vol * 0.35)))
        low = px * (1 - abs(rng.gauss(0, vol * 0.35)))
        opn = px * (1 + rng.gauss(0, vol * 0.25))
        if low > min(opn, px):
            low = min(opn, px) * 0.999
        if high < max(opn, px):
            high = max(opn, px) * 1.001
        tradable = 1
        note = "synthetic"
        if i == HALT_INDEX:
            tradable = 0
            note = "synthetic halt: last print is stale; do not treat as tradable"
        if i == MISSING_INDEX:
            tradable = 0
            note = "synthetic missing session: no reliable print"
        rows.append({
            "date": dt,
            "timezone": "Australia/Sydney",
            "open": round(opn, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(px, 4),
            "volume": int(rng.randint(800_000, 2_400_000) if tradable else 0),
            "tradable_flag": tradable,
            "split_adjustment_factor": split_factor_running,
            "series_label": "SYNTHETIC",
            "note": note,
        })
    return rows


def adjusted_close(row: dict) -> float:
    """Point-in-time split-adjusted close = raw_close * split_adjustment_factor.

    After the 2-for-1, raw prices are halved and the factor is 2, so adjusted
    prices stay continuous. Pre-split, factor is 1 so adjusted = raw.
    This column is derived from information known on that date (the split
    already occurred). It is not a future return.
    """
    return row["close"] * row["split_adjustment_factor"]


def sma(values: list[float], n: int, idx: int) -> float | None:
    if idx + 1 < n:
        return None
    window = values[idx + 1 - n: idx + 1]
    if any(v is None for v in window):
        return None
    return sum(window) / n


def run_rule(adj: list[float | None], opens: list[float | None], lookback: int,
             mode: str, cost_bps: float) -> dict:
    """Long-only: hold when prior (honest) or same-bar (look-ahead) close > SMA.

    Honest fill: signal from yesterday's adjusted close vs SMA; trade at today's open.
    Look-ahead fill: signal from today's close vs SMA; fill at today's close.
    Costs charged on position changes only, applied to the fill price.
    Non-tradable days: no new signal, no fill (flat carry if already in; we flatten
    only on an actual opposite signal on a tradable day).
    """
    pos = 0
    equity = 1.0
    trades = 0
    rets = []
    for i in range(1, len(adj)):
        if adj[i] is None or adj[i - 1] is None:
            rets.append(0.0)
            continue
        if mode == "honest":
            # Information available before today's open: yesterday's close and SMA.
            s = sma(adj, lookback, i - 1)
            signal = 1 if (s is not None and adj[i - 1] > s) else 0
            fill = opens[i]
            if fill is None:
                rets.append(0.0)
                continue
        elif mode == "lookahead":
            s = sma(adj, lookback, i)
            signal = 1 if (s is not None and adj[i] is not None and adj[i] > s) else 0
            fill = adj[i]  # cheating: using the close that contains today's move
            if fill is None:
                rets.append(0.0)
                continue
        else:
            raise ValueError(mode)

        turnover = abs(signal - pos)
        cost = turnover * (cost_bps / 10_000.0)
        # Return of existing position from prior fill to this fill, then apply cost.
        # Using adjusted series so the split does not create a fake -50% return.
        if pos == 1 and adj[i] is not None and adj[i - 1]:
            asset_ret = (adj[i] / adj[i - 1]) - 1.0
        else:
            asset_ret = 0.0
        day_ret = pos * asset_ret - cost
        rets.append(day_ret)
        equity *= (1 + day_ret)
        if turnover:
            trades += 1
        pos = signal
    # summarize
    if not rets:
        return {"total_return": 0.0, "trades": 0, "n": 0}
    tot = equity - 1.0
    return {
        "total_return": round(tot, 6),
        "trades": trades,
        "n": len(rets),
        "mean_daily": round(sum(rets) / len(rets), 8),
    }


def slice_series(xs, start, end):
    return xs[start:end]


def main():
    rng = random.Random(SEED)
    rows = generate_raw_path(rng)

    # Write price file: NO future-looking columns (no next_return, no forward labels).
    fields = ["date", "timezone", "open", "high", "low", "close", "volume",
              "tradable_flag", "split_adjustment_factor", "series_label", "note"]
    with PRICE_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with CA_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "timezone", "action", "factor", "series_label", "note"])
        w.writeheader()
        w.writerow({
            "date": rows[SPLIT_EVENT_INDEX]["date"],
            "timezone": "Australia/Sydney",
            "action": "split",
            "factor": "2-for-1",
            "series_label": "SYNTHETIC",
            "note": "Raw prices from this date inclusive are post-split. Use split_adjustment_factor to build a continuous return series. Invented event.",
        })

    adj = [adjusted_close(r) if r["tradable_flag"] else None for r in rows]
    # For SMA continuity we still want a value on halt/missing so lookbacks do not
    # silently skip; honest code treats None as 'no new print'. Carry-forward is
    # shown as a contrasted (bad) choice, not used in the main honest path.
    raw_unadj = [r["close"] if r["tradable_flag"] else None for r in rows]
    opens_adj = [
        (r["open"] * r["split_adjustment_factor"]) if r["tradable_flag"] else None
        for r in rows
    ]

    # Demonstrate the split trap: raw unadjusted return on the split day.
    split_raw_ret = None
    if rows[SPLIT_EVENT_INDEX - 1]["tradable_flag"] and rows[SPLIT_EVENT_INDEX]["tradable_flag"]:
        split_raw_ret = rows[SPLIT_EVENT_INDEX]["close"] / rows[SPLIT_EVENT_INDEX - 1]["close"] - 1.0
        split_adj_ret = adjusted_close(rows[SPLIT_EVENT_INDEX]) / adjusted_close(rows[SPLIT_EVENT_INDEX - 1]) - 1.0
    else:
        split_adj_ret = None

    # Carry-forward stale halt print vs skipping it.
    halt_stale = rows[HALT_INDEX]["close"]
    halt_note = (
        f"On {rows[HALT_INDEX]['date']} tradable_flag=0. Using that close "
        f"({halt_stale}) as if it were a live print would invent a tradable price."
    )

    def evaluate(lookback: int, start: int, end: int) -> dict:
        a = adj[start:end]
        o = opens_adj[start:end]
        honest_nc = run_rule(a, o, lookback, "honest", 0.0)
        honest_c = run_rule(a, o, lookback, "honest", COST_BPS_EACH_WAY)
        look_nc = run_rule(a, o, lookback, "lookahead", 0.0)
        look_c = run_rule(a, o, lookback, "lookahead", COST_BPS_EACH_WAY)
        return {
            "lookback": lookback,
            "honest_no_cost": honest_nc,
            "honest_with_cost": honest_c,
            "lookahead_no_cost": look_nc,
            "lookahead_with_cost": look_c,
        }

    is_results = [evaluate(lb, 0, IS_DAYS) for lb in LOOKBACKS]
    # Pick best in-sample by honest_with_cost total_return (the 'overfit' step).
    best = max(is_results, key=lambda x: x["honest_with_cost"]["total_return"])
    best_lb = best["lookback"]
    oos = evaluate(best_lb, IS_DAYS, IS_DAYS + OOS_AFTER_IS)
    oos_all = [evaluate(lb, IS_DAYS, IS_DAYS + OOS_AFTER_IS) for lb in LOOKBACKS]

    # Unadjusted look-ahead on RAW prices — shows the split as a fake crash signal.
    raw_as_adj = raw_unadj  # intentionally wrong series
    raw_opens = [r["open"] if r["tradable_flag"] else None for r in rows]
    raw_wrong = run_rule(raw_as_adj, raw_opens, 20, "honest", COST_BPS_EACH_WAY)

    results = {
        "label": "SYNTHETIC",
        "disclaimer": (
            "Invented series generated by compute_backtest.py. Not market data, "
            "not an order book, and not evidence of a tradable edge or expected return. "
            "Do not use these numbers as historical performance."
        ),
        "seed": SEED,
        "timezone": "Australia/Sydney",
        "n_days_written": N_DAYS,
        "in_sample_rows": IS_DAYS,
        "out_of_sample_rows": OOS_AFTER_IS,
        "cost_bps_each_way": COST_BPS_EACH_WAY,
        "split": {
            "date": rows[SPLIT_EVENT_INDEX]["date"],
            "action": "2-for-1",
            "raw_close_return_on_split_day": None if split_raw_ret is None else round(split_raw_ret, 6),
            "split_adjusted_return_on_split_day": None if split_adj_ret is None else round(split_adj_ret, 6),
            "lesson": (
                "Unadjusted raw prices drop by about half on a 2-for-1 split and can "
                "look like a crash. Split-adjusted returns remove that discontinuity. "
                "Both views are useful: raw for corporate-action audit, adjusted for return calculations."
            ),
        },
        "halt_and_missing": {
            "halt_date": rows[HALT_INDEX]["date"],
            "missing_date": rows[MISSING_INDEX]["date"],
            "lesson": halt_note,
        },
        "in_sample_by_lookback": is_results,
        "chosen_lookback_by_in_sample_honest_net": best_lb,
        "out_of_sample_for_chosen_lookback": oos,
        "out_of_sample_all_lookbacks": oos_all,
        "raw_unadjusted_honest_with_cost_full_sample": raw_wrong,
        "interpretation": {
            "look_ahead": (
                "The look-ahead columns use today's close both to decide and to fill. "
                "That information is not available at the open. Any extra return there "
                "is an artefact, not a strategy result."
            ),
            "overfitting": (
                "Choosing the lookback with the best in-sample net return and then "
                "reporting only that number is data-snooping. The out-of-sample block "
                "is the honest check. Degradation or sign-change out of sample is expected "
                "when the in-sample ranking fitted noise."
            ),
            "costs": (
                "Comparing no-cost and with-cost rows shows how quickly a thin apparent "
                "edge can vanish. Paper and live trading should use conservative costs."
            ),
            "no_edge_claim": (
                "Because the path is synthetic and the rule is arbitrary, neither a "
                "positive nor a negative total return here is evidence about real markets."
            ),
        },
    }
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({
        "wrote": [str(PRICE_CSV), str(CA_CSV), str(RESULTS)],
        "best_is_lookback": best_lb,
        "is_honest_net": best["honest_with_cost"]["total_return"],
        "oos_honest_net": oos["honest_with_cost"]["total_return"],
        "is_lookahead_net": best["lookahead_with_cost"]["total_return"],
        "split_raw_ret": results["split"]["raw_close_return_on_split_day"],
        "split_adj_ret": results["split"]["split_adjusted_return_on_split_day"],
    }, indent=2))


if __name__ == "__main__":
    main()
