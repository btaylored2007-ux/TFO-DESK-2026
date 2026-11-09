"""Per-tenant provider keys. Fail closed without a server data key.

This is application encryption with Fernet, not a KMS. The data key stays in
the process environment. Provider keys are not written in plaintext.
"""

import os
from urllib.parse import urlparse

from customer_platform.errors import Denied, ProviderDisabled
from customer_platform.files import read_json, write_json

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception

ALLOW = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "grok": "api.x.ai",
    "deepseek": "api.deepseek.com",
}


class VaultUnavailable(Denied):
    pass


def vault_status():
    if Fernet is None:
        return {"available": False, "kms": False, "reason": "the cryptography package is not installed"}
    raw = os.environ.get("CUSTOMER_PLATFORM_DATA_KEY") or ""
    if not raw:
        return {"available": False, "kms": False, "reason": "CUSTOMER_PLATFORM_DATA_KEY is not set; provider keys are not stored"}
    try:
        Fernet(raw.encode("utf-8"))
    except Exception:
        return {"available": False, "kms": False, "reason": "CUSTOMER_PLATFORM_DATA_KEY is not a usable Fernet key"}
    return {"available": True, "kms": False, "reason": "encrypted with the server data key; this is not a KMS"}


def save_key(path, provider, secret):
    status = vault_status()
    if not status["available"]:
        raise VaultUnavailable(status["reason"])
    if provider not in ALLOW:
        raise Denied("provider is not configurable")
    text = str(secret or "")
    if len(text) < 12 or len(text) > 200:
        raise Denied("key was refused")
    body = read_json(path, {})
    token = Fernet(os.environ["CUSTOMER_PLATFORM_DATA_KEY"].encode("utf-8")).encrypt(text.encode("utf-8"))
    body[provider] = {"ciphertext": token.decode("utf-8"), "last4": text[-4:]}
    write_json(path, body)
    stored = open(path, encoding="utf-8").read()
    if text in stored:
        os.remove(path)
        raise Denied("key storage refused")
    return {"last4": text[-4:], "provider": provider, "stored": True}


def remove_key(path, provider):
    body = read_json(path, {})
    body.pop(provider, None)
    write_json(path, body)
    return {"provider": provider, "stored": False}


def public_keys(path):
    body = read_json(path, {})
    return [{"last4": row.get("last4"), "provider": name, "stored": True} for name, row in sorted(body.items())]


def connection_test(path, provider, transport=None):
    """One explicit test. No call is made unless a key is stored and a transport is passed.

    The product transport is off. A caller that supplies a transport is a test
    or a future reviewed client. The URL host is taken from the allowlist.
    """
    status = vault_status()
    if not status["available"]:
        return {"called": False, "reason": status["reason"]}
    body = read_json(path, {})
    row = body.get(provider)
    if not row:
        return {"called": False, "reason": "no key is stored"}
    if provider not in ALLOW:
        return {"called": False, "reason": "provider is not configurable"}
    if transport is None:
        return {"called": False, "host": ALLOW[provider], "reason": "outbound connection test is off until a reviewed client is configured"}
    try:
        secret = Fernet(os.environ["CUSTOMER_PLATFORM_DATA_KEY"].encode("utf-8")).decrypt(row["ciphertext"].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return {"called": False, "reason": "stored key could not be opened"}
    url = "https://{0}/v1/models".format(ALLOW[provider])
    host = urlparse(url).hostname
    if host != ALLOW[provider]:
        return {"called": False, "reason": "host was refused"}
    result = transport(url, secret)
    return {"called": True, "host": host, "ok": bool(result.get("ok")), "status": result.get("status")}
