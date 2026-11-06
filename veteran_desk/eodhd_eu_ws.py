"""EODHD Cboe Europe real-time websocket. PAPER only. tfo_paidrt_20261031; quota-adaptive since eufeed_20261103.

Connects wss://ws.eodhistoricaldata.com/ws/eu (trades) and /ws/eu-candles (1m bars).
/ws/eu-quote (consolidated BBO) is off unless EODHD_EU_QUOTES=1, because each stream
counts against the plan's websocket symbol cap.

Symbol quota (eufeed_20261103; measured on this key 2026-11-03 16:28-16:37 WITA):
  * the cap is per KEY and shared by every stream and every connection on the key, so another collector on
    the same key (e.g. the Mac's old one) eats into it. At 16:30 WITA 20 symbols were free: the 21st got 422.
  * a subscribe that would pass the cap is answered within ~0.2 s by
    {"status_code":422,"message":"Symbols limit reached"} and NONE of it is applied; the set already accepted
    keeps streaming.
  * an accepted subscribe gets no reply: each symbol's last trade arrives at once, then live prints.
    Unsubscribe is silent too, and a freed slot can be taken again at once (by any stream).
  Quota below therefore sends one request at a time and treats "no 422 within ACK_S" as accepted. 'eu'
  (trades = live marks) fills first, held book names first, liquid scan probes after; 'eu-candles' only gets
  what is left (0 -> "off: quota"). A 422 steps the request down by STEP, then halves to the exact edge.
  The learned cap is kept in state/eodhd_eu/caps.json (start hint) and probed upward again on start and hourly
  (every 5 min while a held name has no slot) without ever dropping the accepted set. When trades are full and a
  new held name appears, the lowest-ranked probe is swapped out for it.

Key: $EODHD_API_KEY / $TFO_EODHD_KEY or ~/.tfo_secrets/eodhd.env. The token is placed
only on the websocket URL inside this process and is never printed, logged, or written
to state/eodhd_eu/.

Writes (atomic):
  state/eodhd_eu/last.json    last trade/quote per desk symbol
  state/eodhd_eu/health.json  heartbeat (authorized, streams, last message) + quota, healthy, why
  state/eodhd_eu/caps.json    learned symbol cap (start hint for the next run)
1m candles also go to data/intraday/bars.sqlite (see intraday_store.py).

Readers (book.live_px, paid_feeds.fetch_quote, data_layer.quote, pos_live) use this
tape when health()["healthy"] and the symbol's print is fresh. Otherwise they leave
the symbol on the existing fallback (FMP for .L, Yahoo elsewhere).

  python3 -m veteran_desk.eodhd_eu_ws            # run until the socket drops (systemd restarts)
  python3 -m veteran_desk.eodhd_eu_ws --status   # health + learned caps JSON, no secrets, no network
  python3 -m veteran_desk.eodhd_eu_ws --symbols  # desk symbols that would be subscribed (held first)
"""
import datetime as dt, json, os, sys, threading, time, urllib.parse
UTC = dt.timezone.utc
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(BASE, "state", "eodhd_eu")
LAST = os.path.join(DIR, "last.json")
HEALTH = os.path.join(DIR, "health.json")
CAPS = os.path.join(DIR, "caps.json")
SECRET = os.path.expanduser("~/.tfo_secrets/eodhd.env")
FRESH_S = int(os.environ.get("EODHD_EU_FRESH_S", "120"))
ALIVE_S = int(os.environ.get("EODHD_EU_ALIVE_S", "45"))
CLOSED_MAX_S = int(os.environ.get("EODHD_EU_CLOSED_MAX_S", str(72 * 3600)))
# eufeed_20261103: a name that holds a live trades slot but has not traded on Cboe for a while keeps its last trade as the
# mark up to QUIET_S (the stream would have sent a newer print). Past ~15 min Yahoo's 15-20 min delayed print can be newer.
QUIET_S = int(os.environ.get("EODHD_EU_QUIET_S", "900"))
ACK_S = float(os.environ.get("EODHD_EU_ACK_S", "3"))                  # a 422 comes back in ~0.2 s; silence = accepted
STEP = max(1, int(os.environ.get("EODHD_EU_STEP", "5")))               # step-down / upward probe size
REPROBE_S = int(os.environ.get("EODHD_EU_REPROBE_S", "3600"))          # look for freed quota hourly ...
REPROBE_SHORT_S = int(os.environ.get("EODHD_EU_REPROBE_SHORT_S", "300"))   # ... every 5 min while a held name has no slot
PLAN_TOTAL = int(os.environ.get("EODHD_EU_PLAN_TOTAL", "50"))          # what the plan says; the free pool is learned
# The Mac's old collector (same key) pushes its own health here via data_sync; used only to NAME who holds the rest of the quota.
MAC_MIRROR = os.environ.get("EODHD_EU_MAC_MIRROR", "/opt/tfo/data/state/eodhd_eu/health.json")
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
CLOSED_ST = {"closed", "cancelled", "canceled", "expired", "rejected", "void", "exited", "stopped", "stopped_out"}
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


