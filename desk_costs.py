#!/usr/bin/env python3
"""desk_costs.py — ONE retail-realistic cost model for every paper fill (V1 and V2). PAPER ONLY.

Every paper fill on the desk calls `fill_fees(...)` and gets back fees in USD with a
line-by-line breakdown. The desk books in USD; non-USD venues also pay an FX
conversion leg. Nothing here trades, quotes or touches a ledger.

SCHEDULE (sources checked 2026-09-25; re-check each quarter)
-----------------------------------------------------------
CRYPTO (Kraken spot, taker — the desk takes liquidity: buys at ask, sells at bid)
  * 13 Oct 2026: Brett's real Kraken fees, taker 0.40% / maker 0.25% (liquidity="maker" for post-only fills).
  * Was: 0.26%/side (funder-specified desk tier; override with env DESK_KRAKEN_TAKER_PCT
    or KRAKEN_TIER).  Kraken's July-2026 schedule (https://www.kraken.com/features/fee-schedule,
    https://support.kraken.com/articles/cross-platform-fee-tier-changes) is Tier 1 0.80% taker
    ... Tier 7 ($250k 30d vol / $400k AoP) 0.22%, Tier 8 0.20%, Tier 9 ($1M / $1M AoP) 0.18%.
    A desk clipping $150k-$500k tickets sits at Tier 8-9, so 0.26% is CONSERVATIVE vs the
    likely real tier and far better than a brand-new retail account (0.80%).
US EQUITIES (IBKR Pro "Fixed"-like; https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)
  * $0.005/share, min $1.00, max 1% of trade value (fixed plan includes exchange fees).
  * SELLS also pay regulatory pass-throughs:
      SEC Section 31 fee $20.60 per $1M of sale proceeds from 2026-04-04
        (SEC Fee Rate Advisory FY2026, https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2).
      FINRA TAF $0.000195/share, max $9.79/trade (FINRA Schedule A, SR-FINRA-2024-019 fee table).
        FINRA SR-FINRA-2026-021 pauses TAF to $0.00 for trades 2026-10-01..2026-12-31; modelled
        date-aware (set DESK_TAF_IGNORE_PAUSE=1 to charge it anyway).
UK (.L)
  * Stamp duty / SDRT 0.5% on BUYS of UK shares (HMRC, https://www.gov.uk/guidance/stamp-duty-reserve-tax-guidance).
    Not modelled: AIM/ETF exemptions (we charge — conservative), PTM levy (~£1.50-£2 on >£10k: immaterial).
  * Commission 0.05% min GBP 3 (IBKR tiered-like UK schedule).
HONG KONG (.HK)  (HKEX "Securities Trading Fees", https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Fees/Securities-(Hong-Kong)/Trading/Transaction)
  * Stamp duty 0.10% both sides (rate since 2023-11-17; rounded up to HK$1 in reality — not rounded here).
  * SFC transaction levy 0.0027%, AFRC levy 0.00015%, HKEX trading fee 0.00565%, both sides.
  * Commission 0.05% min HK$18.
ASX (.AX)  commission 0.08% min A$6 (CommSec/IBKR-like retail online rate). No stamp duty in AU.
EUROPE / OTHER non-US listings  commission 0.05% min 3 units of local currency.
  * Financial-transaction taxes on BUYS where they exist (charged to all names on the venue —
    conservative, since FR/ES apply only above a market-cap threshold):
      France .PA 0.4% (from 2025), Spain .MC 0.2%, Italy .MI 0.1%, Ireland .IR 1% stamp.
FX  0.03% of notional per fill for any non-USD venue (desk books in USD).

API
---
  fill_fees(sym, side, qty, px_usd, px_native=None, ccy=None, when=None) -> dict
      sym     desk symbol: "AAPL", "BARC.L", "0700.HK", "LINK/USD", "XBT/USD", ...
      side    "buy" | "sell" (V1 rail actions HALF/FLAT/STOP on a long are SELLs; covering a
              short is a BUY — use side_for(action, position_side))
      qty     units/shares filled (abs)
      px_usd  fill price per unit in USD
      px_native / ccy  optional native price & currency, used to derive the FX for local minimums
  returns {"fees_usd": float, "breakdown": {name: usd}, "venue": str, "notional_usd": float,
           "bps": float, "schedule": SCHEDULE_ID}
All numbers are PAPER estimates. Rounding to cents is left to the caller's ledger.
"""
import datetime as dt
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
import os

