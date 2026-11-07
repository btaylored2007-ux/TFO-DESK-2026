"""fxfeed_20261103 (PAPER, data only): droplet-native daily bars for FX spot (=X), futures (=F) and indices (^) -> data/pit/pit.sqlite.
WHY: since the Mac went offline (1-2 Nov 2026) nothing on the droplet loaded =X/=F/^ (pit_eod_sgp skips every '='/'^' symbol), so every
FX cross, future and index ended 2026-10-30 and the fx_commodities seat cleared nothing (Brett P0 2 Nov 20:59 WITA "FX IS NOT TRADING").
WHAT: Yahoo chart v8 (veteran_desk.pit.yahoo_chart -> chart_rows, listing-zone session dates) for
  * every canonical =X in pit + every OANDA-book FX cross the seat covers (vd_engine OANDA_FX ex OANDA_PEG, same orientation ->
    'EURUSD=X'); reversed duplicates (CADAUD=X vs AUDCAD=X, USDEUR=X ...) are neither scanned nor invented (canonical_fx);
  * every =F in pit + the seat / macro futures lists (fut_roll.ROLL roots only);
  * every ^ index in pit (+ DX-Y.NYB) so the macro seat's FUT/IDX market is fresh too.
  10y history when a symbol has < 60 bars (gate_v2 min_bars floor), else a 3-month top-up.
  =X / ^ : pit.put_bars (lookahead filter, hl flag review), src 'yahoo_chart_v8_sgp'. =X fallback ONLY when Yahoo returns nothing:
           OANDA practice D mid candles (UTC-aligned -> same session date as Yahoo's bar; complete weekday candles), src
           'oanda_practice_D_mid_sgp' (true closes - Yahoo's FX 'close' is the 00:00-London quote; the label says which).
           stooq is behind a JavaScript challenge on the droplet -> not used.
  =F     : raw rows -> bars_fut_raw (src 'yahoo_chart_v8_sgp'), then fut_roll.rebuild -> ratio back-adjusted bars (method unchanged).
           OANDA spot (XAU_USD, WTICO_USD ...) is NEVER written into =F rows.
GUARD: before writing, closes on dates we already hold (same source family) must agree: >= 60% of the last 15 overlapping closes within
0.01%. A shifted / mismatched series is skipped and reported as date_mismatch - history is never overwritten with mis-dated bars.
Writes state/thf/pit_fx_status.json (feedkeeper 'pit_fx_daily', dash health). Never touches ledgers, books, gates or the engine.
Run: python -m veteran_desk.pit_fx_sgp [--symbols A,B] [--period 10y|3mo] [--db PATH] [--dry-run] [--no-oanda]
systemd: tfo-thf-pit-fx.service/.timer (Mon-Fri 22:10 UTC after the NY close, Mon-Sat 00:40 UTC pre-Asia) then thf_scan."""
import argparse, datetime as dt, json, os, sys, time, urllib.error, urllib.request
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
L = P.BASE; ST = os.path.join(L, "state", "thf", "pit_fx_status.json")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8)); LON = ZoneInfo("Europe/London")
TAG = "fxfeed_20261103"; MIN_BARS = 60; FULL = "10y"; TOPUP = "3mo"
SRC_YAHOO = "yahoo_chart_v8_sgp"; SRC_OANDA = "oanda_practice_D_mid_sgp"
OANDA_HOST = "https://api-fxpractice.oanda.com"   # PRACTICE only (PAPER) - never api-fxtrade
# fxfeed_20261103 (helper 2): OANDA day = the London calendar day, DST-aware (candle opens 00:00 London) = Yahoo's own FX session date
# (exchangeTimezoneName Europe/London). Yahoo's FX daily CLOSE is that day's 00:00-London OPEN quote (measured 3 Nov: Yahoo close(D) ==
# OANDA open(D) within 5bp on 49-95% of 160+ days, vs 6-18% one day shifted) -> every Yahoo FX close is one session stale. OANDA candles
# carry the true close + tick volume, so they are PRIMARY for every OANDA-book pair (A36: "OANDA practice candles for FX"); Yahoo = fallback.
OANDA_ALIGN = "dailyAlignment=0&alignmentTimezone=Europe%2FLondon"
OANDA_FULL = 5000; OANDA_TOPUP = 60; SHIFT_TOL = 5e-4
OANDA_ENV = os.path.expanduser("~/.tfo_secrets/oanda.env")
PRIO = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]   # quote-convention priority when neither orientation is a book pair
IDX_EXTRA = ["DX-Y.NYB"]
# Built-in copy of vd_engine.OANDA_FX / OANDA_PEG (3 Nov 2026) - used only if the source parse below fails.
_OANDA_FX = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'USD_CHF', 'AUD_USD', 'NZD_USD', 'USD_CAD', 'EUR_JPY', 'GBP_JPY', 'AUD_JPY', 'NZD_JPY', 'CAD_JPY', 'CHF_JPY', 'EUR_GBP',
             'EUR_CHF', 'EUR_AUD', 'EUR_CAD', 'EUR_NZD', 'GBP_AUD', 'GBP_CAD', 'GBP_CHF', 'GBP_NZD', 'AUD_NZD', 'AUD_CAD', 'AUD_CHF', 'NZD_CAD', 'NZD_CHF', 'CAD_CHF',
             'EUR_NOK', 'EUR_SEK', 'USD_NOK', 'USD_SEK', 'USD_MXN', 'USD_ZAR', 'USD_SGD', 'USD_HKD', 'USD_TRY', 'USD_CNH', 'USD_PLN', 'EUR_PLN', 'USD_CZK', 'EUR_CZK', 'USD_HUF',
             'EUR_HUF', 'USD_DKK', 'EUR_DKK', 'USD_THB', 'SGD_JPY', 'EUR_TRY', 'ZAR_JPY', 'TRY_JPY', 'HKD_JPY', 'GBP_SGD', 'EUR_SGD', 'AUD_SGD', 'NZD_SGD', 'CAD_SGD', 'CHF_HKD',
             'GBP_HKD', 'AUD_HKD', 'EUR_HKD', 'CAD_HKD', 'NZD_HKD', 'SGD_CHF', 'GBP_PLN', 'GBP_ZAR']
