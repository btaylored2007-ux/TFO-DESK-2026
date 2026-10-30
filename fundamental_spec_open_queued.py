#!/usr/bin/env python3
"""fundamental_spec_open_queued.py — open queued sleeve names IN SESSION. PAPER ONLY.

WHY THIS EXISTS
---------------
Three of the five sleeve candidates were found while their exchanges were shut.
The sealed rule is explicit: NO DEFERRED NAME MAY BE SIZED UNTIL RE-MEASURED IN
SESSION. Out-of-session quotes are not markets — this operation measured FOXA at
a 14.7% "spread" on US$442m of daily turnover, and POOL at 14.6% with a bid size
of 2. Sizing on those is sizing on a number that does not exist.

So this script does not trust the queue's stored figures. It runs at the venue's
open, re-fetches, and re-tests EVERY gate from scratch. The queue carries the
thesis and the fundamentals; the tradability decision is made fresh, on the day,
against a live book.

WHAT IT REFUSES
---------------
- A venue that is not actually open (`marketState != REGULAR`). Being scheduled
  for the open is not evidence the open happened: holidays exist.
- A crossed or absent quote. 9 of 15 ASX passers in one desk run came back
  CROSSED (bid > ask). That is worse than a missing quote and must never be
  arithmetic'd into a spread.
- Any hard-gate breach, re-measured. A name whose spread has widened past 4%
  since it was queued does not open, however good the catalyst.

It never widens a threshold to make a queued name fit. A name that cannot pass
today is left queued for tomorrow, and the reason is logged each time.
"""
import json
import os
import sys

import yfinance as yf

import fundamental_liquidity as lq
import fundamental_spec as sp
import fundamental_spec_book as sb

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "reports", "fundamental_spec_open_log.jsonl")
OPEN_STATES = ("REGULAR",)


def _log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def live_measure(sym, fx_usd):
    """Live quote, spread and ADV. Returns (measures, refusal_or_None).

    NEVER raises. A data-source failure must hold the name, not kill the run —
    this function is called from an unattended launchd job, and an exception here
    used to abort the whole queue including names whose venues were open.
    """
    try:
        i = yf.Ticker(sym).info or {}
    except Exception as exc:                          # noqa: BLE001
        # yfinance raises YFRateLimitError among others. Caught 2026-08-28 when a
        # rate limit crashed this script outright.
        return None, ("data source failed (%s: %s) — no measurement, holding"
                      % (type(exc).__name__, str(exc)[:90]))
    if not i:
        return None, "data source returned an EMPTY record — holding, not sizing"
    state = i.get("marketState")
    if state not in OPEN_STATES:
        return None, ("venue not in session (marketState=%s) — a scheduled run "
                      "is not evidence the market opened" % state)
    bid, ask = i.get("bid"), i.get("ask")
    px = i.get("currentPrice") or i.get("regularMarketPrice")
    if not px:
        return None, "no usable price"
    if not bid or not ask:
        return None, "no bid/ask — spread UNMEASURED, refusing to size"
    if ask <= bid:
        return None, ("CROSSED book (bid %.4f >= ask %.4f) — this is worse than "
                      "a missing quote and must not be turned into a spread"
                      % (bid, ask))
    spread = (ask - bid) / ((ask + bid) / 2.0)

    # STUB-QUOTE GUARD, added 2026-08-29 after ORGO and MREO were refused on
    # "spread exceeds the cap" during a REGULAR session:
    #     ORGO  bid 1.24 / ask 2.18 on a $1.71 stock, ADV US$1.05m -> 54.97%
    #     MREO  bid 0.1863 / ask 0.3403 on $0.2684              -> 58.49%
    # A 94-cent spread on a penny-tick Nasdaq stock is not a market, it is a stub
    # quote — the same shape a desk measured elsewhere as SIGI 58.4% on US$50m of
    # ADV and AMSF 57%. `fundamental_liquidity` already treats anything above
    # PLACEHOLDER_SPREAD_PCT as UNMEASURED; this opener computed its own spread
    # and did not, so it logged a MERIT refusal for an UNMEASURABLE quote and
    # would have repeated that forever.
    #
    # The outcome is unchanged — the name is still NOT sized, which is correct —
    # but the reason is now honest and the name stays eligible for a genuine
    # re-measurement instead of being written off on a number that never existed.
    if spread > lq.PLACEHOLDER_SPREAD_PCT:
        return None, ("quoted %.2f%% (bid %s / ask %s) during a %s session is a "
                      "STUB QUOTE, not a market — above the %.0f%% placeholder "
                      "threshold. Spread UNMEASURED, NOT a refusal on merit; "
                      "re-measure when the book is genuinely two-sided."
                      % (spread * 100, bid, ask, state,
                         lq.PLACEHOLDER_SPREAD_PCT * 100))

    adv = (i.get("averageDailyVolume10Day") or 0) * px * fx_usd
    return {"px": px, "bid": bid, "ask": ask, "spread_pct": spread,
            "adv_usd": adv, "state": state,
            "short_name": i.get("shortName")}, None


