"""2026-10-28 Brett 10:28 WITA: INFO-ONLY tags on every hunt-book ARMED/FILLED trade. PAPER only.
NEVER blocks, delays or resizes a trade: runs AFTER arming, reads ledgers read-only, writes only state/trade_tags/.
 (1) news_risk: earnings / takeover-scheme / trading halt / major announcement inside the 72h hold.
     Sources: desk files state/calendar/inputs/asx_events.json + <mkt>_events.json, yfinance calendar + news.
 (2) quality: balance-sheet floor D/E <= 2.0, interest cover >= 3x, operating cash flow > 0 (yfinance statements).
     Missing data -> 'n/a' (never invented).
CLI: python3 trade_tags.py run | show"""
import datetime as dt, json, os, re, sys, time
BASE = os.path.dirname(os.path.abspath(__file__)); UTC = dt.timezone.utc
OUT = os.path.join(BASE, "state", "trade_tags"); TAGS = os.path.join(OUT, "tags.json"); LOG = os.path.join(OUT, "trades.jsonl")
CACHE = os.path.join(OUT, "cache"); HOLD_H = 72; CACHE_H = 6
BOOKS = {"us_main": "hunt48_short", "us_claude": "hunt48_short_claude", "us_gpt": "hunt48_short_gpt", "asx": "hunt48_asx", "jp": "hunt48_jp",
         "hk": "hunt48_hk", "sg": "hunt48_sg", "kr": "hunt48_kr", "tw": "hunt48_tw", "lse": "hunt48_lse"}
DE_MAX, COVER_MIN = 2.0, 3.0
RISK_WORDS = [("takeover/scheme", r"takeover|scheme of arrangement|\bscheme\b|tender offer|acquisition proposal|merger|buyout|bid for|non-binding|indicative offer"),
              ("trading halt", r"trading halt|suspension|suspended|halted"),
              ("major announcement", r"capital raising|placement|rights issue|profit warning|guidance|restructur|delist|investigation|lawsuit|class action")]