def _limit_msg(m):
    return "limit" in str((m or {}).get("message") or "").lower()


def classify(msg):
    """One websocket JSON object -> ('trade'|'quote'|'candle'|'auth_ok'|'auth_fail'|'status'|'ignore', msg)."""
    if not isinstance(msg, dict): return "ignore", msg
    if "status" in msg and "status_code" not in msg:
        st = msg.get("status")   # eufeed_20261103: a 422 'Symbols limit reached' is a quota answer, not an auth failure
        return ("auth_fail", msg) if st in (401, 403) or (st == 422 and not _limit_msg(msg)) else ("status", msg)
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
        self.symbols = []; self.last_msg = None; self.last_status = None; self.lock = threading.Lock(); self.socks = {}
        self.statuses = []; self.last_by_stream = {}; self.quota = None; self.held = []   # eufeed_20261103

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
                elif kind == "status":
                    msg = str((m or {}).get("message") or "")[:120]
                    code = (m or {}).get("status_code", (m or {}).get("status"))
                    self.last_status = {"code": code, "message": msg, "stream": stream, "utc": now.isoformat()}
                    self.statuses.append((stream, code, msg, now.timestamp()))   # Quota reads these (422 = that subscribe was refused)
                    del self.statuses[:-50]
                    if code == 422 or "limit" in msg.lower():
                        print("eodhd_eu_ws STATUS", stream, code, msg, flush=True)
                if kind in ("trade", "quote", "candle"): self.last_msg = now; self.last_by_stream[stream] = now
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


