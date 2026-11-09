"""Product provider client.

Adapters are openai, anthropic, grok, and deepseek. Each one posts only to
its fixed HTTPS endpoint. The customer key is sent in the request header and
is not written into the stored reply. Public egress stays off unless the
operator sets CUSTOMER_PLATFORM_PROVIDER_EGRESS=allow. A test may replace the
opener with a scratch socket. That scratch reply is not provider acceptance.

The model returns a schema. It does not set a price, a feed, or an approval.
"""

import json
import os
import re
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from customer_platform.ai_vault import ALLOW
from customer_platform.errors import Denied

SEND_PHRASE = "Send to my provider"
TIMEOUT_SECONDS = 5
MAX_REPLY = 500
EGRESS_ENV = "CUSTOMER_PLATFORM_PROVIDER_EGRESS"

ADAPTERS = {
    "openai": {"host": "api.openai.com", "style": "openai", "url": "https://api.openai.com/v1/chat/completions"},
    "anthropic": {"host": "api.anthropic.com", "style": "anthropic", "url": "https://api.anthropic.com/v1/messages"},
    "grok": {"host": "api.x.ai", "style": "openai", "url": "https://api.x.ai/v1/chat/completions"},
    "deepseek": {"host": "api.deepseek.com", "style": "openai", "url": "https://api.deepseek.com/v1/chat/completions"},
}

_FIELDS = {"market", "side", "stop_percent", "symbol"}
_HOSTILE = re.compile(r"ignore the stop|ignore stop|__import__|\bexec\s*\(|\beval\s*\(|\bimport\b", re.I)


def public_egress_allowed():
    return os.environ.get(EGRESS_ENV) == "allow"


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not followed. The Authorization header stays on the first host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Denied("provider redirect was refused")


def official_request(request):
    parsed = urlparse(getattr(request, "full_url", "") or "")
    if parsed.scheme != "https" or parsed.hostname not in ALLOW.values() or parsed.username or parsed.port:
        raise Denied("provider endpoint was refused")
    if parsed.hostname not in {row["host"] for row in ADAPTERS.values()}:
        raise Denied("provider endpoint was refused")
    expected = [row["url"] for row in ADAPTERS.values() if row["host"] == parsed.hostname]
    if request.full_url not in expected:
        raise Denied("provider endpoint was refused")


def product_urlopen(request, timeout):
    """Real HTTPS opener. Refuses any other host and does not follow redirects."""
    official_request(request)
    if not public_egress_allowed():
        raise Denied("public egress is off")
    opener = urllib.request.build_opener(RefuseRedirect)
    response = opener.open(request, timeout=timeout)
    _reject_redirect(response, request.full_url)
    return response


class ProviderClient:
    implemented = True

    def __init__(self, opener=None):
        self.opener = opener or product_urlopen
        self.transport_name = "official" if self.opener is product_urlopen else "scratch"

    def decide(self, *, provider, secret, teaching, reviewed=None):
        if provider not in ADAPTERS:
            return _refused("provider is not configurable", provider)
        if _HOSTILE.search(teaching or ""):
            return _refused("teaching stays in review", provider, called=False)
        spec = ADAPTERS[provider]
        payload = _payload(spec["style"], teaching, reviewed)
        try:
            raw, synthetic = self._post(spec, secret, payload)
        except Denied as exc:
            return _refused(str(exc), provider, called=False)
        except Exception:
            return _refused("provider failed", provider, called=True)
        if secret and secret in raw:
            return _refused("response withheld", provider, called=True)
        parsed = parse_decision(raw)
        parsed["called"] = True
        parsed["host"] = spec["host"]
        parsed["provider"] = provider
        parsed["transport"] = self.transport_name
        parsed["synthetic"] = self.transport_name != "official"
        if reviewed and parsed.get("ok"):
            expected = reviewed.get("stop_percent")
            if expected in (None, ""):
                expected = reviewed.get("percent")
            if parsed["symbol"] != reviewed.get("symbol") or parsed["stop_percent"] != str(expected):
                return _refused("model changed the reviewed rule", provider, called=True, synthetic=synthetic)
        return parsed

    def probe(self, *, provider, secret):
        if provider not in ADAPTERS:
            return {"called": False, "client": "configured", "ok": False, "reason": "provider is not configurable"}
        spec = ADAPTERS[provider]
        try:
            raw, synthetic = self._post(spec, secret, _payload(spec["style"], "connection test", None))
        except Denied as exc:
            return {"called": False, "client": "configured", "ok": False, "reason": str(exc)}
        except Exception:
            return {"called": True, "client": "configured", "ok": False, "reason": "provider failed"}
        if secret and secret in raw:
            return {"called": True, "client": "configured", "ok": False, "reason": "response withheld"}
        return {
            "called": True,
            "client": "configured",
            "host": spec["host"],
            "ok": True,
            "reason": "scratch reply stored; not a provider acceptance" if synthetic else "provider reply stored; live acceptance was not certified here",
            "synthetic": synthetic,
        }

    def _post(self, spec, secret, payload):
        url = spec["url"]
        _guard(url, spec["host"])
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(data))}
        if spec["style"] == "anthropic":
            headers["x-api-key"] = secret
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = "Bearer " + secret
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        response = self.opener(request, TIMEOUT_SECONDS)
        _reject_redirect(response, url)
        status = int(getattr(response, "status", 200))
        body = response.read(MAX_REPLY + 1)
        if status >= 400 or len(body) > MAX_REPLY:
            raise RuntimeError("provider failed")
        text = body.decode("utf-8", "replace")
        try:
            outer = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid schema") from exc
        return _message_text(outer, spec["style"]), self.transport_name != "official"


