#!/usr/bin/env python3
"""distribution_screen.py — how much of the price comes back as CONTRACTED CASH.

WHY THIS EXISTS
---------------
A 35-family sweep opened liquidations, wind-ups, tenders and return-of-capital as
newly tradable, then discovered its own brief was self-contradictory:

  "A liquidating REIT has no index-deletion forced flow. The newly-opened hunting
   ground is precisely the ground where the desk's one validated measuring
   instrument returns NOT_APPLICABLE... The next sweep needs to either hunt index
   changes, or BUILD THE EQUIVALENT ONE-NUMBER INSTRUMENT FOR DISTRIBUTIONS — my
   candidate: distribution as a fraction of market cap, versus the discount to
   stated NAV. Right now the desk has no way to tell a real wind-up from a
   narrative one, which is exactly the gap days-of-volume closed for index
   deletions."

That is what this is. `index_flow_screen.py` answers "can the seller move the
price?" for an index event. This answers "how much of what I pay is contracted to
come back, and when?" for a distribution event. Both replace a story with a number
that has to be computed before a thesis is written.

THE NUMBER
----------
  cash_coverage = near-term contracted distribution / price

That is the fraction of the purchase price returning as CASH on a dated schedule.
Everything above it is the residual — the part still riding on the thesis. A wind-up
where 56% of the price is contracted cash inside 30 days is a different animal from
one promising 100% of NAV "in the first half of 2028", and the difference is
arithmetic, not judgment.

WHAT IT REFUSES
---------------
Every input must be SOURCED. A distribution amount or NAV taken from a pitch rather
than a filing is the CM-069 defect in a new costume — the desk has already had one
position carrying a catalyst date that traced to nothing. No URL, no screen.

It also refuses to treat an UNDATED distribution as near-term. "Within
approximately 30 days following stockholder approval" with no record date fixed is
NOT a dated cash flow, and a screen that counts it as one is lying about the floor.
That distinction is what separates a contracted floor from a hope.
"""
import argparse

# A distribution is only "near-term" if a document fixes its date.
UNDATED_TOKENS = ("approximately", "expected", "anticipated", "intends", "targeted",
                  "around", "circa", "in the first half", "in the second half",
                  "no later than", "tbd", "to be determined", "following approval")


def screen(symbol, price, near_term_dist, near_term_date, total_expected_dist,
           stated_nav, source_url, date_is_fixed, verbose=True):
    """Return the cash-coverage read. Refuses unsourced or undated inputs."""
    if not source_url or "http" not in str(source_url).lower():
        raise SystemExit(
            "REFUSED: %s has no source URL. A distribution amount taken from a "
            "pitch rather than a filing is CM-069 in a new costume." % symbol)
    if not (price and price > 0):
        raise SystemExit("REFUSED: no live price for %s." % symbol)
    if near_term_dist is None or near_term_dist < 0:
        raise SystemExit("REFUSED: near_term_dist must be >= 0.")
    low = str(near_term_date or "").lower()
    hedged = [t for t in UNDATED_TOKENS if t in low]
    if hedged and date_is_fixed:
        raise SystemExit(
            "REFUSED: near_term_date %r contains hedging language (%s) but was "
            "passed as date_is_fixed=True. 'Within approximately 30 days following "
            "approval' with no record date fixed is NOT a dated cash flow, and "
            "counting it as one overstates the floor." % (near_term_date, hedged[0]))
    if not date_is_fixed:
        # an undated payment cannot be part of the contracted near-term floor
        contracted = 0.0
        floor_note = ("near-term distribution is NOT date-fixed (%s) — excluded "
                      "from the floor" % (near_term_date or "no date given"))
    else:
        contracted = float(near_term_dist)
        floor_note = "near-term distribution is date-fixed: %s" % near_term_date

    cov = contracted / price
    residual = price - contracted
    out = {
        "symbol": symbol, "price": price,
        "near_term_distribution": near_term_dist,
        "near_term_date": near_term_date, "date_is_fixed": bool(date_is_fixed),
        "contracted_near_term": contracted,
        "cash_coverage_pct": round(100 * cov, 2),
        "residual_at_risk": round(residual, 4),
        "residual_pct_of_price": round(100 * residual / price, 2),
        "source_url": source_url, "floor_note": floor_note,
    }
    if stated_nav and stated_nav > 0:
        out["stated_nav"] = stated_nav
        out["discount_to_nav_pct"] = round(100 * (stated_nav - price) / stated_nav, 2)
        out["upside_if_nav_realised_pct"] = round(100 * (stated_nav / price - 1), 2)
    if total_expected_dist and total_expected_dist > 0:
        out["total_expected_distribution"] = total_expected_dist
        out["total_vs_price_pct"] = round(100 * total_expected_dist / price, 2)

    if cov >= 0.50:
        out["verdict"] = "STRONG CONTRACTED FLOOR"
        out["read"] = ("%.0f%% of the purchase price is contracted cash on a fixed "
                       "date. Only $%.2f of the $%.2f price is riding on the thesis."
                       % (100 * cov, residual, price))
    elif cov >= 0.20:
        out["verdict"] = "PARTIAL FLOOR"
        out["read"] = ("%.0f%% contracted, %.0f%% of the price still at thesis risk."
                       % (100 * cov, 100 * residual / price))
    elif contracted > 0:
        out["verdict"] = "THIN FLOOR"
        out["read"] = ("Only %.0f%% of the price is contracted cash. This is a "
                       "narrative wind-up wearing a distribution costume."
                       % (100 * cov))
    else:
        out["verdict"] = "NO CONTRACTED FLOOR"
        out["read"] = ("Nothing is date-fixed, so NONE of the price is contracted. "
                       "%s. The entire position is thesis risk with no floor — which "
                       "is exactly what the sleeve's capped-loss gate exists to "
                       "refuse." % floor_note)

    if verbose:
        print("=" * 66)
        print("DISTRIBUTION SCREEN  %s" % symbol)
        print("=" * 66)
        print("  price                      $%.4f" % price)
        print("  near-term distribution     $%.4f   (%s)" % (near_term_dist, near_term_date))
        print("  date fixed?                %s" % ("YES" if date_is_fixed else "NO — excluded from the floor"))
        print("  CONTRACTED near-term       $%.4f" % contracted)
        print()
        print("  CASH COVERAGE              %.2f%%   <- the number" % out["cash_coverage_pct"])
        print("  residual at thesis risk    $%.4f  (%.2f%% of price)"
              % (out["residual_at_risk"], out["residual_pct_of_price"]))
        if "discount_to_nav_pct" in out:
            print("  discount to stated NAV     %.2f%%  (upside if realised %.2f%%)"
                  % (out["discount_to_nav_pct"], out["upside_if_nav_realised_pct"]))
        if "total_vs_price_pct" in out:
            print("  TOTAL expected dist        %.2f%% of price (undated portion included)"
                  % out["total_vs_price_pct"])
        print()
        print("  VERDICT: %s" % out["verdict"])
        print("  %s" % out["read"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--near-dist", type=float, required=True)
    ap.add_argument("--near-date", required=True)
    ap.add_argument("--date-fixed", action="store_true",
                    help="ONLY if a document fixes a record or payment date")
    ap.add_argument("--total-dist", type=float)
    ap.add_argument("--nav", type=float)
    ap.add_argument("--source", required=True)
    a = ap.parse_args()
    screen(a.symbol, a.price, a.near_dist, a.near_date, a.total_dist, a.nav,
           a.source, a.date_fixed)


if __name__ == "__main__":
    main()
