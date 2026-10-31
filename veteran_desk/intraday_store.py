"""Companion intraday store. PAPER only. tfo_paidrt_20261031.

pit.sqlite stays daily (it is already >10GB). Minute bars live here:

    data/intraday/bars.sqlite
    bars(sym, tf, ts, o, h, l, c, v, src)  PRIMARY KEY(sym, tf, ts)
    ts = bar-open epoch milliseconds UTC. tf is 1m, 5m, or 15m.

Retention (pruned on write, about once a minute):
    1m  10 days
    5m  40 days
    15m 120 days
One sqlite file, WAL, no monthly partitions. At ~200 names that is on the order of
tens of MB, not another pit. Raise retention only by editing RETAIN_DAYS.

Writers: tools/intraday_bars_pull.py (Massive 1m/5m/15m for US, EODHD intraday for
EU/LSE and other names data_layer already maps) and eodhd_eu_ws.py (live 1m candles).
Massive outranks a rolled bar, so a later REST pull does not lose the vendor OHLC.

Readers: recent(), summary(), for_symbols() — used by desk.pack, live_ctx, and vd_engine.feats.
Missing file returns empty. Reads are read-only and do not create the database.
"""
import datetime as dt, os, sqlite3, time
UTC = dt.timezone.utc
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "intraday", "bars.sqlite")
HIST = os.path.join(BASE, "data", "history", "history.sqlite")
TFS = {"1m": 60_000, "5m": 300_000, "15m": 900_000}
RETAIN_DAYS = {"1m": 10, "5m": 40, "15m": 120}
SRC_RANK = {"massive": 30, "eodhd_intraday": 20, "eodhd_eu_ws": 20, "history_import": 15, "eodhd_eu_ws_roll": 10, "eodhd_roll": 10}
_PRUNE_AT = [0.0]


def path():
    return os.environ.get("INTRADAY_DB") or DB


def _retain_ms(tf, now_ms):
    return now_ms - RETAIN_DAYS[tf] * 86400 * 1000


def connect(p=None):
    p = p or path(); os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=15000"); c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS bars(
        sym TEXT, tf TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, src TEXT,
        PRIMARY KEY(sym, tf, ts))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_bars_tf_ts ON bars(tf, ts)")
    return c


def _ro():
    p = path()
    if not os.path.exists(p): return None
    c = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def bucket(ts, tf):
    w = TFS[tf]; return (int(ts) // w) * w


def rollup(rows, tf):
    """rows (ts, o, h, l, c, v) -> OHLC buckets of tf. Same open timestamp keeps the latest close."""
    groups = {}
    for ts, o, h, l, c, v in sorted(rows, key=lambda r: r[0]):
        b = bucket(ts, tf); g = groups.get(b)
        if g is None: groups[b] = [b, o, h, l, c, float(v or 0)]
        else:
            g[2] = max(g[2], h); g[3] = min(g[3], l); g[4] = c; g[5] += float(v or 0)
    return [tuple(g) for g in groups.values()]


def prune(c, now_ms=None):
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000); n = 0
    for tf in TFS:
        cur = c.execute("DELETE FROM bars WHERE tf=? AND ts<?", (tf, _retain_ms(tf, now_ms)))
        n += cur.rowcount or 0
    return n


def put(rows, src, now_ms=None):
    """Upsert (sym, tf, ts, o, h, l, c, v). A higher SRC_RANK is not overwritten by a lower one."""
    rows = [r for r in rows if r and r[1] in TFS and r[3] and r[6]]
    if not rows: return 0
    rank = SRC_RANK.get(src, 1); c = connect(); n = 0
    if rank >= 30:
        c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(*r, src) for r in rows]); n = len(rows)
    else:
        for r in rows:
            prev = c.execute("SELECT src FROM bars WHERE sym=? AND tf=? AND ts=?", (r[0], r[1], r[2])).fetchone()
            if prev and SRC_RANK.get(prev[0], 1) > rank: continue
            c.execute("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", (*r, src)); n += 1
    if time.time() - _PRUNE_AT[0] > 60:
        prune(c, now_ms); _PRUNE_AT[0] = time.time()
    c.commit(); c.close(); return n


