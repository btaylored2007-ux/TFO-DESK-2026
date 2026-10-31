"""Operators for the paper exec-speed services. No secrets are printed.

  python3 -m veteran_desk.exec_speed status
  python3 -m veteran_desk.exec_speed risk-once
  python3 -m veteran_desk.exec_speed risk-serve [--port 8766]
  python3 -m veteran_desk.exec_speed stream-daemon
  python3 -m veteran_desk.exec_speed router-daemon
  python3 -m veteran_desk.exec_speed wake-daemon

Daemons stay on paper data. Wake dispatch runs only when EXEC_SPEED_DISPATCH=1.
THF mirroring into the engine queue runs only when EXEC_SPEED_MIRROR_ENGINE=1.
"""
import json
import os
import sys
import threading
import time

from veteran_desk.exec_speed.common import iso, read_json
from veteran_desk.exec_speed.firm_risk_service import publish, publish_from_base, serve
from veteran_desk.exec_speed.paper_router import PaperRouter
from veteran_desk.exec_speed.stream_tape import (
    MASSIVE_CORE, MASSIVE_URL, KRAKEN_PAIRS, KRAKEN_URL, SchemaDriftError, Tape,
    handle_kraken, handle_massive, kraken_subscribe, massive_auth_frame, massive_key,
    massive_subscribe, run_consumer, stream_paths, write_health,
)
from veteran_desk.exec_speed.wake_bus import WakeBus, holdings_from_book

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _root(base):
    return os.path.join(base, "state", "exec_speed")


def _reports(base):
    return os.path.join(base, "reports")


def status(base=BASE):
    paths = [
        os.path.join(base, "reports", "firm_risk_service.json"),
        os.path.join(base, "reports", "exec_speed_stream_kraken.json"),
        os.path.join(base, "reports", "exec_speed_stream_massive.json"),
        os.path.join(base, "reports", "paper_router_health.json"),
        os.path.join(base, "reports", "paper_router_alerts.json"),
        os.path.join(base, "reports", "exec_speed_wake_health.json"),
        os.path.join(base, "state", "exec_speed", "tape_last.json"),
    ]
    for path in paths:
        data = read_json(path)
        if not isinstance(data, dict):
            print("%s missing" % path)
            continue
        print("%s utc=%s status=%s ok=%s" % (path, data.get("utc") or data.get("updated_utc"), data.get("status"), data.get("ok")))


def risk_once(base=BASE):
    snap = publish_from_base(base)
    print("firm_risk_service ok=%s new_entries_allowed=%s heat=%s reason=%s" % (
        snap.get("ok"), snap.get("new_entries_allowed"), snap.get("firm_heat"), snap.get("reason")))
    return snap


def _risk_loop(base, path):
    while True:
        try:
            publish_from_base(base)
        except Exception as exc:
            publish(path, {
                "ok": False, "paper_only": True, "utc": iso(), "service": "firm_risk_service",
                "reason": "publish_failed", "detail": type(exc).__name__,
                "new_entries_allowed": False, "new_entries_halted": True, "exits_allowed": True,
                "reachable": True,
            })
        time.sleep(15)


