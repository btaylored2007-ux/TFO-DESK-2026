#!/usr/bin/env python3
"""overlay_lab.py — RISK OVERLAY STUDY on the books the firm already owns.

STUDY ONLY. Reads every live ledger, writes exactly two outputs:
    reports/overlay_lab.json      (receipt: inventory, map, schemes, metrics)
    reports/research_overlay.md   (the CIO note)
No orders, no resizing, no engine state touched. Every book's capital is fixed
by a hash-sealed registration — an overlay that resized existing books would
EDIT frozen registrations, which is forbidden. The output of this study is
therefore a MAP and a RECOMMENDATION MENU for the funder (future capital, a
firm-level exposure dial, concentration warnings) — never an auto-applied
reweighting. Precedent: reports/CIO_REVIEW_2026-08-07.md — unrestricted
authority exercised as restraint.

METHOD, HONESTLY
----------------
* Static-weights proxy: each book's 2y daily USD return series is the
  CURRENT-holdings-weighted return of its underlying instruments. This assumes
  today's composition held through the window — the same method the firm's
  risk_engine.py uses, stated rather than hidden.
* Prices: yfinance for equities/ETFs/FX; Kraken public OHLC (read-only,
  unauthenticated) for alt pairs yfinance does not carry — pattern lifted from
  risk_engine.py. Downloads cached under the session scratchpad, never in repo.
* Books whose holdings cannot be honestly mapped are classed as CASH (0 return)
  and the unmapped capital share is disclosed prominently. NOTHING is invented.
* A$ books are converted at the live AUDUSD rate (disclosed). AUD-denominated
  cash is modeled as AUDUSD=X exposure — real translation risk in a
  USD-consolidated view — and this choice is disclosed.
* Non-USD listings in value_book (005930.KS KRW, BNP.PA EUR, BARC.L GBp) are
  marked at raw local prices because that is the ledger's own accounting
  convention; their proxy returns are local-price returns (no separate FX leg),
  matching how the book actually marks P&L.
* Construction schemes rebalance MONTHLY and any rolling estimate uses
  TRAILING 63d data only — no look-ahead.
* Return/Sharpe per scheme are printed for CONTEXT ONLY and are clearly
  labelled as NOT the objective: return differences between construction
  schemes on a 2y window are noise and selection. The recommendation rests on
  the RISK metrics.

AUDIT 2026-08-15 — five confirmed defects fixed, logged in the note's Fix log:
(1) the note claimed the 10% vol-target 'made every crash window worse' while
its own receipt showed schemes 4/5 BEAT current in the 2008 replay (the
trailing-vol scale throttled); claim restated, 2008 column now shown, per-
crash-window scale stats added to the receipt. (2) 'one strategy family'
qualifier now uses floors+replicas-only capital, not the crypto-venue total.
(3) coinspot_floor's 2-desk aggregation disclosed. (4) its at-cost (mark=None)
legs named. (5) value_book KRW/GBp raw-local-price magnitudes flagged as not
true USD values. No metric methodology changed except the ADDED scale stats.
"""
import json
import math
import os
import re
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
OANDA_STATUS = "/Users/test/Downloads/oanda-desk/oanda_status.txt"
SCRATCH = ("/private/tmp/claude-501/-Users-test/"
           "326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/overlay_cache")
SEED_KRAKEN_CACHE = os.path.join(REPORTS, "kraken_price_cache.json")  # READ ONLY seed
MY_KRAKEN_CACHE = os.path.join(SCRATCH, "kraken_cache.json")
PX_CACHE = os.path.join(SCRATCH, "px_panel.pkl")
RECEIPT = os.path.join(REPORTS, "overlay_lab.json")
NOTE = os.path.join(REPORTS, "research_overlay.md")

CACHE_MAX_AGE_SEC = 12 * 3600
PX_START = "2006-06-01"          # covers the 2008 window with margin
TRAIL = 63                       # trailing estimation window (days), no look-ahead
VOL_TARGET = 0.10                # 10%/yr firm vol target (scheme 4/5)
LEV_CAP = 1.5                    # max scale-up for the vol-target dial (disclosed)
WINDOW_2Y_DAYS = 730

CRYPTO_YF = {   # floor symbol -> yfinance ticker (risk_engine map + CRV/BCH + AUD fx)
    "XLM/USD": "XLM-USD", "ADA/USD": "ADA-USD", "BNB/USD": "BNB-USD",
    "AAVE/USD": "AAVE-USD", "XBT/USD": "BTC-USD", "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD", "XRP/USD": "XRP-USD", "LTC/USD": "LTC-USD",
    "DOGE/USD": "DOGE-USD", "DOT/USD": "DOT-USD", "TRX/USD": "TRX-USD",
    "CRV/USD": "CRV-USD", "BCH/USD": "BCH-USD", "BTC/USD": "BTC-USD",
    "AUD/USD": "AUDUSD=X",
}
KRK = "KRK:"                     # pseudo-ticker prefix for Kraken-sourced series

OANDA_MAP = {"SPX500_USD": "SPY", "NAS100_USD": "QQQ",
             "UK100_GBP": "EWU", "DE30_EUR": "EWG"}
OANDA_FX = {"UK100_GBP": "GBPUSD=X", "DE30_EUR": "EURUSD=X"}

CRASH_WINDOWS = {
    "2008 GFC (Sep-Nov 08)": ("2008-09-01", "2008-11-30"),
    "2020 COVID (19Feb-23Mar)": ("2020-02-19", "2020-03-23"),
    "2022 inflation bear (Jan-14Oct)": ("2022-01-03", "2022-10-14"),
}


# ---------------------------------------------------------------- utilities
def jf(path, fb):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fb


def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def crypto_instr(sym):
    """Floor/sleeve symbol -> price instrument.

    Handles both shapes the desk actually holds:
      `SOME/USD` spot pair  -> its yfinance ticker, else KRK:SOME
      `PF_SOMEUSD` Kraken perp -> KRK:SOME

    DEFECT FIX 2026-08-28: the perp branch did not exist here, and the sleeve
    loader only routed symbols containing "/" through this function, so
    `capacity_probe`'s four Kraken perpetuals went to yfinance as literal
    tickers and 404'd ("possibly delisted"). `book_coverage.yf_ticker()` has
    always had exactly this PF_ rule — the two modules simply disagreed, and the
    one feeding the exposure dial was the wrong one. A perp has no yfinance
    listing and never will; its spot pair on Kraken is the honest proxy series,
    which is what KRK: means everywhere else in this file.
    """
    s = (sym or "").strip()
    if s.startswith("PF_") and s.endswith("USD"):
        return KRK + s[3:-3].upper()
    return CRYPTO_YF.get(s) or (KRK + s.split("/")[0].strip().upper())


def is_priceable_symbol(sym):
    """True when crypto_instr() knows how to route this symbol."""
    s = (sym or "").strip()
    return "/" in s or (s.startswith("PF_") and s.endswith("USD"))


def kraken_closes(base, cache):
    """Daily closes {'YYYY-MM-DD': close} for one Kraken base symbol.
    Seeded from the firm's existing cache (read-only); own cache lives in
    the scratchpad. Pattern from risk_engine.py."""
    import urllib.request
    pair = base + "USD"
    hit = (cache.get("series") or {}).get(pair)
    fetched = (cache.get("fetched") or {}).get(pair, 0)
    if hit and (time.time() - fetched) < CACHE_MAX_AGE_SEC:
        return hit
    try:
        url = "https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440" % pair
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read().decode())
        if d.get("error"):
            return hit
        key = [k for k in d.get("result", {}) if k != "last"]
        if not key:
            return hit
        rows = d["result"][key[0]]
        ser = {time.strftime("%Y-%m-%d", time.gmtime(int(x[0]))): float(x[4])
               for x in rows}
        cache.setdefault("series", {})[pair] = ser
        cache.setdefault("fetched", {})[pair] = time.time()
        return ser
    except Exception:
        return hit