_OANDA_PEG = {"USD_HKD", "USD_TRY", "USD_CNH", "EUR_TRY", "TRY_JPY", "HKD_JPY", "CHF_HKD", "GBP_HKD", "AUD_HKD", "EUR_HKD", "CAD_HKD", "NZD_HKD", "USD_DKK", "EUR_DKK"}
def now_utc(): return dt.datetime.now(UTC)
def wita(t=None): return (t or now_utc()).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")
_EL = []
def engine_lists():
    """vd_engine.OANDA_FX / OANDA_PEG read from the SOURCE (ast, no engine import: importing vd_engine makes dirs / wakes). Fallback: built-in copy.
    Parsed once per process (helper 2: was re-parsed for every symbol)."""
    if not _EL: _EL.append(_engine_lists())
    return list(_EL[0][0]), set(_EL[0][1])
def _engine_lists():
    try:
        import ast
        out = {}
        with open(os.path.join(L, "veteran_desk", "vd_engine.py")) as f: src = f.read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], "id", None) in ("OANDA_FX", "OANDA_PEG"):
                out[node.targets[0].id] = ast.literal_eval(node.value)
        if out.get("OANDA_FX") and out.get("OANDA_PEG") is not None: return list(out["OANDA_FX"]), set(out["OANDA_PEG"])
    except Exception: pass
    return list(_OANDA_FX), set(_OANDA_PEG)
def seat_fx_crosses():
    """Yahoo tickers of the OANDA-book FX crosses the fx_commodities seat covers (vd_engine orientation, pegs excluded)."""
    fx, peg = engine_lists()
    return [i.replace("_", "") + "=X" for i in fx if i not in peg]
def oanda_instrument(sym):
    """'EURUSD=X' -> 'EUR_USD' when that exact orientation is an OANDA instrument in vd_engine.OANDA_FX (pegs included: data only), else None.
    Reversed tickers (USDEUR=X) and pairs OANDA does not list (USDINR=X ...) stay on Yahoo."""
    if not (isinstance(sym, str) and sym.endswith("=X") and len(sym) == 8): return None
    i = sym[:3] + "_" + sym[3:6]
    return i if i in engine_lists()[0] else None
def expected_last_oanda(now):
    """Newest London weekday whose OANDA candle is knowable at `now` (oanda_knowable), for the fresh/behind count of OANDA-sourced FX."""
    D = now.astimezone(LON).date() + dt.timedelta(days=1)
    for _ in range(10):
        D -= dt.timedelta(days=1)
        if D.weekday() < 5 and oanda_knowable(D.isoformat()) <= now.isoformat(): return D.isoformat()
    return D.isoformat()
