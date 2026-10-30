#!/usr/bin/env python3
"""floor_exit_shadow.py — PAIRED forward test: same entries, real stops, longer holds.

PRE-REGISTERED 2026-09-05. PAPER ONLY. Touches no live floor.

THE TWO FINDINGS THIS TESTS (both verified directly on the ledgers)
-------------------------------------------------------------------
1. THE STOPS DO NOT STOP. `kraken_floor.exit_signal(cfg, pos, H, L, C)` RECEIVES the
   intraday high and low arrays and then tests the stop against C[-1], the close.
   The lows are right there and ignored. Across 63 stop-tagged trades on four
   floors the configured 8/12/14% stops realised a MEAN of -12.61%, median
   -10.65%, worst -62.22% (APR/USD on a 14% stop). 60 of 63 realised worse than
   their level. That is a code path, not a market state (p = 4.5e-15).

2. THE HOLDS ARE TOO SHORT TO SURVIVE THEIR OWN COSTS. Median hold 5.00 days.
   Round-trip cost x 365 / hold_days = a 21.9-58.4%/yr hurdle at 5 days, against a
   best-ever measured net edge of 3.15%/yr. The desk's own holding_period_lab.json
   shows net return crossing zero at 14 days purely through cost amortisation.
   This is arithmetic and reads the same in every regime.

THE DESIGN — WHY A SHADOW AND NOT A PATCH
-----------------------------------------
Patching the live floor would change its track record and make the comparison
argue-able forever. A shadow MIRRORS every live entry — same symbol, side, price,
units, timestamp — and diverges ONLY on the exit rules. Every trade is then a
PAIRED observation: live exit vs shadow exit on an identical entry. The difference
is the treatment. Nothing else.

TREATMENT (what the shadow does differently)
  A. REAL STOP. Walk the hourly bars since entry. A long is stopped on the first
     bar whose LOW touches the stop level; a short on the first bar whose HIGH
     does. Fill = the stop level, UNLESS that bar OPENED through the level, in
     which case fill = the open (a gap is a gap; modelling a fill inside it would
     be the same lie the live floor tells in reverse).
  B. MINIMUM HOLD. Discretionary exits (channel-exit, reverted) are IGNORED before
     MIN_HOLD_DAYS. The stop is never delayed. Max hold is raised to MAX_HOLD_DAYS.
  C. When the live floor closes a name the shadow still holds: if the shadow has
     held it >= MIN_HOLD_DAYS, it exits at the live exit price (same signal,
     honoured). If not, it keeps holding.

CONTROL: the live floor itself, unchanged.

HYPOTHESIS (H1): shadow realised P&L per trade > live realised P&L per trade.
NULL (H0): no difference.
DECISION RULE, fixed now: at 40 paired closed trades, collapse to one observation
per exit day. If the paired mean difference is > +2pp of notional with the sign
consistent in both halves -> treatment adopted on the live floors as an
amendment. If within +/-2pp -> keep running. If < -2pp -> treatment rejected and
this file is closed. The stop half and the hold half are recorded SEPARATELY so
a win on one is not credited to the other.

CAPITAL: $0 deployed. Measurement only. Nothing here can lose a dollar.
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "floor_exit_shadow.json")
LIVE = os.path.join(os.path.dirname(BASE), "KrakenQuantDesk", "kraken_bot", "kraken_floor.json")

MIN_HOLD_DAYS = 14.0          # the desk's own lab: net return crosses zero at 14d
MAX_HOLD_DAYS = 45.0          # raised from 12/15/40
DECISION_N = 40               # paired closed trades before a verdict
DECISION_BAND_PP = 2.0
UA = {"User-Agent": "TFO paper shadow - research"}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load():
    if not os.path.exists(LEDGER):
        return {"registered_utc": _utc(), "paper_only": True, "capital_deployed": 0.0,
                "min_hold_days": MIN_HOLD_DAYS, "max_hold_days": MAX_HOLD_DAYS,
                "positions": {}, "closed": [], "note":
                "paired shadow of kraken_floor — identical entries, real stops, 14d min hold"}
    with open(LEDGER) as f:
        return json.load(f)


def save(d):
    d["updated_utc"] = _utc()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, LEDGER)


def _pair(sym):
    """kraken_floor symbol 'HYPE/USD' -> Kraken OHLC pair name."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(BASE), "KrakenQuantDesk"))
        from kraken_backtest import sym_to_pair
        return sym_to_pair(sym)
    except Exception:                                       # noqa: BLE001
        return sym.replace("/", "")