def parse_decision(text):
    if not isinstance(text, str) or not text or len(text) > MAX_REPLY:
        return {"ok": False, "reason": "invalid schema"}
    if _HOSTILE.search(text):
        return {"ok": False, "reason": "teaching stays in review"}
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "invalid schema"}
    if not isinstance(body, dict):
        return {"ok": False, "reason": "invalid schema"}
    if "feed" in body or "price" in body or "limit" in body or "limit_price" in body:
        return {"ok": False, "reason": "hallucinated feed"}
    if set(body) != _FIELDS:
        return {"ok": False, "reason": "invalid schema"}
    symbol = str(body.get("symbol") or "")
    if not re.fullmatch(r"[A-Z]{1,8}", symbol):
        return {"ok": False, "reason": "invalid schema"}
    if body.get("side") != "buy" or body.get("market") != "shares":
        return {"ok": False, "reason": "invalid schema"}
    try:
        width = Decimal(str(body.get("stop_percent")))
    except (InvalidOperation, ValueError):
        return {"ok": False, "reason": "invalid schema"}
    if not width.is_finite() or width <= 0 or width > Decimal("20"):
        return {"ok": False, "reason": "invalid schema"}
    percent = format(width, "f")
    if "." in percent:
        percent = percent.rstrip("0").rstrip(".")
    return {"ok": True, "market": "shares", "reason": "", "side": "buy", "stop_percent": percent, "symbol": symbol}


def _payload(style, teaching, reviewed):
    instruction = (
        "Return one JSON object with keys symbol, side, stop_percent, market. "
        "side is buy. market is shares. Do not include a price, a feed, or an approval."
    )
    if reviewed:
        instruction += " Reviewed symbol {symbol}. Reviewed stop percent {stop}.".format(
            symbol=reviewed.get("symbol") or "", stop=reviewed.get("stop_percent") or "",
        )
    content = instruction + "\n" + str(teaching or "")[:1000]
    if style == "anthropic":
        return {"max_tokens": 200, "messages": [{"content": content, "role": "user"}], "model": "claude"}
    return {"messages": [{"content": content, "role": "user"}], "model": "provider"}


def _message_text(body, style):
    if not isinstance(body, dict):
        raise RuntimeError("invalid schema")
    if style == "anthropic":
        parts = body.get("content") or []
        if not parts or not isinstance(parts, list):
            raise RuntimeError("invalid schema")
        return str(parts[0].get("text") or "")
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("invalid schema")
    return str((choices[0].get("message") or {}).get("content") or "")


def _reject_redirect(response, url):
    status = int(getattr(response, "status", 200) or 200)
    if status in {301, 302, 303, 307, 308}:
        raise Denied("provider redirect was refused")
    final = response.geturl() if hasattr(response, "geturl") else url
    if final != url:
        raise Denied("provider redirect was refused")


def _guard(url, host):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.port:
        raise Denied("provider endpoint was refused")
    if url != ADAPTERS[_provider_for_host(host)]["url"]:
        raise Denied("provider endpoint was refused")


def _provider_for_host(host):
    for name, spec in ADAPTERS.items():
        if spec["host"] == host:
            return name
    raise Denied("provider endpoint was refused")


def _refused(reason, provider, called=False, synthetic=False):
    spec = ADAPTERS.get(provider) or {}
    return {
        "called": called,
        "host": spec.get("host") or "",
        "ok": False,
        "provider": provider,
        "reason": reason,
        "synthetic": synthetic,
    }