def oanda_knowable(date_str):
    """A London-day candle is complete at 00:00 London the next day; knowable 30 min later (UTC iso). Stricter than pit's generic FX
    21:30 UTC stamp, so a point-in-time read never sees a true close before the candle has closed."""
    D = dt.date.fromisoformat(date_str) + dt.timedelta(days=1)
    return (dt.datetime(D.year, D.month, D.day, 0, 30, tzinfo=LON)).astimezone(UTC).isoformat()
def _prio(s):
    a = s[:3]; return PRIO.index(a) if a in PRIO else 99
def canonical_fx(syms):
    """Drop reversed duplicates, never invent: a book pair keeps the book orientation (EURUSD=X, not USDEUR=X); the reverse of a book
    pair is dropped (CADAUD=X vs AUDCAD=X); when both orientations are present and neither is a book pair the PRIO base currency wins."""
    book = set(seat_fx_crosses()); S = set(syms); out = []
    for s in sorted(S):
        if not (s.endswith("=X") and len(s) == 8): out.append(s); continue
        rev = s[3:6] + s[:3] + "=X"
        if s in book: out.append(s); continue
        if rev in book: continue
        if rev in S and _prio(rev) < _prio(s): continue
        out.append(s)
    return out
def targets(c):
    """(fx, fut, idx) symbol lists for this run."""
    q = lambda like: {r[0] for r in c.execute("SELECT DISTINCT sym FROM bars WHERE sym LIKE ?", (like,))} | {r[0] for r in c.execute("SELECT sym FROM universe WHERE sym LIKE ?", (like,))}
    U = P.UNIVERSE
    fx = canonical_fx(q("%=X") | set(seat_fx_crosses()) | {s for s in U.get("fx_cmdty", []) if s.endswith("=X")})
    fut = q("%=F") | {s for g in ("fx_cmdty", "macro") for s in U.get(g, []) if s.endswith("=F")}
    idx = q("^%") | {s for s in U.get("macro", []) if s.startswith("^")} | set(IDX_EXTRA)
    try:
        from veteran_desk import thf_scan as TS
        fut |= set(TS.FUT_EXTRA) | {s for s in TS.MACRO_EXTRA if s.endswith("=F")}; idx |= {s for s in TS.MACRO_EXTRA if s.startswith("^")}
    except Exception: pass
    try:
        from veteran_desk import fut_roll as FR
        fut = {s for s in fut if s[:-2] in FR.ROLL}   # no roll calendar -> fut_roll.rebuild cannot adjust it; stays out (reported)
    except Exception: pass
    ban = lambda S: sorted(s for s in S if not P.banned(s))
    return ban(fx), ban(fut), ban(idx)
# ---------------- fetch ----------------
def fetch_yahoo(sym, period, tries=3, get=None):
    """(rows, err). 404 = unknown symbol (no retry); 429 = backoff; other errors retried."""
    err = None
    for a in range(tries):
        try: return (get or P.yahoo_chart)(sym, period), None
        except urllib.error.HTTPError as e:
            if e.code == 404: return [], "yahoo 404 (no such symbol)"
            err = "yahoo http %s" % e.code
            if e.code == 429: time.sleep(15 * (a + 1)); continue
        except Exception as e: err = "yahoo %s: %s" % (type(e).__name__, str(e)[:80])
        time.sleep(2 + 3 * a)
    return [], err
def _oanda_token():
    t = os.environ.get("OANDA_PRACTICE_TOKEN")
    if t: return t
    try:
        with open(OANDA_ENV) as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("OANDA_PRACTICE_TOKEN="): return ln.split("=", 1)[1].strip().strip('"').strip("'") or None
    except Exception: pass
    return None
def _oanda_get(u, tok):
    rq = urllib.request.Request(u, headers={"Authorization": "Bearer " + tok, "Accept-Datetime-Format": "RFC3339"})
    return json.loads(urllib.request.urlopen(rq, timeout=30).read())
