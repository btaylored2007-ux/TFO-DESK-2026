"""tfo_dashaudit_20261030 (Brett 22:17 WITA): per-position rows + live market data for VD/THF trader cards. PAPER, READ-ONLY.
Reads the book dict passed in (never writes a ledger). Live data is fetched in ONE background thread (60s loop) from paid
feeds first (Massive snapshot/Benzinga for US, EODHD real-time for non-US, Kraken + Coinalyze for crypto); the dashboard
request only reads the in-memory cache, so a slow feed never slows the page. Every item is source-tagged; keys never leave this process."""
import datetime as dt, json, os, threading, time, urllib.parse, urllib.request
UTC = dt.timezone.utc
_CACHE, _NAMES, _AVGV, _WANT = {}, {}, {}, set(); _LOCK = threading.Lock(); _STARTED = [False]
def _f(x):
    try: return float(x)
    except Exception: return None
def _age_h(u):
    try: return round((dt.datetime.now(UTC) - dt.datetime.fromisoformat(str(u).replace("Z", "+00:00"))).total_seconds() / 3600, 1)
    except Exception: return None
def _today_wita(u):
    try: return (dt.datetime.fromisoformat(str(u).replace("Z", "+00:00")) + dt.timedelta(hours=8)).date() == (dt.datetime.now(UTC) + dt.timedelta(hours=8)).date()
    except Exception: return False
