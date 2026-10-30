#!/usr/bin/env python3
"""HONEST10 PAPER forward-test books RP10 and BOOKA. PAPER ONLY: no orders, no keys, public prices only.

Brett approval 2026-10-24 10:12 WITA. Research + rules: research/honest10/REPORT.md (bt.py variants
"RP SPY+TLT+GLD vt10", "SECTOR lb252 top3", "CTREND f20 s100 t30"). Each book is notionally A$1,000,000 with its own
sqlite ledger and daily equity CSV in A$ after costs. Not firm capital: not in firm equity, firm_risk or funder fee.

  RP10  : risk parity SPY/TLT/GLD (inverse 60d vol), scaled to 10% vol (60d, cap 3x), monthly at the last US close.
  BOOKA : 40% RP10 weights + 30% SECTOR (top 3 of 9 SPDRs by 252d momentum, only if >0, else that third in cash, monthly)
          + 30% crypto trend stand-in CTREND (BTC/ETH: SMA20>SMA100, weight = 0.30/(2*vol30*sqrt365), cap 0.5 per coin,
          traded at each Kraken daily close only when the summed weight change > 0.05). Sleeves reset to 40/30/30 monthly.
Costs (as research): ETFs 5bp per side, Kraken 50bp per side. A$ basis as research (AUD-hedged): book earns USD excess
return + RBA cash; exposure pays USD cash (^IRX); gross above 1x pays +1.5%/yr. Daily P&L converted at that day's AUD/USD.
Fills: at official closes only. First fill at the US close on/after 2026-10-26 (ET) and, for BOOKA's crypto, the Kraken
daily bar of the same date (closes 00:00 UTC next day). Weekends: crypto marks/trades only, ETFs never fill on a weekend.
Idempotent: each bar date is processed once (processed table, PK) under a file lock; reruns are no-ops.
Commands: honest10_books.py run | status      (HONEST10_STATE_DIR overrides ~/.tfo_honest10)
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse, csv, datetime as dt, fcntl, json, math, os, socket, sqlite3, sys, urllib.request
# TFO DoH hook 2026-10-28 (desk_doh: process-local DNS-over-HTTPS for venue/data hosts; kill switch TFO_DESK_DOH=0)
try:
    import sys as _s; _s.path.insert(0, "/Users/test/Applications/MultiVenueDesk") if "/Users/test/Applications/MultiVenueDesk" not in _s.path else None
    import desk_doh as _doh; _doh.install()
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)

import numpy as np, pandas as pd

UTC = dt.timezone.utc
STATE = os.environ.get("HONEST10_STATE_DIR") or os.path.expanduser("~/.tfo_honest10")
START = dt.date(2026, 10, 26)
BOOK0 = 1_000_000.0
BAR_PASS_CAGR, BAR_DD_AUD, BAR_MONTHS = 0.10, -150_000.0, 12
RP = ["SPY", "TLT", "GLD"]
SEC = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU"]
CR = ["BTC", "ETH"]
COST_BP = {**{s: 5.0 for s in RP + SEC}, "BTC": 50.0, "ETH": 50.0}
AUD_CASH = {2026: 0.0360, 2027: 0.0360}   # research table (RBA approx); 2027 assumed flat
FIN_SPREAD = 0.015
US_HOLIDAYS = {dt.date(*d) for d in [(2026,11,26),(2026,12,25),(2027,1,1),(2027,1,18),(2027,2,15),(2027,3,26),(2027,5,31),
               (2027,6,18),(2027,7,5),(2027,9,6),(2027,11,25),(2027,12,24),(2028,1,17)]}
BOOKS = {"RP10": dict(rp=1.0, sec=0.0, cr=0.0), "BOOKA": dict(rp=0.4, sec=0.3, cr=0.3)}
CRYPTO_SOURCE = "CTREND stand-in (research BTC/ETH SMA20/100 trend); D4 live signals not used: D4 is a per-trade swing book (A$100k, stops), not a weight signal"

def is_us_day(d): return d.weekday() < 5 and d not in US_HOLIDAYS
def next_us_day(d):
    d += dt.timedelta(days=1)
    while not is_us_day(d): d += dt.timedelta(days=1)
    return d
def is_month_end(d): return is_us_day(d) and next_us_day(d).month != d.month

# ---------------- signals (match research/honest10/bt.py) ----------------
def rp_weights(px):
    """px: daily closes of SPY/TLT/GLD (US trading days) up to and incl. decision date."""
    r = px[RP].pct_change().dropna()
    if len(r) < 125: raise ValueError("not enough history for RP")
    iv = 1 / r.rolling(60).std(); raw = iv.div(iv.sum(axis=1), axis=0)
    port = (raw.shift(1) * r).sum(axis=1).iloc[-60:]
    vol = port.std() * math.sqrt(252)
    k = min(0.10 / vol, 3.0)
    return {s: float(raw.iloc[-1][s] * k) for s in RP}

def sector_weights(px):
    p = px[SEC].dropna()
    if len(p) < 253: raise ValueError("not enough history for SECTOR")
    m = p.iloc[-1] / p.iloc[-253] - 1
    top = m.sort_values(ascending=False).index[:3]
    return {s: (1/3 if (s in top and m[s] > 0) else 0.0) for s in SEC}

def ctrend_target(cpx):
    """cpx: Kraken daily closes BTC/ETH by UTC bar date, up to and incl. decision bar. Returns sleeve weights."""
    c = cpx[CR].dropna()
    if len(c) < 101: raise ValueError("not enough history for CTREND")
    sig = (c.rolling(20).mean() > c.rolling(100).mean()).iloc[-1]
    vol = c.pct_change().rolling(30).std().iloc[-1] * math.sqrt(365)
    return {s: float(min(0.5, (1.0 if sig[s] else 0.0) / vol[s] / 2 * 0.30)) for s in CR}

# ---------------- data (public, bounded timeouts) ----------------
def load_etf():
    import yfinance as yf
    try: yf.set_tz_cache_location(os.path.join(STATE, "yf_tz"))   # launchd-safe tz cache (same fix as universe_asia)
    except Exception: pass
    syms = RP + SEC + ["^IRX", "AUDUSD=X"]
    d = yf.download(syms, period="2y", auto_adjust=True, progress=False, timeout=20, threads=False)["Close"]
    d.index = [x.date() for x in pd.to_datetime(d.index)]
    return d

def load_crypto():
    out = {}
    for s, pair in [("BTC", "XBTUSD"), ("ETH", "ETHUSD")]:
        with urllib.request.urlopen(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440", timeout=20) as f:
            j = json.load(f)
        if j.get("error"): raise RuntimeError(j["error"])
        rows = [v for k, v in j["result"].items() if k != "last"][0]
        now = dt.datetime.now(UTC).timestamp()
        out[s] = {dt.datetime.fromtimestamp(int(r[0]), UTC).date(): float(r[4]) for r in rows if int(r[0]) + 86400 <= now}
    return pd.DataFrame(out).sort_index()

# ---------------- ledger ----------------
SCHEMA = """create table if not exists book(k text primary key, v text);
create table if not exists processed(d text primary key, ts_utc text, equity_aud real);
create table if not exists positions(sym text primary key, units real, last_px real, sleeve text);
create table if not exists fills(id integer primary key, d text, ts_utc text, sym text, side text, units real, px_usd real,
  audusd real, notional_aud real, cost_aud real, reason text, unique(d, sym, reason));
