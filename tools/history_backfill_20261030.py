"""2026-10-30 22:25 WITA (TFO via parent; Brett paid feeds primary): FULL historical backfill from PAID feeds into a separate indexed store
data/history/history.sqlite (pit.sqlite untouched). PAPER research data only. Resumable (progress table), throttled per feed, pauses in
desk tick minutes, keys never printed/logged (redacted). Phases: eodhd (daily max history + splits/divs), massive (US hour aggs max,
minute aggs for core list), fmp (fundamentals, earnings, estimates, insider), kraken (daily OHLC), coinalyze (daily funding/OI/liq/LS).
Usage: python3 tools/history_backfill_20261030.py [phase ...]   (default: all, in that order). Progress: logs/history_backfill.log"""
import datetime as dt, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request, urllib.error
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
DB = os.path.join(BASE, "data", "history", "history.sqlite"); LOG = os.path.join(BASE, "logs", "history_backfill.log")
REP = os.path.join(BASE, "data", "history", "history_report.json"); UTC = dt.timezone.utc
RATE = dict(eodhd=0.25, massive=0.25, fmp=0.3, kraken=1.2, coinalyze=1.6)   # seconds between calls (well under plan limits)
_last = {}
def key(n):
    for ln in open(os.path.expanduser("~/.tfo_secrets/%s.env" % n)):
        if "=" in ln: return ln.split("=", 1)[1].strip().strip('"\'')
def red(s): return re.sub(r"(api_token|apikey|apiKey|api_key)=[^&\s]+", r"\1=***", str(s))
def log(*a):
    open(LOG, "a").write(dt.datetime.now().strftime("%F %T WITA ") + red(" ".join(str(x) for x in a)) + "\n")
def tickwin(): return dt.datetime.now().minute % 15 <= 2
def get(feed, url, headers=None, tries=3):
    for k in range(tries):
        while tickwin(): time.sleep(10)
        w = RATE[feed] - (time.time() - _last.get(feed, 0))
        if w > 0: time.sleep(w)
        _last[feed] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper history"}, **(headers or {}))), timeout=60) as f: return json.loads(f.read())
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(30 * (k + 1)); continue
            if e.code in (401, 403): raise RuntimeError("%s HTTP %s (plan/entitlement)" % (feed, e.code))
            if e.code == 404: return None
            time.sleep(3)
        except Exception as e: log("net", feed, type(e).__name__, red(str(e))[:120]); time.sleep(5)
    return None
