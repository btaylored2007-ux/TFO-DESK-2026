#!/usr/bin/env python3
"""TFO V2 — SYSTEMATIC paper desk, a SEPARATE paper fund (funder-authorised 2026-09-25). PAPER ONLY.

No broker connection, no real orders, ever. Data: paid feeds first (Massive, EODHD, FMP /stable/; see 'paid feeds'), Kraken public REST, yfinance fallback / the
desk's own native quote layer). Fully rule-based; launchd runs it with no human step.

  tfo_v2.py fund --match-v1 [--execute]   seed reports/v2/tfo_v2_book.json with V1 firm equity (EXTERNAL paper seed)
  tfo_v2.py signals [--setup S1..S4] [--force]   compute due setups -> reports/v2/tfo_v2_staged.json
  tfo_v2.py enter [--execute]             act on staged orders when tape is LIVE and every gate passes
  tfo_v2.py rails [--execute]             half-bank +2% net, trail, stops, time stops, setup exits
  tfo_v2.py mark                          re-mark, refresh ATR/highs, append V2 firm equity row
  tfo_v2.py report                        human summary
  tfo_v2.py coverage                      fetch-only data-coverage probe -> tfo_v2_status.json (no signals, no book)
  tfo_v2.py backtest [S1|S2|S3|S4]        research backtest (v2_backtest.py)
Everything previews without --execute. V2 LAW: see V2_RULES.md.

Files (ALL under reports/v2/, which V1's coverage audit skips by exact path; V2 is not V1 firm capital):
  tfo_v2_book.json  tfo_v2_staged.json  tfo_v2_log.txt  tfo_v2_facts.jsonl  tfo_v2_firm_equity.csv
  tfo_v2_status.json  logs/
V2 NEVER writes V1 files (tfo_head_trader_log.txt, tfo_head_trader_facts.jsonl, any V1 ledger); it READS
reports/firm_equity.csv once, at funding, to match the seed.

Test hooks (never set on the Mac): DESK/BASE (desk root), TFO_V2_MOCK_QUOTES (json quotes file),
TFO_V2_MOCK_BARS (json daily bars), TFO_V2_NOW (ISO UTC clock override),
TFO_V2_FAKE_FETCH_FAIL (comma list of tickers/pairs whose downloads always fail; tests the degraded path).
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse, csv, datetime as dt, json, math, os, re, sys, tempfile, time, traceback, urllib.error, urllib.parse, urllib.request, uuid
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("DESK") or os.environ.get("BASE") or HERE
R = os.path.join(BASE, "reports"); V2 = os.path.join(R, "v2")
for p in (HERE, BASE):
    if p not in sys.path: sys.path.insert(0, p)
import desk_lock
import desk_costs
desk_lock.LOCKDIR = R                    # same lock directory as V1 writers, V2-only lock names
try:
    from paper_execution_price import eligible, receipt
except Exception:                        # the desk ships it; minimal fallback keeps previews alive
    def eligible(q): return isinstance(q, dict) and (q.get("executable") is True or q.get("paper_executable") is True)
    def receipt(q): return {"price_class": q.get("paper_price_class") or "CURRENT PAPER", "price_source_utc": q.get("quote_utc"),
                            "price_received_utc": q.get("fetched_at"), "live_execution": False}

KEY = "tfo_v2"
BOOK = os.path.join(V2, "tfo_v2_book.json"); STAGED = os.path.join(V2, "tfo_v2_staged.json")
LOG = os.path.join(V2, "tfo_v2_log.txt"); FACTS = os.path.join(V2, "tfo_v2_facts.jsonl")
FIRM = os.path.join(V2, "tfo_v2_firm_equity.csv"); STATUS = os.path.join(V2, "tfo_v2_status.json")
CONFIG = os.path.join(BASE, "tfo_v2_config.json")
V1_FIRM = os.path.join(R, "firm_equity.csv")
V1_FILES = {os.path.join(R, "tfo_head_trader_log.txt"), os.path.join(R, "tfo_head_trader_facts.jsonl")}
assert not ({LOG, FACTS} & V1_FILES)
SYD, NY, UTC = ZoneInfo("Australia/Sydney"), ZoneInfo("America/New_York"), dt.timezone.utc
UA = {"User-Agent": "TFO-V2/1.0 (paper research)"}
FIRM_HEADER = ["utc", "equity_usd", "peak_usd", "drawdown_pct", "day_open_equity_usd", "day_pnl_usd", "day_pnl_pct",
               "level", "cash_usd", "deployed_usd", "n_positions"]

# ----------------------------------------------------------------------------- law (defaults; config may only DISABLE / tighten)
# Brett 28 Oct 14:30 WITA: deploy idle V2 cash -> risk_pct 0.005->0.01 (size/setup x2), max_open 10->15, S4 top-N 3->5.
# Per-trade stop (init_k/max_loss_pct 8%), heat 3%, caps, firm rails UNCHANGED; firm theme cap now gated in cmd_enter.
LAW = {"risk_pct": 0.01, "heat_pct": 0.03, "max_open": 15, "s4_top_n": 5, "max_per_cluster": 2, "max_loss_pct": 0.08, "bank_net_pct": 0.02,
       "cap_us": 300000.0, "cap_us_mega": 600000.0, "mega_dv10_usd": 2e9, "mega_spread_bp": 5.0,
       "cap_crypto": 300000.0, "crypto_stack": 500000.0, "max_spread_bp": 20.0, "max_pct_day_dv": 0.05,
       "day_freeze_pct": -3.0, "dd_warn_pct": -10.0, "dd_freeze_new_pct": -10.0, "dd_halt_pct": -20.0,
       "roundtrip_sec": 300, "stop_roundtrip_sec": 60, "live_age_sec": 60,
       # Brett 17 Oct 2026 14:27 (item 10): stocks can gap through the stop at the open. Size so a gap of gap_mult x the stop
       # distance still loses <= risk_pct of equity (sizing, not a tighter stop: a tighter stop does nothing when price
       # gaps through it and only adds whipsaw). Crypto trades 24/7 and keeps 1x.
       "gap_mult_stock": 2.0}
SETUP_DEFAULTS = {
    "S1": {"name": "Crypto trend (20d Donchian > SMA50)", "venue": "KRAKEN", "init_k": 2.5, "trail_k": 3.0, "time_stop": 60, "enabled": True},
    "S2": {"name": "US opening-range gap-and-go", "venue": "US", "init_k": 1.5, "trail_k": 3.0, "time_stop": 0, "enabled": False},
    "S3": {"name": "Post-earnings drift", "venue": "US", "init_k": 2.0, "trail_k": 3.0, "time_stop": 20, "enabled": True},
    "S4": {"name": "ETF relative-strength rotation", "venue": "US", "init_k": 2.5, "trail_k": 3.0, "time_stop": 0, "enabled": True},
}
S1_PAIRS = ["XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "LINK/USD", "AVAX/USD", "LTC/USD", "ADA/USD", "DOT/USD"]
S4_ETFS = "SPY QQQ IWM EFA EEM TLT GLD SLV USO XLE XLF XLK XLV XLI XLY XLP XLU XLB SMH IBB ARKK EWJ EWZ FXI KWEB".split()
ETF_CLUSTER = {"SPY": "us_index", "QQQ": "us_index", "IWM": "us_index", "EFA": "intl", "EEM": "em", "EWJ": "intl", "EWZ": "em",
               "FXI": "china", "KWEB": "china", "TLT": "bonds", "GLD": "metals", "SLV": "metals", "USO": "energy", "XLE": "energy",
               "XLF": "sector_cyc", "XLI": "sector_cyc", "XLB": "sector_cyc", "XLY": "sector_cyc", "XLK": "tech", "SMH": "tech",
               "ARKK": "tech", "IBB": "health", "XLV": "health", "XLP": "defensive", "XLU": "defensive"}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}   # 13:00 NY close: every intraday cut-off moves 3 h earlier
NYSE_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
                 "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def now_utc():
    o = os.environ.get("TFO_V2_NOW")
    return dt.datetime.fromisoformat(o.replace("Z", "+00:00")).astimezone(UTC) if o else dt.datetime.now(UTC)


def iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)


def config():
    c = {"law": dict(LAW), "setups": {k: dict(v) for k, v in SETUP_DEFAULTS.items()}, "universe": {}}
    try:
        u = json.load(open(CONFIG))
        for k, v in (u.get("setups") or {}).items():
            if k in c["setups"]:
                c["setups"][k].update({x: y for x, y in v.items() if x in ("enabled", "disabled_reason", "backtest", "name", "notes", "shadow")})
                if c["setups"][k].get("shadow"): c["setups"][k]["enabled"] = False   # shadow can never arm (Brett 28 Oct 19:39 WITA)
        law = u.get("law") or {}
        for k, v in law.items():               # config may only TIGHTEN the law
            if k not in c["law"] or not isinstance(v, (int, float)): continue
            d = c["law"][k]
            tighter = v <= d if k not in ("day_freeze_pct", "dd_warn_pct", "dd_freeze_new_pct", "dd_halt_pct", "mega_dv10_usd", "gap_mult_stock") else v >= d
            if tighter: c["law"][k] = float(v)
        c["universe"] = u.get("universe") or {}
    except FileNotFoundError:
        pass
    return c


def atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def load_json(path, default=None):
    try: return json.load(open(path))
    except FileNotFoundError: return default


def load_book():
    b = load_json(BOOK)
    if b is None: raise SystemExit("REFUSED: V2 book not funded yet (run: tfo_v2.py fund --match-v1 --execute)")
    return b


def append_line(path, line):
    assert path not in V1_FILES, "V2 must never write V1 files"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f: f.write(line + "\n")


# ----------------------------------------------------------------------------- status / heartbeat (fail-safe monitor)
def status_update(runner, ok, errors=None, coverage=None, **extra):
    """Heartbeat. ok=False is real breakage (red on the dashboard). Data coverage (Head Trader 2026-09-26) is kept per
    setup in s["data_coverage"] until that setup fetches again; each runner gets status ok | degraded | error, where
    'degraded' (signals only) means partial price downloads after retries: ok stays True, so it is never an error.
    runner=None merges coverage only (the `coverage` probe) and leaves every heartbeat time untouched."""
    with desk_lock.ledger_lock("tfo_v2_status", timeout=60):
        s = load_json(STATUS, {}) or {}
        s.setdefault("runners", {})
        t = iso(now_utc())
        if coverage: s.setdefault("data_coverage", {}).update(coverage)
        cov = s.get("data_coverage") or {}
        degraded = sorted(k for k, v in cov.items() if isinstance(v, dict) and v.get("status") == "degraded")
        if runner:
            s["runners"][runner] = {"last_run_utc": t, "ok": bool(ok), "errors": list(errors or [])[:20], **extra}
            if ok: s["runners"][runner]["last_ok_utc"] = t
            else: s["runners"][runner]["last_ok_utc"] = (s.get("runners", {}).get(runner) or {}).get("last_ok_utc")
            s["heartbeat_utc"] = t
        for k, v in s["runners"].items():
            v["status"] = "error" if not v.get("ok") else ("degraded" if k == "signals" and degraded else "ok")
        if "signals" in s["runners"]: s["runners"]["signals"]["data_coverage"] = cov
        s["fund"] = KEY; s["paper_only"] = True
        errs = {k: v["errors"] for k, v in s["runners"].items() if not v.get("ok")}
        s["data_errors_active"] = bool(errs); s["runner_errors"] = errs
        s["data_degraded_active"] = bool(degraded); s["data_degraded_setups"] = degraded
        s["coverage_threshold"] = {"degraded_if_failed_share_gt": COVERAGE_FAIL_SHARE, "or_failed_count_gte": COVERAGE_FAIL_COUNT,
                                   "retry_scheme": RETRY_SCHEME}
        atomic(STATUS, s)
    return s


# ----------------------------------------------------------------------------- data coverage: fetch retry + coverage heartbeat (Head Trader 2026-09-26)
# Fri 2026-09-25: S3 lost 166/512 daily downloads and S2 87/264 intraday (yfinance tz-cache "unable to open database
# file" under threads) while the status said ok. Failed tickers are now RETRIED and the true final failures recorded.
# Retries only recover data that failed; no signal / entry / sizing / exit / rail / eligibility logic reads any of this.
COVERAGE_FAIL_SHARE = 0.05       # degraded when final failures are > 5% of the setup's universe ...
COVERAGE_FAIL_COUNT = 25         # ... OR >= 25 tickers (whichever trips first)
RETRY_PASSES = ((50, 2.0, True), (20, 5.0, False))   # (batch size, sleep s before the pass, yfinance threads)
RETRY_SCHEME = "bulk fetch, then retry failed tickers: pass 1 batches of 50 after 2 s (threads on), pass 2 batches of 20 after 5 s (threads off); Kraken pairs: 2 retries after 2 s / 5 s"
_FETCH_LOG = []                  # this process's fetch records; cmd_signals folds them per setup


def _fake_fail():
    """Test hook (never set on the Mac): TFO_V2_FAKE_FETCH_FAIL=comma list of tickers/pairs whose downloads always fail."""
    return {x.strip() for x in os.environ.get("TFO_V2_FAKE_FETCH_FAIL", "").split(",") if x.strip()}


def _yf_frame(df, s):
    """One ticker's OHLCV frame from a yf.download result (MultiIndex by ticker, or flat for a lone ticker)."""
    if df is None: return None
    cols = df.columns
    if getattr(cols, "nlevels", 1) > 1:
        if s in cols.get_level_values(0): return df[s]
        if s in cols.get_level_values(-1): return df.xs(s, axis=1, level=-1)
        return None
    return df


