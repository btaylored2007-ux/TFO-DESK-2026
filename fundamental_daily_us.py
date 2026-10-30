#!/usr/bin/env python3
"""fundamental_daily_us.py — the DAILY US screening pass. PAPER ONLY.

Screens every SEC filer that reports the concepts it needs — roughly 4,500-5,700
companies, from penny stocks to the S&P 500 — in about EIGHT HTTP calls.

WHY IT IS EIGHT CALLS AND NOT FIVE THOUSAND
-------------------------------------------
EDGAR's `frames` API returns ONE concept for EVERY filer in a period in a single
request. Measured 2026-08-28:

    CashAndCashEquivalentsAtCarryingValue  CY2025Q4I  ->  4,907 filers
    NetCashProvidedByUsedInOperatingActivities CY2025 ->  5,718 filers
    EntityCommonStockSharesOutstanding (dei)  CY2026Q1I -> 4,610 filers

This matters because per-ticker crawling does not survive contact with a daily
job. A prior desk probed 161 tickers one at a time, took ~23 minutes, and was
rate-limited into returning blanks for 43 consecutive names — blanks that read
exactly like "these companies file no data". Frames removes the crawl entirely.

WHAT IT IS AND IS NOT
---------------------
It is a FUNNEL, not a verdict. It computes the two mechanical gates that killed
most candidates in the 2026-08-28 hunts — **cash runway** and **dilution** — and
returns a shortlist for a research desk to work properly under §4 of
GLOBAL_RESEARCH_PROMPT.md. It assigns no verdict, states no thesis, and finds no
catalyst; a catalyst requires reading filings and this reads XBRL numbers.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
* **No verdict.** §1's default is `value_trap` and only a desk may move off it.
* **No catalyst.** Gate 4 needs a dated, sourced document. Three distinct ways a
  sourced catalyst turns out false (fabricated / stale / later killed) are all
  recorded in the skill, and none is detectable from XBRL.
* **No price or spread.** Those are session-dependent and measured at the gate.

MULTIPLE TAGS, MERGED
---------------------
Filers tag the same economics differently. Revenue alone splits across
`Revenues` (2,218) and `RevenueFromContractWithCustomerExcludingAssessedTax`
(2,685). Reading one tag and calling the rest "no data" is the false-null shape
this operation keeps finding, so every concept here merges its known tags and
records which tag supplied each value.

PAPER ONLY. No broker, no order, no credential.
"""
import argparse
import json
import os
import time

import fundamental_feed as ff

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "reports", "feed_cache")
OUT = os.path.join(BASE, "reports", "daily_us_screen.json")
EODHD_ENRICH_N = int(os.environ.get("EODHD_ENRICH_N", "25"))

# Concept -> [(taxonomy, tag, unit)], tried in order and MERGED.
CONCEPTS = {
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
             ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD")],
    # ORDER MATTERS AND WAS WRONG ON THE FIRST THREE RUNS.
    #
    # `dei:EntityCommonStockSharesOutstanding` is the COVER-PAGE count, stated as
    # of the FILING date — which can be months after the period the frame is
    # labelled with. `us-gaap:CommonStockSharesOutstanding` is the BALANCE-SHEET
    # count at period end, which is what the frame label actually means.
    #
    # Proven on RYN 2026-08-28. Its own filed series:
    #     2025-12-31  161,425,616  (10-K)
    #     2026-03-31  301,675,323  (10-Q)
    # The count really did double — but in Q1 2026. The dei tag pulled that later
    # figure into the CY2025Q4I frame and produced "+93.7%/yr" against a period
    # in which almost nothing happened. Measuring a change requires BOTH ENDS to
    # mean the same thing, so the balance-sheet tag leads and dei only fills gaps.
    "shares": [("us-gaap", "CommonStockSharesOutstanding", "shares"),
               ("dei", "EntityCommonStockSharesOutstanding", "shares")],
    "ocf": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "USD")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD")],
    "revenue": [("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
                ("us-gaap", "Revenues", "USD")],
    "assets": [("us-gaap", "Assets", "USD")],
}

