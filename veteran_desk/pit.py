"""Veteran desk phase 1 - point-in-time (PIT) history store. PAPER only, read-only data. Built 2026-10-28 (Brett order 16:35 WITA).
Every row carries knowable_utc = the moment the value became public. Queries MUST pass asof and never see knowable_utc > asof.
Store: data/pit/pit.sqlite  tables: bars(sym,date,o,h,l,c,v,knowable_utc,src) macro(series,date,value,knowable_utc,src)
filings(cik,sym,form,filed_utc,accession,src) universe(sym,venue,status,delisted_date,src) venues(venue,bias_flag,why)."""
import datetime as dt, json, os, sqlite3, sys, time, urllib.request, io, csv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); DB = os.path.join(BASE, "data", "pit", "pit.sqlite")
UTC = dt.timezone.utc
# Conservative publish lag: a daily bar for date D is knowable at the venue's close + 30 min (UTC hour,minute, day offset).
CLOSE_UTC = {"US": (20, 30, 0), "ASX": (6, 40, 0), "FX": (21, 30, 0), "CRYPTO": (0, 30, 1), "FUT": (21, 0, 0), "IDX": (21, 0, 0),
             "LSE": (16, 35, 0), "Euronext": (17, 35, 0), "XETRA": (17, 35, 0), "TSE": (7, 0, 0), "HKEX": (8, 40, 0),
             "SGX": (9, 46, 0), "KRX": (7, 0, 0), "TWSE": (6, 0, 0), "NSE": (10, 30, 0), "NZX": (5, 15, 0),
             "SIX": (16, 31, 0), "BME": (17, 35, 0), "MILAN": (17, 35, 0), "NORDIC": (16, 30, 0), "TSX": (21, 30, 0), "SSE": (7, 30, 0), "SZSE": (7, 30, 0)}
_INTL_BIAS = ("UNIVERSE_BIASED", "Yahoo daily history excludes delisted names on this venue; no free delisting file.")
VENUE_BIAS = {  # survivorship flags: free sources do not carry delisted names for these
    "US": ("UNIVERSE_BIASED", "Yahoo/stooq daily history only lists currently-traded tickers; delisted US names not available free. EDGAR filings retained for dead CIKs."),
    "ASX": ("UNIVERSE_BIASED", "Yahoo .AX history excludes delisted ASX names; no free ASX delisting file."),
    "FX": ("OK", "Currency pairs do not delist."), "FUT": ("RATIO_BACK_ADJUSTED", "=F rows in bars are ratio back-adjusted to the live front contract (veteran_desk/fut_roll.py, 2026-10-28); as-traded Yahoo rows in bars_fut_raw; rolls in fut_rolls. Returns PIT-clean; old levels embed later roll factors."),
    "IDX": ("OK", "Index/yield levels."), "MACRO": ("OK_REVISION_NOTE", "FRED latest vintage; ALFRED vintages not yet pulled (phase 2) - revised series flagged."),
    "CRYPTO": ("UNIVERSE_BIASED", "Kraken public OHLC only for listed pairs; delisted coins missing."),
    "LSE": _INTL_BIAS, "Euronext": _INTL_BIAS, "XETRA": _INTL_BIAS, "TSE": _INTL_BIAS, "HKEX": _INTL_BIAS, "SGX": _INTL_BIAS,
    "KRX": _INTL_BIAS, "TWSE": _INTL_BIAS, "NSE": _INTL_BIAS, "NZX": _INTL_BIAS, "SIX": _INTL_BIAS, "BME": _INTL_BIAS,
    "MILAN": _INTL_BIAS, "NORDIC": _INTL_BIAS, "TSX": _INTL_BIAS, "SSE": _INTL_BIAS, "SZSE": _INTL_BIAS,
}
# Yahoo exchange suffix -> venue (costs/sessions). LOCAL_CLOSE still stamps knowable_utc. Crypto/Kraken unchanged.
SUFFIX_VENUE = {"AX": "ASX", "L": "LSE", "IL": "LSE", "PA": "Euronext", "AS": "Euronext", "BR": "Euronext", "LS": "Euronext", "VI": "Euronext",
    "DE": "XETRA", "T": "TSE", "HK": "HKEX", "SI": "SGX", "KS": "KRX", "KQ": "KRX", "TW": "TWSE", "TWO": "TWSE",
    "NS": "NSE", "BO": "NSE", "NZ": "NZX", "SW": "SIX", "ST": "NORDIC", "CO": "NORDIC", "HE": "NORDIC", "OL": "NORDIC",
    "MC": "BME", "MI": "MILAN", "IR": "Euronext", "TO": "TSX", "V": "TSX", "SS": "SSE", "SZ": "SZSE"}
try:
    from veteran_desk import yf_guard as _YG; _YG.install()   # tfo_seatpipe_yf429_20261030 Yahoo 429 backoff+cache
    try:
        from veteran_desk import paid_first as _PF; _PF.install()   # tfo_paidfirst_wire_20261030: paid_first.py was never installed; EODHD-first daily bars, Yahoo only as fallthrough
    except Exception: pass