def _yf_has(df, s):
    try:
        x = _yf_frame(df, s)
        return x is not None and "Close" in x.columns and bool(x["Close"].notna().any())
    except Exception:
        return False


def yf_download_retry(syms, label="", **kw):
    """yf.download(syms, **kw) as before, then retry the tickers that came back empty (RETRY_PASSES) and splice the
    recovered columns in. Same kwargs and result shape as the single bulk call; records coverage in _FETCH_LOG."""
    import yfinance as yf
    syms = list(syms); fake = _fake_fail()
    df = yf.download(syms, **kw)
    bad = [x for x in syms if x in fake or not _yf_has(df, x)]
    first, passes, got, lone = list(bad), 0, {}, None
    for size, pause, threads in RETRY_PASSES:
        if not bad: break
        passes += 1; time.sleep(pause); still = []
        for i in range(0, len(bad), size):
            chunk = bad[i:i + size]
            try: part = yf.download(chunk, **{**kw, "threads": threads})
            except Exception: part = None
            for x in chunk:
                if x not in fake and _yf_has(part, x):
                    got[x] = _yf_frame(part, x)
                    if len(syms) == 1: lone = part
                else: still.append(x)
        bad = still
    if got:
        if len(syms) == 1: df = lone                     # lone ticker: exactly what a clean single call returns
        else:
            import pandas as pd
            multi = getattr(df.columns, "nlevels", 1) > 1
            keep = df.drop(columns=[x for x in got if x in df.columns.get_level_values(0)], level=0) if multi else df.iloc[:, :0]
            df = pd.concat([keep, pd.concat(got, axis=1)], axis=1)
    gone = [x for x in bad if x in fake and _yf_has(df, x)]
    if gone:                                             # test hook only: a faked failure looks exactly like a real one (all-NaN)
        df = df.copy()
        cols = [c for c in df.columns if (c[0] if isinstance(c, tuple) else None) in gone] if getattr(df.columns, "nlevels", 1) > 1 else list(df.columns)
        df.loc[:, cols] = float("nan")
    _FETCH_LOG.append({"fetch": label or "yfinance %s %s" % (kw.get("interval", ""), kw.get("period", "")), "syms": syms,
                       "first_failed": first, "failed": bad, "retry_passes": passes})
    return df


def coverage_summary(setup, fetches, source="signals"):
    """Pure: fold one setup's fetch records into (heartbeat entry, set of finally-failed tickers). Entry status is
    'degraded' when final failures > COVERAGE_FAIL_SHARE of the universe or >= COVERAGE_FAIL_COUNT, else 'ok'."""
    uni, first, failed, passes, parts = [], set(), set(), 0, []
    for f in fetches:
        if "sym" in f:                                   # per-pair Kraken record
            uni.append(f["sym"]); passes = max(passes, f["attempts"] - 1)
            if not f["ok"]: failed.add(f["sym"])
            if not f["ok"] or f["attempts"] > 1: first.add(f["sym"])
        else:
            uni += list(f["syms"]); first |= set(f["first_failed"]); failed |= set(f["failed"]); passes = max(passes, f["retry_passes"])
            parts.append({"fetch": f["fetch"], "universe": len(f["syms"]), "failed_first_pass": len(f["first_failed"]),
                          "by_source": {v: list((f.get("sources") or {}).values()).count(v) for v in set((f.get("sources") or {}).values())},
                          "failed": len(f["failed"]), "retry_passes": f["retry_passes"], "failed_sample": sorted(f["failed"])[:10]})
    n = len(set(uni))
    if not n: return None, set()
    by_src = {}
    for f in fetches:
        if "sym" in f:
            if f["ok"]: by_src["kraken"] = by_src.get("kraken", 0) + 1
        for v in (f.get("sources") or {}).values(): by_src[v] = by_src.get(v, 0) + 1
    share = len(failed) / n
    deg = share > COVERAGE_FAIL_SHARE or len(failed) >= COVERAGE_FAIL_COUNT
    return {"setup": setup, "status": "degraded" if deg else "ok", "utc": iso(now_utc()), "source": source, "universe": n,
            "failed_first_pass": len(first), "failed": len(failed), "failed_share": round(share, 4),
            "recovered_by_retry": len(first - failed), "retry_attempts": passes, "failed_sample": sorted(failed)[:10],
            "fetches": parts, "by_source": by_src}, failed


def _fetch_fail_msg(e, failed):
    """True when a setup error line names a ticker whose DOWNLOAD failed (reported as coverage, not as an error)."""
    head = str(e).split(":", 1)[0].split()
    return bool(head) and head[-1] in failed


# ----------------------------------------------------------------------------- quotes: Kraken (copied from V1 helpers; V2 never imports tfo_crypto)
def _mock(kind):
    p = os.environ.get("TFO_V2_MOCK_QUOTES" if kind == "q" else "TFO_V2_MOCK_BARS")
    return json.load(open(p)) if p else None


def kget(path, **params):
    url = "https://api.kraken.com/0/public/%s?%s" % (path, urllib.parse.urlencode(params))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
        d = json.loads(r.read().decode())
    if d.get("error"): raise RuntimeError("%s %s" % (path, d["error"]))
    return d["result"]


def kraken_quote(pair):
    m = _mock("q")
    if m is not None:
        q = dict(m[pair]); q.setdefault("pair", pair)
        q["mid"] = (q["bid"] + q["ask"]) / 2; q["spread_bp"] = (q["ask"] - q["bid"]) / q["mid"] * 1e4
        q["tape"] = "LIVE" if q.get("trade_age_sec", 999) < LAW["live_age_sec"] else "STALE"
        q.setdefault("utc", iso(now_utc())); q.setdefault("dollar_vol24", 1e9); q["source"] = "MOCK"
        return q
    t = list(kget("Ticker", pair=pair.replace("/", "")).values())[0]
    tr = kget("Trades", pair=pair.replace("/", ""), count=1); rows = [v for k, v in tr.items() if k != "last"][0]
    last_epoch = max(float(r[2]) for r in rows) if rows else 0
    bid, ask, last = float(t["b"][0]), float(t["a"][0]), float(t["c"][0])
    age = time.time() - last_epoch
    q = {"pair": pair, "bid": bid, "ask": ask, "last": last, "mid": (bid + ask) / 2, "vol24": float(t["v"][1]),
         "dollar_vol24": float(t["v"][1]) * float(t["p"][1]), "trade_age_sec": round(age, 1),
         "spread_bp": (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > bid else None,
         "utc": iso(now_utc()), "source": "Kraken public Ticker+Trades"}
    q["tape"] = "LIVE" if age < LAW["live_age_sec"] else "STALE"
    if not all(math.isfinite(x) and x > 0 for x in (bid, ask, last)): q["tape"] = "BAD"
    return q


def kraken_daily(pair):
    """Completed UTC daily bars (the in-progress bar is dropped)."""
    m = _mock("b")
    if m is not None: return m[pair]
    n, err = 0, None
    for pause in (0,) + tuple(x[1] for x in RETRY_PASSES):          # failed fetch -> retry (coverage fix 2026-09-26)
        if pause: time.sleep(pause)
        n += 1
        try:
            if pair in _fake_fail(): raise RuntimeError("TFO_V2_FAKE_FETCH_FAIL test hook")
            rows = list(kget("OHLC", pair=pair.replace("/", ""), interval=1440).values())[0]
            err = None; break
        except Exception as e:
            err = e
    _FETCH_LOG.append({"fetch": "kraken OHLC 1d", "sym": pair, "ok": err is None, "attempts": n})
    if err is not None: raise err
    rows = [r for r in rows if isinstance(r, list)]
    today = now_utc().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[6]), "vwap": float(r[5])}
            for r in rows if int(r[0]) < today]


# ----------------------------------------------------------------------------- quotes: US (same eligibility logic as V1 / paper_execution_price)
def early_shift(n_ny):
    """Minutes to ADD to NY clock time so that early-close days (13:00 close) reuse the normal 16:00 cut-offs."""
    return 180 if n_ny.strftime("%Y-%m-%d") in NYSE_EARLY_CLOSE else 0


def ny_close_hm(t):
    """NY clock as HH:MM shifted so that 15:45 / 15:30 cut-offs mean 12:45 / 12:30 on early-close days."""
    n = t.astimezone(NY); m = n.hour * 60 + n.minute + early_shift(n)
    return "%02d:%02d" % (m // 60, m % 60)


def us_session_ok(t=None, setup=None):
    """Regular session minus auction windows: no fills 09:30-09:35 (open cross) or 15:50-16:00 (close imbalance)."""
    n = (t or now_utc()).astimezone(NY)
    if n.weekday() >= 5 or n.strftime("%Y-%m-%d") in NYSE_HOLIDAYS: return False, "US market closed (weekend/holiday)"
    hm = n.hour * 60 + n.minute + early_shift(n)
    if hm < 9 * 60 + 35 + early_shift(n): return False, "before 09:35 NY (pre-market / opening auction)"
    if hm >= 15 * 60 + 50: return False, "after the closing auction cut-off (15:50 NY; 12:50 on early-close days)"
    return True, None


# ----------------------------------------------------------------------------- paid feeds (2026-10-30 paidfeeds)
# Brett hard rule 2026-10-30: paid feeds FIRST, Yahoo only as fallback. US quotes: Massive (ex-Polygon) snapshot
# (real-time NBBO + last trade) -> desk native quote layer (Yahoo). US daily bars: Massive aggs -> EODHD eod -> FMP
# /stable/historical-price-eod/full -> Yahoo (yf_download_retry, unchanged retries). Intraday 5m: Massive -> Yahoo.
# S3 earnings: FMP /stable/earnings EPS (+ EODHD calendar BMO/AMC timing) -> Yahoo. Kraken unchanged for crypto.
# Keys: env MASSIVE_API_KEY / EODHD_API_KEY (or TFO_EODHD_KEY) / FMP_API_KEY, else ~/.tfo_secrets/{massive,eodhd,fmp}.env.
# Keys are never printed or logged; Massive key goes in the Authorization header only. TFO_V2_PAID_OFF=1 -> Yahoo-only (old path).
# Every served ticker's source is recorded in _SOURCES and folded into coverage ("by_source").
SECRETS_DIR = os.path.expanduser("~/.tfo_secrets")
_SOURCES = {}                    # "<fetch>:<sym>" -> source that served it (this process)
_PAID_BRK = {}                   # provider -> epoch until which it is skipped (401/403/429 breaker, 10 min)
PAID_WORKERS = int(os.environ.get("TFO_V2_PAID_WORKERS", "12"))


def _paid_key(name, fname, alt=()):
    for n in (name,) + tuple(alt):
        if os.environ.get(n): return os.environ[n].strip()
    try:
        for ln in open(os.path.join(SECRETS_DIR, fname)):
            if ln.strip().startswith(name + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None


def _paid_on(): return os.environ.get("TFO_V2_PAID_OFF") != "1" and _mock("q") is None and _mock("b") is None


def _paid_get(prov, url, headers=None, timeout=15, tries=2):
    """JSON GET with one retry (2 s). 401/403/429 trip a 10-min breaker for the provider. Never logs the URL (keys)."""
    if _PAID_BRK.get(prov, 0) > time.time(): return None
    for i in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)
            return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429): _PAID_BRK[prov] = time.time() + 600; return None
            if e.code == 404: return None
        except Exception:
            pass
        if i + 1 < tries: time.sleep(2.0)
    return None