class Quota:
    """Fit the subscriptions into the key's real EODHD websocket symbol quota (eufeed_20261103). Pure logic, no sockets:
    serve() feeds it 422 answers (on_reply) and calls tick(send, ready, now) about once a second.

    One request is in flight at a time across all streams (the pool is shared). A subscribe with no 422 inside ACK_S is
    accepted (EODHD sends no ack). On a 422 nothing was applied: hi (the smallest TOTAL refused) is pinned and the next
    request steps down by STEP, then halves, until the edge is exact. Streams fill in order - the first ('eu', trades =
    live marks) before any other, held names first in the wanted list - so 'eu-candles' only gets what trades leave.
    A probe upward (forget hi, add STEP more) runs on start (after the caps.json hint is used up), hourly, and every
    REPROBE_SHORT_S while a held name has no trades slot; a refused probe changes nothing that was accepted."""

    def __init__(self, streams, max_n, hint=None, plan_total=None, step=None, ack_s=None, reprobe_s=None, short_s=None, swap_gap_s=60.0):
        self.streams = list(streams); self.max_n = max(0, int(max_n))
        self.acc = {s: [] for s in self.streams}            # accepted EODHD codes per stream, in subscribe order
        self.want, self.held = [], []
        self.inflight = None                                # dict(stream, action, codes, t)
        self.hi = None                                      # smallest total (all streams) EODHD refused; None = no edge known
        self.hint = hint if isinstance(hint, int) and not isinstance(hint, bool) and hint >= 0 else None
        self.next_k = None                                  # size limit for the next subscribe after a 422
        self.plan_total = PLAN_TOTAL if plan_total is None else int(plan_total)
        self.step = max(1, int(STEP if step is None else step))
        self.ack_s = ACK_S if ack_s is None else float(ack_s)
        self.reprobe_s = REPROBE_S if reprobe_s is None else reprobe_s
        self.short_s = REPROBE_SHORT_S if short_s is None else short_s
        self.swap_gap_s = swap_gap_s
        self.next_probe = None; self.swap_t = None
        self.n_422 = 0; self.last_422 = None; self.learned = None
        self.changed = False; self.idle = False; self.notes = []; self._out = []

    def used(self): return sum(len(v) for v in self.acc.values())

    def cap(self):
        """Symbols EODHD lets this key use right now (all streams together) once a 422 pinned the edge, else None."""
        return None if self.hi is None else max(0, self.hi - 1)

    def set_want(self, want, held=()):
        w = []
        for c in want or []:
            if c and c not in w: w.append(c)
        self.want = w; self.held = [c for c in (held or []) if c in w]

    def wanted(self): return self.want[: self.max_n]

    def missing(self, s): return [c for c in self.wanted() if c not in self.acc[s]]

    def room(self, s):
        r = self.max_n - len(self.acc[s])
        if self.hi is not None: r = min(r, self.hi - 1 - self.used())
        return max(0, r)

    def held_missing(self):
        return [c for c in self.held if c not in self.acc[self.streams[0]]]

    def _note(self, now, txt):
        self.notes.append((now, txt)); del self.notes[:-20]; self._out.append(txt)

    def drain(self):
        out, self._out = self._out, []
        return out

    def on_reply(self, stream, code, message, now):
        """A status frame from a socket. Only a 422 / 'limit' answers a subscribe. Returns True when it refused ours."""
        if code != 422 and "limit" not in str(message or "").lower(): return False
        self.n_422 += 1; self.last_422 = now
        f = self.inflight
        if not f or f["stream"] != stream or f["action"] != "subscribe":
            self._note(now, "422 on %s with no subscribe of ours in flight (ignored)" % stream); return False
        self.inflight = None
        k = len(f["codes"]); total = self.used() + k
        self.hi = total if self.hi is None else min(self.hi, total)
        self.next_k = k - self.step if k > self.step else k // 2
        self.learned = now; self.changed = True
        gap = bool(self.held_missing()) or not self.acc[self.streams[0]]
        self.next_probe = now + (self.short_s if gap else self.reprobe_s)
        self._note(now, "422 Symbols limit reached on %s for %d more (would be %d in total): cap now %d" % (stream, k, total, self.hi - 1))
        return True

    def _settle(self, now):
        f, self.inflight = self.inflight, None; s = f["stream"]
        if f["action"] == "subscribe":
            for c in f["codes"]:
                if c not in self.acc[s]: self.acc[s].append(c)
            if self.hi is not None and self.used() >= self.hi: self.hi = None   # the pool grew past the old edge
            gap = (self.hi - 1 - self.used()) if self.hi is not None else 0
            self.next_k = (gap + 1) // 2 if gap > 0 else None
            self._note(now, "accepted %d on %s (live %d: %s)" % (len(f["codes"]), s, self.used(), ", ".join("%s %d" % (x, len(v)) for x, v in self.acc.items())))
        else:
            self.acc[s] = [c for c in self.acc[s] if c not in f["codes"]]; self.next_k = None
        self.changed = True

    def _victim(self, s):
        rank = {c: i for i, c in enumerate(self.want)}
        cands = [c for c in self.acc[s] if c not in self.held]
        return max(cands, key=lambda c: (rank.get(c, 10 ** 6), self.acc[s].index(c))) if cands else None

    def _send(self, send, s, action, codes, now, note=None):
        try: send(s, action, list(codes))
        except Exception as e:
            self._note(now, "%s on %s not sent (%s)" % (action, s, type(e).__name__)); return None
        self.inflight = dict(stream=s, action=action, codes=list(codes), t=now)
        msg = note or "%s %d on %s (live %d)" % (action, len(codes), s, self.used())
        self._note(now, msg); return msg

    def _sub(self, send, s, miss, room, now):
        k = min(len(miss), room)
        if self.next_k: k = min(k, self.next_k)                  # stepping down after a 422
        elif self.hi is None and self.hint is not None:          # start from the learned cap, then probe upward a STEP at a time
            left = self.hint - self.used(); k = min(k, left if left > 0 else self.step)
        return self._send(send, s, "subscribe", miss[:k], now) if k > 0 else None

    def tick(self, send, ready, now):
        """At most one request per call. send(stream, action, codes) writes the frame; ready(stream) = socket open and
        authorized. Returns a short note when a request went out, else None."""
        self.idle = False
        if self.inflight is not None:
            if now - self.inflight["t"] < self.ack_s: return None
            self._settle(now)
        if self.hi is not None and self.next_probe is not None and now >= self.next_probe:
            self.hi = None; self.hint = self.used(); self.next_k = None; self.next_probe = None
            self._note(now, "probing for freed quota above %d" % self.used())
        s0 = self.streams[0]
        if not ready(s0): return None
        miss0, r0 = self.missing(s0), self.room(s0)
        if miss0 and r0 > 0: return self._sub(send, s0, miss0, r0, now)
        hm = [c for c in miss0 if c in self.held]
        if hm and r0 == 0 and (self.swap_t is None or now - self.swap_t >= self.swap_gap_s):
            v = self._victim(s0)
            if v:
                self.swap_t = now
                return self._send(send, s0, "unsubscribe", [v], now, note="trades full: swap probe %s out for held %s" % (v, hm[0]))
        for s in self.streams[1:]:
            if not ready(s): continue
            miss, r = self.missing(s), self.room(s)
            if miss and r > 0: return self._sub(send, s, miss, r, now)
        self.idle = True
        return None

    def snapshot(self, name=lambda c: c):
        s0 = self.streams[0]
        return dict(live=self.used(), per_stream={s: len(v) for s, v in self.acc.items()}, cap=self.cap(), pinned=self.hi is not None,
                    max_per_stream=self.max_n, plan_total=self.plan_total, wanted=len(self.wanted()),
                    held_live=[name(c) for c in self.held if c in self.acc[s0]], held_no_slot=[name(c) for c in self.held_missing()],
                    settling=self.inflight is not None, n_422=self.n_422,
                    last_422_utc=_iso(self.last_422 * 1000) if self.last_422 else None,
                    next_probe_utc=_iso(self.next_probe * 1000) if self.next_probe else None,
                    notes=[t for _, t in self.notes[-6:]])