def _key(f, v):
    k = os.environ.get(v)
    if k: return k.strip()
    try:
        for ln in open(os.path.expanduser("~/.tfo_secrets/%s.env" % f)):
            if ln.strip().startswith(v + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None
def _get(url, headers=None, t=8):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk"}, **(headers or {}))), timeout=t) as f: return json.loads(f.read())
    except Exception: return None
def _crypto(sym): return str(sym).upper().startswith("KRAKEN:")
def _us(p): return (p.get("venue") or "").upper() == "US" or ("." not in str(p.get("ticker")) and not _crypto(p.get("ticker")) and "=" not in str(p.get("ticker")))
def _live_us(t):
    k = _key("massive", "MASSIVE_API_KEY")
    if not k: return None
    H = {"Authorization": "Bearer " + k}; T = urllib.parse.quote(t.replace("-", ".").upper())
    js = _get("https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/%s" % T, H) or {}
    s = js.get("ticker") or {}
    if not s: return None
    d, pdv, q, lt = s.get("day") or {}, s.get("prevDay") or {}, s.get("lastQuote") or {}, s.get("lastTrade") or {}
    bid, ask, bs, az = _f(q.get("p")), _f(q.get("P")), _f(q.get("s")), _f(q.get("S"))
    o = dict(src="massive_snapshot", day_pct=_f(s.get("todaysChangePerc")), vol=_f(d.get("v")), prev_vol=_f(pdv.get("v")),
             spread_bps=(round((ask - bid) / ((ask + bid) / 2) * 1e4, 1) if bid and ask and ask >= bid else None),
             imbalance=(round((bs - az) / (bs + az), 2) if bs is not None and az and (bs + az) else None), last=_f(lt.get("p")) or _f(d.get("c")))
    if t not in _AVGV:
        e = dt.date.today(); a = _get("https://api.massive.com/v2/aggs/ticker/%s/range/1/day/%s/%s?adjusted=true&limit=40" % (T, e - dt.timedelta(days=45), e - dt.timedelta(days=1)), H) or {}
        v = [r.get("v") for r in (a.get("results") or [])][-20:]; _AVGV[t] = (sum(v) / len(v)) if v else None
    o["vol_vs_avg"] = round(o["vol"] / _AVGV[t], 2) if o["vol"] and _AVGV.get(t) else None
    if t not in _NAMES:
        r = _get("https://api.massive.com/v3/reference/tickers/%s" % T, H) or {}; _NAMES[t] = (r.get("results") or {}).get("name")
    n = _get("https://api.massive.com/benzinga/v2/news?" + urllib.parse.urlencode(dict(tickers=t.upper(), limit=3, sort="published.desc")), H) or {}
    nr = [x for x in (n.get("results") or []) if (_age_h(x.get("published_utc") or x.get("published")) or 99) <= 24]
    o["news"] = dict(src="massive_benzinga", n24h=len(nr), latest=(nr[0].get("title") or "")[:140] if nr else None) if n else dict(src="massive_benzinga", n24h=None, latest=None, err="no response")
    return o
def _eod_sym(t):
    m = {".AX": ".AU", ".T": ".TSE", ".L": ".LSE", ".SW": ".SW", ".DE": ".XETRA", ".TO": ".TO", ".PA": ".PA", ".MI": ".MI", ".HK": ".HK", ".AS": ".AS"}
    for a, b in m.items():
        if t.upper().endswith(a): return t[: -len(a)] + b
    return t
def _live_eodhd(t):
    k = _key("eodhd", "EODHD_API_KEY")
    if not k: return None
    s = urllib.parse.quote(_eod_sym(t))
    r = _get("https://eodhd.com/api/real-time/%s?fmt=json&api_token=%s" % (s, urllib.parse.quote(k)))
    if not isinstance(r, dict) or r.get("close") in (None, "NA"): return None
    o = dict(src="eodhd_realtime", day_pct=_f(r.get("change_p")), vol=_f(r.get("volume")), last=_f(r.get("close")), spread_bps=None, imbalance=None,
             asof_utc=(dt.datetime.fromtimestamp(int(r["timestamp"]), UTC).isoformat() if str(r.get("timestamp", "")).isdigit() else None))
    if t not in _AVGV:
        e = dt.date.today(); a = _get("https://eodhd.com/api/eod/%s?fmt=json&from=%s&api_token=%s" % (s, e - dt.timedelta(days=40), urllib.parse.quote(k))) or []
        v = [x.get("volume") for x in a if isinstance(x, dict)][-20:]; _AVGV[t] = (sum(v) / len(v)) if v else None
    o["vol_vs_avg"] = round(o["vol"] / _AVGV[t], 2) if o["vol"] and _AVGV.get(t) else None
    if t not in _NAMES:
        f = _get("https://eodhd.com/api/fundamentals/%s?filter=General::Name&api_token=%s" % (s, urllib.parse.quote(k)))
        _NAMES[t] = f if isinstance(f, str) else None
    return o
def _live_crypto(t):
    try:
        import kraken_dns; kraken_dns.install()   # same Kraken DNS shim the desk feeds use
    except Exception: pass
    pair = t.split(":", 1)[1].upper(); base = pair[:-3]
    js = _get("https://api.kraken.com/0/public/Ticker?pair=" + pair) or {}
    r = list((js.get("result") or {}).values())
    if not r: return None
    r = r[0]; a, b = _f(r["a"][0]), _f(r["b"][0]); az, bz = _f(r["a"][2]), _f(r["b"][2]); op = _f(r.get("o")); last = _f(r["c"][0])
    v24 = _f(r["v"][1]); vt = _f(r["v"][0])
    o = dict(src="kraken_ticker", last=last, day_pct=(round((last / op - 1) * 100, 2) if op and last else None),
             spread_bps=(round((a - b) / ((a + b) / 2) * 1e4, 1) if a and b else None), imbalance=(round((bz - az) / (bz + az), 2) if az and bz else None),
             vol=vt, vol_vs_avg=None, vol_note="today vs 24h %s" % (round(vt / v24, 2) if vt and v24 else "?"))
    trades = _get("https://api.kraken.com/0/public/Trades?count=500&pair=" + pair) or {}
    tr = [x for k, x in (trades.get("result") or {}).items() if k != "last"]
    if tr:
        buy = sum(_f(x[1]) or 0 for x in tr[0] if x[3] == "b"); sell = sum(_f(x[1]) or 0 for x in tr[0] if x[3] == "s")
        o["aggressor_buy_pct"] = round(100 * buy / (buy + sell)) if buy + sell else None
    k = _key("coinalyze", "COINALYZE_API_KEY")
    if k:
        sym = "%sUSDT_PERP.A" % ("BTC" if base in ("XBT", "XXBT") else base); H = {"api_key": k}; now = int(time.time())
        fr = _get("https://api.coinalyze.net/v1/funding-rate?symbols=" + sym, H) or []
        oi = _get("https://api.coinalyze.net/v1/open-interest-history?symbols=%s&interval=1hour&from=%d&to=%d&convert_to_usd=true" % (sym, now - 86400, now), H) or []
        lq = _get("https://api.coinalyze.net/v1/liquidation-history?symbols=%s&interval=1hour&from=%d&to=%d&convert_to_usd=true" % (sym, now - 86400, now), H) or []
        try: o["funding_pct"] = round(float(fr[0]["value"]), 4)   # Coinalyze value is already in %
        except Exception: o["funding_pct"] = None
        try: h = oi[0]["history"]; o["oi_chg_24h_pct"] = round((h[-1]["c"] / h[0]["o"] - 1) * 100, 2)
        except Exception: o["oi_chg_24h_pct"] = None
        try: h = lq[0]["history"]; o["liq_24h_usd"] = dict(longs=round(sum(x.get("l", 0) for x in h)), shorts=round(sum(x.get("s", 0) for x in h)))
        except Exception: o["liq_24h_usd"] = None
        o["deriv_src"] = "coinalyze:" + sym
    else: o["deriv_src"] = "coinalyze key missing"
    return o
def _fetch(t, us):
    try:
        if _crypto(t): return _live_crypto(t)
        if "=" in t: return None   # FX/futures: no paid per-symbol tape wired here
        return _live_us(t) if us else _live_eodhd(t)
    except Exception as e: return dict(src="error", err=type(e).__name__)
def _loop():
    while True:
        for t, us in list(_WANT):
            if os.environ.get("PYTEST_CURRENT_TEST"): break
            o = _fetch(t, us)
            with _LOCK: _CACHE[t] = dict(o or dict(src="no paid data"), fetched_utc=dt.datetime.now(UTC).isoformat())
        time.sleep(60)
def _ensure():
    if not _STARTED[0] and not os.environ.get("PYTEST_CURRENT_TEST"):
        _STARTED[0] = True; threading.Thread(target=_loop, daemon=True, name="pos_live").start()
def seat_view(bk, seat_eq=None):
    """bk = one seat dict from the book (read-only). Returns positions, armed orders and today's closed trades."""
    pos, armed, closed = [], [], []
    for p in (bk.get("positions") or {}).values():
        t = p.get("ticker"); st = p.get("status")
        if st == "armed":
            armed.append(dict(ticker=t, side=p.get("side"), units=p.get("shares"), limit=p.get("entry"), stop=p.get("stop"), target=p.get("target"),
                              risk_aud=p.get("risk_aud"), risk_pct=p.get("risk_pct"), grade=p.get("grade"), armed_utc=p.get("armed_utc"), note=p.get("exec_note")))
            continue
        if st != "open": continue
        _WANT.add((t, _us(p)))
        sg = -1 if str(p.get("side")).upper() == "SHORT" else 1; fill = _f(p.get("fill")) or _f(p.get("entry")); mark = _f(p.get("mark")); sh = _f(p.get("shares")) or 0
        ntl = _f(p.get("mark_notional_aud")); fx = (ntl / (mark * sh)) if ntl and mark and sh else None
        gross = (mark - fill) * sh * sg * fx if fx and fill and mark else None
        fees = (_f(p.get("cost_aud")) or 0) + ((ntl or 0) * (_f(p.get("cost_rt_rate")) or 0) / 2)
        net = round(gross - fees, 2) if gross is not None else None
        r0 = _f(p.get("r0")) or (abs(fill - _f(p.get("initial_stop") or p.get("stop"))) if fill and p.get("stop") else None)
        with _LOCK: lv = dict(_CACHE.get(t) or {})
        pos.append(dict(ticker=t, name=p.get("name") or _NAMES.get(t), side=p.get("side"), units=sh, notional_aud=ntl, entry=fill, live=mark,
                        mark_utc=p.get("mark_utc"), stop=p.get("stop"), target=p.get("target"), upnl_net_aud=net, fees_aud=round(fees, 2),
                        pct=(round((mark / fill - 1) * 100 * sg, 2) if fill and mark else None), R=(round((mark - fill) * sg / r0, 2) if r0 and mark and fill else None),
                        hours_in=_age_h(p.get("filled_utc")), rail=p.get("rail_state"), live_data=lv or dict(src="warming up (first fetch within ~1 min)")))
    for c in (bk.get("closed") or []):
        if _today_wita(c.get("closed_utc")):
            closed.append(dict(ticker=c.get("ticker"), side=c.get("side"), status=c.get("status"), entry=c.get("fill") or c.get("entry"), exit=c.get("exit") or c.get("exit_px"),
                               pnl_aud=next((c.get(k) for k in ("net_aud", "pnl_aud", "realised_aud") if c.get(k) is not None), None), R=c.get("R") or c.get("r_multiple"), why=(c.get("exit_reason") or c.get("why") or "")[:120], closed_utc=c.get("closed_utc")))
    _ensure()
    return dict(positions=pos, armed=armed, closed_today=closed)
