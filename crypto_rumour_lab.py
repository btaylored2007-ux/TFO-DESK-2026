#!/usr/bin/env python3
"""crypto_rumour_lab.py — test "buy the rumour, sell before the news". PAPER ONLY.

WHAT THE FUNDER ASKED FOR AND WHAT IS ACTUALLY IMPLEMENTABLE
------------------------------------------------------------
Asked: speculative crypto sleeve, buy on rumour, sell BEFORE the news, low-vol up
to top 50, lowest-fee venue.

There is no rumour feed. DefiLlama's unlock/emissions endpoint now returns
HTTP 402 Payment Required and CoinGecko's events endpoint 404s. Buying an actual
rumour is not implementable and, more importantly, not TESTABLE.

What IS both: rumours leave a FOOTPRINT in the tape before the news prints —
abnormal volume with price drift while the news is still private, then a volume
CLIMAX as it becomes public. So:

    "buy the rumour"      -> enter on abnormal volume + early drift
    "sell before the news"-> exit INTO the volume climax, not after it

That is the same trade expressed in data we can observe ex-ante, and it can be
falsified. If it does not survive costs, it does not survive.

COSTS ARE THE WHOLE QUESTION (measured live, not assumed)
---------------------------------------------------------
Binance  10 bps taker (VIP0)            <- lowest
Kraken   40 bps taker / 25 maker        <- the desk's actual venue, measured live
Coinbase 60 bps taker / 40 maker
Every result is reported at BOTH 10 and 40 bps. Assuming a fee tier we do not
have is exactly the CM-042 error and is not repeated here.
"""
import json, os, statistics, sys, time, urllib.request, warnings
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "reports", "crypto_rumour_bars.json")
OUT = os.path.join(BASE, "reports", "crypto_rumour_lab.json")
STABLE = ("USDC", "USD1", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "AEUR",
          "USDE", "XUSD", "USDS", "PYUSD", "RLUSD", "BFUSD")


def _get(url, tmo=25):
    req = urllib.request.Request(url, headers={"User-Agent": "tfo-research/1.0"})
    with urllib.request.urlopen(req, timeout=tmo) as r:
        return json.loads(r.read().decode())


def universe(n_top=40, n_low=80):
    t = _get("https://api.binance.com/api/v3/ticker/24hr")
    rows = []
    for x in t:
        s = x["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if any(base.startswith(st) or base == st for st in STABLE):
            continue
        if base.endswith("UP") or base.endswith("DOWN"):
            continue
        try:
            qv = float(x["quoteVolume"])
        except Exception:
            continue
        rows.append((s, qv))
    rows.sort(key=lambda r: -r[1])
    top = [s for s, _ in rows[:n_top]]
    low = [s for s, v in rows if 100_000 <= v < 2_000_000][:n_low]
    return top, low


def bars(sym, limit=1000):
    k = _get("https://api.binance.com/api/v3/klines?symbol=%s&interval=1d&limit=%d"
             % (sym, limit))
    return [{"t": int(b[0]), "o": float(b[1]), "h": float(b[2]), "l": float(b[3]),
             "c": float(b[4]), "v": float(b[7])} for b in k]      # v = QUOTE volume


def fetch_all(syms, sleep=0.12):
    out = {}
    for i, s in enumerate(syms, 1):
        try:
            b = bars(s)
            if len(b) >= 260:
                out[s] = b
        except Exception:
            pass
        if i % 20 == 0:
            print("   ...fetched %d/%d" % (i, len(syms)), flush=True)
        time.sleep(sleep)
    return out


def backtest(series, vol_mult, drift_days, drift_min, climax_mult, max_hold,
             stop_pct, fee_bps, lookback=20):
    """One parameter set. Returns list of trade returns NET of fees."""
    fee = fee_bps / 10000.0
    trades = []
    for sym, b in series.items():
        n = len(b)
        i = lookback + drift_days + 1
        while i < n - 1:
            win = [b[j]["v"] for j in range(i - lookback, i)]
            med = statistics.median(win) if win else 0.0
            if med <= 0:
                i += 1; continue
            vr = b[i]["v"] / med
            drift = b[i]["c"] / b[i - drift_days]["c"] - 1.0
            # "the rumour": abnormal volume WITH early drift, but NOT already a climax
            if vr >= vol_mult and drift >= drift_min and vr < climax_mult:
                entry = b[i + 1]["o"]                    # next-bar open, no lookahead
                exit_px, held, why = None, 0, "max_hold"
                for k in range(i + 1, min(i + 1 + max_hold, n)):
                    held = k - i
                    wk = [b[j]["v"] for j in range(k - lookback, k)]
                    mk = statistics.median(wk) if wk else 0.0
                    # hard stop first
                    if b[k]["l"] <= entry * (1 - stop_pct):
                        exit_px, why = entry * (1 - stop_pct), "stop"; break
                    # "sell BEFORE the news": exit INTO the volume climax
                    if mk > 0 and b[k]["v"] / mk >= climax_mult:
                        exit_px, why = b[k]["c"], "climax"; break
                if exit_px is None:
                    exit_px = b[min(i + max_hold, n - 1)]["c"]
                gross = exit_px / entry - 1.0
                trades.append({"sym": sym, "ret": gross - 2 * fee, "why": why,
                               "held": held})
                i += held + 1
            else:
                i += 1
    return trades


def summarise(tr):
    if not tr:
        return {"n": 0}
    r = [t["ret"] for t in tr]
    mean = sum(r) / len(r)
    sd = statistics.pstdev(r) if len(r) > 1 else 0.0
    wins = sum(1 for x in r if x > 0)
    return {"n": len(r), "mean_pct": round(100 * mean, 3),
            "median_pct": round(100 * statistics.median(r), 3),
            "win_rate": round(100 * wins / len(r), 1),
            "sd_pct": round(100 * sd, 3),
            "t_stat": round(mean / (sd / (len(r) ** 0.5)), 2) if sd > 0 else None,
            "total_pct": round(100 * sum(r), 1),
            "avg_held_d": round(sum(t["held"] for t in tr) / len(tr), 1),
            "exit_mix": {w: sum(1 for t in tr if t["why"] == w)
                         for w in ("climax", "stop", "max_hold")}}


if __name__ == "__main__":
    print("building universe...")
    top, low = universe()
    print("  top-50 band: %d   low-vol band: %d" % (len(top), len(low)))
    syms = top + low
    if os.path.exists(CACHE) and "--refresh" not in sys.argv:
        series = json.load(open(CACHE))
        print("  loaded %d symbols from cache" % len(series))
    else:
        print("fetching daily bars (quote volume)...")
        series = fetch_all(syms)
        json.dump(series, open(CACHE, "w"))
        print("  cached %d symbols" % len(series))
    json.dump({"top": top, "low": low}, open(os.path.join(BASE, "reports",
              "crypto_rumour_universe.json"), "w"), indent=1)
    print("  usable series: %d" % len(series))