def roll_symbols(syms, src="eodhd_eu_ws_roll", since_ms=None):
    """Rebuild the open 5m/15m buckets from recent 1m bars (does not outrank Massive)."""
    syms = sorted(set(syms))
    if not syms or not os.path.exists(path()): return 0
    if since_ms is None: since_ms = int(time.time() * 1000) - 20 * 60 * 1000
    c = connect(); out = []
    for s in syms:
        raw = c.execute("SELECT ts,o,h,l,c,v FROM bars WHERE sym=? AND tf='1m' AND ts>=? ORDER BY ts", (s, int(since_ms))).fetchall()
        if not raw: continue
        for tf in ("5m", "15m"):
            for b, o, h, l, cl, v in rollup(raw, tf): out.append((s, tf, b, o, h, l, cl, v))
    c.close()
    return put(out, src)


def recent(sym, tf="5m", n=48, asof_ms=None):
    """Oldest-first OHLC dicts. asof_ms drops bars that open after that instant (replay guard)."""
    if tf not in TFS: return []
    c = _ro()
    if c is None: return []
    try:
        if asof_ms is None:
            q = c.execute("SELECT ts,o,h,l,c,v,src FROM bars WHERE sym=? AND tf=? ORDER BY ts DESC LIMIT ?", (sym, tf, int(n))).fetchall()
        else:
            q = c.execute("SELECT ts,o,h,l,c,v,src FROM bars WHERE sym=? AND tf=? AND ts<=? ORDER BY ts DESC LIMIT ?", (sym, tf, int(asof_ms), int(n))).fetchall()
    except sqlite3.Error: q = []
    finally:
        c.close()
    rows = [dict(ts=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5], src=r[6], tf=tf,
                 utc=dt.datetime.fromtimestamp(r[0] / 1000.0, UTC).isoformat()) for r in reversed(q)]
    if rows: return rows
    return _history(sym, tf, n, asof_ms)


def _history(sym, tf, n, asof_ms):
    """Read Massive minute rows already sitting in data/history/history.sqlite (res='minute'). 5m/15m are not stored there."""
    if tf != "1m" or not os.path.exists(HIST): return []
    try: c = sqlite3.connect("file:%s?mode=ro" % HIST, uri=True, timeout=10)
    except sqlite3.Error: return []
    try:
        if asof_ms is None:
            q = c.execute("SELECT ts,o,h,l,c,v,src FROM aggs WHERE sym=? AND res IN ('minute','1m') ORDER BY ts DESC LIMIT ?", (sym, int(n))).fetchall()
        else:
            q = c.execute("SELECT ts,o,h,l,c,v,src FROM aggs WHERE sym=? AND res IN ('minute','1m') AND ts<=? ORDER BY ts DESC LIMIT ?", (sym, int(asof_ms), int(n))).fetchall()
    except sqlite3.Error: q = []
    finally:
        c.close()
    return [dict(ts=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5], src=r[6] or "history", tf="1m",
                 utc=dt.datetime.fromtimestamp(r[0] / 1000.0, UTC).isoformat()) for r in reversed(q)]


def last_bar(sym, tf="5m"):
    rows = recent(sym, tf, 1)
    if not rows: return None
    r = rows[-1]
    return dict(tf=tf, ts=r["ts"], utc=r["utc"], c=r["c"], h=r["h"], l=r["l"], src=r["src"])


def summary(sym, n=12, asof_ms=None):
    """Seat-sized picture: prefer 5m, else 15m, else 1m."""
    for tf in ("5m", "15m", "1m"):
        rows = recent(sym, tf, n, asof_ms)
        if rows:
            return dict(tf=tf, n=len(rows), src=rows[-1]["src"], last=rows[-1]["c"], last_utc=rows[-1]["utc"],
                        hi=max(r["h"] for r in rows), lo=min(r["l"] for r in rows), bars=rows)
    return None


def for_symbols(syms, n=12, asof_ms=None):
    out = {}
    for s in syms:
        sm = summary(s, n, asof_ms)
        if sm: out[s] = sm
    return out


def attach(feature):
    """vd_engine.feats hook: one last intraday bar, or the feature unchanged."""
    if not isinstance(feature, dict) or not feature.get("sym"): return feature
    ib = last_bar(feature["sym"], "5m") or last_bar(feature["sym"], "15m") or last_bar(feature["sym"], "1m")
    if ib: feature["intraday"] = ib
    return feature