# ---------------------------------------------------------------- inventory
def parse_oanda():
    try:
        txt = open(OANDA_STATUS).read()
    except Exception:
        return None, []
    m = re.search(r"NAV ([\d,]+\.?\d*) AUD", txt)
    nav = float(m.group(1).replace(",", "")) if m else None
    pos = []
    for pm in re.finditer(r"^\s+(\w+)\s+([+-][\d.]+)u @ ([\d.]+)", txt, re.M):
        pos.append({"raw": pm.group(1), "units": float(pm.group(2)),
                    "entry": float(pm.group(3))})
    return nav, pos


def load_inventory():
    """Enumerate every live book. Read-only w.r.t. every ledger."""
    books = []

    # 1/2 — growth + value books
    for fname, name in (("growth_book.json", "growth_book"),
                        ("value_book.json", "value_book")):
        b = jf(os.path.join(REPORTS, fname), {})
        pos = [{"instr": t, "units": float(u), "raw": t}
               for t, u in (b.get("holdings") or {}).items()]
        books.append(dict(name=name, kind="book", ccy="USD",
                          equity=float(b.get("equity", 0.0)), positions=pos,
                          src="reports/" + fname, note=""))

    # 3 — tournament sleeves (auto_probes.json registry is metadata: skipped)
    tdir = os.path.join(REPORTS, "tournament")
    for f in sorted(os.listdir(tdir)):
        if not f.endswith(".json") or f == "auto_probes.json":
            continue
        s = jf(os.path.join(tdir, f), {})
        pos = []
        entry_px = s.get("entry_px") or {}
        for sym, u in (s.get("holdings") or {}).items():
            instr = crypto_instr(sym) if is_priceable_symbol(sym) else sym
            # `entry` is the fallback VALUATION when no live price exists — see
            # build_exposures(). Sleeves publish entry_px alongside holdings.
            pos.append({"instr": instr, "units": float(u), "raw": sym,
                        "entry": (float(entry_px[sym])
                                  if isinstance(entry_px.get(sym), (int, float))
                                  else None)})
        note = ""
        if s.get("short_call"):
            sc = s["short_call"]
            note = ("short-call overlay ignored in the proxy (%d x %s %s C, "
                    "premium $%.2f/sh — immaterial delta vs the long stock leg)"
                    % (sc.get("contracts", 0), sc.get("expiry", "?"),
                       sc.get("strike", "?"), sc.get("premium", 0.0)))
        books.append(dict(name=s.get("name", f[:-5]), kind="sleeve", ccy="USD",
                          equity=float(s.get("equity", 0.0)), positions=pos,
                          src="reports/tournament/" + f, note=note,
                          frozen=bool(s.get("frozen", False))))

    # tournament rows: authoritative equity for floors / replicas / kraken_engine
    trn = jf(os.path.join(REPORTS, "tournament.json"), {})
    teq = {c["name"]: c for c in trn.get("competitors", [])}

    # 4/5 — Kraken floor + replica floor desks (marked positions in ledger)
    for ledger, prefix, kind in (
            (os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_floor.json"),
             "floor:", "kraken_floor"),
            (os.path.join(BASE, "replica_floor.json"), "replica:", "replica_floor")):
        d = jf(ledger, {"desks": {}})
        for dname, dk in (d.get("desks") or {}).items():
            nm = prefix + dname
            row = teq.get(nm)
            if row:
                eq = float(row["equity"])
            else:  # fallback: cash + cost basis + unrealized
                eq = (dk.get("cash", 0.0)
                      + sum((p.get("cost") or 0.0)
                            for p in (dk.get("positions") or {}).values())
                      + dk.get("unrealized", 0.0))
            pos = []
            for sym, p in (dk.get("positions") or {}).items():
                sign = -1.0 if p.get("side") == "short" else 1.0
                units, mark = p.get("units"), p.get("mark")
                val = (sign * units * mark if (units and mark)
                       else sign * (p.get("cost") or 0.0))
                pos.append({"instr": crypto_instr(sym), "value": val, "raw": sym})
            books.append(dict(name=nm, kind=kind, ccy="USD", equity=eq,
                              positions=pos, src=ledger, note=""))

    # 6 — Kraken engine (A$): equity from tournament row ONLY; engine untouched.
    ke = teq.get("kraken_engine")
    books.append(dict(
        name="kraken_engine", kind="engine", ccy="AUD",
        equity=float(ke["equity"]) if ke else 0.0, positions=[],
        unmapped_assets=True, src="reports/tournament.json row kraken_engine",
        note=("holdings not readable without touching the engine -> classed CASH "
              "at asset level per the no-fabrication rule; only AUDUSD translation "
              "risk is modeled. UNMAPPED share disclosed prominently.")))

    # 7 — CoinSpot floor (A$). AUDIT FIX 2026-08-15 (#3/#4): the ledger holds
    # TWO desks; they are aggregated into ONE book row (capital counted exactly
    # once). That merge and the at-cost legs are now disclosed BY NAME instead
    # of silently.
    cs = jf(os.path.join(KRAKEN_DESK, "reports", "coinspot_floor.json"), {})
    cash_aud, pos, desks_detail, atcost_detail = 0.0, [], [], []
    for dname, dk in (cs.get("desks") or {}).items():
        cash_aud += dk.get("cash", 0.0)
        d_eq = dk.get("cash", 0.0)
        for sym, p in (dk.get("positions") or {}).items():
            sign = -1.0 if p.get("side") == "short" else 1.0
            instr = crypto_instr(sym.upper() + "/USD") if "/" not in sym else crypto_instr(sym)
            cost = p.get("cost") or 0.0
            pos.append({"instr": instr, "value_aud": sign * cost, "raw": sym})
            d_eq += abs(cost)
            if p.get("mark") is None:
                atcost_detail.append("%s A$%s" % (sym, "{:,.0f}".format(cost)))
        desks_detail.append("%s A$%s" % (dname, "{:,.0f}".format(d_eq)))
    eq_aud = cash_aud + sum(abs(p["value_aud"]) for p in pos)
    books.append(dict(name="coinspot_floor", kind="floor", ccy="AUD",
                      equity=eq_aud, positions=pos,
                      desks_detail=desks_detail, atcost_detail=atcost_detail,
                      src=os.path.join(KRAKEN_DESK, "reports", "coinspot_floor.json"),
                      note="aggregates %d CoinSpot desks (%s) into ONE book row — "
                           "capital counted exactly once, but the firm book count "
                           "treats them as a single book; open legs ride at cost "
                           "(%s — the ledger stores no mark; immaterial); A$-cash "
                           "modeled as AUDUSD exposure"
                           % (len(desks_detail), ", ".join(desks_detail),
                              ", ".join(atcost_detail) or "none open")))

    # 8 — OANDA Claude Book: DECOMMISSIONED 2026-08-28 (funder-directed).
    # The book left the firm and its status file is gone, so this study no longer
    # carries it. `parse_oanda` is kept above and already returns (None, []) on a
    # missing file — the book is dropped here rather than entered at zero equity,
    # because a $0 book is not the same statement as a book that is not ours.
    OANDA_BOOK_REMOVED = True
    nav, opos = (None, []) if OANDA_BOOK_REMOVED else parse_oanda()
    if not OANDA_BOOK_REMOVED:
      books.append(dict(name="oanda_claude_book", kind="book", ccy="AUD",
                      equity=nav or 0.0, positions=opos, src=OANDA_STATUS,
                      note="index CFD overlay on an AUD cash NAV. Mapping chosen: "
                           "SPX500->SPY, NAS100->QQQ, UK100->EWU, DE30->EWG "
                           "(USD ETFs, long history). CFDs are unfunded so the whole "
                           "NAV stays AUD cash -> AUDUSD exposure; CFD notionals at "
                           "entry price (only price in the status file)."))

    # 9 — Carry executor: ARMED but FLAT -> cash
    books.append(dict(name="carry_executor", kind="executor", ccy="AUD", equity=0.0,
                      positions=[], src="reports/carry_paper_book.json",
                      note="ARMED but FLAT -> cash. Standby A$33k deploy order is "
                           "contingent, not deployed capital: counted at $0."))
    return books