# Gates. Deliberately the SLEEVE's thresholds — this pass exists to feed the
# speculative sleeve, whose hard gates are runway and dilution.
MIN_RUNWAY_MONTHS = 12.0
MAX_ANNUAL_DILUTION = 0.25

# The runway gate exists for PRE-REVENUE companies — explorers and clinical-stage
# biotechs, where cash / burn genuinely is a countdown to a dilutive raise.
# Applied to an operating business it produces false distress:
#   * CAPITAL SOUTHWEST is a BDC — loan originations are classified as OPERATING
#     outflows, so it "burns" by construction. Read runway 2.6 months.
#   * AVNET, a ~$20bn distributor, shows negative operating cash flow in a year of
#     inventory build. Read runway 9.7 months.
#   * BRIDGFORD FOODS and DUCOMMUN, both real operating companies, likewise.
# None of those is a solvency signal; all four were false rejects on the first run.
# Above this revenue line the runway gate is NOT APPLIED and the name is marked
# as an operating company for a desk to judge on other grounds.
REVENUE_EXEMPTS_RUNWAY_USD = 10_000_000.0


def _frame(taxonomy, tag, unit, period, use_cache=True):
    """One concept, every filer, one call. Cached daily."""
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, "frame_%s_%s_%s_%s.json" % (taxonomy, tag, unit, period))
    if use_cache and os.path.exists(fn) and (time.time() - os.path.getmtime(fn)) < 86400:
        try:
            with open(fn) as f:
                return json.load(f), None
        except Exception:                              # noqa: BLE001
            pass
    url = ("https://data.sec.gov/api/xbrl/frames/%s/%s/%s/%s.json"
           % (taxonomy, tag, unit, period))
    body, err = _rate_limited_get(url)
    if err:
        return None, err
    try:
        rows = json.loads(body).get("data", [])
    except Exception as e:                             # noqa: BLE001
        return None, "unparseable: %s" % e
    if not rows:
        # Empty is a FAILURE, never "no filer reports this".
        return None, "frame returned ZERO rows — treated as a failure, not as absence"
    try:
        with open(fn, "w") as f:
            json.dump(rows, f)
    except OSError:
        pass
    return rows, None


def _rate_limited_get(url):
    body, err = ff._get(url)
    time.sleep(ff.SEC_RATE_SLEEP)
    return body, err


def gather(instant_period, duration_period, prior_shares_period, verbose=True):
    """Build {cik: {...}} for the whole market. Reports every frame that failed."""
    data, sources, failures = {}, {}, []

    def load(concept, period):
        merged, used = {}, []
        for tx, tag, unit in CONCEPTS[concept]:
            rows, err = _frame(tx, tag, unit, period)
            if err:
                failures.append("%s/%s %s: %s" % (tx, tag, period, err))
                continue
            used.append("%s:%s(%s)" % (tx, tag, len(rows)))
            for r in rows:
                # First tag wins; later tags only FILL GAPS. Merging without this
                # would let a secondary tag silently overwrite a primary value.
                merged.setdefault(r["cik"], {"val": r["val"], "tag": tag,
                                             "entity": r.get("entityName")})
        sources[concept] = used
        return merged

    cash = load("cash", instant_period)
    shares = load("shares", instant_period)
    ocf = load("ocf", duration_period)
    capex = load("capex", duration_period)
    rev = load("revenue", duration_period)

    prior = {}
    for tx, tag, unit in CONCEPTS["shares"]:
        rows, err = _frame(tx, tag, unit, prior_shares_period)
        if err:
            failures.append("%s/%s %s: %s" % (tx, tag, prior_shares_period, err))
            continue
        for r in rows:
            prior.setdefault(r["cik"], {"val": r["val"], "tag": tag})

    for cik, c in cash.items():
        data[cik] = {
            "cik": cik, "entity": c.get("entity"),
            "cash_usd": c["val"], "cash_tag": c["tag"],
            "shares": (shares.get(cik) or {}).get("val"),
            "shares_tag": (shares.get(cik) or {}).get("tag"),
            "shares_prior": (prior.get(cik) or {}).get("val"),
            "shares_prior_tag": (prior.get(cik) or {}).get("tag"),
            "ocf_usd": (ocf.get(cik) or {}).get("val"),
            "capex_usd": (capex.get(cik) or {}).get("val"),
            "revenue_usd": (rev.get(cik) or {}).get("val"),
        }
    if verbose:
        print("frames loaded:")
        for k, v in sources.items():
            print("   %-9s %s" % (k, ", ".join(v) or "NONE"))
        print("   universe: %s filers with a cash figure" % "{:,}".format(len(data)))
        if failures:
            print("   FRAME FAILURES (%d) — these are gaps in coverage, NOT "
                  "evidence of absence:" % len(failures))
            for f in failures[:8]:
                print("      -", f)
    return data, sources, failures


