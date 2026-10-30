#!/usr/bin/env python3
"""V1 ENTRY-venue allowlist (funder directive Mon 28 Sep 2026 20:04 AEST). PAPER ONLY.

  "remove London and just trade NASDAQ, Tokyo and ASX AFTER today."

From 2026-09-29 (Australia/Sydney date of the entry) V1 opens NEW equity positions only on
US exchanges (NASDAQ/NYSE: symbols with no suffix), Tokyo (.T) and ASX (.AX). Every other equity
venue (.L, Europe, .HK, .TW, .TO, .SN, .NS, .SA, ...) is refused with VENUE_NOT_ALLOWED.

Scope, deliberately narrow:
  * ENTRIES ONLY. Exits, stops, trails, half-banks, max-hold, marks and quote fetching are never
    gated: existing positions on any venue run off on their rails.
  * CRYPTO IS NOT GATED (funder confirmed crypto continues unchanged, 20:05 AEST 28 Sep 2026).
  * Entries dated before 2026-09-29 Sydney are unaffected (tonight's 28 Sep US fire runs as before).
Single source of truth: every V1 entry path imports this module.

AMENDMENT (funder override Tue 29 Sep 2026 16:22 AEST): "NEW RULE - no more Tokyo or Asia Markets."
From 2026-09-29 16:22 AEST V1 opens NEW equity positions only on US (NASDAQ/NYSE) and ASX (.AX).
Tokyo (.T) and every other Asian venue (.HK .TW .TWO .KS .KQ .SS .SZ .SI .NS .BO .JK .BK .KL ...)
are refused with VENUE_NOT_ALLOWED; the TOKYO watchlist market is no longer hunted. Crypto unchanged.
Existing Tokyo/Asia positions keep running on their normal exit rails (stops, +2% half, trail,
max-hold); they are NOT liquidated (liquidation_exempt() -> tfo_rotation venue_retired keeps them).
"""
import datetime as dt
from zoneinfo import ZoneInfo

SYD = ZoneInfo("Australia/Sydney")
EFFECTIVE_SYD = dt.date(2026, 9, 29)
# Phase 1 (28 Sep directive, from the 29 Sep Sydney date): US / Tokyo / ASX.
PHASE1_EQUITY_VENUES = frozenset({"US", "T", "AX"})
PHASE1_WATCHLIST_MARKETS = frozenset({"US", "TOKYO", "ASX", "CRYPTO"})
DIRECTIVE = "funder directive 2026-09-28 20:04 AEST"
# Phase 2 (29 Sep 16:22 AEST override): US / ASX only; Tokyo and all Asia out.
ASIA_CUTOFF = dt.datetime(2026, 9, 29, 16, 22, tzinfo=SYD)
ALLOWED_EQUITY_VENUES = frozenset({"US", "AX"})
ALLOWED_WATCHLIST_MARKETS = frozenset({"US", "ASX", "CRYPTO"})
ASIA_DIRECTIVE = "funder override 2026-09-29 16:22 AEST: no more Tokyo or Asia markets"
ASIA_VENUES = frozenset({"T", "HK", "TW", "TWO", "KS", "KQ", "SS", "SZ", "SI", "NS", "BO", "JK", "BK", "KL", "PS", "VN", "HM"})
ASIA_MARKETS = frozenset({"TOKYO", "HK", "INDIA"})
CODE = "VENUE_NOT_ALLOWED"


def venue_of(sym):
    s = (sym or "").strip().upper()
    if "/" in s or s.startswith("PF_") or s.endswith("-USD") or s.endswith("USD.P"):
        return "CRYPTO"
    return "US" if "." not in s else s.rsplit(".", 1)[1]


def _syd_date(when=None):
    if when is None:
        when = dt.datetime.now(dt.timezone.utc)
    if isinstance(when, dt.datetime):
        if when.tzinfo is None:
            raise ValueError("naive datetime: pass an aware time")
        return when.astimezone(SYD).date()
    return when  # a date is taken as a Sydney date