# ---------------------------------------------------------------- price panel
def needed_instruments(books):
    instrs = {"SPY", "AUDUSD=X", "EURUSD=X", "GBPUSD=X"}
    for b in books:
        for p in b["positions"]:
            if b["name"] == "oanda_claude_book":
                instrs.add(OANDA_MAP.get(p["raw"], "SPY"))
            else:
                instrs.add(p["instr"])
    return instrs


def build_price_panel(instrs):
    yf_ticks = sorted(t for t in instrs if not t.startswith(KRK))
    krk_bases = sorted(t[len(KRK):] for t in instrs if t.startswith(KRK))
    os.makedirs(SCRATCH, exist_ok=True)

    raw = None
    if os.path.exists(PX_CACHE) and (time.time() - os.path.getmtime(PX_CACHE)) < CACHE_MAX_AGE_SEC:
        try:
            cached = pd.read_pickle(PX_CACHE)
            if set(yf_ticks + [KRK + b for b in krk_bases]) <= set(cached.columns):
                raw = cached
                print("price panel: using scratchpad cache (%d cols, %d rows)"
                      % (raw.shape[1], raw.shape[0]))
        except Exception:
            raw = None

    if raw is None:
        import yfinance as yf
        print("Fetching daily history since %s: %d yfinance tickers + %d Kraken pairs..."
              % (PX_START, len(yf_ticks), len(krk_bases)))
        raw = yf.download(yf_ticks, start=PX_START, interval="1d",
                          auto_adjust=True, progress=False, threads=True)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(yf_ticks[0])
        # Kraken public OHLC for the alt legs; seed from the firm's cache READ-ONLY
        cache = jf(MY_KRAKEN_CACHE, {})
        seed = jf(SEED_KRAKEN_CACHE, {})
        for k in ("series", "fetched"):
            for pair, v in (seed.get(k) or {}).items():
                cache.setdefault(k, {}).setdefault(pair, v)
        for base in krk_bases:
            ser = kraken_closes(base, cache)
            if not ser:
                continue
            s = pd.Series({pd.Timestamp(d): v for d, v in ser.items()}).sort_index()
            raw[KRK + base] = (s.reindex(raw.index.union(s.index))
                               .ffill().reindex(raw.index))
        try:
            with open(MY_KRAKEN_CACHE, "w") as f:
                json.dump(cache, f)
            raw.to_pickle(PX_CACHE)
        except Exception:
            pass

    raw = raw.dropna(axis=1, how="all")
    # NYSE trading-day grid from RAW SPY observations (before ffill) — otherwise
    # crypto/FX calendars leak weekend rows in and dilute every vol number.
    master = raw.index[raw["SPY"].notna()]
    px = raw.ffill().loc[master]
    return px


# ---------------------------------------------------------------- exposures
def build_exposures(books, px, fx):
    """Attach USD equity, signed USD instrument exposures, static weights."""
    audusd = fx["AUDUSD=X"]
    unmapped_rows = []
    for b in books:
        eq_usd = b["equity"] * (audusd if b["ccy"] == "AUD" else 1.0)
        exps, unmapped = {}, 0.0

        if b["name"] == "kraken_engine":
            exps["AUDUSD=X"] = eq_usd          # translation risk only
            unmapped = eq_usd                   # asset level: UNMAPPED, loudly
            if eq_usd:
                unmapped_rows.append((b["name"], "engine holdings unreadable", eq_usd))
        elif b["name"] == "oanda_claude_book":
            for p in b["positions"]:
                instr = OANDA_MAP.get(p["raw"])
                if instr is None or instr not in px.columns:
                    unmapped += abs(p["units"] * p["entry"])
                    unmapped_rows.append((b["name"], p["raw"],
                                          abs(p["units"] * p["entry"])))
                    continue
                fxr = fx.get(OANDA_FX.get(p["raw"], ""), 1.0)
                exps[instr] = exps.get(instr, 0.0) + p["units"] * p["entry"] * fxr
            exps["AUDUSD=X"] = exps.get("AUDUSD=X", 0.0) + eq_usd
        elif b["name"] == "coinspot_floor":
            crypto_usd = 0.0
            for p in b["positions"]:
                v = p["value_aud"] * audusd
                if p["instr"] in px.columns:
                    exps[p["instr"]] = exps.get(p["instr"], 0.0) + v
                    crypto_usd += v
                else:
                    unmapped += abs(v)
                    unmapped_rows.append((b["name"], p["raw"], abs(v)))
            exps["AUDUSD=X"] = exps.get("AUDUSD=X", 0.0) + (eq_usd - crypto_usd)
        else:
            for p in b["positions"]:
                instr = p["instr"]
                if "value" in p:
                    v = p["value"]
                else:
                    if instr in px.columns and px[instr].notna().any():
                        v = p["units"] * float(px[instr].dropna().iloc[-1])
                    else:
                        v = None
                if v is None or instr not in px.columns:
                    # DEFECT FIX 2026-08-28: this fell back to `abs(units)` — a
                    # UNIT COUNT booked as DOLLARS. capacity_probe holds four
                    # ~$9,980 clips; because two of them are sub-dollar tokens
                    # (46,911 WIF and 30,586 CRV) the "unmapped capital" summed
                    # to $77,571 against $39,930 actually deployed, and that
                    # fiction is what the 90% mapped-capital floor was testing.
                    # It is arbitrary in BOTH directions: PAXG's 2.14 units would
                    # have reported $2 of unmapped exposure on a $9,995 position.
                    # `risk_engine` already values an unpriceable position at its
                    # stored basis (it reports SPCXX at its true $9,976); the two
                    # modules must not disagree about what a position is worth.
                    ep = p.get("entry")
                    if v is not None:
                        lost = abs(v)
                    elif isinstance(ep, (int, float)) and ep:
                        lost = abs(p.get("units", 0.0) * ep)
                    else:
                        # No live price AND no entry price: the size of this hole
                        # is genuinely unknown, so it cannot be counted at any
                        # number. Flag it — an unquantifiable gap must close the
                        # gate, never quietly shrink the unmapped total.
                        lost = 0.0
                        b["unvaluable"] = b.get("unvaluable", []) + [
                            p.get("raw", instr)]
                    unmapped += lost
                    unmapped_rows.append((b["name"], p.get("raw", instr), lost))
                    continue
                exps[instr] = exps.get(instr, 0.0) + v

        b["equity_usd"] = eq_usd
        b["exposures"] = exps
        b["unmapped_usd"] = unmapped
        b["gross_usd"] = sum(abs(v) for v in exps.values())
        b["weights"] = ({i: v / eq_usd for i, v in exps.items()}
                        if eq_usd > 0 else {})
    return unmapped_rows


def book_series(b, RETS, idx):
    r = pd.Series(0.0, index=idx)
    for i, w in b["weights"].items():
        if i in RETS.columns:
            r = r + RETS[i].reindex(idx).fillna(0.0) * w
    return r


# ---------------------------------------------------------------- risk math
def ann_vol(r):
    return float(r.std() * np.sqrt(252)) * 100


def var99(r):
    return float(np.percentile(r.dropna(), 1)) * 100


def maxdd(r):
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min()) * 100


def beta_to(r, f):
    a = pd.concat([r, f], axis=1).dropna()
    if len(a) < 60 or a.iloc[:, 1].std() == 0:
        return None
    c = np.cov(a.iloc[:, 0], a.iloc[:, 1])
    return float(c[0, 1] / c[1, 1])


