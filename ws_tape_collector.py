#!/usr/bin/env python3
"""Kraken PUBLIC websocket tape collector — PAPER DESK INFRASTRUCTURE ONLY.

Subscribes to the v2 public trade channel for exactly two pairs (BTC/USD,
ETH/USD — the v2 names; REST calls them XBTUSD/ETHUSD) and appends each print
to reports/ws_tape.sqlite. No credentials, no accounts, no orders: the socket
is the public feed at wss://ws.kraken.com/v2 and nothing here can trade.

Consumers: maker_calibration_probe.py reads this tape to decide whether a
simulated post-only limit would have filled (strict-through rule).

DEPENDENCY (documented per the build order): the framework interpreter
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 already ships
BOTH websocket-client (1.9.0) and websockets (17.0.1); this module pins
`import websocket` (websocket-client) and the healer launches it with the
framework interpreter absolute path — never a PATH-relative python3, which
under launchd resolves to the dependency-free system python.

HOUSE RULES honored here:
  * UTC stamps everywhere.
  * Failures LOUD: every error prints an ERROR line, lands in the sqlite
    health table, and persists reports/ws_tape_last_error.json. A trade
    message missing required fields raises SchemaDriftError and the process
    EXITS non-zero — a schema miss must never look like empty success.
  * RESOURCE-LIGHT: 2 pairs, trade channel only, tiny batched writes (WAL,
    busy_timeout), capped exponential backoff on reconnect, 45-day retention
    prune so the file cannot grow without bound.

Usage:
    python3 ws_tape_collector.py            # run forever (healer-supervised)
    python3 ws_tape_collector.py --status   # print row counts and exit
"""
try:  # 2026-10-28 WITA Brett: ISP DNS hijack -> process-local DoH for Kraken public hosts (guarded)
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "reports", "ws_tape.sqlite")
LAST_ERROR_PATH = os.path.join(BASE, "reports", "ws_tape_last_error.json")
WS_URL = "wss://ws.kraken.com/v2"
PAIRS = ["BTC/USD", "ETH/USD"]          # v2 symbols; REST equivalents XBTUSD/ETHUSD
REQUIRED_FIELDS = ("symbol", "side", "price", "qty", "timestamp")
RETENTION_DAYS = 45
RECV_TIMEOUT_SEC = 20                    # Kraken heartbeats ~1/s; 20s idle => ping
BACKOFF_START_SEC = 1.0
BACKOFF_CAP_SEC = 300.0
STABLE_RESET_SEC = 60.0                  # connection alive this long resets backoff
HEALTH_BEAT_SEC = 900                    # periodic rows_total health row
PRUNE_CHECK_SEC = 3600

try:
    import websocket                     # websocket-client (PINNED import)
except ImportError as exc:               # loud, marker, non-zero — never silent
    msg = ("ERROR ws_tape_collector: cannot import websocket-client under %s: %s"
           % (sys.executable, exc))
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LAST_ERROR_PATH), exist_ok=True)
        with open(LAST_ERROR_PATH, "w") as f:
            json.dump({"utc_ts": datetime.now(timezone.utc).isoformat(),
                       "kind": "import_error", "detail": msg}, f, indent=2)
    except OSError:
        pass
    sys.exit(2)