def fetch_oanda_fx(sym, count=2000, get=None, tok=None):
    """=X spot from OANDA practice daily MID candles aligned to 00:00 LONDON (DST-aware; = Yahoo's FX session date), labelled with the
    candle's London date; complete weekday candles only (the Sunday-evening reopen stub has no Yahoo counterpart). PRIMARY source for
    OANDA-book pairs (true close + tick volume), see refresh_fx_oanda. Returns (rows, src | error). Never used for =F (back-adjusted
    futures) - the token is never logged."""
    if not (sym.endswith("=X") and len(sym) == 8): return [], "not a 6-letter pair"
    tok = tok or _oanda_token()
    if not tok: return [], "no OANDA practice token"
    u = "%s/v3/instruments/%s_%s/candles?granularity=D&count=%d&price=M&%s" % (OANDA_HOST, sym[:3], sym[3:6], min(int(count), 5000), OANDA_ALIGN)
    try: J = (get or _oanda_get)(u, tok)
    except urllib.error.HTTPError as e: return [], "oanda http %s" % e.code
    except Exception as e: return [], "oanda %s" % type(e).__name__
    rows = []
    for x in J.get("candles") or []:
        if not x.get("complete"): continue
        try:
            t = dt.datetime.fromisoformat(str(x.get("time", ""))[:19] + "+00:00").astimezone(LON)
            if t.weekday() >= 5: continue
        except Exception: continue
        m = x.get("mid") or {}; rows.append((t.date().isoformat(), float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]), float(x.get("volume") or 0)))
    return rows, (SRC_OANDA if rows else "oanda returned no complete candles")
def fill_fx_tickvol(c, syms, days=12, get=None, tok=None):
    """FX has no central tape: Yahoo =X bars carry v=0 by nature. Like the Mac's fx_tickvol_fill, store OANDA practice TICK volume
    (count of price updates, NOT notional) for the newest bars: table fx_tickvol + bars.v where v=0, src suffixed '+oanda_tickvol'
    (same unit/label as the Mac-era history). Pairs OANDA lacks keep v=0 and are listed as no_volume. Never touches o/h/l/c."""
    out = dict(filled={}, no_volume={}, bars_updated=0)
    tok = tok or _oanda_token()
    if not tok: out["error"] = "no OANDA practice token"; return out
    c.execute("CREATE TABLE IF NOT EXISTS fx_tickvol(sym TEXT,date TEXT,tick_vol REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(sym,date))")
    for s in syms:
        rows, src = fetch_oanda_fx(s, count=days, get=get, tok=tok)
        if not rows: out["no_volume"][s] = src; continue
        c.executemany("INSERT OR REPLACE INTO fx_tickvol VALUES(?,?,?,?,?)", [(s, r[0], r[5], P.knowable(r[0], "FX"), "oanda_practice_D_mid_tickvol") for r in rows])
        n = 0
        for r in rows:
            if r[5] > 0: n += c.execute("UPDATE bars SET v=?, src=src||'+oanda_tickvol' WHERE sym=? AND date=? AND (v IS NULL OR v=0) AND src NOT LIKE '%oanda%'", (r[5], s, r[0])).rowcount
        out["filled"][s] = n; out["bars_updated"] += n; c.commit(); time.sleep(0.1)
    return out
DATA_NOTE = ("FX =X bars for every OANDA-book pair are OANDA practice daily MID candles (London day 00:00-24:00, true close; v = OANDA "
             "tick volume = count of price updates, compare only within FX), src 'oanda_practice_D_mid_sgp'. Pairs OANDA does not list "
             "(USDINR, USDKRW, USDBRL ...) stay Yahoo spot: volume 0 by nature and Yahoo's FX daily close is that day's 00:00-London "
             "quote (close ~ open). Futures =F volume on the latest completed session may repeat Yahoo's previous value until Yahoo "
             "updates it (re-pulled 22:10 / 00:40 UTC). fxfeed_20261103.")
# ---------------- guard + write ----------------
def overlap_ok(existing, rows, n=15, tol=1e-4, min_frac=0.6):
    """existing: {date: close} already stored (same source family). Closes on the last n common dates must agree (within tol) on at
    least min_frac of them: a series dated one day off (the old fixed-offset bug) or a different source family fails here."""
    com = [r for r in rows if r[0] in existing][-n:]
    if not com: return True, dict(common=0, agree=0)
    good = sum(1 for r in com if existing[r[0]] and abs(r[4] / existing[r[0]] - 1) <= tol)
    return good >= max(1, int(round(min_frac * len(com)))), dict(common=len(com), agree=good)
def _existing(c, table, sym, family):
    return {d: cl for d, cl, src in c.execute("SELECT date,c,src FROM %s WHERE sym=? ORDER BY date DESC LIMIT 40" % table, (sym,)) if str(src or "").startswith(family)}
