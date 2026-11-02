"""Signed one-time funder actions.

The dashboard gets a screen revision with each status payload. An action must
carry that revision, a nonce and an HMAC. A reused nonce, a stale revision, an
expired token, or a timestamp sitting in the future is rejected. No LLM.
"""
import hashlib, hmac, json, os

from thf_guardian.util import parse_dt

TTL_SEC = 120


def _msg(body):
    return json.dumps(body, sort_keys=True, default=str, separators=(",", ":")).encode()


def sign_action(secret, body):
    sig = hmac.new(str(secret).encode(), _msg(body), hashlib.sha256).hexdigest()
    return dict(body=body, sig=sig)


def issue(secret, kind, args, now, screen_rev, ttl=TTL_SEC):
    from thf_guardian.util import wita_str
    exp = now.timestamp() + ttl
    body = dict(kind=kind, args=args or {}, iat=now.isoformat(), exp=exp, screen_rev=int(screen_rev),
                nonce=os.urandom(8).hex(), paper_only=True, wita=wita_str(now))
    return sign_action(secret, body)


def verify(secret, token, now, screen_rev, used, future_sec=60):
    """Return (ok, reason). Does not record the nonce; the caller does that after it acts."""
    if not isinstance(token, dict) or "body" not in token or "sig" not in token:
        return False, "malformed"
    body = token["body"]
    expect = hmac.new(str(secret).encode(), _msg(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, str(token["sig"])):
        return False, "bad signature"
    nonce = body.get("nonce")
    if not nonce or nonce in (used or set()):
        return False, "replay"
    iat = parse_dt(body.get("iat"))
    if iat is None:
        return False, "missing iat"
    ahead = (iat - now).total_seconds()
    if ahead > future_sec:
        return False, "clock drift"
    try:
        exp = float(body.get("exp"))
    except (TypeError, ValueError):
        return False, "missing exp"
    if now.timestamp() > exp:
        return False, "expired"
    if int(body.get("screen_rev", -1)) != int(screen_rev):
        return False, "stale screen"
    return True, "ok"
