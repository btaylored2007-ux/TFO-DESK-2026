#!/usr/bin/env python3
"""Strategy Tournament — every strategy on one leaderboard, judged only by live results.

Rules frozen in TOURNAMENT_PREREGISTRATION.md (2026-08-06). Paper capital is the
experiment budget; forward performance is the only judge. Four new $10k sleeves
compete alongside every existing book. No verdict before 182 days — early boards
are labelled as the noise they are.

PAPER ONLY. No broker, no credentials, no real orders.

Usage:
    python3 tournament.py            # tick all sleeves (each on its own cadence)
    python3 tournament.py --board    # leaderboard across sleeves + all books
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
import sys
import time
import book_kill
KILL_DD_PCT = book_kill.KILL_DD_PCT   # KILL_THRESHOLD_AMENDMENT.md: the number lives in book_kill only
import urllib.request
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
if KRAKEN_DESK not in sys.path:
    sys.path.insert(0, KRAKEN_DESK)

TDIR = os.path.join(BASE, "reports", "tournament")
BOARD = os.path.join(BASE, "reports", "tournament.json")
START = 10000.0
SLEEVE_START = {"covered_call_etf": 25000.0, "vix_regime_switch": 25000.0,
                "cef_discount": 10000.0, "claude_discretionary": 10000.0}
REGISTERED = "2026-08-06"
MIN_EVIDENCE_DAYS = 182

ETF15 = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
         "GLD", "SLV", "DBC", "USO", "IYR", "AGG"]


# ---------------------------------------------------------------- helpers
def jf(path, fb):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fb


# RETIRED sleeves must NEVER be auto-created (funder 2026-09-19: archive cleanup).
# desk_tick → tournament.py was resurrecting full SLEEVE_START capital as fake EXTERNAL funding.
RETIRED_SLEEVES = frozenset({
    "capacity_probe",
    "shareholder_yield",
    "cef_discount",
    "fng_momentum",
    "vix_regime_switch",
    "probe_agri",
    "probe_em_debt",
    "probe_japan",
    "probe_india",
    "claude_discretionary", "covered_call_etf", "dip_rider", "equal_weight_all",
    "spy_trend_cash", "lowvol_crypto", "rally_rider", "rally_rider_hold", "rally_rider_x",
})

def load_sleeve(name):
    path = os.path.join(TDIR, name + ".json")
    s = jf(path, None)
    if s is None:
        if name in RETIRED_SLEEVES:
            return None  # do not resurrect archived capital
        st = SLEEVE_START.get(name, START)
        s = {"name": name, "cash": st, "holdings": {}, "entry_px": {},
             "last_rebalance": None, "costs": 0.0, "peak": st, "start": st,
             "frozen": False, "started": time.strftime("%Y-%m-%d")}
    return s


def save_sleeve(s):
    os.makedirs(TDIR, exist_ok=True)
    s["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = os.path.join(TDIR, s["name"] + ".json.tmp")
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, os.path.join(TDIR, s["name"] + ".json"))


def days_since(datestr):
    if not datestr:
        return 1e9
    return (time.time() - time.mktime(time.strptime(datestr[:10], "%Y-%m-%d"))) / 86400.0


def kraken_last(pairs):
    """{'XBT/USD': px} via public ticker."""
    if not pairs:
        return {}
    q = ",".join(p.replace("/", "") for p in pairs)
    try:
        with urllib.request.urlopen(
                "https://api.kraken.com/0/public/Ticker?pair=" + q, timeout=8) as r:
            res = json.load(r).get("result", {})
    except Exception:
        return {}
    out = {}
    for p in pairs:
        base = p.replace("/", "")
        for k, v in res.items():
            if base in k or k == "X" + base.replace("USD", "ZUSD"):
                try:
                    out[p] = float(v["c"][0])
                except Exception:
                    pass
                break
    return out


_YF_CACHE = [0.0, {}]


def yf_last(tickers):
    now = time.time()
    if now - _YF_CACHE[0] > 120 or any(t not in _YF_CACHE[1] for t in tickers):
        try:
            import yfinance as yf
            raw = yf.download(list(set(tickers) | {"SPY"}), period="5d",
                              interval="1d", auto_adjust=True, progress=False,
                              threads=True)["Close"].ffill().iloc[-1]
            _YF_CACHE[0] = now
            _YF_CACHE[1].update({t: float(raw[t]) for t in raw.index if raw[t] == raw[t]})
        except Exception:
            pass
    return dict(_YF_CACHE[1])



def _firm_halt_blocks_entry(s, what=""):
    """True if the firm-wide halt refuses a NEW entry for this book. CM-047.

    Used at the direct-entry sites that do NOT route through set_holdings
    (rally_rider / rally_rider_x opens, covered_call underlying purchase).
    Fails OPEN — a missing gate must never halt trading — and is loud either way.
    """
    try:
        import book_kill as _kill
        ok, why = _kill.firm_entries_allowed(s.get("name", ""), verbose=False)
    except Exception as e:                          # noqa: BLE001 - fail OPEN
        print("   firm halt gate unavailable (%s: %s) — entry NOT blocked"
              % (type(e).__name__, e))
        return False
    if not ok:
        print("   HALT-REFUSED %s: new entry%s refused — %s"
              % (s.get("name", "?"), (" (%s)" % what) if what else "", why))
        s.setdefault("halt_refusals", []).append(
            {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "n": 1, "syms": [what] if what else [], "reason": why})
        s["halt_refusals"] = s["halt_refusals"][-50:]
        return True
    return False


def mark(s, px):
    eq = s["cash"] + sum(u * px.get(t, s["entry_px"].get(t, 0.0))
                         for t, u in s["holdings"].items())
    s["equity"] = round(eq, 2)
    s["peak"] = max(s.get("peak", eq), eq)
    s["dd_pct"] = round((eq / s["peak"] - 1.0) * 100, 2)
    if s["dd_pct"] <= KILL_DD_PCT and not s["frozen"]:
        s["frozen"] = True
        s["frozen_reason"] = "kill rule: drawdown %.1f%% <= %.0f%%" % (s["dd_pct"], KILL_DD_PCT)
    return eq


def set_holdings(s, targets, px, cost_bps):
    """targets: {sym: weight}. Trades to target, charges cost on turnover.

    CM-047 — firm halt gate. This is the choke point every sleeve rebalances
    through, so the firm-wide HALT_NEW_ENTRIES flag is enforced HERE rather than
    in 14 separate tick functions. Under a halt each target is clamped to at
    most what the sleeve already holds: a new name is refused (have == 0), an
    add-to is refused, while a REDUCTION and a FULL EXIT both pass untouched
    (an exit simply omits the ticker from `targets`, which this loop never
    touches). Freed capital falls to cash, which is the correct posture under a
    firm drawdown halt.

    The halt can never close a position and never blocks an exit.
    """
    eq = mark(s, px)
    halted, halt_reason = False, ""
    try:
        import book_kill as _kill
        _ok, _why = _kill.firm_entries_allowed(s.get("name", ""), verbose=False)
        halted, halt_reason = (not _ok), _why
    except Exception as e:                          # noqa: BLE001 - fail OPEN
        print("   firm halt gate unavailable in set_holdings (%s: %s) — trading "
              "NOT halted by a missing gate" % (type(e).__name__, e))
    traded = 0.0
    new_h, new_e = {}, {}
    refused = []
    for t, w in targets.items():
        if t not in px or px[t] <= 0:
            continue
        want = eq * w
        have = s["holdings"].get(t, 0.0) * px[t]
        if halted and want > have:
            refused.append((t, want, have))
            want = have                             # reductions/exits still pass
            if want <= 0:
                continue
        traded += abs(want - have)
        new_h[t] = want / px[t]
        new_e[t] = px[t]
    if refused:
        print("   HALT-REFUSED %s: %d target(s) clamped to current holding "
              "(%s) — %s" % (s.get("name", "?"), len(refused),
                             ", ".join("%s $%.0f->$%.0f" % (t, w, h)
                                       for t, w, h in refused[:6]), halt_reason))
        s.setdefault("halt_refusals", []).append(
            {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "n": len(refused), "syms": [t for t, _, _ in refused],
             "reason": halt_reason})
        s["halt_refusals"] = s["halt_refusals"][-50:]
    for t, u in s["holdings"].items():
        if t not in new_h and t in px:
            traded += u * px[t]
    cost = traded * cost_bps / 10000.0
    s["holdings"], s["entry_px"] = new_h, new_e
    s["cash"] = eq - sum(u * px[t] for t, u in new_h.items()) - cost
    s["costs"] = round(s.get("costs", 0.0) + cost, 2)
    s["last_rebalance"] = time.strftime("%Y-%m-%d")
    mark(s, px)
    return cost


# ---------------------------------------------------------------- sleeves
def tick_lowvol_crypto(s):
    """Weekly: 5 lowest-30d-vol of the ~20 most liquid Kraken USD pairs, long EW."""
    if days_since(s["last_rebalance"]) < 7:
        px = kraken_last(list(s["holdings"]))
        if px:
            mark(s, px)
        return None
    from kraken_backtest import fetch_ohlc, sym_to_pair
    import numpy as np
    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=6) as r:
            st = json.load(r)
        uni = [(x["symbol"], float(x.get("vol_musd") or 0))
               for x in (st.get("universe") or []) if isinstance(x, dict)]
        uni.sort(key=lambda kv: -kv[1])
        symbols = [k for k, _ in uni[:20]]
    except Exception:
        symbols = ["XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD",
                   "XLM/USD", "LTC/USD", "LINK/USD", "DOT/USD", "AVAX/USD"]
    vols, last = {}, {}
    for sym in symbols:
        try:
            b = fetch_ohlc(sym_to_pair(sym), 1440, 35)
            c = np.array([x[4] for x in b], float)
            if len(c) >= 25:
                r_ = np.diff(c) / c[:-1]
                vols[sym] = float(r_.std())
                last[sym] = float(c[-1])
        except Exception:
            pass
        time.sleep(0.2)
    if len(vols) < 6:
        return "skipped (data)"
    picks = sorted(vols, key=vols.get)[:5]
    cost = set_holdings(s, {p: 0.2 for p in picks}, last, 30.0)
    return "rebalanced into %s (cost $%.2f)" % (", ".join(p.split("/")[0] for p in picks), cost)


def tick_fng_momentum(s):
    """Long BTC while Fear&Greed >= 55, flat below. Daily check."""
    try:
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1",
                                    timeout=10) as r:
            val = float(json.load(r)["data"][0]["value"])
    except Exception:
        return "skipped (F&G unreachable)"
    px = kraken_last(["XBT/USD"])
    if "XBT/USD" not in px:
        return "skipped (price)"
    s["signal"] = val
    holding = bool(s["holdings"])
    if val >= 55 and not holding:
        cost = set_holdings(s, {"XBT/USD": 1.0}, px, 30.0)
        return "F&G %.0f >= 55 -> LONG BTC (cost $%.2f)" % (val, cost)
    if val < 55 and holding:
        cost = set_holdings(s, {}, px, 30.0)
        return "F&G %.0f < 55 -> FLAT (cost $%.2f)" % (val, cost)
    mark(s, px)
    return None


def tick_equal_weight_all(s):
    """15 ETFs equal weight, quarterly."""
    px = yf_last(ETF15)
    if days_since(s["last_rebalance"]) < 88:
        if px:
            mark(s, px)
        return None
    have = [t for t in ETF15 if t in px]
    if len(have) < 12:
        return "skipped (data)"
    cost = set_holdings(s, {t: 1.0 / len(have) for t in have}, px, 10.0)
    return "rebalanced equal-weight %d ETFs (cost $%.2f)" % (len(have), cost)


def tick_spy_trend_cash(s):
    """SPY above 200dma -> SPY; below -> cash. Monthly."""
    if days_since(s["last_rebalance"]) < 30:
        px = yf_last(["SPY"])
        if px:
            mark(s, px)
        return None
    try:
        import yfinance as yf
        h = yf.download("SPY", period="1y", interval="1d", auto_adjust=True,
                        progress=False)["Close"]
        if hasattr(h, "columns"):
            h = h.iloc[:, 0]
        above = float(h.iloc[-1]) > float(h.tail(200).mean())
        px = {"SPY": float(h.iloc[-1])}
    except Exception:
        return "skipped (data)"
    cost = set_holdings(s, ({"SPY": 1.0} if above else {}), px, 10.0)
    return "SPY %s 200dma -> %s (cost $%.2f)" % (
        "above" if above else "below", "LONG" if above else "CASH", cost)


def tick_vix_regime_switch(s):
    """SPY while VIX < VIX3M (calm/contango); IEF when inverted. Flips only."""
    import yfinance as yf
    try:
        h = yf.download(["^VIX", "^VIX3M", "SPY", "IEF"], period="5d",
                        interval="1d", auto_adjust=True, progress=False
                        )["Close"].ffill().iloc[-1]
        vix, v3m = float(h["^VIX"]), float(h["^VIX3M"])
        px = {"SPY": float(h["SPY"]), "IEF": float(h["IEF"])}
    except Exception:
        return "skipped (data)"
    calm = vix < v3m
    s["signal"] = "VIX %.1f %s VIX3M %.1f" % (vix, "<" if calm else ">=", v3m)
    want = "SPY" if calm else "IEF"
    if want in s["holdings"]:
        mark(s, px)
        return None
    cost = set_holdings(s, {want: 1.0}, px, 10.0)
    return "%s -> hold %s (cost $%.2f)" % (s["signal"], want, cost)


CC_SHORTLIST = ["DIA", "IWM", "GLD", "EFA", "TLT", "XLF", "XLE", "EEM"]


def _chain(underlying, expiry=None):
    import yfinance as yf
    tk = yf.Ticker(underlying)
    exps = tk.options
    if expiry and expiry in exps:
        return expiry, tk.option_chain(expiry).calls
    now = time.time()
    for e in exps:
        d = (time.mktime(time.strptime(e, "%Y-%m-%d")) - now) / 86400.0
        if 25 <= d <= 40:
            return e, tk.option_chain(e).calls
    return exps[-1], tk.option_chain(exps[-1]).calls


# --- Non-negotiable #7 stale-refusal gate (funder directive 2026-08-22:
#     "all books need live prices, no stale prices to trade against").
#     Applies to option FILLS only; chain-mid MARKING is display and untouched. ---
CC_MAX_QUOTE_AGE_TDAYS = 1


def _cc_tdays_since(epoch):
    """Trading days (Mon-Fri, UTC dates) strictly after the quote's date."""
    import datetime as dt
    d = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).date()
    today = dt.datetime.now(dt.timezone.utc).date()
    n = 0
    while d < today:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _cc_quote_ok(row):
    """(ok, reason) — an option fill may book only on a quote from an open US
    session whose lastTradeDate is <= CC_MAX_QUOTE_AGE_TDAYS trading days old.
    Session window 13:30-21:00 UTC Mon-Fri covers EDT and EST regular hours;
    holidays fall through to the lastTradeDate age gate."""
    now = time.gmtime()
    mins = now.tm_hour * 60 + now.tm_min
    if now.tm_wday >= 5 or not (810 <= mins <= 1260):
        return False, "US session closed (%02d:%02dZ)" % (now.tm_hour, now.tm_min)
    try:
        ltd = row["lastTradeDate"]
        ep = ltd.timestamp() if hasattr(ltd, "timestamp") else float(ltd)
        age = _cc_tdays_since(ep)
    except Exception as e:
        return False, "no usable lastTradeDate on quote (%s)" % str(e)[:30]
    if age > CC_MAX_QUOTE_AGE_TDAYS:
        return False, ("quote lastTradeDate %d trading days old (limit %d)"
                       % (age, CC_MAX_QUOTE_AGE_TDAYS))
    return True, ""


