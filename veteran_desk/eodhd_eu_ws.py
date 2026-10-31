"""EODHD Cboe Europe real-time websocket. PAPER only. tfo_paidrt_20261031.

Connects wss://ws.eodhistoricaldata.com/ws/eu (trades) and /ws/eu-candles (1m bars).
/ws/eu-quote (consolidated BBO) is off unless EODHD_EU_QUOTES=1, because each stream
counts against the plan's websocket symbol cap.

Key: $EODHD_API_KEY / $TFO_EODHD_KEY or ~/.tfo_secrets/eodhd.env. The token is placed
only on the websocket URL inside this process and is never printed, logged, or written
to state/eodhd_eu/.

Writes (atomic):
  state/eodhd_eu/last.json    last trade/quote per desk symbol
  state/eodhd_eu/health.json  heartbeat (authorized, streams, last message)
1m candles also go to data/intraday/bars.sqlite (see intraday_store.py).

Readers (book.live_px, paid_feeds.fetch_quote, data_layer.quote, pos_live) use this
tape when health()["healthy"] and the symbol's print is fresh. Otherwise they leave
the symbol on the existing fallback (FMP for .L, Yahoo elsewhere).

  python3 -m veteran_desk.eodhd_eu_ws            # run until the socket drops (launchd restarts)
  python3 -m veteran_desk.eodhd_eu_ws --status   # health JSON, no secrets, no network
  python3 -m veteran_desk.eodhd_eu_ws --symbols  # desk symbols that would be subscribed
"""
import datetime as dt, json, os, sys, threading, time, urllib.parse
UTC = dt.timezone.utc
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(BASE, "state", "eodhd_eu")
LAST = os.path.join(DIR, "last.json")
HEALTH = os.path.join(DIR, "health.json")
SECRET = os.path.expanduser("~/.tfo_secrets/eodhd.env")
FRESH_S = int(os.environ.get("EODHD_EU_FRESH_S", "120"))
ALIVE_S = int(os.environ.get("EODHD_EU_ALIVE_S", "45"))
CLOSED_MAX_S = int(os.environ.get("EODHD_EU_CLOSED_MAX_S", str(72 * 3600)))
HOST = "wss://ws.eodhistoricaldata.com/ws/"
# Yahoo suffix -> (tz, open, close, ccy, exchangeName, feed_delay venue). Prices stay in the venue's quoting units (.L is pence).
SESS = {
    "L": ("Europe/London", "08:00", "16:30", "GBp", "LSE", "LSE"),
    "IL": ("Europe/London", "08:00", "16:30", "GBp", "LSE", "LSE"),
    "DE": ("Europe/Berlin", "09:00", "17:30", "EUR", "XETRA", "XETRA"),
    "F": ("Europe/Berlin", "08:00", "20:00", "EUR", "FSE", "XETRA"),
    "PA": ("Europe/Paris", "09:00", "17:30", "EUR", "Euronext", "Euronext"),
    "AS": ("Europe/Amsterdam", "09:00", "17:30", "EUR", "Euronext", "Euronext"),
    "BR": ("Europe/Brussels", "09:00", "17:30", "EUR", "Euronext", "Euronext"),
    "LS": ("Europe/Lisbon", "08:00", "16:30", "EUR", "Euronext", "Euronext"),
    "VI": ("Europe/Vienna", "09:00", "17:30", "EUR", "Euronext", "Euronext"),
    "IR": ("Europe/Dublin", "08:00", "16:30", "EUR", "Euronext", "Euronext"),
    "MC": ("Europe/Madrid", "09:00", "17:30", "EUR", "BME", "BME"),
    "MI": ("Europe/Rome", "09:00", "17:30", "EUR", "MILAN", "MILAN"),
    "SW": ("Europe/Zurich", "09:00", "17:30", "CHF", "SIX", "SIX"),
    "ST": ("Europe/Stockholm", "09:00", "17:30", "SEK", "NORDIC", "NORDIC"),
    "CO": ("Europe/Copenhagen", "09:00", "17:00", "DKK", "NORDIC", "NORDIC"),
    "HE": ("Europe/Helsinki", "10:00", "18:30", "EUR", "NORDIC", "NORDIC"),
    "OL": ("Europe/Oslo", "09:00", "16:20", "NOK", "NORDIC", "NORDIC"),
    "WA": ("Europe/Warsaw", "09:00", "16:50", "PLN", "WAR", "Euronext"),
}
EU_EX = {"LSE", "IL", "XETRA", "F", "PA", "AS", "BR", "LS", "VI", "IR", "MC", "MI", "SW", "ST", "CO", "HE", "OL", "WAR"}
PROBES = ["HSBA.L", "SHEL.L", "BP.L", "SAP.DE", "SIE.DE", "BNP.PA", "MC.PA", "ASML.AS", "SAN.MC", "ISP.MI", "NESN.SW", "NOVO-B.CO", "VOLV-B.ST"]
_REV = None


