#!/usr/bin/env python3
"""Per-venue instrument listings + mapping from the Kraken desk's symbols.

A replica cannot trade what the target venue does not list. The Kraken engine's
live universe is drawn from ~250 USD pairs including deep micro-caps ($0.5-5M/day).
Every retail venue lists far fewer. So a meaningful share of what the Kraken desk
trades has no instrument at the target venue at all — and the micro-cap desk,
which deliberately hunts the *least* institutionalised corner, maps across barely
at all.

That gap is a finding, not a bug. This module measures it, per venue.

+---------------------------------------------------------------------------+
| THE LISTING SETS BELOW ARE ESTIMATES, NOT VERIFIED FEEDS.                  |
| Venues add and remove assets, and availability differs by region and       |
| regulator. Override with venue_universe.json — symbols pulled from the     |
| operator's own account — rather than trusting these.                      |
|                                                                           |
| They are deliberately UNDER-inclusive. A false "not listed" understates    |
| the opportunity set, which is the safe direction to be wrong in. A false   |
| "listed" would invent a tradeable instrument that does not exist.          |
+---------------------------------------------------------------------------+
"""
import json
import os

import venue_costs

BASE = os.path.dirname(os.path.abspath(__file__))
OVERRIDE_FILE = os.path.join(BASE, "venue_universe.json")

VERIFIED_AGAINST_LIVE_ACCOUNT = False

# Robinhood Crypto lists a NARROW set — roughly 20-30 assets, majors and a few
# well-known alts. Materially narrower than eToro's, which matters: switching to
# Robinhood improves cost by ~10x but SHRINKS the tradeable universe.
ROBINHOOD_CRYPTO = {
    "BTC", "ETH", "DOGE", "LTC", "BCH", "ETC", "SOL", "ADA", "AVAX", "LINK",
    "XLM", "UNI", "AAVE", "COMP", "SHIB", "XTZ", "USDC", "MATIC", "POL", "DOT",
    "ARB", "PEPE", "XRP", "TRX", "SUI", "ONDO", "CRV",
}

# eToro — retained for the retired-venue comparison.
ETORO_CRYPTO = {
    "BTC", "ETH", "XRP", "LTC", "BCH", "ADA", "DOT", "DOGE", "SOL", "LINK",
    "XLM", "EOS", "TRX", "ZEC", "XTZ", "NEO", "BNB", "AVAX", "UNI", "AAVE",
    "ALGO", "ATOM", "FIL", "MANA", "SAND", "SHIB", "CRV", "MKR", "COMP", "YFI",
    "ENJ", "BAT", "CHZ", "GRT", "SNX", "ZRX", "APE", "AXS", "DASH", "ETC",
    "GALA", "ICP", "NEAR", "STX", "THETA", "VET", "MATIC", "POL", "FTM",
    "HBAR", "EGLD", "KSM", "SUSHI", "1INCH", "QTUM", "OMG", "MIOTA", "IOTA",
    "SUI", "TON", "APT", "ARB", "OP", "PEPE", "INJ", "IMX", "RNDR", "RENDER",
    "FET", "LDO", "ONDO", "TAO", "SEI", "TIA", "WIF", "JUP", "BONK",
}

# Kraken lists essentially everything the engine ranks — it IS the source universe.
KRAKEN_CRYPTO = None   # None means "no listing restriction"

VENUE_LISTINGS = {
    "robinhood": ROBINHOOD_CRYPTO,
    "etoro": ETORO_CRYPTO,
    "kraken": KRAKEN_CRYPTO,
}

# Kraken uses some legacy tickers.
KRAKEN_TO_COMMON = {
    "XBT": "BTC",
    "XDG": "DOGE",
    "XMR": None,       # privacy coin — delisted at most retail venues
    "MIOTA": "IOTA",
}

# Large, tightly-quoted names get the venue's tighter schedule; the rest route to
# the wider "crypto_alt" schedule. Drawn generously so the modelled cost wall is
# not inflated.
MAJORS = {
    "BTC", "ETH", "XRP", "LTC", "BCH", "ADA", "SOL", "DOT", "DOGE", "LINK",
    "XLM", "TRX", "AVAX", "MATIC", "POL", "UNI", "AAVE", "ATOM", "ALGO", "XTZ",
    "SHIB", "NEAR", "ICP", "FIL", "ETC", "HBAR", "VET", "EOS", "SUI", "TON",
    "APT", "ARB", "OP", "PEPE", "INJ", "IMX", "TIA",
}