def _massive(path, **q):
    k = _paid_key("MASSIVE_API_KEY", "massive.env")
    if not k: return None
    return _paid_get("massive", "https://api.massive.com%s?%s" % (path, urllib.parse.urlencode(q)), {"Authorization": "Bearer " + k})


def _eodhd(path, **q):
    k = _paid_key("EODHD_API_KEY", "eodhd.env", ("TFO_EODHD_KEY",))
    if not k: return None
    return _paid_get("eodhd", "https://eodhd.com/api/%s?%s" % (path, urllib.parse.urlencode({**q, "api_token": k, "fmt": "json"})))


def _fmp(path, **q):
    k = _paid_key("FMP_API_KEY", "fmp.env")
    if not k: return None
    return _paid_get("fmp", "https://financialmodelingprep.com/stable/%s?%s" % (path, urllib.parse.urlencode({**q, "apikey": k})))


def _period_start(period, today):
    n, u = int(re.match(r"(\d+)", period).group(1)), re.sub(r"\d+", "", period)
    return today - dt.timedelta(days={"d": 1, "wk": 7, "mo": 31, "y": 366}[u] * n)


def _ms_sym(s): return s.replace("-", ".")


def _bars_massive(s, start, today, adj_ac=False):
    js = _massive("/v2/aggs/ticker/%s/range/1/day/%s/%s" % (urllib.parse.quote(_ms_sym(s)), start, today),
                  adjusted="true", sort="asc", limit=50000)
    rows = (js or {}).get("results") or []
    out = [{"t": dt.datetime.fromtimestamp(r["t"] / 1000, UTC).astimezone(NY).date().isoformat(), "o": float(r["o"]), "h": float(r["h"]),
            "l": float(r["l"]), "c": float(r["c"]), "ac": float(r["c"]), "v": float(r["v"])} for r in rows if all(k in r for k in "ohlcvt")]
    if out and adj_ac:           # Massive adjusts splits only; Yahoo "Adj Close" also adjusts dividends -> take EODHD adjusted_close
        ac = {r.get("date"): r.get("adjusted_close") for r in (_eodhd("eod/%s.US" % s, **{"from": str(start), "to": str(today)}) or []) if isinstance(r, dict)}
        hit = 0
        for b in out:
            if ac.get(b["t"]) is not None: b["ac"] = float(ac[b["t"]]); hit += 1
        if hit < len(out) * 0.9: return None        # cannot honour dividend-adjusted closes -> next source
    return out or None


def _bars_eodhd(s, start, today, adj_ac=False):
    js = _eodhd("eod/%s.US" % s, **{"from": str(start), "to": str(today)})
    if not isinstance(js, list): return None
    out = []
    for r in js:
        try:   # EODHD OHLC as served (fallback only; adjusted_close carries split+dividend adjustment for S4)
            c, acl = float(r["close"]), float(r["adjusted_close"])
            out.append({"t": r["date"], "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": c, "ac": acl, "v": float(r["volume"])})
        except Exception: pass
    return out or None


def _bars_fmp(s, start, today, adj_ac=False):
    if adj_ac: return None       # FMP /stable/ full EOD carries no dividend-adjusted close in this shape -> Yahoo
    js = _fmp("historical-price-eod/full", symbol=s, **{"from": str(start), "to": str(today)})
    if not isinstance(js, list): return None
    out = [{"t": r["date"], "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]), "ac": float(r["close"]),
            "v": float(r["volume"])} for r in js if all(r.get(k) is not None for k in ("date", "open", "high", "low", "close", "volume"))]
    return sorted(out, key=lambda b: b["t"]) or None


def _bars_broken(b):
    """A close-to-open jump > 2.5x either way between consecutive sessions = an unadjusted split/spin or a reassigned ticker
    (seen 2026-10-30: Massive CTVA pre-2026-07-30 bars at ~$90 vs ~$13.5 after, ATR14 2.6x the Yahoo value)."""
    return any(not (0.4 < y["o"] / x["c"] < 2.5) for x, y in zip(b[:-1], b[1:]) if x["c"] > 0 and y["o"] > 0)


def paid_daily(syms, period, adj_ac=False, label=""):
    """Paid chain per ticker: Massive -> EODHD -> FMP. Returns ({sym: bars incl. today's partial}, {sym: source})."""
    from concurrent.futures import ThreadPoolExecutor
    today = now_utc().astimezone(NY).date(); start = _period_start(period, today); fake = _fake_fail()
    stale_cut = (today - dt.timedelta(days=6)).isoformat()   # last bar must be within ~4 sessions (covers weekends + a holiday)
    def one(s):
        if s in fake: return s, None, None
        for name, fn in (("massive", _bars_massive), ("eodhd", _bars_eodhd), ("fmp", _bars_fmp)):
            try: b = fn(s, start, today, adj_ac)
            except Exception: b = None
            if b and len(b) >= 2 and b[-1]["t"] >= stale_cut and not _bars_broken(b): return s, b, name   # stale / unadjusted corp-action -> next source
        return s, None, None
    got, src = {}, {}
    with ThreadPoolExecutor(max_workers=PAID_WORKERS) as ex:
        for s, b, name in ex.map(one, list(syms)):
            if b: got[s], src[s] = b, name
    return got, src


def paid_intraday_5m(syms, days=15):
    """Massive 5m regular-session bars as a yfinance-shaped frame (MultiIndex ticker x OHLCV, tz-aware index)."""
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor
    t = now_utc(); start = (t - dt.timedelta(days=days + 7)).date(); fake = _fake_fail()
    def one(s):
        if s in fake: return s, None
        js = _massive("/v2/aggs/ticker/%s/range/5/minute/%s/%s" % (urllib.parse.quote(_ms_sym(s)), start, t.date()), adjusted="true", sort="asc", limit=50000)
        rows = (js or {}).get("results") or []
        if not rows: return s, None
        f = pd.DataFrame({"Open": [r["o"] for r in rows], "High": [r["h"] for r in rows], "Low": [r["l"] for r in rows],
                          "Close": [r["c"] for r in rows], "Volume": [r["v"] for r in rows]},
                         index=pd.to_datetime([r["t"] for r in rows], unit="ms", utc=True).tz_convert(NY))
        f = f.between_time("09:30", "15:55")      # prepost=False equivalent
        keep = sorted(set(f.index.date))[-days:]
        return s, f[[d in keep for d in f.index.date]]
    got = {}
    with ThreadPoolExecutor(max_workers=PAID_WORKERS) as ex:
        for s, f in ex.map(one, list(syms)):
            if f is not None and len(f): got[s] = f
    return got


def us_intraday_5m(syms):
    """S2 intraday: Massive first, Yahoo (yf_download_retry, retries unchanged) for the rest. One _FETCH_LOG record."""
    import pandas as pd
    syms = list(syms); got = paid_intraday_5m(syms) if _paid_on() else {}
    rest = [s for s in syms if s not in got]; yfail = list(rest); passes = 0; first = list(rest)
    if rest:
        n0 = len(_FETCH_LOG)
        df = yf_download_retry(rest, label="intraday 5m 15d", period="15d", interval="5m", group_by="ticker", progress=False, prepost=False, threads=True)
        rec = _FETCH_LOG.pop(n0) if len(_FETCH_LOG) > n0 else {"failed": rest, "retry_passes": 0}
        yfail, passes = rec["failed"], rec["retry_passes"]
        for s in rest:
            if s not in yfail and _yf_has(df, s):
                f = _yf_frame(df, s).copy(); f.index = f.index.tz_convert(NY); got[s] = f
    for s in syms:
        if s in got: _SOURCES["intraday:" + s] = "massive" if s not in rest else "yahoo"
    _FETCH_LOG.append({"fetch": "intraday 5m 15d (massive>yahoo)", "syms": syms, "first_failed": first, "failed": [s for s in syms if s not in got],
                       "retry_passes": passes, "sources": {s: _SOURCES["intraday:" + s] for s in syms if s in got}})
    return pd.concat(got, axis=1) if got else pd.DataFrame()


def paid_earnings(sym, since):
    """[(report_date, timing 'bmo'|'amc'|None, actual, estimate, source)] newest first.
    EPS actual/estimate from FMP /stable/earnings (adjusted-EPS basis, same as Yahoo's). EODHD calendar actuals are GAAP and
    disagree with consensus basis (tested 2026-10-30: HII/LHX/WEC/WST flipped sign), so EODHD supplies ONLY the BMO/AMC timing.
    Empty list -> caller falls back to Yahoo."""
    rows = [r for r in (_fmp("earnings", symbol=sym, limit=8) or []) if isinstance(r, dict) and r.get("epsActual") is not None
            and r.get("epsEstimated") is not None and r.get("date", "") >= str(since)]
    if not rows: return []
    timing = {}
    js = _eodhd("calendar/earnings", symbols="%s.US" % sym, **{"from": str(since), "to": str(now_utc().date())})
    for r in ((js or {}).get("earnings") or []) if isinstance(js, dict) else []:
        ba = str(r.get("before_after_market") or "").lower()
        timing[r.get("report_date")] = "bmo" if ba.startswith("before") else "amc" if ba.startswith("after") else None
    return sorted(((r["date"], timing.get(r["date"]), float(r["epsActual"]), float(r["epsEstimated"]),
                    "fmp" + ("+eodhd timing" if timing.get(r["date"]) else "")) for r in rows), reverse=True)


def _us_regular_now(t):
    n = t.astimezone(NY); d = n.date().isoformat()
    if n.weekday() >= 5 or d in NYSE_HOLIDAYS: return "closed"
    hm = n.hour * 60 + n.minute; close = 13 * 60 if d in NYSE_EARLY_CLOSE else 16 * 60
    return "regular" if 9 * 60 + 30 <= hm < close else "pre" if 4 * 60 <= hm < 9 * 60 + 30 else "post" if close <= hm < 20 * 60 else "closed"


def massive_quote(sym):
    """Massive snapshot -> quote dict in the native-layer shape. LIVE eligibility (executable/quote_current) only when the
    last trade is < live_age_sec old AND the US regular session is open by the NYSE clock; otherwise it is a DELAYED print."""
    js = _massive("/v2/snapshot/locale/us/markets/stocks/tickers/%s" % urllib.parse.quote(_ms_sym(sym)))
    tk = (js or {}).get("ticker") or {}
    lt, lq = tk.get("lastTrade") or {}, tk.get("lastQuote") or {}
    if not lt.get("p") or not lt.get("t"): return None
    t = now_utc(); ts = dt.datetime.fromtimestamp(lt["t"] / 1e9, UTC); age = (t - ts).total_seconds(); sess = _us_regular_now(t)
    cur = age < LAW["live_age_sec"] and sess == "regular"
    q = {"sym": sym, "px_usd": float(lt["p"]), "px": float(lt["p"]), "last": float(lt["p"]), "ccy": "USD", "session": sess,
         "quote_utc": iso(ts), "fetched_at": iso(t), "age_sec": age, "quote_current": cur, "executable": cur,
         "bid": float(lq.get("p") or 0), "ask": float(lq.get("P") or 0), "day_vol": float((tk.get("day") or {}).get("v") or 0),
         "source": "massive", "paper_price_class": "CURRENT PAPER" if cur else "DELAYED/CLOSED PAPER (Massive)"}
    b, _ = paid_daily([sym], "1mo")
    hist = [x for x in (b.get(sym) or []) if x["t"] < t.astimezone(NY).date().isoformat()][-10:]
    q["avg10_vol"] = sum(x["v"] for x in hist) / len(hist) if hist else 0.0
    return q