SCHEDULE_ID = "desk_costs v2 2026-10-13 (Kraken taker 0.40% / maker 0.25%)"


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# Kraken July-2026 spot taker tiers (%), keyed by tier name. "desk" = Brett's REAL Kraken account fees (13 Oct 2026):
# taker 0.40%, maker 0.25% (was the funder-specified 0.26% taker). Going forward only; history restated separately
# (research/parallel_desk/fee_restate.py -> state/fee_restatement.json), never rewritten.
KRAKEN_TAKER_TIERS = {"tier1": 0.80, "tier2": 0.60, "tier3": 0.38, "tier4": 0.35, "tier5": 0.30, "tier6": 0.25,
                      "tier7": 0.22, "tier8": 0.20, "tier9": 0.18, "tier10": 0.15, "tier11": 0.12, "tier12": 0.10,
                      "desk": 0.40}
KRAKEN_TIER = os.environ.get("KRAKEN_TIER", "desk")
KRAKEN_TAKER_PCT = _envf("DESK_KRAKEN_TAKER_PCT", KRAKEN_TAKER_TIERS.get(KRAKEN_TIER, 0.40))
KRAKEN_MAKER_PCT = _envf("DESK_KRAKEN_MAKER_PCT", 0.25)   # post-only (resting) fills only

US_PER_SHARE, US_MIN, US_MAX_PCT = 0.005, 1.00, 0.01
SEC_FEE_PER_USD = 20.60 / 1e6                   # from 2026-04-04
SEC_FEE_EFFECTIVE = dt.date(2026, 4, 4)
TAF_PER_SHARE, TAF_MAX = 0.000195, 9.79
TAF_PAUSE = (dt.date(2026, 10, 1), dt.date(2026, 12, 31))
UK_STAMP, UK_COMM, UK_MIN_GBP = 0.005, 0.0005, 3.0
HK_STAMP, HK_SFC, HK_AFRC, HK_HKEX, HK_COMM, HK_MIN_HKD = 0.001, 0.000027, 0.0000015, 0.0000565, 0.0005, 18.0
AX_COMM, AX_MIN_AUD = 0.0008, 6.0
OTHER_COMM, OTHER_MIN_LOCAL = 0.0005, 3.0
FX_CONV = 0.0003
BUY_FTT = {"PA": ("france_ftt", 0.004), "MC": ("spain_ftt", 0.002), "MI": ("italy_ftt", 0.001), "IR": ("irish_stamp", 0.01)}

VENUE_CCY = {"L": "GBP", "IL": "GBP", "HK": "HKD", "AX": "AUD", "DE": "EUR", "F": "EUR", "PA": "EUR", "AS": "EUR",
             "BR": "EUR", "MC": "EUR", "MI": "EUR", "LS": "EUR", "HE": "EUR", "IR": "EUR", "VI": "EUR",
             "CO": "DKK", "ST": "SEK", "OL": "NOK", "SW": "CHF", "T": "JPY", "TO": "CAD", "V": "CAD",
             "KS": "KRW", "KQ": "KRW", "TW": "TWD", "SI": "SGD", "NZ": "NZD", "JO": "ZAR", "SA": "BRL",
             "MX": "MXN", "NS": "INR", "BO": "INR", "SS": "CNY", "SZ": "CNY", "TA": "ILS", "CL": "COP"}
# minor-unit quotes (yfinance convention): price in pence/cents of the major currency
MINOR = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}
# Fallback USD per 1 major unit, ONLY used for local-currency MINIMUM tickets when the fill carries
# no native price (approx. late-Sep-2026 levels; the minimum is a few dollars so precision is moot).
FALLBACK_USD_PER = {"GBP": 1.34, "HKD": 0.128, "AUD": 0.66, "EUR": 1.17, "DKK": 0.157, "SEK": 0.106, "NOK": 0.10,
                    "CHF": 1.25, "JPY": 0.0068, "CAD": 0.72, "KRW": 0.00072, "TWD": 0.033, "SGD": 0.78, "NZD": 0.59,
                    "ZAR": 0.057, "BRL": 0.19, "MXN": 0.054, "INR": 0.0114, "CNY": 0.14, "ILS": 0.27, "COP": 0.00025}


def venue_of(sym):
    s = (sym or "").strip().upper()
    if "/" in s or s.startswith("PF_"):
        return "CRYPTO"
    if s.endswith("-USD"):
        return "CRYPTO"
    if "." not in s:
        return "US"
    return s.rsplit(".", 1)[1]


def side_for(action, position_side="long"):
    """V1 rail action -> fill side. BUY opens a long; HALF/FLAT/STOP/SELL close it; shorts invert."""
    a = (action or "").upper()
    closing = a in ("HALF", "FLAT", "STOP", "SELL", "TRAIL", "TIME", "EXIT")
    if (position_side or "long") == "short":
        return "buy" if closing else "sell"
    return "sell" if closing else "buy"