def _stream_word(q, ok, s):
    """Honest per-stream state for health.json: up | subscribing | waiting | off: quota | off | down."""
    if s not in q.streams: return "off"            # not enabled (eu-quote unless EODHD_EU_QUOTES=1)
    if not ok.get(s): return "down"
    if q.acc.get(s): return "up"
    if q.inflight and q.inflight.get("stream") == s: return "subscribing"
    return "off: quota" if q.room(s) == 0 else "waiting"


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


def load_caps(path=None):
    """Learned total (all streams) from caps.json -> start hint, or None."""
    c = _load(path or CAPS)
    t = c.get("total") if isinstance(c, dict) else None
    return t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None


def save_caps(q, now=None, path=None):
    now = now or dt.datetime.now(UTC); path = path or CAPS
    prev = _load(path); prev = prev if isinstance(prev, dict) else {}
    total = q.cap() if q.hi is not None else q.used()
    doc = dict(paper_only=True, updated_utc=now.isoformat(), total=total, pinned=q.hi is not None,
               per_stream={s: len(v) for s, v in q.acc.items()}, plan_total=q.plan_total, max_per_stream=q.max_n,
               n_422=q.n_422, last_422_utc=_iso(q.last_422 * 1000) if q.last_422 else None,
               note=("EODHD accepted %d symbols in total on this key; one more got 422 Symbols limit reached" % total) if q.hi is not None
                    else ("all %d wanted symbols fit; no EODHD limit hit" % total))
    hist = [h for h in (prev.get("history") or []) if isinstance(h, dict)]
    if prev.get("total") != total or prev.get("pinned") != doc["pinned"]: hist.append(dict(utc=doc["updated_utc"], total=total, pinned=doc["pinned"]))
    doc["history"] = hist[-30:]
    _dump(path, doc); return doc


def _mkstate(now, meta, last, age):
    meta = meta if isinstance(meta, dict) else {}; last = last if isinstance(last, dict) else {}
    alive = bool(meta.get("authorized")) and age is not None and 0 <= age <= ALIVE_S
    sub = meta.get("subscribed")
    live_eu = set(x for x in (sub.get("eu") or []) if isinstance(x, str)) if isinstance(sub, dict) else None
    eu_up = (meta.get("streams") or {}).get("eu") == "up"
    return dict(now=now, meta=meta, last=last, alive=alive, file_age=age, live_eu=live_eu, eu_up=eu_up)


def _state(now=None):
    now = now or dt.datetime.now(UTC)
    return _mkstate(now, _load(HEALTH), _load(LAST), _file_age(HEALTH, now))