def shift_guard(stored, orows, n=60, tol=SHIFT_TOL):
    """Date agreement before a Yahoo -> OANDA source switch. stored: {date: close} of the stored (Yahoo) FX series, whose close is the
    00:00-London quote = the OPEN of the OANDA London-day candle of the SAME date. On the last n common dates the zero-shift match rate
    must be >= 40% AND beat the one-day-shifted match rates by >= 20 points; a series dated a day off fails and nothing is written."""
    od = {r[0]: r[1] for r in orows}; ds = sorted(set(od) & set(stored))[-n:]
    if len(ds) < 10: return True, dict(common=len(ds), note="too few common dates to compare")
    sd = sorted(stored); ix = {d: i for i, d in enumerate(sd)}
    def frac(k):
        pairs = [(od[d], stored[sd[ix[d] + k]]) for d in ds if 0 <= ix[d] + k < len(sd) and stored[sd[ix[d] + k]]]
        return sum(1 for a, b in pairs if abs(a / b - 1) <= tol) / float(max(1, len(pairs)))
    f0, fp, fm = frac(0), frac(1), frac(-1)
    return (f0 >= 0.4 and f0 >= max(fp, fm) + 0.2), dict(common=len(ds), match0=round(f0, 2), match_next=round(fp, 2), match_prev=round(fm, 2))
def _changed_rows(c, sym, rows, tol=1e-7):
    """Only rows that are new or whose o/h/l/c (or a real non-zero volume) differ from the stored row. helper 2: re-writing identical
    rows with Yahoo's v=0 wiped the OANDA tick volume of the Mac era (relvol 0.0 on every FX name, 3 Nov 15:20 run)."""
    if not rows: return []
    st = {d: (o, h, l, cl, v) for d, o, h, l, cl, v in c.execute("SELECT date,o,h,l,c,v FROM bars WHERE sym=? AND date>=?", (sym, min(r[0] for r in rows)))}
    out = []
    for r in rows:
        s = st.get(r[0])
        if s is None or any(abs((a or 0) - (b or 0)) > tol * max(1.0, abs(b or 0)) for a, b in zip(r[1:5], s[:4])) or ((r[5] or 0) > 0 and abs((r[5] or 0) - (s[4] or 0)) > 0.5):
            out.append(r)
    return out
def restore_tickvol(c, sym):
    """Local, no network: bars.v=0 FX rows get the OANDA tick volume already stored in fx_tickvol (Mac-era fill + this loader), src
    suffixed '+oanda_tickvol' (same unit/label as before). OANDA-sourced rows carry their own volume and are not touched."""
    try:
        n = c.execute("UPDATE bars SET v=(SELECT t.tick_vol FROM fx_tickvol t WHERE t.sym=bars.sym AND t.date=bars.date), src=src||'+oanda_tickvol' "
                      "WHERE sym=? AND (v IS NULL OR v=0) AND src NOT LIKE '%oanda%' AND EXISTS(SELECT 1 FROM fx_tickvol t WHERE t.sym=bars.sym AND t.date=bars.date AND t.tick_vol>0)", (sym,)).rowcount
        c.commit(); return n
    except Exception: return 0
def _put_oanda(c, sym, rows, now):
    """P.put_bars for OANDA London-day FX candles with the candle's own knowable time (oanda_knowable) + fx_tickvol upsert."""
    if P.banned(sym): raise ValueError("Indonesian asset banned: " + sym)
    iso = now.isoformat(); rows = [r for r in rows if oanda_knowable(r[0]) <= iso]
    n0 = len(P.HL_REAPPLIED); rows = P.sane_rows(c, sym, rows, SRC_OANDA)
    fixed = set(d for s_, d in P.HL_REAPPLIED[n0:] if s_ == sym)
    c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(sym, r[0], *r[1:6], oanda_knowable(r[0]), SRC_OANDA + ("|hlreview_fix" if r[0] in fixed else "")) for r in rows])
    c.execute("INSERT OR IGNORE INTO universe VALUES(?,?,?,?,?)", (sym, "FX", "listed", None, SRC_OANDA))
    c.execute("CREATE TABLE IF NOT EXISTS fx_tickvol(sym TEXT,date TEXT,tick_vol REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(sym,date))")
    c.executemany("INSERT OR REPLACE INTO fx_tickvol VALUES(?,?,?,?,?)", [(sym, r[0], r[5], oanda_knowable(r[0]), "oanda_practice_D_mid_tickvol") for r in rows if r[5]])
    return len(rows)