def _usd_per_major(venue, px_usd, px_native, ccy):
    local = VENUE_CCY.get(venue)
    if ccy in MINOR:
        local, div = MINOR[ccy]
    else:
        div = 1.0
        if ccy and ccy != "USD":
            local = ccy
    try:
        if px_native and float(px_native) > 0 and px_usd and float(px_usd) > 0 and ccy and ccy != "USD":
            return local, float(px_usd) / float(px_native) * div
    except (TypeError, ValueError):
        pass
    return local, FALLBACK_USD_PER.get(local or "", 1.0)


def _pct_min(notional_usd, pct, min_local, usd_per_local):
    return max(notional_usd * pct, min_local * usd_per_local)


def fill_fees(sym, side, qty, px_usd, px_native=None, ccy=None, when=None, liquidity="taker"):
    """liquidity: "taker" (default: market / at-the-touch fills, stops) or "maker" (post-only resting fills). Crypto only."""
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy/sell, got %r" % side)
    qty = abs(float(qty or 0.0)); px_usd = float(px_usd or 0.0)
    notional = qty * px_usd
    when = when or dt.datetime.now(dt.timezone.utc)
    day = when.date() if isinstance(when, dt.datetime) else when
    v = venue_of(sym)
    b = {}
    if notional <= 0:
        return {"fees_usd": 0.0, "breakdown": {}, "venue": v, "notional_usd": 0.0, "bps": 0.0, "schedule": SCHEDULE_ID}
    if v == "CRYPTO":
        if (liquidity or "taker") == "maker":
            b["kraken_maker"] = notional * KRAKEN_MAKER_PCT / 100.0
        else:
            b["kraken_taker"] = notional * KRAKEN_TAKER_PCT / 100.0
    elif v == "US":
        comm = max(US_MIN, qty * US_PER_SHARE)
        b["commission"] = min(comm, US_MAX_PCT * notional)
        if side == "sell":
            if day >= SEC_FEE_EFFECTIVE:
                b["sec_fee"] = notional * SEC_FEE_PER_USD
            paused = TAF_PAUSE[0] <= day <= TAF_PAUSE[1] and os.environ.get("DESK_TAF_IGNORE_PAUSE") != "1"
            if not paused:
                b["finra_taf"] = min(TAF_MAX, qty * TAF_PER_SHARE)
    else:
        local, usd_per = _usd_per_major(v, px_usd, px_native, ccy)
        if v in ("L", "IL"):
            b["commission"] = _pct_min(notional, UK_COMM, UK_MIN_GBP, usd_per)
            if side == "buy" and v == "L":
                b["uk_stamp_sdrt"] = notional * UK_STAMP
        elif v == "HK":
            b["commission"] = _pct_min(notional, HK_COMM, HK_MIN_HKD, usd_per)
            b["hk_stamp"] = notional * HK_STAMP
            b["hk_sfc_levy"] = notional * HK_SFC
            b["hk_afrc_levy"] = notional * HK_AFRC
            b["hkex_trading_fee"] = notional * HK_HKEX
        elif v == "AX":
            b["commission"] = _pct_min(notional, AX_COMM, AX_MIN_AUD, usd_per)
        else:
            b["commission"] = _pct_min(notional, OTHER_COMM, OTHER_MIN_LOCAL, usd_per)
            if side == "buy" and v in BUY_FTT:
                name, rate = BUY_FTT[v]
                b[name] = notional * rate
        b["fx_conversion"] = notional * FX_CONV
    b = {k: round(x, 6) for k, x in b.items()}
    fees = round(sum(b.values()), 6)
    return {"fees_usd": fees, "breakdown": b, "venue": v, "notional_usd": round(notional, 6),
            "bps": round(1e4 * fees / notional, 4), "schedule": SCHEDULE_ID}


def schedule_table():
    rows = []
    for sym, px in (("XBT/USD", 60000.0), ("AAPL", 200.0), ("BARC.L", 3.0), ("0700.HK", 60.0), ("BHP.AX", 30.0), ("SAP.DE", 250.0), ("MC.PA", 700.0)):
        for n in (10000.0, 150000.0):
            q = n / px
            bu = fill_fees(sym, "buy", q, px, when=dt.date(2026, 9, 25)); se = fill_fees(sym, "sell", q, px, when=dt.date(2026, 9, 25))
            rows.append({"sym": sym, "notional_usd": n, "buy_usd": round(bu["fees_usd"], 2), "sell_usd": round(se["fees_usd"], 2),
                         "round_trip_bps": round(1e4 * (bu["fees_usd"] + se["fees_usd"]) / n, 2), "buy": bu["breakdown"], "sell": se["breakdown"]})
    return rows


if __name__ == "__main__":
    import json
    for r in schedule_table():
        print(json.dumps(r))
