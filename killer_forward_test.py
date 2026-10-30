#!/usr/bin/env python3
"""killer_forward_test.py — is the desk's adversarial verifier CALIBRATED? PAPER ONLY.

THE QUESTION THIS ANSWERS, WHICH NOTHING ELSE ON THE DESK DOES
--------------------------------------------------------------
Every hunt this desk has run ends the same way: candidates are found, adversarial
killers refute them, zero survive. Across five sweeps that is 160+ domains and
~500 agents for zero positions.

There are exactly two explanations and they have opposite consequences:

  A. THE KILLERS ARE RIGHT. The candidates really were value traps, and the
     process has saved the firm from a series of losses. Refusing to trade is the
     product, not a failure of it.

  B. THE KILLERS ARE TOO AGGRESSIVE. They are trained to "default to refuted=true
     if uncertain", which is correct for avoiding fabrication and WRONG if it
     rejects every real situation. In that case the desk is grinding through
     hundreds of agents to systematically talk itself out of money.

Nobody has ever measured which. This does, and it costs nothing to run: the
refuted names are tracked as a PAPER basket with no capital deployed. In 63
trading days the tape answers it.

PRE-REGISTERED, BEFORE ANY OUTCOME IS KNOWN
-------------------------------------------
HYPOTHESIS (H1): names refuted by the killers UNDERPERFORM their benchmark over
63 trading days. That is what "the killers are right" predicts.

NULL (H0): refuted names perform in line with, or better than, their benchmark.
If H0 holds, the killers are costing the firm money and the bar must move.

MEASUREMENT: total return per name from the recorded entry price to the mark at
21 / 63 / 126 trading days, against the named benchmark for that name's market.
Equal weighted. ONE observation per name — no pooling of name-days.

DECISION RULE, DECLARED NOW SO IT CANNOT BE FITTED AFTERWARDS:
  * refuted basket underperforms by >5pp at 63d  -> killers VINDICATED, keep the bar
  * within +/-5pp                                -> INCONCLUSIVE, keep measuring
  * refuted basket OUTPERFORMS by >5pp at 63d    -> killers TOO AGGRESSIVE; the
    desk is refusing tradeable situations and the refutation standard must be
    re-examined, starting with the ones killed on OUR machinery rather than on
    the market's economics.

NOTHING IS TRADED. No capital is deployed. This is measurement only.
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
LEDGER = os.path.join(BASE, "reports", "killer_forward_test.json")

HORIZONS = (21, 63, 126)
DECISION_BAND_PP = 5.0


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load():
    if not os.path.exists(LEDGER):
        return None
    with open(LEDGER) as f:
        return json.load(f)


def save(d):
    d["updated_utc"] = _utc()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, LEDGER)


def create():
    if os.path.exists(LEDGER):
        raise SystemExit("REFUSED: forward test already registered. It is "
                         "append-only — editing it would invalidate the "
                         "pre-registration.")
    d = {
        "test": "killer_calibration",
        "registered_utc": _utc(),
        "paper_only": True,
        "capital_deployed": 0.0,
        "hypothesis": "H1: killer-refuted names UNDERPERFORM their benchmark over 63 trading days.",
        "null": "H0: refuted names perform in line with or better than benchmark.",
        "decision_rule": {
            "underperform_gt_5pp_at_63d": "killers VINDICATED — keep the bar",
            "within_5pp": "INCONCLUSIVE — keep measuring",
            "outperform_gt_5pp_at_63d": "killers TOO AGGRESSIVE — re-examine the standard",
        },
        "horizons_trading_days": list(HORIZONS),
        "weighting": "equal, one observation per name, no pooling of name-days",
        "names": {},
    }
    save(d)
    return d


def _price(tick):
    try:
        import fundamental_book as fb
        px, _ = fb.fetch_prices([tick])
        v = px.get(tick)
        if isinstance(v, dict):
            u = v.get("px_usd")
            if isinstance(u, (int, float)) and u > 0:
                return float(u), v.get("ccy")
    except Exception as e:                                  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:60])
    return None, "no live quote"


def register(symbol, benchmark, killed_on, thesis, verbose=True):
    """Record a refuted name at TODAY's price. Refuses without a live mark."""
    d = load()
    if d is None:
        raise SystemExit("REFUSED: run --create first.")
    if symbol in d["names"]:
        raise SystemExit("REFUSED: %s already registered — append-only." % symbol)
    px, ccy = _price(symbol)
    if px is None:
        raise SystemExit("REFUSED: %s has no live price (%s). A forward test "
                         "cannot start from a price we cannot source." % (symbol, ccy))
    bpx, _b = _price(benchmark)
    if bpx is None:
        raise SystemExit("REFUSED: benchmark %s has no live price." % benchmark)
    d["names"][symbol] = {
        "registered_utc": _utc(), "entry_px_usd": px, "benchmark": benchmark,
        "benchmark_entry_px_usd": bpx, "killed_on": killed_on,
        "thesis": thesis[:400], "marks": [],
    }
    save(d)
    if verbose:
        print("  registered %-10s @ $%-10.4f  vs %-6s @ $%.4f   killed on: %s"
              % (symbol, px, benchmark, bpx, killed_on))
    return d["names"][symbol]


