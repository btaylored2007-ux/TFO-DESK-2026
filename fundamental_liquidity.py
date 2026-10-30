#!/usr/bin/env python3
"""fundamental_liquidity.py — the cost wall. Gates every candidate before sizing.

WHY THIS IS THE MOST IMPORTANT FILE IN THE HUNT
------------------------------------------------
The desk is being pointed at penny stocks through blue chips on any market. In
that range, stock selection is NOT what decides the outcome — cost is. A micro
cap can be genuinely, provably undervalued and still be un-ownable, because:

  * the spread is 4% and you pay half of it twice,
  * your position is three days of the stock's entire volume,
  * nobody will lend it, so the short does not exist at any price,
  * and the exit is into the same illiquidity that made the entry look cheap.

The firm has been here before and wrote it down. The retail-vehicles sweep
(2026-08-20) found a structural law: **every venue where retail is protected from
institutions is capped BY the protecting feature itself.** The same shape governs
small caps — the reason institutions leave them alone is the reason they are hard
to hold at size. This file makes that constraint explicit and refuses candidates
that fail it, BEFORE anyone forms a view on the business.

THE ORDER MATTERS
-----------------
Screen liquidity FIRST, value SECOND. A desk that researches a name for an hour
and then discovers it cannot be traded has wasted the hour, and — far worse — has
built an emotional position in a thesis it must now abandon. Kill it at the gate.

WHAT IT REFUSES, AND WHY EACH ONE
---------------------------------
  ADV       — position must be a small fraction of daily traded value, or the
              exit IS the price move. This is the single most common way a
              backtest that looked wonderful loses money live.
  SPREAD    — half the quoted spread is paid on entry and again on exit. A 4%
              spread is an 4% round-trip handicap before the thesis is tested,
              against a 5% net gate. It cannot work.
  PRICE     — sub-dollar quotes carry tick-size and minimum-increment effects
              that make the modelled spread optimistic.
  MARKETCAP — below a floor, a position of any useful size becomes a meaningful
              share of the company.
  SHORTS    — a short additionally needs BORROW TO EXIST. Below the borrow floor
              the honest answer is that the trade cannot be put on, at any rate.

Every rejection carries the measured number, so the reason is checkable rather
than asserted.

HONEST LIMITS
-------------
Volume and spread come from a public quote feed. Real fills depend on venue, time
of day, and an order book this cannot see. The thresholds are therefore
DELIBERATELY STRICT — the failure mode that matters is a gate too easy to clear,
because that is the one that puts un-exitable positions in the book.

PAPER ONLY.
"""
import math

# --- thresholds. Strict by design. Changing these is a RULE CHANGE. ---
MAX_POSITION_PCT_OF_ADV = 0.10     # position <= 10% of one day's traded value
MIN_ADV_USD = 250_000.0            # below this, no position size is safe
MAX_SPREAD_PCT = 0.010             # 1.0% quoted spread; half paid each way
MIN_PRICE_USD = 1.00               # sub-dollar: tick effects break the model
MIN_MARKET_CAP_USD = 50_000_000.0  # below this a useful position owns the company
MIN_MARKET_CAP_FOR_SHORT_USD = 300_000_000.0   # borrow realistically exists above
MIN_ADV_FOR_SHORT_USD = 2_000_000.0            # crowded exits kill small shorts
PLACEHOLDER_SPREAD_PCT = 0.20   # above this it is not a market, it is a stub quote
SPREAD_WAIVER_ADV_MULTIPLE = 8  # missing bid/ask forgiven only this far above the ADV floor