def _key():
    k = os.environ.get("EODHD_API_KEY") or os.environ.get("TFO_EODHD_KEY")
    if k: return k.strip()
    try:
        for ln in open(SECRET):
            s = ln.strip()
            if s.startswith("EODHD_API_KEY=") or s.startswith("TFO_EODHD_KEY=") or s.startswith("export EODHD_API_KEY=") or s.startswith("export TFO_EODHD_KEY="):
                return s.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None


def _url(stream, key):
    """Websocket URL. Callers must not log the return value."""
    return HOST + stream + "?api_token=" + urllib.parse.quote(key)


def _suf(sym):
    s = str(sym or "").upper()
    return s.rsplit(".", 1)[-1] if "." in s else ""


def _rev():
    global _REV
    if _REV is None:
        from veteran_desk import data_layer as D
        _REV = {}
        for suf, ex in D.EX.items():
            if ex and ex not in _REV: _REV[ex] = suf
    return _REV


def eodhd_of(sym):
    try:
        from veteran_desk import data_layer as D
        return D.eodhd_symbol(sym)
    except Exception: return None


def is_eu(sym):
    code = eodhd_of(sym)
    return bool(code and "." in code and code.rsplit(".", 1)[1] in EU_EX)


def to_desk(code):
    code = str(code or "").upper()
    if "." not in code: return code
    base, ex = code.rsplit(".", 1)
    suf = _rev().get(ex)
    return (base + "." + suf) if suf else code


def session_open(sym, now):
    spec = SESS.get(_suf(sym))
    if not spec: return True
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo(spec[0]))
    if local.weekday() >= 5: return False
    oh, om = (int(x) for x in spec[1].split(":")); ch, cm = (int(x) for x in spec[2].split(":"))
    hm = local.hour * 60 + local.minute
    return oh * 60 + om <= hm < ch * 60 + cm


def classify(msg):
    """One websocket JSON object -> ('trade'|'quote'|'candle'|'auth_ok'|'auth_fail'|'status'|'ignore', msg)."""
    if not isinstance(msg, dict): return "ignore", msg
    if "status" in msg and "status_code" not in msg:
        return ("auth_fail", msg) if msg.get("status") in (401, 403, 422) else ("status", msg)
    if "status_code" in msg:
        code = msg.get("status_code")
        if code == 200: return "auth_ok", msg
        if code in (401, 403): return "auth_fail", msg
        return "status", msg
    if "bp" in msg or "ap" in msg: return "quote", msg
    if "o" in msg and "h" in msg and "s" in msg: return "candle", msg
    if "p" in msg and "s" in msg: return "trade", msg
    return "ignore", msg


def _f(v):
    try:
        x = float(v)
        return x if x == x and x > 0 and abs(x) != float("inf") else None
    except (TypeError, ValueError): return None


def _ms(v):
    try: return int(v)
    except (TypeError, ValueError): return None