except Exception: pass
_SCHEMA_NAMES = ('bars','macro','filings','universe','venues','bar_review','ix_bars_sym_know','ix_macro_series_know','ix_filings_sym_filed')
def con(path=None):
    """tfo_pitlock_20261101 (ported from droplet overlay, PAPER): skip schema DDL (takes a write lock) when the schema
    already exists, so readers never hit 'database is locked' while the Kraken daily backfill writes; retry DDL on lock."""
    p = path or DB
    if os.path.exists(p):
        try:
            c = sqlite3.connect(p, timeout=60); c.execute("PRAGMA busy_timeout=60000")
            if c.execute("select count(*) from sqlite_master where name in (%s)" % ",".join("?" * len(_SCHEMA_NAMES)), _SCHEMA_NAMES).fetchone()[0] >= len(_SCHEMA_NAMES): return c
            c.close()
        except sqlite3.OperationalError: pass
    for i in range(6):
        try: return _con_ddl(path)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == 5: raise
            time.sleep(10)
def _con_ddl(path=None):
    os.makedirs(os.path.dirname(path or DB), exist_ok=True); c = sqlite3.connect(path or DB, timeout=60)
    c.executescript("""CREATE TABLE IF NOT EXISTS bars(sym TEXT,date TEXT,o REAL,h REAL,l REAL,c REAL,v REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(sym,date));
    CREATE TABLE IF NOT EXISTS macro(series TEXT,date TEXT,value REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(series,date));
    CREATE TABLE IF NOT EXISTS filings(cik TEXT,sym TEXT,form TEXT,filed_utc TEXT,accession TEXT PRIMARY KEY,src TEXT);
    CREATE TABLE IF NOT EXISTS universe(sym TEXT PRIMARY KEY,venue TEXT,status TEXT,delisted_date TEXT,src TEXT);
    CREATE TABLE IF NOT EXISTS venues(venue TEXT PRIMARY KEY,bias_flag TEXT,why TEXT);
    CREATE INDEX IF NOT EXISTS ix_bars_sym_know ON bars(sym, knowable_utc);
    CREATE INDEX IF NOT EXISTS ix_macro_series_know ON macro(series, knowable_utc);
    CREATE INDEX IF NOT EXISTS ix_filings_sym_filed ON filings(sym, filed_utc);"""); c.execute(REVIEW_DDL); c.execute("PRAGMA busy_timeout=60000"); return c
# 2026-10-29 stale-bar fix: non-US listings (.T/.HK/.L/.PA ...) were stamped with the US 20:30 UTC close (8411.T). Local close + 30 min, DST-aware.
LOCAL_CLOSE = {"L": ("Europe/London", 16, 35), "DE": ("Europe/Berlin", 17, 35), "PA": ("Europe/Paris", 17, 35), "AS": ("Europe/Amsterdam", 17, 35), "BR": ("Europe/Brussels", 17, 35),
    "LS": ("Europe/Lisbon", 16, 35), "SW": ("Europe/Zurich", 17, 31), "ST": ("Europe/Stockholm", 17, 30), "CO": ("Europe/Copenhagen", 17, 0), "HE": ("Europe/Helsinki", 18, 30),
    "OL": ("Europe/Oslo", 16, 25), "MC": ("Europe/Madrid", 17, 35), "MI": ("Europe/Rome", 17, 35), "IR": ("Europe/Dublin", 16, 35), "VI": ("Europe/Vienna", 17, 35),
    "T": ("Asia/Tokyo", 15, 30), "HK": ("Asia/Hong_Kong", 16, 10), "KS": ("Asia/Seoul", 15, 30), "KQ": ("Asia/Seoul", 15, 30), "TW": ("Asia/Taipei", 13, 30), "TWO": ("Asia/Taipei", 13, 30),
    "SI": ("Asia/Singapore", 17, 16), "NS": ("Asia/Kolkata", 15, 30), "BO": ("Asia/Kolkata", 15, 30), "NZ": ("Pacific/Auckland", 16, 45), "SS": ("Asia/Shanghai", 15, 0), "SZ": ("Asia/Shanghai", 15, 0)}
def local_suffix(sym):
    return sym.rsplit(".", 1)[-1].upper() if "." in sym and not sym.startswith("KRAKEN:") and sym.rsplit(".", 1)[-1].upper() in LOCAL_CLOSE else None
_TZ = {}
def _zone(name):
    z = _TZ.get(name)
    if z is None:
        from zoneinfo import ZoneInfo
        z = ZoneInfo(name); _TZ[name] = z
    return z
