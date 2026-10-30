#!/usr/bin/env python3
"""crypto_rumour_desk.py — the $20k Crypto Rumour-Rider sleeve. PAPER ONLY.

Frozen by CRYPTO_RUMOUR_PREREGISTRATION.md (sealed 2026-08-27, hash
1b3bc7e77e2a7467). Changing any rule constant below INVALIDATES that
registration. PAPER ONLY: no broker, no credentials, no real orders.

STANDING — say it every time, never imply otherwise
---------------------------------------------------
THE BACKTEST DOES NOT SUPPORT THIS STRATEGY. Out of sample (recent half) it
returned mean -0.055%, t = -0.06 at the 40bps we actually pay. 155% of all
backtest profit came from the best 5% of trades. The "sell before the news"
climax exit fired on only 19% of trades. It is deployed as a DECLARED FORWARD
TEST to generate out-of-sample evidence, never as a validated edge.

THE LOW-VOL BAND IS A CONTROL, NOT A CONVICTION
-----------------------------------------------
The backtest says the low-vol band LOSES (-0.345% mean at 40bps, t -0.79) while
the top-50 band is where any signal lives (+1.292%, t 1.96). The funder asked for
low-vol, so it is MEASURED rather than assumed in either direction -- booked and
reported as its own band so the forward test answers the question.
"""
import json, os, statistics, sys, time, urllib.request, warnings
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "crypto_rumour_book.json")
REGISTRATION = "CRYPTO_RUMOUR_PREREGISTRATION.md (sealed 2026-08-27, 1b3bc7e77e2a7467)"

# ---- FROZEN RULE CONSTANTS -- changing any of these invalidates the seal -----
VOL_MULT      = 3.0      # entry: volume >= 3x its own 20d median
CLIMAX_MULT   = 6.0      # entry refused above this; exit fires AT this
DRIFT_DAYS    = 5
DRIFT_MIN     = 0.05     # +5% over DRIFT_DAYS
MAX_HOLD_D    = 5
STOP_PCT      = 0.12
MAX_POS_BAND  = 8
# --- AMENDMENT v1.1 (funder-directed 2026-08-30, registered before it ran) ----
# ENTRIES are post-only at the BASE maker tier: 25 bps, measured live from
# Kraken AssetPairs, requiring NO volume qualification. EXITS stay taker at
# 40 bps -- an exit that does not fill is not an exit, and charging a maker fee
# on an exit we might not get would flatter the book.
ENTRY_COST_BPS = 25.0    # Kraken MAKER, base tier, no volume needed
EXIT_COST_BPS  = 40.0    # Kraken TAKER -- exits must be able to cross the spread
COST_BPS_SIDE = ENTRY_COST_BPS          # retained: older reads reference this
MAKER_OFFSET_BPS = 5.0   # rest the limit this far BELOW the reference price
MAKER_ORDER_TTL_TICKS = 3               # then the order expires as a MISS
BANDS = {"top50":   {"capital": 14000.0, "label": "Top-50 band"},
         "lowvol":  {"capital":  6000.0, "label": "Low-vol band (CONTROL)"}}
LOWVOL_MIN_USD, LOWVOL_MAX_USD = 100_000.0, 2_000_000.0
TOP_N = 50
# ---- kill rules, pre-committed ---------------------------------------------
HALT_DD_PCT       = -20.0    # halt NEW entries at -20% from the sleeve's peak
MAX_CLOSED_TRADES = 100
MAX_DAYS          = 182
# --- CM-053: two DIFFERENT freshness questions, wrongly conflated at first ----
# Non-negotiable #7 ("no stale prices to trade against") governs the price we
# TRADE AT. That price is the live 24h ticker and it is real-time.
# The SIGNAL bar is a settled DAILY candle: by construction it is up to ~24h old
# the moment it settles. Applying a 6h rule to it flagged every position STALE
# on the board and refused every entry for 18 of every 24 hours. Wrong gate on
# the wrong object -- and an alarm that fires 75% of the time is not an alarm.
MAX_SIGNAL_BAR_AGE_SEC = 30 * 3600   # one daily bar + 6h slack; catches a real feed outage
MAX_LIVE_PX_AGE_SEC    = 15 * 60     # the price we actually trade against

STABLE = ("USDC","USD1","FDUSD","TUSD","BUSD","DAI","USDP","EUR","AEUR","USDE",
          "XUSD","USDS","PYUSD","RLUSD","BFUSD")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get(url, tmo=25):
    req = urllib.request.Request(url, headers={"User-Agent": "tfo-desk/1.0"})
    with urllib.request.urlopen(req, timeout=tmo) as r:
        return json.loads(r.read().decode())