class Tape:
    """In-memory tape the socket threads mutate. Flush persists it. No secrets."""
    def __init__(self):
        self.quotes = {}; self.candles = {}; self.ok = {}; self.dead = []; self.auth_failed = False
        self.symbols = []; self.last_msg = None; self.lock = threading.Lock(); self.socks = {}

    def ingest(self, stream, raw, now=None):
        now = now or dt.datetime.now(UTC)
        try: msg = json.loads(raw) if isinstance(raw, str) else raw
        except Exception: return []
        frames = msg if isinstance(msg, list) else [msg]
        out = []
        for fr in frames:
            kind, m = classify(fr)
            out.append(kind)
            with self.lock:
                if kind == "auth_ok": self.ok[stream] = True
                elif kind == "auth_fail": self.auth_failed = True; self.ok[stream] = False
                elif kind == "trade": self._trade(m, now)
                elif kind == "quote": self._quote(m, now)
                elif kind == "candle": self._candle(m, now)
                if kind in ("trade", "quote", "candle"): self.last_msg = now
        return out

    def _trade(self, m, now):
        px, ts = _f(m.get("p")), _ms(m.get("t"))
        if px is None or ts is None: return
        desk = to_desk(m.get("s"))
        q = self.quotes.setdefault(desk, {})
        q.update(px=px, ts_ms=ts, ms=m.get("ms") or "open", eodhd=str(m.get("s") or ""), src="eodhd_eu_ws", kind="trade")

    def _quote(self, m, now):
        ts = _ms(m.get("t")); bid, ask = _f(m.get("bp")), _f(m.get("ap"))
        if ts is None or (bid is None and ask is None): return
        desk = to_desk(m.get("s"))
        q = self.quotes.setdefault(desk, {})
        mid = (bid + ask) / 2 if bid and ask else (bid or ask)
        q.update(bid=bid, ask=ask, eodhd=str(m.get("s") or ""), src="eodhd_eu_ws")
        q.setdefault("px", mid); q.setdefault("ts_ms", ts); q.setdefault("ms", "open")
        if q.get("ts_ms", 0) <= ts and m.get("p") is None and "px" in q and q.get("kind") != "trade":
            q["px"] = mid; q["ts_ms"] = ts

    def _candle(self, m, now):
        ts = _ms(m.get("t")); o, h, l, c = _f(m.get("o")), _f(m.get("h")), _f(m.get("l")), _f(m.get("c"))
        if None in (ts, o, h, l, c): return
        desk = to_desk(m.get("s"))
        v = _f(m.get("v")) or 0.0
        self.candles[(desk, ts)] = (desk, "1m", ts, o, h, l, c, v)
        q = self.quotes.setdefault(desk, {})
        if ts >= q.get("ts_ms", 0):
            q.update(px=c, ts_ms=ts, hi=h, lo=l, open=o, eodhd=str(m.get("s") or ""), src="eodhd_eu_ws", kind="candle", ms=q.get("ms") or "open")


def _load(path):
    try: return json.load(open(path))
    except Exception: return {}


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1); os.replace(tmp, path)


def _file_age(path, now):
    try: return (now - dt.datetime.fromtimestamp(os.path.getmtime(path), UTC)).total_seconds()
    except Exception: return None


def _iso(ms):
    try: return dt.datetime.fromtimestamp(ms / 1000.0, UTC).isoformat()
    except Exception: return None


def _state(now=None):
    now = now or dt.datetime.now(UTC)
    meta, last = _load(HEALTH), _load(LAST)
    if not isinstance(last, dict): last = {}
    age = _file_age(HEALTH, now)
    alive = bool(meta.get("authorized")) and age is not None and 0 <= age <= ALIVE_S
    return dict(now=now, meta=meta, last=last, alive=alive, file_age=age)


def _row(st, sym):
    last = st["last"]
    q = last.get(sym) or last.get(str(sym).upper())
    if not q:
        code = eodhd_of(sym)
        q = last.get(to_desk(code)) if code else None
    return q if isinstance(q, dict) else None


def _usable(sym, q, st):
    if not st["alive"] or not q or not _f(q.get("px")): return False
    ts = _ms(q.get("ts_ms"))
    if ts is None: return False
    age = st["now"].timestamp() - ts / 1000.0
    if age < -5: return False
    if age <= FRESH_S: return True
    if session_open(sym, st["now"]): return False
    return age <= CLOSED_MAX_S


def health(now=None):
    """Reader-facing health. healthy means the consumer heartbeat is fresh AND at least one EU print is usable."""
    st = _state(now); venues, n = set(), 0; newest = None
    for sym, q in st["last"].items():
        if not isinstance(q, dict) or not _usable(sym, q, st): continue
        n += 1; venues.add(SESS.get(_suf(sym), (None, None, None, None, None, None))[5])
        ts = _ms(q.get("ts_ms"))
        if ts and (newest is None or ts > newest): newest = ts
    venues.discard(None)
    why = None
    if not st["meta"].get("authorized"): why = "not authorized"
    elif not st["alive"]: why = "consumer heartbeat stale"
    elif n == 0: why = "no usable EU quote"
    return dict(paper_only=True, src="eodhd_cboe_europe_ws", authorized=bool(st["meta"].get("authorized")),
                alive=st["alive"], healthy=bool(st["alive"] and n > 0), why=why, symbols=n,
                venues_live=sorted(venues), age_sec=None if st["file_age"] is None else round(st["file_age"], 1),
                newest_utc=_iso(newest) if newest else None, streams=st["meta"].get("streams") or {},
                last_msg_utc=st["meta"].get("last_msg_utc"), fresh_s=FRESH_S)