def us_quote(sym):
    m = _mock("q")
    if m is not None:
        q = dict(m[sym]); q.setdefault("sym", sym); q.setdefault("px_usd", q.get("last")); q.setdefault("px", q.get("last"))
        q.setdefault("session", "regular"); q.setdefault("quote_current", True); q.setdefault("executable", True)
        q.setdefault("quote_utc", iso(now_utc())); q.setdefault("ccy", "USD"); q["source"] = "MOCK"
    elif _paid_on() and (_mq := massive_quote(sym)) is not None:
        q = _mq; _SOURCES["quote:" + sym] = "massive"
    else:
        import fundamental_book as fb      # FALLBACK: the desk's native quote layer (tfo_equity_refresh, Yahoo) — same as V1
        qs, fails = fb.fetch_prices([sym], require_executable=False)
        q = qs.get(sym)
        if not q: raise RuntimeError("no quote %s: %s" % (sym, fails.get(sym)))
        q = dict(q)
        try:
            import yfinance as yf
            t = yf.Ticker(sym); inf = t.info; fi = t.fast_info
            q["bid"], q["ask"] = float(inf.get("bid") or 0), float(inf.get("ask") or 0)
            q["avg10_vol"] = float(fi["tenDayAverageVolume"] or 0); q["day_vol"] = float(fi["lastVolume"] or 0)
        except Exception as e:
            q["bidask_error"] = str(e)
        q["source"] = "yahoo (native layer; Massive unavailable)"; _SOURCES["quote:" + sym] = "yahoo"
    px = float(q.get("px_usd") or 0)
    ok = q.get("session") == "regular" and eligible(q) and q.get("quote_current") and (q.get("age_sec") if q.get("age_sec") is not None else 999) < LAW["live_age_sec"]
    q["tape"] = "LIVE" if ok else "DELAYED"
    bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
    if not (bid > 0 and ask >= bid and px > 0 and abs(ask / px - 1) < 0.01 and abs(bid / px - 1) < 0.01):
        q["bidask_ok"] = False       # buys refuse; sells use last print less 5bp (documented model)
        q["bid_model"] = px * (1 - 0.0005) if px > 0 else 0.0
    else:
        q["bidask_ok"] = True
        q["spread_bp"] = (ask - bid) / ((ask + bid) / 2) * 1e4
    q["mid"] = (bid + ask) / 2 if q["bidask_ok"] else px
    return q


def quote(sym):
    return kraken_quote(sym) if "/" in sym else us_quote(sym)


def sell_px(q):
    return q["bid"] if "/" in q.get("pair", q.get("sym", "")) or q.get("bidask_ok") else q.get("bid_model", 0.0)


def us_daily(syms, period="1y", adj_ac=False):
    m = _mock("b")
    if m is not None:
        fake = _fake_fail(); out = {s: m[s] for s in syms if s in m and s not in fake}
        miss = [s for s in syms if s not in out]
        _FETCH_LOG.append({"fetch": "MOCK daily %s" % period, "syms": list(syms), "first_failed": miss, "failed": miss, "retry_passes": 0})
        return out
    today = now_utc().astimezone(NY).date()
    paid, psrc = paid_daily(syms, period, adj_ac) if _paid_on() else ({}, {})
    out = {s: [b for b in paid[s] if b["t"] < today.isoformat()] for s in paid}     # completed sessions only
    rest = [s for s in syms if s not in out]
    first, failed, passes = list(rest), [], 0
    if rest:
        n0 = len(_FETCH_LOG)
        yo = _us_daily_yahoo(rest, period)
        rec = _FETCH_LOG.pop(n0) if len(_FETCH_LOG) > n0 else {"failed": rest, "retry_passes": 0}
        passes = rec["retry_passes"]
        for s, b in yo.items(): out[s] = b; psrc[s] = "yahoo"
    failed = [s for s in syms if not out.get(s)]
    for s in list(out):
        if not out[s]: out.pop(s)
    for s, v in psrc.items(): _SOURCES["daily:" + s] = v
    _FETCH_LOG.append({"fetch": "daily %s (massive>eodhd>fmp>yahoo)" % period, "syms": list(syms), "first_failed": first, "failed": failed,
                       "retry_passes": passes, "sources": {s: psrc[s] for s in out if s in psrc}})
    return out


def _us_daily_yahoo(syms, period="1y"):
    """FALLBACK: the pre-2026-10-30 Yahoo daily path, unchanged (retry passes + MultiIndex lone-ticker fix)."""
    df = yf_download_retry(list(syms), label="daily %s" % period, period=period, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    out = {}
    today = now_utc().astimezone(NY).date()
    for s in syms:
        try:
            # lone ticker: yfinance >= 0.2.5x returns (Ticker, Price) MultiIndex columns even for one symbol, so the bare
            # frame's rows had no "Open" key and every single-name fetch came back EMPTY (mark never refreshed ATR/highs).
            # _yf_frame selects the ticker's frame for both shapes; the multi-ticker branch is unchanged. (fix 2026-09-26)
            x = (df[s] if len(syms) > 1 else _yf_frame(df, s)).dropna()
            x = x[[d.date() < today for d in x.index]]      # completed sessions only
            out[s] = [{"t": str(i.date()), "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"]),
                       "ac": float(r.get("Adj Close", r["Close"])), "v": float(r["Volume"])} for i, r in x.iterrows()]
        except Exception:
            pass
    return out


def atr14(bars, n=14):
    if len(bars) < n + 1: return None
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars[:-1], bars[1:])]
    a = sum(trs[:n]) / n
    for x in trs[n:]: a = (a * (n - 1) + x) / n
    return a


# ----------------------------------------------------------------------------- book mechanics
def new_book(seed, v1_eq, v1_utc, t):
    note = "funder-authorised separate V2 fund seed, matched to V1 firm equity at %s" % v1_utc
    return {"book": KEY, "label": "TFO V2 Systematic — separate paper fund", "mode": "PAPER", "paper_only": True,
            "retail_executable": True, "separate_fund": True, "seed_usd": round(seed, 2), "start": round(seed, 2),
            "cash": round(seed, 2), "equity": round(seed, 2), "peak": round(seed, 2), "realised_usd": 0.0, "fees_paid_usd": 0.0,
            "holdings": {}, "entry_px": {}, "mark_px_usd": {}, "positions": {}, "closed": [], "tickets": [],
            "capital_events": [{"type": "external_seed", "utc": iso(t), "usd": round(seed, 2), "from": "funder (external paper seed)",
                                "to": KEY, "note": note, "paper_only": True}],
            "v1_match": {"v1_firm_equity_usd": v1_eq, "v1_asof_utc": v1_utc, "source": "reports/firm_equity.csv (latest row)",
                         "matched_at_utc": iso(t)},
            "funded_utc": iso(t), "created_utc": iso(t), "rails": "V2 law: see V2_RULES.md", "updated": iso(t)}


def restrike(b):
    pos = 0.0
    for s, p in b["positions"].items():
        px = p.get("mark_usd") or p["entry_px"]
        b["mark_px_usd"][s] = px; b["holdings"][s] = p["qty"]; b["entry_px"][s] = p["entry_px"]
        pos += p["qty"] * px
    for s in list(b["holdings"]):
        if s not in b["positions"]:
            for k in ("holdings", "mark_px_usd", "entry_px"): b[k].pop(s, None)
    b["equity"] = round(b["cash"] + pos, 2); b["peak"] = round(max(float(b.get("peak") or 0), b["equity"]), 2)
    b["updated"] = iso(now_utc())
    return b


def heat(b):
    return sum(max(0.0, p["entry_px"] - p["stop"]) * p["qty"] for p in b["positions"].values())


def log_fill(act, sym, qty, px, tape, principal, proceeds, pnl, fees, fact, tid, setup, *, desk=None):
    syd = now_utc().astimezone(SYD).strftime("%Y-%m-%d %H:%M")
    line = "LOG %s %s %s %.8g %.8g %s %s %.2f %.2f %.2f %.2f" % (syd, act, sym, qty, px, tape, KEY, principal, proceeds, pnl, fees)
    append_line(LOG, line)
    append_line(FACTS, json.dumps({"syd": syd, "fact": fact, "ticket": tid, "fees_usd": round(fees, 2), "setup": setup, "book": KEY}))
    try:
        import audit
        who = desk or ("kraken" if "KRAKEN" in "%s %s" % (tape, setup) else "v2")
        pl = {"ticket": tid, "sym": sym, "action": act, "qty": qty, "px": px, "setup": setup, "tape": tape, "paper_only": True}
        if act == "FUND":
            audit.capital(who, pl)
        elif act == "BUY":
            audit.log("paper_order", who, pl)
            audit.log("fill", who, pl)
        elif act in ("STOP", "TRAIL"):
            audit.log("stop", who, pl)
        else:
            audit.log("exit", who, pl)
    except Exception:
        pass
    return line


def bank_target(p, q_sym):
    """Bid at which selling half nets +2% vs own fill after the entry-fee share and the exit fee."""
    half = p["qty0"] / 2
    fb = float(p.get("entry_fees_usd") or 0) / p["qty0"]                  # entry fee per unit
    px = p["entry_px"] * 1.021
    for _ in range(4):
        fs = desk_costs.fill_fees(q_sym, "sell", half, px, when=now_utc())["fees_usd"] / half
        px = (p["entry_px"] + fb) * (1 + LAW["bank_net_pct"]) + fs
    return px


def sessions_since(entry_utc, venue):
    t0 = parse(entry_utc); t1 = now_utc()
    if venue == "KRAKEN": return (t1.date() - t0.date()).days
    d, n = t0.astimezone(NY).date(), 0
    end = t1.astimezone(NY).date()
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS: n += 1
    return n + 1      # the entry session counts as session 1


# ----------------------------------------------------------------------------- V2 firm rails (own equity series)
def firm_rows():
    try: return list(csv.DictReader(open(FIRM)))
    except FileNotFoundError: return []


def firm_state(b=None):
    rows = firm_rows()
    b = b or load_json(BOOK)
    eq = float(b["equity"]) if b else None
    peak = max([float(r["equity_usd"]) for r in rows] + ([float(b.get("peak") or 0)] if b else []) + ([eq] if eq else [])) if (rows or b) else None
    mid = now_utc().astimezone(SYD).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    opn = None
    for r in rows:
        if parse(r["utc"]) >= mid: opn = float(r["equity_usd"]); break
    if opn is None and rows: opn = float(rows[-1]["equity_usd"])          # last pre-midnight observation
    if opn is None and b: opn = float(b["seed_usd"])
    dd = 100 * (eq / peak - 1) if eq and peak else 0.0
    day = 100 * (eq / opn - 1) if eq and opn else 0.0
    L = LAW
    level = "OK"
    if dd <= L["dd_warn_pct"]: level = "WARN"
    if day <= L["day_freeze_pct"]: level = "DAY_FREEZE"
    if dd <= L["dd_freeze_new_pct"]: level = "DD_FREEZE"
    if dd <= L["dd_halt_pct"]: level = "HALT_NEW_ENTRIES"
    return {"equity": eq, "peak": peak, "dd_pct": round(dd, 4), "day_open": opn, "day_pnl_pct": round(day, 4),
            "day_pnl_usd": round(eq - opn, 2) if eq and opn else 0.0, "level": level,
            "new_entries_allowed": level in ("OK", "WARN")}


