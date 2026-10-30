#!/usr/bin/env python3
"""Basis carry monitor — watching the one trade that actually clears costs.

WHY THIS EXISTS
---------------
Delta-neutral basis carry (long spot X, short perp X) is the only strategy on this
desk that survives its own cost wall. Measured over 2025-08 -> 2026-08:

    BTC-only    +3.83%/yr gross   t_NW 5.35   Sharpe 4.9   maxDD 0.57%
    BTC+ETH     +3.57%/yr gross   t_NW 5.75   Sharpe 5.7   maxDD 0.42%
    break-even round-trip cost  ~765 bps (BTC) / ~714 bps (BTC+ETH)
    current model cost           ~90 bps taker / 50 bps maker round trip

It clears by roughly 8x, because a coin-for-coin hedge never needs re-hedging —
turnover is about one trade a year. That is the entire reason it works, and it is
the mechanism nothing else on this desk has.

THE PROBLEM IS THE RATE, NOT THE MECHANISM. Quarterly BTC+ETH carry ran
9.94 -> 4.19 -> 1.89 -> 0.42 -> 4.85, and over the most recent 127 days BTC paid
+0.85% at t = 1.03 — statistically indistinguishable from zero. Funding is set by
market demand for perp leverage; it is regime-dependent, not permanently dead.

So this monitor does not try to force a trade. It watches, records the regime, and
says plainly when carry returns to a level worth harvesting — so the desk is
already positioned when it turns instead of reading about it afterwards.

HONEST LIMITS
-------------
* Funding here is the CURRENT rate. Realised carry depends on the rate persisting;
  a high instantaneous print is not an annualised return.
* Perp short carries liquidation risk on an adverse spot move (BTC's worst 1-day
  adverse move in sample: +12.2%; 30-day: +18.7%). Size for a 25-30% adverse move,
  <=3x on the futures leg.
* Basis dislocation in a liquidation cascade scales inversely with liquidity:
  on 2025-10-11 ADA -2.89%, SOL -0.23%, ETH -0.19%, BTC +0.02%. Restricting to BTC
  removes most of the tail.
* Venue concentration and unhedged AUD/USD are NOT modelled.
* PAPER ONLY. This monitors and reports. It never places an order.

Usage:
    python3 carry_monitor.py               # current snapshot
    python3 carry_monitor.py --history     # regime history from collected funding
    python3 carry_monitor.py --json
"""
import argparse
import glob
import json
import os
import statistics
import time

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
FUNDING_GLOB = os.path.join(KRAKEN_DESK, "reports", "microstructure", "funding_*.jsonl")
STATE_FILE = os.path.join(BASE, "reports", "carry_monitor.json")
HISTORY_FILE = os.path.join(BASE, "reports", "carry_history.jsonl")

# CM-071 (2026-09-03): Kraken Futures charges funding HOURLY, not per 8 hours.
# The old comment below was wrong and the constant with it, understating every
# published carry number on this desk by 8x.
#
# Measured, 2,009 samples over 168h: under the old per-8h assumption BTC funding
# reads 0.872%/yr and ETH 0.876%/yr. Sustained sub-1%/yr funding on the two
# largest perps is not a real market state. Under the hourly reading the same
# data gives BTC 6.978%/yr and ETH 7.009%/yr, which is an ordinary funding
# regime. The data chose the constant; the docstring had simply been wrong.
INTERVALS_PER_YEAR = 24 * 365

# Round-trip cost of establishing AND unwinding both legs, in bps. Taker on both
# legs is the conservative assumption.
COST_BPS_TAKER = 90.0
COST_BPS_MAKER = 50.0  # current executor: 25 bps/side; modelled, not a fill guarantee

# What counts as worth doing. The trade is held ~1 year, so the cost is amortised
# once — but a carry below the cost is simply a loss, and one barely above it is
# not worth the liquidation risk.
# CM-071: the thresholds are raised 8x IN THE SAME COMMIT so that correcting the
# annualisation changes what is REPORTED and nothing about what is TRADED.
#
# This matters more than the arithmetic. The old gate demanded 4% of an 8x-
# understated number — a true 32%/yr hurdle, which is why the book opened 2
# episodes in 392 days. Fixing the constant while leaving the gate at 4.0 would
# have silently loosened a live entry gate by 8x and looked like a bug fix. The
# desk's own rule test measured the direction of that mistake: relaxing this gate
# from 4% to 2% cost -4.27 pp/yr.
#
# 32.0 and 16.0 are therefore NOT new risk settings — they are the old settings
# restated in corrected units. Whether a true 32%/yr hurdle is the right one is a
# separate question, now answerable for the first time in honest units, and it is
# the funder's to decide. See CARRY_ANNUALISATION_AMENDMENT.md.
HARVEST_THRESHOLD_PCT = 32.0    # annualised, gross (was 4.0 in 8x-understated units)
WATCH_THRESHOLD_PCT = 16.0      # (was 2.0 in the same understated units)

