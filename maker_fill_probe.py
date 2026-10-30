#!/usr/bin/env python3
"""maker_fill_probe.py — LIVE maker fill-rate probe at scale. PAPER ONLY.

WHY
---
`maker_calibration_probe` posts ONE order a day on ONE pair. After a week it had
N=6 (3 fills, 3 misses). That is an anecdote, and it is the single number the
whole fee route depends on: at Kraken, 10 bps maker needs only $250k/30d, and the
fee tier is worth ~33 pp/yr on an identical signal -- larger than any edge found
across ~2,150 tested hypotheses.

This probe posts across MANY pairs x MANY offsets every tick, so a usable series
accumulates in days rather than years.

WHAT IT MEASURES — and the second one is the point
--------------------------------------------------
  1. FILL RATE per offset: did the market trade THROUGH our resting limit?
     A touch does NOT fill you -- you are at the back of the queue at that price.
  2. ADVERSE SELECTION: for every MISS, what did the price go on to do? If the
     orders we miss are the ones that run away, cheap fees are an illusion. This
     is the number that decides whether maker execution is safe for a MOMENTUM
     signal, and it cannot be inferred -- only measured.

NOTHING IS TRADED. No capital, no positions, no P&L. This is an observational
instrument, and it is deliberately kept separate from every book so it can never
affect one.
"""
import json, os, statistics, sys, time, urllib.request, warnings
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "reports", "maker_fill_probe.json")
OFFSETS_BPS = [0.0, 2.0, 5.0, 10.0, 25.0, 50.0]
N_PAIRS = 25
TTL_SEC = 3600.0            # a resting order lives one hour
FWD_SEC = 4 * 3600.0        # judge the outcome four hours after resolution
THROUGH = 1e-4              # must trade 1 bp through to fill
STABLE = ("USDC","USD1","FDUSD","TUSD","BUSD","DAI","USDP","EUR","AEUR","USDE",
          "XUSD","USDS","PYUSD","RLUSD","BFUSD")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get(u, tmo=25):
    r = urllib.request.Request(u, headers={"User-Agent": "tfo-desk/1.0"})
    with urllib.request.urlopen(r, timeout=tmo) as x:
        return json.loads(x.read().decode())


def universe(n=N_PAIRS):
    t = _get("https://api.binance.com/api/v3/ticker/24hr")
    rows = []
    for x in t:
        s = x["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        if any(b.startswith(k) or b == k for k in STABLE):
            continue
        try:
            rows.append((s, float(x["quoteVolume"]), float(x["lastPrice"])))
        except Exception:
            continue
    rows.sort(key=lambda r: -r[1])
    return rows[:n]


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"probe": "maker fill-rate at scale", "paper_only": True,
            "started_utc": _utc(), "note": "NOTHING IS TRADED. Observational only.",
            "through_bps": THROUGH * 10000, "ttl_sec": TTL_SEC,
            "open_orders": [], "resolved": []}