def _cc_pick_underlying(cash, px_map):
    """Most expensive shortlist ETF whose round lot fits; up to 2 lots."""
    afford = [(t, px_map[t]) for t in CC_SHORTLIST
              if t in px_map and px_map[t] * 100 <= cash]
    if not afford:
        return None, 0
    afford.sort(key=lambda kv: -kv[1])
    t, px = afford[0]
    lots = min(2, int(cash // (px * 100)))
    return t, lots


def tick_covered_call_etf(s):
    """Up to 2 round lots of the priciest affordable shortlist ETF + 1 short
    ~3% OTM monthly call per lot. Premium in at BID, close at ASK."""
    px_map = yf_last(CC_SHORTLIST)
    und = s.get("underlying")
    msgs = []

    if not und:
        und, lots = _cc_pick_underlying(s["cash"], px_map)
        if not und:
            return "skipped (nothing affordable)"
        if _firm_halt_blocks_entry(s, und):         # CM-047
            return "skipped (firm-wide halt: new entries refused)"
        px = px_map[und]
        shares = 100.0 * lots
        cost = shares * px * 0.0010
        s["holdings"][und] = shares
        s["entry_px"][und] = px
        s["cash"] -= shares * px + cost
        s["costs"] = round(s["costs"] + cost, 2)
        s["underlying"] = und
        msgs.append("bought %d %s @ %.2f (%d lots)" % (shares, und, px, lots))
    if und not in px_map:
        return "skipped (price)"
    px = px_map[und]

    call = s.get("short_call")
    if call:
        days_left = (time.mktime(time.strptime(call["expiry"], "%Y-%m-%d"))
                     - time.time()) / 86400.0
        if days_left <= 2:
            # Registered rule: "buy back at ASK". Fix 2026-08-22 (funder
            # directive, Non-negotiable #7): the close used to call the
            # undefined _iwm_chain, whose swallowed NameError booked every
            # close at intrinsic — below the real ask. Closes now pay the
            # real fresh ask or REFUSE loudly and retry next tick; after
            # expiry the contract settles at intrinsic vs the LIVE underlying
            # (a settlement value, not a stale quote).
            ask = None
            if days_left <= 0:
                ask = max(0.0, px - call["strike"])
                msgs.append("call %s EXPIRED — settling at intrinsic %.2f"
                            % (call["expiry"], ask))
            else:
                row = ()
                try:
                    _, calls = _chain(und, call["expiry"])
                    row = calls[calls["strike"] == call["strike"]]
                except Exception as e:
                    msgs.append("REFUSED close: chain unavailable (%s) — retry"
                                " next tick" % str(e)[:40])
                if len(row):
                    ok, why = _cc_quote_ok(row.iloc[0])
                    a = float(row["ask"].iloc[0])
                    if not ok:
                        msgs.append("REFUSED close at ask: %s — retry next tick"
                                    % why)
                    elif not a > 0:
                        msgs.append("REFUSED close: no ask on fresh quote —"
                                    " retry next tick")
                    else:
                        ask = a
                elif not isinstance(row, tuple):
                    msgs.append("REFUSED close: strike %.0f absent from %s chain"
                                " — retry next tick"
                                % (call["strike"], call["expiry"]))
            if ask is not None:
                s["cash"] -= ask * 100
                s["costs"] = round(s["costs"] + 1.0, 2)   # $1 contract fee
                s["cash"] -= 1.0
                msgs.append("closed %s call @ ask %.2f" % (call["expiry"], ask))
                s["short_call"] = call = None

    if not s.get("short_call") and s["holdings"].get(und, 0) >= 100:
        n = int(s["holdings"][und] // 100)
        try:
            exp, calls = _chain(und)
            otm = calls[(calls["strike"] >= px * 1.03) & (calls["bid"] > 0)]
            if len(otm):
                row = otm.iloc[0]
                # Stale-refusal gate (funder directive 2026-08-22): premium
                # sells only on a fresh open-session quote; otherwise refuse
                # loudly and retry next tick.
                ok, why = _cc_quote_ok(row)
                if not ok:
                    msgs.append("REFUSED premium sell at bid: %s — retry"
                                " next tick" % why)
                else:
                    bid = float(row["bid"])
                    s["short_call"] = {"strike": float(row["strike"]), "expiry": exp,
                                       "premium": bid, "contracts": n,
                                       "opened": time.strftime("%Y-%m-%d")}
                    s["cash"] += (bid * 100 - 1.0) * n
                    s["costs"] = round(s["costs"] + 1.0 * n, 2)
                    s["last_rebalance"] = time.strftime("%Y-%m-%d")
                    msgs.append("sold %dx %s C%.0f @ bid %.2f (premium $%.0f)"
                                % (n, exp, row["strike"], bid, bid * 100 * n))
        except Exception as e:
            msgs.append("chain unavailable (%s)" % str(e)[:40])

    # mark: shares + cash - live call liability
    liab = 0.0
    call = s.get("short_call")
    if call:
        n = call.get("contracts", 1)
        try:
            _, calls = _chain(und, call["expiry"])
            row = calls[calls["strike"] == call["strike"]]
            if len(row):
                b, a = float(row["bid"].iloc[0]), float(row["ask"].iloc[0])
                liab = ((b + a) / 2 * 100 if a > 0
                        else float(row["lastPrice"].iloc[0]) * 100) * n
        except Exception:
            liab = call["premium"] * 100 * n
    eq = s["cash"] + s["holdings"].get(und, 0) * px - liab
    s["equity"] = round(eq, 2)
    s["peak"] = max(s.get("peak", eq), eq)
    s["dd_pct"] = round((eq / s["peak"] - 1) * 100, 2)
    if s["dd_pct"] <= KILL_DD_PCT and not s["frozen"]:
        s["frozen"] = True
        s["frozen_reason"] = "kill rule: drawdown %.1f%% <= %.0f%%" % (s["dd_pct"], KILL_DD_PCT)
    return "; ".join(msgs) if msgs else None


GLOBAL_PROBES = {"probe_japan": "DXJ", "probe_india": "INDA",
                 "probe_agri": "DBA", "probe_em_debt": "EMLC"}


def _tick_probe(etf):
    """$10k buy-and-hold premium probe: one buy, then honest marking forever."""
    def tick(s):
        px = yf_last([etf])
        if etf not in px:
            return "skipped (price)"
        if not s["holdings"]:
            cost = set_holdings(s, {etf: 1.0}, px, 10.0)
            return "bought %s (cost $%.2f) — premium probe live" % (etf, cost)
        mark(s, px)
        return None
    return tick


def tick_cef_discount(s):
    """Monthly: dollar-neutral CEF discount-to-NAV reversion (disc_z tercile
    long/short). Forward paper probe, spec hash 6d13387831117064
    (CEF_DISCOUNT_PROBE_PREREGISTRATION.md). Between rebalances: mark-only.
    All data failures degrade to mark-only; never raises into the tick."""
    import cef_probe
    if days_since(s["last_rebalance"]) < 28:
        px = cef_probe.spot_prices(list(s["holdings"]))
        if px:
            mark(s, px)
        return None
    targets, px = cef_probe.current_targets()
    if not targets:
        p = cef_probe.spot_prices(list(s["holdings"]))
        if p:
            mark(s, p)
        return "rebalance skipped (data unavailable) — marked only"
    set_holdings(s, targets, px, 25.0)
    nl = sum(1 for w in targets.values() if w > 0)
    ns = sum(1 for w in targets.values() if w < 0)
    return "rebalanced: %d long / %d short (disc_z, dollar-neutral)" % (nl, ns)


def _tick_auto_probe(probe_module, cost_bps):
    """Generic monthly tick for an autonomously-deployed probe (see
    AUTONOMOUS_DEPLOYMENT_POLICY.md / autonomous_deploy.py). Same shape as
    tick_cef_discount: monthly dollar-neutral rebalance, mark-only between,
    degrades to mark-only on any data failure."""
    def tick(s):
        import importlib
        try:
            mod = importlib.import_module(probe_module)
        except Exception as e:
            return "auto-probe module missing: %s" % str(e)[:60]
        if days_since(s["last_rebalance"]) < 28:
            px = mod.spot_prices(list(s["holdings"]))
            if px:
                mark(s, px)
            return None
        targets, px = mod.current_targets()
        if not targets:
            p = mod.spot_prices(list(s["holdings"]))
            if p:
                mark(s, p)
            return "rebalance skipped (data unavailable) — marked only"
        set_holdings(s, targets, px, cost_bps)
        nl = sum(1 for w in targets.values() if w > 0)
        ns = sum(1 for w in targets.values() if w < 0)
        return "rebalanced: %d long / %d short (auto-probe)" % (nl, ns)
    return tick


def _auto_probe_sleeves():
    """Load autonomously-deployed probes from the registry written by
    autonomous_deploy.py. Registry entries with removed=True are skipped."""
    reg = jf(os.path.join(TDIR, "auto_probes.json"), {})
    out = {}
    for name, cfg in reg.items():
        if cfg.get("removed"):
            continue
        out[name] = _tick_auto_probe(cfg.get("probe_module", name + "_probe"),
                                     float(cfg.get("cost_bps", 25.0)))
        SLEEVE_START.setdefault(name, 10000.0)
    return out


def tick_claude_discretionary(s):
    """MARK-ONLY. The discretionary book (spec 64f97dc712aa4c77,
    CLAUDE_DISCRETIONARY_BOOK_PREREGISTRATION.md) is never rebalanced by the
    tick — positions change only in supervised head-trader sessions, each
    logged to reports/claude_discretionary_log.jsonl before marking."""
    px = yf_last(list(s["holdings"]))
    if px:
        mark(s, px)
    return None


# ---------------------------------------------------------------- rally_rider
# Funder-directed 2026-08-20, implemented EXACTLY as instructed and FROZEN.
# Registered in the 2026-08-20 addendum of TOURNAMENT_PREREGISTRATION.md.
# Pre-registered expectation: the desk prior is NEGATIVE (582 momentum/trend
# trials, zero after-cost survivors; exit-axis backtest: profit-taking ties
# the null at best). This sleeve is the LIVE counter-test.
RR_UNIVERSE = ETF15 + ["XBT/USD", "ETH/USD"]
RR_MAX_POSITIONS = 4
RR_WEIGHT = 0.25              # of sleeve equity at entry
RR_LOOKBACK = 20              # trading-day high + volume-median window
RR_VOL_MULT = 1.5
RR_TP1_SIGMA = 2.0            # scale out 1/3 of initial units
RR_TP2_SIGMA = 4.0            # scale out another 1/3
RR_TRAIL_SIGMA = 2.5          # remainder: trailing stop off peak CLOSE
RR_TIME_STOP_BARS = 40        # remainder: trading-day time stop


def _rr_cost_bps(sym):
    """30 bps/side crypto, 10 bps/side ETFs — frozen."""
    return 30.0 if "/" in sym else 10.0


def _rr_settled_yf(symbols):
    """{sym: (dates, closes, volumes)} of SETTLED yfinance daily bars,
    auto_adjust=True. Settled-bar discipline copied from exit_axis: the final
    row is dropped when its date equals the current UTC date (during a live
    session it is an in-progress bar). Volume missing/NaN/zero on a bar is
    recorded as None — never guessed."""
    import yfinance as yf
    raw = yf.download(list(symbols), period="1y", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    closes_df, vol_df = raw["Close"], raw["Volume"]
    if not hasattr(closes_df, "columns"):          # single symbol -> Series
        closes_df = closes_df.to_frame(list(symbols)[0])
        vol_df = vol_df.to_frame(list(symbols)[0])
    today = time.strftime("%Y-%m-%d", time.gmtime())
    out = {}
    for sym in symbols:
        if sym not in closes_df.columns:
            continue
        col = closes_df[sym].dropna()
        dates = [d.strftime("%Y-%m-%d") for d in col.index]
        closes = [float(x) for x in col.values]
        vols = []
        for d in col.index:
            try:
                v = float(vol_df[sym].loc[d])
                vols.append(v if (v == v and v > 0) else None)
            except Exception:
                vols.append(None)
        if dates and dates[-1] == today:
            dates, closes, vols = dates[:-1], closes[:-1], vols[:-1]
        if dates:
            out[sym] = (dates, closes, vols)
    return out


def _rr_settled_kraken(symbols, n=120):
    """{sym: (dates, closes, volumes)} from Kraken public 1440-min OHLC.
    kraken_backtest.fetch_ohlc drops volume, so this hits the same public
    endpoint directly (raw row index 6 = volume). Settled-bar discipline
    copied from exit_axis: the last bar is ALWAYS the in-progress UTC day and
    is ALWAYS dropped. Dates are UTC."""
    from kraken_backtest import sym_to_pair
    out = {}
    for sym in symbols:
        try:
            url = ("https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440"
                   % sym_to_pair(sym))
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            if data.get("error"):
                raise RuntimeError(",".join(data["error"]))
            res = data.get("result", {})
            keys = [k for k in res if k != "last"]
            rows = res[keys[0]][-n:] if keys else []
        except Exception as e:
            print("ERROR: rally_rider kraken OHLC %s failed: %s" % (sym, e))
            continue
        if len(rows) < 2:
            continue
        rows = rows[:-1]                # drop in-progress UTC day — ALWAYS
        dates = [time.strftime("%Y-%m-%d", time.gmtime(float(x[0])))
                 for x in rows]
        closes = [float(x[4]) for x in rows]
        vols = []
        for x in rows:
            try:
                v = float(x[6])
                vols.append(v if (v == v and v > 0) else None)
            except Exception:
                vols.append(None)
        out[sym] = (dates, closes, vols)
        time.sleep(0.2)
    return out


def _rr_entry_signal(closes, vols):
    """(eligible, fired, reason). Both windows use bars strictly up to and
    including the deciding settled close (the last elements). '20-trading-day
    high' is the highest SETTLED CLOSE of the trailing 20 bars (settled closes
    only, per the frozen rule). Missing volume anywhere in the 20-bar window
    makes the name ineligible this bar — recorded, not guessed."""
    import statistics
    if len(closes) < RR_LOOKBACK:
        return False, False, ("insufficient history (<%d settled bars)"
                              % RR_LOOKBACK)
    w_c = closes[-RR_LOOKBACK:]
    w_v = vols[-RR_LOOKBACK:]
    if any(v is None for v in w_v):
        return False, False, "volume missing in 20-bar window"
    med = statistics.median(w_v)
    if med <= 0:
        return False, False, "20-day median volume non-positive"
    fired = (w_c[-1] >= max(w_c)) and (w_v[-1] >= RR_VOL_MULT * med)
    return True, fired, ""


def _rr_sell(s, rr, sym, units, close, reason, bar_date):
    """Book a (partial) exit at the triggering SETTLED close through the
    sleeve cost accounting — identical arithmetic to set_holdings: cost_bps
    charged on the notional traded, cash credited net, units reduced."""
    units = float(units)
    notional = units * float(close)
    cost = notional * _rr_cost_bps(sym) / 10000.0
    left = s["holdings"].get(sym, 0.0) - units
    if left <= 1e-9:
        s["holdings"].pop(sym, None)
        s["entry_px"].pop(sym, None)
    else:
        s["holdings"][sym] = left
    s["cash"] += notional - cost
    s["costs"] = round(s.get("costs", 0.0) + cost, 2)
    ev = {"sym": sym, "side": "sell", "reason": reason,
          "units": round(units, 10), "fill_close": float(close),
          "bar_date": bar_date, "cost": round(cost, 4),
          "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "at_epoch": time.time()}
    rr.setdefault("events", []).append(ev)
    rr["events"] = rr["events"][-200:]
    return cost


def tick_rally_rider(s):
    """rally_rider — funder-directed 2026-08-20, FROZEN (see the 2026-08-20
    addendum in TOURNAMENT_PREREGISTRATION.md). Daily, SETTLED closes only.

    ENTRY: settled close == 20-trading-day high of settled closes AND that
    bar's volume >= 1.5x the 20-day median volume (both windows end at the
    deciding bar). Enter at that settled close, 25% of sleeve equity; max 4
    concurrent positions; skip signals when full; never pyramid an open name.
    Signals processed in fixed universe order (ETF15 order, then XBT, ETH).

    EXITS (sigma FROZEN at entry via exit_axis.compute_entry_sigma from the
    same settled series; sigma*sqrt(bars_held) convention from exit_axis):
    scale out 1/3 of initial units at entry*(1 + 2*sigma*sqrt(bars_held)),
    another 1/3 at entry*(1 + 4*sigma*sqrt(bars_held)); remainder exits on
    trailing stop close <= peak_close*(1 - 2.5*sigma) (peak of settled
    CLOSES) or a 40-trading-day time stop. Take-profits checked before the
    stop on the same close (exit_axis ordering); no exit on the entry bar.
    ALL fills book at the triggering settled close — never the threshold,
    never the peak. Costs 10/30 bps per side on every entry and every
    partial exit. Kill: standard -30% freeze via mark()."""
    import math
    import exit_axis
    rr = s.setdefault("rr", {})
    positions = rr.setdefault("positions", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())

    def _live_mark():
        etfs = [t for t in s["holdings"] if "/" not in t]
        cryp = [t for t in s["holdings"] if "/" in t]
        px = {}
        if etfs:
            px.update({t: p for t, p in yf_last(etfs).items() if t in etfs})
        if cryp:
            px.update(kraken_last(cryp))
        mark(s, px)

    if rr.get("last_eval_utc") == today:     # one evaluation per UTC day
        _live_mark()
        return None

    # ---- settled daily series (closes + volume) for the whole universe ----
    series = {}
    try:
        series.update(_rr_settled_yf([t for t in RR_UNIVERSE if "/" not in t]))
    except Exception as e:
        msg = "ERROR: rally_rider yfinance settled fetch failed: %s" % str(e)[:120]
        print(msg)
        rr["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        series.update(_rr_settled_kraken([t for t in RR_UNIVERSE if "/" in t]))
    except Exception as e:
        msg = "ERROR: rally_rider kraken settled fetch failed: %s" % str(e)[:120]
        print(msg)
        rr["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not series:
        _live_mark()
        return "ERROR: no settled data at all — marked only, will retry"

    # every current holding MUST have a positive settled close, else refuse
    missing = [t for t in s["holdings"]
               if t not in series or not series[t][1] or series[t][1][-1] <= 0]
    if missing:
        msg = ("ERROR: rally_rider: no positive settled close for held %s — "
               "refusing to evaluate" % ", ".join(missing))
        print(msg)
        rr["state_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _live_mark()
        return msg
    rr.pop("state_error", None)
    rr["last_eval_utc"] = today
    msgs = []

    # ---- exits first, at THIS bar's settled close ----
    for gone in [t for t in positions if t not in s["holdings"]]:
        positions.pop(gone, None)
    for sym in list(s["holdings"]):
        dates, closes, _v = series[sym]
        st = positions.get(sym)
        if st is None:                    # must not happen; rebuild loudly
            print("ERROR: rally_rider: %s held with no exit state — "
                  "rebuilding (sigma unknown, time stop only)" % sym)
            st = {"entry_px": float(s["entry_px"].get(sym) or closes[-1]),
                  "entry_date": dates[-1], "entry_sigma": None,
                  "initial_units": s["holdings"][sym], "scaled": 0,
                  "peak_close": closes[-1], "bars_held": 0,
                  "last_bar_date": dates[-1],
                  "sigma_note": "state lost; rebuilt without sigma"}
            positions[sym] = st
        last = st.get("last_bar_date", st["entry_date"])
        for d, c in zip(dates, closes):   # advance over newly settled bars
            if d > last:
                st["bars_held"] = int(st.get("bars_held", 0)) + 1
                st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
                st["last_bar_date"] = d
        close, bar_date = closes[-1], dates[-1]
        bars = int(st.get("bars_held", 0))
        if bars < 1:
            continue                      # never exit on the entry bar
        sig = st.get("entry_sigma")
        entry = float(st["entry_px"])
        if sig:                           # take-profits first (exit_axis order)
            for stage, k in ((1, RR_TP1_SIGMA), (2, RR_TP2_SIGMA)):
                if (st.get("scaled", 0) < stage and sym in s["holdings"]
                        and close >= entry * (1.0 + k * sig * math.sqrt(bars))):
                    u = min(float(st["initial_units"]) / 3.0,
                            s["holdings"][sym])
                    cost = _rr_sell(s, rr, sym, u, close,
                                    "scale_out_%.0fsigma" % k, bar_date)
                    st["scaled"] = stage
                    msgs.append("SCALE-OUT 1/3 %s at settled close %.6g "
                                "(%s, %.0f-sigma leg, cost $%.2f)"
                                % (sym, close, bar_date, k, cost))
        stop = None
        if sig and close <= float(st["peak_close"]) * (1.0 - RR_TRAIL_SIGMA * sig):
            stop = "trailing_stop"
        if stop is None and bars >= RR_TIME_STOP_BARS:
            stop = "time_stop"
        if stop and sym in s["holdings"]:
            cost = _rr_sell(s, rr, sym, s["holdings"][sym], close, stop,
                            bar_date)
            positions.pop(sym, None)
            msgs.append("EXIT remainder %s at settled close %.6g (%s, %s, "
                        "cost $%.2f)" % (sym, close, bar_date, stop, cost))

    # ---- entries, at THIS bar's settled close, fixed universe order ----
    ineligible = {}
    for sym in RR_UNIVERSE:
        if sym in s["holdings"]:
            continue                      # never pyramid an open name
        if sym not in series or not series[sym][1]:
            ineligible[sym] = "no settled data this bar"
            continue
        dates, closes, vols = series[sym]
        ok, fired, why = _rr_entry_signal(closes, vols)
        if not ok:
            ineligible[sym] = why
            continue
        if not fired:
            continue
        if len(s["holdings"]) >= RR_MAX_POSITIONS:
            msgs.append("signal %s SKIPPED (max %d positions)"
                        % (sym, RR_MAX_POSITIONS))
            continue
        close, bar_date = closes[-1], dates[-1]
        if _firm_halt_blocks_entry(s, sym):         # CM-047
            continue
        eq = mark(s, {t: series[t][1][-1] for t in s["holdings"]})
        want = eq * RR_WEIGHT
        units = want / close
        cost = want * _rr_cost_bps(sym) / 10000.0
        s["holdings"][sym] = units
        s["entry_px"][sym] = float(close)
        s["cash"] -= want + cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        s["last_rebalance"] = time.strftime("%Y-%m-%d")
        sigma = exit_axis.compute_entry_sigma(closes)
        st = {"entry_px": float(close), "entry_date": bar_date,
              "entry_sigma": sigma, "initial_units": units, "scaled": 0,
              "peak_close": float(close), "bars_held": 0,
              "last_bar_date": bar_date,
              "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if sigma is None:
            st["sigma_note"] = ("EXIT-EXEMPT from sigma legs: <%d settled "
                                "returns at entry; only the %d-bar time stop "
                                "governs" % (exit_axis.SIGMA_MIN_RETURNS,
                                             RR_TIME_STOP_BARS))
            print("ERROR: rally_rider: %s entered with sigma unavailable — "
                  "only the time stop governs (recorded)" % sym)
        positions[sym] = st
        ev = {"sym": sym, "side": "buy", "reason": "20d_high_1.5x_vol",
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "cost": round(cost, 4),
              "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "at_epoch": time.time()}
        rr.setdefault("events", []).append(ev)
        rr["events"] = rr["events"][-200:]
        msgs.append("ENTER %s at settled close %.6g (%s) 25%% eq $%.0f "
                    "(cost $%.2f, sigma %s)"
                    % (sym, close, bar_date, want, cost,
                       ("%.5f" % sigma) if sigma else "None"))

    rr["ineligible"] = ineligible         # recorded, never guessed
    mark(s, {t: series[t][1][-1] for t in s["holdings"]})
    return "; ".join(msgs) if msgs else None


# ---------------------------------------------------------------- rally_rider_x
# Funder-directed 2026-08-21 ("seal it and launch it"), implemented EXACTLY as
# instructed and FROZEN. Registered in the 2026-08-21 addendum of
# TOURNAMENT_PREREGISTRATION.md. The BREADTH experiment: the same sealed
# entry/exit ladder as rally_rider, testing the ONE axis its $10k sibling
# cannot — 10 concurrent rallies vs 4, a ~34-name universe vs 17.
# Pre-registered expectation: the momentum-family prior is NEGATIVE (house
# receipts; the spec_rally lab found even liquid rally-riding does not beat
# holding its own names).
RRX_UNIVERSE = ETF15 + [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
    "DIA", "MDY", "VNQ", "EWJ", "FXI", "INDA",           # deep-liquid ETFs
    "XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD"]          # crypto majors
RRX_MAX_POSITIONS = 10
RRX_WEIGHT = 0.10             # slice of sleeve equity at entry (NET of costs)
RRX_LOOKBACK = 20             # trading-day high + volume-median window
RRX_VOL_MULT = 1.5
RRX_TP1_SIGMA = 2.0           # scale out 1/3 of initial units
RRX_TP2_SIGMA = 4.0           # scale out another 1/3
RRX_TRAIL_SIGMA = 2.5         # remainder: trailing stop off peak CLOSE
RRX_TIME_STOP_BARS = 40       # remainder: trading-day time stop


def _rrx_settled_kraken(symbols, n=120):
    """rally_rider_x twin of _rr_settled_kraken — own copy ONLY so failure
    lines name the right sleeve (the frozen original must not be edited).
    Same public endpoint, same settled-bar discipline: the last 1440-min bar
    is ALWAYS the in-progress UTC day and is ALWAYS dropped. Dates are UTC."""
    from kraken_backtest import sym_to_pair
    out = {}
    for sym in symbols:
        try:
            url = ("https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440"
                   % sym_to_pair(sym))
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            if data.get("error"):
                raise RuntimeError(",".join(data["error"]))
            res = data.get("result", {})
            keys = [k for k in res if k != "last"]
            rows = res[keys[0]][-n:] if keys else []
        except Exception as e:
            print("ERROR: rally_rider_x kraken OHLC %s failed: %s" % (sym, e))
            continue
        if len(rows) < 2:
            continue
        rows = rows[:-1]                # drop in-progress UTC day — ALWAYS
        dates = [time.strftime("%Y-%m-%d", time.gmtime(float(x[0])))
                 for x in rows]
        closes = [float(x[4]) for x in rows]
        vols = []
        for x in rows:
            try:
                v = float(x[6])
                vols.append(v if (v == v and v > 0) else None)
            except Exception:
                vols.append(None)
        out[sym] = (dates, closes, vols)
        time.sleep(0.2)
    return out


def _rrx_entry_signal(closes, vols):
    """(eligible, fired, vol_ratio, reason). Same frozen windows as
    _rr_entry_signal, plus the DECLARED priority statistic: vol_ratio =
    deciding-bar volume / 20-day median volume. When qualifying signals
    exceed free slots, highest vol_ratio enters first (declared, frozen).
    Missing volume anywhere in the 20-bar window makes the name ineligible
    this bar — recorded, not guessed."""
    import statistics
    if len(closes) < RRX_LOOKBACK:
        return False, False, None, ("insufficient history (<%d settled bars)"
                                    % RRX_LOOKBACK)
    w_c = closes[-RRX_LOOKBACK:]
    w_v = vols[-RRX_LOOKBACK:]
    if any(v is None for v in w_v):
        return False, False, None, "volume missing in 20-bar window"
    med = statistics.median(w_v)
    if med <= 0:
        return False, False, None, "20-day median volume non-positive"
    ratio = w_v[-1] / med
    fired = (w_c[-1] >= max(w_c)) and (ratio >= RRX_VOL_MULT)
    return True, fired, ratio, ""


def tick_rally_rider_x(s):
    """rally_rider_x — funder-directed 2026-08-21, FROZEN (see the 2026-08-21
    addendum in TOURNAMENT_PREREGISTRATION.md). $100,000 paper. Daily,
    SETTLED closes only — the BREADTH counter-test of rally_rider.

    ENTRY (identical trigger to rally_rider): settled close == 20-trading-day
    high of settled closes AND that bar's volume >= 1.5x the 20-day median
    volume (both windows end at the deciding bar). Enter at that settled
    close. Slice = 10% of sleeve equity at entry, sized NET OF COSTS —
    notional chosen so notional + cost equals the allocation exactly, and the
    allocation is capped at free cash, so cash can NEVER go negative (the v2
    accounting improvement disclosed in the rally_rider addendum). Max 10
    concurrent positions; never pyramid an open name. When qualifying signals
    exceed free slots, priority = highest volume-ratio (declared, frozen).

    EXITS (identical sealed ladder; sigma FROZEN at entry via
    exit_axis.compute_entry_sigma from the same settled series, imported
    never copied; sigma*sqrt(bars_held) convention from exit_axis): scale out
    1/3 of initial units at entry*(1 + 2*sigma*sqrt(bars_held)), another 1/3
    at entry*(1 + 4*sigma*sqrt(bars_held)); remainder exits on trailing stop
    close <= peak_close*(1 - 2.5*sigma) (peak of settled CLOSES) or a
    40-trading-day time stop. Take-profits checked before the stop on the
    same close (exit_axis ordering); no exit on the entry bar. ALL fills book
    at the triggering settled close. Costs 10/30 bps per side on every entry
    and every partial exit. Kill: standard -30% freeze via mark()."""
    import math
    import exit_axis
    rrx = s.setdefault("rr_x", {})
    positions = rrx.setdefault("positions", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())

    def _live_mark():
        etfs = [t for t in s["holdings"] if "/" not in t]
        cryp = [t for t in s["holdings"] if "/" in t]
        px = {}
        if etfs:
            px.update({t: p for t, p in yf_last(etfs).items() if t in etfs})
        if cryp:
            px.update(kraken_last(cryp))
        mark(s, px)

    if rrx.get("last_eval_utc") == today:    # one evaluation per UTC day
        _live_mark()
        return None

    # ---- settled daily series (closes + volume) for the whole universe ----
    series = {}
    try:
        series.update(_rr_settled_yf([t for t in RRX_UNIVERSE if "/" not in t]))
    except Exception as e:
        msg = ("ERROR: rally_rider_x yfinance settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        rrx["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        series.update(_rrx_settled_kraken([t for t in RRX_UNIVERSE if "/" in t]))
    except Exception as e:
        msg = ("ERROR: rally_rider_x kraken settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        rrx["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not series:
        _live_mark()
        return ("ERROR: rally_rider_x: no settled data at all — marked only, "
                "will retry")

    # every current holding MUST have a positive settled close, else refuse
    missing = [t for t in s["holdings"]
               if t not in series or not series[t][1] or series[t][1][-1] <= 0]
    if missing:
        msg = ("ERROR: rally_rider_x: no positive settled close for held %s — "
               "refusing to evaluate" % ", ".join(missing))
        print(msg)
        rrx["state_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _live_mark()
        return msg
    rrx.pop("state_error", None)
    rrx["last_eval_utc"] = today
    msgs = []

    # ---- exits first, at THIS bar's settled close ----
    for gone in [t for t in positions if t not in s["holdings"]]:
        positions.pop(gone, None)
    for sym in list(s["holdings"]):
        dates, closes, _v = series[sym]
        st = positions.get(sym)
        if st is None:                    # must not happen; rebuild loudly
            print("ERROR: rally_rider_x: %s held with no exit state — "
                  "rebuilding (sigma unknown, time stop only)" % sym)
            st = {"entry_px": float(s["entry_px"].get(sym) or closes[-1]),
                  "entry_date": dates[-1], "entry_sigma": None,
                  "initial_units": s["holdings"][sym], "scaled": 0,
                  "peak_close": closes[-1], "bars_held": 0,
                  "last_bar_date": dates[-1],
                  "sigma_note": "state lost; rebuilt without sigma"}
            positions[sym] = st
        last = st.get("last_bar_date", st["entry_date"])
        for d, c in zip(dates, closes):   # advance over newly settled bars
            if d > last:
                st["bars_held"] = int(st.get("bars_held", 0)) + 1
                st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
                st["last_bar_date"] = d
        close, bar_date = closes[-1], dates[-1]
        bars = int(st.get("bars_held", 0))
        if bars < 1:
            continue                      # never exit on the entry bar
        sig = st.get("entry_sigma")
        entry = float(st["entry_px"])
        if sig:                           # take-profits first (exit_axis order)
            for stage, k in ((1, RRX_TP1_SIGMA), (2, RRX_TP2_SIGMA)):
                if (st.get("scaled", 0) < stage and sym in s["holdings"]
                        and close >= entry * (1.0 + k * sig * math.sqrt(bars))):
                    u = min(float(st["initial_units"]) / 3.0,
                            s["holdings"][sym])
                    cost = _rr_sell(s, rrx, sym, u, close,
                                    "scale_out_%.0fsigma" % k, bar_date)
                    st["scaled"] = stage
                    msgs.append("SCALE-OUT 1/3 %s at settled close %.6g "
                                "(%s, %.0f-sigma leg, cost $%.2f)"
                                % (sym, close, bar_date, k, cost))
        stop = None
        if sig and close <= float(st["peak_close"]) * (1.0 - RRX_TRAIL_SIGMA * sig):
            stop = "trailing_stop"
        if stop is None and bars >= RRX_TIME_STOP_BARS:
            stop = "time_stop"
        if stop and sym in s["holdings"]:
            cost = _rr_sell(s, rrx, sym, s["holdings"][sym], close, stop,
                            bar_date)
            positions.pop(sym, None)
            msgs.append("EXIT remainder %s at settled close %.6g (%s, %s, "
                        "cost $%.2f)" % (sym, close, bar_date, stop, cost))

    # ---- entries, at THIS bar's settled close; priority = volume-ratio ----
    ineligible = {}
    fired = []
    for sym in RRX_UNIVERSE:
        if sym in s["holdings"]:
            continue                      # never pyramid an open name
        if sym not in series or not series[sym][1]:
            ineligible[sym] = "no settled data this bar"
            continue
        _dates, closes, vols = series[sym]
        ok, hit, ratio, why = _rrx_entry_signal(closes, vols)
        if not ok:
            ineligible[sym] = why
            continue
        if hit:
            fired.append((float(ratio), sym))
    fired.sort(key=lambda kv: (-kv[0], kv[1]))   # highest vol-ratio first
    for ratio, sym in fired:
        if len(s["holdings"]) >= RRX_MAX_POSITIONS:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (max %d positions)"
                        % (sym, ratio, RRX_MAX_POSITIONS))
            continue
        dates, closes, _vols = series[sym]
        close, bar_date = closes[-1], dates[-1]
        eq = mark(s, {t: series[t][1][-1] for t in s["holdings"]})
        alloc = eq * RRX_WEIGHT
        if s["cash"] < alloc:             # NET-OF-COSTS guarantee: cash >= 0
            msgs.append("alloc for %s capped by free cash ($%.2f < $%.2f)"
                        % (sym, s["cash"], alloc))
            alloc = s["cash"]
        if alloc <= 0:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (no free cash)"
                        % (sym, ratio))
            continue
        if _firm_halt_blocks_entry(s, sym):         # CM-047
            continue
        fee = _rr_cost_bps(sym) / 10000.0
        notional = alloc / (1.0 + fee)
        cost = alloc - notional           # notional + cost == alloc EXACTLY
        units = notional / close
        s["holdings"][sym] = units
        s["entry_px"][sym] = float(close)
        s["cash"] -= notional + cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        s["last_rebalance"] = time.strftime("%Y-%m-%d")
        sigma = exit_axis.compute_entry_sigma(closes)
        st = {"entry_px": float(close), "entry_date": bar_date,
              "entry_sigma": sigma, "initial_units": units, "scaled": 0,
              "peak_close": float(close), "bars_held": 0,
              "last_bar_date": bar_date, "entry_vol_ratio": round(ratio, 4),
              "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if sigma is None:
            st["sigma_note"] = ("EXIT-EXEMPT from sigma legs: <%d settled "
                                "returns at entry; only the %d-bar time stop "
                                "governs" % (exit_axis.SIGMA_MIN_RETURNS,
                                             RRX_TIME_STOP_BARS))
            print("ERROR: rally_rider_x: %s entered with sigma unavailable — "
                  "only the time stop governs (recorded)" % sym)
        positions[sym] = st
        ev = {"sym": sym, "side": "buy", "reason": "20d_high_1.5x_vol",
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "cost": round(cost, 4),
              "vol_ratio": round(ratio, 4),
              "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "at_epoch": time.time()}
        rrx.setdefault("events", []).append(ev)
        rrx["events"] = rrx["events"][-400:]
        msgs.append("ENTER %s at settled close %.6g (%s) 10%% eq $%.0f "
                    "net-of-costs (notional $%.2f, cost $%.2f, vol-ratio "
                    "%.2f, sigma %s)"
                    % (sym, close, bar_date, alloc, notional, cost, ratio,
                       ("%.5f" % sigma) if sigma else "None"))

    rrx["ineligible"] = ineligible        # recorded, never guessed
    mark(s, {t: series[t][1][-1] for t in s["holdings"]})
    if s["cash"] < 0:                     # must be impossible — say so loudly
        print("ERROR: rally_rider_x: cash negative ($%.6f) after tick — "
              "net-of-costs invariant violated" % s["cash"])
        rrx["cash_error"] = {"cash": s["cash"], "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return "; ".join(msgs) if msgs else None


SLEEVES = {
    "covered_call_etf": tick_covered_call_etf,
    "vix_regime_switch": tick_vix_regime_switch,
    "lowvol_crypto": tick_lowvol_crypto,
    "fng_momentum": tick_fng_momentum,
    "equal_weight_all": tick_equal_weight_all,
    "spy_trend_cash": tick_spy_trend_cash,
    "cef_discount": tick_cef_discount,
    "claude_discretionary": tick_claude_discretionary,
    "rally_rider": tick_rally_rider,
    **{name: _tick_probe(etf) for name, etf in GLOBAL_PROBES.items()},
    **_auto_probe_sleeves(),
}

# rally_rider_x registration — append-only (the SLEEVES literal and the
# SLEEVE_START literal above are untouched; the frozen files rule): dispatch
# entry + the funder-directed $100,000 SLEEVE_START, 2026-08-21 addendum.
SLEEVES["rally_rider_x"] = tick_rally_rider_x
SLEEVE_START["rally_rider_x"] = 100000.0


# ------------------------------------------- rally_rider_hold / dip_rider
# Funder-directed 2026-08-22 ("do it"), implemented EXACTLY as instructed and
# FROZEN. Registered in the 2026-08-22 addendum of
# TOURNAMENT_PREREGISTRATION.md. Together with the live siblings these
# complete the 2x2 experiment:
#   strength entry / bank thirds  = rally_rider       (live 2026-08-20)
#   strength entry / LET IT RIDE  = rally_rider_hold  (sleeve A, below)
#   WEAKNESS entry / bank thirds  = dip_rider         (sleeve B, below)
#   null cell                     = buy-and-hold controls at review
# rally_rider_hold isolates the EXIT axis (same entries as rally_rider; every
# performance difference is attributable to the exit rule alone). dip_rider
# isolates the ENTRY axis (exact inversion of rally_rider's entry, identical
# management). Priors, stated honestly in the addendum: the spec-rally and
# exit-axis labs found letting winners run >= scale-outs net (mildly positive
# prior for A); the mean-reversion family died in backtests and a 20d closing
# low on heavy volume is often a falling knife (NEGATIVE prior for B). Both
# are funder-directed live counter-tests, judged only by the 182-day record.
RRH_UNIVERSE = ETF15 + ["XBT/USD", "ETH/USD"]   # identical to rally_rider
RRH_MAX_POSITIONS = 4
RRH_WEIGHT = 0.25             # slice of sleeve equity at entry (NET of costs)
RRH_LOOKBACK = 20             # same frozen windows as _rrx_entry_signal
RRH_VOL_MULT = 1.5
RRH_TRAIL_SIGMA = 2.5         # the ONLY sigma exit: trail off peak settled close
RRH_TIME_STOP_BARS = 40       # the only other exit — NO profit-taking legs

DR_UNIVERSE = ETF15 + ["XBT/USD", "ETH/USD"]    # identical to rally_rider
DR_MAX_POSITIONS = 4
DR_WEIGHT = 0.25              # slice of sleeve equity at entry (NET of costs)
DR_LOOKBACK = 20              # trading-day LOW + volume-median window
DR_VOL_MULT = 1.5
DR_TP1_SIGMA = 2.0            # full rally_rider ladder, unchanged
DR_TP2_SIGMA = 4.0
DR_TRAIL_SIGMA = 2.5
DR_TIME_STOP_BARS = 40


def _sleeve_settled_kraken(sleeve, symbols, n=120):
    """Kraken settled-daily fetch for the 2026-08-22 sleeves — ONE shared
    helper parameterised by sleeve name so failure lines still name the right
    sleeve (the frozen per-sleeve copies above must never be edited). Same
    public endpoint, same settled-bar discipline as the siblings: the last
    1440-min bar is ALWAYS the in-progress UTC day and is ALWAYS dropped.
    Dates are UTC."""
    from kraken_backtest import sym_to_pair
    out = {}
    for sym in symbols:
        try:
            url = ("https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440"
                   % sym_to_pair(sym))
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            if data.get("error"):
                raise RuntimeError(",".join(data["error"]))
            res = data.get("result", {})
            keys = [k for k in res if k != "last"]
            rows = res[keys[0]][-n:] if keys else []
        except Exception as e:
            print("ERROR: %s kraken OHLC %s failed: %s" % (sleeve, sym, e))
            continue
        if len(rows) < 2:
            continue
        rows = rows[:-1]                # drop in-progress UTC day — ALWAYS
        dates = [time.strftime("%Y-%m-%d", time.gmtime(float(x[0])))
                 for x in rows]
        closes = [float(x[4]) for x in rows]
        vols = []
        for x in rows:
            try:
                v = float(x[6])
                vols.append(v if (v == v and v > 0) else None)
            except Exception:
                vols.append(None)
        out[sym] = (dates, closes, vols)
        time.sleep(0.2)
    return out


def _dr_entry_signal(closes, vols):
    """(eligible, fired, vol_ratio, reason) — the EXACT INVERSION of the
    rally_rider entry: fires when the settled close equals the LOWEST settled
    close of the trailing 20 trading days AND the deciding bar's volume >=
    1.5x the 20-day median (a volume flush on the dip). Both windows use bars
    strictly up to and including the deciding settled close. vol_ratio is the
    declared slot-priority statistic. Missing volume anywhere in the 20-bar
    window makes the name ineligible this bar — recorded, not guessed."""
    import statistics
    if len(closes) < DR_LOOKBACK:
        return False, False, None, ("insufficient history (<%d settled bars)"
                                    % DR_LOOKBACK)
    w_c = closes[-DR_LOOKBACK:]
    w_v = vols[-DR_LOOKBACK:]
    if any(v is None for v in w_v):
        return False, False, None, "volume missing in 20-bar window"
    med = statistics.median(w_v)
    if med <= 0:
        return False, False, None, "20-day median volume non-positive"
    ratio = w_v[-1] / med
    fired = (w_c[-1] <= min(w_c)) and (ratio >= DR_VOL_MULT)
    return True, fired, ratio, ""


def tick_rally_rider_hold(s):
    """rally_rider_hold — funder-directed 2026-08-22 ("do it"), FROZEN (see
    the 2026-08-22 addendum in TOURNAMENT_PREREGISTRATION.md). $10,000 paper.
    Daily, SETTLED closes only. The LET-IT-RIDE twin of rally_rider: the EXIT
    AXIS isolated — same entries, bank-thirds vs let-ride.

    ENTRY (identical trigger to rally_rider, via _rrx_entry_signal whose
    frozen 20-bar / 1.5x windows equal RRH_LOOKBACK / RRH_VOL_MULT): settled
    close == 20-trading-day high of settled closes AND that bar's volume >=
    1.5x the 20-day median volume (both windows end at the deciding bar).
    Enter at that settled close. Slice = 25% of sleeve equity at entry, sized
    NET OF COSTS (v2 accounting: notional = alloc / (1 + fee), allocation
    capped at free cash, cash can NEVER go negative). Max 4 concurrent; never
    pyramid an open name; when qualifying signals exceed free slots, priority
    = highest volume-ratio (declared, frozen; ties break alphabetically).

    EXITS — NO profit-taking whatsoever. ONLY the trailing stop close <=
    peak_close*(1 - 2.5*sigma) (peak of settled CLOSES) and the 40-bar time
    stop. Sigma FROZEN at entry via exit_axis.compute_entry_sigma (imported,
    never copied). No exit on the entry bar; ALL fills book at the triggering
    settled close. Costs 10/30 bps per side on every fill. Kill: standard
    -30% freeze via mark()."""
    import exit_axis
    rrh = s.setdefault("rr_hold", {})
    positions = rrh.setdefault("positions", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())

    def _live_mark():
        etfs = [t for t in s["holdings"] if "/" not in t]
        cryp = [t for t in s["holdings"] if "/" in t]
        px = {}
        if etfs:
            px.update({t: p for t, p in yf_last(etfs).items() if t in etfs})
        if cryp:
            px.update(kraken_last(cryp))
        mark(s, px)

    if rrh.get("last_eval_utc") == today:    # one evaluation per UTC day
        _live_mark()
        return None

    # ---- settled daily series (closes + volume) for the whole universe ----
    series = {}
    try:
        series.update(_rr_settled_yf([t for t in RRH_UNIVERSE if "/" not in t]))
    except Exception as e:
        msg = ("ERROR: rally_rider_hold yfinance settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        rrh["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        series.update(_sleeve_settled_kraken(
            "rally_rider_hold", [t for t in RRH_UNIVERSE if "/" in t]))
    except Exception as e:
        msg = ("ERROR: rally_rider_hold kraken settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        rrh["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not series:
        _live_mark()
        return ("ERROR: rally_rider_hold: no settled data at all — marked "
                "only, will retry")

    # every current holding MUST have a positive settled close, else refuse
    missing = [t for t in s["holdings"]
               if t not in series or not series[t][1] or series[t][1][-1] <= 0]
    if missing:
        msg = ("ERROR: rally_rider_hold: no positive settled close for held "
               "%s — refusing to evaluate" % ", ".join(missing))
        print(msg)
        rrh["state_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _live_mark()
        return msg
    rrh.pop("state_error", None)
    rrh["last_eval_utc"] = today
    msgs = []

    # ---- exits first, at THIS bar's settled close — trail/time ONLY ----
    for gone in [t for t in positions if t not in s["holdings"]]:
        positions.pop(gone, None)
    for sym in list(s["holdings"]):
        dates, closes, _v = series[sym]
        st = positions.get(sym)
        if st is None:                    # must not happen; rebuild loudly
            print("ERROR: rally_rider_hold: %s held with no exit state — "
                  "rebuilding (sigma unknown, time stop only)" % sym)
            st = {"entry_px": float(s["entry_px"].get(sym) or closes[-1]),
                  "entry_date": dates[-1], "entry_sigma": None,
                  "initial_units": s["holdings"][sym],
                  "peak_close": closes[-1], "bars_held": 0,
                  "last_bar_date": dates[-1],
                  "sigma_note": "state lost; rebuilt without sigma"}
            positions[sym] = st
        last = st.get("last_bar_date", st["entry_date"])
        for d, c in zip(dates, closes):   # advance over newly settled bars
            if d > last:
                st["bars_held"] = int(st.get("bars_held", 0)) + 1
                st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
                st["last_bar_date"] = d
        close, bar_date = closes[-1], dates[-1]
        bars = int(st.get("bars_held", 0))
        if bars < 1:
            continue                      # never exit on the entry bar
        sig = st.get("entry_sigma")
        stop = None
        if sig and close <= float(st["peak_close"]) * (1.0 - RRH_TRAIL_SIGMA * sig):
            stop = "trailing_stop"
        if stop is None and bars >= RRH_TIME_STOP_BARS:
            stop = "time_stop"
        if stop and sym in s["holdings"]:
            cost = _rr_sell(s, rrh, sym, s["holdings"][sym], close, stop,
                            bar_date)
            positions.pop(sym, None)
            msgs.append("EXIT (let-ride) %s at settled close %.6g (%s, %s, "
                        "cost $%.2f)" % (sym, close, bar_date, stop, cost))

    # ---- entries, at THIS bar's settled close; priority = volume-ratio ----
    ineligible = {}
    fired = []
    for sym in RRH_UNIVERSE:
        if sym in s["holdings"]:
            continue                      # never pyramid an open name
        if sym not in series or not series[sym][1]:
            ineligible[sym] = "no settled data this bar"
            continue
        _dates, closes, vols = series[sym]
        ok, hit, ratio, why = _rrx_entry_signal(closes, vols)
        if not ok:
            ineligible[sym] = why
            continue
        if hit:
            fired.append((float(ratio), sym))
    fired.sort(key=lambda kv: (-kv[0], kv[1]))   # highest vol-ratio first
    for ratio, sym in fired:
        if len(s["holdings"]) >= RRH_MAX_POSITIONS:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (max %d positions)"
                        % (sym, ratio, RRH_MAX_POSITIONS))
            continue
        dates, closes, _vols = series[sym]
        close, bar_date = closes[-1], dates[-1]
        eq = mark(s, {t: series[t][1][-1] for t in s["holdings"]})
        alloc = eq * RRH_WEIGHT
        if s["cash"] < alloc:             # NET-OF-COSTS guarantee: cash >= 0
            msgs.append("alloc for %s capped by free cash ($%.2f < $%.2f)"
                        % (sym, s["cash"], alloc))
            alloc = s["cash"]
        if alloc <= 0:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (no free cash)"
                        % (sym, ratio))
            continue
        if _firm_halt_blocks_entry(s, sym):         # CM-047
            continue
        fee = _rr_cost_bps(sym) / 10000.0
        notional = alloc / (1.0 + fee)
        cost = alloc - notional           # notional + cost == alloc EXACTLY
        units = notional / close
        s["holdings"][sym] = units
        s["entry_px"][sym] = float(close)
        s["cash"] -= notional + cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        s["last_rebalance"] = time.strftime("%Y-%m-%d")
        sigma = exit_axis.compute_entry_sigma(closes)
        st = {"entry_px": float(close), "entry_date": bar_date,
              "entry_sigma": sigma, "initial_units": units,
              "peak_close": float(close), "bars_held": 0,
              "last_bar_date": bar_date, "entry_vol_ratio": round(ratio, 4),
              "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if sigma is None:
            st["sigma_note"] = ("EXIT-EXEMPT from the sigma trail: <%d "
                                "settled returns at entry; only the %d-bar "
                                "time stop governs"
                                % (exit_axis.SIGMA_MIN_RETURNS,
                                   RRH_TIME_STOP_BARS))
            print("ERROR: rally_rider_hold: %s entered with sigma "
                  "unavailable — only the time stop governs (recorded)" % sym)
        positions[sym] = st
        ev = {"sym": sym, "side": "buy", "reason": "20d_high_1.5x_vol",
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "cost": round(cost, 4),
              "vol_ratio": round(ratio, 4),
              "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "at_epoch": time.time()}
        rrh.setdefault("events", []).append(ev)
        rrh["events"] = rrh["events"][-200:]
        msgs.append("ENTER %s at settled close %.6g (%s) 25%% eq $%.0f "
                    "net-of-costs (notional $%.2f, cost $%.2f, vol-ratio "
                    "%.2f, sigma %s)"
                    % (sym, close, bar_date, alloc, notional, cost, ratio,
                       ("%.5f" % sigma) if sigma else "None"))

    rrh["ineligible"] = ineligible        # recorded, never guessed
    mark(s, {t: series[t][1][-1] for t in s["holdings"]})
    if s["cash"] < 0:                     # must be impossible — say so loudly
        print("ERROR: rally_rider_hold: cash negative ($%.6f) after tick — "
              "net-of-costs invariant violated" % s["cash"])
        rrh["cash_error"] = {"cash": s["cash"], "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return "; ".join(msgs) if msgs else None


def tick_dip_rider(s):
    """dip_rider — funder-directed 2026-08-22 ("do it"), FROZEN (see the
    2026-08-22 addendum in TOURNAMENT_PREREGISTRATION.md). $10,000 paper.
    Daily, SETTLED closes only. The MIRROR-ENTRY twin of rally_rider: the
    ENTRY AXIS isolated — buying 20d weakness vs 20d strength with identical
    management. Honest prior: NEGATIVE (the mean-reversion family died in
    backtests; a 20d closing low on heavy volume is often a falling knife).

    ENTRY (the EXACT INVERSION of rally_rider's): settled close == LOWEST
    settled close of the trailing 20 trading days AND that bar's volume >=
    1.5x the 20-day median volume — a volume flush on the dip (both windows
    end at the deciding bar). Enter at that settled close. Slice = 25% of
    sleeve equity at entry, sized NET OF COSTS (v2 accounting: notional =
    alloc / (1 + fee), allocation capped at free cash, cash can NEVER go
    negative). Max 4 concurrent; never pyramid an open name; when qualifying
    signals exceed free slots, priority = highest volume-ratio (declared,
    frozen; ties break alphabetically).

    EXITS (the FULL rally_rider ladder; sigma FROZEN at entry via
    exit_axis.compute_entry_sigma, imported never copied; sigma*sqrt(bars)
    convention from exit_axis): scale out 1/3 of initial units at
    entry*(1 + 2*sigma*sqrt(bars_held)), another 1/3 at
    entry*(1 + 4*sigma*sqrt(bars_held)); remainder exits on trailing stop
    close <= peak_close*(1 - 2.5*sigma) (peak of settled CLOSES) or a 40-bar
    time stop. Take-profits checked before the stop on the same close
    (exit_axis ordering); no exit on the entry bar; ALL fills book at the
    triggering settled close. Costs 10/30 bps per side on every fill incl.
    partial exits. Kill: standard -30% freeze via mark()."""
    import math
    import exit_axis
    dr = s.setdefault("dip", {})
    positions = dr.setdefault("positions", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())

    def _live_mark():
        etfs = [t for t in s["holdings"] if "/" not in t]
        cryp = [t for t in s["holdings"] if "/" in t]
        px = {}
        if etfs:
            px.update({t: p for t, p in yf_last(etfs).items() if t in etfs})
        if cryp:
            px.update(kraken_last(cryp))
        mark(s, px)

    if dr.get("last_eval_utc") == today:     # one evaluation per UTC day
        _live_mark()
        return None

    # ---- settled daily series (closes + volume) for the whole universe ----
    series = {}
    try:
        series.update(_rr_settled_yf([t for t in DR_UNIVERSE if "/" not in t]))
    except Exception as e:
        msg = ("ERROR: dip_rider yfinance settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        dr["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        series.update(_sleeve_settled_kraken(
            "dip_rider", [t for t in DR_UNIVERSE if "/" in t]))
    except Exception as e:
        msg = ("ERROR: dip_rider kraken settled fetch failed: %s"
               % str(e)[:120])
        print(msg)
        dr["data_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not series:
        _live_mark()
        return ("ERROR: dip_rider: no settled data at all — marked only, "
                "will retry")

    # every current holding MUST have a positive settled close, else refuse
    missing = [t for t in s["holdings"]
               if t not in series or not series[t][1] or series[t][1][-1] <= 0]
    if missing:
        msg = ("ERROR: dip_rider: no positive settled close for held %s — "
               "refusing to evaluate" % ", ".join(missing))
        print(msg)
        dr["state_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _live_mark()
        return msg
    dr.pop("state_error", None)
    dr["last_eval_utc"] = today
    msgs = []

    # ---- exits first, at THIS bar's settled close — full ladder ----
    for gone in [t for t in positions if t not in s["holdings"]]:
        positions.pop(gone, None)
    for sym in list(s["holdings"]):
        dates, closes, _v = series[sym]
        st = positions.get(sym)
        if st is None:                    # must not happen; rebuild loudly
            print("ERROR: dip_rider: %s held with no exit state — "
                  "rebuilding (sigma unknown, time stop only)" % sym)
            st = {"entry_px": float(s["entry_px"].get(sym) or closes[-1]),
                  "entry_date": dates[-1], "entry_sigma": None,
                  "initial_units": s["holdings"][sym], "scaled": 0,
                  "peak_close": closes[-1], "bars_held": 0,
                  "last_bar_date": dates[-1],
                  "sigma_note": "state lost; rebuilt without sigma"}
            positions[sym] = st
        last = st.get("last_bar_date", st["entry_date"])
        for d, c in zip(dates, closes):   # advance over newly settled bars
            if d > last:
                st["bars_held"] = int(st.get("bars_held", 0)) + 1
                st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
                st["last_bar_date"] = d
        close, bar_date = closes[-1], dates[-1]
        bars = int(st.get("bars_held", 0))
        if bars < 1:
            continue                      # never exit on the entry bar
        sig = st.get("entry_sigma")
        entry = float(st["entry_px"])
        if sig:                           # take-profits first (exit_axis order)
            for stage, k in ((1, DR_TP1_SIGMA), (2, DR_TP2_SIGMA)):
                if (st.get("scaled", 0) < stage and sym in s["holdings"]
                        and close >= entry * (1.0 + k * sig * math.sqrt(bars))):
                    u = min(float(st["initial_units"]) / 3.0,
                            s["holdings"][sym])
                    cost = _rr_sell(s, dr, sym, u, close,
                                    "scale_out_%.0fsigma" % k, bar_date)
                    st["scaled"] = stage
                    msgs.append("SCALE-OUT 1/3 %s at settled close %.6g "
                                "(%s, %.0f-sigma leg, cost $%.2f)"
                                % (sym, close, bar_date, k, cost))
        stop = None
        if sig and close <= float(st["peak_close"]) * (1.0 - DR_TRAIL_SIGMA * sig):
            stop = "trailing_stop"
        if stop is None and bars >= DR_TIME_STOP_BARS:
            stop = "time_stop"
        if stop and sym in s["holdings"]:
            cost = _rr_sell(s, dr, sym, s["holdings"][sym], close, stop,
                            bar_date)
            positions.pop(sym, None)
            msgs.append("EXIT remainder %s at settled close %.6g (%s, %s, "
                        "cost $%.2f)" % (sym, close, bar_date, stop, cost))

    # ---- entries, at THIS bar's settled close; priority = volume-ratio ----
    ineligible = {}
    fired = []
    for sym in DR_UNIVERSE:
        if sym in s["holdings"]:
            continue                      # never pyramid an open name
        if sym not in series or not series[sym][1]:
            ineligible[sym] = "no settled data this bar"
            continue
        _dates, closes, vols = series[sym]
        ok, hit, ratio, why = _dr_entry_signal(closes, vols)
        if not ok:
            ineligible[sym] = why
            continue
        if hit:
            fired.append((float(ratio), sym))
    fired.sort(key=lambda kv: (-kv[0], kv[1]))   # highest vol-ratio first
    for ratio, sym in fired:
        if len(s["holdings"]) >= DR_MAX_POSITIONS:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (max %d positions)"
                        % (sym, ratio, DR_MAX_POSITIONS))
            continue
        dates, closes, _vols = series[sym]
        close, bar_date = closes[-1], dates[-1]
        eq = mark(s, {t: series[t][1][-1] for t in s["holdings"]})
        alloc = eq * DR_WEIGHT
        if s["cash"] < alloc:             # NET-OF-COSTS guarantee: cash >= 0
            msgs.append("alloc for %s capped by free cash ($%.2f < $%.2f)"
                        % (sym, s["cash"], alloc))
            alloc = s["cash"]
        if alloc <= 0:
            msgs.append("signal %s (vol-ratio %.2f) SKIPPED (no free cash)"
                        % (sym, ratio))
            continue
        if _firm_halt_blocks_entry(s, sym):         # CM-047
            continue
        fee = _rr_cost_bps(sym) / 10000.0
        notional = alloc / (1.0 + fee)
        cost = alloc - notional           # notional + cost == alloc EXACTLY
        units = notional / close
        s["holdings"][sym] = units
        s["entry_px"][sym] = float(close)
        s["cash"] -= notional + cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        s["last_rebalance"] = time.strftime("%Y-%m-%d")
        sigma = exit_axis.compute_entry_sigma(closes)
        st = {"entry_px": float(close), "entry_date": bar_date,
              "entry_sigma": sigma, "initial_units": units, "scaled": 0,
              "peak_close": float(close), "bars_held": 0,
              "last_bar_date": bar_date, "entry_vol_ratio": round(ratio, 4),
              "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if sigma is None:
            st["sigma_note"] = ("EXIT-EXEMPT from sigma legs: <%d settled "
                                "returns at entry; only the %d-bar time stop "
                                "governs" % (exit_axis.SIGMA_MIN_RETURNS,
                                             DR_TIME_STOP_BARS))
            print("ERROR: dip_rider: %s entered with sigma unavailable — "
                  "only the time stop governs (recorded)" % sym)
        positions[sym] = st
        ev = {"sym": sym, "side": "buy", "reason": "20d_low_1.5x_vol",
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "cost": round(cost, 4),
              "vol_ratio": round(ratio, 4),
              "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "at_epoch": time.time()}
        dr.setdefault("events", []).append(ev)
        dr["events"] = dr["events"][-200:]
        msgs.append("ENTER (dip) %s at settled close %.6g (%s) 25%% eq $%.0f "
                    "net-of-costs (notional $%.2f, cost $%.2f, vol-ratio "
                    "%.2f, sigma %s)"
                    % (sym, close, bar_date, alloc, notional, cost, ratio,
                       ("%.5f" % sigma) if sigma else "None"))

    dr["ineligible"] = ineligible         # recorded, never guessed
    mark(s, {t: series[t][1][-1] for t in s["holdings"]})
    if s["cash"] < 0:                     # must be impossible — say so loudly
        print("ERROR: dip_rider: cash negative ($%.6f) after tick — "
              "net-of-costs invariant violated" % s["cash"])
        dr["cash_error"] = {"cash": s["cash"], "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return "; ".join(msgs) if msgs else None


# rally_rider_hold + dip_rider registration — append-only (the SLEEVES and
# SLEEVE_START literals above are untouched; the frozen-files rule): dispatch
# entries + explicit $10,000 SLEEVE_START stamps. NOTE: the module default
# START is already 10000.0, so the two SLEEVE_START lines are redundant in
# effect but explicit on purpose — the 2026-08-22 addendum registers both
# sleeves at $10,000 and the registration should not depend on a default.
SLEEVES["rally_rider_hold"] = tick_rally_rider_hold
SLEEVES["dip_rider"] = tick_dip_rider
SLEEVE_START["rally_rider_hold"] = 10000.0
SLEEVE_START["dip_rider"] = 10000.0



# ======================================================================== #
# capacity_probe — funder-directed 2026-08-24 ("commit $100k paper and run
# it for 8 weeks"). FROZEN by the 2026-08-24 addendum in
# TOURNAMENT_PREREGISTRATION.md. APPEND-ONLY: nothing above this line was
# edited; every live sibling tick function is byte-identical.
#
# PURPOSE, in the order the funder asked for it:
#   1. CAPACITY AND COST MEASUREMENT (primary). Committing $100k to the
#      sub-$1M/day perp corner, HOW MUCH can actually be deployed, at WHAT
#      realized cost, and what is the capacity ceiling in $/day?
#   2. EDGE TEST (secondary). NOT claimed at 8 weeks. 182 days minimum.
# Refusals are the dataset, not a failure.
# ======================================================================== #

# The thin PF roster, read LIVE from desk_healer.py's COLLECTORS[
# "orderbook_thin"] argv so code and collector can never drift apart. If the
# read fails the sleeve refuses to trade rather than guess a universe.
CP_ROSTER_FALLBACK_REASON = None


def _cp_roster():
    """The 20-symbol thin roster, parsed out of desk_healer.COLLECTORS.

    Single source of truth: the collector's own --symbols argv. Returns
    (symbols, error_or_None). Never invents a universe."""
    global CP_ROSTER_FALLBACK_REASON
    try:
        import importlib
        if BASE not in sys.path:          # so the import works when tournament
            sys.path.insert(0, BASE)      # is imported, not just run as a script
        dh = importlib.import_module("desk_healer")
        argv = dh.COLLECTORS["orderbook_thin"][1]
        i = argv.index("--symbols")
        syms = [x.strip() for x in argv[i + 1].split(",") if x.strip()]
        if not syms:
            raise ValueError("--symbols parsed empty")
        CP_ROSTER_FALLBACK_REASON = None
        return syms, None
    except Exception as e:
        msg = ("ERROR: capacity_probe: cannot read the thin roster from "
               "desk_healer.COLLECTORS['orderbook_thin'] argv: %s" % str(e)[:140])
        CP_ROSTER_FALLBACK_REASON = msg
        return [], msg


# ---- frozen constants (2026-08-24 addendum; changing any is a rule change)
CP_SLEEVE_CAPITAL = 100000.0
CP_MAX_POSITIONS = 10             # target concurrent positions
CP_WEIGHT = 0.10                  # target clip = 10% of sleeve equity
CP_DEPTH_FRACTION = 0.25          # clip must be <= 25% of same-side depth
CP_MAX_SNAP_AGE_S = 900           # 15 min — Non-negotiable #7, no stale fills
CP_MAX_SIGNAL_AGE_H = 24.0        # signal older than its own horizon is dead
CP_MIN_BREADTH = 6                # min symbols with a live signal to rank at all
CP_TOP_QUINTILE = 0.20            # long the top quintile by cvd_slope
CP_TAKER_FEE_BPS = 5.0            # Kraken PF bottom-tier taker, 0.05%/side
CP_TRAIL_SIGMA = 2.5              # trailing stop off peak SETTLED close
CP_TIME_STOP_BARS = 40            # time stop, settled daily bars
CP_FALLBACK_EXIT_COST_BPS = 30.0  # declared, flagged, exits only (never entries)
CP_ZROLL_W = 48                   # futures_panel_lab.zroll default window
CP_ZROLL_MIN = 12                 # futures_panel_lab.zroll min obs
CP_CVD_DIFF_K = 2                 # futures_panel_lab: sig["cvd_slope"]=zroll(diff(cvd,2))
CP_OB_DIR = os.path.join(KRAKEN_DESK, "reports", "microstructure")
CP_PANEL_DB = os.path.join(KRAKEN_DESK, "kraken_bot", "market_intelligence.sqlite")
CP_CAPACITY_VERDICT_DATE = "2026-10-19"   # 8 weeks — capacity/cost only
CP_EDGE_VERDICT_MIN_DAYS = 182            # edge verdict not before this


def _cp_utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------- order-book depth
def _cp_latest_snapshots(symbols):
    """{sym: snapshot dict} — the most recent order-book snapshot per symbol
    from reports/microstructure/ob_perp_thin_*.jsonl (the thin collector's own
    output stream). Reads only the tail of the newest file(s); malformed lines
    are counted, never guessed at. Returns (snaps, error_or_None)."""
    import glob
    want = set(symbols)
    snaps, bad = {}, 0
    try:
        files = sorted(glob.glob(os.path.join(CP_OB_DIR, "ob_perp_thin_*.jsonl")))
        if not files:
            return {}, ("ERROR: capacity_probe: no ob_perp_thin_*.jsonl in %s — "
                        "the thin order-book collector is not producing data"
                        % CP_OB_DIR)
        for path in reversed(files[-3:]):     # newest first, at most 3 files
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 4 * 1024 * 1024))
                chunk = f.read()
            lines = chunk.split(b"\n")[1:] if size > 4 * 1024 * 1024 else chunk.split(b"\n")
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    bad += 1
                    continue
                sym = d.get("sym")
                if sym not in want:
                    continue
                if sym not in snaps or float(d.get("t", 0)) > float(snaps[sym].get("t", 0)):
                    snaps[sym] = d
            if len(snaps) >= len(want):
                break
    except Exception as e:
        return snaps, ("ERROR: capacity_probe: order-book read failed: %s"
                       % str(e)[:140])
    if bad:
        print("ERROR: capacity_probe: %d malformed order-book lines skipped "
              "(recorded, never guessed)" % bad)
    return snaps, None


def _cp_book(snap, side):
    """(mid, spread_bps, top_of_book_usd, depth10_usd, age_s) for the side we
    must CROSS. Buying crosses the ASK, selling crosses the BID. Depth fields
    in the snapshot are BASE units (10-level sums); converted at the snapshot
    mid. Returns None on any unusable field — never a substituted number."""
    try:
        mid = float(snap["mid"])
        spread = float(snap["spread_bps"])
        if side == "buy":
            l1 = float(snap["ask_sz0"]); d10 = float(snap["ask_depth"])
        else:
            l1 = float(snap["bid_sz0"]); d10 = float(snap["bid_depth"])
        age = time.time() - float(snap["t"])
        if not (mid > 0 and spread >= 0 and l1 >= 0 and d10 >= 0):
            return None
        return (mid, spread, l1 * mid, d10 * mid, age)
    except Exception:
        return None


def _cp_cost_bps(clip_usd, spread_bps, l1_usd, d10_usd):
    """(total_cost_bps, spread_component, impact_component) for ONE side.

    MEASURED, never assumed:
      * spread  = spread_bps / 2  (crossing the touch; the round trip pays the
                  full measured spread, exactly the convention of
                  reports/research_liquidity_floor.md).
      * fee     = CP_TAKER_FEE_BPS (0.05%/side, Kraken PF bottom tier).
      * impact  = DECLARED depth-walk approximation, flagged as such in the
                  registration: zero while the clip stays inside top-of-book,
                  then LINEAR in the notional consumed beyond it, reaching one
                  full measured spread of extra slippage when the clip would
                  consume the entire visible 10-level book. It uses only the
                  three measured quantities the collector records (L1, 10-level
                  depth, spread) — the snapshot carries no per-level prices, so
                  a true level-by-level walk is not computable from this feed.
                  The depth gate caps the clip at 25% of the book, so impact is
                  bounded at ~0.25 x spread by construction."""
    impact = 0.0
    if clip_usd > l1_usd and d10_usd > l1_usd:
        impact = spread_bps * (clip_usd - l1_usd) / (d10_usd - l1_usd)
    elif clip_usd > l1_usd:               # degenerate book: L1 == 10-level
        impact = spread_bps
    return (spread_bps / 2.0 + impact + CP_TAKER_FEE_BPS,
            spread_bps / 2.0, impact)


# ------------------------------------------------- settled daily prices
def _cp_settled_daily(symbols, n=140):
    """{sym: (dates, closes)} of SETTLED daily bars for the PF instrument
    ITSELF — Kraken public futures charts, the same contract the depth gate
    measures, so price and book can never describe different instruments.
    The final candle is ALWAYS the in-progress UTC day and is ALWAYS dropped
    (settled-bar discipline, copied from _rrx_settled_kraken)."""
    out = {}
    for sym in symbols:
        try:
            url = ("https://futures.kraken.com/api/charts/v1/trade/%s/1d" % sym)
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
            rows = data.get("candles") or []
        except Exception as e:
            print("ERROR: capacity_probe futures OHLC %s failed: %s"
                  % (sym, str(e)[:100]))
            continue
        if len(rows) < 2:
            continue
        rows = rows[-(n + 1):][:-1]       # drop in-progress UTC day — ALWAYS
        try:
            dates = [time.strftime("%Y-%m-%d", time.gmtime(float(x["time"]) / 1000.0))
                     for x in rows]
            closes = [float(x["close"]) for x in rows]
        except Exception as e:
            print("ERROR: capacity_probe: malformed candle for %s: %s"
                  % (sym, str(e)[:80]))
            continue
        if any(c <= 0 for c in closes):
            print("ERROR: capacity_probe: non-positive settled close for %s — "
                  "symbol dropped this bar" % sym)
            continue
        out[sym] = (dates, closes)
        time.sleep(0.12)
    return out


# ------------------------------------------------------------- the signal
def _cp_zroll_last(x):
    """The LAST value of futures_panel_lab.zroll(x) — causal rolling z-score,
    window 48, min 12 finite obs. Replicated faithfully in pure python from
    futures_panel_lab.py:167-175 (`def zroll(x,w=48)`); the lab imports numpy
    and this module deliberately does not."""
    s = [v for v in x[-CP_ZROLL_W:] if v is not None]
    if len(s) < CP_ZROLL_MIN or x[-1] is None:
        return None
    n = len(s)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / (n - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    return (x[-1] - mean) / sd


def _cp_cvd_slope(symbols):
    """{sym: (cvd_slope, latest_bucket_epoch, n_buckets)} — the panel signal.

    EXACTLY futures_panel_lab.py's definition, cited:
        build_signals(): sig["cvd_slope"] = zroll(diff(cvd, 2))   (line 206)
        diff(x,k):  out[k:] = x[k:] - x[:-k]                      (line 183)
        zroll(x,w=48): causal rolling z-score, min 12 obs         (line 167)
    The panel's cvd is a 30-min bucket series, so diff(cvd,2) is the 1-hour
    change in cumulative volume delta and the z-score window is 24 hours.
    Buckets are reconstructed with the same payload handling as
    futures_panel_lab STAGE build_panel (SELECT only, never a write).
    Returns (signals, error_or_None)."""
    import sqlite3
    import collections as _c
    out = {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % CP_PANEL_DB, uri=True)
    except Exception as e:
        return {}, ("ERROR: capacity_probe: cannot open the futures_analytics "
                    "panel read-only at %s: %s" % (CP_PANEL_DB, str(e)[:100]))
    try:
        for sym in symbols:
            ser = {}
            try:
                rows = con.execute(
                    "SELECT payload_json FROM futures_analytics "
                    "WHERE metric='cvd' AND futures_symbol=?", (sym,)).fetchall()
            except Exception as e:
                con.close()
                return out, ("ERROR: capacity_probe: futures_analytics SELECT "
                             "failed: %s" % str(e)[:100])
            for (pj,) in rows:
                try:
                    d = json.loads(pj)
                except Exception:
                    continue
                ts = d.get("timestamp") or []
                vals = (d.get("data") or {}).get("cvd") or []
                if not ts or len(vals) != len(ts):
                    continue
                for t, v in zip(ts, vals):
                    try:
                        t = int(t)
                        t = t // 1000 if t > 1e11 else t
                        v = float(v)
                    except Exception:
                        continue
                    if not (v == v and 1700000000 < t < 1800000000):
                        continue
                    ser[t] = v
            if not ser:
                continue
            keys = sorted(ser)
            cvd = [ser[k] for k in keys]
            if len(cvd) <= CP_CVD_DIFF_K:
                continue
            dif = [None] * CP_CVD_DIFF_K + [
                cvd[i] - cvd[i - CP_CVD_DIFF_K] for i in range(CP_CVD_DIFF_K, len(cvd))]
            z = _cp_zroll_last(dif)
            if z is None:
                continue
            out[sym] = (z, keys[-1], len(cvd))
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out, None


def tick_capacity_probe(s):
    """capacity_probe — funder-directed 2026-08-24, FROZEN (see the 2026-08-24
    addendum in TOURNAMENT_PREREGISTRATION.md). $100,000 paper, LONG/FLAT only.

    PRIMARY: capacity and cost MEASUREMENT of the sub-$1M/day perp corner.
    SECONDARY: an edge test whose verdict is NOT claimed before 182 days.

    UNIVERSE: the 20-symbol thin PF roster, read live from desk_healer's
    COLLECTORS["orderbook_thin"] argv (code and collector cannot drift).

    SIGNAL: cvd_slope = zroll(diff(cvd,2)) exactly as futures_panel_lab.py
    defines it; long the TOP QUINTILE, flat otherwise. Shorts are excluded —
    the desk's shorts prior is negative. A symbol whose newest cvd bucket is
    older than 24h is DEAD this bar (a signal older than its own horizon).
    Fewer than 6 live signals => no ranking at all, recorded loudly.

    DEPTH-GATED FILLS (the load-bearing mechanism): required clip = target
    notional (10% of equity). The fill is ALLOWED only if clip <= 25% of the
    measured same-side book depth AND the snapshot is < 15 minutes old
    (Non-negotiable #7). Otherwise it is REFUSED and logged with the reason,
    the shortfall, and the size that WOULD have been fillable. Refusals are
    the primary dataset.

    REALIZED COST, measured per fill: half the measured spread (crossing) +
    the declared depth-walk impact term + 5 bps taker. Recorded per fill:
    intended size, filled size, spread bps, impact bps, total bps.

    EXITS: 2.5-sigma trailing stop off peak SETTLED close, 40-bar time stop,
    sigma FROZEN at entry via exit_axis.compute_entry_sigma. NO scale-outs
    (keeps the capacity question clean). Exits are NEVER depth-gated — a risk
    control must not be blocked by a data gap; a stale or missing snapshot on
    an exit is priced and FLAGGED, never skipped.

    CAPACITY SCAN: every tick records the measured book for every roster
    symbol whether or not it trades. That is the deliverable the 8-week clock
    judges. Kill: freeze at -30% drawdown. Clocks: capacity/cost verdict
    2026-10-19; edge verdict not before 182 days."""
    import math
    import exit_axis
    cp = s.setdefault("cap_probe", {})
    positions = cp.setdefault("positions", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())
    cp["purpose"] = ("PRIMARY: capacity + realized-cost measurement. "
                     "SECONDARY: edge test, NOT claimed before %d days."
                     % CP_EDGE_VERDICT_MIN_DAYS)
    cp["capacity_verdict_due"] = CP_CAPACITY_VERDICT_DATE
    cp["edge_verdict_min_days"] = CP_EDGE_VERDICT_MIN_DAYS

    roster, rerr = _cp_roster()
    if rerr:
        print(rerr)
        cp["roster_error"] = {"msg": rerr, "at_utc": _cp_utcnow()}
        mark(s, {t: s["entry_px"].get(t, 0.0) for t in s["holdings"]})
        return rerr
    cp.pop("roster_error", None)
    cp["roster"] = roster
    cp["roster_n"] = len(roster)

    if cp.get("last_eval_utc") == today:      # one evaluation per UTC day
        snaps, _ = _cp_latest_snapshots(list(s["holdings"]) or roster)
        px = {}
        for t in s["holdings"]:
            b = _cp_book(snaps.get(t) or {}, "sell")
            if b:
                px[t] = b[0]
        mark(s, px)
        return None

    # ---------------- data: book, prices, signal ----------------
    snaps, oberr = _cp_latest_snapshots(roster)
    if oberr:
        print(oberr)
        cp["book_error"] = {"msg": oberr, "at_utc": _cp_utcnow()}
    else:
        cp.pop("book_error", None)

    series = _cp_settled_daily(sorted(set(roster) | set(s["holdings"])))
    if not series:
        msg = ("ERROR: capacity_probe: no settled daily data at all — marked "
               "only, will retry")
        print(msg)
        cp["data_error"] = {"msg": msg, "at_utc": _cp_utcnow()}
        mark(s, {t: s["entry_px"].get(t, 0.0) for t in s["holdings"]})
        return msg
    cp.pop("data_error", None)

    missing = [t for t in s["holdings"] if t not in series]
    if missing:
        msg = ("ERROR: capacity_probe: no positive settled close for held %s — "
               "refusing to evaluate" % ", ".join(sorted(missing)))
        print(msg)
        cp["state_error"] = {"msg": msg, "at_utc": _cp_utcnow()}
        mark(s, {t: s["entry_px"].get(t, 0.0) for t in s["holdings"]})
        return msg
    cp.pop("state_error", None)

    sigs, sigerr = _cp_cvd_slope(roster)
    if sigerr:
        print(sigerr)
        cp["signal_error"] = {"msg": sigerr, "at_utc": _cp_utcnow()}
    else:
        cp.pop("signal_error", None)

    cp["last_eval_utc"] = today
    msgs = []
    now = time.time()

    # ---------------- CAPACITY SCAN — every roster symbol, every tick -------
    # This is the primary deliverable and it accrues whether or not the sleeve
    # trades. Nothing here is modelled: spread, top-of-book and 10-level depth
    # are read straight out of the collector's snapshot.
    eq_now = mark(s, {t: series[t][1][-1] for t in s["holdings"] if t in series})
    target_clip = eq_now * CP_WEIGHT
    scan = {}
    for sym in roster:
        row = {"target_clip_usd": round(target_clip, 2)}
        snap = snaps.get(sym)
        if not snap:
            row["status"] = "no_snapshot"
            scan[sym] = row
            continue
        b = _cp_book(snap, "buy")
        if not b:
            row["status"] = "unusable_snapshot_fields"
            scan[sym] = row
            continue
        mid, spread, l1, d10, age = b
        fillable = CP_DEPTH_FRACTION * d10
        tot, sp, imp = _cp_cost_bps(min(target_clip, fillable), spread, l1, d10)
        row.update({
            "status": "ok" if age <= CP_MAX_SNAP_AGE_S else "stale_snapshot",
            "snapshot_age_s": round(age, 1), "mid": mid,
            "spread_bps": round(spread, 4),
            "top_of_book_usd": round(l1, 2),
            "depth10_usd": round(d10, 2),
            "max_fillable_usd": round(fillable, 2),
            "clip_fits": bool(target_clip <= fillable),
            "shortfall_usd": round(max(0.0, target_clip - fillable), 2),
            "rt_cost_bps_at_max_fillable": round(2.0 * tot, 3),
            "cost_spread_bps": round(sp, 3), "cost_impact_bps": round(imp, 3),
            "cost_fee_bps": CP_TAKER_FEE_BPS,
        })
        scan[sym] = row
    cp["capacity_scan"] = scan
    cp["capacity_scan_at_utc"] = _cp_utcnow()
    fits = [k for k, v in scan.items() if v.get("clip_fits")]
    ok_rows = [v for v in scan.values() if v.get("status") == "ok"]
    cp["capacity_summary"] = {
        "roster_n": len(roster), "with_fresh_book": len(ok_rows),
        "clip_fits_n": len(fits), "target_clip_usd": round(target_clip, 2),
        "sum_max_fillable_usd": round(
            sum(v.get("max_fillable_usd", 0.0) for v in ok_rows), 2),
        "median_spread_bps": (sorted(v["spread_bps"] for v in ok_rows)[len(ok_rows) // 2]
                              if ok_rows else None),
        "median_depth10_usd": (sorted(v["depth10_usd"] for v in ok_rows)[len(ok_rows) // 2]
                               if ok_rows else None),
    }

    # ---------------- EXITS FIRST (never depth-gated) ----------------
    for gone in [t for t in positions if t not in s["holdings"]]:
        positions.pop(gone, None)
    for sym in list(s["holdings"]):
        dates, closes = series[sym]
        st = positions.get(sym)
        if st is None:
            print("ERROR: capacity_probe: %s held with no exit state — "
                  "rebuilding (sigma unknown, time stop only)" % sym)
            st = {"entry_px": float(s["entry_px"].get(sym) or closes[-1]),
                  "entry_date": dates[-1], "entry_sigma": None,
                  "initial_units": s["holdings"][sym],
                  "peak_close": closes[-1], "bars_held": 0,
                  "last_bar_date": dates[-1],
                  "sigma_note": "state lost; rebuilt without sigma"}
            positions[sym] = st
        last = st.get("last_bar_date", st["entry_date"])
        for d, c in zip(dates, closes):
            if d > last:
                st["bars_held"] = int(st.get("bars_held", 0)) + 1
                st["peak_close"] = max(float(st.get("peak_close", c)), float(c))
                st["last_bar_date"] = d
        close, bar_date = closes[-1], dates[-1]
        bars = int(st.get("bars_held", 0))
        if bars < 1:
            continue                       # never exit on the entry bar
        sig = st.get("entry_sigma")
        stop = None
        if sig and close <= float(st["peak_close"]) * (1.0 - CP_TRAIL_SIGMA * sig):
            stop = "trailing_stop"
        if stop is None and bars >= CP_TIME_STOP_BARS:
            stop = "time_stop"
        if not stop:
            continue
        units = s["holdings"][sym]
        notional = units * float(close)
        b = _cp_book(snaps.get(sym) or {}, "sell")
        flag = None
        if b and b[4] <= CP_MAX_SNAP_AGE_S:
            tot, spc, impc = _cp_cost_bps(notional, b[1], b[2], b[3])
        elif b:
            tot, spc, impc = _cp_cost_bps(notional, b[1], b[2], b[3])
            flag = "cost_from_stale_snapshot_age_%ds" % int(b[4])
        else:
            tot, spc, impc = CP_FALLBACK_EXIT_COST_BPS, None, None
            flag = "no_snapshot_declared_fallback_%.0fbps" % CP_FALLBACK_EXIT_COST_BPS
        if flag:
            print("ERROR: capacity_probe: exit %s priced without a fresh book "
                  "(%s) — exit taken anyway, risk control is never blocked"
                  % (sym, flag))
        cost = notional * tot / 10000.0
        s["holdings"].pop(sym, None)
        s["entry_px"].pop(sym, None)
        s["cash"] += notional - cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        positions.pop(sym, None)
        ev = {"sym": sym, "side": "sell", "reason": stop,
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "notional_usd": round(notional, 2),
              "cost_usd": round(cost, 4), "realized_cost_bps": round(tot, 3),
              "spread_bps": (round(spc, 3) if spc is not None else None),
              "impact_bps": (round(impc, 3) if impc is not None else None),
              "fee_bps": CP_TAKER_FEE_BPS, "cost_flag": flag,
              "at_utc": _cp_utcnow(), "at_epoch": time.time()}
        cp.setdefault("fills", []).append(ev)
        cp["fills"] = cp["fills"][-400:]
        msgs.append("EXIT %s at settled close %.6g (%s, %s, $%.2f notional, "
                    "cost %.1f bps%s)"
                    % (sym, close, bar_date, stop, notional, tot,
                       "" if not flag else " [" + flag + "]"))

    # ---------------- SIGNAL: rank, top quintile ----------------
    live, dead = {}, {}
    for sym in roster:
        if sym not in sigs:
            dead[sym] = "no cvd in the futures_analytics panel"
            continue
        z, tsl, n = sigs[sym]
        age_h = (now - tsl) / 3600.0
        if age_h > CP_MAX_SIGNAL_AGE_H:
            dead[sym] = ("cvd stale %.1fh > %.0fh horizon (newest bucket %s)"
                         % (age_h, CP_MAX_SIGNAL_AGE_H,
                            time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(tsl))))
            continue
        if sym not in series:
            dead[sym] = "no settled daily close this bar"
            continue
        live[sym] = {"cvd_slope": round(z, 6), "cvd_age_h": round(age_h, 2),
                     "n_buckets": n}
    cp["signal_live"] = live
    cp["signal_dead"] = dead
    cp["signal_breadth"] = len(live)

    longs = []
    if len(live) < CP_MIN_BREADTH:
        msg = ("ERROR: capacity_probe: signal breadth %d < %d — NO ranking, NO "
               "entries this bar. cvd_slope is a cross-sectional top-quintile "
               "rule and a quintile of %d is not a cross-section. The panel "
               "collector does not cover the thin roster (see the 2026-08-24 "
               "addendum, PREREQUISITE)." % (len(live), CP_MIN_BREADTH, len(live)))
        print(msg)
        cp["breadth_error"] = {"msg": msg, "breadth": len(live),
                               "required": CP_MIN_BREADTH, "at_utc": _cp_utcnow()}
        msgs.append("NO ENTRIES: signal breadth %d < %d" % (len(live), CP_MIN_BREADTH))
    else:
        cp.pop("breadth_error", None)
        ranked = sorted(live.items(), key=lambda kv: (-kv[1]["cvd_slope"], kv[0]))
        k = max(1, int(math.ceil(len(ranked) * CP_TOP_QUINTILE)))
        longs = [sym for sym, _ in ranked[:k]]
        cp["top_quintile"] = longs
        cp["ranked"] = [(sym, v["cvd_slope"]) for sym, v in ranked]

    # ---------------- ENTRIES: depth-gated. Refusals ARE the dataset -------
    refusals = {}
    for sym in longs:
        if sym in s["holdings"]:
            refusals[sym] = {"reason": "already_held_never_pyramid"}
            continue
        if len(s["holdings"]) >= CP_MAX_POSITIONS:
            refusals[sym] = {"reason": "max_positions",
                             "max": CP_MAX_POSITIONS}
            continue
        eq = mark(s, {t: series[t][1][-1] for t in s["holdings"] if t in series})
        alloc = eq * CP_WEIGHT
        if s["cash"] < alloc:
            alloc = s["cash"]
        if alloc <= 0:
            refusals[sym] = {"reason": "no_free_cash"}
            continue
        snap = snaps.get(sym)
        if not snap:
            refusals[sym] = {"reason": "no_orderbook_snapshot",
                             "intended_usd": round(alloc, 2),
                             "fillable_usd": 0.0,
                             "shortfall_usd": round(alloc, 2)}
            continue
        b = _cp_book(snap, "buy")
        if not b:
            refusals[sym] = {"reason": "unusable_snapshot_fields",
                             "intended_usd": round(alloc, 2)}
            continue
        mid, spread, l1, d10, age = b
        if age > CP_MAX_SNAP_AGE_S:
            refusals[sym] = {"reason": "stale_snapshot",
                             "snapshot_age_s": round(age, 1),
                             "max_age_s": CP_MAX_SNAP_AGE_S,
                             "intended_usd": round(alloc, 2),
                             "fillable_usd": round(CP_DEPTH_FRACTION * d10, 2),
                             "note": "Non-negotiable #7: no stale fills"}
            continue
        fillable = CP_DEPTH_FRACTION * d10
        if alloc > fillable:
            refusals[sym] = {"reason": "depth_gate",
                             "intended_usd": round(alloc, 2),
                             "fillable_usd": round(fillable, 2),
                             "shortfall_usd": round(alloc - fillable, 2),
                             "depth10_usd": round(d10, 2),
                             "depth_fraction": CP_DEPTH_FRACTION,
                             "spread_bps": round(spread, 4),
                             "snapshot_age_s": round(age, 1)}
            continue
        # ---- ALLOWED. Net-of-costs sizing: notional + cost == alloc exactly
        tot, spc, impc = _cp_cost_bps(alloc, spread, l1, d10)
        fee = tot / 10000.0
        notional = alloc / (1.0 + fee)
        tot2, spc, impc = _cp_cost_bps(notional, spread, l1, d10)
        fee = tot2 / 10000.0
        notional = alloc / (1.0 + fee)
        cost = alloc - notional
        close, bar_date = series[sym][1][-1], series[sym][0][-1]
        if _firm_halt_blocks_entry(s, sym):         # CM-047
            continue
        units = notional / close
        s["holdings"][sym] = units
        s["entry_px"][sym] = float(close)
        s["cash"] -= notional + cost
        s["costs"] = round(s.get("costs", 0.0) + cost, 2)
        s["last_rebalance"] = time.strftime("%Y-%m-%d")
        sigma = exit_axis.compute_entry_sigma(series[sym][1])
        st = {"entry_px": float(close), "entry_date": bar_date,
              "entry_sigma": sigma, "initial_units": units,
              "peak_close": float(close), "bars_held": 0,
              "last_bar_date": bar_date,
              "entry_cvd_slope": live[sym]["cvd_slope"],
              "opened_utc": _cp_utcnow()}
        if sigma is None:
            st["sigma_note"] = ("EXIT-EXEMPT from the sigma stop: <%d settled "
                                "returns at entry; only the %d-bar time stop "
                                "governs" % (exit_axis.SIGMA_MIN_RETURNS,
                                             CP_TIME_STOP_BARS))
            print("ERROR: capacity_probe: %s entered with sigma unavailable — "
                  "only the time stop governs (recorded)" % sym)
        positions[sym] = st
        ev = {"sym": sym, "side": "buy", "reason": "cvd_slope_top_quintile",
              "units": round(units, 10), "fill_close": float(close),
              "bar_date": bar_date, "intended_usd": round(alloc, 2),
              "filled_usd": round(notional, 2), "cost_usd": round(cost, 4),
              "realized_cost_bps": round(tot2, 3),
              "spread_bps": round(spc, 3), "impact_bps": round(impc, 3),
              "fee_bps": CP_TAKER_FEE_BPS,
              "depth10_usd": round(d10, 2),
              "max_fillable_usd": round(fillable, 2),
              "snapshot_age_s": round(age, 1),
              "cvd_slope": live[sym]["cvd_slope"],
              "at_utc": _cp_utcnow(), "at_epoch": time.time()}
        cp.setdefault("fills", []).append(ev)
        cp["fills"] = cp["fills"][-400:]
        msgs.append("ENTER %s at settled close %.6g (%s) $%.2f net-of-costs "
                    "(filled $%.2f, cost %.1f bps = %.1f spread + %.1f impact "
                    "+ %.1f fee, book $%.0f, cvd_slope %.3f)"
                    % (sym, close, bar_date, alloc, notional, tot2, spc, impc,
                       CP_TAKER_FEE_BPS, d10, live[sym]["cvd_slope"]))

    cp["refusals"] = refusals
    cp["refusals_at_utc"] = _cp_utcnow()
    if refusals:
        hist = cp.setdefault("refusal_history", [])
        hist.append({"date": today, "n": len(refusals),
                     "by_reason": {r: sum(1 for v in refusals.values()
                                          if v.get("reason") == r)
                                   for r in sorted(set(v.get("reason")
                                                       for v in refusals.values()))}})
        cp["refusal_history"] = hist[-200:]
        for sym, r in sorted(refusals.items()):
            msgs.append("REFUSED %s (%s%s)"
                        % (sym, r.get("reason"),
                           "" if r.get("shortfall_usd") is None else
                           ", short $%.2f of $%.2f, fillable $%.2f"
                           % (r["shortfall_usd"], r.get("intended_usd", 0.0),
                              r.get("fillable_usd", 0.0))))

    # ---------------- deployment: the number the 8-week clock judges --------
    eq = mark(s, {t: series[t][1][-1] for t in s["holdings"] if t in series})
    invested = sum(u * series[t][1][-1] for t, u in s["holdings"].items()
                   if t in series)
    cp["deployed_usd"] = round(invested, 2)
    cp["deployed_pct_of_equity"] = round(100.0 * invested / eq, 3) if eq else None
    cp["cash_usd"] = round(s["cash"], 2)
    if s["cash"] < 0:
        print("ERROR: capacity_probe: cash negative ($%.6f) after tick — "
              "net-of-costs invariant violated" % s["cash"])
        cp["cash_error"] = {"cash": s["cash"], "at_utc": _cp_utcnow()}
    return "; ".join(msgs) if msgs else None


# capacity_probe registration — append-only (the SLEEVES literal and the
# SLEEVE_START literal above are untouched; the frozen-files rule): dispatch
# entry + the funder-directed $100,000 SLEEVE_START, 2026-08-24 addendum.
SLEEVES["capacity_probe"] = tick_capacity_probe
SLEEVE_START["capacity_probe"] = CP_SLEEVE_CAPITAL

# ------------------------------------------------------- shareholder_yield
# Funder-directed 2026-08-27 ("TRADE $40K ON THE FUNDAMENTALS BOOK, FREE REIGN
# TO DEPLOY"), implemented EXACTLY as instructed and FROZEN. Registered in the
# 2026-08-27 addendum of TOURNAMENT_PREREGISTRATION.md.
#
# PRE-REGISTERED PRIOR (verbatim spirit): the value/quality/factor family is the
# most heavily falsified family on this desk — 360 cross-sectional hypotheses,
# the equity-factor lab ("beta not alpha"), defensive factors (the apparent
# survivor was de-levered beta with NEGATIVE excess vs the index; the alpha t
# dropped to 0.20 after the effective-bets correction), and global value
# long/short (0 of 15 variants). Shareholder yield is deployed as a
# funder-directed EXCEPTION to survivors-only because it tests a DISTINCT axis —
# cash actually returned to owners rather than an accounting ratio — and is the
# closest fundamentals cousin to carry, the only family that has ever shown
# anything real here. It is judged ONLY by its 182-day live record.
#
# SIGNAL: shareholder yield = trailing dividend yield + net buyback yield,
# where net buyback yield is the NET share-count reduction over the trailing
# twelve months (net of issuance — a share count is the honest measure; a
# cash-flow "repurchase" line is not, because it ignores dilution).
#
# DATA HONESTY IS THE WHOLE POINT OF THIS BOOK. A name whose buyback data
# cannot be honestly sourced is EXCLUDED and recorded as excluded. It is NEVER
# imputed and NEVER assumed zero: a book that silently zero-fills buybacks is
# measuring dividend yield and calling it shareholder yield.
#
# UNIVERSE (frozen, verbatim, 76 names). Chosen ONCE, by hand, as liquid
# US-listed large/mid caps spread across every GICS sector, and written into the
# code and the registration so it can never drift. DISCLOSED BIAS: these are all
# names that are large and listed TODAY (2026-08-27); the list was not
# reconstructed as of a past date. For a FORWARD live test that bias is small
# (membership is fixed at t0 and never re-pulled, so no future information can
# enter), but it is real for anyone who later backtests this list, and it is
# stated here rather than buried. The list is NOT a live index-membership pull.
SY_UNIVERSE = [
    # Information technology / communication services (17)
    "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AVGO", "ORCL", "CSCO", "ADBE",
    "CRM", "INTC", "IBM", "QCOM", "TXN", "AMAT", "ACN", "INTU",
    # Consumer discretionary (8)
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TGT",
    # Consumer staples (9)
    "COST", "WMT", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL",
    # Financials (12)
    "BRK-B", "JPM", "BAC", "WFC", "C", "GS", "MS", "AXP", "BLK", "SPGI",
    "USB", "PNC",
    # Health care (11)
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "LLY", "TMO", "ABT", "AMGN", "GILD",
    "CVS",
    # Industrials (8)
    "CAT", "DE", "HON", "UNP", "UPS", "RTX", "LMT", "GD",
    # Energy (4)
    "XOM", "CVX", "COP", "SLB",
    # Materials / utilities / real estate (7)
    "LIN", "FCX", "NEE", "DUK", "SO", "AMT", "PLD",
]

SY_SLEEVE_CAPITAL = 40000.0   # funder-directed 2026-08-27
SY_COST_BPS = 10.0            # 10 bps PER SIDE, charged on every fill
SY_REBALANCE_DAYS = 63        # quarterly, the house cadence
SY_MIN_TOP_N = 8              # hold top decile OR top 8, whichever is LARGER
SY_MIN_SPAN_DAYS = 300        # share-count observations must span >= 300 days
SY_MC_RATIO_LO = 1.0 / 3.0    # share_count * price vs reported market cap
SY_MC_RATIO_HI = 3.0
SY_MAX_ABS_BYY = 50.0         # |net buyback yield| > 50%/yr => data error
SY_MIN_USABLE = 20            # fewer usable names than this => REFUSE to trade


def _sy_utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sy_settled_closes(symbols):
    """{sym: (dates, closes)} of SETTLED yfinance daily bars, auto_adjust=True.
    Settled-bar discipline is the desk standard (exit_axis / _rr_settled_yf):
    the final row is dropped when its date equals the current UTC date, because
    during a live session that bar is still in progress. Fills may only ever
    reference these closes. Own copy (not _rr_settled_yf) so every failure line
    names shareholder_yield and no byte of a frozen sleeve is touched."""
    import yfinance as yf
    raw = yf.download(list(symbols), period="6mo", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    closes_df = raw["Close"]
    if not hasattr(closes_df, "columns"):          # single symbol -> Series
        closes_df = closes_df.to_frame(list(symbols)[0])
    today = time.strftime("%Y-%m-%d", time.gmtime())
    out = {}
    for sym in symbols:
        if sym not in closes_df.columns:
            continue
        col = closes_df[sym].dropna()
        dates = [d.strftime("%Y-%m-%d") for d in col.index]
        closes = [float(x) for x in col.values]
        if dates and dates[-1] == today:           # drop in-progress bar
            dates, closes = dates[:-1], closes[:-1]
        if dates and closes[-1] > 0:
            out[sym] = (dates, closes)
    return out


def _sy_name_yield(sym, px):
    """Shareholder yield for one name, or an EXCLUSION with a stated reason.

    Returns a dict. On success it carries div_yield_pct, buyback_yield_pct and
    shareholder_yield_pct; on failure it carries 'excluded' with the reason and
    NO yield is invented for it anywhere downstream.

    NET BUYBACK YIELD: from the quarterly balance sheet share count
    ('Ordinary Shares Number', falling back to 'Share Issued'). Take the most
    recent non-null observation and the earlier observation whose span is
    closest to 365 days (and at least SY_MIN_SPAN_DAYS), then annualise
    (prior - latest) / prior. Because a share COUNT is used, issuance and
    stock comp are already netted off — this is net buyback, not gross.

    THE SHARE-CLASS TRAP (found 2026-08-27 building this book, recorded in the
    addendum): for BRK-B yfinance returns the CLASS A share count (~1.4 million)
    against a B-share price. Left alone it produced a plausible-looking, quietly
    meaningless ~0% buyback yield. So every share count is CROSS-CHECKED against
    the reported market cap: share_count * price must land within a factor of
    three of market cap, or the name is EXCLUDED as a wrong-share-class /
    mis-sourced figure. This guard is part of the frozen rule.

    DIVIDEND YIELD: trailing-12-month sum of actual paid dividends divided by
    the settled close. An empty dividend series on a name whose price history
    is fine is a GENUINE non-payer (e.g. ADBE) and books a true 0.0, recorded
    as such; a dividend FETCH FAILURE excludes the name."""
    import yfinance as yf
    d = {"sym": sym}
    try:
        t = yf.Ticker(sym)
        bs = t.quarterly_balance_sheet
        row = None
        for cand in ("Ordinary Shares Number", "Share Issued"):
            if bs is not None and not bs.empty and cand in bs.index:
                row = cand
                break
        if row is None:
            d["excluded"] = "no share-count row in quarterly balance sheet"
            return d
        ser = bs.loc[row].dropna().sort_index()
        if len(ser) < 2:
            d["excluded"] = "fewer than 2 non-null share-count observations"
            return d
        last_dt, latest = ser.index[-1], float(ser.iloc[-1])
        if latest <= 0:
            d["excluded"] = "latest share count non-positive"
            return d
        best = None
        for dt, v in zip(ser.index[:-1], ser.values[:-1]):
            span = (last_dt - dt).days
            if span >= SY_MIN_SPAN_DAYS and float(v) > 0:
                if best is None or abs(span - 365) < abs(best[2] - 365):
                    best = (dt, float(v), span)
        if best is None:
            d["excluded"] = ("share-count history spans <%dd (have %dd)"
                             % (SY_MIN_SPAN_DAYS,
                                (last_dt - ser.index[0]).days))
            return d
        prior_dt, prior, span = best
        byy = ((prior - latest) / prior) * (365.0 / span) * 100.0
        d.update({"share_row": row, "shares_latest": latest,
                  "shares_prior": prior, "span_days": span,
                  "shares_latest_date": str(last_dt)[:10],
                  "shares_prior_date": str(prior_dt)[:10],
                  "buyback_yield_pct": round(byy, 4)})
        try:
            mcap = float(t.fast_info["market_cap"])
        except Exception as e:
            d["excluded"] = ("no market cap for the share-class cross-check "
                             "(%s)" % str(e)[:60])
            return d
        if not mcap or mcap <= 0:
            d["excluded"] = "market cap unavailable for share-class cross-check"
            return d
        ratio = (latest * float(px)) / mcap
        d["mcap_ratio"] = round(ratio, 4)
        if not (SY_MC_RATIO_LO <= ratio <= SY_MC_RATIO_HI):
            d["excluded"] = ("share count inconsistent with market cap "
                             "(x%.4f, band %.2f-%.2f) — mis-sourced or wrong "
                             "share class" % (ratio, SY_MC_RATIO_LO,
                                              SY_MC_RATIO_HI))
            return d
        if abs(byy) > SY_MAX_ABS_BYY:
            d["excluded"] = ("implausible net buyback yield %.2f%%/yr (|.| > "
                             "%.0f%%) — treated as a data error" % (byy,
                                                                   SY_MAX_ABS_BYY))
            return d
        try:
            div = t.dividends
        except Exception as e:
            d["excluded"] = "dividend history fetch failed (%s)" % str(e)[:60]
            return d
        if div is None:
            d["excluded"] = "dividend history unavailable"
            return d
        if len(div) == 0:
            d["div_yield_pct"] = 0.0
            d["div_source"] = "no dividend ever paid (genuine zero, not imputed)"
        else:
            import pandas as pd
            idx = (div.index.tz_localize(None) if div.index.tz is not None
                   else div.index)
            cutoff = (pd.Timestamp.now("UTC").tz_localize(None)
                  - pd.Timedelta(days=365))
            ttm = float(div.values[idx > cutoff].sum())
            d["div_yield_pct"] = round(ttm / float(px) * 100.0, 4)
            d["div_source"] = ("trailing-12m paid dividends / settled close"
                               if ttm > 0 else
                               "dividend history present, none paid in "
                               "trailing 12m (genuine zero)")
        d["shareholder_yield_pct"] = round(d["div_yield_pct"]
                                           + d["buyback_yield_pct"], 4)
    except Exception as e:
        d["excluded"] = "fundamentals fetch error: %s" % str(e)[:80]
    return d


def _sy_solve_notional(avail, have, fee):
    """Largest EQUAL per-name notional N such that, after trading every target
    name from its current notional have_i to N and paying `fee` on every
    dollar of turnover, residual cash is >= 0:

        f(N) = avail - k*N - fee * sum_i |N - have_i|  >=  0

    f is strictly decreasing, f(0) >= 0 and f(avail/k) <= 0, so bisection on
    the feasible side gives exact NET-OF-COSTS sizing: cash after the rebalance
    is non-negative BY CONSTRUCTION, not by a check afterwards."""
    k = len(have)
    if k == 0 or avail <= 0:
        return 0.0
    lo, hi = 0.0, avail / k
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if avail - k * mid - fee * sum(abs(mid - h) for h in have) >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def _sy_rebalance_to(s, sy, targets, px, tag):
    """Trade the sleeve to an equal-weight book over `targets` at SETTLED
    closes, 10 bps per side on every fill. Sells first, then exact
    net-of-costs sizing via _sy_solve_notional. Returns (fills, total_cost)."""
    fee = SY_COST_BPS / 10000.0
    fills, total_cost = [], 0.0
    for sym in list(s["holdings"]):
        if sym in targets and sym in px:
            continue
        if sym not in px:
            continue                       # cannot price it; refuse to fill
        units = s["holdings"].pop(sym)
        s["entry_px"].pop(sym, None)
        notional = units * px[sym]
        cost = notional * fee
        s["cash"] += notional - cost
        total_cost += cost
        fills.append({"sym": sym, "side": "sell", "reason": tag + "_exit",
                      "units": round(units, 10), "fill_close": px[sym],
                      "notional": round(notional, 2), "cost": round(cost, 4)})
    tg = [t for t in targets if t in px and px[t] > 0]
    have = [s["holdings"].get(t, 0.0) * px[t] for t in tg]
    avail = s["cash"] + sum(have)
    N = _sy_solve_notional(avail, have, fee)
    for t, h in zip(tg, have):
        delta = N - h
        cost = abs(delta) * fee
        s["cash"] += h - N - cost
        total_cost += cost
        s["holdings"][t] = N / px[t]
        s["entry_px"][t] = float(px[t])
        if abs(delta) > 1e-9:
            fills.append({"sym": t, "side": "buy" if delta > 0 else "sell",
                          "reason": tag, "units": round(abs(delta) / px[t], 10),
                          "fill_close": float(px[t]),
                          "notional": round(abs(delta), 2),
                          "cost": round(cost, 4)})
    s["costs"] = round(s.get("costs", 0.0) + total_cost, 2)
    s["last_rebalance"] = time.strftime("%Y-%m-%d")
    return fills, round(total_cost, 4)


def _sy_bench_step(bench, targets, px, rebalance):
    """A benchmark book run with the SAME $40k start, the SAME 10 bps per side
    and the SAME settled closes as the sleeve. Two are kept: SPY (the market)
    and the equal-weight universe itself (the basket). The universe benchmark
    is the one that separates the SIGNAL from the BASKET — if the top-decile
    book cannot beat its own equal-weight universe, the shareholder-yield
    ranking added nothing and the book has failed on its own terms, whatever
    it did against SPY."""
    fee = SY_COST_BPS / 10000.0
    h = bench.setdefault("holdings", {})
    if rebalance:
        tg = [t for t in targets if t in px and px[t] > 0]
        for sym in list(h):
            if sym not in tg and sym in px:
                bench["cash"] += h.pop(sym) * px[sym] * (1.0 - fee)
        have = [h.get(t, 0.0) * px[t] for t in tg]
        avail = bench["cash"] + sum(have)
        N = _sy_solve_notional(avail, have, fee)
        for t, hv in zip(tg, have):
            bench["cash"] += hv - N - abs(N - hv) * fee
            h[t] = N / px[t]
        bench["costs"] = round(bench.get("costs", 0.0)
                               + sum(abs(N - hv) * fee for hv in have), 4)
    # CM-043 (2026-08-27, hostile review D1): the old line silently DROPPED any
    # holding missing from px -- `if t in px` with no fallback. yf_last() returns
    # an EMPTY dict whenever its download raises (bare except: pass), so a cold
    # cache collapsed a benchmark to $0 / -100% with no error and no state flag,
    # and one missing name out of 75 shaved 1.4% off silently. That biased the
    # equal-weight-universe benchmark DOWNWARD -- i.e. IN THE BOOK'S OWN FAVOUR --
    # on every data gap, and the registration itself calls that benchmark "the one
    # that matters most". Now: fall back to the last known price per holding (the
    # same discipline mark() uses), and make any gap LOUD and machine-visible.
    last = bench.setdefault("last_px", {})
    for t, q in px.items():
        if q and q > 0:
            last[t] = float(q)
    missing = [t for t in h if t not in px or not px.get(t)]
    eq = bench["cash"]
    for t, u in h.items():
        q = px.get(t) or last.get(t)
        if q:
            eq += u * q
    if missing:
        bench["bench_error"] = {
            "msg": ("%d of %d holdings had no price this step (%s) — marked at "
                    "last known, NEVER dropped. A dropped holding would bias this "
                    "benchmark DOWN, in the sleeve's favour."
                    % (len(missing), len(h), ", ".join(missing[:6]))),
            "missing": missing[:20],
            "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        print("   ERROR shareholder_yield benchmark %s: %d/%d holdings unpriced "
              "(%s) — marked at last known"
              % (bench.get("name", "?"), len(missing), len(h),
                 ", ".join(missing[:6])))
        unpriceable = [t for t in missing if t not in last]
        if unpriceable:
            bench["bench_error"]["never_priced"] = unpriceable[:20]
            print("   ERROR shareholder_yield benchmark %s: %d holdings have NO "
                  "last-known price at all (%s) — benchmark is UNDERSTATED"
                  % (bench.get("name", "?"), len(unpriceable),
                     ", ".join(unpriceable[:6])))
    else:
        bench.pop("bench_error", None)
    bench["equity"] = round(eq, 2)
    bench["pnl_pct"] = round((eq / SY_SLEEVE_CAPITAL - 1.0) * 100.0, 3)
    return eq


def tick_shareholder_yield(s):
    """shareholder_yield — funder-directed 2026-08-27, FROZEN (see the
    2026-08-27 addendum in TOURNAMENT_PREREGISTRATION.md). $40,000 paper.

    THE FROZEN RULE
      Universe   SY_UNIVERSE above — 76 liquid US-listed large/mid caps, fixed
                 and written verbatim into the code and the registration. Not a
                 live index-membership pull; the survivorship caveat is
                 disclosed at SY_UNIVERSE rather than hidden.
      Signal     shareholder yield = trailing-12m dividend yield + net buyback
                 yield (net share-count reduction over the trailing 12 months).
                 A name whose buyback data cannot be honestly sourced is
                 EXCLUDED and recorded — never imputed, never assumed zero.
      Book       top decile by shareholder yield, or top 8, WHICHEVER IS
                 LARGER, EQUAL WEIGHT.
      Cadence    quarterly, 63 trading-day-equivalent days, the house cadence.
      Costs      10 bps PER SIDE on every fill. Sizing is NET OF COSTS by
                 construction (_sy_solve_notional): cash can never go negative.
      Fills      SETTLED daily closes only; the in-progress bar is dropped.
      Kill       freeze at -30% drawdown (standard mark()).
      Benchmark  SPY AND the equal-weight universe itself, both run on the same
                 $40k, the same costs and the same closes.
      Evidence   no verdict before 182 days; reviewed with the tournament.

    Once per UTC day. On non-rebalance days the sleeve marks to settled closes
    and does nothing else. Every failure is LOUD: a printed ERROR line and a
    state field on the sleeve — this book NEVER fails silently, because a
    silent failure here degrades it into dividend yield wearing another name."""
    sy = s.setdefault("shareholder_yield", {})
    today = time.strftime("%Y-%m-%d", time.gmtime())

    def _live_mark():
        px = yf_last([t for t in s["holdings"]])
        mark(s, px)

    if sy.get("last_eval_utc") == today:      # one evaluation per UTC day
        _live_mark()
        return None

    # ---------------- settled daily closes for the whole universe ----------
    try:
        series = _sy_settled_closes(SY_UNIVERSE)
    except Exception as e:
        msg = ("ERROR: shareholder_yield: settled close fetch failed: %s"
               % str(e)[:140])
        print(msg)
        sy["data_error"] = {"msg": msg, "at_utc": _sy_utcnow()}
        _live_mark()
        return msg
    if not series:
        msg = ("ERROR: shareholder_yield: no settled closes at all — marked "
               "only, will retry")
        print(msg)
        sy["data_error"] = {"msg": msg, "at_utc": _sy_utcnow()}
        _live_mark()
        return msg
    sy.pop("data_error", None)

    # every current holding MUST have a positive settled close, else refuse
    missing = [t for t in s["holdings"] if t not in series]
    if missing:
        msg = ("ERROR: shareholder_yield: no positive settled close for held "
               "%s — refusing to evaluate" % ", ".join(sorted(missing)))
        print(msg)
        sy["state_error"] = {"msg": msg, "at_utc": _sy_utcnow()}
        _live_mark()
        return msg
    sy.pop("state_error", None)
    sy["last_eval_utc"] = today
    px = {t: c[1][-1] for t, c in series.items()}
    bar_date = max(c[0][-1] for c in series.values())
    sy["settled_bar_date"] = bar_date

    due = (not s["holdings"]) or (days_since(s.get("last_rebalance"))
                                  >= SY_REBALANCE_DAYS)
    if not due:
        mark(s, px)
        _sy_bench_step(sy.setdefault("bench_spy", {"cash": SY_SLEEVE_CAPITAL}),
                       ["SPY"], yf_last(["SPY"]), False)
        _sy_bench_step(sy.setdefault("bench_ew_universe",
                                     {"cash": SY_SLEEVE_CAPITAL}),
                       [], px, False)
        sy["next_rebalance_in_days"] = round(
            SY_REBALANCE_DAYS - days_since(s.get("last_rebalance")), 1)
        return None

    # ---------------- the signal: shareholder yield, honestly sourced ------
    from concurrent.futures import ThreadPoolExecutor
    names = [t for t in SY_UNIVERSE if t in px]
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            rows = list(ex.map(lambda t: _sy_name_yield(t, px[t]), names))
    except Exception as e:
        msg = ("ERROR: shareholder_yield: fundamentals pass failed: %s"
               % str(e)[:140])
        print(msg)
        sy["signal_error"] = {"msg": msg, "at_utc": _sy_utcnow()}
        mark(s, px)
        return msg
    sy.pop("signal_error", None)

    usable = {r["sym"]: r for r in rows if "excluded" not in r}
    excluded = {r["sym"]: r["excluded"] for r in rows if "excluded" in r}
    for t in SY_UNIVERSE:
        if t not in px:
            excluded[t] = "no settled close this bar"
    n_uni, n_ok = len(SY_UNIVERSE), len(usable)
    cover = round(100.0 * n_ok / n_uni, 1)
    sy["coverage"] = {"universe": n_uni, "usable_buyback_data": n_ok,
                      "coverage_pct": cover, "at_utc": _sy_utcnow(),
                      "bar_date": bar_date}
    sy["excluded"] = excluded            # recorded, never imputed
    sy["yields"] = {k: {"sy": v["shareholder_yield_pct"],
                        "div": v["div_yield_pct"],
                        "buyback": v["buyback_yield_pct"],
                        "span_days": v["span_days"],
                        "mcap_ratio": v.get("mcap_ratio")}
                    for k, v in usable.items()}
    print("shareholder_yield: buyback data usable for %d of %d names (%.1f%%) "
          "at settled bar %s" % (n_ok, n_uni, cover, bar_date))
    if cover < 50.0:
        print("ERROR: shareholder_yield: FEWER THAN HALF the universe has "
              "honestly sourced buyback data (%d/%d) — the registered signal "
              "may not be honestly computable; this book's rebalance is at "
              "risk of being DIVIDEND-DOMINATED. Flagged, not hidden."
              % (n_ok, n_uni))
        sy["coverage_warning"] = {
            "msg": "buyback coverage %.1f%% < 50%% — rebalance may be "
                   "dividend-dominated" % cover, "at_utc": _sy_utcnow()}
    else:
        sy.pop("coverage_warning", None)
    if n_ok < SY_MIN_USABLE:
        msg = ("ERROR: shareholder_yield: only %d usable names (< %d) — "
               "REFUSING to rebalance; no fills, book unchanged"
               % (n_ok, SY_MIN_USABLE))
        print(msg)
        sy["coverage_error"] = {"msg": msg, "at_utc": _sy_utcnow()}
        mark(s, px)
        return msg
    sy.pop("coverage_error", None)

    # ---------------- top decile (or top 8, whichever is larger) -----------
    top_n = max(SY_MIN_TOP_N, int(round(n_ok / 10.0)))
    ranked = sorted(usable.values(),
                    key=lambda r: (-r["shareholder_yield_pct"], r["sym"]))
    picks = [r["sym"] for r in ranked[:top_n]]
    sy["top_n"] = top_n
    sy["picks"] = [{"sym": r["sym"], "sy": r["shareholder_yield_pct"],
                    "div": r["div_yield_pct"],
                    "buyback": r["buyback_yield_pct"]} for r in ranked[:top_n]]
    d_avg = sum(r["div_yield_pct"] for r in ranked[:top_n]) / top_n
    b_avg = sum(r["buyback_yield_pct"] for r in ranked[:top_n]) / top_n
    sy["basket_decomposition"] = {
        "avg_div_yield_pct": round(d_avg, 3),
        "avg_buyback_yield_pct": round(b_avg, 3),
        "buyback_share_of_signal_pct": (round(100.0 * b_avg / (d_avg + b_avg), 1)
                                        if (d_avg + b_avg) else None),
        "note": "if buyback share is near zero this book is dividend yield "
                "wearing another name — say so at review"}

    fills, cost = _sy_rebalance_to(s, sy, picks, px, "quarterly_rebalance")
    ev = {"bar_date": bar_date, "at_utc": _sy_utcnow(), "top_n": top_n,
          "picks": picks, "fills": fills, "cost": cost,
          "usable": n_ok, "universe": n_uni}
    sy.setdefault("events", []).append(ev)
    sy["events"] = sy["events"][-40:]
    sy["last_rebalance_utc"] = _sy_utcnow()
    sy["next_rebalance_in_days"] = float(SY_REBALANCE_DAYS)

    # ---------------- benchmarks on the same closes and the same costs -----
    try:
        spy_px = {"SPY": px.get("SPY")} if px.get("SPY") else {}
        if not spy_px:
            spy_px = {k: v for k, v in _sy_settled_closes(["SPY"]).items()}
            spy_px = {"SPY": spy_px["SPY"][1][-1]} if spy_px else {}
        if spy_px:
            _sy_bench_step(sy.setdefault("bench_spy",
                                         {"cash": SY_SLEEVE_CAPITAL}),
                           ["SPY"], spy_px, True)
        else:
            print("ERROR: shareholder_yield: SPY benchmark unpriceable this "
                  "bar — benchmark not stepped (recorded)")
            sy["bench_error"] = {"msg": "SPY unpriceable", "at_utc": _sy_utcnow()}
        _sy_bench_step(sy.setdefault("bench_ew_universe",
                                     {"cash": SY_SLEEVE_CAPITAL}),
                       sorted(usable), px, True)
    except Exception as e:
        msg = "ERROR: shareholder_yield: benchmark step failed: %s" % str(e)[:120]
        print(msg)
        sy["bench_error"] = {"msg": msg, "at_utc": _sy_utcnow()}

    eq = mark(s, px)
    sy["deployed_usd"] = round(sum(u * px[t] for t, u in s["holdings"].items()
                                   if t in px), 2)
    sy["cash_usd"] = round(s["cash"], 2)
    if s["cash"] < 0:                    # must be impossible — say so loudly
        print("ERROR: shareholder_yield: cash negative ($%.6f) after tick — "
              "net-of-costs invariant violated" % s["cash"])
        sy["cash_error"] = {"cash": s["cash"], "at_utc": _sy_utcnow()}
    return ("REBALANCE top %d of %d usable (%d excluded) at settled closes %s: "
            "%s; cost $%.2f; deployed $%.0f of equity $%.0f"
            % (top_n, n_ok, len(excluded), bar_date, ", ".join(picks), cost,
               sy["deployed_usd"], eq))


# shareholder_yield registration — append-only (the SLEEVES literal and the
# SLEEVE_START literal above are untouched, and every prior registration line
# is left byte-identical; the frozen-files rule): dispatch entry + the
# funder-directed $40,000 SLEEVE_START, 2026-08-27 addendum.
SLEEVES["shareholder_yield"] = tick_shareholder_yield
SLEEVE_START["shareholder_yield"] = SY_SLEEVE_CAPITAL



def tick(verbose=True):
    actions = {}
    for name, fn in SLEEVES.items():
        s = load_sleeve(name)

        if s is None:

            continue  # retired/archived — do not resurrect
        # ---- cash yield amendment (CASH_YIELD_AMENDMENT.md, funder-directed
        # 2026-08-27, "GO FOR IT"). UNIFORM: every sleeve's idle cash accrues
        # the live 13-week T-bill rate (^IRX), prorated by ACTUAL elapsed time,
        # FORWARD ONLY from each sleeve's first accrual — never retroactive.
        # Placed HERE, in the shared per-sleeve loop before the sleeve's own
        # frozen tick body runs, so (a) not one byte of any tick_* function had
        # to change and (b) the sleeve marks itself to market with the interest
        # already in cash. cash_interest_accrued is kept as a SEPARATE field so
        # the November review can always split STRATEGY return from INTEREST
        # return. Fail-safe: no rate / bad rate / stale >24h accrues ZERO,
        # loudly. Never breaks a sleeve tick.
        try:
            import cash_yield
            # CM-070: never pay T-bill on short proceeds. A dollar-neutral
            # sleeve's `cash` line includes the short leg; crediting yield on
            # it is interest the sleeve did not earn, on money it did not lend.
            import cash_yield_books as _cyb
            _elig = _cyb.yield_eligible_cash(s)
            _cy = cash_yield.accrue(s, _elig)
            if _cy:
                s["cash"] = s.get("cash", 0.0) + _cy
                if s.get("frozen") and isinstance(s.get("equity"), (int, float)):
                    # a killed sleeve never re-marks; keep equity consistent
                    s["equity"] = round(s["equity"] + _cy, 2)
        except Exception as e:
            msg = "CASH-YIELD ERROR: sleeve %s: %s" % (name, str(e)[:200])
            print(msg)
            s["cash_yield_error"] = {"msg": msg, "at_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if s.get("frozen"):
            actions[name] = "FROZEN (%s)" % s.get("frozen_reason", "kill rule")
            save_sleeve(s)
            continue
        try:
            act = fn(s)
        except Exception as e:
            act = "error: %s" % str(e)[:80]
        # ---- exit axis. While disabled it does bookkeeping only (not a rule
        # change): freezes entry_sigma at entry from the sleeve's own settled
        # daily closes, tracks peak_close per symbol from settled DAILY closes
        # (never the intraday mark), stamps peak_tracking_since (UTC).
        # DECISIONS are a NO-OP unless reports/exit_axis_config.json
        # enables it — enabling any pre-registered sleeve is a rule change
        # needing a funder-signed amendment). Own try/except: an exit-axis
        # failure prints a loud EXIT-AXIS ERROR line and sets a state field,
        # and must never break the sleeve tick or reach the 80-char note above.
        try:
            import exit_axis
            xa_act = exit_axis.tournament_exit_check(s, set_holdings)
            if xa_act:
                act = (act + "; " + xa_act) if act else xa_act
        except Exception as e:
            msg = "EXIT-AXIS ERROR: sleeve %s: %s" % (name, str(e)[:200])
            print(msg)
            s["exit_axis_error"] = {"msg": msg,
                                    "at_utc": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        save_sleeve(s)
        if act:
            actions[name] = act
    if verbose:
        for n, a in actions.items():
            print("  %-18s %s" % (n, a))
        if not actions:
            print("  (all sleeves current; marked to market)")
    return actions


# ---------------------------------------------------------------- board
# --------------------------------------------------------------------------- #
# COMPETITOR / CONTROL SEPARATION
# --------------------------------------------------------------------------- #
# A leaderboard ranked by raw P&L put buy-and-hold ETFs alongside strategies, so
# probe_japan sat 3rd at +2.57% -- scoring DXJ's beta as though it were skill.
#
# These roles are not a judgement; they are read straight out of what the code
# already does, and the pre-registrations already say so:
#
#   CONTROL       No signal exists. _tick_probe (tournament.py:387-402) buys one
#                 ETF on the first tick and marks forever; tick_equal_weight_all
#                 rebalances 15 fixed tickers to 1/n every 88 days and its weights
#                 never depend on a market observation. The registration calls
#                 these "premium measurements" and a "pure-diversification
#                 control". They are the BENCHMARK. Ranking them against the
#                 strategies that must beat them is a category error.
#
#   DISCRETIONARY No fixed rule to replay. claude_discretionary is explicitly
#                 mark-only between supervised sessions; kraken_engine is
#                 self-modifying (its rule is a function of its own prior runs).
#                 Neither can be validated, so neither can be ranked on the same
#                 axis as a rule that can.
#
#   COMPETITOR    A signal that maps observation to position. The only class for
#                 which "did it beat the control, after costs" is a real question.
#
ROLE_COMPETITOR = "competitor"
ROLE_CONTROL = "control"
ROLE_DISCRETIONARY = "discretionary"

CONTROLS = {
    "probe_japan": "buy-and-hold DXJ; one buy then marks forever",
    "probe_india": "buy-and-hold INDA; one buy then marks forever",
    "probe_agri": "buy-and-hold DBA; one buy then marks forever",
    "probe_em_debt": "buy-and-hold EMLC; one buy then marks forever",
    "equal_weight_all": "static 1/n over 15 fixed ETFs; weights ignore the market",
}
DISCRETIONARY = {
    "claude_discretionary": "mark-only; positions change only in supervised sessions",
    "kraken_engine": "adaptive and self-modifying; not replayable",
}


def role_of(name):
    """CONTROL / DISCRETIONARY / COMPETITOR for a board row."""
    if name in CONTROLS:
        return ROLE_CONTROL
    if name in DISCRETIONARY:
        return ROLE_DISCRETIONARY
    return ROLE_COMPETITOR


def role_reason(name):
    return CONTROLS.get(name) or DISCRETIONARY.get(name) or ""


def board(verbose=True):
    rows = []
    unavailable = []

    def add(name, kind, equity, start, started, dd=None, ccy="USD", note="",
            reason=None):
        if equity is None:
            # A competitor whose equity cannot be marked must be VISIBLY missing.
            # Dropping the row silently made the board look complete when it was
            # not; the leaderboard now carries its own gaps.
            unavailable.append({"name": name, "kind": kind, "ccy": ccy,
                                "note": note,
                                "reason": reason or "no equity mark available"})
            return
        age = int(days_since(started + "T")) if started else None
        rows.append({
            "name": name, "kind": kind, "ccy": ccy,
            "role": role_of(name), "role_reason": role_reason(name),
            "equity": round(equity, 2), "start": start,
            "pnl_pct": round((equity / start - 1) * 100, 2) if start else None,
            "dd_pct": dd, "age_days": age,
            "evidence": "ok" if (age or 0) >= MIN_EVIDENCE_DAYS else
            "insufficient (<%dd)" % MIN_EVIDENCE_DAYS,
            "note": note,
        })

    for name in SLEEVES:
        s = load_sleeve(name)

        if s is None:

            continue  # retired/archived — do not resurrect
        st = s.get("start", SLEEVE_START.get(name, START))
        add(name, "sleeve", s.get("equity", st), st, s.get("started"),
            s.get("dd_pct"), note="FROZEN" if s.get("frozen") else
            (s.get("last_rebalance") or "no trade yet"))

    g = jf(os.path.join(BASE, "reports", "growth_book.json"), {})
    add("growth_book", "book", g.get("equity"), 50000.0, "2026-08-06",
        g.get("drawdown_pct"), note="dual momentum")
    v = jf(os.path.join(BASE, "reports", "value_book.json"), {})
    add("value_book", "book", v.get("equity"), 50000.0, "2026-08-06",
        v.get("drawdown_pct"), note="global value (USD-translated)",
        reason=v.get("equity_status"))

    for ledger, label, started in (
            (os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_floor.json"),
             "floor", "2026-08-05"),
            (os.path.join(BASE, "replica_floor.json"), "replica", "2026-08-06")):
        d = jf(ledger, {"desks": {}})
        for dk_name, dk in (d.get("desks") or {}).items():
            eq = (dk.get("cash", 0.0)
                  + sum(p.get("cost", 0.0) for p in (dk.get("positions") or {}).values())
                  + dk.get("unrealized", 0.0))
            add("%s:%s" % (label, dk_name), label, eq, 40000.0, started,
                note="%d closed" % len(dk.get("closed_trades", [])))

    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=6) as r:
            st = json.load(r)
        # The engine is a USD book: start_cash is 40,000 USD, every pair is
        # XXX/USD and every mark is USD. It was labelled AUD here (and its rows
        # were stamped "ccy":"AUD" upstream), which printed A$ on dollars and
        # invited an FX conversion that must never happen. Fixed 2026-08-16.
        add("kraken_engine", "engine", st.get("equity"),
            st.get("start_cash", 40000.0), "2026-08-05", ccy="USD",
            note="autonomous Chief-PM")
    except Exception:
        pass

    rows.sort(key=lambda x: -(x["pnl_pct"] or -1e9))

    # Split by role. `competitors` still carries EVERY row so nothing disappears
    # from a consumer that has not been taught the roles yet; the separated lists
    # are additive. Only `ranked` is a leaderboard -- controls and discretionary
    # books are reported, never ranked against the strategies.
    ranked = [r for r in rows if r["role"] == ROLE_COMPETITOR]
    controls = [r for r in rows if r["role"] == ROLE_CONTROL]
    discretionary = [r for r in rows if r["role"] == ROLE_DISCRETIONARY]

    # The control BAR: a competitor that does not clear the best control has not
    # earned its place, whatever its raw P&L. Stated here so the board carries the
    # comparison rather than leaving the reader to eyeball two tables.
    control_pnls = [r["pnl_pct"] for r in controls if r["pnl_pct"] is not None]
    best_control = max(control_pnls) if control_pnls else None
    median_control = (sorted(control_pnls)[len(control_pnls) // 2]
                      if control_pnls else None)
    for r in ranked:
        r["beats_best_control"] = (
            None if (best_control is None or r["pnl_pct"] is None)
            else r["pnl_pct"] > best_control)

    payload = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "min_evidence_days": MIN_EVIDENCE_DAYS,
               "registered": REGISTERED, "competitors": rows,
               "ranked": ranked, "controls": controls,
               "discretionary": discretionary,
               "control_bar": {"best_pnl_pct": best_control,
                               "median_pnl_pct": median_control,
                               "n": len(controls),
                               "note": "Controls are buy-and-hold or static "
                                       "allocations with no signal. They are the "
                                       "benchmark, not entrants, and are never "
                                       "ranked against the strategies."},
               "n_beating_best_control": sum(
                   1 for r in ranked if r.get("beats_best_control")),
               "unavailable": unavailable}
    with open(BOARD, "w") as f:
        json.dump(payload, f, indent=2)

    if verbose:
        print("=" * 84)
        print("STRATEGY TOURNAMENT — live leaderboard  [%s]" % payload["generated"])
        print("=" * 84)
        print("  %-26s %-8s %12s %8s %8s %5s  %s"
              % ("competitor", "kind", "equity", "P&L %", "DD %", "age", "evidence"))
        for r in rows:
            c = "A$" if r["ccy"] == "AUD" else "$"
            # 2026-09-26 HT audit: an unmarked competitor (None P&L/equity) printed as "—" instead of crashing the hourly tick
            print("  %-26s %-8s %10s %8s %7s %4sd  %s"
                  % (r["name"], r["kind"], (c + "{:,.0f}".format(r["equity"])) if r.get("equity") is not None else "—",
                     ("%+7.2f%%" % r["pnl_pct"]) if r.get("pnl_pct") is not None else "—",
                     r["dd_pct"] if r["dd_pct"] is not None else "—",
                     r["age_days"], r["evidence"]))
        if unavailable:
            print("\n  NOT MARKED THIS RUN — these competitors are missing from the")
            print("  board above; it is NOT the whole firm:")
            for u in unavailable:
                print("    %-26s %-8s  %s" % (u["name"], u["kind"], u["reason"]))
        print("\n  Ranking metric at evaluation: return/maxDD — raw return NEVER ranks.")
        print("  First review 2026-11-06. No verdict before %d days: this board is noise\n"
              "  until then, and says so." % MIN_EVIDENCE_DAYS)
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="store_true")
    args = ap.parse_args()
    if args.board:
        board()
    else:
        tick()
        board(verbose=False)


if __name__ == "__main__":
    main()