def universe():
    """(top50, lowvol) symbol lists, live from Binance 24h quote volume."""
    t = _get("https://api.binance.com/api/v3/ticker/24hr")
    rows = []
    for x in t:
        s = x["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        if any(b.startswith(k) or b == k for k in STABLE):
            continue
        if b.endswith("UP") or b.endswith("DOWN"):
            continue
        try:
            rows.append((s, float(x["quoteVolume"]), float(x["lastPrice"])))
        except Exception:
            continue
    rows.sort(key=lambda r: -r[1])
    top = [s for s, _, _ in rows[:TOP_N]]
    low = [s for s, v, _ in rows if LOWVOL_MIN_USD <= v < LOWVOL_MAX_USD]
    px = {s: p for s, _, p in rows}
    return top, low, px


def bars(sym, limit=40):
    k = _get("https://api.binance.com/api/v3/klines?symbol=%s&interval=1d&limit=%d"
             % (sym, limit))
    return [{"t": int(b[0]), "o": float(b[1]), "h": float(b[2]), "l": float(b[3]),
             "c": float(b[4]), "v": float(b[7]), "close_t": int(b[6])} for b in k]


def load():
    if os.path.exists(LEDGER):
        return json.load(open(LEDGER))
    d = {"book": "Crypto Rumour-Rider", "registration": REGISTRATION,
         "paper_only": True, "opened_utc": _utc(), "opened_epoch": time.time(),
         "seed_usd": sum(b["capital"] for b in BANDS.values()),
         "cost_bps_per_side": COST_BPS_SIDE,
         "standing": ("NO VALIDATED EDGE. Backtest FAILED out of sample (recent "
                      "half mean -0.055%, t -0.06 at 40bps). 155% of backtest "
                      "profit came from the best 5% of trades. Declared forward "
                      "test on the rally_rider precedent."),
         "bands": {k: {"label": v["label"], "capital": v["capital"],
                       "cash": v["capital"], "peak_equity": v["capital"],
                       "positions": {}, "closed": [], "realised_usd": 0.0,
                       "costs_usd": 0.0, "frozen": False}
                   for k, v in BANDS.items()},
         "events": []}
    save(d)
    return d


def save(d):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, LEDGER)


def _log(d, ev):
    d.setdefault("events", []).append(dict(ev, utc=_utc()))
    d["events"] = d["events"][-300:]


def band_equity(bk, px):
    eq = bk["cash"]
    for sym, p in bk["positions"].items():
        mark = px.get(sym) or p.get("mark") or p["entry"]
        eq += p["units"] * mark
    return eq