def knowable(date_str, venue, sym=None):
    suf = local_suffix(sym) if sym else None
    if suf:
        tz, hh, mm = LOCAL_CLOSE[suf]; D = dt.date.fromisoformat(date_str)
        return (dt.datetime(D.year, D.month, D.day, hh, mm, tzinfo=_zone(tz)) + dt.timedelta(minutes=30)).astimezone(UTC).isoformat()
    if venue == "US":   # usclose_20261103 (master F16): US close stamp follows New York time (16:15 ET); the fixed 20:30 UTC was 15:30 EST in winter, BEFORE the 16:00 close
        D = dt.date.fromisoformat(date_str)
        return dt.datetime(D.year, D.month, D.day, 16, 15, tzinfo=_zone("America/New_York")).astimezone(UTC).isoformat()
    h, m, d = CLOSE_UTC.get(venue, (23, 59, 1)); D = dt.date.fromisoformat(date_str) + dt.timedelta(days=d)
    return dt.datetime(D.year, D.month, D.day, h, m, tzinfo=UTC).isoformat()
def venue_of(sym):
    if sym.startswith("KRAKEN:"): return "CRYPTO"
    if str(sym).upper().startswith("PF_"): return "CRYPTO"   # tfo_pfshort_20261101: Kraken Futures perp (bare PF_ symbol)
    if sym.endswith("=X"): return "FX"
    if sym.endswith("=F"): return "FUT"
    if sym.startswith("^"): return "IDX"
    if "." in sym:
        suf = sym.rsplit(".", 1)[-1].upper()
        if suf in SUFFIX_VENUE: return SUFFIX_VENUE[suf]
    return "US"
def banned(sym): return sym.upper().endswith(".JK") or "IDR" in sym.upper()   # Brett: no Indonesian assets
HL_ATR_K = 3.0; HL_MIN_REL = 0.05   # excess must be >3x ATR AND >5% of the body edge (stops tick-size noise on illiquid names)
def hl_check(o, h, l, cl, atr, k=HL_ATR_K, rel=HL_MIN_REL):
    """2026-10-29 hl-sanity: pure check, returns (h, l, flags) with h/l clamped to the body edge when flagged. Since flagmode put_bars stores the RAW bar and only uses the flags."""
    fl = []
    if atr and atr > 0:
        if h > max(o, cl) + max(k * atr, rel * max(o, cl)): fl.append("high %.4g > body %.4g + %gxATR %.4g" % (h, max(o, cl), k, atr)); h = max(o, cl)
        if l < min(o, cl) - max(k * atr, rel * min(o, cl)): fl.append("low %.4g < body %.4g - %gxATR %.4g" % (l, min(o, cl), k, atr)); l = min(o, cl)
    return h, l, fl
def _atr_of(b):
    """Mean true range of an ascending (h,l,c) window. Same formula as the per-bar SQL path. None until 6 bars."""
    if len(b) < 6: return None
    tr = [max(b[i][0] - b[i][1], abs(b[i][0] - b[i - 1][2]), abs(b[i][1] - b[i - 1][2])) for i in range(1, len(b))]
    return sum(tr) / len(tr)
def _atr_before(c, sym, date, n=14):
    b = c.execute("SELECT h,l,c FROM bars WHERE sym=? AND date<? ORDER BY date DESC LIMIT ?", (sym, date, n + 1)).fetchall()[::-1]
    return _atr_of(b)
def _atr_from_prior(prior, date, n=14):
    """prior is ascending (date,h,l,c) already stored. ATR uses only bars strictly before `date` (the ingest batch is not visible yet)."""
    b = [r[1:] for r in prior if r[0] < date][-(n + 1):]
    return _atr_of(b)
HL_REJECTS = []   # (sym, date, flags) for every bar the filter flags (flag mode) or clips (legacy clip mode)
HL_REAPPLIED = []   # (sym, date) where a stored IBKR-confirmed fix was re-applied to a re-downloaded raw bar
# 2026-10-29 flagmode (Brett): the ingest filter no longer clips blindly. It stores the RAW bar and writes the flag to bar_review.
# Only bars IBKR confirms bad (veteran_desk/hl_review_ibkr.py, read-only via ibkr_bar_check.py) are clipped; that fix is remembered
# here so a later Yahoo re-download cannot undo it. PIT_HL_MODE=clip restores the old blind clamp (rollback switch). Crypto exempt.
HL_MODE = os.environ.get("PIT_HL_MODE", "flag")
REVIEW_DDL = ("CREATE TABLE IF NOT EXISTS bar_review(sym TEXT,date TEXT,o REAL,h REAL,l REAL,c REAL,atr REAL,flags TEXT,src TEXT,"
              "status TEXT,fix_h REAL,fix_l REAL,ibkr TEXT,why TEXT,flagged_utc TEXT,resolved_utc TEXT,PRIMARY KEY(sym,date))")
REVIEW_BAD = ("ibkr_bad", "hand_bad")   # resolved: clip to fix_h/fix_l
def _review_get(c, sym, date):
    try: return c.execute("SELECT status,h,l,fix_h,fix_l FROM bar_review WHERE sym=? AND date=?", (sym, date)).fetchone()
    except sqlite3.OperationalError: c.execute(REVIEW_DDL); return None