def save(d):
    tmp = STATE + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def tick(verbose=True):
    d = load()
    now = time.time()
    try:
        uni = universe()
    except Exception as e:
        print("   maker_fill_probe: universe unavailable (%s)" % type(e).__name__)
        return d
    px = {s: p for s, _, p in uni}

    # ---- resolve resting orders ------------------------------------------
    # CM-055: the low must come from real 1-MINUTE BARS over the resting window,
    # not from sampling the ticker once per tick. Tick-sampling sees one price an
    # hour and therefore never sees the intraday dip that fills a passive order —
    # it reported 57.0% at the 5bp offset where the bar-based study said 99.1%.
    # Biased LOW, the exact mirror of the earlier lab's touch-counting bias.
    # An instrument that is wrong in either direction is not a measurement.
    lows = {}
    for o in d["open_orders"]:
        if o["sym"] in lows:
            continue
        try:
            k = _get("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m"
                     "&startTime=%d&limit=1000"
                     % (o["sym"], int(o["posted_epoch"] * 1000)))
            lows[o["sym"]] = min(float(b[3]) for b in k) if k else None
        except Exception:
            lows[o["sym"]] = None
        time.sleep(0.05)
    still = []
    for o in d["open_orders"]:
        cur = px.get(o["sym"])
        if cur is None:
            still.append(o); continue
        true_low = lows.get(o["sym"])
        o["low_seen"] = min(x for x in (o.get("low_seen", cur), cur, true_low)
                            if x is not None)
        o["low_source"] = "1m bars" if true_low is not None else "tick sample (DEGRADED)"
        if o["low_seen"] < o["limit"] * (1 - THROUGH):
            o.update({"outcome": "FILL", "resolved_utc": _utc(),
                      "resolved_epoch": now, "px_at_resolve": cur})
            d["resolved"].append(o)
        elif now - o["posted_epoch"] >= TTL_SEC:
            o.update({"outcome": "MISS", "resolved_utc": _utc(),
                      "resolved_epoch": now, "px_at_resolve": cur,
                      "moved_pct": round(100 * (cur / o["ref"] - 1), 4)})
            d["resolved"].append(o)
        else:
            still.append(o)
    d["open_orders"] = still

    # ---- stamp the forward outcome on anything old enough ----------------
    for r in d["resolved"]:
        if "fwd_pct" in r or now - r.get("resolved_epoch", now) < FWD_SEC:
            continue
        cur = px.get(r["sym"])
        if cur is None:
            continue
        base = r["limit"] if r["outcome"] == "FILL" else r["px_at_resolve"]
        r["fwd_pct"] = round(100 * (cur / base - 1), 4)

    # ---- post a fresh grid ------------------------------------------------
    live = {(o["sym"], o["offset_bps"]) for o in d["open_orders"]}
    posted = 0
    for sym, _, p in uni:
        for off in OFFSETS_BPS:
            if (sym, off) in live or not p:
                continue
            d["open_orders"].append({
                "sym": sym, "offset_bps": off, "ref": p,
                "limit": p * (1 - off / 10000.0), "low_seen": p,
                "posted_utc": _utc(), "posted_epoch": now})
            posted += 1

    d["resolved"] = d["resolved"][-20000:]
    d["updated"] = _utc()
    d["epoch"] = now

    # ---- the summary that is the whole point ------------------------------
    stats = {}
    for off in OFFSETS_BPS:
        rs = [r for r in d["resolved"] if r["offset_bps"] == off]
        if not rs:
            continue
        fills = [r for r in rs if r["outcome"] == "FILL"]
        miss = [r for r in rs if r["outcome"] == "MISS"]
        ff = [r["fwd_pct"] for r in fills if "fwd_pct" in r]
        mf = [r["fwd_pct"] for r in miss if "fwd_pct" in r]
        stats["%.0fbp" % off] = {
            "n": len(rs), "fill_rate": round(len(fills) / len(rs), 4),
            "n_fwd_filled": len(ff), "n_fwd_missed": len(mf),
            "fwd_filled_pct": round(statistics.mean(ff), 4) if ff else None,
            "fwd_missed_pct": round(statistics.mean(mf), 4) if mf else None,
            "adverse_selection_pct": (round(statistics.mean(mf) - statistics.mean(ff), 4)
                                      if ff and mf else None),
            "thin": len(rs) < 100,
        }
    d["stats"] = stats
    save(d)
    if verbose:
        print("   maker_fill_probe: %d resting, %d resolved total, %d posted this tick"
              % (len(d["open_orders"]), len(d["resolved"]), posted))
        for k, v in sorted(stats.items(), key=lambda kv: float(kv[0][:-2])):
            print("      %-6s n=%-5d fill %5.1f%%  fwd|fill %8s  fwd|miss %8s  adverse %8s%s"
                  % (k, v["n"], 100 * v["fill_rate"],
                     ("%+.3f%%" % v["fwd_filled_pct"]) if v["fwd_filled_pct"] is not None else "-",
                     ("%+.3f%%" % v["fwd_missed_pct"]) if v["fwd_missed_pct"] is not None else "-",
                     ("%+.3f%%" % v["adverse_selection_pct"]) if v["adverse_selection_pct"] is not None else "-",
                     "  THIN" if v["thin"] else ""))
    return d


if __name__ == "__main__":
    tick(verbose="--quiet" not in sys.argv)
