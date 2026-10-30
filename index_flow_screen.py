#!/usr/bin/env python3
"""index_flow_screen.py — forced index flow as a MULTIPLE OF ADV. PAPER ONLY.

WHY THIS EXISTS
---------------
A 34-situation dislocation sweep found three genuinely real, sourced, dated
forced sellers (TAP, SHEN, Kanadevia) and funded none of them. Its own closing
verdict named the reason:

  "Nobody measured whether the forced flow is large enough to set the price.
   Every one of these theses ASSERTED it. That single unmeasured quantity killed
   SHEN outright and left TAP unfundable... Next sweep should be ONE INSTRUMENT,
   NOT SIX NAMES: forced flow as a multiple of ADV, computed from index-provider
   float/weight and disclosed tracker AUM, applied to every index-deletion
   candidate BEFORE any narrative is written."

Finding a forced seller turns out to be easy. Knowing whether it matters is the
hard part, and it is arithmetic, not judgment. Run this first; write the thesis
only if the number survives.

WHAT IT REFUSES TO DO
---------------------
It does not return a point estimate. Index-provider AUM and aggregate index market
cap are not precisely knowable from free sources, so a single number would be
false precision dressed as measurement. It returns the RANGE across a grid of
defensible assumptions and reports the worst case, because the worst case is what
decides whether to trade. Where a conclusion flips inside the grid, it says so.

CAP-WEIGHTED vs PRICE-WEIGHTED — THIS DISTINCTION IS LOAD-BEARING
------------------------------------------------------------------
S&P 500/400/600, Russell and MSCI are FLOAT-CAP weighted: weight = float cap /
index float cap. The Nikkei 225 is PRICE weighted: weight = adjusted price /
(index level x divisor). Applying the cap-weighted formula to a Nikkei name gives
a badly wrong answer — a low-priced large company looks heavy when it is light.
This tool refuses to guess a price-weighted weight without a sourced divisor.
"""
import argparse
import json
import urllib.request

UA = {"User-Agent": __import__("json").load(open(__import__("os").path.expanduser("~/Applications/MultiVenueDesk/research_controls/sec_contact.json")))["user_agent"]}

# Defensible ranges, stated as ranges because they are not precisely knowable.
# index -> (aggregate index market cap USD grid, indexed AUM USD grid, weighting)
INDEX_PARAMS = {
    "SP500":   ((55e12, 62e12, 70e12), (8.0e12, 10.6e12, 13.0e12), "cap"),
    "SP400":   ((3.0e12, 3.5e12, 4.0e12), (0.20e12, 0.30e12, 0.40e12), "cap"),
    "SP600":   ((1.2e12, 1.5e12, 1.8e12), (0.08e12, 0.12e12, 0.16e12), "cap"),
    "RUSSELL2000": ((2.5e12, 3.0e12, 3.5e12), (0.35e12, 0.50e12, 0.70e12), "cap"),
    "NIKKEI225": (None, (0.15e12, 0.25e12, 0.35e12), "price"),
}


def _get(url, timeout=30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "ignore")