def live_px(sym, now=None):
    """book.live_px shape, or None when this symbol should stay on the fallback."""
    if not is_eu(sym): return None
    st = _state(now); q = _row(st, sym)
    if not q or not _usable(sym, q, st): return None
    ts = dt.datetime.fromtimestamp(q["ts_ms"] / 1000.0, UTC)
    px = float(q["px"])
    return dict(last=px, hi=float(q.get("hi") or px), lo=float(q.get("lo") or px), open=float(q.get("open") or px),
                asof=ts.isoformat(), window_start=ts.isoformat(), src="eodhd_eu_ws", bid=q.get("bid"), ask=q.get("ask"),
                ms=q.get("ms"))


def _periods(sym, now_epoch):
    spec = SESS.get(_suf(sym))
    if not spec: return None
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(spec[0]); day = dt.datetime.fromtimestamp(now_epoch, tz).date()
    def at(hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz).timestamp()
    if day.weekday() >= 5:
        z = {"start": 0, "end": 0}; return {"pre": z, "regular": z, "post": z}, spec
    z = {"start": 0, "end": 0}
    return {"pre": z, "regular": {"start": at(spec[1]), "end": at(spec[2])}, "post": z}, spec


def chart_quote(sym, now=None):
    """tfo_equity_refresh.parse_chart quote from the tape, or None (caller falls back)."""
    if not is_eu(sym): return None
    st = _state(now); q = _row(st, sym)
    if not q or not _usable(sym, q, st): return None
    per, spec = _periods(sym, st["now"].timestamp())
    if not per: return None
    ts = q["ts_ms"] / 1000.0; px = float(q["px"])
    payload = {"chart": {"result": [{"meta": {"currency": spec[3], "regularMarketTime": ts, "regularMarketPrice": px,
                                               "currentTradingPeriod": per, "exchangeName": spec[4], "exchangeTimezoneName": spec[0]},
                                      "timestamp": [ts], "indicators": {"quote": [{"close": [px]}]}}]}}
    import tfo_equity_refresh as ter
    fetched = st["now"].astimezone(UTC).isoformat().replace("+00:00", "Z")
    out = ter.parse_chart(sym, payload, fetched)
    out.update(source="EODHD Cboe Europe websocket (real-time)", url=None, paid_feed=True, feed="eodhd_eu_ws")
    return out


def subscribe_symbols():
    """Held EU names first, then liquid probes, capped by EODHD_EU_MAX_SYMBOLS (default 24)."""
    cap = int(os.environ.get("EODHD_EU_MAX_SYMBOLS", "24")); held, probes = [], []
    def add(bucket, sym):
        if sym and is_eu(sym) and sym not in bucket and sym not in held: bucket.append(sym)
    for rel in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
        try: book = json.load(open(os.path.join(BASE, rel)))
        except Exception: continue
        seats = book.get("seats") or {}
        blocks = list(seats.values()) if isinstance(seats, dict) else []
        if not blocks and isinstance(book.get("positions"), dict): blocks = [book]
        for B in blocks:
            for p in (B.get("positions") or {}).values():
                if isinstance(p, dict) and p.get("ticker"): add(held, str(p["ticker"]).upper())
    for s in PROBES: add(probes, s)
    out = []
    for s in held + probes:
        if s not in out: out.append(s)
        if len(out) >= cap: break
    return out


def subscribe_codes(desk_syms=None):
    return [eodhd_of(s) for s in (desk_syms if desk_syms is not None else subscribe_symbols()) if eodhd_of(s)]