def tick(verbose=True):
    d = load()
    try:
        top, low, live_px = universe()
    except Exception as e:
        print("   crypto_rumour: universe unavailable (%s) — NO trading this tick"
              % type(e).__name__)
        return d
    members = {"top50": top, "lowvol": low}

    # firm-wide halt (CM-047) — entries only, exits never gated
    firm_ok, firm_why = True, ""
    try:
        import book_kill as _kill
        firm_ok, firm_why = _kill.firm_entries_allowed("Crypto Rumour-Rider",
                                                       verbose=False)
    except Exception as e:
        print("   firm halt gate unavailable (%s) — entries NOT blocked"
              % type(e).__name__)

    now = time.time()
    days_live = (now - d.get("opened_epoch", now)) / 86400.0
    total_closed = sum(len(b["closed"]) for b in d["bands"].values())
    experiment_over = total_closed >= MAX_CLOSED_TRADES or days_live >= MAX_DAYS

    for bk_name, bk in d["bands"].items():
        syms = members[bk_name]
        # ---------------- EXITS FIRST. Never gated by anything. --------------
        for sym in list(bk["positions"]):
            p = bk["positions"][sym]
            try:
                b = bars(sym)
            except Exception:
                p["mark_stale"] = True
                continue
            if len(b) < 25:
                continue
            settled = b[-2]
            age = now - settled["close_t"] / 1000.0
            live = live_px.get(sym)
            cur = live or settled["c"]
            # STALE means we had to fall back off the live ticker, or the signal
            # feed itself has stopped producing bars. Not "a daily bar is a day old".
            p["mark"], p["mark_utc"] = cur, _utc()
            p["mark_stale"] = (live is None) or (age > MAX_SIGNAL_BAR_AGE_SEC)
            if p["mark_stale"]:
                p["stale_reason"] = ("no live ticker price" if live is None
                                     else "signal feed stalled: last settled bar %.1fh old"
                                          % (age / 3600.0))
            else:
                p.pop("stale_reason", None)
            med = statistics.median([x["v"] for x in b[-22:-2]])
            vr = (settled["v"] / med) if med > 0 else 0.0
            held = (now - p["entry_epoch"]) / 86400.0
            why = None
            if cur <= p["entry"] * (1 - STOP_PCT):
                why = "stop -%.0f%%" % (STOP_PCT * 100)
            elif vr >= CLIMAX_MULT:
                why = "VOLUME CLIMAX %.1fx — selling INTO the news" % vr
            elif held >= MAX_HOLD_D:
                why = "max hold %dd" % MAX_HOLD_D
            if why:
                proceeds = p["units"] * cur
                cost = proceeds * EXIT_COST_BPS / 10000.0
                pnl = proceeds - cost - p["notional"]
                bk["cash"] = round(bk["cash"] + proceeds - cost, 2)
                bk["realised_usd"] = round(bk["realised_usd"] + pnl, 2)
                bk["costs_usd"] = round(bk["costs_usd"] + cost, 2)
                bk["closed"].append({"sym": sym, "utc": _utc(), "why": why,
                                     "entry": p["entry"], "exit": cur,
                                     "pnl_usd": round(pnl, 2),
                                     "ret_pct": round(100 * (cur / p["entry"] - 1), 3),
                                     "held_d": round(held, 2)})
                _log(d, {"event": "EXIT", "band": bk_name, "sym": sym,
                         "why": why, "pnl_usd": round(pnl, 2)})
                if verbose:
                    print("   EXIT  %-12s %-9s %+8.2f%%  $%+8.2f  (%s)"
                          % (sym, bk_name, 100 * (cur / p["entry"] - 1), pnl, why))
                del bk["positions"][sym]

        eq = band_equity(bk, live_px)
        bk["peak_equity"] = max(bk.get("peak_equity") or eq, eq)
        dd = (eq / bk["peak_equity"] - 1) * 100 if bk["peak_equity"] else 0.0
        bk["equity"] = round(eq, 2)
        bk["drawdown_pct"] = round(dd, 2)
        if dd <= HALT_DD_PCT and not bk["frozen"]:
            bk["frozen"] = True
            bk["frozen_reason"] = "sleeve drawdown %.2f%% <= %.0f%%" % (dd, HALT_DD_PCT)
            _log(d, {"event": "FROZEN", "band": bk_name, "dd_pct": round(dd, 2)})
            print("   !! %s FROZEN at %.2f%% — new entries refused, exits unaffected"
                  % (bk_name, dd))

        # ---------------- ENTRIES: every gate must pass ----------------------
        if experiment_over or bk["frozen"] or not firm_ok:
            continue
        room = MAX_POS_BAND - len(bk["positions"])
        if room <= 0:
            continue
        unit = BANDS[bk_name]["capital"] / MAX_POS_BAND
        cands = []
        for sym in syms:
            if sym in bk["positions"] or room <= 0:
                continue
            try:
                b = bars(sym)
            except Exception:
                continue
            if len(b) < 25:
                continue
            settled = b[-2]
            if now - settled["close_t"] / 1000.0 > MAX_SIGNAL_BAR_AGE_SEC:
                continue                      # the signal feed has actually stalled
            if live_px.get(sym) is None:
                continue                      # Non-negotiable #7: no live price, no trade
            med = statistics.median([x["v"] for x in b[-22:-2]])
            if med <= 0:
                continue
            vr = settled["v"] / med
            drift = settled["c"] / b[-2 - DRIFT_DAYS]["c"] - 1.0
            if vr >= VOL_MULT and vr < CLIMAX_MULT and drift >= DRIFT_MIN:
                cands.append((vr, sym, live_px.get(sym) or settled["c"], drift))
        cands.sort(reverse=True)
        # ---- v1.1: rest a POST-ONLY limit; it is not a position until it fills
        pend = bk.setdefault("pending", {})
        for sym in list(pend):
            o = pend[sym]
            o["ticks"] = o.get("ticks", 0) + 1
            try:
                b = bars(sym)
            except Exception:
                continue
            low = min(x["l"] for x in b[-2:]) if len(b) >= 2 else None
            ref = live_px.get(sym)
            if low is not None and low < o["limit"] * (1 - 1e-4):
                # FILLED: the market traded through our resting limit
                if any(sym in _b.get("positions", {}) for _b in d["bands"].values()):
                    # belt AND braces, now SLEEVE-wide (CM-064): never overwrite a
                    # live position, never debit cash for one we already hold, and
                    # never hold the same symbol in two bands.
                    print("   REFUSED duplicate fill %s — position already open" % sym)
                    _log(d, {"event": "DUPLICATE_FILL_REFUSED", "band": bk_name,
                             "sym": sym})
                    del pend[sym]
                    continue
                cost = unit * ENTRY_COST_BPS / 10000.0
                units = (unit - cost) / o["limit"]
                bk["positions"][sym] = {
                    "units": units, "entry": o["limit"],
                    "notional": round(unit - cost, 2),
                    "entry_utc": _utc(), "entry_epoch": now, "mark": ref or o["limit"],
                    "mark_utc": _utc(), "mark_stale": False,
                    "signal": o["signal"], "execution": "maker post-only",
                    "posted_utc": o["posted_utc"], "ticks_to_fill": o["ticks"],
                }
                bk["cash"] = round(bk["cash"] - unit, 2)
                bk["costs_usd"] = round(bk["costs_usd"] + cost, 2)
                bk.setdefault("fills", []).append(
                    {"sym": sym, "utc": _utc(), "limit": o["limit"],
                     "ticks": o["ticks"]})
                _log(d, {"event": "MAKER_FILL", "band": bk_name, "sym": sym,
                         "ticks_to_fill": o["ticks"]})
                if verbose:
                    print("   FILL  %-12s %-9s maker @ %.8g after %d tick(s)"
                          % (sym, bk_name, o["limit"], o["ticks"]))
                del pend[sym]
            elif o["ticks"] >= MAKER_ORDER_TTL_TICKS:
                # MISSED. Record what it went on to do -- this is the adverse
                # selection number the whole fee route depends on.
                slip = ((ref / o["ref"] - 1) * 100) if ref and o.get("ref") else None
                bk.setdefault("misses", []).append(
                    {"sym": sym, "utc": _utc(), "limit": o["limit"],
                     "ref_at_post": o.get("ref"), "ref_now": ref,
                     "moved_pct": round(slip, 3) if slip is not None else None})
                bk["misses"] = bk["misses"][-200:]
                _log(d, {"event": "MAKER_MISS", "band": bk_name, "sym": sym,
                         "moved_pct": round(slip, 3) if slip is not None else None})
                if verbose:
                    print("   MISS  %-12s %-9s limit %.8g expired; price moved %s"
                          % (sym, bk_name, o["limit"],
                             ("%+.2f%%" % slip) if slip is not None else "n/a"))
                del pend[sym]

        room = MAX_POS_BAND - len(bk["positions"]) - len(pend)
        for vr, sym, price, drift in cands[:max(0, room)]:
            # CM-056: `cands` is built BEFORE the pending-fill loop above runs,
            # so a symbol that filled THIS tick is still in the stale candidate
            # list. Without the `in bk["positions"]` guard it gets re-posted,
            # fills again, OVERWRITES the position dict, and debits cash a second
            # time. Observed live on BEAMXUSDT: two MAKER_FILLs, one position,
            # $750 of cash gone. Re-check both live sets at post time.
            # CM-064: "one position per symbol" is a SLEEVE-wide rule, not a
            # per-band one — the registration's "per band" qualifies the COUNT
            # (max 8), not symbol uniqueness. CM-056 only guarded within a band,
            # so a coin whose volume drifted from the top-50 band into the
            # low-vol band opened a SECOND position 22h later. Observed live on
            # COTIUSDT. That is double exposure to one name AND it contaminates
            # the top50-vs-lowvol comparison the two bands exist to make.
            _held_anywhere = any(sym in _b.get("positions", {})
                                 or sym in _b.get("pending", {})
                                 for _b in d["bands"].values())
            if bk["cash"] < unit or not price or _held_anywhere:
                continue
            pend[sym] = {"limit": price * (1 - MAKER_OFFSET_BPS / 10000.0),
                         "ref": price, "posted_utc": _utc(), "ticks": 0,
                         "signal": {"vol_ratio": round(vr, 2),
                                    "drift_pct": round(100 * drift, 2)}}
            _log(d, {"event": "MAKER_POST", "band": bk_name, "sym": sym,
                     "limit": pend[sym]["limit"], "offset_bps": MAKER_OFFSET_BPS})
            if verbose:
                print("   POST  %-12s %-9s limit %.8g (%.0fbp under, post-only)"
                      % (sym, bk_name, pend[sym]["limit"], MAKER_OFFSET_BPS))
        # the old immediate-fill path is retained ONLY as dead reference below
        for vr, sym, price, drift in []:
            cost = unit * ENTRY_COST_BPS / 10000.0
            units = (unit - cost) / price
            bk["positions"][sym] = {
                "units": units, "entry": price, "notional": round(unit - cost, 2),
                "entry_utc": _utc(), "entry_epoch": now, "mark": price,
                "mark_utc": _utc(), "mark_stale": False,
                "signal": {"vol_ratio": round(vr, 2), "drift_pct": round(100*drift, 2)},
            }
            bk["cash"] = round(bk["cash"] - unit, 2)
            bk["costs_usd"] = round(bk["costs_usd"] + cost, 2)
            _log(d, {"event": "ENTRY", "band": bk_name, "sym": sym,
                     "vol_ratio": round(vr, 2), "drift_pct": round(100*drift, 2)})
            if verbose:
                print("   ENTRY %-12s %-9s vol %.1fx  drift %+.1f%%  $%.0f"
                      % (sym, bk_name, vr, 100 * drift, unit))
        bk["equity"] = round(band_equity(bk, live_px), 2)

    # CM-063: publish RULES-ONLY alongside as-traded, every tick, automatically.
    # A discretionary close (funder-directed 2026-08-31, HEMIUSDT) is an
    # intervention, not an observation. The forward test is only meaningful on
    # what the RULES produced, so both numbers are carried side by side and
    # neither can be quoted without the other being visible.
    _all, _rules = [], []
    for _b in d["bands"].values():
        for _c in (_b.get("closed") or []):
            _all.append(_c.get("pnl_usd") or 0.0)
            if not _c.get("discretionary"):
                _rules.append(_c.get("pnl_usd") or 0.0)
    d["realised_as_traded_usd"] = round(sum(_all), 2)
    d["realised_rules_only_usd"] = round(sum(_rules), 2)
    d["closed_rules_only"] = len(_rules)
    d["closed_discretionary"] = len(_all) - len(_rules)
    d["rules_only_note"] = (
        "rules_only EXCLUDES funder-directed discretionary closes. It is the only "
        "number the 182-day verdict may be based on; as_traded is what the account "
        "actually did." if len(_all) != len(_rules) else
        "no discretionary closes — as_traded and rules_only are identical")
    d["equity"] = round(sum(b["equity"] for b in d["bands"].values()), 2)
    d["closed_trades"] = sum(len(b["closed"]) for b in d["bands"].values())
    d["days_live"] = round(days_live, 2)
    d["experiment_over"] = experiment_over
    d["firm_halt"] = None if firm_ok else firm_why
    d["updated"] = _utc()
    d["epoch"] = now
    save(d)
    if verbose:
        print("=" * 68)
        print("CRYPTO RUMOUR-RIDER   [%s]" % d["updated"])
        print("  equity $%s of $%s seed   closed trades %d/%d   day %.1f/%d"
              % ("{:,.2f}".format(d["equity"]), "{:,.0f}".format(d["seed_usd"]),
                 d["closed_trades"], MAX_CLOSED_TRADES, days_live, MAX_DAYS))
        for k, b in d["bands"].items():
            print("  %-24s equity $%9.2f  %d open  %d closed  realised $%+.2f  dd %.2f%%%s"
                  % (b["label"], b["equity"], len(b["positions"]), len(b["closed"]),
                     b["realised_usd"], b["drawdown_pct"], "  FROZEN" if b["frozen"] else ""))
        if d.get("closed_discretionary"):
            print("  realised: as-traded $%+.2f  |  RULES-ONLY $%+.2f (%d trades) "
                  "— %d discretionary close(s) excluded"
                  % (d["realised_as_traded_usd"], d["realised_rules_only_usd"],
                     d["closed_rules_only"], d["closed_discretionary"]))
        print("  NO VALIDATED EDGE — backtest FAILED out of sample. Declared trial.")
    return d


if __name__ == "__main__":
    _book = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "crypto_rumour_book.json")
    if not os.path.exists(_book):
        raise SystemExit(0)  # RETIRED 2026-09-19 — do not resurrect
    tick(verbose="--quiet" not in sys.argv)