def health():
    p = path(); base = dict(path="data/intraday/bars.sqlite", present=os.path.exists(p), n_1m=0, n_5m=0, n_15m=0,
                            newest_utc=None, symbols=0, retain_days=dict(RETAIN_DAYS))
    if not base["present"]: return base
    try:
        c = _ro()
        if c is None: return base
        for tf, key in (("1m", "n_1m"), ("5m", "n_5m"), ("15m", "n_15m")):
            base[key] = c.execute("SELECT COUNT(*) FROM bars WHERE tf=?", (tf,)).fetchone()[0]
        ts = c.execute("SELECT MAX(ts) FROM bars").fetchone()[0]
        base["symbols"] = c.execute("SELECT COUNT(DISTINCT sym) FROM bars").fetchone()[0]
        c.close()
        if ts: base["newest_utc"] = dt.datetime.fromtimestamp(ts / 1000.0, UTC).isoformat()
    except Exception as e:
        base["error"] = type(e).__name__
    return base


def _massive_rows(sym, mult, span, get):
    if get is None:
        from veteran_desk import massive as M
        t = dt.datetime.now(UTC)
        path_ = "/v2/aggs/ticker/%s/range/%s/%s/%s/%s" % (sym, mult, span, (t - dt.timedelta(days=12)).date().isoformat(), t.date().isoformat())
        js = M._call("intraday", path_, dict(adjusted="true", sort="asc", limit=50000))
        return (js or {}).get("results") or []
    return get(sym, mult, span) or []


def _eodhd_rows(sym, interval, get):
    if get is None:
        from veteran_desk import data_layer as D
        return D.intraday(sym, interval)
    return get(sym, interval) or []


def _from_aggs(results, sym, tf):
    out = []
    for r in results or []:
        try: out.append((sym, tf, int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r.get("v") or 0)))
        except Exception: continue
    return out


def _from_eodhd(rows, sym, tf):
    out = []
    for r in rows or []:
        try: out.append((sym, tf, int(r[0]) * 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)))
        except Exception: continue
    return out


def pull_symbol(sym, massive_get=None, eodhd_get=None):
    """Fetch 1m/5m/15m for one symbol into the store. Returns rows written. No network when getters return []."""
    s = str(sym or "").upper()
    if not s or s.startswith("KRAKEN:") or s.endswith("=F") or s.endswith("=X") or s.startswith("^"): return 0
    n = 0
    us = "." not in s or s.rsplit(".", 1)[-1] in ("A", "B")
    if us:
        for tf, mult, span in (("1m", 1, "minute"), ("5m", 5, "minute"), ("15m", 15, "minute")):
            n += put(_from_aggs(_massive_rows(s, mult, span, massive_get), s, tf), "massive")
        return n
    m1 = _from_eodhd(_eodhd_rows(s, "1m", eodhd_get), s, "1m")
    m5 = _from_eodhd(_eodhd_rows(s, "5m", eodhd_get), s, "5m")
    n += put(m1, "eodhd_intraday"); n += put(m5, "eodhd_intraday")
    src_rows = [(r[2], r[3], r[4], r[5], r[6], r[7]) for r in (m5 or m1)]
    if src_rows:
        base_tf = "5m" if m5 else "1m"
        rolled = [(s, "15m", b, o, h, l, c, v) for b, o, h, l, c, v in rollup(src_rows, "15m")]
        if base_tf == "1m":
            rolled += [(s, "5m", b, o, h, l, c, v) for b, o, h, l, c, v in rollup(src_rows, "5m")]
        n += put(rolled, "eodhd_roll")
    return n


def import_history(now_ms=None):
    """Copy recent Massive minute/5m/15m rows already in history.sqlite. No-op when that file is absent."""
    if not os.path.exists(HIST): return 0
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    try: c = sqlite3.connect("file:%s?mode=ro" % HIST, uri=True, timeout=30)
    except sqlite3.Error: return 0
    n = 0
    try:
        for res, tf, days in (("minute", "1m", 10), ("1m", "1m", 10), ("5m", "5m", 40), ("15m", "15m", 120)):
            cut = now_ms - days * 86400 * 1000
            try: rows = c.execute("SELECT sym,ts,o,h,l,c,v FROM aggs WHERE res=? AND ts>=?", (res, cut)).fetchall()
            except sqlite3.Error: rows = []
            n += put([(r[0], tf, r[1], r[2], r[3], r[4], r[5], r[6] or 0) for r in rows if r[2] and r[5]], "history_import")
    finally:
        c.close()
    return n


def pull_universe(syms, massive_get=None, eodhd_get=None):
    n = 0
    for s in syms:
        try: n += pull_symbol(s, massive_get, eodhd_get)
        except Exception: continue
    return n