def _review_flag(c, sym, r, atr, fl, src, prev):
    """Upsert a pending review row unless the same raw bar was already resolved (ok/no_ibkr keep their verdict)."""
    if prev and prev[0] != "pending" and abs((prev[1] or 0) - r[2]) < 1e-6 * max(1.0, abs(r[2])) and abs((prev[2] or 0) - r[3]) < 1e-6 * max(1.0, abs(r[3])): return
    c.execute("INSERT OR REPLACE INTO bar_review(sym,date,o,h,l,c,atr,flags,src,status,flagged_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (sym, r[0], r[1], r[2], r[3], r[4], atr, json.dumps(fl), src, "pending", dt.datetime.now(UTC).isoformat()))
def sane_rows(c, sym, rows, src=None):
    if ":" in sym or venue_of(sym) == "CRYPTO": return rows   # real crypto wicks are kept; filter is for equity/ETF bad prints (LSE 15 Oct etc.)
    out = []
    prior = c.execute("SELECT date,h,l,c FROM bars WHERE sym=? ORDER BY date", (sym,)).fetchall()   # one read; ATR matches the old per-bar query (batch not yet inserted)
    for r in rows:
        try:
            prev = _review_get(c, sym, r[0])
            if prev and prev[0] in REVIEW_BAD:   # IBKR-confirmed (or hand-list) bad: re-apply the verified fix, never a blind clamp
                h, l = r[2], r[3]
                if prev[3] is not None and h > prev[3]: h = prev[3]
                if prev[4] is not None and l < prev[4]: l = prev[4]
                if (h, l) != (r[2], r[3]): HL_REAPPLIED.append((sym, r[0])); r = (r[0], r[1], h, l, *r[4:])
                out.append(r); continue
            atr = _atr_from_prior(prior, r[0]); h, l, fl = hl_check(r[1], r[2], r[3], r[4], atr)
            if fl:
                HL_REJECTS.append((sym, r[0], fl))
                if HL_MODE == "clip": r = (r[0], r[1], h, l, *r[4:])   # legacy blind clamp (rollback switch only)
                else: _review_flag(c, sym, r, atr, fl, src, prev)   # flag mode: raw bar stored, flag queued for the IBKR batch check
        except Exception: pass
        out.append(r)
    return out
def put_bars(c, sym, rows, src):
    if banned(sym): raise ValueError("Indonesian asset banned: " + sym)
    v = venue_of(sym); _now = dt.datetime.now(UTC).isoformat()
    rows = [r for r in rows if knowable(r[0], v, sym) <= _now]   # 2026-10-29 lookahead fix: never store an unfinished session's bar (partial values would surface at knowable_utc)
    n0 = len(HL_REAPPLIED); rows = sane_rows(c, sym, rows, src)   # 2026-10-29 flagmode: raw bar kept + bar_review flag; IBKR-confirmed fixes re-applied
    fixed = set(d for s_, d in HL_REAPPLIED[n0:] if s_ == sym)
    c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(sym, r[0], *r[1:6], knowable(r[0], v, sym), src + ("|hlreview_fix" if r[0] in fixed else "")) for r in rows])
    c.execute("INSERT OR IGNORE INTO universe VALUES(?,?,?,?,?)", (sym, v, "listed", None, src))
def chart_tz(meta):
    """fxfeed_20261103: the listing's own zone (meta.exchangeTimezoneName, DST-aware), fixed meta.gmtoffset only as a fallback.
    gmtoffset is the CURRENT offset only: applied to bars from before a clock change (EU 25 Oct, US 1 Nov 2026) it dates them one
    day early (CL=F 04:00 UTC = 00:00 EDT -> 23:00 EST prev day; EURUSD=X 23:00 UTC = 00:00 BST -> prev UTC day) and a
    backfill then OVERWRITES good rows one day off. Named zone -> every bar lands on Yahoo's own session date."""
    name = (meta or {}).get("exchangeTimezoneName")
    if name:
        try: return _zone(name)
        except Exception: pass
    return dt.timezone(dt.timedelta(seconds=int((meta or {}).get("gmtoffset") or 0)))
def chart_rows(result):
    """[(date, o, h, l, c, v)] from ONE Yahoo chart v8 result dict (pure, no network; unit-tested with a fake payload).
    date = the bar timestamp rendered in chart_tz(meta) - identical to yfinance / Yahoo's own history table. NaN closes dropped."""
    ts = result.get("timestamp") or []; q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    tz = chart_tz(result.get("meta")); out = []
    for i, t in enumerate(ts):
        o, h, l, cl, v = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close", "volume"))
        if cl is None or cl != cl or h is None or l is None: continue
        out.append((dt.datetime.fromtimestamp(int(t), tz).date().isoformat(), float(o if o is not None else cl), float(h), float(l), float(cl), float(v or 0)))
    return out