# DEFECT FOUND 2026-08-28 (forced-seller + dual-class desks, independently).
# PLACEHOLDER_SPREAD_PCT at 20% catches only grotesque stub quotes. It does NOT
# catch the far commoner out-of-session artifact: a 5-15% band on a mega cap.
# Measured after hours: FOXA 14.74% on US$442m ADV, FOX 14.88%, GEF-B 13.49%,
# BIO-B 14.34%, POOL 14.6% with BID SIZE 2 / ASK SIZE 2, and MKTX and CZR both
# returning EXACTLY 16.01%. One desk lost 21 of 29 names to this; another lost 5.
#
# The tell is arithmetic, not judgement: a security turning over tens of millions
# of dollars a day CANNOT have a percent-wide spread. Real spreads at that
# turnover are basis points. So a wide reading on a very liquid name is an
# artifact of the clock, and refusing on it deletes the entire US universe
# whenever the gate runs outside 09:30-16:00 ET.
IMPLAUSIBLE_SPREAD_ADV_USD = 50_000_000.0   # above this turnover...
IMPLAUSIBLE_SPREAD_PCT = 0.02               # ...a spread this wide cannot be real


def thresholds():
    return {
        "max_position_pct_of_adv": MAX_POSITION_PCT_OF_ADV,
        "min_adv_usd": MIN_ADV_USD,
        "max_spread_pct": MAX_SPREAD_PCT,
        "min_price_usd": MIN_PRICE_USD,
        "min_market_cap_usd": MIN_MARKET_CAP_USD,
        "min_market_cap_for_short_usd": MIN_MARKET_CAP_FOR_SHORT_USD,
        "min_adv_for_short_usd": MIN_ADV_FOR_SHORT_USD,
        "note": ("Deliberately strict. Volume and spread come from a public quote "
                 "feed and cannot see the order book; a gate that is too easy to "
                 "clear is what puts un-exitable positions in a book."),
    }


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def assess(info, notional_usd, side="long", fx_usd=1.0):
    """Gate one candidate. `info` is a yfinance-style dict.

    Returns {ok, reasons[], measured{}} — never a bare boolean, because a refusal
    is only useful if it carries the number that caused it.
    """
    px = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if px is None:  # 2026-10-29 infra (Iris): previousClose only as sanity-checked fallback vs daily bar
        try:
            from veteran_desk import feeds as _FD
            _pc = _FD.prior_close(info.get("symbol") or "", yahoo_prev=info.get("previousClose"))
            px = _num(_pc.get("close"))
        except Exception:
            px = _num(info.get("previousClose"))
    vol = (_num(info.get("averageDailyVolume3Month"))
           or _num(info.get("averageDailyVolume10Day"))
           or _num(info.get("volume")))
    bid, ask = _num(info.get("bid")), _num(info.get("ask"))
    # CONVERT THE MARKET CAP. Found 2026-08-27 by the Japan small-cap desk:
    # price was scaled by fx_usd while marketCap was compared RAW against USD
    # floors, which silently DISABLED both cap gates on every non-USD line.
    # 6470.T is $201m USD and cleared the $300m short-borrow floor as JPY 32.1bn.
    # A gate that is too easy to clear is the one failure mode this file exists
    # to prevent, so the bug is recorded here rather than quietly patched.
    mcap_native = _num(info.get("marketCap"))

    # GBp/GBP UNIT SPLIT — found 2026-08-28 by the speculative penny desk, which
    # could not patch it (read-only) and had to correct by hand downstream.
    #
    # On London lines the quote is in PENCE while `marketCap` is reported in
    # POUNDS. The caller correctly passes a GBp fx rate (~0.0136) to convert the
    # price, and that same rate then converts a POUNDS market cap — making it
    # 100x TOO SMALL. SRB.L read US$3.27m against a true US$326.8m.
    #
    # The consequence was silent and total: every London name fell under the
    # US$50m market-cap floor and was refused for a reason that had nothing to
    # do with the company. THE GATE WAS DELETING THE ENTIRE UK MARKET and
    # reporting it as a size failure. Two of that desk's five candidates would
    # have been lost, and no earlier desk would have known why.
    #
    # Detection is arithmetic and exact, not a heuristic: when marketCap is in
    # pounds and price in pence, `price * sharesOutstanding` overstates
    # `marketCap` by exactly 100, so the ratio pins to 0.01. Proven to 1.0000 on
    # SRB.L and POLB.L. A 3% tolerance keeps ordinary rounding inside it while
    # staying far from the >=1.10x / <=0.90x band that flags the OTHER two
    # causes of a share-count mismatch.
    _shares0 = _num(info.get("sharesOutstanding"))
    mcap_unit_fixed = None
    measured_unit_suspect = None
    if (mcap_native and px and _shares0 and fx_usd and fx_usd < 0.05):
        _implied = px * _shares0
        _ratio0 = mcap_native / _implied if _implied > 0 else None
        # WIDENED 2026-08-28 after the AIM desk found FTC.L (Filtronic) MISSED.
        # Its ratio was 0.0149, outside the original <0.0003 tolerance, because
        # the pence/pounds split COMPOUNDED with a ~1.49x stale share count. The
        # tolerance assumed the unit split was the ONLY defect present. It read
        # US$10.5m against a true ~US$700m and was silently passed uncorrected.
        #
        # Exact matches are still auto-corrected. Anything in the wider band is
        # SUSPECTED but NOT silently fixed — a second, unknown error is in play,
        # so the honest action is to refuse the name rather than apply a
        # correction that may itself be wrong.
        if _ratio0 is not None and 0.005 <= _ratio0 <= 0.02 and abs(_ratio0 - 0.01) >= 0.0003:
            mcap_unit_fixed = None
            measured_unit_suspect = (
                "market cap / (price x shares) = %.4f, inside the GBp/GBP band "
                "(0.005-0.02) but NOT the exact 0.01 of a clean unit split. A "
                "SECOND error compounds with it — FTC.L was a ~1.49x stale share "
                "count on top of the pence/pounds split, reading US$10.5m against "
                "~US$700m. NOT auto-corrected: applying a x100 fix here would "
                "propagate the other error. REFUSED pending manual reconciliation."
                % _ratio0)
        else:
            measured_unit_suspect = None
        if _implied > 0 and abs((mcap_native / _implied) - 0.01) < 0.0003:
            mcap_unit_fixed = (
                "MINOR-UNIT / MAJOR-UNIT SPLIT: marketCap is reported in the MAJOR "
                "unit against a MINOR-unit quote — corrected x100 (%s -> %s "
                "native). Uncorrected the cap reads 100x too small and the line "
                "fails the market-cap floor for a reason unrelated to the company. "
                "CONFIRMED ON TWO MARKETS, so do NOT read this as London-only: "
                "(1) LONDON, pounds vs PENCE (SRB.L, POLB.L); (2) TEL AVIV, "
                "shekels vs AGOROT — found 2026-08-28 on ALL 272 .TA lines, ratio "
                "0.01000 exactly, and an uncorrected first pass lost every Israeli "
                "candidate. Expect the same shape wherever a market quotes in a "
                "minor unit."
                % ("{:,.0f}".format(mcap_native),
                   "{:,.0f}".format(mcap_native * 100)))
            mcap_native = mcap_native * 100

    mcap = (mcap_native * fx_usd) if mcap_native else None

    px_usd = (px * fx_usd) if px else None
    adv_usd = (px_usd * vol) if (px_usd and vol) else None
    spread = None
    locked_book = None
    if bid and ask and ask > 0 and bid > 0 and ask == bid:
        # LOCKED BOOK — found 2026-08-28 by the Asia desk on NINE names
        # (4541.TWO, 3498.TWO, 6834.TW, 3236.TWO, 8111.TWO, 6284.TWO, 3059.TW,
        # 8050.TWO, 0120.KL), mostly Taiwan limit-moves.
        #
        # `ask >= bid` let ask == bid through, producing a spread of EXACTLY
        # 0.000% — which then passed every spread test as the tightest market
        # imaginable. It is the opposite: a locked or limit-halted book where
        # you cannot transact at all. A perfect spread is not a good sign, it is
        # an impossible one.
        locked_book = ("bid == ask == %.4f: LOCKED BOOK (limit move or halt), "
                       "not a 0%% spread. Spread UNMEASURED — you cannot trade "
                       "a market that is not two-sided." % bid)
    elif bid and ask and ask > 0 and bid > 0 and ask > bid:
        mid = (ask + bid) / 2.0
        spread = (ask - bid) / mid if mid else None

    # --- CORRUPT-QUOTE CHECK (added 2026-08-27, Hunt 2 MEA desk) ---------
    # A hazard NOT in GLOBAL_RESEARCH_PROMPT.md section 3, and the cheapest
    # check on this page. The Egypt batch found `regularMarketPrice` wrong by a
    # DIFFERENT factor per name (1.30x to 3.54x), corrupting marketCap, P/E and
    # P/B together. ETEL.CA screened at P/E 2.24 / P/B 0.76 -- the cheapest asset
    # in the entire hunt -- and is truly P/E 8.78 / P/B 3.11.
    #
    # Three of the five were catchable WITH NO SECOND SOURCE, because the quoted
    # price sat BELOW the 52-week low carried in the same payload. A price
    # outside its own 52-week range is internally contradictory: one of the two
    # fields is wrong, and neither can be trusted until it is resolved.
    lo = _num(info.get("fiftyTwoWeekLow"))
    hi = _num(info.get("fiftyTwoWeekHigh"))
    quote_corrupt = None
    # DEFECT FOUND 2026-08-28 (stub/SOTP desk): `fiftyTwoWeekLow` is 0.0 on ALL
    # nine KRX names tested. A 0.0 is falsy, so the guard below skipped the check
    # entirely and `quote_corrupt` stayed None — which every caller reads as
    # "quote verified fine". The corrupt-quote check was therefore silently
    # DISABLED ACROSS AN ENTIRE EXCHANGE and reported as a pass.
    #
    # A check that cannot run must say so. This is not a refusal (that would
    # delete the KRX), but it must never again masquerade as a clean result.
    quote_range_unusable = None
    if px and not (lo and hi and hi > lo > 0):
        quote_range_unusable = (
            "52-week range unusable (low=%s high=%s) — the corrupt-quote check "
            "COULD NOT RUN on this name. Absence of a corruption flag here is "
            "absence of a test, not evidence the quote is sound. Known to affect "
            "every KRX line." % (lo, hi))
    if px and lo and hi and hi > lo > 0:
        if px < lo * 0.995:
            quote_corrupt = ("price %.4f is BELOW its own 52-week low %.4f in the "
                             "same record — internally contradictory, so the quote "
                             "AND every ratio built on it are untrusted" % (px, lo))
        elif px > hi * 1.005:
            quote_corrupt = ("price %.4f is ABOVE its own 52-week high %.4f in the "
                             "same record — internally contradictory, so the quote "
                             "AND every ratio built on it are untrusted" % (px, hi))

    # ---- VOLUME TREND (funder LEAD RULE, 2026-08-28) ------------------------
    # "The rumour is what creates the volume and interest."
    #
    # ADV was only ever a STATIC FLOOR — can we trade today? That does not answer
    # the rule's question: IS INTEREST BUILDING INTO THE EVENT? The 10-day vs
    # 3-month average volume ratio is free, already in the payload, and had never
    # been used. It is a RECORDED SIGNAL, never a gate: volume without
    # undervaluation is momentum, and this firm's record on momentum is ~1,040
    # killed hypotheses with no validated edge.
    #
    # First reading on our own book (2026-08-28): SIX OF NINE sleeve positions
    # FADING, three of them into catalysts inside six weeks.
    _v10 = _num(info.get("averageDailyVolume10Day"))
    _v3m = (_num(info.get("averageVolume"))
            or _num(info.get("averageDailyVolume3Month")))
    volume_trend = None
    if _v10 and _v3m and _v3m > 0:
        _r = _v10 / _v3m
        volume_trend = {
            "ratio_10d_over_3m": round(_r, 3),
            "state": ("building" if _r > 1.15 else
                      "fading" if _r < 0.85 else "steady"),
            "note": ("BUILDING = anticipation is drawing volume ahead of the "
                     "date. FADING = interest is LEAVING as the catalyst "
                     "approaches — ambiguous: it may mean the market has not "
                     "noticed (we are early) or does not care (the exit "
                     "liquidity will not be there). Say WHICH and why."),
        }

    measured = {
        "volume_trend": volume_trend,
        "price_usd": None if px_usd is None else round(px_usd, 4),
        "adv_usd": None if adv_usd is None else round(adv_usd, 0),
        "position_pct_of_adv": (round(notional_usd / adv_usd, 4)
                                if adv_usd else None),
        "spread_pct": None if spread is None else round(spread * 100, 3),
        "market_cap_usd": mcap, "market_cap_native": mcap_native,
        "mcap_unit_fixed": mcap_unit_fixed,
        "mcap_unit_suspect": measured_unit_suspect,
        "side": side,
        "fifty_two_week_low": lo, "fifty_two_week_high": hi,
        "quote_corrupt": quote_corrupt,
        "quote_range_unusable": quote_range_unusable,
        "locked_book": locked_book,
    }
    # --- SHARE-COUNT INTEGRITY (added 2026-08-28, net-net desk) ------------
    # `sharesOutstanding` returns only the LISTED TRANCHE on H-share companies
    # and ADRs, while `marketCap` uses the full count. 78 of 1,181 names in the
    # net-net sweep disagreed by >10%, systematically ~4x in Hong Kong: 2386.HK
    # read 2.67x too cheap and 1456.HK 12.8x. It is not in the section 3 hazard
    # list and it silently corrupts ANY per-share screen -- NCAV, book, earnings.
    # This does not refuse the name; it flags that per-share figures derived from
    # the share count cannot be trusted without reconciling the two fields.
    shares = _num(info.get("sharesOutstanding"))
    if px and shares and mcap_native and mcap_native > 0:
        implied = px * shares
        ratio = mcap_native / implied if implied else None
        if ratio and (ratio > 1.10 or ratio < 0.90):
            measured["share_count_mismatch"] = (
                "marketCap disagrees with price x sharesOutstanding by %.2fx "
                "(%.0f vs %.0f). FOUR KNOWN CAUSES, in OPPOSITE directions — "
                "DIAGNOSE, never assume: "
                "(a) `sharesOutstanding` covers only the LISTED TRANCHE (H-share "
                "or ADR) while marketCap uses the full count — this makes every "
                "PER-SHARE figure look TOO CHEAP (6049.HK 1.595x, 2386.HK 2.67x, "
                "1456.HK 12.8x); (b) on a "
                "DUAL-CLASS issuer `sharesOutstanding` is correct and per-class "
                "while marketCap applies ONE class's price to ALL classes — "
                "proven arithmetically on VOW.DE, and worst at LISN.SW (+378%%) — "
                "this INFLATES the market cap, the DANGEROUS direction, because "
                "it makes a company look larger and more liquid than it is "
                "(TAEE11.SA 3.00x, 0902.HK 3.36x, VER.VI 2.04x, CWEN 2.04x, "
                "found across 13 utility lines 2026-08-28); "
                "or (c) a GBp/GBP UNIT SPLIT on London, which is detected and "
                "auto-corrected separately and reported as `mcap_unit_fixed` — "
                "check that field before diagnosing (a) or (b). "
                "Diagnose before using either field. EVERY per-share figure "
                "derived from the wrong one is off by this factor."
                % (ratio, mcap_native, implied))

    reasons = []
    # DOES THE LISTING STILL EXIST? Added 2026-08-28 after the AIM desk found
    # ANCR.L passing the liquidity gate, the dilution gate AND the cap check
    # while no longer trading at all — court-sanctioned scheme, suspended,
    # cancelled. Its `marketState` was None. CYAN.L is the same shape.
    #
    # This is not a rare edge: 11 of 110 US tickers, 60 of 161 Canadian, 32 of
    # 138 ASX and 31 of 131 London lines in the 2026-08-28 hunts were dead,
    # renamed or unquoted. **A position in a security that does not trade is not
    # an expensive position, it is not a position.** No amount of good
    # fundamentals survives the listing being gone.
    _state = info.get("marketState")
    _qtype = info.get("quoteType")
    _name = info.get("shortName") or info.get("longName")
    if _state is None and not vol and not px:
        reasons.append(
            "LISTING NOT VERIFIED: no marketState, no volume and no price. This "
            "is the shape of a suspended, cancelled or delisted line, not a "
            "quiet one. Confirm the security still trades before any research.")
    elif not _name or not _qtype:
        measured["listing_unconfirmed"] = (
            "shortName/quoteType absent — identity NOT confirmed. Prior desks "
            "researched two entirely different companies (ENDO -> Endocan Corp, "
            "ENDI -> ENDI Corp) and hit corrupt composite records "
            "(NCU.TO, SENS.ST). Confirm the issuer before trusting any figure.")

    if quote_corrupt:
        reasons.append("CORRUPT QUOTE: " + quote_corrupt)
    if measured_unit_suspect:
        reasons.append("UNIT SPLIT SUSPECTED, NOT CORRECTED: " + measured_unit_suspect)

    # Missing data is a REFUSAL, not a pass. An unmeasurable position cannot be
    # shown to be tradable, and "we could not check" must never read as "fine".
    if px_usd is None:
        reasons.append("no usable price — cannot assess tradability")
    elif fx_usd == 1.0 and px_usd < MIN_PRICE_USD:
        # The floor is tested on the LOCAL quote, not its USD equivalent. Found
        # 2026-08-27 by the net-net desk: HK$4.98 converts to US$0.635 and was
        # refused as a penny stock. HK$4.98 is an ordinary quote with ordinary
        # tick behaviour -- the rule exists because sub-dollar TICK SIZE and
        # minimum increments make a modelled spread optimistic, and tick size is
        # a property of the local market, not of the exchange rate.
        #
        # Applied to converted prices it deleted every Hong Kong candidate plus
        # Singapore and Sweden from a global screen, for reasons having nothing
        # to do with the companies. So it is enforced only where the quote is
        # already USD; elsewhere the spread and ADV tests do the work.
        reasons.append("price $%.4f below the $%.2f floor — tick-size and minimum "
                       "increment effects make any modelled spread optimistic"
                       % (px_usd, MIN_PRICE_USD))
    elif (fx_usd != 1.0 and px is not None and px < MIN_PRICE_USD
          and spread is None):
        # NARROWED 2026-08-28 after an ASX minerals desk showed this refusing
        # AIS.AX on price NOTATION while every tradability measure passed:
        # **measured spread 0.96%**, ADV US$4.73m, cap US$566m.
        #
        # The floor was only ever a PROXY for "the modelled spread will be too
        # optimistic". When the spread has actually been MEASURED, the proxy is
        # redundant and the measurement governs — refusing a name whose real
        # spread is 0.96% because it is quoted at A$0.65 is deleting it on price
        # notation, not on tradability, and on the ASX that removes a large slice
        # of perfectly liquid small-cap miners.
        #
        # It is also dimensionally arbitrary: MIN_PRICE_USD is 1.00, but 1.00 AUD,
        # 1.00 HKD, 1.00 JPY and 1.00 KRW are not comparable amounts or comparable
        # tick contexts. Keeping it ONLY where the spread is unmeasured confines it
        # to the case it was written for.
        reasons.append("local quote %.4f is sub-unit in its own currency AND the "
                       "spread is UNMEASURED — with no measured spread the "
                       "tick-size risk cannot be bounded. Re-measure in session; "
                       "if the real spread clears the cap this refusal does not "
                       "apply." % px)
    if adv_usd is None:
        reasons.append("no volume data — average daily traded value unknown, so "
                       "exit impact cannot be bounded")
    else:
        if adv_usd < MIN_ADV_USD:
            reasons.append("ADV $%s below the $%s floor — no position size is safe"
                           % ("{:,.0f}".format(adv_usd), "{:,.0f}".format(MIN_ADV_USD)))
        frac = notional_usd / adv_usd
        if frac > MAX_POSITION_PCT_OF_ADV:
            reasons.append("position is %.1f%% of one day's traded value (max %.0f%%) "
                           "— the exit would BE the price move"
                           % (frac * 100, MAX_POSITION_PCT_OF_ADV * 100))
    # SESSION CHECK — added 2026-09-03. This supersedes the two MAGNITUDE
    # heuristics below for the case they were both invented to approximate.
    #
    # Each guard below infers "the market is shut" from how WIDE the spread
    # looks: above 20% it is a stub, or above 2% on US$50m+ of turnover it is
    # arithmetically impossible. Neither asks whether the market is ACTUALLY
    # shut — and between them sits a measured blind spot. A name turning over
    # LESS than US$50m with a spread between 1% and 20% satisfies neither guard
    # and falls through to the 1% MERIT refusal.
    #
    # MEASURED 2026-09-03 by the strategic-reviews desk, capturing 22:00-22:50 ET
    # with New York in POSTPOST: YEXT 9.21%, ONL 10.30%, RYAM 3.70%, PBI 3.51%,
    # HE 2.10% — every one refused as "spread exceeds 1.00%", every one on
    # US$1m-40m of daily turnover, and not one of them holding a real quote at
    # the moment of capture. The gate was rejecting tradable names on merit,
    # citing a number that did not exist.
    #
    # A quote posted outside a REGULAR session is not a wide spread. It is not a
    # spread at all. So it becomes UNMEASURED — never a pass, never a refusal —
    # and the name is judged on the session-independent criteria exactly as the
    # placeholder branch already does.
    #
    # `_state` is only trusted when truthy: a None marketState is the shape of a
    # suspended or delisted line and is handled by the listing check above, so it
    # must not be silently converted into "the market is merely closed".
    if spread is not None and _state and _state != "REGULAR":
        measured["spread_pct"] = None
        measured["spread_unmeasured"] = (
            "quoted %.2f%% while the venue is in %s, not REGULAR. An out-of-session "
            "quote is not a market: the spread is UNMEASURED, which is NOT a "
            "refusal on merit. Re-measure in session before sizing."
            % (spread * 100, _state))
        measured["spread_unmeasured_reason"] = "out_of_session"
        spread = None

    if (spread is not None and spread > IMPLAUSIBLE_SPREAD_PCT
            and adv_usd and adv_usd >= IMPLAUSIBLE_SPREAD_ADV_USD
            and spread <= PLACEHOLDER_SPREAD_PCT):
        # Wide spread on a very heavily traded name: impossible, therefore an
        # out-of-session artifact. Same treatment as the placeholder band — it
        # becomes UNMEASURED, never a pass and never a refusal.
        measured["spread_pct"] = None
        measured["spread_unmeasured"] = (
            "quoted %.2f%% on US$%.0fm of daily turnover is arithmetically "
            "impossible — a name this liquid trades at basis-point spreads, so "
            "this is an out-of-session artifact, not a market. Spread UNMEASURED; "
            "re-measure in session (09:30-16:00 ET) before sizing."
            % (spread * 100, adv_usd / 1e6))
        spread = None
    elif spread is not None and spread > PLACEHOLDER_SPREAD_PCT:
        # A "spread" this wide is not a market, it is a placeholder band posted
        # outside session hours. Found 2026-08-27 by the US small-cap desk running
        # at 08:43 ET: NPK quoted bid $67.75 / ask $240.55 on a $151 stock (112%),
        # and the gate refused 44 of 44 names on quotes that did not exist yet.
        #
        # Treating that as a REFUSAL is a false negative that would kill an entire
        # session's work. Treating it as a PASS would be worse. So it is neither:
        # spread becomes UNMEASURED, the name is judged on the session-independent
        # criteria (price, ADV, position-%-of-ADV, market cap), and the flag says
        # plainly that round-trip cost is unknown and MUST be re-measured in
        # session before anything is sized.
        measured["spread_pct"] = None
        measured["spread_unmeasured"] = (
            "quoted %.1f%% is a placeholder band, not a market (likely outside "
            "session hours) — spread UNMEASURED, re-measure in session before "
            "sizing. This is the criterion most likely to fail at small cap."
            % (spread * 100))
        spread = None
    elif spread is None:
        # NOTE 2026-09-03: this branch must keep matching EVERY None spread, so
        # that None still terminates the if/elif chain before `spread >
        # MAX_SPREAD_PCT` below. An earlier attempt at the fix added the
        # already-explained condition to THIS line, which let an out-of-session
        # None fall through to that comparison and raise
        # `TypeError: '>' not supported between NoneType and float`. The guard
        # belongs on the MESSAGE, not on the branch.
        #
        # Absence of a quote field is NOT evidence of a wide spread. Found
        # 2026-08-27 by the holdco desk: this refusal killed 14 of 25 candidates
        # and, because every Euronext line returns no bid/ask at all, it deleted
        # the entire continental-European holding-company pool -- GBL, Sofina,
        # Exor, Prosus, Bollore, Wendel -- none of which is remotely illiquid.
        # It re-ran byte-identical, so it is a FEED limitation, not timing.
        #
        # The honest distinction is size. On a name whose traded value clears the
        # floor several times over, a missing bid/ask is a gap in the data. On a
        # thin name it is a genuine red flag, because that is exactly where a
        # quote goes missing for real reasons. So: forgive it where ADV is
        # comfortable, refuse it where ADV is marginal, and never silently
        # pretend the cost is known.
        if measured.get("spread_unmeasured"):
            # An upstream branch (out-of-session, placeholder band, implausible
            # spread) already set spread to None AND recorded why. Do not
            # overwrite that with "no bid/ask in the feed" — there WAS a
            # two-sided quote, and a reader acting on the overwritten text would
            # hunt a feed problem that does not exist.
            pass
        elif adv_usd and adv_usd >= MIN_ADV_USD * SPREAD_WAIVER_ADV_MULTIPLE:
            measured["spread_unmeasured"] = (
                "no bid/ask in the feed. ADV of $%s clears the floor %.0fx, so this "
                "reads as a data gap rather than illiquidity — spread UNMEASURED, "
                "re-measure before sizing. Round-trip cost is NOT established."
                % ("{:,.0f}".format(adv_usd), adv_usd / MIN_ADV_USD))
        else:
            reasons.append("no bid/ask AND ADV only $%s — on a name this thin a "
                           "missing quote is a red flag, not a feed gap; half the "
                           "spread is paid on entry and again on exit"
                           % ("{:,.0f}".format(adv_usd or 0)))
    elif spread > MAX_SPREAD_PCT:
        reasons.append("spread %.2f%% exceeds %.2f%% — round-trip handicap of ~%.2f%% "
                       "against a 5%% net gate"
                       % (spread * 100, MAX_SPREAD_PCT * 100, spread * 100))
    if mcap is None:
        reasons.append("no market cap — cannot size relative to the company")
    elif mcap < MIN_MARKET_CAP_USD:
        reasons.append("market cap $%s below the $%s floor"
                       % ("{:,.0f}".format(mcap), "{:,.0f}".format(MIN_MARKET_CAP_USD)))

    if side == "short":
        if mcap is not None and mcap < MIN_MARKET_CAP_FOR_SHORT_USD:
            reasons.append("SHORT REFUSED: market cap $%s below $%s — borrow does "
                           "not realistically exist. A short that cannot be "
                           "borrowed is not an expensive position, it is not a "
                           "position." % ("{:,.0f}".format(mcap),
                                          "{:,.0f}".format(MIN_MARKET_CAP_FOR_SHORT_USD)))
        if adv_usd is not None and adv_usd < MIN_ADV_FOR_SHORT_USD:
            reasons.append("SHORT REFUSED: ADV $%s below $%s — a crowded exit in a "
                           "thin name is how a short becomes unclosable"
                           % ("{:,.0f}".format(adv_usd),
                              "{:,.0f}".format(MIN_ADV_FOR_SHORT_USD)))

    return {"ok": not reasons, "reasons": reasons, "measured": measured}


def max_safe_notional(info, fx_usd=1.0):
    """Largest position the ADV rule permits. None when volume is unknown."""
    px = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if px is None:  # 2026-10-29 infra (Iris)
        try:
            from veteran_desk import feeds as _FD
            _pc = _FD.prior_close(info.get("symbol") or "", yahoo_prev=info.get("previousClose"))
            px = _num(_pc.get("close"))
        except Exception:
            px = _num(info.get("previousClose"))
    vol = (_num(info.get("averageDailyVolume3Month"))
           or _num(info.get("averageDailyVolume10Day")))
    if not px or not vol:
        return None
    return px * fx_usd * vol * MAX_POSITION_PCT_OF_ADV


if __name__ == "__main__":
    import json
    print(__doc__)
    print(json.dumps(thresholds(), indent=2))