def load_overrides(path=OVERRIDE_FILE):
    """Replace an estimated listing set with the operator's real one.

    Shape: {"venues": {"robinhood": ["BTC", "ETH", ...]}, "verified": true}
    """
    global VERIFIED_AGAINST_LIVE_ACCOUNT
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return False
    for vname, syms in (data.get("venues") or {}).items():
        if isinstance(syms, list) and syms:
            VENUE_LISTINGS[vname] = {str(s).upper() for s in syms}
    if data.get("verified") is True:
        VERIFIED_AGAINST_LIVE_ACCOUNT = True
    return True


def listings(venue_name=None):
    return VENUE_LISTINGS.get(venue_name or venue_costs.ACTIVE_VENUE, set())


def verification_notice(venue_name=None):
    v = venue_costs.venue(venue_name)
    if VERIFIED_AGAINST_LIVE_ACCOUNT:
        return "%s universe: OPERATOR-VERIFIED (venue_universe.json)" % v["label"]
    return ("%s universe: ESTIMATED listing set, deliberately under-inclusive"
            % v["label"])


def base_ticker(kraken_symbol):
    """'XBT/USD' -> 'BTC'. None if it maps to nothing tradeable."""
    base = kraken_symbol.split("/")[0].upper()
    if base in KRAKEN_TO_COMMON:
        return KRAKEN_TO_COMMON[base]
    return base


def resolve(kraken_symbol, venue_name=None):
    """Map a Kraken symbol to a venue instrument.

    Returns (ticker, instrument_class) or (None, reason).
    """
    vname = venue_name or venue_costs.ACTIVE_VENUE
    base = base_ticker(kraken_symbol)
    if base is None:
        return None, "delisted-or-unavailable"
    listed = VENUE_LISTINGS.get(vname)
    if listed is not None and base not in listed:
        return None, "not-listed-at-%s" % vname
    return base, ("crypto" if base in MAJORS else "crypto_alt")


def is_tradeable(kraken_symbol, venue_name=None):
    return resolve(kraken_symbol, venue_name)[0] is not None


def coverage(kraken_symbols, venue_name=None):
    """How much of a Kraken universe actually exists at the venue."""
    tradeable, missing = [], {}
    for s in kraken_symbols:
        tick, info = resolve(s, venue_name)
        if tick:
            tradeable.append(s)
        else:
            missing[s] = info
    n = len(kraken_symbols)
    return {
        "venue": venue_name or venue_costs.ACTIVE_VENUE,
        "total": n, "tradeable": len(tradeable), "missing": len(missing),
        "coverage_pct": round(len(tradeable) / n * 100, 1) if n else 0.0,
        "tradeable_symbols": tradeable, "missing_symbols": missing,
    }


def kraken_live_universe(port=18799, timeout=6):
    """Pull the Kraken engine's current live universe (read-only)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/state" % port, timeout=timeout) as r:
            state = json.load(r)
    except Exception:
        return []
    out = []
    for x in state.get("universe") or []:
        if isinstance(x, str) and "/" in x:
            out.append(x)
        elif isinstance(x, dict) and isinstance(x.get("symbol"), str):
            out.append(x["symbol"])
    return out


load_overrides()


if __name__ == "__main__":
    print(verification_notice())
    print()
    live = kraken_live_universe()
    if not live:
        print("Kraken engine not reachable on :18799 — cannot measure live coverage.")
    else:
        print("KRAKEN LIVE UNIVERSE (%d symbols) -> COVERAGE BY VENUE" % len(live))
        for v in ("robinhood", "etoro"):
            c = coverage(live, v)
            print("  %-12s %2d of %2d tradeable (%.1f%%)"
                  % (venue_costs.VENUES[v]["label"], c["tradeable"], c["total"],
                     c["coverage_pct"]))
        print()
        c = coverage(live)
        print("Not tradeable at the ACTIVE venue (%s):" % c["venue"])
        for sym, why in sorted(c["missing_symbols"].items()):
            print("    %-14s %s" % (sym, why))