def in_force(when=None):
    return _syd_date(when) >= EFFECTIVE_SYD


def asia_in_force(when=None):
    """True from 2026-09-29 16:22 AEST. A bare date on/after 29 Sep counts as in force (cannot prove it predates 16:22)."""
    if when is None:
        when = dt.datetime.now(dt.timezone.utc)
    if isinstance(when, dt.datetime):
        if when.tzinfo is None:
            raise ValueError("naive datetime: pass an aware time")
        return when >= ASIA_CUTOFF
    return when >= ASIA_CUTOFF.date()


def allowed_equity_venues(when=None):
    return ALLOWED_EQUITY_VENUES if asia_in_force(when) else PHASE1_EQUITY_VENUES


def allowed_watchlist_markets(when=None):
    return ALLOWED_WATCHLIST_MARKETS if asia_in_force(when) else PHASE1_WATCHLIST_MARKETS


def is_asia(sym):
    return venue_of(sym) in ASIA_VENUES


def liquidation_exempt(sym):
    """Asia/Tokyo leftovers run off on their rails (16:22 override); never sold by a venue-retired sweep."""
    return is_asia(sym)


def entry_block_reason(sym, when=None):
    """None if a NEW V1 entry in `sym` is allowed at `when`; else a VENUE_NOT_ALLOWED reason."""
    v = venue_of(sym)
    if v == "CRYPTO" or not in_force(when):
        return None
    if asia_in_force(when) and v in ASIA_VENUES:
        return ("%s: .%s entries closed from 2026-09-29 16:22 AEST (%s); V1 entry venues are US, ASX (.AX); "
                "existing positions run off on their rails" % (CODE, v, ASIA_DIRECTIVE))
    if v in allowed_equity_venues(when):
        return None
    return ("%s: .%s entries closed from %s Sydney (%s); V1 entry venues are %s; "
            "existing positions run off on their rails" % (CODE, v, EFFECTIVE_SYD.isoformat(), DIRECTIVE,
                                                           "US, ASX (.AX)" if asia_in_force(when) else "US, Tokyo (.T), ASX (.AX)"))


def market_block_reason(market, when=None):
    """Watchlist market key (tfo_watchlist MARKETS) gate: None if still hunted."""
    m = (market or "").upper()
    if m == "CRYPTO" or not in_force(when):
        return None
    if asia_in_force(when) and m in ASIA_MARKETS:
        return ("%s: market %s removed from V1 pre-open hunt from 2026-09-29 16:22 AEST (%s); hunted: US, ASX, CRYPTO"
                % (CODE, m, ASIA_DIRECTIVE))
    if m in allowed_watchlist_markets(when):
        return None
    return ("%s: market %s removed from V1 pre-open hunt from %s Sydney (%s); hunted: %s"
            % (CODE, m, EFFECTIVE_SYD.isoformat(), DIRECTIVE, "US, ASX, CRYPTO" if asia_in_force(when) else "US, TOKYO, ASX, CRYPTO"))


def require_entry_allowed(sym, when=None):
    """Hard stop for code paths that open positions without a gate list."""
    r = entry_block_reason(sym, when)
    if r:
        raise SystemExit("REFUSED: " + r)


# ----------------------------------------------------------------------------- Fundamental-only override
# Brett 28 Oct 2026 14:30 WITA: the TFO Fundamental book (value_deploy / value_deploy_auto) may ALSO open
# Japan (.T) and London (.L). Every other V1 book keeps the US/ASX rule above unchanged.
FUNDAMENTAL_EXTRA_VENUES = frozenset({"T", "L"})
FUNDAMENTAL_OVERRIDE = "Brett 2026-10-28 14:30 WITA: Fundamental book may enter .T and .L"


def fundamental_entry_block_reason(sym, when=None):
    """Like entry_block_reason, but .T / .L are allowed for the Fundamental book only."""
    if venue_of(sym) in FUNDAMENTAL_EXTRA_VENUES:
        return None
    return entry_block_reason(sym, when)
