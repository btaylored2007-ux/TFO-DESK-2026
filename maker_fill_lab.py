#!/usr/bin/env python3
"""maker_fill_lab.py — measure maker FILL RATE and ADVERSE SELECTION. PAPER ONLY.

WHY THIS EXISTS
---------------
Kraken charges 40 bps taker / 25 bps maker at base, and 10 bps maker at only
$250k/30-day volume. On an identical signal that fee difference is worth ~33
percentage points a year -- larger than any edge found across ~2,150 tested
hypotheses. So the desk should post passively rather than cross the spread.

But a maker order that does not fill is NOT free: the trade you wanted did not
happen. The entire question brackets between two answers miles apart --

    misses RANDOM   -> the fee saving survives whole  (+2.1% mean at 90% fill)
    misses ADVERSE  -> destroyed even at 90% fill     (-1.5% mean)

and `maker_calibration_probe` has posted SIX orders in seven days. That is an
anecdote, not an estimate, and it is the number the whole route depends on.

WHAT THIS MEASURES
------------------
For many pairs x many price offsets from touch, using 1-minute bars:
  1. FILL RATE       -- did a passive buy at (close x (1-offset)) get hit inside
                        the horizon? A bar's LOW reaching the limit is the fill.
  2. ADVERSE SELECTION -- and this is the point. For FILLED and UNFILLED orders
                        separately, what did the price do afterwards? If the
                        orders you miss are the ones that run away, the fee
                        saving is an illusion. This is measured, not assumed.
  3. NET ADVANTAGE   -- fee saved (taker - maker) minus the adverse-selection
                        cost, per offset. The offset that maximises it is the
                        answer to "how far inside the touch should we post".

NO CAPITAL IS AT RISK. Nothing is traded. This is an observational study of
public 1-minute bars.
"""
import json, os, statistics, sys, time, urllib.request, warnings, math
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "reports", "maker_fill_bars.json")
OUT = os.path.join(BASE, "reports", "maker_fill_lab.json")

TAKER_BPS = 40.0            # measured Kraken base
MAKER_BPS = 25.0            # measured Kraken base
MAKER_BPS_TIER = 10.0       # $250k/30d tier
OFFSETS_BPS = [0, 2, 5, 10, 15, 25, 40, 60]   # how far BELOW touch we post a buy
HORIZONS_MIN = [15, 60, 240]                  # how long the order rests
FWD_MIN = 240                                 # how far ahead we judge the outcome
THROUGH_BPS = 1.0                             # must trade 1bp THROUGH to fill us


def _get(u, tmo=25):
    r = urllib.request.Request(u, headers={"User-Agent": "tfo-research/1.0"})
    with urllib.request.urlopen(r, timeout=tmo) as x:
        return json.loads(x.read().decode())


def universe(n=15):
    t = _get("https://api.binance.com/api/v3/ticker/24hr")
    STABLE = ("USDC", "USD1", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR",
              "AEUR", "USDE", "XUSD", "USDS", "PYUSD", "RLUSD", "BFUSD")
    rows = []
    for x in t:
        s = x["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        if any(b.startswith(k) or b == k for k in STABLE):
            continue
        try:
            rows.append((s, float(x["quoteVolume"])))
        except Exception:
            continue
    rows.sort(key=lambda r: -r[1])
    return [s for s, _ in rows[:n]]


def bars_1m(sym, days=7):
    """Paginated 1-minute bars. 1000 bars per call = ~16.7h."""
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out, cur = [], start
    while cur < end:
        k = _get("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m"
                 "&startTime=%d&limit=1000" % (sym, cur))
        if not k:
            break
        out.extend([{"t": int(b[0]), "o": float(b[1]), "h": float(b[2]),
                     "l": float(b[3]), "c": float(b[4])} for b in k])
        nxt = int(k[-1][0]) + 60_000
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.12)
    return out