create table if not exists equity_daily(d text primary key, equity_aud real, pnl_aud real, carry_aud real, cost_aud real,
  gross_aud real, cash_aud real, peak_aud real, dd_aud real);"""

def conn(book):
    os.makedirs(STATE, exist_ok=True)
    c = sqlite3.connect(os.path.join(STATE, f"{book}.sqlite"), isolation_level=None); c.executescript(SCHEMA); return c
def bk(c, k, default=None):
    r = c.execute("select v from book where k=?", (k,)).fetchone(); return json.loads(r[0]) if r else default
def setbk(c, k, v): c.execute("insert or replace into book(k,v) values(?,?)", (k, json.dumps(v)))
def now_s(): return dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

def last_le(df, col, d):
    s = df[col].dropna(); s = s[[x <= d for x in s.index]]
    return (float(s.iloc[-1]), s.index[-1]) if len(s) else (None, None)

def processable_dates(book, etf, cry, now_utc, last_done):
    """Dates to process, in order. Stops at the first date whose data is not final yet."""
    d = max(START, (dt.date.fromisoformat(last_done) + dt.timedelta(days=1)) if last_done else START)
    out = []
    while True:
        if now_utc < dt.datetime(d.year, d.month, d.day, tzinfo=UTC) + dt.timedelta(days=1, minutes=30): break
        us = is_us_day(d)
        if us and d not in set(etf["SPY"].dropna().index): break          # weekday close not in yet: wait
        need_c = BOOKS[book]["cr"] > 0
        if need_c and d not in set(cry["BTC"].dropna().index) | set(cry["ETH"].dropna().index): break
        if need_c and (d not in set(cry["BTC"].dropna().index) or d not in set(cry["ETH"].dropna().index)): break
        if us or need_c: out.append(d)
        d += dt.timedelta(days=1)
    return out

def step(c, book, d, etf, cry, log):
    cfg = BOOKS[book]; ds = d.isoformat()
    if c.execute("select 1 from processed where d=?", (ds,)).fetchone(): return
    us = is_us_day(d)
    started = bk(c, "started")
    if not started and not us: return            # never start on a weekend/holiday
    c.execute("begin immediate")
    try:
        fx, _ = last_le(etf, "AUDUSD=X", d)       # USD per AUD
        irx, _ = last_le(etf, "^IRX", d); usd_r = max(0.0, (irx or 0) / 100)
        aud_r = AUD_CASH.get(d.year, 0.036)
        pos = {r[0]: dict(units=r[1], last_px=r[2], sleeve=r[3]) for r in c.execute("select sym,units,last_px,sleeve from positions")}
        def px(sym):
            if sym in CR: return float(cry.loc[d, sym])
            return last_le(etf, sym, d)[0]
        E = bk(c, "equity_aud", BOOK0); last_d = bk(c, "last_d")
        days = (d - dt.date.fromisoformat(last_d)).days if last_d else 0
        # 1) mark
        pnl_usd = 0.0; cr_pnl_usd = 0.0
        for s, p in pos.items():
            q = px(s); dp = p["units"] * (q - p["last_px"]); pnl_usd += dp; p["last_px"] = q
            if s in CR: cr_pnl_usd += dp
        pnl = pnl_usd / fx
        gross = sum(abs(p["units"] * p["last_px"]) for p in pos.values()) / fx
        carry = (E * aud_r - gross * usd_r - max(0.0, gross - E) * FIN_SPREAD) * days / 365.0
        E = E + pnl + carry
        csl = bk(c, "crypto_sleeve_aud", 0.0)
        if cfg["cr"] and started:   # crypto sleeve capital tracks its own P&L + carry
            cr_gross = sum(abs(pos[s]["units"] * pos[s]["last_px"]) for s in CR if s in pos) / fx
            csl = csl + cr_pnl_usd / fx + (csl * aud_r - cr_gross * usd_r) * days / 365.0
        costs = 0.0
        def trade_to(targets_aud, reason):
            nonlocal costs
            for s, tgt in targets_aud.items():
                q = px(s); cur = pos.get(s, dict(units=0.0, last_px=q, sleeve=""))
                new_units = tgt * fx / q; du = new_units - cur["units"]
                if abs(du * q / fx) < 1.0: continue
                notional = abs(du * q) / fx; cost = notional * COST_BP[s] / 1e4; costs += cost
                c.execute("insert into fills(d,ts_utc,sym,side,units,px_usd,audusd,notional_aud,cost_aud,reason) values(?,?,?,?,?,?,?,?,?,?)",
                          (ds, now_s(), s, "buy" if du > 0 else "sell", abs(du), q, fx, notional, cost, reason))
                pos[s] = dict(units=new_units, last_px=q, sleeve="crypto" if s in CR else "etf")
        # 2) trades
        if not started or (us and is_month_end(d)):
            reason = "initial" if not started else "month_end_rebalance"
            tg = {}
            w = rp_weights(etf.loc[[x for x in etf.index if x <= d], RP].dropna())
            for s in RP: tg[s] = cfg["rp"] * w[s] * E
            if cfg["sec"]:
                w = sector_weights(etf.loc[[x for x in etf.index if x <= d], SEC].dropna())
                for s in SEC: tg[s] = cfg["sec"] * w[s] * E
            for s in list(pos):
                if s not in tg and s not in CR: tg[s] = 0.0
            if cfg["cr"]:
                csl = cfg["cr"] * E
                w = ctrend_target(cry.loc[[x for x in cry.index if x <= d]])
                for s in CR: tg[s] = w[s] * csl
                setbk(c, "crypto_w", w)
            trade_to(tg, reason)
            if not started: setbk(c, "started", ds)
            log(f"{book} {ds} {reason}: {len(tg)} targets")
        elif cfg["cr"]:
            w = ctrend_target(cry.loc[[x for x in cry.index if x <= d]])
            cur = {s: (pos[s]["units"] * pos[s]["last_px"] / fx / csl if s in pos and csl > 0 else 0.0) for s in CR}
            if sum(abs(w[s] - cur[s]) for s in CR) > 0.05:
                trade_to({s: w[s] * csl for s in CR}, "crypto_band"); setbk(c, "crypto_w", w)
                log(f"{book} {ds} crypto band trade -> {w}")
        E -= costs
        if cfg["cr"]: csl -= sum(r[0] for r in c.execute("select cost_aud from fills where d=? and sym in ('BTC','ETH')", (ds,)))
        for s, p in pos.items():
            c.execute("insert or replace into positions(sym,units,last_px,sleeve) values(?,?,?,?)", (s, p["units"], p["last_px"], p["sleeve"]))
        gross = sum(abs(p["units"] * p["last_px"]) for p in pos.values()) / fx
        peak = max(bk(c, "peak_aud", BOOK0), E)
        setbk(c, "equity_aud", E); setbk(c, "peak_aud", peak); setbk(c, "last_d", ds); setbk(c, "crypto_sleeve_aud", csl)
        setbk(c, "min_dd_aud", min(bk(c, "min_dd_aud", 0.0), E - peak))
        c.execute("insert into equity_daily values(?,?,?,?,?,?,?,?,?)", (ds, E, pnl, carry, costs, gross, E - gross, peak, E - peak))
        c.execute("insert into processed values(?,?,?)", (ds, now_s(), E))
        c.execute("commit")
    except Exception:
        c.execute("rollback"); raise

def write_csv(c, book):
    rows = c.execute("select * from equity_daily order by d").fetchall()
    p = os.path.join(STATE, f"{book}_equity.csv"); tmp = p + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "equity_aud", "pnl_aud", "carry_aud", "cost_aud", "gross_exposure_aud", "cash_aud", "peak_aud", "drawdown_aud"])
        for r in rows: w.writerow([r[0]] + [round(x, 2) for x in r[1:]])
    os.replace(tmp, p)

def scorecard(c):
    E = bk(c, "equity_aud", BOOK0); st = bk(c, "started"); last = bk(c, "last_d")
    days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(st)).days if st and last else 0
    ann = ((E / BOOK0) ** (365 / days) - 1) if days >= 30 else None
    dd = bk(c, "min_dd_aud", 0.0)
    review = (dt.date.fromisoformat(st) + dt.timedelta(days=365)).isoformat() if st else None
    if not st: verdict = "NOT STARTED (first fill at the 26 Oct 2026 US close)"
    elif dd < BAR_DD_AUD: verdict = "FAIL (drop worse than -A$150,000)"
    elif days < 365: verdict = "RUNNING (verdict at 12 months)"
    else: verdict = "PASS" if (ann is not None and ann >= BAR_PASS_CAGR) else "FAIL (below 10%/yr)"
    return dict(bar="12 months: >=10%/yr after costs AND worst drop no worse than -A$150,000", started=st, last_mark=last,
                days=days, equity_aud=round(E, 2), pnl_aud=round(E - BOOK0, 2), annualised=ann, worst_drop_aud=round(dd, 2),
                review_date=review, verdict=verdict)

def status_all():
    out = dict(tag="TFO-HONEST10", paper_only=True, generated_utc=now_s(), crypto_source=CRYPTO_SOURCE, books={})
    for b in BOOKS:
        c = conn(b)
        pos = [dict(sym=r[0], units=r[1], px_usd=r[2]) for r in c.execute("select sym,units,last_px from positions where abs(units)>1e-12")]
        fills = [dict(zip(["d", "sym", "side", "units", "px_usd", "notional_aud", "cost_aud", "reason"], r)) for r in
                 c.execute("select d,sym,side,units,px_usd,notional_aud,cost_aud,reason from fills order by id desc limit 20")]
        out["books"][b] = dict(scorecard=scorecard(c), positions=pos, recent_fills=fills,
                               costs_aud=round(sum(r[0] for r in c.execute("select cost_aud from fills")) or 0, 2), last_error=bk(c, "last_error"))
        c.close()
    p = os.path.join(STATE, "status.json"); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(out, f, indent=1, default=str)
    os.replace(tmp, p); return out

def run(etf=None, cry=None, now_utc=None, books=BOOKS):
    os.makedirs(STATE, exist_ok=True)
    logf = open(os.path.join(STATE, "run_log.txt"), "a")
    def log(m): logf.write(f"{now_s()} {m}\n"); print(m)
    lk = open(os.path.join(STATE, ".lock"), "w"); fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        now_utc = now_utc or dt.datetime.now(UTC)
        if now_utc.date() < START + dt.timedelta(days=1) and etf is None:
            log("before first fill date; nothing to do"); status_all(); return
        socket.setdefaulttimeout(30)
        if etf is None: etf = load_etf()
        if cry is None: cry = load_crypto()
        for b in books:
            c = conn(b)
            try:
                _ds = processable_dates(b, etf, cry, now_utc, bk(c, "last_d"))
                for d in _ds: step(c, b, d, etf, cry, log)
                if not _ds:   # 2026-10-29: a silent no-op left the mark stale at 27 Oct; say why
                    log(f"{b} no new date (last {bk(c, 'last_d')}; SPY last {etf['SPY'].dropna().index[-1] if len(etf['SPY'].dropna()) else None}; BTC last {cry['BTC'].dropna().index[-1] if len(cry) else None})")
                setbk(c, "last_error", None)
            except Exception as e:
                setbk(c, "last_error", f"{now_s()} {type(e).__name__}: {e}"); log(f"{b} ERROR {e}")
            write_csv(c, b); c.close()
        status_all()
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); logf.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["run", "status"]); a = ap.parse_args()
    if a.cmd == "run": run()
    else: print(json.dumps(status_all(), indent=1, default=str))
