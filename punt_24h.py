#!/usr/bin/env python3
"""THE 24-HOUR PUNT — $10,000 paper, PUMP/USD, 20:1 LEVERED, 24 hours, no stop.

Rule frozen in PUNT_24H_PREREGISTRATION.md (spec 7cdee16eebb663aa, leverage
amendment f0a76749ea8aac08), which declares IN ADVANCE that the result proves
nothing either way. Quarantined from the tournament board; excluded from the
firm's hypothesis count, because it tests no hypothesis.

PAPER ONLY. Public Kraken endpoints, no credentials, no real orders.

Risk is unconstrained by funder instruction. MEASUREMENT IS NOT:
  * REAL LIQUIDATION  - equity can never go below $0 and never recovers after a
    breach. Modelled on Kraken's margin-level rule (liquidate at 40% of used
    margin), which triggers EARLIER than the naive 1/L move.
  * REAL BORROW COST  - accrued on the borrowed notional per elapsed hour.
  * INTRADAY CHECKING - the heartbeat is hourly; a wipeout at minute 20 must not
    be missed, so every tick scans the intraday LOW of each bar since entry.

Usage: python3 punt_24h.py [--status]
"""
import argparse, json, os, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "punt_24h.json")
# RETIRED 2026-09-19: do not recreate ledger if archived
if not os.path.exists(BOOK):
    raise SystemExit(0)
SYM, PAIR = "PUMP/USD", "PUMPUSD"
START, COST_BPS, HOURS = 10000.0, 30.0, 24.0
LEVERAGE = 20.0
MAINT_MARGIN_FRAC = 0.40      # Kraken margin-level liquidation at 40% of used margin
BORROW_BPS_PER_4H = 2.0       # 0.02%/4h on borrowed notional (assumption; works against us)


def _get(url):
    import kraken_dns; kraken_dns.install()
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def price():
    res = _get("https://api.kraken.com/0/public/Ticker?pair=" + PAIR).get("result", {})
    for _, v in res.items():
        return float(v["c"][0])
    raise RuntimeError("no price for " + PAIR)


def lows_since(epoch):
    """Intraday LOWS of 1-min bars since `epoch` — so a wipeout between hourly
    ticks is caught. Returns (min_low, n_bars). Empty on failure (never fakes)."""
    res = _get("https://api.kraken.com/0/public/OHLC?pair=%s&interval=1&since=%d"
               % (PAIR, int(epoch))).get("result", {})
    lows = []
    for k, v in res.items():
        if k == "last":
            continue
        for row in v:
            if int(row[0]) >= epoch:
                lows.append(float(row[3]))
    return (min(lows), len(lows)) if lows else (None, 0)


def load():
    try:
        with open(BOOK) as f:
            return json.load(f)
    except Exception:
        return {"start": START, "cash": START, "units": 0.0, "entry": None,
                "opened_utc": None, "closed_utc": None, "events": [],
                "costs": 0.0, "spec": "7cdee16eebb663aa"}


