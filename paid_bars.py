"""paid_bars.py — paid-first BAR sourcing for the hunt engines. PAPER ONLY. 2026-10-30 23:0x WITA TFO ruling (paidbars).

Every function returns EXACTLY the schema the engine used from Yahoo, or None so the caller falls back to its unchanged
Yahoo function. Paid data is used only where it was verified to match Yahoo's schema, timezone, bucket boundaries,
adjustment and completed-bar semantics (verified 30 Oct against today's bars):
  US 15m     Massive 15m aggs, regular session starts 09:30<=start<16:00 ET, last 5 sessions, in-progress bar kept
             (same as Yahoo; manage()/try_fill() already skip bars with start+15m > now).
  US daily   Massive 1d aggs (split-adj) x Massive cash-dividend back-adjustment (= Yahoo auto_adjust; max diff <0.06%).
  ASX daily  EODHD eod, OHLC scaled by adjusted_close/close (= Yahoo; exact). Used only if it has the latest session row.
  ASX 1h     EODHD intraday 1h (UTC start; END = start+1h; same 10:00..16:00-auction buckets as Yahoo). Used only when it
             covers every bucket that has ENDED by now (else Yahoo) -> never a partial or missing bar treated as complete.
  JP (.T), LSE (.L): no paid source matches (EODHD: no TSE intraday/eod in plan, LSE intraday end-of-day only + 30-min
             auction bar; FMP drops the ASX/LSE auction and JP 12:30 half-hour bucket, unadjusted mismatch) -> Yahoo, logged.
Each fetch is logged (sym, kind, source, reason) to state/paid_bars/fetch_YYYYMMDD.jsonl; STATS counts per source.
No keys are read here (paid_feeds reads them internally). Py3.9-safe."""
import datetime as dt, json, os, threading, time, urllib.parse
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc; NY = ZoneInfo("America/New_York"); SYD = ZoneInfo("Australia/Sydney")
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
LOGD = os.path.join(BASE, "state", "paid_bars")
STATS = {}; _LK = threading.Lock(); _DIV = {}
ENGINE = os.path.basename(os.environ.get("PAID_BARS_ENGINE", "") or "")