def append_firm_row(b):
    fs = firm_state(b)
    deployed = sum(p["qty"] * (p.get("mark_usd") or p["entry_px"]) for p in b["positions"].values())
    row = {"utc": iso(now_utc()), "equity_usd": "%.2f" % b["equity"], "peak_usd": "%.2f" % fs["peak"], "drawdown_pct": "%.4f" % fs["dd_pct"],
           "day_open_equity_usd": "%.2f" % fs["day_open"], "day_pnl_usd": "%.2f" % fs["day_pnl_usd"], "day_pnl_pct": "%.4f" % fs["day_pnl_pct"],
           "level": fs["level"], "cash_usd": "%.2f" % b["cash"], "deployed_usd": "%.2f" % deployed, "n_positions": len(b["positions"])}
    new = not os.path.exists(FIRM) or os.path.getsize(FIRM) == 0
    with open(FIRM, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIRM_HEADER)
        if new: w.writeheader()
        w.writerow(row)
    return fs


# ----------------------------------------------------------------------------- fund
def v1_latest():
    rows = list(csv.DictReader(open(V1_FIRM)))
    for r in reversed(rows):
        try:
            if r.get("level") == "UNMEASURED" or (r.get("books_readable") and r.get("books_total") and r["books_readable"] != r["books_total"]):
                continue
            return float(r["firm_equity_usd"]), r["utc"]
        except (KeyError, ValueError):
            continue
    raise RuntimeError("no complete V1 firm equity observation in reports/firm_equity.csv")


def cmd_fund(amount, match_v1, execute):
    os.makedirs(V2, exist_ok=True)
    with desk_lock.ledger_lock(KEY, timeout=120):
        b = load_json(BOOK)
        if b is not None and (b.get("funded_utc") or float(b.get("seed_usd") or 0) > 0):
            print("REFUSED: V2 already funded at %s with $%s (idempotent; nothing written)" % (b.get("funded_utc"), "{:,.2f}".format(b["seed_usd"])))
            return 1
        if match_v1:
            v1_eq, v1_utc = v1_latest(); seed = v1_eq
        elif amount:
            seed, v1_eq, v1_utc = float(amount), None, None
            try: v1_eq, v1_utc = v1_latest()
            except Exception: pass
        else:
            print("REFUSED: pass --match-v1 (funder spec) or an explicit amount"); return 2
        t = now_utc()
        nb = new_book(seed, v1_eq, v1_utc, t)
        print(json.dumps({"seed_usd": nb["seed_usd"], "v1_match": nb["v1_match"], "note": nb["capital_events"][0]["note"]}, indent=1))
        if not execute:
            print("PREVIEW (not written). Re-run with --execute."); return 0
        atomic(BOOK, nb)
        append_firm_row(nb)
        log_fill("FUND", "USD", 0, 0, "N/A", seed, 0, 0, 0.0, "V2 FUND external paper seed $%.2f: %s" % (seed, nb["capital_events"][0]["note"]), "V2-FUND", "-")
        print("FUNDED %s $%s" % (KEY, "{:,.2f}".format(seed)))
    status_update("fund", True, [], seed_usd=seed)
    return 0


# ----------------------------------------------------------------------------- signals
def staged():
    s = load_json(STAGED, None) or {}
    s.setdefault("orders", []); s.setdefault("exit_flags", {}); s.setdefault("runs", {}); s.setdefault("data_errors", {})
    return s


def _sma(xs, n): return sum(xs[-n:]) / n if len(xs) >= n else None


def sig_s1(cfg):
    out, errs, exits, atrs = [], [], {}, {}
    for pair in cfg["universe"].get("S1") or S1_PAIRS:
        try:
            bars = kraken_daily(pair)
            if len(bars) < 60: errs.append("%s: only %d daily bars" % (pair, len(bars))); continue
            c = [x["c"] for x in bars]; last = bars[-1]
            hh20 = max(x["h"] for x in bars[-21:-1]); ll10 = min(x["l"] for x in bars[-11:-1])
            sma50 = _sma(c, 50); a = atr14(bars); dv20 = sum(x["c"] * x["v"] for x in bars[-20:]) / 20
            atrs[pair] = {"atr": a, "high": last["h"], "bar_t": last["t"]}
            if last["c"] < ll10: exits[pair] = {"setup": "S1", "reason": "close %.6g < 10-day low %.6g" % (last["c"], ll10), "bar_t": last["t"]}
            if last["c"] > hh20 and last["c"] > sma50 and dv20 > 20e6 and a:
                out.append({"setup": "S1", "sym": pair, "venue": "KRAKEN", "cluster": "crypto", "atr": a, "ref_close": last["c"],
                            "score": (last["c"] / hh20 - 1) / (a / last["c"]), "signal_bar": dt.datetime.fromtimestamp(last["t"], UTC).strftime("%Y-%m-%d"),
                            "valid_until_utc": iso(dt.datetime.fromtimestamp(last["t"], UTC) + dt.timedelta(days=2)),
                            "why": "close %.6g > 20d high %.6g and > SMA50 %.6g; 20d $vol %.0fM" % (last["c"], hh20, sma50, dv20 / 1e6)})
        except Exception as e:
            errs.append("%s: %s" % (pair, e))
    return out, errs, exits, atrs


def s4_week_key(t): return "%d-W%02d" % t.astimezone(NY).isocalendar()[:2]


def sig_s4(cfg, book):
    syms = cfg["universe"].get("S4") or S4_ETFS
    bars = us_daily(syms, "1y", adj_ac=True); out, errs, exits, atrs = [], [], {}, {}
    rows = []
    for s in syms:
        x = bars.get(s)
        if not x or len(x) < 200: errs.append("%s: insufficient daily history" % s); continue
        c = [b["ac"] if "ac" in b else b["c"] for b in x]
        mom = ((c[-1] / c[-22] - 1) + (c[-1] / c[-64] - 1) + (c[-1] / c[-127] - 1)) / 3
        above = c[-1] > sum(c[-200:]) / 200
        a = atr14(x); atrs[s] = {"atr": a, "high": x[-1]["h"], "bar_t": x[-1]["t"]}
        rows.append((s, mom, above, a, x[-1]["c"]))
    if len(errs) > len(syms) // 3:
        return [], errs + ["S4: too many data errors — no signals this week"], {}, atrs
    rows.sort(key=lambda r: -r[1]); top = rows[:int(cfg["law"].get("s4_top_n", 3))]; keep = {r[0] for r in top if r[2]}
    for s, p in (book or {}).get("positions", {}).items():
        if p.get("setup") == "S4" and s not in keep:
            exits[s] = {"setup": "S4", "reason": "rotation: no longer top-%d above SMA200" % len(top), "week": s4_week_key(now_utc())}
    for s, mom, above, a, c in top:
        if above and a:
            out.append({"setup": "S4", "sym": s, "venue": "US", "cluster": ETF_CLUSTER.get(s, s), "atr": a, "ref_close": c, "score": mom,
                        "signal_bar": bars[s][-1]["t"], "window_ny": ["09:35", "11:00"], "valid_until_utc": iso(now_utc() + dt.timedelta(hours=10)),
                        "why": "top-%d" % len(top) + " blended 1/3/6m momentum %.2f%% and above SMA200" % (100 * mom)})
    return out, errs, exits, atrs


def sig_s3(cfg):
    """Pre-open: names whose LAST session was an earnings day-1 (gap >= +1% on >= 2x 20d volume) with EPS surprise >= +5%."""
    uni = cfg["universe"].get("S3") or []
    if not uni: return [], ["S3: no universe in tfo_v2_config.json"], {}, {}
    bars = us_daily(uni, "3mo"); out, errs, atrs = [], [], {}
    cands = []
    for s in uni:
        x = bars.get(s)
        if not x or len(x) < 25: continue
        d1, prev = x[-1], x[-2]
        av20 = sum(b["v"] for b in x[-21:-1]) / 20; dv20 = sum(b["c"] * b["v"] for b in x[-21:-1]) / 20
        if prev["c"] > 10 and dv20 >= 50e6 and d1["o"] / prev["c"] - 1 >= 0.01 and d1["v"] >= 2 * av20:
            cands.append((s, x, d1["o"] / prev["c"] - 1, d1["v"] / av20))
    if not bars: errs.append("S3: no daily bars returned")
    for s, x, gap, rv in cands:
        try:
            m = _mock("b")
            if m is not None and "_earnings" in m:
                e = m["_earnings"].get(s)
                sur = e and e.get("surprise_pct"); edate = e and e.get("date")
            elif _paid_on() and (pe := paid_earnings(s, dt.date.fromisoformat(x[-1]["t"]) - dt.timedelta(days=120))):
                sur, edate = None, None
                last = dt.date.fromisoformat(x[-1]["t"]); prevd = dt.date.fromisoformat(x[-2]["t"])
                for rd, timing, act, est, esrc in pe:
                    d = dt.date.fromisoformat(rd)
                    # day0 = report date (BMO) or next day (AMC). Unknown timing (FMP): accept either -> prevd <= d <= last.
                    ok = (prevd < d <= last) if timing == "bmo" else (prevd <= d < last) if timing == "amc" else (prevd <= d <= last)
                    if ok and abs(est) >= 0.05:
                        sur = 100 * (act - est) / abs(est); edate = "%s %s (%s)" % (rd, timing or "?", esrc); break
                _SOURCES["earnings:" + s] = pe[0][4]
            else:
                import yfinance as yf
                _SOURCES["earnings:" + s] = "yahoo"
                ed = yf.Ticker(s).get_earnings_dates(limit=8).dropna(subset=["Reported EPS", "EPS Estimate"])
                sur, edate = None, None
                for ts, r in ed.iterrows():
                    local = ts.tz_convert(NY); day0 = local.date() + (dt.timedelta(days=1) if local.hour >= 16 else dt.timedelta(0))
                    # day-1 must be the LAST bar: the first session on/after day0 (same rule as v2_backtest S3)
                    last = dt.date.fromisoformat(x[-1]["t"]); prevd = dt.date.fromisoformat(x[-2]["t"])
                    if prevd < day0 <= last and abs(float(r["EPS Estimate"])) >= 0.05:
                        sur = 100 * (float(r["Reported EPS"]) - float(r["EPS Estimate"])) / abs(float(r["EPS Estimate"])); edate = str(local); break
            if sur is None or sur < 5.0: continue
            a = atr14(x)
            out.append({"setup": "S3", "sym": s, "venue": "US", "cluster": (cfg["universe"].get("sectors") or {}).get(s, "?"), "atr": a,
                        "ref_close": x[-1]["c"], "score": sur, "signal_bar": x[-1]["t"], "window_ny": ["09:35", "10:30"],
                        "valid_until_utc": iso(now_utc() + dt.timedelta(hours=10)),
                        "why": "EPS surprise %+.1f%% (%s); day-1 gap %+.2f%% on %.1fx volume" % (sur, edate, 100 * gap, rv)})
        except Exception as e:
            errs.append("S3 %s earnings: %s" % (s, e))
    return out, errs, {}, atrs


