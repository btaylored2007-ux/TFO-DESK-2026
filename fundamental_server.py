#!/usr/bin/env python3
"""fundamental_server.py — TFO Fundamental Desk dashboard server. READ-ONLY.

Serves the dashboard and the book's ledger/equity JSON. Completely decoupled: it
reads files, never ticks the book, never fetches a price, never touches any other
desk, and holds no broker connection of any kind. If this process dies, the book
is unaffected — only the view goes dark.

Binds to 127.0.0.1 only.

Usage:  python3 fundamental_server.py [--port 18820]
"""
import argparse
import csv
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(BASE, "fundamental_dashboard.html")
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
EQUITY_CSV = os.path.join(BASE, "reports", "fundamental_equity.csv")
# The SPECULATIVE SLEEVE keeps its own ledger deliberately — separate capital,
# separate rules, separate kill. It is surfaced on the same page so there is ONE
# place to look, but never merged into the main book's numbers: blending a 15%-gate
# penny sleeve into a 5%-gate global book would misreport both.
SPEC_LEDGER = os.path.join(BASE, "reports", "fundamental_spec_book.json")


def equity_series(limit=600):
    """Real marked snapshots only. Nothing is back-filled or synthesised — an
    empty series means the book genuinely has no history yet, which is a fact
    worth showing rather than a gap worth papering over."""
    try:
        with open(EQUITY_CSV) as f:
            rows = [(r["utc"], float(r["equity_usd"]))
                    for r in csv.DictReader(f) if r.get("equity_usd")]
    except (OSError, ValueError, KeyError):
        return {"points": [], "n_raw": 0}
    if not rows:
        return {"points": [], "n_raw": 0}
    step = max(1, len(rows) // limit)
    pts = [list(r) for r in rows[::step]]
    if list(rows[-1]) != pts[-1]:
        pts.append(list(rows[-1]))
    vals = [p[1] for p in pts]
    return {"points": pts, "n_raw": len(rows),
            "min": min(vals), "max": max(vals), "last": rows[-1][1]}


class Handler(BaseHTTPRequestHandler):
    server_version = "TFOFundamentalDesk/1.0"

    def log_message(self, fmt, *args):        # quiet; the desk log is the record
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json_file(self, path):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), "application/json; charset=utf-8")
        except OSError as e:
            # Say what is missing. A dashboard that renders zeros when the ledger
            # is absent is worse than one that says the ledger is absent.
            self._send(503, json.dumps({
                "error": "ledger unavailable", "path": path,
                "detail": "%s: %s" % (type(e).__name__, e)}).encode(),
                "application/json; charset=utf-8")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"dashboard page missing", "text/plain")
        elif p == "/api/book":
            self._json_file(LEDGER)
        elif p == "/api/postexit":
            self._json_file(os.path.join(BASE, "reports",
                                         "post_exit_tracker.json"))
        elif p == "/api/alerts":
            # Catalyst-expiry fills. Surfaced so the event is visible from a
            # PHONE — the first one fires ~40 days out and nobody will be
            # watching the terminal when it does.
            path = os.path.join(BASE, "reports", "catalyst_alerts.jsonl")
            out = []
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            out.append(json.loads(line))
            except OSError:
                pass
            except ValueError:
                pass
            self._send(200, json.dumps(out[-25:]).encode(),
                       "application/json; charset=utf-8")
        elif p == "/api/spec":
            # 404 rather than an empty object when the sleeve has not been
            # created: "absent" and "flat" must never look the same.
            if not os.path.exists(SPEC_LEDGER):
                self._send(404, json.dumps({
                    "error": "speculative sleeve not open",
                    "detail": "no ledger at %s" % SPEC_LEDGER}).encode(),
                    "application/json; charset=utf-8")
            else:
                self._json_file(SPEC_LEDGER)
        elif p == "/api/equity":
            self._send(200, json.dumps(equity_series()).encode(),
                       "application/json; charset=utf-8")
        elif p == "/api/health":
            self._send(200, json.dumps({
                "ok": True, "book": "TFO Fundamental Desk", "paper_only": True,
                "ledger_present": os.path.exists(LEDGER),
                "spec_sleeve_present": os.path.exists(SPEC_LEDGER),
                "series_present": os.path.exists(EQUITY_CSV)}).encode(),
                "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18820)
    # Bound to loopback only, so it was invisible from the tailnet — i.e. from
    # the funder's phone, which is the only place this dashboard is ever read.
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("TFO Fundamental Desk dashboard -> http://127.0.0.1:%d  (READ-ONLY)" % a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
