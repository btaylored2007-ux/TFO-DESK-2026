"""Kraken public trades + Massive stock quotes → one tape. PAPER ONLY.

No credentials are logged. The Massive key is read the same way as
veteran_desk.massive (env or ~/.tfo_secrets/massive.env) and is sent only as
the websocket auth frame. Kraken uses the public v2 trade channel, the same
shape as ws_tape_collector.py.

The tape is a file bus the desks can read without sharing memory:
  <root>/tape_last.json     latest print per symbol
  <root>/tape.jsonl         append-only prints, rotated by size
  <root>/kraken_tape.sqlite Kraken trades, ws_tape_collector schema
  reports/exec_speed_stream_kraken.json
  reports/exec_speed_stream_massive.json

Usage
-----
  python3 -m veteran_desk.exec_speed stream-daemon
Verify on the Mac: health file shows kraken last_msg_utc moving, and
tape_last.json has BTC/USD. Massive stays "no_key" until the env file exists
and then shows SPY. Neither path places an order.
"""
import json
import os
import sqlite3
import threading
import time

from veteran_desk.exec_speed.common import append_jsonl, atomic_json, iso, read_json, redact, utc_now

PAPER_ONLY = True
KRAKEN_URL = "wss://ws.kraken.com/v2"
MASSIVE_URL = "wss://socket.massive.com/stocks"
KRAKEN_PAIRS = ["BTC/USD", "ETH/USD"]
MASSIVE_CORE = ["SPY", "QQQ", "IWM"]
KRAKEN_FIELDS = ("symbol", "side", "price", "qty", "timestamp")
BACKOFF_START = 1.0
BACKOFF_CAP = 300.0
STABLE_SEC = 60.0
TAPE_ROTATE_BYTES = 20 * 1024 * 1024
SECRET_FILE = os.path.expanduser("~/.tfo_secrets/massive.env")


class SchemaDriftError(Exception):
    """A frame did not carry the fields this tape stores. Do not reconnect over it."""


class AuthFailed(Exception):
    """Massive rejected auth. The key is not part of this message."""