def save(b):
    b["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
    os.replace(tmp, BOOK)


def _equity(b, px):
    """Mark-to-market equity net of borrow. Never below zero once liquidated."""
    if b.get("liquidated_utc"):
        return 0.0
    return b["units"] * px - b.get("borrowed", 0.0) - b.get("borrow_accrued", 0.0)


def tick(verbose=True):
    b = load()
    try:
        px = price()
    except Exception as e:
        b["error"] = "price fetch failed: %s: %s" % (type(e).__name__, str(e)[:120])
        save(b); print("PUNT ERROR: %s" % b["error"]); return b
    b.pop("error", None)
    now = time.time()

    # ---- LEVER UP (one-time amendment of the live unlevered position) ----
    if b["units"] > 0 and not b.get("levered_utc") and not b.get("closed_utc"):
        eq = b["units"] * px                       # unlevered: no borrow yet
        target_notional = eq * LEVERAGE
        add_notional = target_notional - eq
        cost = add_notional * COST_BPS / 10000.0
        eq_after = eq - cost
        target_notional = eq_after * LEVERAGE      # re-target on post-cost equity
        b["units"] = target_notional / px
        b["borrowed"] = target_notional - eq_after
        b["borrow_accrued"] = 0.0
        b["used_margin"] = eq_after
        b["costs"] = round(b.get("costs", 0.0) + cost, 2)
        b["leverage"] = LEVERAGE
        b["levered_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        b["levered_epoch"] = now
        # liquidation price: units*p - borrowed - accrued <= MAINT * used_margin
        b["liq_price"] = ((MAINT_MARGIN_FRAC * b["used_margin"]) + b["borrowed"]) / b["units"]
        b["events"].append({"utc": b["levered_utc"], "side": "lever",
                            "sym": SYM, "px": px, "to_leverage": LEVERAGE,
                            "notional": target_notional, "borrowed": b["borrowed"],
                            "cost": round(cost, 2), "liq_price": b["liq_price"]})
        print("PUNT LEVERED %.0f:1 @ %.8f | notional $%.2f | borrowed $%.2f | "
              "LIQ PRICE %.8f (%.2f%% away) | cost $%.2f"
              % (LEVERAGE, px, target_notional, b["borrowed"], b["liq_price"],
                 (b["liq_price"] / px - 1) * 100, cost))

    # ---- borrow accrual ----
    if b["units"] > 0 and b.get("levered_epoch") and not b.get("liquidated_utc"):
        hrs = (now - b.get("_last_accrual", b["levered_epoch"])) / 3600.0
        if hrs > 0:
            b["borrow_accrued"] = round(b.get("borrow_accrued", 0.0)
                                        + b["borrowed"] * (BORROW_BPS_PER_4H / 10000.0)
                                        * (hrs / 4.0), 4)
        b["_last_accrual"] = now

    # ---- INTRADAY LIQUIDATION CHECK (the honesty requirement) ----
    if b["units"] > 0 and not b.get("liquidated_utc") and b.get("levered_epoch"):
        low, nbars = lows_since(b["levered_epoch"])
        b["intraday_low_seen"] = low
        b["intraday_bars_checked"] = nbars
        if low is not None and low <= b["liq_price"]:
            b["liquidated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            b["liquidated_at_price"] = low
            b["units"] = 0.0
            b["cash"] = 0.0
            b["closed_utc"] = b["liquidated_utc"]
            b["events"].append({"utc": b["liquidated_utc"], "side": "LIQUIDATED",
                                "sym": SYM, "px": low, "reason":
                                "intraday low %.8f <= liq price %.8f"
                                % (low, b["liq_price"])})
            print("*** PUNT LIQUIDATED *** intraday low %.8f hit liq price %.8f "
                  "-> equity $0.00" % (low, b["liq_price"]))

    # ---- 24h deadline close ----
    if b["units"] > 0 and not b.get("closed_utc") \
            and now - b.get("opened_epoch", now) >= HOURS * 3600:
        gross = b["units"] * px
        cost = gross * COST_BPS / 10000.0
        b["cash"] = max(0.0, gross - cost - b["borrowed"] - b.get("borrow_accrued", 0.0))
        b["costs"] = round(b["costs"] + cost, 2)
        b["events"].append({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "side": "sell", "sym": SYM, "px": px, "gross": gross,
                            "cost": round(cost, 2), "reason": "24h_deadline"})
        b["units"] = 0.0
        b["closed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print("PUNT CLOSED at 24h: %s @ %.8f -> $%.2f" % (SYM, px, b["cash"]))

    b["mark"] = px
    b["equity"] = round(b["cash"] if b.get("closed_utc") else _equity(b, px), 2)
    b["pnl"] = round(b["equity"] - START, 2)
    b["pnl_pct"] = round((b["equity"] / START - 1) * 100, 3)
    if b.get("liq_price") and b["units"]:
        b["pct_to_liquidation"] = round((b["liq_price"] / px - 1) * 100, 2)
    if b.get("opened_epoch") and not b.get("closed_utc"):
        b["hours_left"] = round(HOURS - (now - b["opened_epoch"]) / 3600.0, 2)
    save(b)
    if verbose:
        status(b)
    return b


def status(b=None):
    b = b or load()
    st = ("LIQUIDATED" if b.get("liquidated_utc") else
          "CLOSED" if b.get("closed_utc") else "OPEN" if b["units"] else "unopened")
    print("THE 24-HOUR PUNT  [%s]  %.0f:1 LEVERED  PAPER ONLY — proves nothing either way"
          % (st, b.get("leverage") or 1))
    print("  equity  $%.2f   P&L $%+.2f (%+.2f%%)   start $%.0f"
          % (b.get("equity", START), b.get("pnl", 0), b.get("pnl_pct", 0), START))
    if b["units"]:
        print("  notional $%.2f  borrowed $%.2f  borrow accrued $%.2f"
              % (b["units"] * (b.get("mark") or 0), b.get("borrowed", 0),
                 b.get("borrow_accrued", 0)))
        print("  mark %.8f | LIQ PRICE %.8f | %.2f%% away  <-- the number that matters"
              % (b.get("mark") or 0, b.get("liq_price") or 0,
                 b.get("pct_to_liquidation") or 0))
        print("  intraday lows checked: %s bars, lowest %s"
              % (b.get("intraday_bars_checked"), b.get("intraday_low_seen")))
        print("  hours left: %s" % b.get("hours_left"))
    if b.get("liquidated_utc"):
        print("  LIQUIDATED %s at %.8f — equity $0.00, unrecoverable"
              % (b["liquidated_utc"], b.get("liquidated_at_price") or 0))
    print("  costs paid $%.2f   spec %s" % (b.get("costs", 0), b.get("spec")))
    if b.get("error"):
        print("  ERROR: %s" % b["error"])


if __name__ == "__main__":
    if not os.path.exists(BOOK):
        raise SystemExit(0)  # RETIRED 2026-09-19 — do not resurrect
    ap = argparse.ArgumentParser(); ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    status() if a.status else tick()