def compute(rec, months_in_duration=12.0):
    """Runway and dilution. Returns reasons a name is NOT screenable."""
    out, why = {}, []
    cash, ocf, capex = rec.get("cash_usd"), rec.get("ocf_usd"), rec.get("capex_usd")

    if cash is None:
        why.append("no cash figure")
    if ocf is None:
        why.append("no operating cash flow figure")
    elif ocf > 0:
        # CASH-GENERATIVE: operations fund the business. Runway is NOT the right
        # question and must not be computed.
        #
        # DEFECT FOUND AND FIXED 2026-08-28, first run: the burn formula was
        # `-OCF + capex` for everyone, so a profitable company with heavy GROWTH
        # capex read as a burner. **Air Products was rejected at "runway 3.3
        # months"** — a ~$60bn industrial gas producer — along with Avnet,
        # Bridgford and a BDC. Capex funded BY operations is capital allocation,
        # not a solvency countdown, and conflating them rejects exactly the
        # healthy companies a screen exists to keep.
        out["runway_months"] = None
        out["cash_generative"] = True
        out["burn_annual_usd"] = 0.0
        out["capex_included"] = capex is not None
        out["fcf_annual_usd"] = ocf - (capex or 0.0)   # kept for information only
    else:
        # BURNING: operations consume cash. Now the capex-check matters.
        # Found 2026-08-28: PANR.L showed a 69-month runway on operating cash
        # flow and 6.6 months once development capex (booked to investing) was
        # included. For a company already burning, capex is additional burn.
        burn_annual = -(ocf) + (capex or 0.0)
        out["burn_annual_usd"] = burn_annual
        out["capex_included"] = capex is not None
        out["cash_generative"] = False
        if burn_annual <= 0:
            out["runway_months"] = None
        else:
            monthly = burn_annual / months_in_duration
            out["runway_months"] = round((cash or 0) / monthly, 1) if monthly else None

    s, sp = rec.get("shares"), rec.get("shares_prior")
    st, spt = rec.get("shares_tag"), rec.get("shares_prior_tag")
    if s and sp and sp > 0 and st and spt and st != spt:
        # DEFECT FOUND AND FIXED 2026-08-28, third run. The two share-count tags
        # measure different things — `dei:EntityCommonStockSharesOutstanding` is
        # the COVER-PAGE count at the filing date; `us-gaap:CommonStockSharesOut-
        # standing` is the BALANCE-SHEET count at period end. Comparing one
        # against the other across two dates invents dilution that never
        # happened: **HNI read +51.6%/yr and RAYONIER +93.7%/yr**, both real
        # operating companies.
        #
        # This is the INTRUM shape — a pre-rights numerator over a post-rights
        # denominator, "two different companies in one ratio". A share-count
        # change is only meaningful when BOTH ENDS ARE THE SAME MEASUREMENT.
        why.append("share count tags differ across the two dates (%s vs %s) — "
                   "dilution NOT computable from mismatched measurements"
                   % (spt, st))
    elif s and sp and sp > 0:
        ratio = s / sp
        out["share_growth"] = round(ratio - 1.0, 4)
        # A REVERSE SPLIT shows as a large NEGATIVE — four names in one prior
        # batch (VRCA -74.5%, ACET -82.4%, PXLW -84.7%, STRO -79.9%). It is a
        # listing-compliance rescue, not the company retiring stock, so it is
        # FLAGGED rather than scored as good news.
        if ratio < 0.75:
            out["possible_reverse_split"] = True
    else:
        why.append("share count unavailable at one or both dates")
    return out, why