def mark(verbose=True):
    """Mark every registered name and its benchmark. Never invents a price."""
    d = load()
    if d is None:
        return None
    for sym, n in d["names"].items():
        px, _ = _price(sym)
        bpx, _b = _price(n["benchmark"])
        if px is None or bpx is None:
            if verbose:
                print("  %-10s UNMARKED — no live price, carrying last" % sym)
            continue
        r = 100.0 * (px / n["entry_px_usd"] - 1.0)
        br = 100.0 * (bpx / n["benchmark_entry_px_usd"] - 1.0)
        n["marks"].append({"utc": _utc(), "px_usd": px, "bench_px_usd": bpx,
                           "ret_pct": round(r, 3), "bench_ret_pct": round(br, 3),
                           "excess_pp": round(r - br, 3)})
        if verbose:
            print("  %-10s %+7.2f%%  vs bench %+7.2f%%  ->  excess %+7.2f pp"
                  % (sym, r, br, r - br))
    save(d)
    return d


def status():
    d = load()
    if d is None:
        print("forward test not registered")
        return
    print("=" * 70)
    print("KILLER CALIBRATION FORWARD TEST   registered %s" % d["registered_utc"])
    print("=" * 70)
    print("  %s" % d["hypothesis"])
    print("  capital deployed: $%.2f — measurement only" % d["capital_deployed"])
    print()
    ex = []
    for sym, n in sorted(d["names"].items()):
        last = n["marks"][-1] if n["marks"] else None
        if last:
            ex.append(last["excess_pp"])
            print("  %-10s excess %+7.2f pp   (killed on: %s)"
                  % (sym, last["excess_pp"], str(n["killed_on"])[:44]))
        else:
            print("  %-10s no mark yet          (killed on: %s)"
                  % (sym, str(n["killed_on"])[:44]))
    if ex:
        avg = sum(ex) / len(ex)
        print()
        print("  BASKET MEAN EXCESS: %+.2f pp across %d names" % (avg, len(ex)))
        if avg < -DECISION_BAND_PP:
            print("  READ: killers VINDICATED so far — refuted names are underperforming.")
        elif avg > DECISION_BAND_PP:
            print("  READ: killers may be TOO AGGRESSIVE — refuted names are OUTPERFORMING.")
            print("        The desk may be talking itself out of tradeable situations.")
        else:
            print("  READ: INCONCLUSIVE — inside the +/-%.0fpp band. Keep measuring." % DECISION_BAND_PP)
        print("  (decision horizon is 63 trading days; this is an interim read)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.create:
        create(); print("forward test registered. PAPER ONLY, $0 deployed.")
    elif a.mark:
        mark()
    else:
        status()


if __name__ == "__main__":
    main()