def process(dry_run=False):
    d = sb.load()
    queue = d.get("queued") or {}
    if not queue:
        print("queue empty")
        return
    opened, held = [], []
    # RATE-LIMIT / OUTAGE GUARD. A US micro-cap desk found yfinance silently
    # returning empty financials for 43 CONSECUTIVE tickers after a rate limit —
    # no exception, just blanks that read like "this company has no data". Here
    # the same shape would read as "every venue is shut" and hold everything,
    # which looks identical to a quiet, correct run. Count consecutive source
    # failures and say so loudly rather than reporting a clean nothing.
    source_failures = 0
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    for sym, q in list(queue.items()):
        _vr = v1_venues.entry_block_reason(sym)
        if _vr:  # before any FX/quote fetch; stays queued, never opened
            held.append((sym, _vr)); continue
        fx = None
        try:
            import fundamental_book as fb
            fx = fb.fx_to_usd(q["ccy"])
        except Exception as exc:                     # noqa: BLE001
            held.append((sym, "FX lookup failed: %s" % exc))
            continue
        if not fx:
            held.append((sym, "no FX rate for %s" % q["ccy"]))
            continue

        m, refusal = live_measure(sym, fx)
        if refusal:
            if "data source" in refusal:
                source_failures += 1
            held.append((sym, refusal))
            _log({"utc": sb.fb._utc(), "sym": sym, "action": "held",
                  "reason": refusal})
            continue
        source_failures = 0

        g = sp.screen(sym, adv_usd=m["adv_usd"], spread_pct=m["spread_pct"],
                      position_usd=q["notional_usd"],
                      cash_runway_months=q["runway_months"],
                      annual_dilution_pct=q["dilution_pct"],
                      has_dated_catalyst=True)
        if not g["ok"]:
            reason = "; ".join(g["reasons"])
            held.append((sym, reason))
            _log({"utc": sb.fb._utc(), "sym": sym, "action": "held",
                  "measured": m, "reason": reason})
            continue

        if dry_run:
            opened.append((sym, "WOULD OPEN — spread %.3f%% ADV US$%s"
                           % (m["spread_pct"] * 100,
                              "{:,.0f}".format(m["adv_usd"]))))
            continue

        try:
            p, cost = sb.open_position(
                sym=sym, ccy=q["ccy"], notional_usd=q["notional_usd"],
                thesis=q["thesis"], catalyst_date=q["catalyst_date"],
                runway_months=q["runway_months"],
                dilution_pct=q["dilution_pct"],
                spread_measured_pct=m["spread_pct"], adv_usd=m["adv_usd"])
        except SystemExit as exc:
            held.append((sym, str(exc)))
            _log({"utc": sb.fb._utc(), "sym": sym, "action": "refused",
                  "reason": str(exc)})
            continue

        # Re-load: open_position wrote the ledger, so the in-memory copy is stale.
        d = sb.load()
        d.get("queued", {}).pop(sym, None)
        sb.save(d)
        opened.append((sym, "%.1f units @ US$%.4f, spread %.3f%%, entry cost $%.2f"
                       % (p["units"], p["entry_px_usd"],
                          m["spread_pct"] * 100, cost)))
        _log({"utc": sb.fb._utc(), "sym": sym, "action": "opened",
              "measured": m, "units": p["units"],
              "entry_px_usd": p["entry_px_usd"]})

    for sym, note in opened:
        print("OPENED %-8s %s" % (sym, note))
    for sym, note in held:
        print("HELD   %-8s %s" % (sym, note[:150]))
    if source_failures >= 3:
        msg = ("DATA SOURCE DEGRADED: %d consecutive source failures. This run "
               "proves NOTHING about these names — do not read it as 'no venue "
               "was open'. Re-run when the source recovers." % source_failures)
        print("!! " + msg)
        _log({"utc": sb.fb._utc(), "action": "source_degraded",
              "consecutive_failures": source_failures, "reason": msg})
    if not dry_run and opened:
        sb.mark()


if __name__ == "__main__":
    import head_trader_gate as _htg
    if _htg.active() and "--dry-run" not in sys.argv:
        print("fundamental_spec_open_queued: Head Trader only flag set, engine will not open")
        sys.exit(0)
    process(dry_run="--dry-run" in sys.argv)