def bars_since(sym, since_epoch):
    """Hourly OHLC bars from Kraken public API since an epoch. [] on any failure."""
    try:
        u = ("https://api.kraken.com/0/public/OHLC?pair=%s&interval=60&since=%d"
             % (_pair(sym), int(since_epoch) - 3600))
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20))
        if j.get("error"):
            return []
        k = [x for x in j["result"] if x != "last"][0]
        out = []
        for r in j["result"][k]:
            out.append({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
                        "l": float(r[3]), "c": float(r[4])})
        return [b for b in out if b["t"] >= int(since_epoch)]
    except Exception:                                       # noqa: BLE001
        return []


def real_stop_fill(pos, bars, stop_pct):
    """First bar whose LOW (long) / HIGH (short) touches the stop. Gap-honest fill."""
    e = pos["entry"]
    if pos["side"] == "long":
        lvl = e * (1.0 - stop_pct / 100.0)
        for b in bars:
            if b["l"] <= lvl:
                return (b["o"] if b["o"] < lvl else lvl), b["t"]
    else:
        lvl = e * (1.0 + stop_pct / 100.0)
        for b in bars:
            if b["h"] >= lvl:
                return (b["o"] if b["o"] > lvl else lvl), b["t"]
    return None, None


def _pnl(pos, px):
    if pos["side"] == "long":
        return (px - pos["entry"]) * pos["units"]
    return (pos["entry"] - px) * pos["units"]


def tick(verbose=True):
    d = load()
    try:
        live = json.load(open(LIVE))
    except Exception as e:                                  # noqa: BLE001
        print("shadow: live floor unreadable (%s) — no tick" % e)
        return d
    now = time.time()
    live_open = {}
    live_closed_by_key = {}
    for dn, desk in (live.get("desks") or {}).items():
        stop = {"Mean-Reversion": 8.0, "Breakout": 8.0, "Micro-Cap Momentum": 14.0}.get(dn, 8.0)
        for sym, p in (desk.get("positions") or {}).items():
            live_open["%s|%s" % (dn, sym)] = (p, stop)
        for t in desk.get("closed_trades") or []:
            live_closed_by_key.setdefault("%s|%s" % (dn, t.get("sym")), []).append(t)

    # 1. MIRROR every live entry the shadow has not seen
    for key, (p, stop) in live_open.items():
        if key in d["positions"] or any(c["key"] == key and c.get("entry_epoch") == p.get("entry_epoch")
                                        for c in d["closed"]):
            continue
        d["positions"][key] = {
            "key": key, "desk": key.split("|")[0], "sym": key.split("|")[1],
            "side": p["side"], "entry": p["entry"], "units": p["units"],
            "entry_epoch": p["entry_epoch"], "stop_pct": stop,
            "mirrored_utc": _utc(), "mark": p.get("mark"),
        }
        if verbose:
            print("  MIRROR %-28s %s @ %.6g" % (key, p["side"], p["entry"]))

    # 2. RUN THE TREATMENT on every shadow position
    for key in list(d["positions"]):
        s = d["positions"][key]
        held = (now - s["entry_epoch"]) / 86400.0
        bars = bars_since(s["sym"], s["entry_epoch"])
        if bars:
            s["mark"] = bars[-1]["c"]
        reason, fill = None, None
        # A. real stop — never delayed
        fpx, ft = real_stop_fill(s, bars, s["stop_pct"]) if bars else (None, None)
        if fpx is not None:
            reason, fill = "real-stop", fpx
        # B/C. live floor closed it -> honour only after min hold
        elif key not in live_open:
            lc = [t for t in live_closed_by_key.get(key, [])]
            if lc and held >= MIN_HOLD_DAYS:
                last = lc[-1]
                lr = last.get("ret_pct")
                fill = (s["entry"] * (1 + lr / 100.0) if s["side"] == "long"
                        else s["entry"] * (1 - lr / 100.0)) if lr is not None else s.get("mark")
                reason = "live-%s-honoured-after-minhold" % last.get("reason")
            elif lc and held < MIN_HOLD_DAYS:
                s["live_exited_early"] = {"reason": lc[-1].get("reason"), "live_ret_pct": lc[-1].get("ret_pct")}
        if reason is None and held >= MAX_HOLD_DAYS and s.get("mark"):
            reason, fill = "max-hold-45d", s["mark"]
        if reason and fill:
            pnl = _pnl(s, fill)
            live_t = (live_closed_by_key.get(key) or [None])[-1]
            rec = {**s, "exit_px": fill, "exit_utc": _utc(), "reason": reason,
                   "held_days": round(held, 2), "shadow_pnl": round(pnl, 2),
                   "shadow_ret_pct": round(100 * pnl / (s["entry"] * s["units"]), 2),
                   "live_pnl": (live_t or {}).get("pnl"), "live_ret_pct": (live_t or {}).get("ret_pct"),
                   "live_reason": (live_t or {}).get("reason")}
            d["closed"].append(rec)
            del d["positions"][key]
            if verbose:
                print("  EXIT   %-28s %-34s shadow %+.2f%%  live %s"
                      % (key, reason, rec["shadow_ret_pct"],
                         ("%+.2f%%" % rec["live_ret_pct"]) if rec["live_ret_pct"] is not None else "open"))
    save(d)
    return d