def _row(st, sym):
    last = st["last"]
    q = last.get(sym) or last.get(str(sym).upper())
    if not q:
        code = eodhd_of(sym)
        q = last.get(to_desk(code)) if code else None
    return q if isinstance(q, dict) else None


def _age_s(q, now):
    ts = _ms((q or {}).get("ts_ms"))
    return None if ts is None else now.timestamp() - ts / 1000.0


def _usable(sym, q, st):
    if not st["alive"] or not q or not _f(q.get("px")): return False
    ts = _ms(q.get("ts_ms"))
    if ts is None: return False
    age = st["now"].timestamp() - ts / 1000.0
    if age < -5: return False
    if age <= FRESH_S: return True
    if session_open(sym, st["now"]):   # eufeed_20261103: quiet name on a live trades slot -> its last trade is still the newest
        live = st.get("live_eu")
        return bool(live and st.get("eu_up") and str(sym).upper() in live and age <= QUIET_S)
    return age <= CLOSED_MAX_S


def _mins(sec): return "%d min" % max(1, round(sec / 60.0))


def _mac_holder(now):
    """(symbols, streams up, age_s) from the Mac collector's pushed health when it streamed in the last 10 min, else None."""
    try:
        m = json.load(open(MAC_MIRROR)); t = dt.datetime.fromisoformat(str(m.get("last_msg_utc") or m.get("flushed_utc")))
        age = (now - (t if t.tzinfo else t.replace(tzinfo=UTC))).total_seconds()
        ups = sum(1 for v in (m.get("streams") or {}).values() if v == "up")
        n = int(m.get("symbols_subscribed") or 0)
        return (n, ups, age) if m.get("authorized") and n > 0 and ups and -60 <= age <= 600 else None
    except Exception: return None


def _quota_words(st, n):
    """Plain words for the dash and feed keeper: how many symbols are live, why the cap is what it is, and how each held EU
    name is marked. -> (ok, why, held_view). ok = trades hold symbols and every held EU name has a trades slot."""
    now, meta, live = st["now"], st["meta"], st["live_eu"]
    qd = meta.get("quota") or {}; per = qd.get("per_stream") or {}
    n_live, n_c = len(live), int(per.get("eu-candles") or 0)
    cap, plan = qd.get("cap"), qd.get("plan_total")
    held = [h for h in (meta.get("held") or []) if isinstance(h, str)]
    view, quiet = {}, []
    for h in held:
        age = _age_s(_row(st, h), now)
        if h not in live: view[h] = "no live slot (Yahoo marks it)"
        elif not session_open(h, now): view[h] = "live slot, market closed"
        elif age is None: view[h] = "live slot, no trade seen yet"
        elif age <= FRESH_S: view[h] = "live, last trade %ds ago" % age
        elif age <= QUIET_S: view[h] = "live, quiet: last Cboe trade %s ago" % _mins(age); quiet.append("%s last traded %s ago" % (h, _mins(age)))
        else: view[h] = "live slot, no Cboe trade for %s (Yahoo marks it meanwhile)" % _mins(age); quiet.append("%s has not traded on Cboe for %s (Yahoo marks it meanwhile)" % (h, _mins(age)))
    no_slot = [h for h in held if h not in live]
    probes = n_live - sum(1 for h in held if h in live)
    if not n_live and qd.get("settling"):
        return False, "starting: asking EODHD for live symbols (held EU positions first)", view
    if cap is not None:
        head = "EODHD currently accepts up to %d live symbol%s for this Singapore collector" % (cap, "" if cap == 1 else "s")
        if plan and plan > cap:
            mac = _mac_holder(now)
            head += " (configured plan %d; remaining capacity ownership is not verified)" % plan
            if mac:
                head += "; a Mac collector was observed with %d symbols x %d streams, last streamed %s ago (not proof of vendor slot allocation)" % (mac[0], mac[1], _mins(max(mac[2], 0)))
    else:
        head = "%d EU symbol%s live on trades (no EODHD limit hit)" % (n_live, "" if n_live == 1 else "s")
    if not held: mid = "no EU positions held; %d scan names live" % probes
    elif no_slot: mid = "%s %s no live slot and %s marked from Yahoo (15-20 min late)" % (", ".join(no_slot), "has" if len(no_slot) == 1 else "have", "is" if len(no_slot) == 1 else "are")
    else: mid = "all %d EU position%s live (%s), %d scan names fill the rest" % (len(held), " is" if len(held) == 1 else "s are", ", ".join(held), probes)
    if quiet and not no_slot: mid += "; " + "; ".join(quiet)
    tail = ("1-minute candles on %d" % n_c) if n_c else "1-minute candles off: quota (live prices come first)"
    ok = n_live > 0 and not no_slot
    if not ok: tail += "; looking for a free slot every %s" % _mins(REPROBE_SHORT_S)
    return ok, "%s; %s; %s." % (head, mid, tail), view