def sig_s2(cfg):
    """09:46-10:15 NY: liquid gappers (>= +2%, 10d avg $vol >= $500M, px > $10) with first-15-min volume >= 2x its recent average."""
    uni = cfg["universe"].get("S2") or []
    if not uni: return [], ["S2: no universe in tfo_v2_config.json"], {}, {}
    import yfinance as yf
    import pandas as pd
    daily = us_daily(uni, "3mo"); out, errs = [], []
    df = us_intraday_5m(uni)
    today = now_utc().astimezone(NY).date()
    for s in uni:
        try:
            x = daily.get(s)
            if not x or len(x) < 15: continue
            prevc = x[-1]["c"]; dv10 = sum(b["c"] * b["v"] for b in x[-10:]) / 10
            if prevc <= 10 or dv10 < 500e6: continue
            b = df[s].dropna(); b.index = b.index.tz_convert(NY) if b.index.tz is not None else b.index
            tb = b[b.index.date == today]; orb = tb.between_time("09:30", "09:40")
            if len(orb) < 3: continue
            gap = float(tb["Open"].iloc[0]) / prevc - 1
            if gap < 0.02: continue
            hist = [float(b[b.index.date == d].between_time("09:30", "09:40")["Volume"].sum()) for d in sorted(set(b.index.date)) if d < today][-10:]
            if len(hist) < 5 or sum(hist) <= 0: continue
            rv = float(orb["Volume"].sum()) / (sum(hist) / len(hist))
            if rv < 2.0: continue
            a = atr14(x)
            out.append({"setup": "S2", "sym": s, "venue": "US", "cluster": (cfg["universe"].get("sectors") or {}).get(s, "?"), "atr": a,
                        "trigger": float(orb["High"].max()), "or_low": float(orb["Low"].min()), "ref_close": prevc, "score": rv,
                        "signal_bar": str(today), "window_ny": ["09:45", "12:00"], "valid_until_utc": iso(now_utc() + dt.timedelta(hours=3)),
                        "why": "gap %+.2f%%, first-15m rvol %.1fx, OR %.2f-%.2f" % (100 * gap, rv, float(orb["Low"].min()), float(orb["High"].max()))})
        except Exception as e:
            errs.append("S2 %s: %s" % (s, e))
    return out, errs, {}, {}


def due(setup, s, t):
    n = t.astimezone(NY); hm = n.hour * 60 + n.minute; runs = s["runs"]
    if setup == "S1":
        key = t.strftime("%Y-%m-%d")
        return (t.hour * 60 + t.minute >= 5 and runs.get("S1") != key), key
    sess = n.weekday() < 5 and n.strftime("%Y-%m-%d") not in NYSE_HOLIDAYS
    if setup in ("S3", "S4"):
        key = n.strftime("%Y-%m-%d") if setup == "S3" else s4_week_key(t)
        return (sess and 8 * 60 + 30 <= hm < 9 * 60 + 30 and runs.get(setup) != key), key
    if setup == "S2":
        key = n.strftime("%Y-%m-%d")
        return (sess and 9 * 60 + 46 <= hm < 10 * 60 + 15 and runs.get("S2") != key), key
    return False, None


def indicative_fields(o, b, cfg):
    """DISPLAY ONLY (Head Trader 2026-09-25): indicative size from the signal close via size_order, the same
    sizer v2-enter uses. v2-enter never reads indicative_*; final size is always computed at the live ask."""
    r = {"indicative_usd": None, "indicative_units": None, "indicative_label": "indicative, final size at live ask"}
    try:
        px = float(o.get("ref_close") or 0)
        if not (b and px > 0): return {**r, "indicative_note": "no book or signal close"}
        sz, why = size_order(o, b, cfg, {"ask": px})
        if not sz: return {**r, "indicative_note": why}
        return {**r, "indicative_usd": round(sz["qty"] * px, 2), "indicative_units": sz["qty"], "indicative_px": px}
    except Exception as e:
        return {**r, "indicative_note": "error: %s" % e}


def restage_needed(s, cfg, book, t, lookback_days=3):
    """Brett 28 Oct 14:30 WITA: an order that EXPIRED unfilled (window missed / gate) while its setup is enabled is
    re-evaluated by re-running that setup's signal NOW; only syms the fresh signal still produces are re-staged
    (signal still valid). Held syms, already-staged syms and disabled setups are skipped. -> {setup: {sym: old_id}}"""
    held = set((book or {}).get("positions", {}))
    live = {(o["setup"], o["sym"]) for o in s["orders"] if o.get("status") == "STAGED"}
    done = {(o["setup"], o["sym"]) for o in s["orders"] if o.get("status") == "FILLED"}
    out = {}
    for o in s["orders"]:
        if o.get("status") != "EXPIRED" or not cfg["setups"].get(o.get("setup"), {}).get("enabled"): continue
        try:
            if parse(o["valid_until_utc"]) < t - dt.timedelta(days=lookback_days): continue
        except Exception: continue
        k = (o["setup"], o["sym"])
        if o["sym"] in held or k in live or k in done or o.get("restaged_to"): continue
        out.setdefault(o["setup"], {})[o["sym"]] = o.get("id")
    return out


SHADOW_LOG = os.path.join(V2, "tfo_v2_shadow_signals.jsonl")


def shadow_log(setup, sigs, t):
    """Brett 28 Oct 2026 19:39 WITA: SHADOW-ONLY setups log their would-be signals (outside P&L); never staged/armed/booked."""
    try:
        os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
        with open(SHADOW_LOG, "a") as f:
            for o in sigs:
                f.write(json.dumps({"utc": iso(t), "setup": setup, "sym": o.get("sym"), "ref_close": o.get("ref_close"), "atr": o.get("atr"),
                                    "score": o.get("score"), "why": o.get("why"), "book": "SHADOW_ZERO_RISK"}, default=str) + "\n")
    except Exception as e:
        print("shadow log failed: %s" % e)


def cmd_signals(only=None, force=False):
    cfg = config(); t = now_utc(); errors = []; done = []; coverage = {}
    book = load_json(BOOK)
    with desk_lock.ledger_lock("tfo_v2_staged", timeout=300):
        s = staged()
        s["orders"] = [o for o in s["orders"] if o.get("status") == "STAGED" and parse(o["valid_until_utc"]) > t] + \
                      [o for o in s["orders"] if o.get("status") != "STAGED" and parse(o.get("valid_until_utc", iso(t))) > t - dt.timedelta(days=3)]
        restage = restage_needed(s, cfg, book, t)
        for setup in ("S1", "S2", "S3", "S4"):
            if only and setup != only: continue
            is_due, key = due(setup, s, t)
            if not (is_due or force or setup in restage): continue
            st = cfg["setups"][setup]
            del _FETCH_LOG[:]
            try:
                fn = {"S1": lambda: sig_s1(cfg), "S2": lambda: sig_s2(cfg), "S3": lambda: sig_s3(cfg), "S4": lambda: sig_s4(cfg, book)}[setup]
                sigs, errs, exits, atrs = fn()
            except Exception as e:
                sigs, errs, exits, atrs = [], ["%s crashed: %s" % (setup, e)], {}, {}
            cov, cov_failed = coverage_summary(setup, _FETCH_LOG)
            if cov: coverage[setup] = cov
            # exits and ATR refreshes apply even when a setup is disabled for NEW entries
            if is_due or force:   # a re-stage-only run never raises exits (S4 rotation exits stay weekly)
                for sym, x in exits.items(): s["exit_flags"][sym] = {**x, "flagged_utc": iso(t)}
            s.setdefault("atr", {}).update({k: {**v, "utc": iso(t)} for k, v in atrs.items()})
            fatal = bool(errs) and not sigs and not atrs
            s["data_errors"][setup] = {"utc": iso(t), "errors": errs[:20], "blocks_entries": fatal}
            if errs: errors += [e for e in errs if not _fetch_fail_msg(e, cov_failed)][:5]   # download failures -> coverage
            if not st.get("enabled"):
                sigs_note = "DISABLED: %s" % st.get("disabled_reason", "")
                if st.get("shadow") and (is_due or force):
                    shadow_log(setup, sigs, t); sigs_note = "SHADOW-ONLY (logged, not staged): %s" % st.get("disabled_reason", "")
                s["runs"][setup] = key; done.append("%s (%d signals, %s)" % (setup, len(sigs), sigs_note)); continue
            if not fatal:
                have = {(o["setup"], o["sym"]) for o in s["orders"] if o.get("status") == "STAGED"}
                for o in sigs:
                    if (o["setup"], o["sym"]) in have: continue
                    if setup in restage and not is_due and not force and o["sym"] not in restage[setup]: continue   # re-stage run: only expired syms
                    if o["sym"] in (book or {}).get("positions", {}): continue
                    if o["sym"] in restage.get(setup, {}): o["restaged_from"] = restage[setup][o["sym"]]
                    o.update(id="V2S-" + uuid.uuid4().hex[:10], status="STAGED", staged_utc=iso(t),
                             init_k=st["init_k"], trail_k=st["trail_k"], time_stop=st["time_stop"])
                    o.update(indicative_fields(o, book, cfg))      # display-only; v2-enter ignores indicative_*
                    s["orders"].append(o)
                    if o.get("restaged_from"):
                        for old in s["orders"]:
                            if old.get("id") == o["restaged_from"]: old["restaged_to"] = o["id"]
                s["runs"][setup] = key
            done.append("%s (%d signals%s)" % (setup, len(sigs), ", DATA ERRORS -> no entries" if fatal else ""))
        s["updated_utc"] = iso(t); s["paper_only"] = True
        atomic(STAGED, s)
    print("signals: %s" % ("; ".join(done) if done else "nothing due"))
    for o in s["orders"]:
        if o.get("status") == "STAGED": print("  STAGED %-4s %-9s score %.3g  %s" % (o["setup"], o["sym"], o.get("score") or 0, o.get("why", "")))
    for k, c in coverage.items():
        print("  coverage %s: %s  universe %d  failed %d -> %d after %d retry pass(es) (%.1f%%)%s" % (
            k, c["status"].upper(), c["universe"], c["failed_first_pass"], c["failed"], c["retry_attempts"], 100 * c["failed_share"],
            ("  e.g. " + ", ".join(c["failed_sample"])) if c["failed_sample"] else ""))
    status_update("signals", not errors, errors, coverage=coverage, ran=done)


def cmd_coverage():
    """Fetch-only coverage probe (Head Trader 2026-09-26): downloads each setup's universe through the SAME fetch+retry
    path `signals` uses and records coverage in tfo_v2_status.json. Computes NO signals, stages nothing, never opens the
    book, and does not touch any runner's heartbeat time."""
    cfg = config(); U = cfg["universe"]; cov = {}
    for setup in ("S1", "S2", "S3", "S4"):
        del _FETCH_LOG[:]; note = None
        try:
            if setup == "S1":
                for pair in U.get("S1") or S1_PAIRS:
                    try: kraken_daily(pair)
                    except Exception: pass
            elif setup == "S2" and U.get("S2"):
                us_daily(U["S2"], "3mo")
                us_intraday_5m(U["S2"])
            elif setup == "S3" and U.get("S3"):
                us_daily(U["S3"], "3mo")
            elif setup == "S4":
                us_daily(U.get("S4") or S4_ETFS, "1y", adj_ac=True)
        except Exception as e:
            note = "%s: %s" % (type(e).__name__, e)
        c, _ = coverage_summary(setup, _FETCH_LOG, source="coverage probe")
        if c:
            cov[setup] = c
            print("coverage %s: %s  universe %d  failed %d -> %d after %d retry pass(es) (%.1f%%)%s" % (
                setup, c["status"].upper(), c["universe"], c["failed_first_pass"], c["failed"], c["retry_attempts"], 100 * c["failed_share"],
                ("  e.g. " + ", ".join(c["failed_sample"])) if c["failed_sample"] else ""))
            print("    by source: %s" % json.dumps(c.get("by_source") or {}, sort_keys=True))
        else:
            print("coverage %s: no fetch%s" % (setup, (" (" + note + ")") if note else ""))
    status_update(None, True, coverage=cov)
    return 0


# ----------------------------------------------------------------------------- enter
def in_window(o, t):
    if o["venue"] == "KRAKEN": return True, None
    ok, why = us_session_ok(t)
    if not ok: return False, why
    w = o.get("window_ny")
    if w:
        hm = t.astimezone(NY).strftime("%H:%M")
        if not (w[0] <= hm < w[1]): return False, "outside %s window %s-%s NY" % (o["setup"], w[0], w[1])
    return True, None