def con():
    os.makedirs(os.path.dirname(DB), exist_ok=True); c = sqlite3.connect(DB, timeout=300); c.execute("PRAGMA busy_timeout=300000"); c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS eod(sym TEXT, date TEXT, o REAL, h REAL, l REAL, c REAL, adj_c REAL, v REAL, src TEXT, PRIMARY KEY(sym, date));
    CREATE TABLE IF NOT EXISTS corp_actions(sym TEXT, date TEXT, kind TEXT, value TEXT, src TEXT, PRIMARY KEY(sym, date, kind));
    CREATE TABLE IF NOT EXISTS aggs(sym TEXT, res TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, vw REAL, n INTEGER, src TEXT, PRIMARY KEY(sym, res, ts));
    CREATE TABLE IF NOT EXISTS fundamentals(sym TEXT, kind TEXT, period TEXT, data TEXT, src TEXT, PRIMARY KEY(sym, kind, period));
    CREATE TABLE IF NOT EXISTS crypto_ohlc(pair TEXT, res TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, vw REAL, v REAL, n INTEGER, src TEXT, PRIMARY KEY(pair, res, ts));
    CREATE TABLE IF NOT EXISTS derivs(sym TEXT, kind TEXT, ts INTEGER, value REAL, extra TEXT, src TEXT, PRIMARY KEY(sym, kind, ts));
    CREATE TABLE IF NOT EXISTS progress(phase TEXT, item TEXT, status TEXT, rows INTEGER, utc TEXT, PRIMARY KEY(phase, item));
    CREATE INDEX IF NOT EXISTS eod_date ON eod(date); CREATE INDEX IF NOT EXISTS aggs_ts ON aggs(ts); CREATE INDEX IF NOT EXISTS derivs_ts ON derivs(ts);""")
    return c
def done(c, ph, it): return c.execute("SELECT 1 FROM progress WHERE phase=? AND item=? AND status IN ('ok','empty')", (ph, it)).fetchone()
def mark(c, ph, it, st, n=0): c.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?)", (ph, it, st, n, dt.datetime.now(UTC).isoformat())); c.commit()
def universes():
    from veteran_desk import pit as P, data_layer as D
    pc = P.con(); full = sorted({r[0] for r in pc.execute("SELECT DISTINCT sym FROM universe WHERE status='listed'")}); desk = set(P.stale_universe(pc))
    try:
        J = json.load(open(os.path.join(BASE, "data", "pit", "scan_universe.json"))); U = J.get("universe"); desk |= set(U if isinstance(U, list) else (U or {}).keys())
    except Exception: pass
    for f in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
        try:
            for B in (json.load(open(os.path.join(BASE, f))).get("seats") or {}).values():
                desk |= {p.get("ticker") for p in list((B.get("positions") or {}).values()) + list(B.get("closed") or []) if p.get("ticker")}
        except Exception: pass
    desk = sorted(s for s in desk if s); pc.close()
    return full, desk, D.eodhd_symbol
def ph_eodhd(c, full, desk, emap):
    k = key("eodhd"); dset = set(desk)
    order = desk + [s for s in full if s not in dset]
    for i, s in enumerate(order):
        code = emap(s)
        if not code or done(c, "eodhd", s): continue
        try:
            rows = get("eodhd", "https://eodhd.com/api/eod/%s?%s" % (urllib.parse.quote(code), urllib.parse.urlencode(dict(api_token=k, fmt="json", **{"from": "1900-01-01"})))) or []
            c.executemany("INSERT OR REPLACE INTO eod VALUES(?,?,?,?,?,?,?,?,?)", [(s, r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("adjusted_close"), r.get("volume"), "eodhd") for r in rows if isinstance(r, dict)])
            if s in dset and not s.endswith("=X") and not s.startswith("^"):
                for kind, ep in (("split", "splits"), ("div", "div")):
                    a = get("eodhd", "https://eodhd.com/api/%s/%s?%s" % (ep, urllib.parse.quote(code), urllib.parse.urlencode(dict(api_token=k, fmt="json", **{"from": "1900-01-01"})))) or []
                    c.executemany("INSERT OR REPLACE INTO corp_actions VALUES(?,?,?,?,?)", [(s, r.get("date"), kind, json.dumps(r), "eodhd") for r in a if isinstance(r, dict)])
            mark(c, "eodhd", s, "ok" if rows else "empty", len(rows))
        except RuntimeError as e: log("eodhd STOP", e); return
        except Exception as e: mark(c, "eodhd", s, "err:" + type(e).__name__)
        if i % 250 == 0: log("eodhd", i, "/", len(order), s)
def ph_massive(c, full, desk, emap):
    k = key("massive"); us = [s for s in desk if re.fullmatch(r"[A-Z][A-Z.]{0,5}", s or "") and "." not in s]
    core = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLE", "XLF", "XLK", "SMH", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "JPM"]
    held = [s for s in us if s not in core][:0]
    jobs = [(s, "hour") for s in us] + [(s, "minute") for s in core + held]
    for s, res in jobs:
        it = "%s|%s" % (s, res)
        if done(c, "massive", it): continue
        n, to = 0, dt.date.today(); span = 3650 if res == "hour" else 365
        try:
            while True:   # walk back in windows until the plan returns nothing
                fr = to - dt.timedelta(days=span)
                url = "https://api.massive.com/v2/aggs/ticker/%s/range/1/%s/%s/%s?adjusted=true&sort=asc&limit=50000&apiKey=%s" % (s, res, fr, to, k); got = 0
                while url:
                    j = get("massive", url) or {}
                    R = j.get("results") or []
                    c.executemany("INSERT OR REPLACE INTO aggs VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(s, res, r["t"], r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v"), r.get("vw"), r.get("n"), "massive") for r in R])
                    got += len(R); nu = j.get("next_url"); url = (nu + "&apiKey=" + k) if nu else None
                c.commit(); n += got
                if not got or fr.year < 2000: break
                to = fr - dt.timedelta(days=1)
            mark(c, "massive", it, "ok" if n else "empty", n)
        except RuntimeError as e: log("massive STOP", e); mark(c, "massive", it, "err:entitlement"); return
        except Exception as e: mark(c, "massive", it, "err:" + type(e).__name__)
        log("massive", it, n)
def ph_fmp(c, full, desk, emap):
    k = key("fmp"); us = [s for s in desk if re.fullmatch(r"[A-Z][A-Z]{0,4}", s or "")]
    eps = [("income_q", "income-statement", dict(period="quarter", limit=200)), ("balance_q", "balance-sheet-statement", dict(period="quarter", limit=200)),
           ("cashflow_q", "cash-flow-statement", dict(period="quarter", limit=200)), ("earnings", "earnings", dict(limit=200)),
           ("estimates_a", "analyst-estimates", dict(period="annual", limit=40)), ("insider", "insider-trading/search", dict(limit=500))]
    for s in us:
        if done(c, "fmp", s): continue
        n = 0
        try:
            for kind, ep, q in eps:
                j = get("fmp", "https://financialmodelingprep.com/stable/%s?%s" % (ep, urllib.parse.urlencode(dict(q, symbol=s, apikey=k)))) or []
                rows = j if isinstance(j, list) else []
                c.executemany("INSERT OR REPLACE INTO fundamentals VALUES(?,?,?,?,?)", [(s, kind, str(r.get("date") or r.get("transactionDate") or r.get("filingDate") or i), json.dumps(r), "fmp") for i, r in enumerate(rows) if isinstance(r, dict)])
                n += len(rows)
            mark(c, "fmp", s, "ok" if n else "empty", n)
        except RuntimeError as e: log("fmp STOP", e); return
        except Exception as e: mark(c, "fmp", s, "err:" + type(e).__name__)
    log("fmp done", len(us))
def ph_kraken(c, full, desk, emap):
    import kraken_dns; kraken_dns.install()
    try: pairs = json.load(open(os.path.join(BASE, "data", "pit", "kraken_pairs.json"))).get("pairs") or []
    except Exception: pairs = []
    pairs = [p if isinstance(p, str) else p.get("alt") for p in pairs if isinstance(p, str) or str(p.get("quote")) in ("ZUSD", "USD", "USDT", "USDC")]
    for p in [x for x in pairs if x]:
        if done(c, "kraken", p): continue
        j = get("kraken", "https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440" % p) or {}
        R = next((v for kk, v in (j.get("result") or {}).items() if kk != "last"), [])
        c.executemany("INSERT OR REPLACE INTO crypto_ohlc VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(p, "1d", int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7]), "kraken") for r in R])
        mark(c, "kraken", p, "ok" if R else "empty", len(R))
    log("kraken done", len(pairs))
def ph_coinalyze(c, full, desk, emap):
    k = key("coinalyze"); h = {"api_key": k}; now = int(time.time()); fr = now - 86400 * 365 * 8
    fut = get("coinalyze", "https://api.coinalyze.net/v1/future-markets", h) or []
    syms = [m["symbol"] for m in fut if m.get("exchange") == "A" and m.get("is_perpetual") and m.get("quote_asset") == "USDT"][:60] or ["BTCUSDT_PERP.A", "ETHUSDT_PERP.A", "SOLUSDT_PERP.A"]
    for i in range(0, len(syms), 10):
        B = ",".join(syms[i:i + 10])
        for kind, ep, extra in (("funding", "funding-rate-history", ""), ("oi_usd", "open-interest-history", "&convert_to_usd=true"),
                                ("liq_usd", "liquidation-history", "&convert_to_usd=true"), ("ls_ratio", "long-short-ratio-history", "")):
            it = "%s|%d" % (kind, i)
            if done(c, "coinalyze", it): continue
            j = get("coinalyze", "https://api.coinalyze.net/v1/%s?symbols=%s&interval=daily&from=%d&to=%d%s" % (ep, B, fr, now, extra), h) or []
            rows = []
            for x in j:
                for z in x.get("history") or []:
                    v = z.get("c") if kind in ("funding", "oi_usd") else (z.get("r") if kind == "ls_ratio" else (float(z.get("l") or 0) + float(z.get("s") or 0)))
                    rows.append((x["symbol"], kind, int(z["t"]), v, json.dumps(z), "coinalyze"))
            c.executemany("INSERT OR REPLACE INTO derivs VALUES(?,?,?,?,?,?)", rows); mark(c, "coinalyze", it, "ok" if rows else "empty", len(rows))
    log("coinalyze done", len(syms))
def report(c):
    q = lambda s: c.execute(s).fetchone()
    rep = dict(utc=dt.datetime.now(UTC).isoformat(),
        eodhd=dict(symbols=q("SELECT COUNT(DISTINCT sym) FROM eod")[0], bars=q("SELECT COUNT(*) FROM eod")[0], range=q("SELECT MIN(date),MAX(date) FROM eod"), corp_actions=q("SELECT COUNT(*) FROM corp_actions")[0]),
        massive={r[0]: dict(symbols=r[1], bars=r[2], first=dt.datetime.fromtimestamp((r[3] or 0) / 1000, UTC).date().isoformat(), last=dt.datetime.fromtimestamp((r[4] or 0) / 1000, UTC).date().isoformat())
                 for r in c.execute("SELECT res,COUNT(DISTINCT sym),COUNT(*),MIN(ts),MAX(ts) FROM aggs GROUP BY res")},
        fmp=dict(symbols=q("SELECT COUNT(DISTINCT sym) FROM fundamentals")[0], rows=q("SELECT COUNT(*) FROM fundamentals")[0], by_kind=dict(c.execute("SELECT kind,COUNT(*) FROM fundamentals GROUP BY kind").fetchall())),
        kraken=dict(pairs=q("SELECT COUNT(DISTINCT pair) FROM crypto_ohlc")[0], bars=q("SELECT COUNT(*) FROM crypto_ohlc")[0], range=[dt.datetime.fromtimestamp(x or 0, UTC).date().isoformat() for x in q("SELECT MIN(ts),MAX(ts) FROM crypto_ohlc")]),
        coinalyze=dict(symbols=q("SELECT COUNT(DISTINCT sym) FROM derivs")[0], rows=q("SELECT COUNT(*) FROM derivs")[0], range=[dt.datetime.fromtimestamp(x or 0, UTC).date().isoformat() for x in q("SELECT MIN(ts),MAX(ts) FROM derivs")]),
        progress=dict(((r[0] + ":" + r[1]), r[2]) for r in c.execute("SELECT phase,status,COUNT(*) FROM progress GROUP BY phase,status")))
    t = REP + ".tmp"; json.dump(rep, open(t, "w"), indent=1); os.replace(t, REP); return rep
PH = dict(coinalyze=ph_coinalyze, kraken=ph_kraken, eodhd=ph_eodhd, fmp=ph_fmp, massive=ph_massive)
if __name__ == "__main__":
    c = con(); full, desk, emap = universes(); log("START", sys.argv[1:] or "all", "full", len(full), "desk", len(desk))
    for n in (sys.argv[1:] or list(PH)):
        for att in range(8):   # lockretry: parallel phases share one sqlite; phases are resumable via progress
            try: PH[n](c, full, desk, emap); break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e): log("PHASE FAIL", n, "OperationalError", red(str(e))[:200]); break
                log("phase locked, retry", n, att); 
                try: c.rollback()
                except Exception: pass
                time.sleep(20 + 10 * att)
            except Exception as e: log("PHASE FAIL", n, type(e).__name__, red(str(e))[:200]); break
        report(c); log("phase end", n)
    log("END", json.dumps(report(c))[:800])