class SchemaDriftError(Exception):
    """A trade-channel message did not carry the fields this desk relies on."""


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def open_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        utc_ts   TEXT NOT NULL,
        pair     TEXT NOT NULL,
        price    REAL NOT NULL,
        qty      REAL NOT NULL,
        side     TEXT NOT NULL,
        trade_id INTEGER)""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_pair_tid
                    ON trades(pair, trade_id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ix_trades_pair_ts
                    ON trades(pair, utc_ts)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS health (
        utc_ts     TEXT NOT NULL,
        event      TEXT NOT NULL,
        detail     TEXT,
        rows_total INTEGER)""")
    conn.commit()
    return conn


def rows_total(conn):
    return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]


def health(conn, event, detail=""):
    try:
        conn.execute("INSERT INTO health VALUES (?,?,?,?)",
                     (utc_now_iso(), event, str(detail)[:500], rows_total(conn)))
        conn.commit()
    except sqlite3.Error as exc:
        print("ERROR ws_tape_collector: health write failed: %s" % exc, flush=True)


def loud_error(kind, detail, conn=None):
    """Print ERROR, persist the marker file, log a health row. Never silent."""
    line = "ERROR ws_tape_collector %s: %s" % (kind, detail)
    print(line, flush=True)
    try:
        with open(LAST_ERROR_PATH, "w") as f:
            json.dump({"utc_ts": utc_now_iso(), "kind": kind,
                       "detail": str(detail)[:2000]}, f, indent=2)
    except OSError as exc:
        print("ERROR ws_tape_collector: could not write last_error marker: %s"
              % exc, flush=True)
    if conn is not None:
        health(conn, "error:" + kind, detail)


def prune_old(conn):
    """Retention guard: drop prints older than RETENTION_DAYS, keep file small."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute("DELETE FROM trades WHERE utc_ts < ?", (cutoff,))
    conn.execute("DELETE FROM health WHERE utc_ts < ?", (cutoff,))
    conn.commit()
    if cur.rowcount:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        health(conn, "prune", "deleted %d rows older than %s" % (cur.rowcount, cutoff))
    return cur.rowcount


def validate_trade(item):
    """A trade dict missing a required field is schema drift — raise, loudly."""
    if not isinstance(item, dict):
        raise SchemaDriftError("trade item is not an object: %r" % (item,))
    missing = [f for f in REQUIRED_FIELDS if f not in item]
    if missing:
        raise SchemaDriftError("trade message missing required fields %s: %r"
                               % (missing, item))
    try:
        float(item["price"]), float(item["qty"])
    except (TypeError, ValueError):
        raise SchemaDriftError("non-numeric price/qty in trade: %r" % (item,))


def insert_trades(conn, items):
    rows = []
    for item in items:
        validate_trade(item)
        rows.append((str(item["timestamp"]), str(item["symbol"]),
                     float(item["price"]), float(item["qty"]),
                     str(item["side"]), item.get("trade_id")))
    conn.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def handle_message(conn, raw, counters):
    msg = json.loads(raw)
    if not isinstance(msg, dict):
        raise SchemaDriftError("non-object frame from websocket: %r" % raw[:200])
    channel = msg.get("channel")
    if channel == "trade":
        data = msg.get("data")
        if not isinstance(data, list):
            raise SchemaDriftError("trade frame without list data: %r" % raw[:300])
        counters["rows"] += insert_trades(conn, data)
        return
    if channel in ("heartbeat", "status"):
        return
    method = msg.get("method")
    if method == "subscribe":
        if msg.get("success") is True:
            health(conn, "subscribed", json.dumps(msg.get("result", {}))[:300])
        else:
            # A refused subscription is a hard failure — empty tape must not
            # masquerade as a healthy collector.
            raise RuntimeError("subscribe rejected: %s" % raw[:300])
        return
    if method in ("ping", "pong"):
        return
    counters["unknown"] += 1             # tolerated, but visible in health beats


def run_session(conn, counters):
    """One connect->stream session. Returns when the socket drops."""
    ws = websocket.create_connection(WS_URL, timeout=15)
    ws.settimeout(RECV_TIMEOUT_SEC)
    connected_at = time.time()
    counters["connects"] += 1
    health(conn, "connect", WS_URL)
    print("[%s] ws_tape_collector connected to %s (pairs %s)"
          % (utc_now_iso(), WS_URL, ",".join(PAIRS)), flush=True)
    try:
        ws.send(json.dumps({"method": "subscribe",
                            "params": {"channel": "trade", "symbol": PAIRS,
                                       "snapshot": False}}))
        last_beat = time.time()
        last_prune = time.time()
        pinged = False
        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                if pinged:               # ping already outstanding -> dead link
                    raise ConnectionError("no data and no pong within %ds"
                                          % (2 * RECV_TIMEOUT_SEC))
                ws.send(json.dumps({"method": "ping"}))    # app-level ping per docs
                pinged = True
                continue
            pinged = False
            if not raw:
                raise ConnectionError("empty frame — server closed the stream")
            handle_message(conn, raw, counters)
            now = time.time()
            if now - last_beat >= HEALTH_BEAT_SEC:
                health(conn, "beat", "connects=%d disconnects=%d errors=%d unknown=%d"
                       % (counters["connects"], counters["disconnects"],
                          counters["errors"], counters["unknown"]))
                last_beat = now
            if now - last_prune >= PRUNE_CHECK_SEC:
                prune_old(conn)
                last_prune = now
    finally:
        try:
            ws.close()
        except Exception:
            pass
        counters["disconnects"] += 1
        health(conn, "disconnect",
               "session lasted %.0fs" % (time.time() - connected_at))
    return connected_at


def main_loop():
    conn = open_db()
    prune_old(conn)
    counters = {"connects": 0, "disconnects": 0, "errors": 0,
                "unknown": 0, "rows": 0}
    backoff = BACKOFF_START_SEC
    while True:
        started = None
        try:
            started = run_session(conn, counters)
        except SchemaDriftError as exc:
            # Fatal by design: the message shape this desk depends on changed.
            # Never absorb it into a reconnect loop that pretends all is well.
            counters["errors"] += 1
            loud_error("schema_drift", str(exc), conn)
            sys.exit(3)
        except KeyboardInterrupt:
            health(conn, "stopped", "keyboard interrupt")
            return
        except Exception as exc:
            counters["errors"] += 1
            loud_error(type(exc).__name__, str(exc), conn)
        # capped exponential backoff; a session that held >= STABLE_RESET_SEC
        # earns a reset so a healthy stream reconnects fast after a blip
        if started is not None and time.time() - started >= STABLE_RESET_SEC:
            backoff = BACKOFF_START_SEC
        print("[%s] ws_tape_collector reconnecting in %.0fs"
              % (utc_now_iso(), backoff), flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2.0, BACKOFF_CAP_SEC)


def print_status():
    if not os.path.exists(DB_PATH):
        print("ws_tape.sqlite does not exist yet — collector has never run")
        return
    conn = open_db()
    for pair in PAIRS:
        row = conn.execute(
            "SELECT COUNT(*), MIN(utc_ts), MAX(utc_ts) FROM trades WHERE pair=?",
            (pair,)).fetchone()
        print("%s: %d rows, %s .. %s" % (pair, row[0], row[1], row[2]))
    ev = conn.execute("SELECT utc_ts, event, detail FROM health "
                      "ORDER BY utc_ts DESC LIMIT 8").fetchall()
    for r in ev:
        print("health %s %s %s" % r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        print_status()
    else:
        main_loop()