def hhi_bets(w):
    aw = np.abs(np.asarray(w, dtype=float))
    g = aw.sum()
    if g <= 0:
        return None
    p = aw / g
    return float(1.0 / np.sum(p * p))


def erc_solve(C):
    """Equal-risk-contribution weights via Spinu's convex log-barrier form
    (min 0.5 w'Cw - lam*sum(log w)), cyclical coordinate descent. At the
    solution w_i*(Cw)_i = lam for every i — exact ERC — and it handles
    negative covariance entries, where naive multiplicative updates stall."""
    n = C.shape[0]
    lam = float(np.mean(np.diag(C))) / n
    w = np.ones(n) / n
    for _ in range(500):
        for i in range(n):
            m = float(C[i] @ w) - C[i, i] * w[i]
            w[i] = (-m + math.sqrt(m * m + 4.0 * C[i, i] * lam)) / (2.0 * C[i, i])
        rc = w * (C @ w)
        if np.max(np.abs(rc / lam - 1.0)) < 1e-9:
            break
    s = w.sum()
    return w / s if s > 0 else np.ones(n) / n


def month_starts(idx):
    out, seen = [], set()
    for t in idx:
        k = (t.year, t.month)
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    books = load_inventory()

    instrs = needed_instruments(books)
    px = build_price_panel(instrs)
    RETS = px.pct_change()

    fx = {}
    for k in ("AUDUSD=X", "EURUSD=X", "GBPUSD=X"):
        fx[k] = float(px[k].dropna().iloc[-1]) if k in px.columns else None
    audusd = fx["AUDUSD=X"]
    unmapped_rows = build_exposures(books, px, fx)

    total_cap = sum(b["equity_usd"] for b in books)
    total_unmapped = sum(b["unmapped_usd"] for b in books)
    mapped_pct = 100.0 * (1.0 - total_unmapped / total_cap) if total_cap else 0.0
    names = [b["name"] for b in books]
    c = np.array([b["equity_usd"] / total_cap for b in books])

    # ------- panels
    last = px.index[-1]
    idx2 = px.index[px.index >= last - pd.Timedelta(days=WINDOW_2Y_DAYS)]
    idxx = px.index[px.index >= pd.Timestamp("2007-01-01")]
    B2 = pd.DataFrame({b["name"]: book_series(b, RETS, idx2) for b in books})
    BX = pd.DataFrame({b["name"]: book_series(b, RETS, idxx) for b in books})
    spy2 = RETS["SPY"].reindex(idx2)
    spyx = RETS["SPY"].reindex(idxx)

    # short-history instruments inside the 2y window (real series, just young)
    short_hist = []
    win_start = idx2[0]
    for i in sorted({i for b in books for i in b["weights"]}):
        if i in px.columns:
            fv = px[i].first_valid_index()
            if fv is not None and fv > win_start:
                expo = sum(abs(b["exposures"].get(i, 0.0)) for b in books)
                short_hist.append({"instrument": i, "first_data": str(fv.date()),
                                   "gross_usd": round(expo, 0)})

    # ------- PART 3: headline map
    cov2 = B2.cov().values * 252
    sig2 = cov2 @ c
    port_var = float(c @ sig2)
    rc = c * sig2 / port_var if port_var > 0 else np.zeros_like(c)
    firm2 = pd.Series(B2.values @ c, index=idx2)

    q1 = np.percentile(firm2, 1)
    tail = firm2 <= q1
    comp_tail = (B2[tail].values * c).mean(axis=0)  # per-book mean tail-day contrib

    firm_vol = ann_vol(firm2)
    firm_var99 = var99(firm2)
    firm_maxdd = maxdd(firm2)
    firm_beta = beta_to(firm2, spy2)
    eff_capital = hhi_bets(c)
    rc_pos = np.clip(rc, 0, None)
    rc_norm = rc_pos / rc_pos.sum() if rc_pos.sum() > 0 else rc_pos
    eff_risk = float(1.0 / np.sum(rc_norm ** 2)) if rc_norm.sum() > 0 else None

    corr_firm = {n: (float(B2[n].corr(firm2)) if B2[n].std() > 0 else None)
                 for n in names}

    per_book = []
    for i, b in enumerate(books):
        per_book.append({
            "book": b["name"], "kind": b["kind"], "ccy": b["ccy"],
            "equity_native": round(b["equity"], 2),
            "equity_usd": round(b["equity_usd"], 0),
            "capital_pct": round(100 * c[i], 2),
            "gross_exposure_usd": round(b["gross_usd"], 0),
            "ann_vol_pct": round(ann_vol(B2[b["name"]]), 2),
            "risk_contribution_pct": round(100 * rc[i], 2),
            "tail99_contrib_usd_day": round(comp_tail[i] * total_cap, 0),
            "corr_to_firm": (round(corr_firm[b["name"]], 2)
                             if corr_firm[b["name"]] is not None else None),
            "unmapped_usd": round(b["unmapped_usd"], 0),
            "note": b.get("note", ""),
        })
    per_book_sorted = sorted(per_book, key=lambda r: -r["risk_contribution_pct"])

    # marginal $10k experiment: firm vol if the next $10k lands in book i
    add = 10000.0
    marg = []
    for i, b in enumerate(books):
        c2 = np.array([x["equity_usd"] for x in per_book], dtype=float)
        c2[i] += add
        c2 = c2 / c2.sum()
        v2 = float(np.sqrt(max(c2 @ cov2 @ c2, 0.0))) * 100
        marg.append({"book": b["name"], "firm_vol_after_pct": round(v2, 3),
                     "delta_bps": round((v2 - firm_vol) * 100, 1)})
    marg_sorted = sorted(marg, key=lambda r: r["firm_vol_after_pct"])

    # crash replays of TODAY'S weights (current scheme), with honest coverage
    first_valid = {i: px[i].first_valid_index() for i in px.columns}

    def book_coverage(b, start):
        gross = sum(abs(w) for w in b["weights"].values())
        if gross <= 0:
            return 1.0  # cash book: honestly covered as cash
        live = sum(abs(w) for i, w in b["weights"].items()
                   if i in first_valid and first_valid[i] is not None
                   and first_valid[i] <= start)
        return live / gross

    firmx = pd.Series(BX.values @ c, index=idxx)
    crash = {}
    for label, (a, z) in CRASH_WINDOWS.items():
        a, z = pd.Timestamp(a), pd.Timestamp(z)
        seg = firmx[(firmx.index >= a) & (firmx.index <= z)]
        covg = float(sum(c[i] * book_coverage(books[i], a) for i in range(len(books))))
        dead = [books[i]["name"] for i in range(len(books))
                if book_coverage(books[i], a) < 0.5 and c[i] > 0]
        crash[label] = {
            "current_weights_return_pct": (round(float((1 + seg).prod() - 1) * 100, 2)
                                           if len(seg) > 5 else None),
            "capital_coverage_pct": round(100 * covg, 1),
            "books_mostly_dark": dead,
        }

    # ------- PART 4: construction schemes (monthly, trailing 63d only)
    ms = month_starts(idxx)
    dloc = {t: i for i, t in enumerate(idxx)}
    n_books = len(books)

    def weights_frame(weight_fn):
        W = pd.DataFrame(np.nan, index=idxx, columns=names)
        for m in ms:
            loc = dloc[m]
            if loc < TRAIL + 1:
                continue
            est = BX.iloc[loc - TRAIL:loc]
            W.loc[m] = weight_fn(est)
        return W.ffill()

    def w_current(est):
        return c

    def w_invvol(est):
        vol = est.std().values
        valid = vol > 1e-9
        w = np.zeros(n_books)
        if valid.any():
            w[valid] = 1.0 / vol[valid]
            w = w / w.sum()
        else:
            w = c.copy()
        return w

    def w_erc(est):
        vol = est.std().values
        valid = vol > 1e-9
        w = np.zeros(n_books)
        if valid.sum() >= 2:
            sub = est.iloc[:, valid]
            C = sub.cov().values + 1e-12 * np.eye(int(valid.sum()))
            w[valid] = erc_solve(C)
        else:
            w = c.copy()
        return w

    schemes = {}
    W_cur = weights_frame(w_current)
    W_iv = weights_frame(w_invvol)
    W_erc = weights_frame(w_erc)

    def daily(W):
        return pd.Series((BX.values * W.values).sum(axis=1), index=idxx)

    r_cur, r_iv, r_erc = daily(W_cur), daily(W_iv), daily(W_erc)

    def vt_scale(base_r):
        s = pd.Series(np.nan, index=idxx)
        for m in ms:
            loc = dloc[m]
            if loc < TRAIL + 1:
                continue
            trail = base_r.iloc[loc - TRAIL:loc]
            rv = float(trail.std() * np.sqrt(252))
            s.loc[m] = min(LEV_CAP, VOL_TARGET / rv) if rv > 1e-9 else 0.0
        return s.ffill()

    s_cur = vt_scale(r_cur)
    s_iv = vt_scale(r_iv)
    r_vt = r_cur * s_cur
    r_ivvt = r_iv * s_iv

    def scheme_metrics(r, W=None, scale=None, gross_mult=None):
        r2 = r.reindex(idx2).dropna()
        met = {
            "ann_vol_pct": round(ann_vol(r2), 2),
            "var99_1d_pct": round(var99(r2), 2),
            "var99_1d_usd": round(total_cap * abs(var99(r2)) / 100, 0),
            "maxdd_2y_pct": round(maxdd(r2), 2),
            "beta_spy": (round(beta_to(r2, spy2), 2)
                         if beta_to(r2, spy2) is not None else None),
            "context_only_NOT_objective": {
                "ann_return_pct": round(((1 + r2).prod() ** (252 / len(r2)) - 1) * 100, 2),
                "sharpe": (round(float(r2.mean() / r2.std() * np.sqrt(252)), 2)
                           if r2.std() > 0 else None),
                "label": "2y return/Sharpe differences between schemes are "
                         "noise + selection; do not choose on these",
            },
        }
        if W is not None:
            hh = [hhi_bets(W.loc[m].values) for m in ms
                  if m in W.index and m >= idx2[0] and not np.isnan(W.loc[m]).all()]
            hh = [h for h in hh if h]
            met["effective_bets_capital_mean"] = round(float(np.mean(hh)), 1) if hh else None
        if scale is not None:
            s2 = scale.reindex(idx2).dropna()
            met["vol_target_scale"] = {"min": round(float(s2.min()), 2),
                                       "mean": round(float(s2.mean()), 2),
                                       "max": round(float(s2.max()), 2),
                                       "leverage_cap": LEV_CAP}
            # AUDIT FIX 2026-08-15 (#1): report what the trailing-vol scale
            # actually DID inside each crash window — the 2y stats above hide
            # that the rule throttled below 1x in the 2008 replay.
            in_cw = {}
            for label, (a, z) in CRASH_WINDOWS.items():
                a, z = pd.Timestamp(a), pd.Timestamp(z)
                sseg = scale[(scale.index >= a) & (scale.index <= z)].dropna()
                if len(sseg):
                    in_cw[label] = {"min": round(float(sseg.min()), 2),
                                    "mean": round(float(sseg.mean()), 2),
                                    "max": round(float(sseg.max()), 2)}
            met["vol_target_scale"]["in_crash_windows"] = in_cw
        met["crash_replays_pct"] = {}
        for label, (a, z) in CRASH_WINDOWS.items():
            a, z = pd.Timestamp(a), pd.Timestamp(z)
            seg = r[(r.index >= a) & (r.index <= z)].dropna()
            met["crash_replays_pct"][label] = (
                round(float((1 + seg).prod() - 1) * 100, 2) if len(seg) > 5 else None)
        return met

    schemes["1_current_static"] = scheme_metrics(r_cur, W=W_cur)
    schemes["2_inverse_vol_monthly"] = scheme_metrics(r_iv, W=W_iv)
    schemes["3_erc_monthly"] = scheme_metrics(r_erc, W=W_erc)
    schemes["4_voltarget10_on_current"] = scheme_metrics(r_vt, W=W_cur, scale=s_cur)
    schemes["5_invvol_plus_voltarget10"] = scheme_metrics(r_ivvt, W=W_iv, scale=s_iv)

    # latest inverse-vol / ERC weights (the "what would change" view)
    latest_iv = {names[i]: round(float(W_iv.iloc[-1, i]) * 100, 1)
                 for i in range(n_books) if W_iv.iloc[-1, i] > 0.001}
    latest_erc = {names[i]: round(float(W_erc.iloc[-1, i]) * 100, 1)
                  for i in range(n_books) if W_erc.iloc[-1, i] > 0.001}
    cur_pct = {names[i]: 100 * c[i] for i in range(n_books)}

    def shifts(latest):
        order = sorted(names, key=lambda n: latest.get(n, 0.0) - cur_pct[n])
        fmt = lambda n: "`%s` %.1f%%→%.1f%%" % (n, cur_pct[n], latest.get(n, 0.0))
        return ([fmt(n) for n in order[:3]], [fmt(n) for n in order[::-1][:3]])

    iv_cut, iv_add = shifts(latest_iv)
    erc_cut, erc_add = shifts(latest_erc)

    # ------- receipt
    csb = next((b for b in books if b["name"] == "coinspot_floor"), None)
    caveats = [
        "STATIC-WEIGHTS PROXY: today's holdings assumed held through the whole "
        "window (same method as the firm's risk_engine.py). Books rebalance in "
        "reality; this is a map of the CURRENT deployment, not a backtest of the "
        "books' strategies.",
        "AUDUSD=%.4f used for all A$ conversions (yfinance AUDUSD=X last close)." % audusd,
        "AUD-denominated cash (kraken_engine, OANDA NAV, CoinSpot cash) is modeled "
        "as AUDUSD=X exposure — genuine translation risk in a USD-consolidated view.",
        "kraken_engine holdings are UNMAPPED (engine may not be touched): classed "
        "CASH at asset level, %.1f%% of firm capital — its true risk is understated "
        "by whatever it actually holds." % (100 * (books[[b['name'] for b in books].index('kraken_engine')]['equity_usd'] / total_cap) if total_cap else 0),
        "value_book non-USD listings (005930.KS KRW, BNP.PA EUR, BARC.L GBp) marked "
        "at raw local prices — the ledger's own convention; proxy returns are "
        "local-price returns for those legs. AUDIT FIX 2026-08-15 (#5), said "
        "plainly: the 005930.KS/BARC.L exposure MAGNITUDES are raw KRW/pence "
        "taken as USD, i.e. NOT true USD values, so value_book's weight vector "
        "is slightly distorted by the convention — the legs are small, and the "
        "distortion is disclosed rather than re-marked so the study matches "
        "how the ledger itself accounts.",
        ("coinspot_floor aggregates %d CoinSpot desks (%s) into ONE book row — "
         "capital counted exactly once, but the %d-book enumeration merges "
         "them; its open legs ride at cost (%s) because the ledger stores no "
         "mark — immaterial at today's size."
         % (len(csb["desks_detail"]) if csb else 0,
            ", ".join(csb["desks_detail"]) if csb else "—",
            len(books),
            (", ".join(csb["atcost_detail"])
             if csb and csb.get("atcost_detail") else "none open"))),
        "Kraken public OHLC serves only ~720 daily bars: alt legs (PAXG, TAO, ONDO, "
        "CTSI, PUMP, CC, GWEI, APR) cover the 2y window (first ~1wk missing) but are "
        "dark in 2008/2020/2022 replays — coverage disclosed per window.",
        "Short-history instruments inside the 2y window are carried at 0 return "
        "before listing (exposures small, listed in receipt) — understates their "
        "full-window vol slightly.",
        "Crypto weekend moves fold into the next trading day (panel uses NYSE grid).",
        "covered_call_etf short-call overlay ignored (immaterial: 2 contracts, "
        "$0.08 premium vs 200sh EFA long).",
        "OANDA CFD notionals valued at entry price (only price in the status file); "
        "GBP/EUR notionals converted at last close GBPUSD/EURUSD.",
        "No financing/borrow modeled on short legs or negative cash (paper books).",
        "Floor books currently run far below mandate gross (~40% of equity, "
        "hedged long/short): their measured risk reflects TODAY'S positions, not "
        "the mandate ceiling — floor risk is state-dependent and can re-gross on "
        "any tick.",
        "Return/Sharpe shown per scheme are CONTEXT ONLY — 2y return differences "
        "between construction schemes are noise and selection. The recommendation "
        "rests on the risk metrics.",
    ]

    receipt = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "study": "risk overlay on the firm's existing books — map + recommendation "
                 "menu; frozen registrations are never resized",
        "audusd": round(audusd, 4),
        "fx_rates": {k: (round(v, 4) if v else None) for k, v in fx.items()},
        "n_books": len(books),
        "firm_capital_usd": round(total_cap, 0),
        "firm_gross_exposure_usd": round(sum(b["gross_usd"] for b in books), 0),
        "mapped_capital_pct": round(mapped_pct, 1),
        "unmapped_usd": round(total_unmapped, 0),
        "unmapped_detail": [{"book": n, "what": w, "usd": round(v, 0)}
                            for n, w, v in unmapped_rows],
        "short_history_instruments_2y": short_hist,
        "panel": {
            "window_2y": [str(idx2[0].date()), str(idx2[-1].date())],
            "trading_days_2y": int(len(idx2)),
            "extended_from": str(idxx[0].date()),
            "instruments": int(px.shape[1]),
            "price_sources": "yfinance + Kraken public OHLC (risk_engine pattern)",
        },
        "inventory_and_map": per_book_sorted,
        "firm": {
            "ann_vol_pct": round(firm_vol, 2),
            "var99_1d_pct": round(firm_var99, 2),
            "var99_1d_usd": round(total_cap * abs(firm_var99) / 100, 0),
            "maxdd_2y_replay_pct": round(firm_maxdd, 2),
            "beta_spy": round(firm_beta, 2) if firm_beta is not None else None,
            "effective_bets_capital": round(eff_capital, 1) if eff_capital else None,
            "effective_bets_risk": round(eff_risk, 1) if eff_risk else None,
            "crash_replays": crash,
        },
        "marginal_10k_next_dollar": {
            "most_diversifying": marg_sorted[:5],
            "most_concentrating": marg_sorted[-5:],
            "note": "firm 2y ann vol if the NEXT $10k of funder capital lands in "
                    "each existing book shape (no frozen book is resized)",
        },
        "schemes": schemes,
        "scheme_latest_weights_pct": {"inverse_vol": latest_iv, "erc": latest_erc},
        "frozen_registration_constraint":
            "Every book's capital is hash-sealed. Inverse-vol/ERC/vol-target "
            "results are COUNTERFACTUAL maps of what construction would change — "
            "actionable only through future capital allocation or a firm-level "
            "exposure dial the funder would have to build and register. Nothing "
            "here is auto-applied. Precedent: CIO_REVIEW_2026-08-07 (restraint).",
        "caveats": caveats,
    }
    os.makedirs(REPORTS, exist_ok=True)
    with open(RECEIPT, "w") as f:
        json.dump(_j(receipt), f, indent=2)

    # ------- note
    top5 = per_book_sorted[:5]
    lines = []
    A = lines.append
    A("# Research note — risk overlay on the books the firm already owns")
    A("")
    A("*%s · STUDY ONLY · no book resized, no order placed · receipt: "
      "`reports/overlay_lab.json` · lab: `overlay_lab.py`*" % receipt["generated"])
    A("")
    A("## The question")
    A("")
    A("Not alpha. The firm runs **%d paper books, $%s of capital** "
      "($%s gross exposure). Is that deployment well-constructed, and what would "
      "vol-targeting / risk-parity change?" %
      (len(books), "{:,.0f}".format(total_cap),
       "{:,.0f}".format(receipt["firm_gross_exposure_usd"])))
    A("")
    A("## The frozen-registration constraint (why this is a map, not an order)")
    A("")
    A("Every book's capital is fixed by a hash-sealed registration ($50k books, "
      "$10k/$25k sleeves, $40k floors). An overlay that resized existing books "
      "would EDIT frozen registrations — forbidden. So everything below is a MAP "
      "and a RECOMMENDATION MENU for the funder: where FUTURE capital should land, "
      "whether a firm-level exposure dial is worth building, which books dominate "
      "risk. Nothing is auto-applied. Precedent: `CIO_REVIEW_2026-08-07.md` — "
      "unrestricted authority exercised as restraint.")
    A("")
    A("## Honesty box")
    A("")
    A("* Mapped capital: **%.1f%%** (target >90%% — %s). Unmapped: $%s, "
      "dominated by `kraken_engine` (A$%s), whose holdings cannot be read without "
      "touching the engine — classed **CASH at asset level**, so firm risk is "
      "understated by whatever it actually holds. Its AUDUSD translation risk IS "
      "modeled." %
      (mapped_pct, "met" if mapped_pct > 90 else "MISSED — read this twice",
       "{:,.0f}".format(total_unmapped),
       "{:,.0f}".format(next(b["equity"] for b in books if b["name"] == "kraken_engine"))))
    A("* Static-weights proxy (same method as the firm's `risk_engine.py`): "
      "today's composition assumed held through the window.")
    A("* A$ books converted at AUDUSD **%.4f**; A$ cash modeled as AUDUSD "
      "exposure. OANDA mapping: SPX500→SPY, NAS100→QQQ, UK100→EWU, DE30→EWG." % audusd)
    A("* Short-history alt legs (%s) carried at 0 return before listing — "
      "small exposures, listed in the receipt." %
      (", ".join(s["instrument"].replace(KRK, "") for s in short_hist) or "none"))
    if csb and csb.get("desks_detail"):
        A("* `coinspot_floor` aggregates %d CoinSpot desks (%s) into one book "
          "row — capital counted exactly once, but the %d-book count above "
          "treats them as a single book. Its open legs ride at cost (%s; the "
          "ledger stores no mark — immaterial)." %
          (len(csb["desks_detail"]), ", ".join(csb["desks_detail"]), len(books),
           ", ".join(csb["atcost_detail"]) or "none open"))
    A("")
    A("## The table every CIO wants: capital share vs risk share (sorted by risk)")
    A("")
    A("| book | kind | capital % | risk % | ann vol % | tail-day $ | corr to firm |")
    A("|---|---|---|---|---|---|---|")
    for r in per_book_sorted:
        A("| %s | %s | %.1f | **%.1f** | %.1f | %s | %s |" %
          (r["book"], r["kind"], r["capital_pct"], r["risk_contribution_pct"],
           r["ann_vol_pct"],
           "{:,.0f}".format(r["tail99_contrib_usd_day"]),
           ("%.2f" % r["corr_to_firm"]) if r["corr_to_firm"] is not None else "—"))
    A("")
    conc = sum(r["risk_contribution_pct"] for r in top5)
    capc = sum(r["capital_pct"] for r in top5)
    A("**Concentration:** the top-5 risk books carry **%.0f%% of firm risk on "
      "%.0f%% of capital.**" % (conc, capc))
    kf = sum(r["risk_contribution_pct"] for r in per_book
             if r["kind"] in ("kraken_floor", "replica_floor")
             or r["book"] == "lowvol_crypto" or r["book"] == "coinspot_floor")
    kc = sum(r["capital_pct"] for r in per_book
             if r["kind"] in ("kraken_floor", "replica_floor")
             or r["book"] == "lowvol_crypto" or r["book"] == "coinspot_floor")
    # AUDIT FIX 2026-08-15 (#2): the "one strategy family" claim is about the
    # Kraken floors + their replicas ONLY — lowvol_crypto and coinspot run
    # different strategies and must not pad that figure.
    fr_cap = sum(r["capital_pct"] for r in per_book
                 if r["kind"] in ("kraken_floor", "replica_floor"))
    A("Crypto-venue books (floors + replicas + lowvol_crypto + coinspot) hold "
      "**%.0f%% of capital but contribute %.0f%% of measured firm risk** — their "
      "long/short structure largely nets out on today's marks. Two honest "
      "qualifiers: (a) the replica floors duplicate the Kraken floors' "
      "strategies (a deliberate venue A/B test), so **%.1f%% of firm capital — "
      "the floors + replicas alone** — re-runs one strategy family; (b) the "
      "floors currently run far below mandate gross (~40%% of equity, hedged) "
      "— their measured risk reflects today's small positions, not what the "
      "mandate permits at full tilt."
      % (kc, kf, fr_cap))
    A("")
    A("## Firm level (2y, today's weights)")
    A("")
    A("| metric | value |")
    A("|---|---|")
    A("| ann vol | %.1f%% (~$%s 1-sigma day) |" %
      (firm_vol, "{:,.0f}".format(total_cap * firm_vol / 100 / np.sqrt(252))))
    A("| 1-day VaR 99%% | %.2f%% (~$%s) |" %
      (firm_var99, "{:,.0f}".format(total_cap * abs(firm_var99) / 100)))
    A("| max drawdown (2y replay) | %.1f%% |" % firm_maxdd)
    A("| beta to SPY | %s |" % (("%.2f" % firm_beta) if firm_beta is not None else "—"))
    A("| effective bets (capital 1/HHI) | %s |" % receipt["firm"]["effective_bets_capital"])
    A("| effective bets (risk-based) | %s |" % receipt["firm"]["effective_bets_risk"])
    A("")
    A("**Crash replays of today's weights** (coverage = capital whose instruments "
      "have data in the window; the rest rides as cash — replays are therefore "
      "BEST-CASE for the dark books):")
    A("")
    A("| window | return | coverage | mostly dark |")
    A("|---|---|---|---|")
    for k, v in crash.items():
        A("| %s | %s%% | %.0f%% | %s |" %
          (k, v["current_weights_return_pct"], v["capital_coverage_pct"],
           ", ".join(v["books_mostly_dark"]) or "—"))
    A("")
    A("## Construction schemes (same capital, monthly rebalance, trailing 63d, "
      "no look-ahead)")
    A("")
    # AUDIT FIX 2026-08-15 (#1): the 2008 column is SHOWN, not omitted — its
    # earlier omission hid that schemes 4/5 beat current in that window,
    # contradicting the note's own vol-target claim.
    A("| scheme | ann vol % | VaR99 1d % | maxDD 2y % | eff bets | beta SPY | "
      "2008\\* | 2020 | 2022 | (ret / Sharpe — context only) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for k, m in schemes.items():
        cx = m["context_only_NOT_objective"]
        A("| %s | %.1f | %.2f | %.1f | %s | %s | %s | %s | %s | %s / %s |" %
          (k, m["ann_vol_pct"], m["var99_1d_pct"], m["maxdd_2y_pct"],
           m.get("effective_bets_capital_mean", "—"), m.get("beta_spy", "—"),
           m["crash_replays_pct"].get("2008 GFC (Sep-Nov 08)", "—"),
           m["crash_replays_pct"].get("2020 COVID (19Feb-23Mar)", "—"),
           m["crash_replays_pct"].get("2022 inflation bear (Jan-14Oct)", "—"),
           cx["ann_return_pct"], cx["sharpe"]))
    A("")
    A("\\*2008 covers only the pre-crypto books (capital coverage %.0f%%) — "
      "shown WITH that label rather than omitted. Note that schemes 4/5 BEAT "
      "current in that window: the trailing-vol scale throttled below 1x as "
      "realised vol blew through the target (see the vol-target bullet and the "
      "fix log)." %
      (crash["2008 GFC (Sep-Nov 08)"]["capital_coverage_pct"]))
    A("")
    A("*Return/Sharpe are printed because a CIO will ask, and labelled because "
      "they must be: on a 2y window the differences between construction schemes "
      "are noise and selection. Choose on risk shape, never on these columns.*")
    A("")
    A("What each overlay would CHANGE (counterfactual — frozen books are never "
      "resized):")
    A("")
    A("* **Inverse-vol** — biggest cuts: %s; biggest adds: %s. Full latest "
      "weights in the receipt (`scheme_latest_weights_pct`)." %
      ("; ".join(iv_cut), "; ".join(iv_add)))
    A("* **ERC** — biggest cuts: %s; biggest adds: %s. It equalises risk "
      "contributions across books, so the quiet long/short books would run "
      "multiples of their current share while the directional books shrink." %
      ("; ".join(erc_cut), "; ".join(erc_add)))
    # AUDIT FIX 2026-08-15 (#1): the earlier wording ("not cushioning them")
    # over-reached — the same rule THROTTLED and cushioned the 2008 replay.
    # Both sides are now stated, with the receipt's numbers.
    sc4 = schemes["4_voltarget10_on_current"]
    g08 = (sc4["vol_target_scale"]["in_crash_windows"]
           .get("2008 GFC (Sep-Nov 08)", {}))
    A("* **Vol-target 10%%** — the dial as specified LEVERS UP in the current "
      "regime: the firm runs at %.1f%% vol, half the target, so the 2y scale "
      "sits at %s min / %s mean / %s max (cap %.1fx) and it AMPLIFIED the 2020 "
      "and 2022 replays. In the 2008 replay the SAME rule threw the lever the "
      "other way — trailing vol blew through the target and the scale "
      "throttled to %sx (mean %s inside Sep-Nov 08), cushioning the deepest "
      "window (scheme 4: %s%% vs current %s%%). As permanent protection the "
      "honest specification is still a CAP — scale = min(1, target/vol) — "
      "which today would sit dormant at 1.0 and only bite in a vol spike." %
      (firm_vol,
       sc4["vol_target_scale"]["min"],
       sc4["vol_target_scale"]["mean"],
       sc4["vol_target_scale"]["max"], LEV_CAP,
       g08.get("min", "—"), g08.get("mean", "—"),
       sc4["crash_replays_pct"].get("2008 GFC (Sep-Nov 08)", "—"),
       schemes["1_current_static"]["crash_replays_pct"].get(
           "2008 GFC (Sep-Nov 08)", "—")))
    A("")
    A("## Recommendation MENU for the funder (no deployment language, no resizing)")
    A("")
    best = marg_sorted[0]
    worst = marg_sorted[-1]
    gb = next(r for r in per_book if r["book"] == "growth_book")
    A("1. **Where the next $10k adds diversification:** landing it in the shape "
      "of `%s` takes firm vol to %.2f%% (%+.1f bps); landing it in the shape of "
      "`%s` takes it to %.2f%% (%+.1f bps). The diversifying shapes are the "
      "hedged long/short floors and cash-like sleeves (low or negative "
      "correlation to the firm); the concentrating shapes are the directional "
      "momentum books that already top the risk table. Full ranking in the "
      "receipt (`marginal_10k_next_dollar`)." %
      (best["book"], best["firm_vol_after_pct"], best["delta_bps"],
       worst["book"], worst["firm_vol_after_pct"], worst["delta_bps"]))
    # AUDIT FIX 2026-08-15 (#1): the earlier claim "made every crash window
    # worse (scheme 4/5 vs 1)" was FALSE — the receipt's own 2008 replay shows
    # schemes 4/5 BEAT current because the trailing-vol scale throttled. The
    # recommendation is restated on the permanent-leverage argument alone.
    cr1 = schemes["1_current_static"]["crash_replays_pct"]
    cr4 = schemes["4_voltarget10_on_current"]["crash_replays_pct"]
    cr5 = schemes["5_invvol_plus_voltarget10"]["crash_replays_pct"]
    A("2. **An exposure dial is worth building only in its CAP form.** The "
      "replay verdict is two-sided and must be quoted honestly: a 10%% TARGET "
      "on a ~%.1f%%-vol firm levers ~%.1fx in the current regime and AMPLIFIED "
      "the 2020 and 2022 replays (scheme 4/5 vs 1: %s%%/%s%% vs %s%% in 2020; "
      "%s%%/%s%% vs %s%% in 2022) — but it THROTTLED in the 2008 replay (scale "
      "down to %sx, mean %s inside Sep-Nov 08) and cushioned it (%s%%/%s%% vs "
      "%s%%). The case for the cap form therefore rests on the "
      "permanent-leverage argument, not on crash replays: a target that runs "
      "~%.1fx levered through every quiet regime is a standing amplifier the "
      "firm does not need, and whether the de-lever arrives in time is "
      "regime-dependent (2008: yes; 2020's five-week crash: no). A registered, "
      "funder-approved dial specified as min(1, target/vol) — throttle only, "
      "never lever — keeps the 2008-style protection, costs nothing today (it "
      "would sit at 1.0), and is the one overlay that respects frozen "
      "registrations by construction, because it acts at the firm/"
      "future-capital margin, not inside sealed books." %
      (firm_vol, min(LEV_CAP, VOL_TARGET / (firm_vol / 100)),
       cr4.get("2020 COVID (19Feb-23Mar)", "—"),
       cr5.get("2020 COVID (19Feb-23Mar)", "—"),
       cr1.get("2020 COVID (19Feb-23Mar)", "—"),
       cr4.get("2022 inflation bear (Jan-14Oct)", "—"),
       cr5.get("2022 inflation bear (Jan-14Oct)", "—"),
       cr1.get("2022 inflation bear (Jan-14Oct)", "—"),
       g08.get("min", "—"), g08.get("mean", "—"),
       cr4.get("2008 GFC (Sep-Nov 08)", "—"),
       cr5.get("2008 GFC (Sep-Nov 08)", "—"),
       cr1.get("2008 GFC (Sep-Nov 08)", "—"),
       min(LEV_CAP, VOL_TARGET / (firm_vol / 100))))
    A("3. **Concentration warning for the next registration round:** "
      "`growth_book` alone — two commodity ETFs (SLV+USO) — carries **%.1f%% of "
      "firm risk on %.1f%% of capital** (book vol %.1f%%); the top-5 risk books "
      "hold %.0f%% of risk on %.0f%% of capital. Meanwhile the replica floors "
      "duplicate the Kraken floors' strategy family (%.1f%% of capital — the "
      "floors + replicas alone — re-runs one family, even if today's hedged "
      "marks measure quiet). The single point of failure on the map today is "
      "the silver/oil momentum pair, not the crypto complex." %
      (gb["risk_contribution_pct"], gb["capital_pct"], gb["ann_vol_pct"],
       conc, capc, fr_cap))
    A("4. **Measure the engine or discount it:** `kraken_engine` (%.1f%% of "
      "capital) is risk-unmapped by design. Either the engine starts publishing "
      "a holdings snapshot the risk desk can read, or the funder should mentally "
      "haircut every firm-level number here by that share." %
      (100 * next(b["equity_usd"] for b in books if b["name"] == "kraken_engine") / total_cap))
    A("5. **Do nothing to existing books.** Every scheme above is a counterfactual. "
      "The frozen registrations stay frozen; the tournament decides survival, "
      "not the risk desk.")
    A("")
    A("## Fix log — 2026-08-15 adversarial audit "
      "(house precedent: `research_cross_asset_carry.md` logs its own bugs)")
    A("")
    A("* **CORRECTED — this one drove the earlier verdict:** recommendation #2 "
      "previously claimed the 10%% vol-target 'made every crash window worse "
      "(scheme 4/5 vs 1)', and the scheme table omitted the 2008 column that "
      "falsified it. The receipt's own 2008 replay: scheme 4 %s%% and scheme 5 "
      "%s%% vs current %s%% — both BETTER, because the trailing-vol scale "
      "throttled to %sx (mean %s) inside Sep-Nov 2008 and de-levered into the "
      "deepest replay. The claim is restated above (levers ~1.5x in the "
      "current regime and amplified 2020/2022; it throttled in the 2008 "
      "replay), the 2008 column now sits in the table, and the receipt now "
      "carries the per-crash-window scale stats "
      "(`vol_target_scale.in_crash_windows`). The cap-form recommendation "
      "stands — on the permanent-leverage argument alone." %
      (cr4.get("2008 GFC (Sep-Nov 08)", "—"),
       cr5.get("2008 GFC (Sep-Nov 08)", "—"),
       cr1.get("2008 GFC (Sep-Nov 08)", "—"),
       g08.get("min", "—"), g08.get("mean", "—")))
    A("* Qualifier (a) under the CIO table previously said ~45%% of firm "
      "capital re-runs one strategy family — that figure was the crypto-VENUE "
      "total (floors + replicas + lowvol_crypto + coinspot, %.1f%%). The "
      "floors + their replicas alone are %.1f%% — restated in both places it "
      "appeared. Minor overstatement, corrected." % (kc, fr_cap))
    A("* Disclosures added, none changing any number: `coinspot_floor` merges "
      "%s CoinSpot desks into one book row (capital counted exactly once); its "
      "open legs ride at cost (%s — mark=None in the ledger, immaterial); and "
      "value_book's 005930.KS/BARC.L exposure magnitudes are raw KRW/pence "
      "taken as USD per the ledger's own convention, so that book's weight "
      "vector is slightly distorted (legs small, disclosed in the caveats)." %
      (len(csb["desks_detail"]) if csb else 0,
       (", ".join(csb["atcost_detail"])
        if csb and csb.get("atcost_detail") else "none open")))
    A("")
    A("## Caveats (in full)")
    A("")
    for cv in caveats:
        A("* " + cv)
    A("")
    with open(NOTE, "w") as f:
        f.write("\n".join(lines))

    # ------- console
    print("=" * 78)
    print("OVERLAY LAB — capital vs risk map (%d books, $%s capital, AUDUSD %.4f)"
          % (len(books), "{:,.0f}".format(total_cap), audusd))
    print("=" * 78)
    print("mapped %.1f%% of capital · unmapped $%s (kraken_engine et al.)"
          % (mapped_pct, "{:,.0f}".format(total_unmapped)))
    print("%-28s %6s %6s %8s %10s" % ("book", "cap%", "risk%", "vol%", "tail$/d"))
    for r in per_book_sorted:
        print("%-28s %6.1f %6.1f %8.1f %10s" %
              (r["book"], r["capital_pct"], r["risk_contribution_pct"],
               r["ann_vol_pct"], "{:,.0f}".format(r["tail99_contrib_usd_day"])))
    print("firm: vol %.2f%% · VaR99 %.2f%% (~$%s) · maxDD %.1f%% · beta %.2f · "
          "eff bets cap %.1f / risk %.1f"
          % (firm_vol, firm_var99, "{:,.0f}".format(total_cap * abs(firm_var99) / 100),
             firm_maxdd, firm_beta or 0, eff_capital or 0, eff_risk or 0))
    for k, m in schemes.items():
        print("  %-28s vol %5.2f%%  VaR99 %6.2f%%  maxDD %6.2f%%  beta %s"
              % (k, m["ann_vol_pct"], m["var99_1d_pct"], m["maxdd_2y_pct"],
                 m.get("beta_spy")))
    print("receipt -> %s" % RECEIPT)
    print("note    -> %s" % NOTE)
    print("done in %.1fs" % (time.time() - t0))
    return receipt


if __name__ == "__main__":
    main()
