"""Attach the guardian panel and /api/guardian* routes to thf_dash.

The dashboard process stays on its existing bind. These routes proxy to the
guardian inbox on 127.0.0.1 only. If the inbox is down, GET still serves the
last status file.
"""
import json, os, urllib.request

from thf_guardian.dash_panel import panel_html
from thf_guardian.util import read_json

ALLOWED = {"/api/guardian_status", "/api/guardian_thread", "/api/guardian_alerts", "/api/guardian_msg"}


def _local_base():
    return "http://127.0.0.1:%s" % os.environ.get("THF_GUARDIAN_PORT", "18871")


def proxy(handler):
    path = handler.path.split("?")[0]
    if path not in ALLOWED:
        return handler._send(404, "not found", "text/plain")
    url = _local_base() + handler.path
    try:
        if handler.command == "POST":
            n = int(handler.headers.get("Content-Length") or 0)
            data = handler.rfile.read(n) if n else b"{}"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            body = urllib.request.urlopen(req, timeout=10).read()
        else:
            body = urllib.request.urlopen(url, timeout=5).read()
        handler._send(200, body.decode(), "application/json")
    except Exception:
        if path == "/api/guardian_status":
            fallback = read_json(os.environ.get("THF_GUARDIAN_STATUS", "/opt/tfo/live/thf/state/thf/guardian_status.json"), {}) or {}
            fallback = dict(fallback, bridge="down", inbox="down")
            return handler._send(200, json.dumps(fallback), "application/json")
        handler._send(503, json.dumps(dict(error="guardian inbox down", paper_only=True)), "application/json")


def install(handler_cls, module=None):
    if module is not None and hasattr(module, "_fm_panel"):
        orig = module._fm_panel

        def _fm_panel():
            try:
                base = orig() or ""
            except Exception:
                base = ""
            return base + panel_html()

        module._fm_panel = _fm_panel
    orig_get = handler_cls.do_GET
    orig_post = getattr(handler_cls, "do_POST", None)

    def do_GET(self):
        if self.path.split("?")[0] in ALLOWED:
            return proxy(self)
        return orig_get(self)

    def do_POST(self):
        if self.path.split("?")[0] in ALLOWED:
            return proxy(self)
        if orig_post is None:
            return self._send(404, "not found", "text/plain")
        return orig_post(self)

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