def shares_outstanding(cik):
    """Most recent sourced share count from SEC XBRL. None if not sourceable."""
    cik = str(cik).lstrip("CIK").zfill(10)
    try:
        j = json.loads(_get("https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % cik, 40))
    except Exception as e:                                  # noqa: BLE001
        return None, "SEC companyfacts unreachable: %s" % str(e)[:70]
    best = None
    for concept in ("WeightedAverageNumberOfSharesOutstandingBasic",
                    "WeightedAverageNumberOfDilutedSharesOutstanding",
                    "CommonStockSharesOutstanding", "CommonStockSharesIssued"):
        u = (j.get("facts", {}).get("us-gaap", {}).get(concept, {})
             .get("units", {}).get("shares", []))
        rows = [x for x in u if x.get("val")]
        if not rows:
            continue
        rows.sort(key=lambda x: x["end"])
        cand = rows[-1]
        if best is None or cand["end"] > best[0]["end"]:
            best = (cand, concept)
    if best is None:
        return None, "no sourceable share count in XBRL"
    return best[0]["val"], "SEC XBRL %s, period end %s (%s)" % (
        best[1], best[0]["end"], best[0].get("form"))


def screen(symbol, index, price, adv_shares, shares_out, shares_src,
           divisor=None, index_level=None, fx_usd=1.0, verbose=True):
    """Return the forced-flow/ADV grid. Never a point estimate."""
    if index not in INDEX_PARAMS:
        raise SystemExit("REFUSED: unknown index %r. Known: %s"
                         % (index, ", ".join(sorted(INDEX_PARAMS))))
    caps, aums, weighting = INDEX_PARAMS[index]
    if not (price and price > 0):
        raise SystemExit("REFUSED: no live price for %s. A flow screen on a price "
                         "we cannot source is a guess." % symbol)
    if not (adv_shares and adv_shares > 0):
        raise SystemExit("REFUSED: no ADV for %s. Without it there is no denominator "
                         "and the whole question is unanswerable." % symbol)
    # Share count is required ONLY for cap weighting. A price-weighted weight is
    # adjusted_price / (index_level x divisor) and never touches it — verified:
    # 1M, 170M and 999M shares all return 18.89-44.09 days for 7004. Demanding an
    # input the arithmetic ignores is an invitation to supply a made-up one, which
    # is exactly what happened: a review flagged an unsourced 170,000,000 as "the
    # weakest number in the table" when it was load-bearing on nothing.
    if weighting == "cap" and not (shares_out and shares_out > 0):
        raise SystemExit("REFUSED: no sourced share count for %s (%s). Cap weighting "
                         "needs it." % (symbol, shares_src))
    if weighting == "price" and shares_out:
        shares_src = (shares_src or "") + "  [NOT USED — price-weighted]"

    # CURRENCY. Indexed AUM is in USD, so the ADV denominator MUST be in USD too.
    # Caught live on 7004.T: price ¥1,155 gave an "ADV" of 738,507,000 that was
    # actually YEN, divided into a USD forced sale — a 156x mismatch that printed
    # "FLOW IS IMMATERIAL, 0.12-0.28 days" when the true answer is 18.9-44.1 days
    # and MATERIAL. The verdict was inverted, stated confidently, with a clean
    # audit trail. Same defect class as the GBp/ZAc minor-unit bugs on this desk.
    if not (fx_usd and fx_usd > 0):
        raise SystemExit("REFUSED: fx_usd must be positive; got %r. A non-USD name "
                         "screened without an FX rate silently compares USD to "
                         "local currency." % fx_usd)
    adv_usd = adv_shares * price * fx_usd
    ratios = []
    rows = []
    if weighting == "price":
        if not (divisor and index_level):
            raise SystemExit(
                "REFUSED: %s is a PRICE-WEIGHTED index. Its weight is "
                "adjusted_price / (index_level x divisor), NOT market cap share. "
                "Pass --divisor and --index-level from a sourced Nikkei factsheet, "
                "or this returns nothing. Guessing here would silently produce a "
                "badly wrong answer — a cheap large company looks heavy when it is "
                "light." % index)
        w = price / (index_level * divisor)
        for aum in aums:
            f = w * aum
            ratios.append(f / adv_usd)
            rows.append((None, w, aum, f, f / adv_usd))
    else:
        mc = shares_out * price * fx_usd
        for cap in caps:
            w = mc / cap
            for aum in aums:
                f = w * aum
                ratios.append(f / adv_usd)
                rows.append((cap, w, aum, f, f / adv_usd))

    lo, hi = min(ratios), max(ratios)
    out = {
        "symbol": symbol, "index": index, "weighting": weighting,
        "price": price, "fx_usd": fx_usd,
        "adv_shares": adv_shares, "adv_usd": adv_usd,
        "shares_out": shares_out, "shares_source": shares_src,
        "days_of_volume_low": round(lo, 2), "days_of_volume_high": round(hi, 2),
        "grid": [{"index_cap": c, "weight_pct": round(100 * w, 5),
                  "indexed_aum": a, "forced_usd": round(f, 0),
                  "days": round(d, 2)} for c, w, a, f, d in rows],
    }
    # the verdict is set by the WORST case, because that is what decides a trade
    if lo >= 3.0:
        out["verdict"] = "FLOW IS MATERIAL"
        out["read"] = ("Worst case %.2f days of volume. The forced seller plausibly "
                       "sets the price. The flow leg stands on its own." % lo)
    elif hi < 1.0:
        out["verdict"] = "FLOW IS IMMATERIAL"
        out["read"] = ("Best case only %.2f days of volume. The forced seller does NOT "
                       "set the price. There is no flow trade here regardless of the "
                       "narrative." % hi)
    else:
        out["verdict"] = "INCONCLUSIVE — CONCLUSION FLIPS INSIDE THE GRID"
        out["read"] = ("Range %.2f to %.2f days straddles the threshold. The answer "
                       "depends on assumptions that are not precisely knowable, so "
                       "this must NOT be reported as measured." % (lo, hi))
    if verbose:
        print("=" * 68)
        print("FORCED-FLOW SCREEN  %s  vs  %s  (%s-weighted)" % (symbol, index, weighting))
        print("=" * 68)
        print("  shares out   %s   %s"
              % (format(shares_out, ",") if shares_out else "n/a", shares_src))
        print("  price        %.4f local  x fx %.6f  = $%.4f USD" % (price, fx_usd, price * fx_usd))
        print("  ADV          %s sh = $%s/day" % (format(adv_shares, ","), format(adv_usd, ",.0f")))
        print()
        print("  %-12s %-11s %-12s %-16s %s" % ("index cap", "weight", "indexed AUM", "forced sale", "days"))
        for c, w, a, f, d in rows:
            print("  %-12s %-11s %-12s $%-15s %.2f" % (
                ("$%.0fT" % (c / 1e12)) if c else "n/a", "%.5f%%" % (100 * w),
                "$%.2fT" % (a / 1e12), format(f, ",.0f"), d))
        print()
        print("  RANGE: %.2f - %.2f days of volume" % (lo, hi))
        print("  VERDICT: %s" % out["verdict"])
        print("  %s" % out["read"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--index", required=True, help=", ".join(sorted(INDEX_PARAMS)))
    ap.add_argument("--cik", help="SEC CIK for a sourced share count")
    ap.add_argument("--shares", type=float,
                    help="share count if not on SEC. NOT needed for price-weighted "
                         "indices — the weight never uses it.")
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--adv", type=float, required=True, help="average daily volume in SHARES")
    ap.add_argument("--divisor", type=float, help="price-weighted indices only")
    ap.add_argument("--index-level", type=float, help="price-weighted indices only")
    ap.add_argument("--fx", type=float, default=1.0,
                    help="local currency -> USD. REQUIRED for non-USD names: indexed "
                         "AUM is USD, so the ADV denominator must be USD too.")
    a = ap.parse_args()
    if a.cik:
        sh, src = shares_outstanding(a.cik)
    else:
        sh, src = a.shares, "supplied on the command line — NOT independently sourced"
    screen(a.symbol, a.index, a.price, a.adv, sh, src,
           divisor=a.divisor, index_level=a.index_level, fx_usd=a.fx)


if __name__ == "__main__":
    main()