def _health(st):
    now, meta = st["now"], st["meta"]; venues, n, newest = set(), 0, None
    for sym, q in st["last"].items():
        if not isinstance(q, dict) or not _usable(sym, q, st): continue
        n += 1; venues.add(SESS.get(_suf(sym), (None, None, None, None, None, None))[5])
        ts = _ms(q.get("ts_ms"))
        if ts and (newest is None or ts > newest): newest = ts
    venues.discard(None)
    eu_open = any(session_open("X." + s, now) for s in SESS)   # tfo_falsealarm_20261101: weekend/overnight -> closed, not broken
    healthy, why, view = bool(st["alive"] and n > 0), None, None
    if not meta.get("authorized"): why = "not authorized"
    elif not st["alive"]: why = "consumer heartbeat stale"
    elif isinstance(meta.get("quota"), dict) and st["live_eu"] is not None:   # eufeed_20261103: quota-aware words
        ok, why, view = _quota_words(st, n); healthy = healthy and ok
        if n == 0 and ok: why = "no usable EU quote yet; " + why
    elif n == 0: why = "no usable EU quote"
    qd = meta.get("quota") if isinstance(meta.get("quota"), dict) else {}
    return dict(paper_only=True, src="eodhd_cboe_europe_ws", authorized=bool(meta.get("authorized")),
                alive=st["alive"], healthy=healthy, why=why, markets_open=eu_open, symbols=n,
                venues_live=sorted(venues), age_sec=None if st["file_age"] is None else round(st["file_age"], 1),
                newest_utc=_iso(newest) if newest else None,
                newest_age_s=round(now.timestamp() - newest / 1000.0, 1) if newest else None,
                streams=meta.get("streams") or {}, last_msg_utc=meta.get("last_msg_utc"), fresh_s=FRESH_S,
                live_symbols=len(st["live_eu"]) if st["live_eu"] is not None else None,
                cap=qd.get("cap"), plan_total=qd.get("plan_total"), candles=(qd.get("per_stream") or {}).get("eu-candles"),
                held=view)


def health(now=None):
    """Reader-facing health. healthy = the consumer heartbeat is fresh, at least one EU print is usable and (quota-aware
    since eufeed_20261103) the trades stream holds symbols with a live slot for every held EU name. why = plain words."""
    return _health(_state(now))