def entry_gates(o, b, cfg, q, t):
    L = cfg["law"]; fails = []
    st = cfg["setups"][o["setup"]]
    if not st.get("enabled"): fails.append("setup DISABLED: %s" % st.get("disabled_reason", ""))
    fs = firm_state(b)
    if not fs["new_entries_allowed"]: fails.append("V2 firm rail %s (dd %.2f%%, day %.2f%%)" % (fs["level"], fs["dd_pct"], fs["day_pnl_pct"]))
    if o["sym"] in b["positions"]: fails.append("already held")
    if len(b["positions"]) >= L["max_open"]: fails.append("max %d open" % L["max_open"])
    if sum(1 for p in b["positions"].values() if p["setup"] == o["setup"] and p["cluster"] == o["cluster"]) >= L["max_per_cluster"]:
        fails.append("cluster %s/%s already has %d" % (o["setup"], o["cluster"], L["max_per_cluster"]))
    for tk in b["tickets"][-200:]:
        if tk.get("sym") == o["sym"] and (t - parse(tk["utc"])).total_seconds() < L["roundtrip_sec"]: fails.append("round-trip guard (<5 min)"); break
    if any(c.get("sym") == o["sym"] and c.get("closed_utc", "")[:10] == iso(t)[:10] for c in b["closed"][-50:]): fails.append("no same-day re-entry")
    ok, why = in_window(o, t)
    if not ok: fails.append(why)
    if q["tape"] != "LIVE": fails.append("tape %s (not LIVE)" % q["tape"])
    if o["venue"] == "US" and not q.get("bidask_ok"): fails.append("no usable bid/ask (buys need the ask)")
    sp = q.get("spread_bp")
    if sp is None or sp >= L["max_spread_bp"]: fails.append("spread %s bp >= %.0f" % (sp and round(sp, 1), L["max_spread_bp"]))
    if o["setup"] == "S2" and float(q.get("last") or q.get("px_usd") or 0) <= o["trigger"]: fails.append("S2 trigger %.2f not broken" % o["trigger"])
    return fails, fs


def size_order(o, b, cfg, q):
    L = cfg["law"]; eq = float(b["equity"]); ask = float(q["ask"]); a = float(o.get("atr") or 0)
    if not (a > 0 and ask > 0): return None, "no ATR/ask"
    stop = ask - o["init_k"] * a
    if o["setup"] == "S2": stop = max(o["or_low"], ask - 1.5 * a)
    stop = max(stop, ask * (1 - L["max_loss_pct"]))
    if stop >= ask: return None, "stop above ask"
    if o["venue"] == "KRAKEN":
        stack = sum(p["qty"] * p["entry_px"] for p in b["positions"].values() if p["venue"] == "KRAKEN")
        cap = min(L["cap_crypto"], L["crypto_stack"] - stack); dv = float(q.get("dollar_vol24") or 0)
    else:
        dv10 = float(q.get("avg10_vol") or 0) * ask
        mega = dv10 >= L["mega_dv10_usd"] and (q.get("spread_bp") or 99) <= L["mega_spread_bp"]
        cap = L["cap_us_mega"] if mega else L["cap_us"]; dv = float(q.get("day_vol") or 0) * ask or dv10
    if dv > 0: cap = min(cap, L["max_pct_day_dv"] * dv)
    gm = 1.0 if o["venue"] == "KRAKEN" else max(1.0, float(L.get("gap_mult_stock", 2.0)))
    qty = min(L["risk_pct"] * eq / (gm * (ask - stop)), cap / ask) if cap > 0 else 0.0
    room = L["heat_pct"] * eq - heat(b)
    if (ask - stop) * qty > room:
        q2 = max(0.0, room / (ask - stop))
        if q2 < 0.25 * qty: return None, "open heat %.0f leaves no room (<25%% of intended size)" % heat(b)
        qty = q2
    fee = desk_costs.fill_fees(o["sym"], "buy", qty, ask, when=now_utc())["fees_usd"]
    if qty * ask + fee > b["cash"]:
        qty = max(0.0, (b["cash"] - fee - 1) / ask)
    if qty * ask < 1000: return None, "size under $1,000 after caps"
    return {"qty": qty, "stop": stop, "ask": ask, "cap": cap, "risk_usd": (ask - stop) * qty, "gap_mult": gm, "gap_risk_usd": gm * (ask - stop) * qty}, None


def us_tech_red():
    try:
        d = json.load(open(os.path.join(BASE, "state", "theme_exposure.json")))
        return any(t.get("theme") == "us_tech" and t.get("flag") == "RED" for t in d["themes"])
    except Exception:
        return True                                         # unreadable -> treat as RED (fail closed)


def is_tech(sym, cluster=None):
    c = cluster or ETF_CLUSTER.get(sym) or (config()["universe"].get("sectors") or {}).get(sym) or ""
    return "tech" in str(c).lower() or sym in ("QQQ", "TQQQ", "SOXL")


def theme_gate(sym, usd, cluster=None):
    """Firm theme cap (theme_exposure.check_buy, enforce mode; fails closed). US$ -> A$ at desk AUDUSD.
    Plus: US tech is RED -> V2 adds nothing in tech (XLK/ARKK are not in theme_exposure.MAP, so gate by cluster too)."""
    if is_tech(sym, cluster) and us_tech_red(): return False, "theme us_tech RED -> V2 adds nothing in tech (%s)" % sym
    try:
        import theme_exposure, fundamental_true_return as _ftr
        res = theme_exposure.check_buy(sym, usd / _ftr.audusd()["rate"], ccy="USD", book="tfo_v2")
    except Exception as e:
        return False, "theme cap check failed (%s) -> blocked (fail closed)" % str(e)[:80]
    if not res[0]: return res
    try:   # tfo_usbook_cap_20261030 (Brett 19:35 WITA): main-book US beta cap (40% main-book equity); this order's own staged row excluded
        import us_book_cap
        if us_book_cap.is_us_equity(sym):
            uok, uwhy, _ = us_book_cap.check(sym, usd, "long", book="tfo_v2", exclude=[("TFO V2 (staged)", sym)])
            if not uok: return False, uwhy
    except Exception as e:
        return False, "us_book_cap check failed (%s) -> blocked (fail closed)" % str(e)[:80]
    return res


def cmd_enter(execute):
    cfg = config(); t = now_utc(); lines = []; errors = []; report = []
    st = load_json(STATUS, {}) or {}
    s0 = load_json(STAGED, None)
    if not s0 or not s0.get("orders"):
        print("enter: nothing staged"); status_update("enter", True, [], mode="execute" if execute else "preview", fills=0); return
    with desk_lock.ledger_lock("tfo_v2_staged", timeout=300), desk_lock.ledger_lock(KEY, timeout=300):
        s = staged(); b = load_book()
        for o in s["orders"]:
            if o.get("status") != "STAGED": continue
            if parse(o["valid_until_utc"]) <= t: o["status"] = "EXPIRED"; continue
            de = (s["data_errors"].get(o["setup"]) or {})
            if de.get("blocks_entries"): report.append((o, ["signal data errors — no new entries (fail-safe)"])); continue
            ok, why = in_window(o, t)
            if not ok: report.append((o, [why])); continue
            try: q = quote(o["sym"])
            except Exception as e:
                errors.append("%s quote: %s" % (o["sym"], e)); report.append((o, ["quote failed (fail-safe: no entry)"])); continue
            fails, fs = entry_gates(o, b, cfg, q, t)
            sz, why = (None, None) if fails else size_order(o, b, cfg, q)
            if why: fails.append(why)
            if sz and not fails:
                tok, twhy = theme_gate(o["sym"], sz["qty"] * sz["ask"], o.get("cluster"))
                if not tok: fails.append(twhy)
            report.append((o, fails, sz))
            if fails:
                try:
                    import audit
                    audit.log("risk_decision", "kraken" if o.get("venue") == "KRAKEN" else "v2",
                              {"sym": o.get("sym"), "setup": o.get("setup"), "reasons": [str(x)[:160] for x in fails[:8]], "paper_only": True})
                except Exception:
                    pass
            if fails or not execute: continue
            tid = "V2-" + uuid.uuid4().hex[:10]; utc = iso(t)
            qty, px = sz["qty"], sz["ask"]
            fee = desk_costs.fill_fees(o["sym"], "buy", qty, px, when=t); fees = round(fee["fees_usd"], 2)
            b["cash"] = round(b["cash"] - qty * px - fees, 2)
            b["realised_usd"] = round(float(b.get("realised_usd") or 0) - fees, 2); b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
            qrec = {k: q.get(k) for k in ("bid", "ask", "last", "px_usd", "spread_bp", "trade_age_sec", "age_sec", "utc", "quote_utc", "session", "tape", "source")}
            tk = {"ticket_id": tid, "utc": utc, "action": "BUY", "sym": o["sym"], "setup": o["setup"], "qty": qty, "px_usd": px, "tape": q["tape"],
                  "principal_usd": round(qty * px, 2), "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"],
                  "stop": sz["stop"], "risk_usd": round(sz["risk_usd"], 2), "why": o.get("why"), "staged_id": o["id"], "quote": qrec,
                  "by": "TFO V2 engine", "paper_only": True, **(receipt(q) if o["venue"] == "US" else {})}
            b["positions"][o["sym"]] = {"sym": o["sym"], "venue": o["venue"], "setup": o["setup"], "cluster": o["cluster"], "qty": qty, "qty0": qty,
                "entry_px": px, "orig_entry": px, "entry_utc": utc, "entry_fees_usd": fees, "init_stop": sz["stop"], "stop": sz["stop"],
                "atr": o["atr"], "trail_k": o["trail_k"], "time_stop": o["time_stop"], "hh": px, "banked": False,
                "mark_usd": q.get("mid") or px, "mark_utc": utc, "mark_tape": q["tape"], "last_ticket_utc": utc, "entry_ticket": tk,
                "why": o.get("why"), "exit_fills": []}
            b["positions"][o["sym"]]["bank_target_px"] = round(bank_target(b["positions"][o["sym"]], o["sym"]), 8)
            b["tickets"].append(tk); o["status"] = "FILLED"; o["ticket_id"] = tid; o["filled_utc"] = utc
            restrike(b)
            lines.append(log_fill("BUY", o["sym"], qty, px, q["tape"], qty * px, 0, 0, fees,
                                  "V2 BUY %s %s $%.0f at ask %.6g; stop %.6g (risk $%.0f); fees %.2f; %s" % (o["setup"], o["sym"], qty * px, px, sz["stop"], sz["risk_usd"], fees, o.get("why")), tid, o["setup"],
                                  desk="kraken" if o.get("venue") == "KRAKEN" else "v2"))
        if execute and lines:
            restrike(b); atomic(BOOK, b); append_firm_row(b)
        atomic(STAGED, s)
    for r in report:
        o, fails = r[0], r[1]; sz = r[2] if len(r) > 2 else None
        print("%-8s %-4s %-9s %s" % ("BLOCKED" if fails else ("READY" if not execute else "FILLED"), o["setup"], o["sym"],
                                     "; ".join(fails) if fails else "qty %.6g at ask %.6g stop %.6g risk $%.0f" % (sz["qty"], sz["ask"], sz["stop"], sz["risk_usd"])))
    print("\n".join(lines) if lines else "LOG none")
    status_update("enter", not errors, errors, mode="execute" if execute else "preview", fills=len(lines))