def yahoo_chart(sym, period="1mo"):
    """2026-10-29: direct Yahoo chart v8 endpoint (works where yfinance silently returns empty / 'possibly delisted', e.g. py3.9 yfinance 0.2.40).
    Returns [(date, o, h, l, c, v)] in the listing's local trading dates, NaN closes dropped."""
    import urllib.parse
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d&includePrePost=false&events=" % (urllib.parse.quote(sym), period)
    rq = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}); J = json.loads(urllib.request.urlopen(rq, timeout=30).read())
    R = (J.get("chart") or {}).get("result") or []
    if not R: return []
    return chart_rows(R[0])   # fxfeed_20261103: dates in the listing's own DST-aware zone (was one fixed gmtoffset)
def yahoo_rows(sym, period="10y"):
    """Yahoo primary: yfinance first, direct chart endpoint if yfinance is empty or stale. Returns (rows, nan_dates, src)."""
    rows, nan, src = [], [], "yahoo"
    try:
        import yfinance as yf
        df = yf.Ticker(sym).history(period=period, auto_adjust=False, actions=False)
        rows = [(d.date().isoformat(), float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume or 0)) for d, r in df.iterrows() if r.Close == r.Close]
        nan = [d.date().isoformat() for d, r in df.iterrows() if r.Close != r.Close]
    except Exception: rows = []
    if not rows or rows[-1][0] < (dt.date.today() - dt.timedelta(days=4)).isoformat():
        try:
            ch = yahoo_chart(sym, period)
            if ch and (not rows or ch[-1][0] > rows[-1][0]): rows, src = ch, "yahoo_chart_v8"
        except Exception: pass
    return rows, nan, src
def ingest_yahoo(c, sym, period="10y", fetched=None):
    rows, nan, ysrc = fetched if fetched is not None else yahoo_rows(sym, period)
    if venue_of(sym) == "FUT":   # 2026-10-28: raw -> bars_fut_raw, adjusted -> bars (see fut_roll.py)
        sys.path.insert(0, BASE); from veteran_desk import fut_roll as FR
        FR.ensure(c); FR.seed_raw_from_bars(c, sym)
        c.executemany("INSERT OR REPLACE INTO bars_fut_raw VALUES(?,?,?,?,?,?,?,?,?)", [(sym, r[0], *r[1:6], knowable(r[0], "FUT"), "yahoo") for r in rows])
        c.execute("INSERT OR IGNORE INTO universe VALUES(?,?,?,?,?)", (sym, "FUT", "listed", None, "yahoo")); FR.rebuild(c, sym); return len(rows)
    put_bars(c, sym, rows, ysrc)
    # 2026-10-29: Yahoo NaN close (28 Oct gap) or Yahoo missing the last session -> IBKR read-only fallback (US listings; never overwrites)
    if venue_of(sym) == "US" and "." not in sym:
        exp = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        if not rows or rows[-1][0] < exp: nan = list(nan) + [(dt.date.today() - dt.timedelta(days=k)).isoformat() for k in range(1, 6)]
        if nan: rows += _ibkr_fill(c, sym, nan)
    return len(rows)
def _ibkr_fill(c, sym, dates):
    """Fill US daily bars Yahoo left NaN from IBKR paper gateway (veteran_desk/ibkr_hist.py, readonly, no orders). Never overwrites an existing row."""
    try:
        import subprocess
        out = subprocess.run([os.path.expanduser("~/ibkr/venv/bin/python"), os.path.join(BASE, "veteran_desk", "ibkr_hist.py"), sym], capture_output=True, text=True, timeout=90).stdout
        got = [r for r in json.loads(out.strip().splitlines()[-1]).get(sym, []) if isinstance(r, list) and r[0] in dates]
        got = [r for r in got if knowable(r[0], "US") <= dt.datetime.now(UTC).isoformat()]   # 2026-10-29: no unfinished session bars
        got = [r for r in got if not c.execute("SELECT 1 FROM bars WHERE sym=? AND date=?", (sym, r[0])).fetchone()]
        if got: c.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(sym, r[0], *r[1:6], knowable(r[0], "US"), "ibkr_hist(TRADES,RTH)") for r in got])
        return got
    except Exception: return []
def _get(url, ua=True):
    rq = urllib.request.Request(url, headers={"User-Agent": "TFO paper research brett-desk research@example.com"} if ua else {})
    return urllib.request.urlopen(rq, timeout=30).read()
def ingest_fred(c, series, lag_days=1):
    txt = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}").decode(); n = 0
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try: val = float(r[1])
        except Exception: continue
        D = dt.date.fromisoformat(r[0]) + dt.timedelta(days=lag_days)
        c.execute("INSERT OR REPLACE INTO macro VALUES(?,?,?,?,?)", (series, r[0], val, dt.datetime(D.year, D.month, D.day, 21, 0, tzinfo=UTC).isoformat(), "fred_latest_vintage")); n += 1
    return n
