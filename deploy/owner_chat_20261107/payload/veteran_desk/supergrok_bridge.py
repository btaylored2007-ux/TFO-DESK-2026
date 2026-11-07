"""SuperGrok page-UI client. Local composer bridge only.

Enabled only when a fresh live health check says the grok.com composer is
logged in. A status file, the Cursor Grok route, and the super fan-out are
not this provider. This module does not read cookies or tokens, does not call
grok.com, and does not start a browser.
"""
import json
import os
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:18892"
STATUS_FILE = "/opt/tfo/live/thf/state/thf/supergrok_health.json"
FRESH_S = 120
PROVIDER = "supergrok"
ROUTE = "grok_com_page_ui"


class Reply(str):
    def __new__(cls, text, metadata):
        obj = str.__new__(cls, text)
        obj.provider = metadata.get("provider")
        obj.route = metadata.get("route")
        obj.request_id = metadata.get("request_id")
        obj.endpoint = metadata.get("endpoint")
        return obj


def _endpoint(url):
    if url != URL:
        raise RuntimeError("SuperGrok client refuses a non-local bridge")
    return url


def classify(body, fetched_at=None):
    fetched_at = time.time() if fetched_at is None else fetched_at
    if not isinstance(body, dict) or not body:
        return "unavailable", "SuperGrok page bridge did not answer"
    if body.get("removed") or body.get("stub") or body.get("state") == "removed":
        return "unavailable", "SuperGrok worker is not running"
    state = str(body.get("state") or "")
    checked = body.get("checked_epoch")
    if checked is not None:
        try:
            age = fetched_at - float(checked)
        except (TypeError, ValueError):
            age = FRESH_S + 1
        if age > FRESH_S or age < -30:
            return "stale", "SuperGrok health is outside the freshness window"
    if state in ("captcha", "cloudflare", "blocked", "challenge"):
        return "blocked", "SuperGrok is blocked on the normal page. No bypass is attempted."
    if state in ("needs_signin", "login", "logged_out", "login_required"):
        return "login_required", "SuperGrok needs an owner sign-in on the normal page"
    composer = body.get("composer")
    if state == "logged_in" and composer in (True, "ready", "logged_in"):
        return "ready", "SuperGrok composer is ready on the local page bridge"
    if state == "logged_in":
        return "blocked", "SuperGrok is signed in but the composer is not ready"
    if state:
        return "unavailable", "SuperGrok state is not a ready composer"
    return "unavailable", "SuperGrok health had no state"


def snapshot(path=None):
    """Last observation only. Missing or old data is not a connection."""
    try:
        body = json.load(open(path or STATUS_FILE, encoding="utf-8"))
    except Exception:
        return "unavailable", "SuperGrok has no fresh composer check"
    return classify(body)


def block_reason():
    state, note = snapshot()
    if state == "ready":
        return None
    return note


def ready():
    return block_reason() is None


def observe(body, path=None, fetched_at=None):
    state, note = classify(body, fetched_at=fetched_at)
    record = dict(body if isinstance(body, dict) else {})
    record.update(state=state, note=note, provider=PROVIDER, route=ROUTE, checked_epoch=time.time() if fetched_at is None else fetched_at)
    target = path or STATUS_FILE
    if os.environ.get("PYTEST_CURRENT_TEST") and target == STATUS_FILE:
        return record
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(record, stream)
    os.replace(temporary, target)
    return record


def live_health(url=None, timeout=3):
    target = _endpoint(url or URL)
    try:
        payload = json.loads(urllib.request.urlopen(target + "/health", timeout=timeout).read())
    except Exception:
        payload = {}
    payload = dict(payload if isinstance(payload, dict) else {})
    payload.setdefault("checked_epoch", time.time())
    return payload


def ask(prompt, timeout=280, images=None, seat=None, url=None, health_body=None):
    if images:
        raise RuntimeError("SuperGrok page route has no image attach in this package")
    body = health_body if health_body is not None else live_health(url)
    state, note = classify(body)
    if state != "ready":
        raise RuntimeError(note)
    target = _endpoint(url or URL)
    request_id = "sg-%d" % int(time.time() * 1000)
    encoded = json.dumps({"prompt": str(prompt or ""), "timeout": int(timeout), "request_id": request_id}).encode()
    request = urllib.request.Request(target + "/ask", data=encoded, headers={"Content-Type": "application/json"})
    try:
        payload = json.loads(urllib.request.urlopen(request, timeout=timeout + 60).read())
    except urllib.error.URLError as error:
        raise RuntimeError("SuperGrok page bridge did not answer") from error
    if payload.get("provider") not in (None, PROVIDER) or payload.get("route") not in (None, ROUTE):
        raise RuntimeError("SuperGrok bridge returned a different route")
    if not payload.get("ok") or not str(payload.get("text") or "").strip():
        raise RuntimeError("SuperGrok " + str(payload.get("error") or "empty"))
    return Reply(payload["text"], {"provider": PROVIDER, "route": ROUTE, "request_id": request_id, "endpoint": "127.0.0.1:18892", "seat": seat})
