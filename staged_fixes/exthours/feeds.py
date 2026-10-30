"""Free live feeds for veteran seats. Every item tagged source + available_at_utc. A feed failing 3 runs in a row is DROPPED (skipped) until
it succeeds on a daily retry. Health: state/veteran_desk/feeds_health.json. PAPER research only; public endpoints, no keys."""
import datetime as dt, json, os, re, sys, urllib.request, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); UTC = dt.timezone.utc; FH = os.path.join(BASE, "state", "veteran_desk", "feeds_health.json")
UA = {"User-Agent": "TFO paper research brett-desk research@example.com"}
class _Redir(urllib.request.HTTPRedirectHandler):
    """py3.9 urllib does not follow 308 (CoinDesk RSS 308s to the no-slash URL); treat 307/308 like 302 for GET."""
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, 302 if code in (307, 308) and req.get_method() in ("GET", "HEAD") else code, msg, hdrs, newurl)
    def http_error_308(self, req, fp, code, msg, hdrs): return self.http_error_302(req, fp, code, msg, hdrs)
_OPENER = urllib.request.build_opener(_Redir)
def _get(u, t=15): return _OPENER.open(urllib.request.Request(u, headers=UA), timeout=t).read()
def now(): return dt.datetime.now(UTC)
CRYPTO = ["XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD", "DOTUSD", "LINKUSD", "AVAXUSD", "DOGEUSD"]
MIN_24H_AUD = 500000.0   # Brett 2026-10-29 13:02 WITA: all caps, min 24h traded value A$500k (was US$20m); desk.py converts via book.aud
MIN_24H_USD = MIN_24H_AUD * 0.65   # fallback flag only (AUDUSD~0.65); desk.py re-checks in AUD at live FX
def kraken_spot():
    import kraken_dns; kraken_dns.install()
    d = json.loads(_get("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(CRYPTO)))["result"]; out = {}
    for k, v in d.items():
        last = float(v["c"][0]); vol = float(v["v"][1]) * float(v["p"][1])
        out[k] = dict(last=last, vol24h_usd=round(vol), liquid=vol >= MIN_24H_USD, hi24=float(v["h"][1]), lo24=float(v["l"][1]), open24=float(v["o"]))
    return out
def kraken_futures():
    import kraken_dns; kraken_dns.install()
    T = json.loads(_get("https://futures.kraken.com/derivatives/api/v3/tickers"))["tickers"]; out = {}
    for t in T:
        s = t.get("symbol", "")
        if s.startswith("PF_") and s[3:6] in ("XBT", "ETH", "SOL", "XRP", "ADA", "LTC", "DOT", "LIN", "AVA", "DOG"):
            out[s] = dict(funding_rate=t.get("fundingRate"), funding_pred=t.get("fundingRatePrediction"), open_interest=t.get("openInterest"), mark=t.get("markPrice"), index=t.get("indexPrice"),
                          basis_pct=round((t["markPrice"] / t["indexPrice"] - 1) * 100, 4) if t.get("markPrice") and t.get("indexPrice") else None)
    return out
CG_CACHE = os.path.join(BASE, "state", "veteran_desk", "coingecko_cache.json"); CG_MAX_STALE_H = 6
def coingecko_global(sleep=None, get=None):
    """CoinGecko public /global with backoff on 429/5xx (honours Retry-After, 3 tries: ~2s/4s/8s); on failure serves last good value if <6h old (stale=True)."""
    import time, urllib.error
    sleep = sleep or time.sleep; get = get or _get; err = None
    for i in range(4):
        try:
            d = json.loads(get("https://api.coingecko.com/api/v3/global"))["data"]
            out = dict(btc_dominance_pct=round(d["market_cap_percentage"]["btc"], 2), eth_dominance_pct=round(d["market_cap_percentage"]["eth"], 2), mcap_change_24h_pct=round(d["market_cap_change_percentage_24h_usd"], 2))
            try:
                os.makedirs(os.path.dirname(CG_CACHE), exist_ok=True); t = CG_CACHE + ".tmp"; json.dump(dict(out, cached_utc=now().isoformat()), open(t, "w")); os.replace(t, CG_CACHE)
            except Exception: pass
            return out
        except urllib.error.HTTPError as e:
            err = e
            if e.code != 429 and e.code < 500: break
            ra = (e.headers.get("Retry-After") if e.headers else None) or ""
            if i < 3: sleep(min(float(ra) if ra.isdigit() else 2.0 * 2 ** i, 30))
        except Exception as e:
            err = e; break
    try:
        c = json.load(open(CG_CACHE)); age = (now() - dt.datetime.fromisoformat(c["cached_utc"])).total_seconds() / 3600
        if age <= CG_MAX_STALE_H: return dict(c, stale=True, stale_age_h=round(age, 2))
    except Exception: pass
    raise err or RuntimeError("coingecko failed")
FRED_FALLBACK = {"AUDUSD=X": ("DEXUSAL", False), "^VIX": ("VIXCLS", False)}   # DEXUSAL is USD per AUD (= AUDUSD); FRED is prior-day close
def fred_last(series, get=None):
    """Last non-missing FRED observation (public fredgraph CSV, no key). Returns (date, value)."""
    txt = (get or _get)("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series, 20).decode().strip().splitlines()
    rows = [x.split(",") for x in txt[1:] if len(x.split(",")) > 1 and x.split(",")[1] not in (".", "")]
    if not rows: raise RuntimeError("FRED %s empty" % series)
    return rows[-1][0], float(rows[-1][1])
def fred_px(sym, get=None):
    """Fallback quote for AUDUSD=X / ^VIX when yfinance is empty: dict(last, hi, lo, asof, src) like book.live_px, or None."""
    if sym not in FRED_FALLBACK: return None
    try: d, v = fred_last(FRED_FALLBACK[sym][0], get)
    except Exception: return None
    return dict(last=v, hi=v, lo=v, asof=d, src="fred:" + FRED_FALLBACK[sym][0], delayed="prior-day")
def px_with_fallback(sym, yf_fn):
    """Call yf_fn(sym) (e.g. book.live_px); if it raises/returns empty, use FRED for AUDUSD=X / ^VIX."""
    try: p = yf_fn(sym)
    except Exception: p = None
    return p if p else fred_px(sym)
def cftc_cot():
    txt = _get("https://www.cftc.gov/dea/newcot/FinFutWk.txt", 30).decode("latin1"); out = {}
    for line in txt.splitlines():
        f = [x.strip().strip('"') for x in line.split(",")]
        name = f[0]
        for key in ("AUSTRALIAN DOLLAR", "JAPANESE YEN", "EURO FX", "E-MINI S&P 500 -", "UST 10Y NOTE"):
            if name.startswith(key) and key not in out:
                try: out[key] = dict(report_date=f[2], open_interest=int(f[7]), lev_money_long=int(f[14]), lev_money_short=int(f[15]))
                except Exception: pass
    return out   # COT released Fri 15:30 ET for Tuesday positions: available_at = release, not report_date
RSS = {"bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml", "fed_press": "https://www.federalreserve.gov/feeds/press_all.xml",
       "ecb_press": "https://www.ecb.europa.eu/rss/press.html", "sec_press": "https://www.sec.gov/news/pressreleases.rss", "rba_media": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"}
RSS_FALLBACK = {"fed_press": ["https://www.federalreserve.gov/feeds/press_monetary.xml"]}   # fix 6/7: official list https://www.federalreserve.gov/feeds/feeds.htm (press_all.xml verified 200 2026-10-28 21:10 WITA)
def rss(name):
    err = None
    for u in [RSS[name]] + RSS_FALLBACK.get(name, []):
        try: raw = _get(u); break
        except Exception as e: err = e
    else: raise err
    root = ET.fromstring(raw); out = []
    for it in root.iter("item"):
        out.append(dict(title=(it.findtext("title") or "").strip()[:200], published=(it.findtext("pubDate") or "").strip()))
        if len(out) >= 8: break
    return out
def edgar_8k(tickers):
    """EDGAR full-text search: 8-Ks in last 7 days for the given names (public efts endpoint)."""
    s = (now() - dt.timedelta(days=7)).date().isoformat(); out = {}
    for t in tickers[:12]:
        try:
            d = json.loads(_get(f"https://efts.sec.gov/LATEST/search-index?q=%22{t}%22&forms=8-K&dateRange=custom&startdt={s}"))
            out[t] = [dict(filed=h["_source"].get("file_date"), name=(h["_source"].get("display_names") or [""])[0][:80]) for h in d.get("hits", {}).get("hits", [])[:3]]
        except Exception: pass
    return out
TECH = ["NVDA", "AMD", "AVGO", "MSFT", "META", "ORCL", "CRWV", "NBIS", "SMCI", "MU"]
def tech_pack():
    """Nico Hart: options put/call volume (Yahoo, ~15m delayed), short % float (Yahoo, bi-monthly FINRA data), IBKR borrow, SEC Form 4 insider atom,
    neocloud financing 8-Ks (keyword). Each sub-item tagged src. Not wired: GPU lease rates, CDS, 13F detail (quarterly, 45-day lag) - planned."""
    import yfinance as yf
    out = dict(options={}, short_interest={}, borrow={}, insider_form4=[], not_wired=["GPU pricing/lease rates (no free reliable source)", "CDS spreads (paid)", "13F holdings detail (planned; 45-day lag)", "hyperscaler capex (from 10-Q text - planned)"])
    for t in TECH:
        try:
            tk = yf.Ticker(t); ex = tk.options[0]; ch = tk.option_chain(ex); cv, pv = float(ch.calls.volume.fillna(0).sum()), float(ch.puts.volume.fillna(0).sum())
            out["options"][t] = dict(expiry=ex, call_vol=cv, put_vol=pv, put_call=round(pv / cv, 2) if cv else None, src="yahoo_options_delayed")
            inf = tk.info; out["short_interest"][t] = dict(short_pct_float=inf.get("shortPercentOfFloat"), days_to_cover=inf.get("shortRatio"), src="yahoo/finra (bi-monthly)")
        except Exception: pass
    try:
        b = json.load(open(os.path.join(BASE, "state", "ibkr_borrow.json")))["us"]["names"]; out["borrow"] = {t: dict(b.get(t) or {}, src="ibkr_ftp") for t in TECH}
    except Exception: pass
    try:
        root = ET.fromstring(_get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=100&output=atom")); A = "{http://www.w3.org/2005/Atom}"
        names = ("NVIDIA", "ADVANCED MICRO", "BROADCOM", "MICROSOFT", "META PLATFORMS", "ORACLE", "COREWEAVE", "NEBIUS", "SUPER MICRO", "MICRON")
        out["insider_form4"] = [dict(title=e.findtext(A + "title"), updated=e.findtext(A + "updated"), src="sec_form4_atom") for e in root.iter(A + "entry") if any(n in (e.findtext(A + "title") or "").upper() for n in names)][:10]
    except Exception: pass
    return out
FEEDS = {"tech_pack": tech_pack, "kraken_spot": kraken_spot, "kraken_futures": kraken_futures, "coingecko_global": coingecko_global, "cftc_cot": cftc_cot,
         **{f"rss_{k}": (lambda k=k: rss(k)) for k in RSS}}
NOT_WIRED = {"binance_okx_public": "blocked by ISP DNS sinkhole on current Wi-Fi (connection refused)", "eia": "needs a free EIA API key from Brett (not provisioned; not requested - no keys in prompts)",
             "google_trends": "no stable free API (pytrends rate-limited) - planned", "alfred_vintages": "planned phase 2", "earnings_calendar": "planned (Nasdaq JSON needs scraping)",
             "onchain_flows": "no reliable free keyless source found - planned"}
def fetch(names):
    H = {}
    try: H = json.load(open(FH))
    except Exception: pass
    out = {}
    for n in names:
        h = H.setdefault(n, dict(fails=0)); dropped = h["fails"] >= 3 and h.get("last_try", "")[:10] == now().isoformat()[:10]
        if dropped: out[n] = dict(source=n, dropped=True, why="unreliable: 3+ consecutive failures"); continue
        h["last_try"] = now().isoformat()
        try: out[n] = dict(source=n, available_at_utc=now().isoformat(), data=FEEDS[n]()); h.update(fails=0, last_ok=now().isoformat()); h.pop("last_err", None)   # fix 6/7: no stale error after a success
        except Exception as e: h["fails"] += 1; h["last_err"] = str(e)[:120]; out[n] = dict(source=n, error=str(e)[:120])
    H["_not_wired"] = NOT_WIRED; os.makedirs(os.path.dirname(FH), exist_ok=True); json.dump(H, open(FH, "w"), indent=1); return out
if __name__ == "__main__":
    r = fetch(list(FEEDS)); print({k: ("OK" if "data" in v else v.get("error") or "dropped") for k, v in r.items()})
# ---- live VIX (Brett follow-up 2026-10-28 20:35 WITA): Yahoo chart API ^VIX (free, no key) -> CBOE delayed quote fallback; every quote time-stamped.
try:
    from zoneinfo import ZoneInfo; _ET = ZoneInfo("America/New_York")
except Exception: _ET = None
def _et(t): return t.astimezone(_ET) if _ET else t.astimezone(dt.timezone(dt.timedelta(hours=-4)))
def us_hours(t):
    e = _et(t); m = e.hour * 60 + e.minute; return e.weekday() < 5 and 9 * 60 + 30 <= m < 16 * 60 + 15
def last_us_close(t):
    """Most recent weekday 16:00 ET at or before t (US holidays not modelled -> a holiday makes the prior day's close the bar, conservative)."""
    e = _et(t); c = e.replace(hour=16, minute=0, second=0, microsecond=0)
    if e < c: c -= dt.timedelta(days=1)
    while c.weekday() >= 5: c -= dt.timedelta(days=1)
    return c.astimezone(dt.timezone.utc)
def vix_stale(asof, t=None):
    """Stale = >30 min old during US hours (09:30-16:15 ET), or older than the last US close outside US hours."""
    t = t or dt.datetime.now(dt.timezone.utc)
    if asof is None: return True, "no quote time"
    a = asof if isinstance(asof, dt.datetime) else dt.datetime.fromisoformat(str(asof))
    if us_hours(t): return ((t - a).total_seconds() > 1800), "US hours: age %.0f min (limit 30)" % ((t - a).total_seconds() / 60)
    lc = last_us_close(t); return (a < lc), "outside US hours: quote %s vs last close %s" % (a.isoformat(timespec="minutes"), lc.isoformat(timespec="minutes"))
def _vix_yahoo():
    d = json.loads(_get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=5m&range=1d", 12))["chart"]["result"][0]; m = d["meta"]
    q = d.get("indicators", {}).get("quote", [{}])[0]; hs = [x for x in (q.get("high") or [])[-6:] if x]; ls = [x for x in (q.get("low") or [])[-6:] if x]
    px = float(m["regularMarketPrice"]); return dict(last=px, hi=max(hs + [px]), lo=min(ls + [px]), asof=dt.datetime.fromtimestamp(int(m["regularMarketTime"]), dt.timezone.utc).isoformat(), src="yahoo:^VIX chart")
def _vix_cboe():
    d = json.loads(_get("https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json", 12)); x = d["data"]; px = float(x["current_price"])
    a = dt.datetime.fromisoformat(d["timestamp"].replace(" ", "T")).replace(tzinfo=dt.timezone.utc)   # CBOE 'timestamp' is UTC (verified = box UTC clock 2026-10-28)
    return dict(last=px, hi=float(x.get("high") or px), lo=float(x.get("low") or px), asof=a.isoformat(), src="cboe:delayed _VIX")
def vix_live(t=None):
    """Best fresh VIX: Yahoo first, CBOE delayed fallback; returns the freshest NON-stale quote, else the freshest stale one flagged delayed=True."""
    t = t or dt.datetime.now(dt.timezone.utc); got = []
    for f in (_vix_yahoo, _vix_cboe):
        try:
            q = f(); st, why = vix_stale(q["asof"], t); q.update(delayed=st, stale_why=why, checked_utc=t.isoformat()); got.append(q)
            if not st: return q
        except Exception as e: got.append(dict(src=f.__name__, error=str(e)[:120]))
    ok = [g for g in got if "last" in g]
    return max(ok, key=lambda g: g["asof"]) if ok else None


def prior_close(sym, yahoo_prev=None, bar_close=None, max_div=0.05):
    """Last daily-bar close preferred; Yahoo previousClose only as fallback with sanity check.
    Iris 29 Oct 2026: JP/KR Yahoo previousClose can show bogus 5-30% drops vs daily bars.
    Returns dict(close, src, rejected_prev=...). Never raises."""
    bar = bar_close
    if bar is None:
        try:
            from veteran_desk import pit as P
            c = P.con(); lb = c.execute("SELECT c FROM bars WHERE sym=? ORDER BY date DESC LIMIT 1", (sym,)).fetchone(); c.close()
            if lb: bar = float(lb[0])
        except Exception: bar = None
    if bar is None:
        try:
            from veteran_desk import book as BK
            q = BK.yahoo_v8_quote(sym, "1d", "10d") if hasattr(BK, "yahoo_v8_quote") else None
            if q and q.get("last"): bar = float(q["last"])
        except Exception: pass
    prev = None
    try: prev = float(yahoo_prev) if yahoo_prev is not None else None
    except Exception: prev = None
    if prev is None:
        try:
            import yfinance as yf
            fi = yf.Ticker(sym).fast_info
            prev = float(getattr(fi, "previous_close", None) or fi["previousClose"])
        except Exception: prev = None
    if bar is not None and prev is not None and abs(prev / bar - 1) > max_div:
        return dict(close=bar, src="daily_bar", rejected_prev=prev, why="previousClose diverged >%.0f%% from daily bar" % (max_div * 100))
    if bar is not None: return dict(close=bar, src="daily_bar")
    if prev is not None: return dict(close=prev, src="previousClose_fallback")
    return dict(close=None, src=None)


# ---- 2026-10-29 exthours: US extended-hours quote path (PAPER; used only for gate/arrival checks, never for fills) ----
# Bug: arrival/gate 'live already through stop' used the regular-session close, blind to US pre/post prints, so every overnight earnings
# gap was judged on the wrong price (GKOS 2026-10-29: regular 181.43 vs post-market ~213). Yahoo stays primary (yfinance quote fields
# marketState / preMarketPrice / postMarketPrice + times); IBKR read-only 1-min bars (useRTH=False) only when Yahoo has no fresh print.
EXT_MAX_AGE_S = 1800   # a print counts only if <= 30 min old
def is_us(sym):
    s = str(sym or "").upper()
    if not s or s.startswith("KRAKEN:") or s.startswith("^") or s.endswith("=X") or s.endswith("=F"): return False
    if s.endswith(("-USD", "-USDT", "-USDC", "-EUR", "-GBP", "-AUD")): return False   # Yahoo crypto pairs
    return "." not in s or s.rsplit(".", 1)[-1] in ("A", "B")   # BRK.B-style share classes are US
def us_session_of(t):
    """PRE 04:00-09:30 ET, REGULAR 09:30-16:00, POST 16:00-20:00, else CLOSED (weekends CLOSED; holidays not modelled)."""
    e = _et(t); m = e.hour * 60 + e.minute
    if e.weekday() >= 5: return "CLOSED"
    if 240 <= m < 570: return "PRE"
    if 570 <= m < 960: return "REGULAR"
    if 960 <= m < 1200: return "POST"
    return "CLOSED"
def _ts(x):
    if x in (None, "", 0): return None
    if isinstance(x, dt.datetime): return x if x.tzinfo else x.replace(tzinfo=UTC)
    try: return dt.datetime.fromtimestamp(float(x), UTC)
    except Exception: pass
    try:
        d = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")); return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception: return None
def _yahoo_info(sym):
    import yfinance as yf
    i = yf.Ticker(sym).info or {}
    return {k: i.get(k) for k in ("marketState", "regularMarketPrice", "regularMarketTime", "preMarketPrice", "preMarketTime", "postMarketPrice", "postMarketTime")}
_IBKR_SNAP = r'''
import sys, json
from ib_async import IB, Stock
ib = IB(); ib.connect("127.0.0.1", 4002, clientId=96, readonly=True, timeout=15)
try:
    c = Stock(sys.argv[1].replace("-", " ").replace(".", " "), "SMART", "USD"); ib.qualifyContracts(c)
    bs = ib.reqHistoricalData(c, endDateTime="", durationStr="3600 S", barSizeSetting="1 min", whatToShow="TRADES", useRTH=False, formatDate=2, timeout=30)
    print(json.dumps(dict(last=bs[-1].close, asof=bs[-1].date.isoformat()) if bs else {}))
finally: ib.disconnect()
'''
def _ibkr_snapshot(sym):
    """IBKR backup (read-only paper gateway, historical 1-min TRADES incl. extended hours). None on any failure."""
    try:
        import subprocess
        py = os.path.expanduser("~/ibkr/venv/bin/python")
        if not os.path.exists(py): return None
        out = subprocess.run([py, "-c", _IBKR_SNAP, sym], capture_output=True, text=True, timeout=45).stdout.strip().splitlines()
        d = json.loads(out[-1]) if out else {}
        return d if d.get("last") else None
    except Exception: return None
def ext_quote(sym, now_utc=None, info_fn=None, ibkr_fn=None):
    """Freshest valid US print: regular / pre / post, whichever is newest and <= 30 min old; else the regular price (flagged stale).
    Returns dict(last, session, asof, src, market_state, fresh, fallback, candidates) or None (non-US name, or no data at all)."""
    if not is_us(sym): return None
    t = now_utc or now(); info_fn = info_fn or _yahoo_info; ibkr_fn = ibkr_fn or _ibkr_snapshot
    try: q = info_fn(sym) or {}
    except Exception as e: q = {"_err": str(e)[:120]}
    cands = []
    for ses, pk, tk in (("REGULAR", "regularMarketPrice", "regularMarketTime"), ("PRE", "preMarketPrice", "preMarketTime"), ("POST", "postMarketPrice", "postMarketTime")):
        try: px = float(q.get(pk)) if q.get(pk) is not None else None
        except Exception: px = None
        ts = _ts(q.get(tk))
        if px and px > 0 and ts: cands.append(dict(session=ses, last=px, asof=ts, src="yahoo:" + pk))
    def fresh(c): return -120 <= (t - c["asof"]).total_seconds() <= EXT_MAX_AGE_S
    ok = sorted([c for c in cands if fresh(c)], key=lambda c: c["asof"])
    pick, fb = (ok[-1], None) if ok else (None, None)
    if pick is None:
        ib = None
        if us_session_of(t) != "CLOSED":
            try: ib = ibkr_fn(sym)
            except Exception: ib = None
        if ib and ib.get("last"):
            c = dict(last=float(ib["last"]), asof=_ts(ib.get("asof")), src="ibkr:1min_trades_rth_false")
            if c["asof"] is not None:
                c["session"] = us_session_of(c["asof"]); cands.append(c)
                if fresh(c): pick, fb = c, "yahoo_no_fresh_print->ibkr"
    if pick is None:
        reg = [c for c in cands if c["session"] == "REGULAR"]
        if not reg: return None
        pick = reg[0]; fb = "no print within 30 min -> regular-session price"
    def ser(c): return dict(session=c["session"], last=c["last"], asof=c["asof"].isoformat(), src=c["src"], age_min=round((t - c["asof"]).total_seconds() / 60, 1))
    return dict(last=pick["last"], session=pick["session"], asof=pick["asof"].isoformat(), src=pick["src"], market_state=q.get("marketState"),
                fresh=fb is None or fb.endswith("ibkr"), fallback=fb, checked_utc=t.isoformat(), candidates=[ser(c) for c in cands])
def fresh_px(sym, base_fn=None, **kw):
    """live_px-shaped quote for arrival checks: US -> ext_quote (pre/post aware); anything else -> base_fn (book.live_px), unchanged."""
    if is_us(sym):
        try: q = ext_quote(sym, **kw)
        except Exception: q = None
        if q and q.get("last"): return dict(q, hi=q["last"], lo=q["last"])
    if base_fn is None:
        from veteran_desk import book as BK; base_fn = BK.live_px
    return base_fn(sym)