def load_fx_idx(c, sym, rows, src, now):
    v = P.venue_of(sym); iso = now.isoformat()
    rows = [r for r in rows if P.knowable(r[0], v, sym) <= iso]   # no unfinished session (put_bars filters too; counted here)
    b = c.execute("SELECT COUNT(*), MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()
    w = _changed_rows(c, sym, rows)   # helper 2: identical stored rows are left alone (their v / src survive)
    if w: P.put_bars(c, sym, w, src)
    c.commit(); tv = restore_tickvol(c, sym) if v == "FX" else 0
    a = c.execute("SELECT COUNT(*), MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()
    return dict(rows=len(rows), written=len(w), new=a[0] - b[0], last=a[1], tickvol_restored=tv)
def load_fut(c, sym, rows, src, now):
    from veteran_desk import fut_roll as FR
    if sym[:-2] not in FR.ROLL: return dict(error="no roll calendar for " + sym)
    FR.ensure(c); FR.seed_raw_from_bars(c, sym); iso = now.isoformat()
    rows = [r for r in rows if P.knowable(r[0], "FUT") <= iso]
    prev = {d: (cl, v) for d, cl, v in c.execute("SELECT date,c,v FROM bars_fut_raw WHERE sym=? ORDER BY date DESC LIMIT 40", (sym,))}
    changed = (not prev) or any(r[0] not in prev or abs(prev[r[0]][0] - r[4]) > 1e-9 * max(1.0, abs(r[4])) or (prev[r[0]][1] or 0) != (r[5] or 0) for r in rows[-10:])
    b = c.execute("SELECT COUNT(*) FROM bars_fut_raw WHERE sym=?", (sym,)).fetchone()[0]
    c.executemany("INSERT OR REPLACE INTO bars_fut_raw VALUES(?,?,?,?,?,?,?,?,?)", [(sym, r[0], *r[1:6], P.knowable(r[0], "FUT"), src) for r in rows])
    c.execute("INSERT OR IGNORE INTO universe VALUES(?,?,?,?,?)", (sym, "FUT", "listed", None, src))
    raw_max = c.execute("SELECT MAX(date) FROM bars_fut_raw WHERE sym=?", (sym,)).fetchone()[0]
    bars_max = c.execute("SELECT MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()[0]
    # back-adjusted bars rebuilt from raw when raw changed OR the adjusted table lags raw (an interrupted earlier rebuild: fut_roll.ensure's
    # executescript commits the raw insert before the rebuild). Rolls need Yahoo contract chains, so an unchanged symbol is not rebuilt.
    reb = FR.rebuild(c, sym) if (changed or (bars_max or "") < (raw_max or "")) else None
    c.commit()
    a = c.execute("SELECT COUNT(*), MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()
    return dict(rows=len(rows), new=c.execute("SELECT COUNT(*) FROM bars_fut_raw WHERE sym=?", (sym,)).fetchone()[0] - b, last=a[1], rebuilt=bool(reb),
                rolls=(reb or {}).get("rolls"), exact_from=(reb or {}).get("exact_from"))
def refresh_fx_oanda(c, sym, now, period=None, dry=False):
    """OANDA-book FX pair: OANDA practice London-day candles are the primary bars. First run per pair (< MIN_BARS OANDA rows stored) =
    source SWITCH: up to 5000 candles (~19y) replace the Yahoo rows on the same dates after shift_guard proves the dates agree (older
    Yahoo-only history is kept). Later runs: 60-candle top-up checked by overlap_ok against the stored OANDA closes. Only new/changed
    rows are written. Returns the status row (status ok | dry_run | date_mismatch | failed)."""
    have = c.execute("SELECT COUNT(*) FROM bars WHERE sym=?", (sym,)).fetchone()[0]
    have_o = c.execute("SELECT COUNT(*) FROM bars WHERE sym=? AND src LIKE 'oanda%'", (sym,)).fetchone()[0]
    switch = have_o < MIN_BARS; cnt = OANDA_FULL if (switch or period == FULL) else OANDA_TOPUP
    r = dict(kind="fx", period="oanda_D_%d" % cnt, had=have, had_oanda=have_o, status="ok", src=SRC_OANDA, instrument=oanda_instrument(sym))
    rows, o = fetch_oanda_fx(sym, count=cnt)
    if not rows: r.update(status="failed", error=o); return r
    if switch:
        stored = {d: cl for d, cl in c.execute("SELECT date,c FROM bars WHERE sym=? AND date>=? AND src NOT LIKE 'oanda%'", (sym, rows[0][0]))}
        ok, stats = shift_guard(stored, rows); r.update(switch=True, guard=stats)
    else:
        ok, stats = overlap_ok(_existing(c, "bars", sym, "oanda"), rows); r["overlap"] = stats
    if not ok:
        r.update(status="date_mismatch", error="OANDA candles disagree with the stored dates (%s) - not written" % json.dumps(stats)); return r
    if dry: r.update(status="dry_run", fetched=len(rows), first_fetched=rows[0][0], newest_fetched=rows[-1][0]); return r
    try:
        b = c.execute("SELECT COUNT(*) FROM bars WHERE sym=?", (sym,)).fetchone()[0]
        w = _changed_rows(c, sym, rows); n = _put_oanda(c, sym, w, now) if w else 0; c.commit()
        a = c.execute("SELECT COUNT(*), MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()
        r.update(rows=len(rows), written=n, new=a[0] - b, last=a[1], first_oanda=rows[0][0])
    except Exception as e:
        c.rollback(); r.update(status="failed", error="write %s: %s" % (type(e).__name__, str(e)[:100]))
    return r
def refresh_one(c, sym, kind, now, period=None, dry=False, oanda=True, get=None):
    """One symbol end to end. kind: fx | fut | idx. Returns the status row."""
    table = "bars_fut_raw" if kind == "fut" else "bars"
    if kind == "fut":
        from veteran_desk import fut_roll as FR; FR.ensure(c)
    oerr = None
    if kind == "fx" and oanda and oanda_instrument(sym):   # helper 2: OANDA primary for OANDA-book pairs (true close + tick volume)
        ro = refresh_fx_oanda(c, sym, now, period, dry)
        if ro["status"] in ("ok", "dry_run"): return ro
        oerr = "%s: %s" % (ro["status"], ro.get("error"))   # OANDA failed / disagreed -> Yahoo keeps the pair fresh (labelled)
    have = c.execute("SELECT COUNT(*) FROM %s WHERE sym=?" % table, (sym,)).fetchone()[0]
    per = period or (FULL if have < MIN_BARS else TOPUP)
    r = dict(kind=kind, period=per, had=have, status="ok", src=SRC_YAHOO)
    if oerr: r["oanda_error"] = oerr
    rows, err = fetch_yahoo(sym, per, get=get)
    if not rows and kind == "fx" and oanda and not oerr:
        rows, o = fetch_oanda_fx(sym, count=2000 if have < MIN_BARS else 90)
        if rows: r["src"] = o; r["yahoo_error"] = err
        else: err = "%s; %s" % (err, o)
    if rows and kind == "fx" and r["src"].startswith("yahoo") and c.execute("SELECT 1 FROM bars WHERE sym=? AND src LIKE 'oanda%' LIMIT 1", (sym,)).fetchone():
        last = c.execute("SELECT MAX(date) FROM bars WHERE sym=?", (sym,)).fetchone()[0] or ""
        rows = [x for x in rows if x[0] > last]; r["fallback_new_dates_only"] = True   # never overwrite OANDA rows with Yahoo's 00:00-quote closes
        if not rows: r.update(status="ok", rows=0, new=0, last=last or None); return r
    if not rows: r.update(status="failed", error=err or "empty"); return r
    ex = _existing(c, table, sym, "yahoo" if r["src"].startswith("yahoo") else "oanda")
    ok, stats = overlap_ok(ex, rows); r["overlap"] = stats
    if not ok: r.update(status="date_mismatch", error="fetched closes disagree with stored rows on %d of %d common dates - not written" % (stats["common"] - stats["agree"], stats["common"])); return r
    if dry: r.update(status="dry_run", fetched=len(rows), newest_fetched=rows[-1][0]); return r
    try: r.update(load_fut(c, sym, rows, r["src"], now) if kind == "fut" else load_fx_idx(c, sym, rows, r["src"], now))
    except Exception as e:
        c.rollback(); r.update(status="failed", error="write %s: %s" % (type(e).__name__, str(e)[:100]))
    if r.get("error") and r["status"] == "ok": r["status"] = "failed"
    return r
def run(db=None, symbols=None, period=None, dry=False, oanda=True, log=print, pause=0.25, get=None, status_path=None):
    c = P.con(db); now = now_utc(); fx, fut, idx = targets(c)
    if symbols:
        sel = set(symbols); fx, fut, idx = [s for s in fx if s in sel], [s for s in fut if s in sel], [s for s in idx if s in sel]
        for s in sel - set(fx) - set(fut) - set(idx):   # explicit symbol outside the computed lists: route by venue
            k = {"FX": "fx", "FUT": "fut", "IDX": "idx"}.get(P.venue_of(s)); (fx if k == "fx" else fut if k == "fut" else idx).append(s) if k else None
    st = dict(paper_only=True, tag=TAG, run_utc=now.isoformat(timespec="seconds"), run_wita=wita(now), dry_run=dry, by_sym={},
              note="=X: OANDA practice London-day candles PRIMARY for OANDA-book pairs (true close + tick volume; shift_guard before the "
                   "Yahoo->OANDA switch), Yahoo chart v8 (listing-zone dates) for the rest and as labelled fallback (new dates only); =F raw "
                   "Yahoo -> fut_roll ratio back-adjust; ^ Yahoo. Guard: stored closes must agree before any write; identical rows are "
                   "never re-written. Reversed FX tickers are not scanned or invented.")
    for kind, syms in (("fx", fx), ("fut", fut), ("idx", idx)):
        g = dict(n=len(syms), ok=0, fresh=0, failed=0, mismatch=0, newest=None, expected=None, symbols=syms)
        for s in syms:
            t0 = time.time(); r = refresh_one(c, s, kind, now, period, dry, oanda, get=get); r["secs"] = round(time.time() - t0, 1)
            if r["status"] in ("ok", "dry_run"):
                g["ok"] += 1; last = r.get("last") or r.get("newest_fetched")
                if last:
                    g["newest"] = max(g["newest"] or "", last)
                    exp = expected_last_oanda(now) if str(r.get("src", "")).startswith("oanda") else P.expected_last_date(s, now); g["expected"] = max(g["expected"] or "", exp)
                    if last >= exp: g["fresh"] += 1
                    else: r["behind"] = exp
            elif r["status"] == "date_mismatch": g["mismatch"] += 1; g["failed"] += 1
            else: g["failed"] += 1
            st["by_sym"][s] = r; log("%s %s %s %s" % (kind, s, r["status"], json.dumps({k: v for k, v in r.items() if k not in ("kind", "status")}, default=str)))
            time.sleep(pause)
        st[kind] = g
    st["fx_crosses_added"] = [s for s, r in st["by_sym"].items() if r.get("kind") == "fx" and r.get("had", 1) == 0 and r.get("status") == "ok"]
    st["data_note"] = DATA_NOTE
    FXR = {s: st["by_sym"][s] for s in fx if s in st["by_sym"]}   # helper 2: which source each FX pair is on now
    st["fx_source"] = dict(oanda=sorted(s for s, r in FXR.items() if r.get("status") in ("ok", "dry_run") and str(r.get("src")).startswith("oanda")),
                           yahoo_not_on_oanda=sorted(s for s, r in FXR.items() if r.get("status") in ("ok", "dry_run") and not oanda_instrument(s)),
                           yahoo_fallback=sorted(s for s, r in FXR.items() if r.get("oanda_error")),
                           switched_this_run=sorted(s for s, r in FXR.items() if r.get("switch") and r.get("status") == "ok"))
    if oanda and not dry and fx:   # OANDA tick volume by network only for OANDA-book pairs that had to fall back to Yahoo this run
        try: st["fx_tickvol"] = fill_fx_tickvol(c, [s for s in st["fx_source"]["yahoo_fallback"] if FXR[s]["status"] == "ok"], days=60)   # 60 sessions: the whole 20-day relvol window has volume (12 left zeros inside it -> relvol 2.4-2.6 on PLN/CZK/HUF, 3 Nov)
        except Exception as e: st["fx_tickvol"] = dict(error="%s: %s" % (type(e).__name__, str(e)[:100]))
        log("fx_tickvol %s" % json.dumps({k: (v if k != "filled" else len(v)) for k, v in st["fx_tickvol"].items()}, default=str)[:600])
    st["finished_utc"] = now_utc().isoformat(timespec="seconds"); st["secs"] = round((now_utc() - now).total_seconds(), 1)
    if not dry:
        sp = status_path or ST; tmp = sp + ".tmp"
        with open(tmp, "w") as f: json.dump(st, f, indent=1, default=str)
        os.replace(tmp, sp)
    c.close(); return st
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0]); ap.add_argument("--symbols", default="")
    ap.add_argument("--period", default=None, help="10y | 3mo | ... (default: 10y when < 60 bars stored, else 3mo)")
    ap.add_argument("--db", default=None); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--no-oanda", action="store_true")
    a = ap.parse_args(argv)
    st = run(db=a.db, symbols=[s for s in a.symbols.split(",") if s] or None, period=a.period, dry=a.dry_run, oanda=not a.no_oanda)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "symbols"} for k, v in st.items() if k in ("fx", "fut", "idx")} | dict(secs=st["secs"], added=st["fx_crosses_added"]), indent=1))
    return 0 if all((st.get(k) or {}).get("ok") for k in ("fx", "fut") if (st.get(k) or {}).get("n")) else 1
if __name__ == "__main__": sys.exit(main())