# The tail-risk finding: restrict to deep names. Illiquid perps dislocate hardest
# in a cascade, which is exactly when a "market-neutral" book stops being neutral.
# Kraken keys bitcoin as XBT on the futures side.
BASE_ALIAS = {"XBT": "BTC"}
PREFERRED = ["BTC", "ETH"]
MIN_VOL24H_USD = 1_000_000.0


def load_funding(hours=None):
    """Read collected funding snapshots. Returns rows sorted by time."""
    rows = []
    cutoff = time.time() - hours * 3600 if hours else None
    for fn in sorted(glob.glob(FUNDING_GLOB)):
        try:
            with open(fn) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if cutoff and r.get("t", 0) < cutoff:
                        continue
                    if r.get("fundingRate") is None:
                        continue
                    rows.append(r)
        except OSError:
            continue
    rows.sort(key=lambda r: r.get("t", 0))
    return rows


def relative_rate(funding_rate, mark_price):
    """Kraken Futures quotes fundingRate as an ABSOLUTE rate, not a fraction.

    It must be divided by the mark price to get the relative per-interval rate.
    Skipping this produced a first run reading 51,542% annualised on XBT and a
    false HARVESTABLE flag — a unit error that would have manufactured a signal
    out of nothing.
    """
    try:
        mp = float(mark_price)
        if mp <= 0:
            return None
        return float(funding_rate) / mp
    except (TypeError, ValueError):
        return None


def annualise(rate_per_interval):
    """Relative per-8h funding rate -> annualised percent.

    A SHORT perp position RECEIVES funding when the rate is positive, so positive
    funding is positive carry for this trade.
    """
    if rate_per_interval is None:
        return None
    return rate_per_interval * INTERVALS_PER_YEAR * 100.0


def latest_by_base(rows):
    """Most recent funding print per asset."""
    out = {}
    for r in rows:
        b = r.get("base")
        if not b:
            continue
        b = BASE_ALIAS.get(b, b)
        if b not in out or r.get("t", 0) > out[b].get("t", 0):
            out[b] = r
    return out


def snapshot(rows=None):
    rows = rows if rows is not None else load_funding()
    if not rows:
        return {"error": "no funding data collected yet"}
    latest = latest_by_base(rows)

    assets = []
    for base, r in latest.items():
        base = BASE_ALIAS.get(base, base)
        vol = float(r.get("vol24h") or 0) * float(r.get("markPrice") or 0)
        ann = annualise(relative_rate(r["fundingRate"], r.get("markPrice")))
        if ann is None:
            continue
        pred = annualise(relative_rate(r.get("fundingRatePrediction"),
                                       r.get("markPrice")))
        assets.append({
            "base": base,
            "perp": r.get("perp"),
            "funding_rate": r["fundingRate"],
            "annualised_pct": round(ann, 3),
            "predicted_annualised_pct": round(pred, 3) if pred is not None else None,
            "mark": r.get("markPrice"),
            "vol24h_usd": round(vol, 0),
            "liquid": vol >= MIN_VOL24H_USD,
            "age_sec": round(time.time() - r.get("t", 0)),
        })
    assets.sort(key=lambda a: -a["annualised_pct"])

    stale_assets = [a for a in assets if not 0 <= a["age_sec"] < 1800]
    assets = [a for a in assets if 0 <= a["age_sec"] < 1800]
    liquid = [a for a in assets if a["liquid"]]
    preferred = [a for a in assets if a["base"] in PREFERRED]

    def verdict(ann):
        if ann >= HARVEST_THRESHOLD_PCT:
            return "HARVESTABLE"
        if ann >= WATCH_THRESHOLD_PCT:
            return "watch"
        if ann > 0:
            return "positive but thin"
        return "negative — short perp PAYS funding here"

    btc = next((a for a in assets if a["base"] == "BTC"), None)
    pref_mean = (round(statistics.mean(a["annualised_pct"] for a in preferred), 3)
                 if len(preferred) == len(PREFERRED) else None)

    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_assets": len(assets),
        "n_liquid": len(liquid),
        "btc_annualised_pct": btc["annualised_pct"] if btc else None,
        "preferred_mean_annualised_pct": pref_mean,
        "preferred_verdict": verdict(pref_mean) if pref_mean is not None else None,
        "median_annualised_pct": round(
            statistics.median(a["annualised_pct"] for a in liquid), 3) if liquid else None,
        "share_positive": round(
            sum(1 for a in liquid if a["annualised_pct"] > 0) / len(liquid) * 100, 1)
        if liquid else None,
        "cost_bps_taker": COST_BPS_TAKER,
        "cost_bps_maker": COST_BPS_MAKER,
        "harvest_threshold_pct": HARVEST_THRESHOLD_PCT,
        "assets": assets,
        "stale_assets": stale_assets,
        "funding_max_age_sec": 1800,
        "top": assets[:10],
        "bottom": assets[-5:],
    }