def live_px(sym, now=None):
    """book.live_px shape, or None when this symbol should stay on the fallback."""
    if not is_eu(sym): return None
    st = _state(now); q = _row(st, sym)
    if not q or not _usable(sym, q, st): return None
    ts = dt.datetime.fromtimestamp(q["ts_ms"] / 1000.0, UTC)
    px = float(q["px"])
    # eustop_20261103 (fixer): _trade() updates px but leaves the hi/lo/open of the last 1m CANDLE in the row (BNP.PA 3 Nov:
    # px 88.84, hi=lo=open 86.51), and book's hard stop reads hi/lo only -> the BNP.PA short stop 88.597 and the KNOS.L long
    # stop never fired. Only a candle row has a window; a trade/quote row is this one print. Always bracket px.
    if q.get("kind") == "candle":
        hi, lo, op = max(float(q.get("hi") or px), px), min(float(q.get("lo") or px), px), float(q.get("open") or px)
    else:
        hi = lo = op = px
    return dict(last=px, hi=hi, lo=lo, open=op,
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


def max_per_stream():
    try: return max(0, int(os.environ.get("EODHD_EU_MAX_SYMBOLS", "24")))
    except ValueError: return 24


def held_symbols():
    """EU names the THF book (then the veteran desk book) holds open or armed. They always get a live trades slot first."""
    out = []
    for rel in ("state/thf/thf_book.json", "reports/veteran_desk_book.json"):
        try: book = json.load(open(os.path.join(BASE, rel)))
        except Exception: continue
        if not isinstance(book, dict): continue
        seats = book.get("seats") or {}
        blocks = list(seats.values()) if isinstance(seats, dict) else []
        if not blocks and isinstance(book.get("positions"), dict): blocks = [book]
        for B in blocks:
            pos = (B.get("positions") or {}) if isinstance(B, dict) else {}
            for p in (pos.values() if isinstance(pos, dict) else pos if isinstance(pos, list) else []):
                if not isinstance(p, dict) or not p.get("ticker"): continue
                if str(p.get("status") or "open").lower() in CLOSED_ST: continue
                s = str(p["ticker"]).upper()
                if s not in out and is_eu(s): out.append(s)
    return out


def subscribe_symbols(held=None):
    """Held EU names first, then liquid probes, capped by EODHD_EU_MAX_SYMBOLS per stream (default 24). The live count can be
    lower: Quota fits it to what the key really has free (eufeed_20261103)."""
    cap = max_per_stream()
    held = list(held_symbols() if held is None else held); probes = []
    def add(sym):
        if sym and is_eu(sym) and sym not in held and sym not in probes: probes.append(sym)
    for s in PROBES: add(s)
    try:   # feed100_20261102: fill the remaining plan slots with the most liquid EU/UK scan-universe names (rest stay on Yahoo/FMP fallback)
        names = json.load(open(os.path.join(BASE, "data/pit/scan_universe.json"))).get("names") or []
        for n in sorted(names, key=lambda n: -(n.get("adv20_aud") or 0)):
            if len(held) + len(probes) >= cap: break
            add(str(n.get("sym") or "").upper())
    except Exception: pass
    out = []
    for s in held + probes:
        if len(out) >= cap: break
        if s not in out: out.append(s)
    return out


def subscribe_codes(desk_syms=None):
    return [eodhd_of(s) for s in (desk_syms if desk_syms is not None else subscribe_symbols()) if eodhd_of(s)]


def flush(tape, now=None):
    """Persist quotes, heartbeat, and any 1m candles. Returns the health meta dict. Never includes the key."""
    now = now or dt.datetime.now(UTC)
    with tape.lock:
        quotes = {k: dict(v) for k, v in tape.quotes.items()}
        candles = list(tape.candles.items())
        ok = dict(tape.ok); last_msg = tape.last_msg; symbols = list(tape.symbols); last_status = getattr(tape, 'last_status', None)
        by = dict(getattr(tape, "last_by_stream", None) or {}); held = list(getattr(tape, "held", None) or [])
    q = getattr(tape, "quota", None)
    prev = _load(LAST)
    if isinstance(prev, dict):
        for k, v in prev.items():
            if k not in quotes and isinstance(v, dict): quotes[k] = v
    _dump(LAST, quotes)
    streams = {s: (("up" if ok.get(s) else "down") if q is None else _stream_word(q, ok, s)) for s in ("eu", "eu-candles", "eu-quote")}
    meta = dict(paper_only=True, src="eodhd_cboe_europe_ws", authorized=bool(ok.get("eu") or ok.get("eu-candles")),
                streams=streams, last_msg_utc=last_msg.isoformat() if last_msg else None,
                symbols_subscribed=q.used() if q is not None else len(symbols),
                quotes=len(quotes), flushed_utc=now.isoformat(), last_status=last_status)
    if q is not None:   # eufeed_20261103: what is really live, why, and the verdict readers (feed keeper, dash) can show as is
        meta.update(symbols_wanted=len(symbols), subscribed={s: [to_desk(c) for c in v] for s, v in q.acc.items()}, held=held,
                    quota=q.snapshot(name=to_desk), last_msg_by_stream={s: t.isoformat() for s, t in by.items()})
        h = _health(_mkstate(now, meta, quotes, 0.0))
        meta.update(healthy=h["healthy"], why=h["why"], markets_open=h["markets_open"], usable=h["symbols"],
                    newest_utc=h["newest_utc"], newest_age_s=h["newest_age_s"], held_view=h["held"])
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


def _send(ws, codes, action="subscribe"):
    if codes: ws.send(json.dumps({"action": action, "symbols": ",".join(codes)}))


def resub(tape, q):
    """Every minute: re-read the books and the scan list into the wanted list. The cap is respected: Quota.tick only fills
    free room, and when trades are full a new held name swaps out the lowest-ranked probe (at most one swap a minute)."""
    held = held_symbols(); syms = subscribe_symbols(held=held)
    with tape.lock: tape.held = held; tape.symbols = syms
    q.set_want(subscribe_codes(syms), subscribe_codes(held))
    return syms


def serve():
    key = _key()
    if not key:
        print("ERROR eodhd_eu_ws: key missing (EODHD_API_KEY or ~/.tfo_secrets/eodhd.env)", flush=True)
        return 2
    try: import websocket  # websocket-client, framework interpreter
    except ImportError:
        print("ERROR eodhd_eu_ws: websocket-client is not installed for %s" % sys.executable, flush=True)
        return 2
    streams = ["eu", "eu-candles"]
    if os.environ.get("EODHD_EU_QUOTES") == "1": streams.append("eu-quote")
    tape = Tape(); q = Quota(streams, max_per_stream(), hint=load_caps()); tape.quota = q
    syms = resub(tape, q)
    if not q.want:
        print("ERROR eodhd_eu_ws: no EU/LSE symbols to subscribe", flush=True)
        return 2
    print("eodhd_eu_ws want %d symbols (held EU first: %s) on %s; start hint %s, plan %d, max %d per stream"
          % (len(syms), ",".join(tape.held) or "none", ",".join(streams), q.hint, q.plan_total, q.max_n), flush=True)
    prev = _load(LAST)
    if isinstance(prev, dict): tape.quotes.update({k: v for k, v in prev.items() if isinstance(v, dict)})

    def run(stream):
        def on_open(ws):
            tape.socks[stream] = ws    # Quota decides what to subscribe once this socket is authorized
        def on_message(ws, raw):
            try: tape.ingest(stream, raw)
            except Exception: print("bad frame", stream, flush=True)
            if tape.auth_failed: os._exit(2)
        def on_error(ws, err):
            print("ws error", stream, type(err).__name__, flush=True)  # never str(err): it can embed the URL
        app = websocket.WebSocketApp(_url(stream, key), on_open=on_open, on_message=on_message, on_error=on_error)
        app.run_forever(ping_interval=20, ping_timeout=10)
        tape.dead.append(stream)

    def send(stream, action, codes): _send(tape.socks[stream], codes, action)
    def ready(stream): return bool(tape.ok.get(stream)) and stream in tape.socks

    for s in streams: threading.Thread(target=run, args=(s,), daemon=True).start()
    last_resub = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        with tape.lock: answers, tape.statuses = tape.statuses, []
        for stream, code, msg, _t in answers: q.on_reply(stream, code, msg, now)
        if now - last_resub >= 60:
            last_resub = now
            try: resub(tape, q)
            except Exception as e: print("resub: %s" % type(e).__name__, flush=True)
        try: q.tick(send, ready, now)
        except Exception as e: print("quota tick: %s" % type(e).__name__, flush=True)
        for line in q.drain(): print("eodhd_eu_ws quota: %s" % line, flush=True)
        try: flush(tape)
        except Exception as e: print("flush: %s" % type(e).__name__, flush=True)
        if q.changed and q.idle and q.inflight is None:
            try: save_caps(q); q.changed = False
            except Exception as e: print("caps: %s" % type(e).__name__, flush=True)
        if tape.auth_failed:
            print("ERROR eodhd_eu_ws: auth rejected", flush=True); return 2
        if tape.dead:
            print("ERROR eodhd_eu_ws: stream closed %s" % ",".join(tape.dead), flush=True); return 1


def status():
    h = health(); out = {k: h[k] for k in h if k != "streams"}; out["streams"] = h.get("streams")
    c = _load(CAPS)
    if isinstance(c, dict) and c: out["learned_caps"] = {k: c.get(k) for k in ("total", "pinned", "per_stream", "plan_total", "updated_utc", "note")}
    json.dump(out, sys.stdout, indent=1); sys.stdout.write("\n"); return 0


if __name__ == "__main__":
    if "--status" in sys.argv: sys.exit(status())
    if "--symbols" in sys.argv:
        held = held_symbols(); syms = subscribe_symbols(held=held)
        json.dump(dict(n=len(syms), held=held, symbols=syms, codes=subscribe_codes(syms)), sys.stdout, indent=1); sys.stdout.write("\n"); sys.exit(0)
    sys.exit(serve())