def status():
    d = load()
    paired = [c for c in d["closed"] if c.get("live_ret_pct") is not None]
    print("=" * 70)
    print("FLOOR EXIT SHADOW — real stops + %.0fd min hold   (paper, $0 deployed)" % MIN_HOLD_DAYS)
    print("=" * 70)
    print("  registered %s   mirrored open %d   closed %d   PAIRED %d / %d needed"
          % (d.get("registered_utc"), len(d["positions"]), len(d["closed"]), len(paired), DECISION_N))
    if paired:
        diffs = [c["shadow_ret_pct"] - c["live_ret_pct"] for c in paired]
        stops = [c for c in paired if c["reason"] == "real-stop"]
        holds = [c for c in paired if "minhold" in c["reason"] or "max-hold" in c["reason"]]
        print()
        print("  paired mean (shadow - live): %+.2f pp   median %+.2f pp"
              % (sum(diffs) / len(diffs), sorted(diffs)[len(diffs) // 2]))
        if stops:
            sd = [c["shadow_ret_pct"] - c["live_ret_pct"] for c in stops]
            print("  STOP half   n=%d  mean %+.2f pp" % (len(stops), sum(sd) / len(sd)))
        if holds:
            hd = [c["shadow_ret_pct"] - c["live_ret_pct"] for c in holds]
            print("  HOLD half   n=%d  mean %+.2f pp" % (len(holds), sum(hd) / len(hd)))
        print("  (verdict at %d paired trades, +/-%.0fpp band, halves checked separately)"
              % (DECISION_N, DECISION_BAND_PP))
    for k, s in d["positions"].items():
        held = (time.time() - s["entry_epoch"]) / 86400.0
        print("  open  %-28s %-5s entry %.6g  held %.1fd%s"
              % (k, s["side"], s["entry"], held,
                 "  [live exited: %s]" % s["live_exited_early"]["reason"] if s.get("live_exited_early") else ""))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", action="store_true")
    a = ap.parse_args()
    if a.tick:
        tick()
    else:
        status()