# ----------------------------------------------------------------------------- rails
def decide(p, q, s, t, cfg):
    px = sell_px(q)
    if not px or px <= 0: return None, "no bid"
    if px <= p["stop"]: return ("TRAIL" if p.get("banked") else "STOP"), "bid %.6g <= stop %.6g" % (px, p["stop"])
    if not p.get("banked") and px >= p["bank_target_px"]: return "HALF", "bid %.6g >= +2%% net target %.6g" % (px, p["bank_target_px"])
    fl = s["exit_flags"].get(p["sym"])
    if fl and fl.get("setup") == p["setup"] and parse(fl["flagged_utc"]) > parse(p["entry_utc"]): return "EXIT", fl["reason"]
    if p["setup"] == "S2":
        if ny_close_hm(t) >= "15:45" or t.astimezone(NY).date() > parse(p["entry_utc"]).astimezone(NY).date():
            return "TIME", "S2 flat by the close (15:45 NY)"
    ts = p.get("time_stop") or 0
    if ts:
        n = sessions_since(p["entry_utc"], p["venue"])
        late = p["venue"] == "KRAKEN" or ny_close_hm(t) >= "15:30"
        if (n > ts) or (n == ts and late): return "TIME", "time stop %d sessions (held %d)" % (ts, n)
    return None, None


def cmd_rails(execute):
    cfg = config(); t = now_utc(); lines = []; errors = []; rows = []
    if not os.path.exists(BOOK): print("rails: V2 not funded"); status_update("rails", True, [], note="not funded"); return
    with desk_lock.ledger_lock(KEY, timeout=300):
        b = load_book(); s = staged(); changed = False
        for sym, p in list(b["positions"].items()):
            if p["venue"] == "US":
                ok, w = us_session_ok(t)
                if not ok: rows.append((sym, "HOLD", "US shut: %s" % w)); continue
            try: q = quote(sym)
            except Exception as e: errors.append("%s quote: %s" % (sym, e)); rows.append((sym, "QUOTE_FAIL", str(e))); continue
            mark = q.get("mid") or q.get("px_usd")
            last_good = p.get("mark_usd") or p["entry_px"]
            if not mark or mark > 2.5 * last_good or mark < last_good / 2.5:
                errors.append("%s price sanity fail (%s vs %s)" % (sym, mark, last_good)); rows.append((sym, "SANITY", "")); continue
            if q["tape"] == "LIVE":
                p["mark_usd"], p["mark_utc"], p["mark_tape"] = mark, iso(t), "LIVE"
                p["hh"] = max(float(p.get("hh") or p["entry_px"]), float(q.get("last") or q.get("px_usd") or mark))
                a = (s.get("atr") or {}).get(sym, {}).get("atr") or p["atr"]
                if a and parse(p["entry_utc"]) < t:
                    new = p["hh"] - p["trail_k"] * float(a)
                    if new > p["stop"]: p["stop"] = round(new, 10); changed = True
                    p["atr"] = a
            act, why = decide(p, q, s, t, cfg)
            if not act: rows.append((sym, "HOLD", "stop %.6g / target %.6g" % (p["stop"], p["bank_target_px"]))); continue
            blk = None
            if q["tape"] != "LIVE": blk = "tape %s" % q["tape"]
            elif p["venue"] == "US":
                ok, w = us_session_ok(t)
                if not ok: blk = w
            lt = p.get("last_ticket_utc")
            gap = cfg["law"]["stop_roundtrip_sec"] if act in ("STOP", "TRAIL") else cfg["law"]["roundtrip_sec"]
            if not blk and lt and (t - parse(lt)).total_seconds() < gap: blk = "round-trip guard (<%ds since last ticket)" % gap
            rows.append((sym, ("BLOCKED " + act) if blk else ("READY " + act), blk or why))
            if blk or not execute: continue
            px = sell_px(q); qty0 = p["qty"]; sold = p["qty0"] / 2 if act == "HALF" else qty0
            sold = min(sold, qty0)
            fee = desk_costs.fill_fees(sym, "sell", sold, px, when=t); fees = round(fee["fees_usd"], 2)
            principal = sold * p["entry_px"]; proceeds = sold * px; pnl = proceeds - principal
            b["cash"] = round(b["cash"] + proceeds - fees, 2)
            b["realised_usd"] = round(float(b.get("realised_usd") or 0) + pnl - fees, 2)
            b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
            p["qty"] = qty0 - sold; tid = "V2-" + uuid.uuid4().hex[:10]; utc = iso(t)
            if act == "HALF": p["banked"] = True; p["banked_utc"] = utc
            p["last_ticket_utc"] = utc
            tk = {"ticket_id": tid, "utc": utc, "action": act, "sym": sym, "setup": p["setup"], "qty": sold, "px_usd": px, "tape": q["tape"],
                  "principal_usd": round(principal, 2), "proceeds_usd": round(proceeds, 2), "pnl_usd": round(pnl, 2), "fees_usd": fees,
                  "net_pnl_usd": round(pnl - fees, 2), "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"], "why": why,
                  "ret_vs_fill_pct": round(100 * (px / p["entry_px"] - 1), 3), "bid_model": not (p["venue"] == "KRAKEN" or q.get("bidask_ok")),
                  "by": "TFO V2 engine", "paper_only": True}
            p["exit_fills"].append(tk); b["tickets"].append(tk); changed = True
            if p["qty"] <= 1e-12 or act != "HALF":
                ent = float(p.get("entry_fees_usd") or 0)
                net = sum(f["net_pnl_usd"] for f in p["exit_fills"]) - ent
                b["closed"].append({"sym": sym, "setup": p["setup"], "closed_utc": utc, "reason": act, "entry_utc": p["entry_utc"],
                                    "entry_px": p["entry_px"], "qty0": p["qty0"], "net_pnl_usd": round(net, 2),
                                    "gross_pnl_usd": round(sum(f["pnl_usd"] for f in p["exit_fills"]), 2),
                                    "fees_usd": round(ent + sum(f["fees_usd"] for f in p["exit_fills"]), 2),
                                    "ret_net_pct": round(100 * net / (p["qty0"] * p["entry_px"]), 3), "position": p})
                del b["positions"][sym]
                s["exit_flags"].pop(sym, None)
            lines.append(log_fill(act, sym, sold, px, q["tape"], principal, proceeds, pnl, fees,
                                  "V2 %s %s %s: %s; pnl %.2f; fees %.2f" % (act, p["setup"], sym, why, pnl, fees), tid, p["setup"],
                                  desk="kraken" if p.get("venue") == "KRAKEN" else "v2"))
        restrike(b)
        if changed or lines: atomic(BOOK, b)
        if lines: append_firm_row(b)
    if lines:
        with desk_lock.ledger_lock("tfo_v2_staged", timeout=120):
            s2 = staged()
            for sym in list(s2["exit_flags"]):
                if sym not in b["positions"]: s2["exit_flags"].pop(sym, None)
            atomic(STAGED, s2)
    for r in rows: print("%-14s %-9s %s" % r)
    print("\n".join(lines) if lines else "LOG none")
    status_update("rails", not errors, errors, mode="execute" if execute else "preview", exits=len(lines), open=len(b["positions"]))


# ----------------------------------------------------------------------------- mark / report / backtest
def refresh_daily(p, t, errors):
    """Once per day: refresh ATR14 from completed daily bars and fold completed-bar highs since entry into the
    chandelier high-water mark (rails also folds in live prints every minute)."""
    day = t.strftime("%Y-%m-%d")
    if p.get("atr_asof") == day: return
    try:
        if p["venue"] == "KRAKEN":
            bars = kraken_daily(p["sym"]); e0 = parse(p["entry_utc"]).timestamp()
            highs = [x["h"] for x in bars if x["t"] + 86400 > e0]
        else:
            bars = us_daily([p["sym"]], "3mo").get(p["sym"]) or []
            ed = parse(p["entry_utc"]).astimezone(NY).date().isoformat()
            highs = [x["h"] for x in bars if x["t"] >= ed]
        a = atr14(bars)
        if a: p["atr"] = a
        if highs: p["hh"] = max(float(p.get("hh") or p["entry_px"]), max(highs))
        p["atr_asof"] = day
    except Exception as e:
        errors.append("%s daily refresh: %s" % (p["sym"], e))


def cmd_mark():
    t = now_utc(); errors = []
    if not os.path.exists(BOOK): print("mark: V2 not funded"); status_update("mark", True, [], note="not funded"); return
    with desk_lock.ledger_lock(KEY, timeout=300):
        b = load_book()
        for sym, p in b["positions"].items():
            try:
                q = quote(sym); mk = q.get("mid") or q.get("px_usd")
                if mk and mk > 0:
                    p["mark_usd"], p["mark_utc"], p["mark_tape"] = mk, iso(t), q["tape"]
            except Exception as e:
                errors.append("%s mark: %s" % (sym, e))
            refresh_daily(p, t, errors)
        restrike(b); atomic(BOOK, b); fs = append_firm_row(b)
    print(json.dumps({"equity": b["equity"], "cash": b["cash"], "n": len(b["positions"]), "level": fs["level"], "dd_pct": fs["dd_pct"], "day_pnl_pct": fs["day_pnl_pct"]}))
    status_update("mark", not errors, errors, v2_equity_usd=b["equity"], level=fs["level"])


def cmd_report():
    b = load_book(); fs = firm_state(b); cfg = config()
    print("TFO V2 (separate paper fund) equity $%s seed $%s P&L $%s (%.2f%%) dd %.2f%% day %.2f%% level %s" % (
        "{:,.2f}".format(b["equity"]), "{:,.2f}".format(b["seed_usd"]), "{:,.2f}".format(b["equity"] - b["seed_usd"]),
        100 * (b["equity"] / b["seed_usd"] - 1), fs["dd_pct"], fs["day_pnl_pct"], fs["level"]))
    print("cash $%s  heat $%s (%.2f%% of equity)  fees paid $%s" % ("{:,.2f}".format(b["cash"]), "{:,.0f}".format(heat(b)), 100 * heat(b) / b["equity"], "{:,.2f}".format(b.get("fees_paid_usd") or 0)))
    for s, p in b["positions"].items():
        print("  %-4s %-9s qty %.6g fill %.6g mark %.6g stop %.6g banked %s" % (p["setup"], s, p["qty"], p["entry_px"], p.get("mark_usd") or 0, p["stop"], p["banked"]))
    for k, v in cfg["setups"].items():
        print("  %s %-40s %s %s" % (k, v["name"], "ENABLED" if v.get("enabled") else "DISABLED", v.get("disabled_reason", "")))


def cmd_backtest(which):
    import v2_backtest
    r = v2_backtest.run(tuple([which]) if which else ("S1", "S2", "S3", "S4"))
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    atomic(os.path.join(V2, "tfo_v2_backtest_latest.json"), out)
    print(json.dumps(out, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="c", required=True)
    f = sp.add_parser("fund"); f.add_argument("amount", nargs="?", type=float); f.add_argument("--match-v1", action="store_true"); f.add_argument("--execute", action="store_true")
    g = sp.add_parser("signals"); g.add_argument("--setup", choices=["S1", "S2", "S3", "S4"]); g.add_argument("--force", action="store_true")
    e = sp.add_parser("enter"); e.add_argument("--execute", action="store_true")
    r = sp.add_parser("rails"); r.add_argument("--execute", action="store_true")
    sp.add_parser("mark"); sp.add_parser("report"); sp.add_parser("coverage")
    bt = sp.add_parser("backtest"); bt.add_argument("setup", nargs="?", choices=["S1", "S2", "S3", "S4"])
    a = ap.parse_args()
    os.makedirs(os.path.join(V2, "logs"), exist_ok=True)
    runner = {"enter": "enter", "rails": "rails", "signals": "signals", "mark": "mark"}.get(a.c)
    try:
        if a.c == "fund": return cmd_fund(a.amount, a.match_v1, a.execute)
        if a.c == "signals": return cmd_signals(a.setup, a.force)
        if a.c == "enter": return cmd_enter(a.execute)
        if a.c == "rails": return cmd_rails(a.execute)
        if a.c == "mark": return cmd_mark()
        if a.c == "report": return cmd_report()
        if a.c == "coverage": return cmd_coverage()
        if a.c == "backtest": return cmd_backtest(a.setup)
    except SystemExit:
        raise
    except Exception as ex:          # fail safe: record, never crash-loop silently
        traceback.print_exc()
        if runner:
            try: status_update(runner, False, ["%s: %s" % (type(ex).__name__, ex)])
            except Exception: pass
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
