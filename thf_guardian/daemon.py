"""Long-running inbox plus a 90s check loop. Restart=always belongs on this unit.

The check timer is a second entry (`python -m thf_guardian.guardian`) so a stuck
loop is still watched. Both take the same flock and skip a pass that is fresh.
The inbox listens on 127.0.0.1 only.
"""
import json, os, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from thf_guardian.config import Config
from thf_guardian.guardian import run_once
from thf_guardian.inbox import handle_message
from thf_guardian.util import read_json, read_jsonl


def _reject_public(host):
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("refusing to bind the inbox on %s" % host)


class Inbox:
    def __init__(self, cfg, secret, io):
        self.cfg = cfg
        self.secret = secret
        self.io = io
        self.used = set()
        self.snap = {}

    def message(self, text, confirm, bridge=None):
        from thf_guardian.util import utcnow
        return handle_message(self.cfg, self.snap, text, utcnow(), self.secret, self.used, self.io, confirm=confirm, bridge=bridge)


def make_handler(inbox):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ct="application/json"):
            raw = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?")[0]
            cfg = inbox.cfg
            if path == "/api/guardian_status":
                self._send(200, json.dumps(read_json(cfg.status_path, {}) or {}))
            elif path == "/api/guardian_thread":
                self._send(200, json.dumps(dict(messages=read_jsonl(cfg.thread))))
            elif path == "/api/guardian_alerts":
                self._send(200, json.dumps(dict(alerts=read_jsonl(cfg.alerts))))
            else:
                self._send(404, json.dumps(dict(error="not found")))

        def do_POST(self):
            if self.path.split("?")[0] != "/api/guardian_msg":
                return self._send(404, json.dumps(dict(error="not found")))
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20000:
                return self._send(413, json.dumps(dict(error="too large")))
            body = json.loads(self.rfile.read(n) or b"{}")
            out = inbox.message(body.get("text") or "", body.get("confirm"), bridge=body.get("_bridge"))
            safe = {k: v for k, v in out.items() if k != "action" or True}
            self._send(200, json.dumps(safe, default=str))
    return H


def serve(cfg, inbox):
    _reject_public(cfg.inbox_host)
    httpd = ThreadingHTTPServer((cfg.inbox_host, cfg.inbox_port), make_handler(inbox))
    return httpd


def once(cfg=None, runner=None):
    from thf_guardian.collect import collect
    cfg = cfg or Config()
    snap = collect(cfg, runner=runner)
    io = dict(systemctl=_systemctl, clear_lock=_clear_mkdir, write_queue=lambda q: None,
              fill=lambda a: None, health=lambda: {}, head=lambda t: None, write_book=lambda b: None,
              run=None, push=lambda seats: dict(seats=seats), min_free=50_000_000)
    return run_once(cfg, snap, io)


def _systemctl(how, unit):
    if how not in ("start", "restart") or not unit.startswith("tfo-"):
        raise RuntimeError("refusing %s %s" % (how, unit))
    subprocess_run = __import__("subprocess").run
    subprocess_run(["sudo", "-n", "systemctl", how, unit], timeout=30)


def _clear_mkdir(path):
    if path and os.path.isdir(path) and path.endswith(".tick.lock"):
        os.rmdir(path)


def main():
    import sys
    if "--once" in sys.argv:
        once()
        return
    cfg = Config()
    secret_path = cfg.p("state", "thf", "guardian_action.key")
    if os.path.exists(secret_path):
        secret = open(secret_path).read().strip()
    else:
        secret = os.urandom(24).hex()
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        with open(secret_path, "w") as fh:
            fh.write(secret)
        os.chmod(secret_path, 0o600)
    def _push(seats):
        from thf_guardian.ai_mode import load_modes
        from thf_guardian.push import dispatch_ai, packets_from_top10, plan
        from thf_guardian.util import read_json, utcnow
        now = utcnow()
        modes = load_modes(cfg.ai_mode, now)
        book = read_json(cfg.book, {}) or {}
        result = plan(seats, modes, packets_from_top10(cfg, seats), book, now)
        if result["ai_dispatch"]:
            dispatch_ai(result["ai_dispatch"], cfg)
        return result

    io = dict(systemctl=_systemctl, clear_lock=_clear_mkdir, write_queue=lambda q: None,
              fill=lambda a: None, health=lambda: once(cfg), head=lambda t: None,
              write_book=lambda b: None, run=None, push=_push)
    inbox = Inbox(cfg, secret, io)
    httpd = serve(cfg, inbox)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def _loop():
        import time
        while True:
            try:
                once(cfg)
            except Exception as exc:
                print("guardian pass failed: %s" % exc, flush=True)
            time.sleep(cfg.interval_sec)

    threading.Thread(target=_loop, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