def log(sym, kind, source, reason=None, n=None):
    with _LK:
        STATS[source] = STATS.get(source, 0) + 1
    try:
        os.makedirs(LOGD, exist_ok=True)
        now = dt.datetime.now(UTC)
        with open(os.path.join(LOGD, "fetch_%s.jsonl" % now.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")), "a") as f:
            f.write(json.dumps(dict(utc=now.isoformat(timespec="seconds"), pid=os.getpid(), sym=sym, kind=kind, source=source, reason=reason, n=n)) + "\n")
    except Exception: pass

def _pf():
    import paid_feeds as P; return P

def _msym(sym): return urllib.parse.quote(str(sym).replace("-", "."))
def _is_us(sym): s = str(sym); return "." not in s and "=" not in s and not s.startswith("^")

# ---------------------------------------------------------------- US
def us_15m_from_rows(rows, sessions=5):
    """Massive 15m agg rows -> Yahoo bars_15m schema (regular session only, last `sessions` session dates)."""
    out = []
    for r in rows or []:
        try:
            st = dt.datetime.fromtimestamp(r["t"] / 1000.0, UTC); lt = st.astimezone(NY)
            if lt.weekday() >= 5 or not (dt.time(9, 30) <= lt.time() < dt.time(16, 0)): continue
            out.append(dict(start=st, o=float(r["o"]), h=float(r["h"]), l=float(r["l"]), c=float(r["c"]), v=float(r.get("v") or 0) or None))
        except Exception: continue
    out.sort(key=lambda b: b["start"])
    days = sorted({b["start"].astimezone(NY).date() for b in out})[-sessions:]
    return [b for b in out if b["start"].astimezone(NY).date() in days]

def us_15m(sym, now=None):
    if not _is_us(sym): return None
    now = now or dt.datetime.now(UTC); d = now.astimezone(NY).date()
    try:
        js = _pf().massive("/v2/aggs/ticker/%s/range/15/minute/%s/%s" % (_msym(sym), d - dt.timedelta(days=12), d), adjusted="true", sort="asc", limit=50000)
        b = us_15m_from_rows(js.get("results"))
        if not b: raise RuntimeError("no regular-session bars")
        log(sym, "15m", "massive", n=len(b)); return b
    except Exception as e:
        log(sym, "15m", "yahoo_fallback", str(e)[:100]); return None

def div_adjust(rows, divs):
    """rows [(date,h,l,c)] split-adjusted, divs [(ex_date, cash)] -> [{h,l,c}] back-adjusted like Yahoo (factor 1-div/prev_close)."""
    f = [1.0] * len(rows)
    for ex, amt in divs:
        idx = [i for i, r in enumerate(rows) if r[0] < ex]
        if not idx or idx[-1] == len(rows) - 1: continue      # ex-date before window or after the last bar -> no effect
        pc = rows[idx[-1]][3]
        if not pc: continue
        k = 1.0 - float(amt) / pc
        for i in idx: f[i] *= k
    return [dict(h=r[1] * f[i], l=r[2] * f[i], c=r[3] * f[i]) for i, r in enumerate(rows)]

def _yahoo_1y_start(now):
    """First session date Yahoo history(period='1y') returns: the UTC date one year back (verified 30 Oct: 252/252 US, 255/255 ASX)."""
    d = now.astimezone(UTC).date()
    return d.replace(year=d.year - 1) if not (d.month == 2 and d.day == 29) else d.replace(year=d.year - 1, day=28)

def us_daily(sym, now=None):
    if not _is_us(sym): return None
    now = now or dt.datetime.now(UTC); d = now.astimezone(NY).date()
    try:
        P = _pf(); start = _yahoo_1y_start(now)
        js = P.massive("/v2/aggs/ticker/%s/range/1/day/%s/%s" % (_msym(sym), start, d), adjusted="true", sort="asc", limit=50000)
        rows = [(dt.datetime.fromtimestamp(r["t"] / 1000.0, NY).date(), float(r["h"]), float(r["l"]), float(r["c"])) for r in (js.get("results") or []) if r.get("c")]
        if not rows: raise RuntimeError("no daily bars")
        if sym not in _DIV:
            dv = P.massive("/v3/reference/dividends", ticker=str(sym).replace("-", "."), limit=1000, order="desc")
            _DIV[sym] = [(dt.date.fromisoformat(x["ex_dividend_date"]), float(x["cash_amount"])) for x in (dv.get("results") or []) if x.get("ex_dividend_date") and x.get("cash_amount")]
        out = div_adjust(rows, _DIV[sym]); log(sym, "1d", "massive", n=len(out)); return out
    except Exception as e:
        log(sym, "1d", "yahoo_fallback", str(e)[:100]); return None

# ---------------------------------------------------------------- ASX
def _asx_last_session_date(now):
    """Most recent ASX weekday whose session has STARTED by now (Sydney date). Holidays -> EODHD lacks it -> Yahoo."""
    l = now.astimezone(SYD); d = l.date()
    if l.time() < dt.time(10, 0): d -= dt.timedelta(days=1)
    while d.weekday() >= 5: d -= dt.timedelta(days=1)
    return d

def asx_daily_from_rows(rows, need_date):
    out, last = [], None
    for r in rows or []:
        try:
            c, a = float(r["close"]), float(r["adjusted_close"])
            if not c: continue
            k = a / c; out.append(dict(h=float(r["high"]) * k, l=float(r["low"]) * k, c=a)); last = r["date"]
        except Exception: continue
    if not out or last != need_date.isoformat(): return None
    return out

def asx_daily(sym, now=None):
    if not str(sym).endswith(".AX"): return None
    now = now or dt.datetime.now(UTC); d = now.astimezone(SYD).date()
    try:
        need = _asx_last_session_date(now)
        l = now.astimezone(SYD)
        if l.weekday() < 5 and dt.time(10, 0) <= l.time():      # session started today: Yahoo carries today's (partial) row; EODHD eod only after close
            need = l.date()
        start = _yahoo_1y_start(now)
        rows = _pf().eodhd("eod/" + str(sym)[:-3] + ".AU", **{"from": start.isoformat()})
        out = asx_daily_from_rows(rows, need)
        if not out: raise RuntimeError("eodhd eod lacks session %s" % need)
        log(sym, "1d", "eodhd", n=len(out)); return out
    except Exception as e:
        log(sym, "1d", "yahoo_fallback", str(e)[:100]); return None

def asx_buckets_ended(now, start):
    """END times (UTC) of every ASX 1h bucket (10:00..16:00 local starts; 16:00 = closing-auction bucket) with start>=start-2h and END<=now."""
    out = []; d = (start - dt.timedelta(hours=2)).astimezone(SYD).date(); last = now.astimezone(SYD).date()
    while d <= last:
        if d.weekday() < 5:
            for h in range(10, 17):
                e = dt.datetime(d.year, d.month, d.day, h, 0, tzinfo=SYD).astimezone(UTC) + dt.timedelta(hours=1)
                if e <= now and e - dt.timedelta(hours=1) >= start - dt.timedelta(hours=2): out.append(e)
        d += dt.timedelta(days=1)
    return out

def asx_1h_from_rows(rows):
    out = []
    for r in rows or []:
        try:
            t = dt.datetime.fromtimestamp(int(r["timestamp"]), UTC) + dt.timedelta(hours=1)   # bar END (Yahoo convention in cal_paper_bench.bars_yf)
            v = r.get("volume")
            out.append(dict(t=t, o=float(r["open"]), h=float(r["high"]), l=float(r["low"]), c=float(r["close"]), v=(float(v) if v is not None else None)))
        except Exception: continue
    out.sort(key=lambda b: b["t"]); return out

def asx_1h(sym, start, now=None, require_cover=True):
    if not str(sym).endswith(".AX"): return None
    now = now or dt.datetime.now(UTC)
    try:
        d0 = (start - dt.timedelta(hours=2)).astimezone(UTC).date()   # same first day as bars_yf (download start = that date)
        rows = _pf().eodhd("intraday/" + str(sym)[:-3] + ".AU", interval="1h", **{"from": int(dt.datetime(d0.year, d0.month, d0.day, tzinfo=UTC).timestamp())})
        b = asx_1h_from_rows(rows)
        if not b: raise RuntimeError("no 1h bars")
        if require_cover:   # every ENDED bucket must be present (no-trade buckets for illiquid names -> Yahoo, conservative)
            have = {x["t"] for x in b}; miss = [e for e in asx_buckets_ended(now, start) if e not in have]
            if miss: raise RuntimeError("eodhd 1h missing %d ended bucket(s), last %s" % (len(miss), miss[-1].isoformat()))
        log(sym, "1h", "eodhd", n=len(b)); return b
    except Exception as e:
        log(sym, "1h", "yahoo_fallback", str(e)[:100]); return None

# ---------------------------------------------------------------- engine entry points (paid first, Yahoo fallback)
def hourly(y, start, yahoo=None):
    """Drop-in for cal_paper_bench.bars_yf(y, start)."""
    b = asx_1h(y, start) if str(y).endswith(".AX") else None
    if b is None:
        if not str(y).endswith(".AX"): log(y, "1h", "yahoo", "no exact-match paid 1h source for this venue")
        if yahoo is None:
            import cal_paper_bench as CB; yahoo = CB.bars_yf
        return yahoo(y, start)
    return b

_DC = {}
def daily_hr(sym, hr_daily, status=None):
    """Paid-first wrapper for hunt48_room.daily(sym) ({h,l,c} 1y), cached per process like HR.daily.
    Falls back to hr_daily (its retries + disk cache + DATA_STATUS unchanged)."""
    if sym in _DC: return _DC[sym]
    b = asx_daily(sym) if str(sym).endswith(".AX") else None
    if b is None:
        if not str(sym).endswith(".AX"): log(sym, "1d", "yahoo", "no exact-match paid daily source for this venue")
        return hr_daily(sym)
    _DC[sym] = b
    if status is not None: status[sym] = "ok"
    return b