def flush(tape, now=None):
    """Persist quotes, heartbeat, and any 1m candles. Returns the health meta dict. Never includes the key."""
    now = now or dt.datetime.now(UTC)
    with tape.lock:
        quotes = {k: dict(v) for k, v in tape.quotes.items()}
        candles = list(tape.candles.items())
        ok = dict(tape.ok); last_msg = tape.last_msg; symbols = list(tape.symbols)
    prev = _load(LAST)
    if isinstance(prev, dict):
        for k, v in prev.items():
            if k not in quotes and isinstance(v, dict): quotes[k] = v
    _dump(LAST, quotes)
    meta = dict(paper_only=True, src="eodhd_cboe_europe_ws", authorized=bool(ok.get("eu") or ok.get("eu-candles")),
                streams={s: ("up" if ok.get(s) else "down") for s in ("eu", "eu-candles", "eu-quote")},
                last_msg_utc=last_msg.isoformat() if last_msg else None, symbols_subscribed=len(symbols),
                quotes=len(quotes), flushed_utc=now.isoformat())
    _dump(HEALTH, meta)
    if candles:
        try:
            from veteran_desk import intraday_store as IB
            IB.put([v for _, v in candles], "eodhd_eu_ws")
            IB.roll_symbols([v[0] for _, v in candles], "eodhd_eu_ws_roll")
            with tape.lock:
                for k, _v in candles: tape.candles.pop(k, None)
        except Exception as e:
            print("candle store: %s" % type(e).__name__, flush=True)
            with tape.lock:
                for k, v in candles: tape.candles.setdefault(k, v)
    return meta


def _send(ws, codes):
    if codes: ws.send(json.dumps({"action": "subscribe", "symbols": ",".join(codes)}))


def serve():
    key = _key()
    if not key:
        print("ERROR eodhd_eu_ws: key missing (EODHD_API_KEY or ~/.tfo_secrets/eodhd.env)", flush=True)
        return 2
    try: import websocket  # websocket-client, framework interpreter
    except ImportError:
        print("ERROR eodhd_eu_ws: websocket-client is not installed for %s" % sys.executable, flush=True)
        return 2
    tape = Tape(); tape.symbols = subscribe_symbols(); codes = subscribe_codes(tape.symbols)
    if not codes:
        print("ERROR eodhd_eu_ws: no EU/LSE symbols to subscribe", flush=True)
        return 2
    streams = ["eu", "eu-candles"]
    if os.environ.get("EODHD_EU_QUOTES") == "1": streams.append("eu-quote")
    print("eodhd_eu_ws subscribe %d symbols on %s" % (len(codes), ",".join(streams)), flush=True)
    prev = _load(LAST)
    if isinstance(prev, dict): tape.quotes.update({k: v for k, v in prev.items() if isinstance(v, dict)})

    def run(stream):
        def on_open(ws):
            tape.socks[stream] = ws; _send(ws, codes)
        def on_message(ws, raw):
            try: tape.ingest(stream, raw)
            except Exception: print("bad frame", stream, flush=True)
            if tape.auth_failed: os._exit(2)
        def on_error(ws, err):
            print("ws error", stream, type(err).__name__, flush=True)  # never str(err): it can embed the URL
        app = websocket.WebSocketApp(_url(stream, key), on_open=on_open, on_message=on_message, on_error=on_error)
        app.run_forever(ping_interval=20, ping_timeout=10)
        tape.dead.append(stream)

    def resub():
        seen = set(codes)
        while True:
            time.sleep(60)
            try:
                tape.symbols = subscribe_symbols(); want = subscribe_codes(tape.symbols); add = [c for c in want if c not in seen]
                if not add: continue
                seen.update(add)
                for ws in list(tape.socks.values()):
                    try: _send(ws, add)
                    except Exception: pass
                print("eodhd_eu_ws added %d symbols" % len(add), flush=True)
            except Exception: pass

    threading.Thread(target=resub, daemon=True).start()
    for s in streams: threading.Thread(target=run, args=(s,), daemon=True).start()
    while True:
        time.sleep(1)
        try: flush(tape)
        except Exception as e: print("flush: %s" % type(e).__name__, flush=True)
        if tape.auth_failed:
            print("ERROR eodhd_eu_ws: auth rejected", flush=True); return 2
        if tape.dead:
            print("ERROR eodhd_eu_ws: stream closed %s" % ",".join(tape.dead), flush=True); return 1


def status():
    h = health(); json.dump({k: h[k] for k in h if k != "streams"}, sys.stdout, indent=1); sys.stdout.write("\n"); return 0 if h.get("authorized") or not os.path.exists(HEALTH) else 0


if __name__ == "__main__":
    if "--status" in sys.argv: sys.exit(status())
    if "--symbols" in sys.argv:
        syms = subscribe_symbols(); json.dump(dict(n=len(syms), symbols=syms, codes=subscribe_codes(syms)), sys.stdout, indent=1); sys.stdout.write("\n"); sys.exit(0)
    sys.exit(serve())