def ingest_kraken(c, pair="XBTUSD"):
    sys.path.insert(0, BASE); import kraken_dns; kraken_dns.install()   # desk-approved DoH for Kraken public hosts only
    d = json.loads(_get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440", ua=False)); res = d.get("result") or {}
    k = [x for x in res if x != "last"]; rows = []
    for t, o, h, l, cl, vw, v, n in (res[k[0]] if k else []):
        rows.append((dt.datetime.fromtimestamp(int(t), UTC).date().isoformat(), float(o), float(h), float(l), float(cl), float(v)))
    put_bars(c, "KRAKEN:" + pair, rows, "kraken_public"); return len(rows)
def ingest_edgar(c, sym, cik):
    d = json.loads(_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")); r = d["filings"]["recent"]; n = 0
    from veteran_desk.filing_freshness import remember_submissions
    remember_submissions(sym,d)
    for form, acc, fd, at in zip(r["form"], r["accessionNumber"], r["filingDate"], r.get("acceptanceDateTime") or r["filingDate"]):
        if form not in ("10-K", "10-Q", "8-K", "20-F", "6-K"): continue
        ts = (at[:19] + "+00:00") if "T" in at else knowable(fd, "US")   # acceptance time = public moment
        c.execute("INSERT OR REPLACE INTO filings VALUES(?,?,?,?,?,?)", (str(cik), sym, form, ts, acc, "sec_edgar")); n += 1
    return n
# ---- point-in-time reads (the only API seats use) ----
import threading
_BAR_CACHE = None; _BAR_LOCK = threading.Lock()
class bar_cache:
    """Process-local read cache for bars_asof. Off by default so a raw SQL write cannot serve a stale row. desk/engine turns it on for one round."""
    def __enter__(self):
        global _BAR_CACHE
        self._prev = _BAR_CACHE
        if _BAR_CACHE is None: _BAR_CACHE = {}
        return _BAR_CACHE
    def __exit__(self, *a):
        global _BAR_CACHE
        _BAR_CACHE = self._prev
def _asof_iso(asof):
    return asof.astimezone(UTC).isoformat() if isinstance(asof, dt.datetime) else asof
def bars_asof(c, sym, asof, n=260):
    a = _asof_iso(asof); key = (sym, a, int(n))
    if _BAR_CACHE is not None:
        with _BAR_LOCK:
            hit = _BAR_CACHE.get(key)
        if hit is not None: return hit
    sql = "SELECT date,o,h,l,c,v,knowable_utc FROM bars WHERE sym=? AND knowable_utc<=? ORDER BY date DESC LIMIT ?"
    try:
        # The default planner selects (sym, knowable_utc) then sorts the full
        # history for every symbol. Walk the existing (sym, date) primary key
        # backwards instead; the same knowable cutoff still filters every row.
        rows = c.execute(sql.replace("FROM bars WHERE", "FROM bars INDEXED BY sqlite_autoindex_bars_1 WHERE"), (sym, a, n)).fetchall()
    except sqlite3.OperationalError as error:
        # Alternate/read-only fixture schemas may not have the canonical PK.
        # Preserve their existing query; propagate unrelated database failures.
        if "no such index" not in str(error).lower(): raise
        rows = c.execute(sql, (sym, a, n)).fetchall()
    q = list(reversed(rows))
    if _BAR_CACHE is not None:
        with _BAR_LOCK: _BAR_CACHE[key] = q
    return q
def bars_asof_many(c, syms, asof, n=260):
    """Same rows as bars_asof, using the (sym, date) primary key and the round cache. A window scan over every bar was slower than LIMIT."""
    return {s: bars_asof(c, s, asof, n) for s in dict.fromkeys(syms)}
def macro_asof(c, series, asof, n=60):
    a = asof.astimezone(UTC).isoformat() if isinstance(asof, dt.datetime) else asof
    return list(reversed(c.execute("SELECT date,value,knowable_utc FROM macro WHERE series=? AND knowable_utc<=? ORDER BY date DESC LIMIT ?", (series, a, n)).fetchall()))
def filings_asof(c, sym, asof, n=10):
    a = asof.astimezone(UTC).isoformat() if isinstance(asof, dt.datetime) else asof
    return c.execute("SELECT form,filed_utc,accession FROM filings WHERE sym=? AND filed_utc<=? ORDER BY filed_utc DESC LIMIT ?", (sym, a, n)).fetchall()
UNIVERSE = {"energy": ["XOM", "CVX", "COP", "EOG", "OXY", "WDS.AX", "STO.AX", "XLE"],
            "essentials": ["PG", "KO", "PEP", "WMT", "COST", "WOW.AX", "COL.AX", "NEE", "DUK", "SO", "AGL.AX", "ORG.AX", "XLP", "XLU"],
            "banks": ["JPM", "BAC", "WFC", "C", "CBA.AX", "WBC.AX", "NAB.AX", "ANZ.AX", "XLF"],
            "healthcare": ["JNJ", "UNH", "LLY", "MRK", "ABBV", "PFE", "CSL.AX", "COH.AX", "XLV"],
            "tech": ["NVDA", "AMD", "AVGO", "TSM", "MSFT", "GOOGL", "AMZN", "META", "ORCL", "CRWV", "NBIS", "SMCI", "MU", "QQQ", "SMH", "XLK"],
            "crypto": ["KRAKEN:XBTUSD", "KRAKEN:ETHUSD", "KRAKEN:SOLUSD", "KRAKEN:XRPUSD", "KRAKEN:LINKUSD", "KRAKEN:ADAUSD"],
            "fx_cmdty": ["AUDUSD=X", "USDJPY=X", "EURUSD=X", "GBPUSD=X", "USDCAD=X", "NZDUSD=X", "AUDJPY=X", "CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "ZW=F", "ZC=F"],
            "macro": ["^TNX", "CL=F", "BZ=F", "NG=F", "AUDUSD=X", "USDJPY=X", "EURUSD=X", "GC=F", "^GSPC"]}
CRYPTO_CORE = list(UNIVERSE["crypto"])   # refreshed by refresh(); full Kraken list refreshed by com.tfo.pit-kraken-daily
def _kraken_all():
    try:
        J = json.load(open(os.path.join(BASE, "data", "pit", "kraken_pairs.json")))
        return ["KRAKEN:" + x["alt"] for x in J["pairs"] if not banned("KRAKEN:" + x["alt"])]
    except Exception: return []
UNIVERSE["crypto"] = CRYPTO_CORE + sorted(set(_kraken_all()) - set(CRYPTO_CORE))   # Brett 2026-10-28: every Kraken pair visible (data only; no pump-chasing)
FRED = ["DGS10", "DGS2", "T10Y2Y", "DCOILWTICO", "DEXUSAL", "FEDFUNDS", "CPIAUCSL"]
CIKS = {"XOM": 34088, "CVX": 93410, "JPM": 19617, "BAC": 70858, "JNJ": 200406, "UNH": 731766, "PG": 80424, "KO": 21344, "LLY": 59478, "WFC": 72971,
        "LEHMQ": 806085}   # Lehman: dead CIK kept to prove delisted filings retained
# 2026-10-29: bank peers already in pit.sqlite (merge) but outside UNIVERSE were never refreshed (stuck on 27 Oct). Data only; seat universes unchanged.
REFRESH_EXTRA = ["GS", "MS", "SCHW", "KRE", "KBE", "HSBC", "BCS", "HSBA.L", "BARC.L", "SUN.AX", "IAG.AX", "8766.T", "8411.T",
                 "AIG", "TRV", "CB", "EUFN", "MUFG", "SMFG", "MQG.AX", "QBE.AX", "8306.T", "8316.T", "BNP.PA", "GLE.PA", "DBK.DE", "SAN.MC", "ISP.MI", "UCG.MI", "INGA.AS", "FPH.AX", "ALD.AX", "2800.HK"]   # Victor 29 Oct: still on 27 Oct
def last_bar(c, sym):
    return c.execute("SELECT date,knowable_utc FROM bars WHERE sym=? ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
def expected_last_date(sym, now=None):
    """Latest local trading date whose bar should be knowable now (weekdays only; holidays tolerated by stale_days slack)."""
    now = now or dt.datetime.now(UTC); v = venue_of(sym); D = now.date() + dt.timedelta(days=1)
    for _ in range(10):
        D -= dt.timedelta(days=1)
        if v != "CRYPTO" and D.weekday() >= 5: continue
        if knowable(D.isoformat(), v, sym) <= now.isoformat(): return D.isoformat()
    return D.isoformat()
def is_stale(c, sym, now=None, slack_days=2):
    """True if the newest stored bar is older than the expected last session by more than slack_days weekdays.
    2026-10-29 infra: default slack 2 (was 1) so a Yahoo NaN/holiday gap (e.g. 2800.HK empty 28 Oct bar) does not perpetual-stale the name."""
    lb = last_bar(c, sym)
    if not lb: return True
    exp = dt.date.fromisoformat(expected_last_date(sym, now)); d = dt.date.fromisoformat(lb[0]); n = 0
    while d < exp:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 or venue_of(sym) == "CRYPTO": n += 1
    return n > slack_days - 1 if slack_days else n > 0
def _fetch_yahoo(sym, period):
    try: return sym, yahoo_rows(sym, period), None
    except Exception as e: return sym, None, str(e)[:120]
def refresh_symbols(syms, period="1mo", c=None, workers=8):
    """Top up any symbols (Yahoo primary -> chart endpoint -> IBKR gaps). Used by refresh_stale + gate_v2 on-demand freshness.
    2026-10-29: empty Yahoo (delisted / 404) recorded in fail so refresh_stale can skip them next pass; never raises to caller.
    Downloads run in parallel; bars are applied in the caller's symbol order so the stored rows match a serial ingest."""
    c = c or con(); ok, fail = {}, {}; todo = [s for s in syms if not s.startswith("KRAKEN:") and not banned(s)]
    got = {}
    if todo:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(min(max(int(workers or 1), 1), 8)) as ex:
            for sym, payload, err in ex.map(lambda s: _fetch_yahoo(s, period), todo):
                got[sym] = (payload, err)
    for s in todo:
        payload, err = got[s]
        if err: fail[s] = err; continue
        try:
            n = ingest_yahoo(c, s, period, fetched=payload)
            if n: ok[s] = n
            else: fail[s] = "yahoo empty (delisted or no data)"
        except Exception as e: fail[s] = str(e)[:120]
        c.commit()
    return ok, fail
def stale_universe(c):
    """Every equity/ETF/index symbol the seats and hunts use: seat universes + REFRESH_EXTRA + pit_staging europe_apac/us_ca index."""
    S = {x for L in UNIVERSE.values() for x in L if not x.startswith("KRAKEN:") and not x.endswith("=F")} | set(REFRESH_EXTRA)
    for rel in ("europe_apac/_work/universe.json", "us_ca/_work/universe.json", "us_ca/universe.json"):
        try:
            J = json.load(open(os.path.join(BASE, "data", "pit_staging", rel))); S |= {s for L in (J.values() if isinstance(J, dict) else []) for s in (L if isinstance(L, list) else [])}
        except Exception: pass
    return sorted(s for s in S if not banned(s))
def refresh_stale(period="1mo", limit=None):
    """2026-10-29 durable fix: tops up every stale symbol. Also covers us_ca staging; skips yahoo-empty fails until next UTC day."""
    c = con(); skip = set()
    try:
        prev = json.load(open(os.path.join(BASE, "data", "pit", "refresh_stale_report.json")))
        if (prev.get("utc") or "")[:10] == dt.datetime.now(UTC).date().isoformat():
            skip = {s for s, e in (prev.get("fail") or {}).items() if "empty" in str(e) or "404" in str(e) or "delisted" in str(e).lower()}
    except Exception: pass
    todo = [s for s in stale_universe(c) if s not in skip and is_stale(c, s, slack_days=0)]   # tfo_seatpipe_bars_20261030: top-up selects anything behind the expected last close (slack 2 left 28-Oct bars un-refreshed); stale-bar GUARD elsewhere keeps slack 2
    if limit: todo = todo[:int(limit)]
    ok, fail = refresh_symbols(todo, period, c)
    rep = dict(utc=dt.datetime.now(UTC).isoformat(), stale=len(todo), ok=len(ok), fail=fail, skipped_empty=len(skip))
    json.dump(rep, open(os.path.join(BASE, "data", "pit", "refresh_stale_report.json"), "w"), indent=1); return rep
def refresh(period="10y"):
    c = con(); rep = dict(ok={}, fail={}, started=dt.datetime.now(UTC).isoformat())
    for v, (f, w) in VENUE_BIAS.items(): c.execute("INSERT OR REPLACE INTO venues VALUES(?,?,?)", (v, f, w))
    syms = sorted({x for L in UNIVERSE.values() for x in L if not x.startswith("KRAKEN:")} | set(REFRESH_EXTRA))
    import concurrent.futures
    got = {}
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for sym, payload, err in ex.map(lambda s: _fetch_yahoo(s, period), syms): got[sym] = (payload, err)
    for s in syms:
        payload, err = got[s]
        try:
            if err: raise RuntimeError(err)
            rep["ok"][s] = ingest_yahoo(c, s, period, fetched=payload)
        except Exception as e: rep["fail"][s] = str(e)[:120]
        c.commit()
    for s in FRED:
        try: rep["ok"]["FRED:" + s] = ingest_fred(c, s)
        except Exception as e: rep["fail"]["FRED:" + s] = str(e)[:120]
    for k in CRYPTO_CORE:
        try: rep["ok"][k] = ingest_kraken(c, k.split(":")[1])
        except Exception as e: rep["fail"][k] = str(e)[:120]
    for s, k in CIKS.items():
        try: rep["ok"]["EDGAR:" + s] = ingest_edgar(c, s, k); time.sleep(0.2)
        except Exception as e: rep["fail"]["EDGAR:" + s] = str(e)[:120]
    c.execute("INSERT OR REPLACE INTO universe VALUES(?,?,?,?,?)", ("LEHMQ", "US", "delisted", "2008-09-17", "sec_edgar (filings only; no free price history)"))
    c.commit(); rep["finished"] = dt.datetime.now(UTC).isoformat(); rep["venue_bias"] = {k: v[0] for k, v in VENUE_BIAS.items()}
    json.dump(rep, open(os.path.join(BASE, "data", "pit", "refresh_report.json"), "w"), indent=1); return rep
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "stale":
    args = sys.argv[2:]; period = args[0] if args else "1mo"; limit = int(args[1]) if len(args) > 1 else None
    print(json.dumps(refresh_stale(period, limit))); sys.exit(0)
if __name__ == "__main__":
    r = refresh(sys.argv[1] if len(sys.argv) > 1 else "10y"); print("ok", len(r["ok"]), "fail", r["fail"])
