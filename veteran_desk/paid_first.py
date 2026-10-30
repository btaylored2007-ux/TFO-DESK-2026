"""tfo_datalayer_20261030 step 2 (Brett HARD RULE 20:59 WITA): route yfinance DAILY history/download through the paid data layer.
install() patches yf.Ticker.history and yf.download so interval='1d' requests are served from EODHD (veteran_desk/data_layer.py)
first, with today's in-progress bar from EODHD real-time (like Yahoo), and fall through to the ORIGINAL yfinance call on any miss
(crypto/futures/unknown symbol, empty EODHD, breaker open, unsupported args). Intraday intervals stay on the existing paths
(US live quotes are Massive-first in book.live_px). Frames carry attrs['src']='eodhd'. Disable: TFO_PAID_FIRST=0.
Chained under yf_guard (429 backoff/cache) so the Yahoo fallback keeps its protection."""
import datetime as dt, os, re
_TZ = {"AU": "Australia/Sydney", "LSE": "Europe/London", "IL": "Europe/London", "TO": "America/Toronto", "V": "America/Toronto", "HK": "Asia/Hong_Kong",
       "TSE": "Asia/Tokyo", "KO": "Asia/Seoul", "KQ": "Asia/Seoul", "TW": "Asia/Taipei", "TWO": "Asia/Taipei", "NSE": "Asia/Kolkata", "BSE": "Asia/Kolkata",
       "NZ": "Pacific/Auckland", "SW": "Europe/Zurich", "XETRA": "Europe/Berlin", "F": "Europe/Berlin", "PA": "Europe/Paris", "AS": "Europe/Amsterdam",
       "BR": "Europe/Brussels", "MC": "Europe/Madrid", "MI": "Europe/Rome", "ST": "Europe/Stockholm", "CO": "Europe/Copenhagen", "HE": "Europe/Helsinki",
       "OL": "Europe/Oslo", "LS": "Europe/Lisbon", "VI": "Europe/Vienna", "IR": "Europe/Dublin", "SHG": "Asia/Shanghai", "SHE": "Asia/Shanghai", "SG": "Asia/Singapore"}
_OPEN = {"America/New_York": (9, 30), "America/Toronto": (9, 30), "Australia/Sydney": (10, 0), "Europe/London": (8, 0), "Asia/Hong_Kong": (9, 30),
         "Asia/Tokyo": (9, 0), "Asia/Seoul": (9, 0), "Asia/Kolkata": (9, 15), "Pacific/Auckland": (10, 0)}   # else 09:00 local
_PER = {"1d": 1, "5d": 7, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": 365 * 30}
def enabled(): return os.environ.get("TFO_PAID_FIRST", "1") != "0"
def _start(period, start):
    today = dt.date.today()
    if start: return str(start)[:10]
    p = str(period or "1mo")
    if p == "ytd": return dt.date(today.year, 1, 1).isoformat()
    if p in _PER: return (today - dt.timedelta(days=_PER[p])).isoformat()
    m = re.fullmatch(r"(\d+)(d|mo|y)", p)
    if not m: return None
    n = int(m.group(1)); return (today - dt.timedelta(days=n * {"d": 1, "mo": 31, "y": 366}[m.group(2)])).isoformat()
def frame(sym, period=None, start=None, end=None, auto_adjust=True, actions=True, get=None, rt_get=None):
    """EODHD daily frame shaped like yfinance Ticker.history, or None (caller then uses Yahoo)."""
    import pandas as pd
    from veteran_desk import data_layer as D
    code = D.eodhd_symbol(sym); s0 = _start(period, start)
    if not code or not s0 or code.endswith(".FOREX"): return None   # FX stays OANDA/Yahoo
    q = {"period": "d", "from": s0}
    if end: q["to"] = str(end)[:10]
    js = D.call("eod", "/eod/" + code, q, get)
    if not isinstance(js, list) or not js: return None
    rows = []
    for r in js:
        try:
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close")); ac = float(r.get("adjusted_close") or c)
            if c > 0: rows.append([str(r["date"])[:10], o, h, l, c, ac, float(r.get("volume") or 0)])
        except Exception: continue
    if not rows: return None
    tzn = _TZ.get(code.rsplit(".", 1)[1], "America/New_York")
    if not end:   # Yahoo includes today's in-progress session bar; mirror it from EODHD real-time (delayed <=20m) if newer than the last EOD bar
        try:
            rt = D.call("rt", "/real-time/" + code, {}, rt_get or get)
            import zoneinfo
            ts = dt.datetime.fromtimestamp(int(rt["timestamp"]), zoneinfo.ZoneInfo(tzn)); d = ts.date().isoformat(); c = float(rt["close"])
            if d > rows[-1][0] and c > 0 and ts.weekday() < 5 and (ts.hour, ts.minute) >= _OPEN.get(tzn, (9, 0)):   # no pre-market bar (Yahoo has none)
                rows.append([d, float(rt.get("open") or c), float(rt.get("high") or c), float(rt.get("low") or c), c, c, float(rt.get("volume") or 0)])
        except Exception: pass
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("Date"))).tz_localize(tzn); df.index.name = "Date"
    if auto_adjust:
        f = df["Adj Close"] / df["Close"]
        for k in ("Open", "High", "Low", "Close"): df[k] = df[k] * f
        df = df.drop(columns=["Adj Close"])
    if actions: df["Dividends"] = 0.0; df["Stock Splits"] = 0.0
    df.attrs["src"] = "eodhd"; return df
def install():
    try: import yfinance as yf
    except Exception: return
    if getattr(yf, "_tfo_paid_first", False): return
    _h = yf.Ticker.history; _d = yf.download
    def history(self, *a, **k):
        if enabled() and not a and k.get("interval", "1d") == "1d" and not k.get("prepost") and not k.get("repair"):
            try:
                f = frame(self.ticker, k.get("period", "1mo"), k.get("start"), k.get("end"), k.get("auto_adjust", True), k.get("actions", True))
                if f is not None and len(f): return f
            except Exception: pass
        return _h(self, *a, **k)
    def download(tickers, *a, **k):
        if enabled() and not a and k.get("interval", "1d") == "1d" and not k.get("prepost") and not k.get("repair") and not k.get("actions"):
            try:
                import pandas as pd
                T = tickers.split() if isinstance(tickers, str) else list(tickers)
                if 0 < len(T) <= 60:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(8) as ex:
                        fs = dict(zip(T, ex.map(lambda t: frame(t, k.get("period", "1mo") if not k.get("start") else None, k.get("start"), k.get("end"), k.get("auto_adjust", True), False), T)))
                    if all(f is not None and len(f) for f in fs.values()):
                        for f in fs.values(): f.index = f.index.tz_localize(None)   # yf.download returns naive dates
                        if len(T) == 1 and not k.get("multi_level_index", True): out = fs[T[0]]
                        else:
                            out = pd.concat(fs, axis=1)   # (Ticker, Price)
                            if k.get("group_by", "column") != "ticker": out = out.swaplevel(0, 1, axis=1).sort_index(axis=1, level=0)
                            out.columns.names = ["Ticker", "Price"] if k.get("group_by") == "ticker" else ["Price", "Ticker"]
                        out.attrs["src"] = "eodhd"; return out
            except Exception: pass
        return _d(tickers, *a, **k)
    yf.Ticker.history = history; yf.download = download; yf._tfo_paid_first = True