class Tape:
    """In-memory last print plus a durable file bus. Safe for two writer threads."""
    def __init__(self, root, rotate_bytes=TAPE_ROTATE_BYTES):
        self.root = root
        self.last_path = os.path.join(root, "tape_last.json")
        self.jsonl_path = os.path.join(root, "tape.jsonl")
        self.db_path = os.path.join(root, "kraken_tape.sqlite")
        self.rotate_bytes = rotate_bytes
        self.mem = {}
        self._db = None
        self._lock = threading.Lock()

    def add(self, row):
        with self._lock:
            return self._add(row)

    def _add(self, row):
        sym = str(row["sym"])
        self.mem[sym] = row
        os.makedirs(self.root, exist_ok=True)
        self._rotate()
        append_jsonl(self.jsonl_path, row)
        atomic_json(self.last_path, self.mem)
        if row.get("src") == "kraken":
            self._insert_kraken(row)
        return row

    def latest(self, sym=None):
        if sym is None:
            return dict(self.mem)
        return self.mem.get(sym)

    def _rotate(self):
        try:
            if os.path.exists(self.jsonl_path) and os.path.getsize(self.jsonl_path) >= self.rotate_bytes:
                dest = self.jsonl_path + ".1"
                os.replace(self.jsonl_path, dest)
        except OSError:
            pass

    def _connect(self):
        if self._db is None:
            os.makedirs(self.root, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("""CREATE TABLE IF NOT EXISTS trades (
                utc_ts TEXT NOT NULL, pair TEXT NOT NULL, price REAL NOT NULL,
                qty REAL NOT NULL, side TEXT NOT NULL, trade_id INTEGER)""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_pair_tid ON trades(pair, trade_id)")
            conn.commit()
            self._db = conn
        return self._db

    def _insert_kraken(self, row):
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)",
            (row["utc"], row["sym"], float(row["px"]), float(row.get("qty") or 0),
             str(row.get("side") or ""), row.get("trade_id")))
        conn.commit()


def latest(path, sym=None):
    data = read_json(path) or {}
    if not isinstance(data, dict):
        return None
    return data if sym is None else data.get(sym)


def massive_key():
    """Same contract as veteran_desk.massive._key. Never log the return value."""
    env = os.environ.get("MASSIVE_API_KEY")
    if env:
        return env.strip()
    try:
        with open(SECRET_FILE) as fh:
            for line in fh:
                if line.strip().startswith("MASSIVE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        return None
    return None


def _num(value, field):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SchemaDriftError("non-numeric %s" % field)


def handle_kraken(raw, tape, now=None):
    """One Kraken v2 frame. Returns the number of trades stored."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaDriftError("kraken frame is not json") from exc
    if not isinstance(msg, dict):
        raise SchemaDriftError("kraken frame is not an object")
    channel = msg.get("channel")
    if channel in ("heartbeat", "status") or msg.get("method") in ("pong", "ping"):
        return 0
    if msg.get("method") == "subscribe":
        if msg.get("success") is True:
            return 0
        raise RuntimeError("kraken subscribe rejected")
    if channel != "trade":
        return 0
    data = msg.get("data")
    if not isinstance(data, list):
        raise SchemaDriftError("kraken trade frame has no list")
    n = 0
    for item in data:
        if not isinstance(item, dict):
            raise SchemaDriftError("kraken trade item is not an object")
        missing = [f for f in KRAKEN_FIELDS if f not in item]
        if missing:
            raise SchemaDriftError("kraken trade missing %s" % ",".join(missing))
        tape.add({
            "utc": str(item["timestamp"]), "sym": str(item["symbol"]),
            "px": _num(item["price"], "price"), "qty": _num(item["qty"], "qty"),
            "side": str(item["side"]), "src": "kraken", "trade_id": item.get("trade_id"),
        })
        n += 1
    return n


def handle_massive(raw, tape, now=None):
    """One Massive text frame (a list of events, or one object). Key never stored."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaDriftError("massive frame is not json") from exc
    events = msg if isinstance(msg, list) else [msg]
    n = 0
    for event in events:
        if not isinstance(event, dict):
            raise SchemaDriftError("massive event is not an object")
        kind = event.get("ev")
        if kind == "status":
            status = str(event.get("status") or "")
            if status == "auth_failed":
                raise AuthFailed("massive auth failed")
            continue
        if kind == "T":
            sym = event.get("sym")
            if not sym or "p" not in event:
                raise SchemaDriftError("massive trade missing sym or p")
            tape.add({"utc": iso(now), "sym": str(sym), "px": _num(event["p"], "p"),
                      "qty": float(event["s"]) if event.get("s") is not None else None,
                      "side": None, "src": "massive", "t": event.get("t")})
            n += 1
        elif kind == "Q":
            sym = event.get("sym")
            if not sym:
                raise SchemaDriftError("massive quote missing sym")
            bid, ask = event.get("bp"), event.get("ap")
            if bid is None or ask is None:
                raise SchemaDriftError("massive quote missing bp or ap")
            mid = (_num(bid, "bp") + _num(ask, "ap")) / 2.0
            tape.add({"utc": iso(now), "sym": str(sym), "px": mid, "qty": None, "side": None,
                      "src": "massive", "bp": float(bid), "ap": float(ask), "t": event.get("t")})
            n += 1
    return n


def kraken_subscribe(pairs=None):
    return json.dumps({"method": "subscribe", "params": {
        "channel": "trade", "symbol": list(pairs or KRAKEN_PAIRS), "snapshot": False}})


def massive_auth_frame(key):
    return json.dumps({"action": "auth", "params": key})


def massive_subscribe(symbols):
    params = ",".join("T.%s,Q.%s" % (s, s) for s in symbols)
    return json.dumps({"action": "subscribe", "params": params})


def write_health(path, payload, secrets=()):
    clean = json.loads(redact(json.dumps(payload, default=str), secrets))
    clean["paper_only"] = True
    clean["utc"] = payload.get("utc") or iso()
    atomic_json(path, clean)
    return clean


def run_session(connect, on_raw, send_on_open=()):
    """One socket session. `connect` returns an object with send/recv/close.

    recv timeout raises the socket's timeout type; the caller treats that as idle.
    """
    sock = connect()
    try:
        for frame in send_on_open:
            sock.send(frame)
        while True:
            raw = sock.recv()
            if not raw:
                raise ConnectionError("empty frame")
            on_raw(raw)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def run_consumer(connect, on_raw, health_path, *, name, send_on_open=(), sleep=time.sleep,
                 max_cycles=None, secrets=(), now_fn=None):
    """Reconnect with capped backoff. Schema drift and auth failure do not retry."""
    backoff = BACKOFF_START
    cycles = 0
    health = {"name": name, "connects": 0, "errors": 0, "rows": 0, "status": "starting"}
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        started = time.time()
        held = 0.0
        try:
            last_flush = {"t": 0.0}
            def _on(raw, _health=health):
                n = on_raw(raw)
                _health["rows"] = _health.get("rows", 0) + int(n or 0)
                _health["last_msg_utc"] = iso(None if now_fn is None else now_fn())
                _health["status"] = "streaming"
                now_s = time.time()
                if last_flush["t"] == 0.0 or now_s - last_flush["t"] >= 5:
                    _health["utc"] = _health["last_msg_utc"]
                    write_health(health_path, _health, secrets)
                    last_flush["t"] = now_s
            run_session(connect, _on, send_on_open)
            held = time.time() - started
            health["connects"] = health.get("connects", 0) + 1
        except (SchemaDriftError, AuthFailed) as exc:
            health["status"] = type(exc).__name__
            health["errors"] = health.get("errors", 0) + 1
            health["last_error"] = type(exc).__name__
            health["utc"] = iso(None if now_fn is None else now_fn())
            write_health(health_path, health, secrets)
            raise
        except Exception as exc:
            health["connects"] = health.get("connects", 0) + 1
            health["errors"] = health.get("errors", 0) + 1
            health["status"] = "reconnecting"
            health["last_error"] = type(exc).__name__
            held = time.time() - started
        if held >= STABLE_SEC:
            backoff = BACKOFF_START
        health["backoff_s"] = backoff
        health["utc"] = iso(None if now_fn is None else now_fn())
        write_health(health_path, health, secrets)
        sleep(backoff)
        backoff = min(backoff * 2.0, BACKOFF_CAP)
    return health


def stream_paths(base):
    root = os.path.join(base, "state", "exec_speed")
    return {
        "root": root,
        "health": os.path.join(base, "reports", "exec_speed_stream_health.json"),
        "kraken_health": os.path.join(base, "reports", "exec_speed_stream_kraken.json"),
        "massive_health": os.path.join(base, "reports", "exec_speed_stream_massive.json"),
    }