def risk_serve(base=BASE, port=8766):
    path = os.path.join(_reports(base), "firm_risk_service.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    threading.Thread(target=_risk_loop, args=(base, path), daemon=True).start()
    _risk_loop_once(base, path)
    serve(path, "127.0.0.1", port)


def _risk_loop_once(base, path):
    try:
        publish_from_base(base)
    except Exception as exc:
        publish(path, {
            "ok": False, "paper_only": True, "utc": iso(), "service": "firm_risk_service",
            "reason": "publish_failed", "detail": type(exc).__name__,
            "new_entries_allowed": False, "new_entries_halted": True, "exits_allowed": True,
        })


def stream_daemon(base=BASE):
    paths = stream_paths(base)
    tape = Tape(paths["root"])
    os.makedirs(paths["root"], exist_ok=True)

    def kraken():
        def connect():
            import websocket
            sock = websocket.create_connection(KRAKEN_URL, timeout=20)
            sock.settimeout(20)
            return sock
        try:
            run_consumer(connect, lambda raw: handle_kraken(raw, tape), paths["kraken_health"], name="kraken",
                         send_on_open=(kraken_subscribe(KRAKEN_PAIRS),))
        except SchemaDriftError:
            os._exit(3)
        except Exception as exc:
            write_health(paths["kraken_health"], {"name": "kraken", "status": type(exc).__name__, "utc": iso()})
            raise

    def massive():
        while True:
            key = massive_key()
            if not key:
                write_health(paths["massive_health"], {"name": "massive", "status": "no_key", "utc": iso(), "paper_only": True})
                time.sleep(60)
                continue
            def connect(key=key):
                import websocket
                sock = websocket.create_connection(MASSIVE_URL, timeout=20)
                sock.settimeout(20)
                return sock
            try:
                run_consumer(connect, lambda raw: handle_massive(raw, tape), paths["massive_health"], name="massive",
                             send_on_open=(massive_auth_frame(key), massive_subscribe(MASSIVE_CORE)),
                             secrets=(key,))
            except Exception as exc:
                write_health(paths["massive_health"], {"name": "massive", "status": type(exc).__name__, "utc": iso()}, secrets=(key,))
                time.sleep(30)

    threading.Thread(target=kraken, daemon=True).start()
    massive()


def router_daemon(base=BASE):
    router = PaperRouter(_root(base), _reports(base))
    seen = {}
    last = os.path.join(_root(base), "tape_last.json")
    while True:
        tape = read_json(last) or {}
        if isinstance(tape, dict):
            for sym, row in tape.items():
                if not isinstance(row, dict) or row.get("px") is None:
                    continue
                stamp = row.get("utc")
                if seen.get(sym) == stamp:
                    continue
                seen[sym] = stamp
                router.on_print(sym, row["px"])
        time.sleep(1)


def wake_daemon(base=BASE):
    mirror = os.path.join(base, "state", "vd_engine", "wake_queue.jsonl")
    mirror = mirror if os.environ.get("EXEC_SPEED_MIRROR_ENGINE") == "1" else None
    counts = os.path.join(base, "state", "vd_engine", "wake_counts.json")
    bus = WakeBus(_root(base), _reports(base), engine_counts_path=counts if os.path.exists(counts) else None,
                  mirror_queue=mirror)
    import subprocess
    seen = set()
    if os.path.exists(bus.queue_path):
        for line in open(bus.queue_path):
            if line.strip():
                try:
                    seen.add(json.loads(line).get("wake_id"))
                except json.JSONDecodeError:
                    continue
    while True:
        holdings = {}
        for rel in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
            book = read_json(os.path.join(base, rel))
            if isinstance(book, dict):
                holdings.update(holdings_from_book(book))
        desk = "thf" if os.environ.get("THF_DESK") == "1" else "veteran"
        bus.poll(os.path.join(_root(base), "tape_last.json"),
                 news_path=os.path.join(base, "state", "veteran_desk", "news_alerts.jsonl"),
                 events_path=os.path.join(_root(base), "events.jsonl"),
                 holdings=holdings, desk=desk)
        if os.environ.get("EXEC_SPEED_DISPATCH") == "1" and os.path.exists(bus.queue_path):
            for line in open(bus.queue_path):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("wake_id") in seen or row.get("status") != "queued":
                    continue
                seen.add(row["wake_id"])
                bus.dispatch(row, subprocess.Popen, base)
        time.sleep(2)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        status()
    elif cmd == "risk-once":
        risk_once()
    elif cmd == "risk-serve":
        port = 8766
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        risk_serve(port=port)
    elif cmd == "stream-daemon":
        stream_daemon()
    elif cmd == "router-daemon":
        router_daemon()
    elif cmd == "wake-daemon":
        wake_daemon()
    else:
        print("unknown command", cmd, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