def record(snap):
    """Append a compact row so the regime can be tracked over weeks."""
    if snap.get("error"):
        return
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({
            "t": round(time.time(), 1),
            "checked_at": snap["checked_at"],
            "btc": snap["btc_annualised_pct"],
            "preferred_mean": snap["preferred_mean_annualised_pct"],
            "median_liquid": snap["median_annualised_pct"],
            "share_positive": snap["share_positive"],
        }) + "\n")
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f, indent=2)
    os.replace(tmp, STATE_FILE)


def history():
    rows = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        pass
    return rows


def print_snapshot(s):
    if s.get("error"):
        print("carry monitor: %s" % s["error"])
        return
    print("=" * 76)
    print("BASIS CARRY MONITOR — long spot / short perp, same asset   [%s]" % s["checked_at"])
    print("=" * 76)
    print("PAPER ONLY. This monitors and reports; it never places an order.\n")

    pv = s["preferred_verdict"]
    flag = "***" if pv == "HARVESTABLE" else "   "
    print("%s BTC+ETH mean carry : %s%% annualised  -> %s"
          % (flag, s["preferred_mean_annualised_pct"], pv))
    print("    BTC alone          : %s%% annualised" % s["btc_annualised_pct"])
    print("    median liquid perp : %s%%   (%s%% of liquid perps positive)"
          % (s["median_annualised_pct"], s["share_positive"]))
    print("    harvest threshold  : %.1f%%   round-trip cost %.0f bps taker / %.0f maker"
          % (s["harvest_threshold_pct"], s["cost_bps_taker"], s["cost_bps_maker"]))
    print()

    print("HIGHEST CARRY (short perp receives funding)")
    print("  %-7s %12s %12s %14s %s" % ("asset", "annual %", "predicted %", "24h vol $", ""))
    for a in s["top"]:
        print("  %-7s %11.2f%% %11s%% %14s %s"
              % (a["base"], a["annualised_pct"],
                 a["predicted_annualised_pct"] if a["predicted_annualised_pct"] is not None else "  n/a",
                 "{:,.0f}".format(a["vol24h_usd"]),
                 "" if a["liquid"] else "ILLIQUID — cascade risk"))
    print()
    print("MOST NEGATIVE (short perp PAYS — carry runs against this trade)")
    for a in s["bottom"]:
        print("  %-7s %11.2f%%" % (a["base"], a["annualised_pct"]))

    print()
    if pv == "HARVESTABLE":
        print("  >>> Basket signal clears its threshold; existing PAPER executor gates still apply.")
        print("      This is not validation or permission to increase the registered capital cap.")
    else:
        print("  Carry is below the basket entry threshold. No new entry signal.")
        print("  The current rule remains unvalidated; latest funding is not earned net yield.")


def print_history(rows):
    if not rows:
        print("No carry history yet — run this repeatedly to build the regime record.")
        return
    print("CARRY REGIME HISTORY (%d snapshots)" % len(rows))
    print("  %-21s %10s %14s %14s" % ("when", "BTC %", "BTC+ETH %", "median liq %"))
    for r in rows[-25:]:
        print("  %-21s %9s%% %13s%% %13s%%"
              % (r["checked_at"], r.get("btc"), r.get("preferred_mean"),
                 r.get("median_liquid")))
    vals = [r["preferred_mean"] for r in rows if r.get("preferred_mean") is not None]
    if len(vals) > 1:
        print("\n  BTC+ETH carry over the record: min %.2f%%  median %.2f%%  max %.2f%%"
              % (min(vals), statistics.median(vals), max(vals)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hours", type=int, default=None,
                    help="only use funding collected in the last N hours")
    args = ap.parse_args()

    if args.history:
        print_history(history())
        return
    s = snapshot(load_funding(args.hours))
    record(s)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print_snapshot(s)


if __name__ == "__main__":
    main()
