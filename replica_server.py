#!/usr/bin/env python3
"""Venue paper replica — dashboard server (standalone, READ-ONLY).

Serves the replica's dashboard and its ledger/report JSON. Completely decoupled:
it reads files, never ticks the desk, never touches the Kraken engine, and holds
no broker connection of any kind. If this process dies, nothing else is affected.

Binds to 127.0.0.1 only.

Usage:  python3 replica_server.py [--port 18810]
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_PATH = os.path.join(BASE_DIR, "replica_dashboard.html")
LEDGER = os.path.join(BASE_DIR, "replica_floor.json")
EQUITY_CSV = os.path.join(BASE_DIR, "replica_equity.csv")
COST_WALL = os.path.join(BASE_DIR, "reports", "cost_wall.json")


def equity_series(limit=500):
    """Simulated equity snapshots, written from the first tick onward.

    Nothing is backfilled or synthesised — an empty series means the replica
    genuinely has no history yet, not that data is missing.
    """
    import csv
    try:
        with open(EQUITY_CSV) as f:
            rows = [(r["timestamp"], float(r["combined_equity"]))
                    for r in csv.DictReader(f) if r.get("combined_equity")]
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
    server_version = "EtoroPaperReplica/1.0"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, fallback, ctype="application/json; charset=utf-8"):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            body = fallback
        self._send(200, body, ctype)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(PAGE_PATH, "rb") as f:
                    body = f.read()
            except OSError:
                self._send(404, b"replica_dashboard.html not found",
                           "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/ledger":
            self._send_file(LEDGER, b'{"desks":{}}')
        elif path == "/costwall":
            self._send_file(COST_WALL, b'{"results":{}}')
        elif path == "/equity":
            self._send(200, json.dumps(equity_series()).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/costs":
            import venue_costs
            import venue_universe
            v = venue_costs.active()
            payload = {
                "venue": venue_costs.ACTIVE_VENUE,
                "venue_label": v["label"],
                "venue_status": v["status"],
                "venue_api": v.get("api"),
                "eligibility": v.get("eligibility", {}),
                "verification": venue_costs.verification_notice(),
                "universe_verification": venue_universe.verification_notice(),
                "instrument_classes": venue_costs.classes(),
                "account_fees": venue_costs.account_fees(),
                "reference_venues": {
                    name: {"label": vv["label"], "status": vv["status"],
                           "classes": vv["classes"]}
                    for name, vv in venue_costs.VENUES.items()
                    if name != venue_costs.ACTIVE_VENUE},
            }
            self._send(200, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        self._send(405, b"read-only viewer", "text/plain; charset=utf-8")

    def log_message(self, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18810)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Venue paper replica dashboard (read-only) on http://%s:%d"
          % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