def _j(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def sym_of(t): return t.get("sym") or t.get("ticker") or t.get("yahoo")
def ysym(book, s):
    return s if (book.startswith("us") or "." in s) else s

def desk_events(book, s, now):
    """earnings/halt/announcements from the desk's own event files (already fetched by the universes)."""
    out = []; end = now + dt.timedelta(hours=HOLD_H)
    mk = "asx" if book == "asx" else (None if book.startswith("us") else book)
    if mk:
        j = _j(os.path.join(BASE, "state", "calendar", "inputs", f"{mk}_events.json"), {}) or {}
        code = s[:-3] if mk == "asx" and s.endswith(".AX") else s
        e = (j.get("earnings") or {}).get(code)
        try:
            ed = dt.date.fromisoformat(str(e)[:10]) if e else None
            if ed and now.date() - dt.timedelta(days=1) <= ed <= end.date(): out.append(f"earnings {ed} (desk calendar)")
        except Exception: pass
        if code in (j.get("halted") or []) or s in (j.get("halted") or []): out.append("trading halt (desk feed)")
        for a in j.get("announcements") or []:
            if a.get("code") == code and a.get("price_sensitive"):
                try: w = dt.datetime.fromisoformat(a["when_utc"])
                except Exception: continue
                if (now - w).total_seconds() <= HOLD_H * 3600: out.append("price-sensitive ASX ann: " + str(a.get("headline"))[:80])
    return out

def _yf(s):
    """cached yfinance pull (calendar, news, statements). Any failure -> None fields (shown n/a)."""
    os.makedirs(CACHE, exist_ok=True); p = os.path.join(CACHE, s.replace("/", "_") + ".json"); c = _j(p)
    if c and time.time() - c.get("_t", 0) < CACHE_H * 3600: return c
    r = dict(_t=time.time(), earnings=None, news=[], de=None, cover=None, ocf=None)
    try:
        import yfinance as yf; T = yf.Ticker(s)
        try:
            ed = (T.calendar or {}).get("Earnings Date") or []
            r["earnings"] = [str(d) for d in ed]
        except Exception: pass
        try:
            for n in (T.news or [])[:20]:
                c_ = n.get("content") or n
                ts = c_.get("pubDate") or c_.get("providerPublishTime")
                r["news"].append(dict(title=c_.get("title") or "", when=ts if isinstance(ts, str) else (dt.datetime.fromtimestamp(ts, UTC).isoformat() if ts else None)))
        except Exception: pass
        def pick(df, *names):
            try:
                for nm in names:
                    if nm in df.index:
                        v = df.loc[nm].dropna()
                        if len(v): return float(v.iloc[0])
            except Exception: pass
            return None
        try:
            bs = T.balance_sheet; debt = pick(bs, "Total Debt"); eq = pick(bs, "Stockholders Equity", "Common Stock Equity")
            if debt is not None and eq: r["de"] = round(debt / eq, 3) if eq > 0 else "neg equity"
        except Exception: pass
        try:
            inc = T.income_stmt; ebit = pick(inc, "EBIT"); ie = pick(inc, "Interest Expense")
            if ebit is not None and ie is not None: r["cover"] = round(ebit / abs(ie), 2) if ie else "no interest"
        except Exception: pass
        try: r["ocf"] = pick(T.cashflow, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
        except Exception: pass
    except Exception as e: r["err"] = str(e)[:120]
    json.dump(r, open(p, "w"), default=str); return r

def news_risk(book, s, now, Y):
    tags = desk_events(book, s, now); end = now + dt.timedelta(hours=HOLD_H)
    for e in Y.get("earnings") or []:
        try:
            d = dt.date.fromisoformat(str(e)[:10])
            if now.date() - dt.timedelta(days=1) <= d <= end.date(): tags.append(f"earnings {d} (yfinance)")
        except Exception: pass
    for n in Y.get("news") or []:
        try: w = dt.datetime.fromisoformat(str(n["when"]).replace("Z", "+00:00"))
        except Exception: continue
        if (now - w).total_seconds() > HOLD_H * 3600: continue
        for lab, rx in RISK_WORDS:
            if re.search(rx, n.get("title", ""), re.I): tags.append(f"{lab}: {n['title'][:80]}"); break
    return sorted(set(tags))

def quality(Y):
    de, cv, ocf = Y.get("de"), Y.get("cover"), Y.get("ocf"); fails = []
    if isinstance(de, (int, float)) and de > DE_MAX: fails.append(f"D/E {de:.2f} > {DE_MAX}")
    if de == "neg equity": fails.append("negative equity")
    if isinstance(cv, (int, float)) and cv < COVER_MIN: fails.append(f"interest cover {cv:.1f}x < {COVER_MIN:g}x")
    if isinstance(ocf, (int, float)) and ocf <= 0: fails.append("operating cash flow <= 0")
    known = sum(isinstance(x, (int, float)) or x in ("neg equity", "no interest") for x in (de, cv, ocf))
    verdict = "FAIL" if fails else ("PASS" if known == 3 else ("n/a" if known == 0 else "PASS (partial)"))
    na = lambda x, f: "n/a" if x is None else (x if isinstance(x, str) else f(x))
    return dict(verdict=verdict, de=na(de, lambda v: round(v, 2)), interest_cover=na(cv, lambda v: round(v, 1)),
                ocf_positive=("n/a" if ocf is None else bool(ocf > 0)), fails=fails)

def tag_trade(book, t, now, yf_fn=_yf):
    s = sym_of(t); Y = yf_fn(ysym(book, s)) or {}
    nr = news_risk(book, s, now, Y); q = quality(Y)
    return dict(book=book, sym=s, trade_id=t.get("id"), status=t.get("status"), direction=t.get("direction"), tagged_utc=now.isoformat(),
                news_risk=nr, news_flag=bool(nr), quality=q, catalyst=t.get("catalyst"), quality_flag=q["verdict"] == "FAIL", info_only=True)

def run(now=None, yf_fn=_yf):
    now = now or dt.datetime.now(UTC); os.makedirs(OUT, exist_ok=True)
    old = _j(TAGS, {}) or {}; new = {}
    for book, d in BOOKS.items():
        for t in (_j(os.path.join(BASE, "state", d, "ledger.json"), {}) or {}).get("trades", []):
            if t.get("status") not in ("ARMED", "FILLED", "awaiting_funder"): continue
            k = f"{book}:{sym_of(t)}"; prev = old.get(k)
            if prev and prev.get("trade_id") == t.get("id") and prev.get("status") == t.get("status") and \
               now.timestamp() - dt.datetime.fromisoformat(prev["tagged_utc"]).timestamp() < CACHE_H * 3600:
                new[k] = prev; continue
            try: r = tag_trade(book, t, now, yf_fn)
            except Exception as e: r = dict(book=book, sym=sym_of(t), trade_id=t.get("id"), status=t.get("status"), tagged_utc=now.isoformat(), error=str(e)[:120],
                                            news_risk=[], news_flag=None, quality=dict(verdict="n/a"), info_only=True)
            new[k] = r
            with open(LOG, "a") as f: f.write(json.dumps(r, default=str) + "\n")    # per-trade log for later flagged-vs-unflagged study
    tmp = TAGS + ".tmp"; json.dump(new, open(tmp, "w"), indent=1, default=str); os.replace(tmp, TAGS)
    try:      # refresh tags on pending funder-queue entries (display only)
        import funder_gate as FG
        def f(Q):
            for e in Q["entries"].values():
                r = new.get(f"{e['book']}:{e['ticker']}")
                if r and e.get("state") == FG.WAIT: e["tags"] = dict(e.get("tags") or {}, news_risk=r.get("news_risk") or [], quality=(r.get("quality") or {}).get("verdict", "n/a"))
        if BASE == FG.BASE: FG.update(f)
    except Exception: pass
    return new

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "show"
    R = run() if c == "run" else (_j(TAGS, {}) or {})
    for k, r in R.items(): print(k, r.get("status"), "news:", r.get("news_risk") or "none", "| quality:", r.get("quality"))