def screen(instant="CY2025Q4I", duration="CY2025", prior_shares="CY2024Q4I",
           verbose=True):
    data, sources, failures = gather(instant, duration, prior_shares, verbose)
    if not data:
        raise SystemExit("REFUSED: no universe was built. Every frame failed — "
                         "this is a SOURCE outage, and recording a null result "
                         "now would be a false finding.")
    passed, rejected, unscreenable = [], [], []
    for cik, rec in data.items():
        m, why = compute(rec)
        rec.update(m)
        if why:
            rec["unscreenable"] = why
            unscreenable.append(rec)
            continue
        reasons = []
        rw = rec.get("runway_months")
        revenue = rec.get("revenue_usd") or 0.0
        operating_co = revenue >= REVENUE_EXEMPTS_RUNWAY_USD
        rec["operating_company"] = operating_co
        if operating_co:
            rec["runway_gate"] = ("NOT APPLIED — revenue $%s. Cash/burn is a "
                                  "countdown only for a pre-revenue company; on "
                                  "an operating business negative OCF is working "
                                  "capital or, for a BDC, loan origination."
                                  % "{:,.0f}".format(revenue))
        if not operating_co and not rec.get("cash_generative"):
            if rw is None:
                reasons.append("runway not computable")
            elif rw < MIN_RUNWAY_MONTHS:
                reasons.append("runway %.1f months below the %.0f-month floor"
                               % (rw, MIN_RUNWAY_MONTHS))
        g = rec.get("share_growth")
        if g is not None and g > MAX_ANNUAL_DILUTION:
            if operating_co:
                # The gate CANNOT distinguish stock issued for an ACQUISITION
                # from a distress placement — both grow the count. On a company
                # with real revenue the dominant cause is M&A: INTERNATIONAL
                # PAPER (+39.7%, DS Smith) and HNI (+51.1%) are both acquirers,
                # not distressed issuers. Dilution is still dilution to a holder,
                # so it is not waived — but calling it distress would be wrong,
                # and a desk must establish which it is.
                reasons.append(
                    "share count +%.1f%%/yr above the %.0f%% ceiling — but this "
                    "is an OPERATING company ($%s revenue), where the dominant "
                    "cause is an ACQUISITION rather than a distress placing. "
                    "Dilution is real either way; CAUSE NOT DETERMINED by this "
                    "screen and a desk must establish it."
                    % (g * 100, MAX_ANNUAL_DILUTION * 100,
                       "{:,.0f}".format(revenue)))
            else:
                reasons.append("share count +%.1f%%/yr above the %.0f%% ceiling"
                               % (g * 100, MAX_ANNUAL_DILUTION * 100))
        if reasons:
            rec["reject_reasons"] = reasons
            rejected.append(rec)
        else:
            passed.append(rec)

    tmap = ff.edgar_ticker_map()
    by_cik = {}
    if tmap.ok:
        for t, v in tmap.data.items():
            by_cik.setdefault(v["cik"], t)
    for r in passed + rejected + unscreenable:
        r["ticker"] = by_cik.get(r["cik"])

    out = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paper_only": True,
        "source": "SEC EDGAR XBRL frames — free, point-in-time capable",
        "periods": {"instant": instant, "duration": duration,
                    "prior_shares": prior_shares},
        "frames_used": sources,
        "frame_failures": failures,
        "gates_applied": {"min_runway_months": MIN_RUNWAY_MONTHS,
                          "max_annual_dilution": MAX_ANNUAL_DILUTION,
                          "note": ("MECHANICAL ONLY. No verdict, no catalyst, no "
                                   "price. A pass here is a name for a desk to "
                                   "research under §4, NOT a candidate.")},
        "counts": {"universe": len(data), "passed": len(passed),
                   "rejected": len(rejected), "unscreenable": len(unscreenable),
                   # Written so the truncation is VISIBLE: if n_cash_generative
                   # exceeds the 400 cap, names are being dropped from the file.
                   "n_cash_generative": sum(1 for r in passed if r.get("cash_generative")),
                   "passed_written_to_file": min(len(passed), 400)},
        # ORDERING DEFECT FOUND 2026-09-03 — the fix above created this one.
        #
        # A CASH-GENERATIVE company is given `runway_months = None` (correct: it
        # has no countdown). This writer then sorted by `-(runway_months or 0)`,
        # so None became 0 and every profitable company sorted DEAD LAST, while
        # a company burning a rounding error sorted FIRST — KINGSWAY Corp, OCF
        # -$3,000 against $135m of revenue, scored a 33,224-month runway.
        #
        # With the list truncated to 400 the effect was total: of 2,940 names
        # that passed the gates, the 400 actually WRITTEN TO DISK contained ZERO
        # cash-generative companies. The funnel feeding the speculative sleeve
        # was emitting the 400 largest arithmetic artifacts and silently
        # discarding every profitable business it had just screened.
        #
        # Cash-generative names now lead, then burners by descending runway.
        "passed": sorted(
            passed,
            key=lambda r: (0 if r.get("cash_generative") else 1,
                           -(r.get("runway_months") or 0)))[:400],
        "rejected_sample": rejected[:200],
        "unscreenable_sample": unscreenable[:100],
    }
    # 2026-10-30 eodhd: enrich the head of the shortlist with EODHD fundamentals (key confirmed 20:53 WITA).
    # Additive only: gates/ordering above are unchanged; a failure is recorded, never a blank.
    enrich = {"source": "eodhd fundamentals", "ok": 0, "failed": 0, "rows": {}, "errors": []}
    for r in out["passed"][:EODHD_ENRICH_N]:
        t = r.get("ticker")
        if not t: continue
        res = ff.eodhd_fundamentals("%s.US" % t)
        if res.ok and isinstance(res.data, dict):
            h, g = res.data.get("Highlights") or {}, res.data.get("General") or {}
            enrich["rows"][t] = {"sector": g.get("Sector"), "market_cap": h.get("MarketCapitalization"),
                                 "pe": h.get("PERatio"), "eps_ttm": h.get("DilutedEpsTTM"),
                                 "mrq": h.get("MostRecentQuarter"), "rev_ttm": h.get("RevenueTTM")}
            enrich["ok"] += 1
        else:
            enrich["failed"] += 1; enrich["errors"].append("%s: %s" % (t, str(res.reason)[:120]))
    out["eodhd_enrichment"] = enrich
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    if verbose:
        c = out["counts"]
        print("\nSCREEN: %s universe | %s passed | %s rejected | %s unscreenable"
              % tuple("{:,}".format(c[k]) for k in
                      ("universe", "passed", "rejected", "unscreenable")))
        print("written -> %s" % OUT)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instant", default="CY2025Q4I")
    ap.add_argument("--duration", default="CY2025")
    ap.add_argument("--prior-shares", default="CY2024Q4I")
    a = ap.parse_args()
    screen(a.instant, a.duration, a.prior_shares)