def study(series):
    """Returns rows: one per (offset, horizon) with fill rate + adverse selection."""
    rows = []
    for off in OFFSETS_BPS:
        for hz in HORIZONS_MIN:
            filled_fwd, missed_fwd, n_fill, n_tot = [], [], 0, 0
            for sym, b in series.items():
                n = len(b)
                step = 30                      # a candidate order every 30 min
                for i in range(60, n - hz - FWD_MIN, step):
                    ref = b[i]["c"]
                    limit = ref * (1 - off / 10000.0)
                    window = b[i + 1: i + 1 + hz]
                    if not window:
                        continue
                    n_tot += 1
                    # STRICT-THROUGH, not touch. A bar whose LOW merely equals
                    # our limit does NOT fill us -- we are at the back of the
                    # queue at that price. A real passive fill requires the
                    # market to trade THROUGH the limit. Counting touches is why
                    # the first run reported 98.6% against the live probe's 50%.
                    hit = any(x["l"] < limit * (1 - THROUGH_BPS / 10000.0)
                              for x in window)
                    fwd_i = i + hz + FWD_MIN
                    if fwd_i >= n:
                        continue
                    fwd_px = b[fwd_i]["c"]
                    if hit:
                        n_fill += 1
                        filled_fwd.append(fwd_px / limit - 1.0)
                    else:
                        # the opportunity we did NOT get: we would have had to pay
                        # the market. Measure from the price at expiry.
                        exp_px = b[i + hz]["c"]
                        missed_fwd.append(fwd_px / exp_px - 1.0)
            if not n_tot:
                continue
            fr = n_fill / n_tot
            mf = statistics.mean(filled_fwd) if filled_fwd else 0.0
            mm = statistics.mean(missed_fwd) if missed_fwd else 0.0
            # Adverse selection: how much better were the trades we MISSED?
            adverse = mm - mf
            fee_saved_bps = TAKER_BPS - MAKER_BPS_TIER
            # cost of adverse selection, weighted by how often we miss
            adverse_cost_bps = (1 - fr) * adverse * 10000.0
            n_missed = len(missed_fwd)
            rows.append({
                "offset_bps": off, "horizon_min": hz, "n": n_tot,
                "n_missed": n_missed,
                "missed_sample_thin": n_missed < 100,
                "fill_rate": round(fr, 4),
                "fwd_ret_filled_bps": round(10000 * mf, 2),
                "fwd_ret_missed_bps": round(10000 * mm, 2),
                "adverse_selection_bps": round(10000 * adverse, 2),
                "fee_saved_bps": fee_saved_bps,
                "adverse_cost_bps": round(adverse_cost_bps, 2),
                "net_advantage_bps": round(fee_saved_bps - adverse_cost_bps, 2),
            })
    return rows


if __name__ == "__main__":
    days = 7
    if os.path.exists(CACHE) and "--refresh" not in sys.argv:
        series = json.load(open(CACHE))
        print("loaded %d symbols from cache" % len(series))
    else:
        syms = universe()
        print("fetching %d-day 1-minute bars for %d pairs..." % (days, len(syms)))
        series = {}
        for i, s in enumerate(syms, 1):
            try:
                b = bars_1m(s, days)
                if len(b) > 5000:
                    series[s] = b
                print("   %2d/%d %-12s %d bars" % (i, len(syms), s, len(b)), flush=True)
            except Exception as e:
                print("   %2d/%d %-12s FAILED %s" % (i, len(syms), s, type(e).__name__), flush=True)
        json.dump(series, open(CACHE, "w"))
    tot = sum(len(v) for v in series.values())
    print("universe: %d pairs, %s one-minute bars" % (len(series), "{:,}".format(tot)))
    rows = study(series)
    json.dump({"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "taker_bps": TAKER_BPS, "maker_bps_base": MAKER_BPS,
               "maker_bps_tier": MAKER_BPS_TIER,
               "pairs": sorted(series), "bars": tot, "rows": rows},
              open(OUT, "w"), indent=1)
    print("wrote %s (%d cells)" % (OUT, len(rows)))
