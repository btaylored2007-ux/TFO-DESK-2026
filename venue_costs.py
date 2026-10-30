#!/usr/bin/env python3
"""Multi-venue cost model — PAPER SIMULATION ONLY.

Answers one question per venue: *what would this trade actually cost there?*

Venue-neutral by design. Swapping the target is a config change (`ACTIVE_VENUE`),
not a rewrite — because it has already been swapped once and will be again.

+---------------------------------------------------------------------------+
| THE NUMBERS BELOW ARE MODELLED, NOT READ FROM ANY LIVE ACCOUNT.           |
|                                                                           |
| This desk holds NO broker credentials and makes NO authenticated broker    |
| calls. Its entire outbound surface is public Kraken market-data endpoints  |
| plus localhost. Fee schedules vary by region, regulator, instrument and    |
| time. Replace these with your account's published rates via               |
| venue_costs.json before leaning on any conclusion.                        |
+---------------------------------------------------------------------------+

Operator context: resident of AUSTRALIA and the UNITED KINGDOM. Venue eligibility
below is recorded per jurisdiction, because it is the constraint that actually
decides what is usable — not the cost table.
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
OVERRIDE_FILE = os.path.join(BASE, "venue_costs.json")

ACTIVE_VENUE = "robinhood"
VERIFIED_AGAINST_LIVE_ACCOUNT = False
ACCOUNT_CURRENCY = "AUD"


# ---------------------------------------------------------------------------
# VENUES
#   spread_pct        full round-trip spread as a fraction of notional
#   commission_pct    per-side explicit fee
#   overnight_bps_day financing per calendar day, margin/CFD only
#   weekend_mult      multiplier applied on weekend_day (0=Mon .. 6=Sun)
# ---------------------------------------------------------------------------
VENUES = {

    # ---------------------------------------------------------------- ACTIVE
    # Robinhood Crypto. Official public REST trading API (announced 2024-05-30,
    # trading.robinhood.com), API-key auth: buying power, holdings, market data,
    # place/retrieve/cancel orders. A genuinely sanctioned automation path --
    # unlike eToro, which has none for retail.
    #
    # ELIGIBILITY, the binding constraint:
    #   AU: NOT available. Australia is not a launch market as of 2026-08.
    #   UK: crypto NEWLY cleared -- Robinhood U.K. Ltd entered the FCA cryptoasset
    #       register 2026-07-31. BUT it may only ARRANGE OR TRANSMIT crypto orders;
    #       it cannot hold customer cryptoassets, take client money, or run an
    #       exchange. That is a materially different service from US Robinhood
    #       Crypto, and custody sitting elsewhere changes execution and risk.
    #   API: documented for US crypto customers. NO evidence of UK API access.
    #       VERIFY DIRECTLY WITH ROBINHOOD before assuming a UK account can use it.
    #
    # Pricing is spread-embedded, NO volume tiers, NO maker rebates -- so cost is
    # less predictable than maker/taker and widens in volatility, exactly when a
    # signal would want to trade.
    "robinhood": {
        "label": "Robinhood Crypto",
        "status": "active",
        "api": "official REST trading API, API-key auth — CRYPTO ONLY, documented for US customers",
        "eligibility": {
            "AU": {"available": False,
                   "note": "Not a launch market as of 2026-08"},
            "UK": {"available": "partial",
                   "note": "FCA cryptoasset register 2026-07-31; may only arrange/transmit "
                           "orders, cannot hold cryptoassets or client money. API access "
                           "from the UK is UNCONFIRMED — verify with Robinhood."},
        },
        "equities_api": False,
        "classes": {
            "crypto": {
                "spread_pct": 0.0035,      # BTC-USD typically ~0.3-0.4%
                "commission_pct": 0.0,     # embedded in spread, no explicit fee
                "overnight_bps_day": 0.0,  # spot
                "weekend_mult": 1.0, "weekend_day": None,
                "min_trade_usd": 1.0, "max_leverage": 1.0,
                "note": "Spread-based, no volume tiers, no maker rebates.",
            },
            "crypto_alt": {
                "spread_pct": 0.0150,      # alts run much wider, 1.5%+
                "commission_pct": 0.0, "overnight_bps_day": 0.0,
                "weekend_mult": 1.0, "weekend_day": None,
                "min_trade_usd": 1.0, "max_leverage": 1.0,
            },
        },
        # NOTE: fx_conversion is modelled as 0 because Robinhood charges US crypto
        # customers no explicit conversion fee. For a GBP- or AUD-funded account the
        # real conversion cost is NOT zero and is currently UNMODELLED — it must be
        # measured from the operator's own funding path before any net figure here
        # is treated as complete.
        "account_fees": {"fx_conversion_pct": 0.0, "withdrawal_fee_usd": 0.0,
                         "fx_conversion_unmodelled_for_non_usd_funder": True},
    },

    # ------------------------------------------------------------- REFERENCE
    # Kraken. The venue whose public data this desk consumes and the wall every
    # research conclusion so far was measured against. These numbers are what the
    # live paper engine actually runs on.
    #
    # WORTH NOTING: Kraken has a full authenticated REST/WebSocket trading API,
    # serves BOTH the UK and Australia, and has the lowest costs of anything here
    # (maker can reach 0% at volume). If the goal is an automatable venue with the
    # best cost structure, this desk has arguably been sitting on it all along.
    # No authenticated Kraken call has ever been made from this desk.
    "kraken": {
        "label": "Kraken spot",
        "status": "reference",
        "api": "full authenticated REST/WebSocket trading API (NOT used — public data only)",
        "eligibility": {
            "AU": {"available": True, "note": "serves Australia"},
            "UK": {"available": True, "note": "serves the UK"},
        },
        "equities_api": False,
        "verified": True,
        "classes": {
            "crypto": {
                "spread_pct": 0.0010, "commission_pct": 0.0010,   # maker
                "overnight_bps_day": 0.0, "weekend_mult": 1.0, "weekend_day": None,
                "min_trade_usd": 10.0, "max_leverage": 1.0,
            },
        },
        "account_fees": {"fx_conversion_pct": 0.0, "withdrawal_fee_usd": 0.0},
    },

    # --------------------------------------------------------------- RETIRED
    # eToro. Retired 2026-08-06 at the operator's direction. Kept as a reference
    # profile because "eToro crypto is ~10x too expensive" is a FINDING, not dead
    # code -- 931 trades were measured against it, and no candidate in the
    # literature survey had a break-even above its 350 bps wall. It also has no
    # retail order-placement API, so it was never automatable.
    "etoro": {
        "label": "eToro (retired)",
        "status": "retired",
        "api": "NONE for retail — partner/institutional only",
        "retired_on": "2026-08-06",
        "retired_reason": "~350 bps crypto round trip (~12x Kraken) and no retail API",
        "eligibility": {"AU": {"available": True}, "UK": {"available": True}},
        "equities_api": False,
        "classes": {
            "crypto": {"spread_pct": 0.0150, "commission_pct": 0.0100,
                       "overnight_bps_day": 2.6, "weekend_mult": 3.0, "weekend_day": 4,
                       "min_trade_usd": 10.0, "max_leverage": 2.0},
            "crypto_alt": {"spread_pct": 0.0500, "commission_pct": 0.0100,
                           "overnight_bps_day": 2.6, "weekend_mult": 3.0, "weekend_day": 4,
                           "min_trade_usd": 10.0, "max_leverage": 2.0},
            "fx_major": {"spread_pct": 0.0020, "commission_pct": 0.0,
                         "overnight_bps_day": 1.4, "weekend_mult": 3.0, "weekend_day": 2,
                         "min_trade_usd": 50.0, "max_leverage": 30.0},
            "stock": {"spread_pct": 0.0018, "commission_pct": 0.0,
                      "overnight_bps_day": 1.9, "weekend_mult": 3.0, "weekend_day": 2,
                      "min_trade_usd": 10.0, "max_leverage": 5.0},
        },
        "account_fees": {"fx_conversion_pct": 0.0050, "withdrawal_fee_usd": 5.0},
    },
}


def venue(name=None):
    v = VENUES.get(name or ACTIVE_VENUE)
    if v is None:
        raise KeyError("unknown venue: %r (known: %s)" % (name, ", ".join(sorted(VENUES))))
    return v


def active():
    return venue(ACTIVE_VENUE)


def set_active(name):
    global ACTIVE_VENUE
    if name not in VENUES:
        raise KeyError("unknown venue: %r" % (name,))
    ACTIVE_VENUE = name
    return active()


def classes(venue_name=None):
    return venue(venue_name)["classes"]


def account_fees(venue_name=None):
    return venue(venue_name).get("account_fees", {})


def eligibility(venue_name=None, jurisdiction=None):
    """Can the operator actually use this venue? The constraint that decides usability."""
    el = venue(venue_name).get("eligibility", {})
    return el.get(jurisdiction) if jurisdiction else el


def load_overrides(path=OVERRIDE_FILE):
    """Merge operator-supplied real rates over the modelled defaults.

    Shape: {"venues": {"robinhood": {"classes": {"crypto": {"spread_pct": 0.003}}}},
            "active": "robinhood", "verified": true}
    """
    global VERIFIED_AGAINST_LIVE_ACCOUNT, ACTIVE_VENUE
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return False
    for vname, vpatch in (data.get("venues") or {}).items():
        if vname not in VENUES:
            continue
        for cname, fields in (vpatch.get("classes") or {}).items():
            if cname in VENUES[vname]["classes"] and isinstance(fields, dict):
                VENUES[vname]["classes"][cname].update(fields)
        VENUES[vname].setdefault("account_fees", {}).update(vpatch.get("account_fees") or {})
    if data.get("active") in VENUES:
        ACTIVE_VENUE = data["active"]
    if data.get("verified") is True:
        VERIFIED_AGAINST_LIVE_ACCOUNT = True
    return True


def verification_notice(venue_name=None):
    v = venue(venue_name)
    if v.get("verified"):
        return "%s costs: VERIFIED (the schedule the live paper engine runs on)" % v["label"]
    if VERIFIED_AGAINST_LIVE_ACCOUNT:
        return "%s costs: OPERATOR-VERIFIED (venue_costs.json)" % v["label"]
    return ("%s costs: MODELLED — no broker account is connected and no authenticated "
            "call is made; indicative only" % v["label"])


# ---------------------------------------------------------------------------
# Cost maths
# ---------------------------------------------------------------------------
def _cfg(instrument_class, venue_name=None, schedule=None):
    if schedule is not None:
        return schedule
    cls = classes(venue_name)
    cfg = cls.get(instrument_class)
    if cfg is None:
        # Deliberately NO silent fallback. Pricing a stock at the crypto schedule
        # because the venue doesn't list stocks would quietly produce a wrong
        # number in a module whose entire job is producing right ones. If a venue
        # cannot trade an instrument class, that is a finding — surface it.
        raise KeyError(
            "venue %r has no schedule for instrument class %r (has: %s)"
            % (venue_name or ACTIVE_VENUE, instrument_class, ", ".join(sorted(cls))))
    return cfg


def is_cfd(side, leverage=1.0):
    return side == "short" or (leverage or 1.0) > 1.0


def weekend_charges(hold_days, weekend_day, weekend_mult):
    if weekend_day is None or hold_days <= 0:
        return 0.0
    return (hold_days / 7.0) * (weekend_mult - 1.0)


def entry_cost(instrument_class, notional, venue_name=None, schedule=None):
    cfg = _cfg(instrument_class, venue_name, schedule)
    return float(notional) * (cfg.get("spread_pct", 0.0) / 2.0
                              + cfg.get("commission_pct", 0.0))


def exit_cost(instrument_class, notional, venue_name=None, schedule=None):
    return entry_cost(instrument_class, notional, venue_name, schedule)


def cost_items(instrument_class, notional, venue_name=None, schedule=None):
    cfg = _cfg(instrument_class, venue_name, schedule)
    n = float(notional)
    return {"spread": n * cfg.get("spread_pct", 0.0) / 2.0,
            "commission": n * cfg.get("commission_pct", 0.0)}


def financing(instrument_class, notional, hold_days, side="long", leverage=1.0,
              venue_name=None, schedule=None):
    if not is_cfd(side, leverage):
        return 0.0
    cfg = _cfg(instrument_class, venue_name, schedule)
    days = max(0.0, float(hold_days))
    days += weekend_charges(days, cfg.get("weekend_day"), cfg.get("weekend_mult", 1.0))
    return float(notional) * (cfg.get("overnight_bps_day", 0.0) / 10000.0) * days


def min_trade_usd(instrument_class, venue_name=None):
    return _cfg(instrument_class, venue_name).get("min_trade_usd", 0.0)


def max_leverage(instrument_class, venue_name=None):
    return _cfg(instrument_class, venue_name).get("max_leverage", 1.0)


def round_trip(instrument_class, notional, hold_days=0.0, side="long",
               leverage=1.0, venue_name=None, schedule=None):
    cfg = _cfg(instrument_class, venue_name, schedule)
    notional = float(notional)
    hold_days = max(0.0, float(hold_days))
    spread = notional * cfg.get("spread_pct", 0.0)
    commission = notional * cfg.get("commission_pct", 0.0) * 2.0
    over = financing(instrument_class, notional, hold_days, side, leverage,
                     venue_name, schedule)
    total = spread + commission + over
    return {
        "venue": venue_name or ACTIVE_VENUE,
        "instrument_class": instrument_class, "side": side,
        "is_cfd": is_cfd(side, leverage),
        "notional": round(notional, 2), "hold_days": round(hold_days, 3),
        "spread": round(spread, 4), "commission": round(commission, 4),
        "overnight": round(over, 4), "total": round(total, 4),
        "pct_of_notional": round(total / notional * 100.0, 4) if notional else 0.0,
        "bps_of_notional": round(total / notional * 10000.0, 2) if notional else 0.0,
    }


def kraken_round_trip(notional, hold_days=0.0, side="long"):
    return round_trip("crypto", notional, hold_days, side, venue_name="kraken")


def breakeven_move_pct(instrument_class, hold_days=0.0, side="long", leverage=1.0,
                       venue_name=None):
    return round_trip(instrument_class, 10000.0, hold_days, side, leverage,
                      venue_name)["pct_of_notional"]


def compare(instrument_class="crypto", notional=4000.0, hold_days=7.0, side="long",
            venues=("kraken", "robinhood", "etoro")):
    out = {}
    for v in venues:
        try:
            out[v] = round_trip(instrument_class, notional, hold_days, side, venue_name=v)
        except KeyError:
            continue
    return out


def account_drag(deposit_amount, venue_name=None):
    af = account_fees(venue_name)
    conv = af.get("fx_conversion_pct", 0.0)
    total = deposit_amount * conv * 2 + af.get("withdrawal_fee_usd", 0.0)
    return {"conversion_each_way_pct": conv * 100, "total": round(total, 2),
            "pct_of_deposit": round(total / deposit_amount * 100, 3) if deposit_amount else 0.0}


load_overrides()


if __name__ == "__main__":
    a = active()
    print("ACTIVE VENUE: %s   [%s]" % (a["label"], a["status"]))
    print(verification_notice())
    print("  API: %s" % a.get("api"))
    for j, e in (a.get("eligibility") or {}).items():
        print("  %s: available=%s — %s" % (j, e.get("available"), e.get("note", "")))
    print()

    print("SAME TRADE, EVERY VENUE  ($4,000 crypto long, 7-day hold)")
    for v, r in compare().items():
        vv = VENUES[v]
        print("  %-22s %8.2f  (%6.1f bps)   %-9s api:%s"
              % (vv["label"], r["total"], r["bps_of_notional"], vv["status"],
                 "yes" if "NONE" not in vv["api"] else "NONE"))
    print()

    print("BREAK-EVEN HURDLE (gross %% move needed, crypto long, flat)")
    for v in ("kraken", "robinhood", "etoro"):
        print("  %-22s %7.3f%%" % (VENUES[v]["label"],
                                   breakeven_move_pct("crypto", 0, "long", venue_name=v)))
    print()

    print("ELIGIBILITY FOR THIS OPERATOR (AU + UK resident)")
    for v, vv in VENUES.items():
        for j in ("AU", "UK"):
            e = (vv.get("eligibility") or {}).get(j, {})
            print("  %-22s %-3s %s" % (vv["label"], j, e.get("available")))
