#!/usr/bin/env python3
"""Maker-fill calibration probe — PAPER ONLY, risk-tool calibration, NOT an edge.

Forward-tests the EXECUTION assumptions behind swing_follow_daily Y1%/Z2% on
BTC (reports/research_btc_swing.md section 7). The strategy's edge claim
already FAILED at N = 1,486 (DSR 0.000, loses to holding, dies at taker fees);
this probe measures only:
  (a) realized maker fill rate vs the backtest's 100% fill assumption,
  (b) live drawdown-control delta vs a held-BTC baseline,
  (c) slippage-vs-assumption on filled orders.

Frozen mechanics: reports/MAKER_CALIBRATION_PREREGISTRATION.md (2026-08-22).
$10k paper book. Signal on SETTLED daily UTC bars only (Kraken public OHLC,
in-progress bar dropped — exit_axis.py convention). Simulated POST-ONLY limits
at the signal price P; an order fills only when the public WS tape
(reports/ws_tape.sqlite, ws_tape_collector.py) prints a trade STRICTLY THROUGH
P (buy: < P, sell: > P). Fill books at P, maker fee 8 bps/side. Un-filled by
the next settled bar -> MISS. No credentials, no accounts, no real orders.

Failures LOUD: any schema miss raises after printing an ERROR line and
persisting reports/maker_calibration_last_error.json. Never empty success.

Usage:
    python3 maker_calibration_probe.py            # one hourly tick
    python3 maker_calibration_probe.py --status   # honest status print
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE, "reports", "maker_calibration.json")
LAST_ERROR_PATH = os.path.join(BASE, "reports", "maker_calibration_last_error.json")
TAPE_DB = os.path.join(BASE, "reports", "ws_tape.sqlite")
TAPE_PAIR = "BTC/USD"                    # WS v2 symbol
REST_PAIR = "XBTUSD"                     # REST name for the same market
OHLC_URL = "https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440" % REST_PAIR

BOOK_USD = 10000.0
Y = 0.01                                 # entry threshold (frozen)
Z = 0.02                                 # trailing stop (frozen)
LOOKBACK = 5                             # rolling-extreme bars (frozen)
MAKER_FEE = 0.0008                       # 8 bps per side (frozen)
MAX_EVENTS = 500


class SchemaDriftError(Exception):
    """Public endpoint or tape answered in a shape this probe cannot trust."""


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def loud_fail(kind, detail):
    line = "ERROR maker_calibration_probe %s: %s" % (kind, detail)
    print(line, flush=True)
    try:
        with open(LAST_ERROR_PATH, "w") as f:
            json.dump({"utc_ts": iso(utc_now()), "kind": kind,
                       "detail": str(detail)[:2000]}, f, indent=2)
    except OSError as exc:
        print("ERROR maker_calibration_probe: cannot write error marker: %s"
              % exc, flush=True)


def fetch_settled_daily():
    """Settled daily UTC bars from Kraken public OHLC.
    Returns list of dicts {date, open, high, low, close}, oldest first, with
    the ALWAYS-in-progress last bar dropped. Schema miss raises, loudly."""
    req = urllib.request.Request(OHLC_URL, headers={"User-Agent": "tfo-paper-desk"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if not isinstance(payload, dict) or "result" not in payload:
        raise SchemaDriftError("OHLC response missing 'result': %s"
                               % repr(payload)[:300])
    if payload.get("error"):
        raise SchemaDriftError("OHLC endpoint error: %r" % (payload["error"],))
    result = payload["result"]
    keys = [k for k in result if k != "last"]
    if len(keys) != 1:
        raise SchemaDriftError("OHLC result keys unexpected: %r" % (list(result),))
    raw = result[keys[0]]
    if not isinstance(raw, list) or len(raw) < LOOKBACK + 3:
        raise SchemaDriftError("OHLC bars missing/too few: %d" % len(raw or []))
    bars = []
    for b in raw:
        if not isinstance(b, (list, tuple)) or len(b) < 5:
            raise SchemaDriftError("OHLC bar malformed: %r" % (b,))
        bars.append({"date": datetime.fromtimestamp(float(b[0]), timezone.utc)
                             .strftime("%Y-%m-%d"),
                     "open": float(b[1]), "high": float(b[2]),
                     "low": float(b[3]), "close": float(b[4])})
    return bars[:-1]                     # drop the in-progress UTC day, always


def tape_first_through(side, price, t_from, t_to):
    """First tape print strictly through the limit inside (t_from, t_to].
    Returns (utc_ts, print_price) or None. Also returns tape row count in the
    window via second element so callers can tell MISS from data gap."""
    if not os.path.exists(TAPE_DB):
        return None, 0
    conn = sqlite3.connect("file:%s?mode=ro" % TAPE_DB, uri=True, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        n = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE pair=? AND utc_ts>? AND utc_ts<=?",
            (TAPE_PAIR, t_from, t_to)).fetchone()[0]
        cmp_op = "<" if side == "buy" else ">"
        row = conn.execute(
            "SELECT utc_ts, price FROM trades WHERE pair=? AND utc_ts>? AND "
            "utc_ts<=? AND price %s ? ORDER BY utc_ts LIMIT 1" % cmp_op,
            (TAPE_PAIR, t_from, t_to, price)).fetchone()
        return (row if row else None), n
    finally:
        conn.close()


def next_utc_midnight(dt):
    d = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d


def swing_edges(closes):
    """Fresh-edge signal on the LATEST settled bar, exactly per btc_swing_lab
    run_swing: rolling LOOKBACK-bar close min/max shifted one bar; edge only
    where the previous bar was not already signalling. Returns +1/-1/0."""
    def sig_at(t):
        if t < LOOKBACK:
            return (False, False)
        window = closes[t - LOOKBACK:t]          # shift(1): excludes bar t
        up = closes[t] >= min(window) * (1.0 + Y)
        dn = closes[t] <= max(window) * (1.0 - Y)
        return (up, dn)
    t = len(closes) - 1
    if t < LOOKBACK + 1:
        return 0
    up_now, dn_now = sig_at(t)
    up_prev, dn_prev = sig_at(t - 1)
    if up_now and not up_prev:
        return 1
    if dn_now and not dn_prev:
        return -1
    return 0


def exit_wanted(bars_since_entry, entry_px, direction):
    """Walk settled bars since entry per run_swing bar order (stop checked with
    the PRIOR best, then best updates). Returns stop level if triggered."""
    best = entry_px
    for b in bars_since_entry:
        if direction > 0:
            stop = best * (1.0 - Z)
            if b["low"] <= stop:
                return stop
            best = max(best, b["high"])
        else:
            stop = best * (1.0 + Z)
            if b["high"] >= stop:
                return stop
            best = min(best, b["low"])
    return None


def new_state(bars, now):
    last = bars[-1]
    return {
        "registered": "2026-08-22",
        "purpose": "risk-tool calibration, NOT an edge claim (edge failed at N=1486)",
        "params": {"book_usd": BOOK_USD, "Y": Y, "Z": Z, "lookback": LOOKBACK,
                   "maker_fee": MAKER_FEE, "fill_rule": "strict-through",
                   "pair": TAPE_PAIR},
        "armed_utc": iso(now),
        "baseline": {"start_date": last["date"], "start_close": last["close"],
                     "units": BOOK_USD / last["close"], "equity": BOOK_USD,
                     "peak": BOOK_USD, "max_dd": 0.0},
        "equity": BOOK_USD, "peak": BOOK_USD, "max_dd": 0.0,
        "position": None, "pending_order": None,
        "orders_posted": 0, "fills": 0, "misses": 0, "voids": 0,
        "fill_rate": None,
        "orders": [], "events": [],
        "last_settled_date": last["date"],   # signal arms on the NEXT bar
        "last_tick_utc": iso(now),
    }


def add_event(state, kind, detail):
    state["events"].append({"utc_ts": iso(utc_now()), "event": kind,
                            "detail": detail})
    state["events"] = state["events"][-MAX_EVENTS:]
    print("[%s] maker_calibration %s: %s" % (iso(utc_now()), kind, detail),
          flush=True)


def post_order(state, side, purpose, price, direction, now):
    state["pending_order"] = {
        "id": state["orders_posted"] + 1, "side": side, "purpose": purpose,
        "price": round(price, 2), "direction": direction,
        "posted_utc": iso(now), "expiry_utc": iso(next_utc_midnight(now)),
    }
    state["orders_posted"] += 1
    add_event(state, "order_posted", "%s %s limit %.2f (post-only sim), expires %s"
              % (purpose, side, price, state["pending_order"]["expiry_utc"]))


def resolve_order(state, now):
    """Fill / miss / void the pending order against the WS tape."""
    o = state.get("pending_order")
    if not o:
        return
    window_end = min(iso(now), o["expiry_utc"])
    hit, n_prints = tape_first_through(o["side"], o["price"],
                                       o["posted_utc"], window_end)
    if hit:
        ts, px = hit
        slip_bps = abs(px - o["price"]) / o["price"] * 1e4
        record = dict(o, outcome="FILL", fill_utc=ts, booked_px=o["price"],
                      first_through_px=px, through_bps=round(slip_bps, 2))
        state["orders"].append(record)
        state["fills"] += 1
        state["pending_order"] = None
        apply_fill(state, o, ts)
        add_event(state, "fill", "%s %s filled at %.2f (first through print "
                  "%.2f, %.1f bps through)" % (o["purpose"], o["side"],
                                               o["price"], px, slip_bps))
    elif iso(now) >= o["expiry_utc"]:
        if n_prints == 0:
            state["orders"].append(dict(o, outcome="VOID_DATA_GAP"))
            state["voids"] += 1
            add_event(state, "void_data_gap",
                      "order %d window had ZERO tape prints — collector gap; "
                      "excluded from denominator (LOUD: check ws_tape_collector)"
                      % o["id"])
            loud_fail("tape_gap", "order %d voided: no %s prints %s..%s"
                      % (o["id"], TAPE_PAIR, o["posted_utc"], o["expiry_utc"]))
        else:
            state["orders"].append(dict(o, outcome="MISS"))
            state["misses"] += 1
            add_event(state, "miss", "%s %s at %.2f expired unfilled "
                      "(%d prints in window, none strictly through)"
                      % (o["purpose"], o["side"], o["price"], n_prints))
        state["pending_order"] = None
    resolved = state["fills"] + state["misses"]
    state["fill_rate"] = (state["fills"] / resolved) if resolved else None


def apply_fill(state, order, fill_ts):
    if order["purpose"] == "entry":
        state["position"] = {
            "direction": order["direction"], "entry_px": order["price"],
            "entry_equity": state["equity"], "entry_utc": fill_ts,
            "entry_date": fill_ts[:10],
        }
    else:                                # exit
        p = state["position"]
        if p is None:
            raise SchemaDriftError("exit fill with no open position — state corrupt")
        g = p["direction"] * (order["price"] / p["entry_px"] - 1.0)
        state["equity"] = (p["entry_equity"] * (1.0 - MAKER_FEE)
                           * (1.0 + g) * (1.0 - MAKER_FEE))
        state["position"] = None


def mark_book(state, bars):
    last_close = bars[-1]["close"]
    p = state.get("position")
    if p:
        m = p["direction"] * (last_close / p["entry_px"] - 1.0)
        eq = p["entry_equity"] * (1.0 - MAKER_FEE) * (1.0 + m)
    else:
        eq = state["equity"]
    state["marked_equity"] = round(eq, 2)
    state["peak"] = max(state["peak"], eq)
    state["max_dd"] = min(state["max_dd"], eq / state["peak"] - 1.0)
    b = state["baseline"]
    b["equity"] = round(b["units"] * last_close, 2)
    b["peak"] = max(b["peak"], b["equity"])
    b["max_dd"] = min(b["max_dd"], b["equity"] / b["peak"] - 1.0)


def decide(state, bars, now):
    """Signal step — only when a NEW settled bar exists and no order pends."""
    latest = bars[-1]["date"]
    if latest <= state["last_settled_date"] or state.get("pending_order"):
        return
    state["last_settled_date"] = latest
    p = state.get("position")
    if p:
        since = [b for b in bars if b["date"] >= p["entry_date"]]
        stop = exit_wanted(since, p["entry_px"], p["direction"])
        if stop is not None:
            side = "sell" if p["direction"] > 0 else "buy"
            post_order(state, side, "exit", stop, -p["direction"], now)
    else:
        closes = [b["close"] for b in bars]
        d = swing_edges(closes)
        if d != 0:
            side = "buy" if d > 0 else "sell"
            post_order(state, side, "entry", bars[-1]["close"], d, now)
        else:
            add_event(state, "no_signal",
                      "settled bar %s: no fresh edge — armed, flat" % latest)


def save_state(state):
    state["last_tick_utc"] = iso(utc_now())
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


def tick():
    now = utc_now()
    bars = fetch_settled_daily()
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
    else:
        state = new_state(bars, now)
        add_event(state, "armed", "probe armed; baseline %f BTC from settled "
                  "close %.2f (%s); signal live from the next settled bar"
                  % (state["baseline"]["units"], state["baseline"]["start_close"],
                     state["baseline"]["start_date"]))
    resolve_order(state, now)
    decide(state, bars, now)
    mark_book(state, bars)
    save_state(state)
    print("[%s] maker_calibration tick ok: settled=%s equity=%.2f baseline=%.2f "
          "orders=%d fills=%d misses=%d voids=%d pending=%s"
          % (iso(now), state["last_settled_date"], state["marked_equity"],
             state["baseline"]["equity"], state["orders_posted"], state["fills"],
             state["misses"], state["voids"],
             "yes" if state["pending_order"] else "no"), flush=True)


def status():
    if not os.path.exists(STATE_PATH):
        print("maker_calibration: NO DATA — probe has never ticked. Zero orders, "
              "zero fills, zero conclusions.")
        return
    with open(STATE_PATH) as f:
        s = json.load(f)
    resolved = s["fills"] + s["misses"]
    print("MAKER CALIBRATION PROBE — risk-tool calibration, NOT an edge claim")
    print("(the underlying edge already failed at N=1,486; see prereg)")
    print("armed %s | last tick %s | last settled bar %s"
          % (s["armed_utc"], s["last_tick_utc"], s["last_settled_date"]))
    print("book: marked %.2f (max DD %.2f%%) | baseline held-BTC %.2f (max DD %.2f%%)"
          % (s.get("marked_equity", s["equity"]), s["max_dd"] * 100,
             s["baseline"]["equity"], s["baseline"]["max_dd"] * 100))
    print("orders posted %d | fills %d | misses %d | voids %d"
          % (s["orders_posted"], s["fills"], s["misses"], s["voids"]))
    if resolved:
        print("fill rate: %.1f%% of %d resolved orders (backtest assumed 100%%)"
              % (s["fill_rate"] * 100, resolved))
    else:
        print("fill rate: NO RESOLVED ORDERS YET — nothing to report, honestly.")
    print("evidence bar: 60+ days AND >=30 resolved orders before ANY verdict.")
    if s.get("pending_order"):
        o = s["pending_order"]
        print("pending: %s %s limit %.2f posted %s expires %s"
              % (o["purpose"], o["side"], o["price"], o["posted_utc"],
                 o["expiry_utc"]))
    if s.get("position"):
        p = s["position"]
        print("position: %s from %.2f (%s)"
              % ("LONG" if p["direction"] > 0 else "SHORT", p["entry_px"],
                 p["entry_utc"]))
    for e in s["events"][-5:]:
        print("event %s %s: %s" % (e["utc_ts"], e["event"], e["detail"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    try:
        status() if args.status else tick()
    except SchemaDriftError as exc:
        loud_fail("schema_drift", str(exc))
        sys.exit(3)
    except Exception as exc:
        loud_fail(type(exc).__name__, str(exc))
        sys.exit(1)
