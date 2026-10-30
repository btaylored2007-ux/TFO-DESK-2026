"""DESK-ROOT shared copy (research/entry_engine/kraken_dns.py is the entry-engine copy). Scoped DNS-over-HTTPS resolver for Kraken PUBLIC hosts only (2026-10-27 WITA, Brett-approved).

Why: on the current Indonesian Wi-Fi every plain port-53 DNS answer for api.kraken.com (system, 1.1.1.1, 8.8.8.8) is
hijacked to a block-page IP (202.169.44.80) that refuses connections. DoH (Cloudflare 1.1.1.1 / Google 8.8.8.8, reached
by IP so their own lookup cannot be hijacked) returns the real Cloudflare IPs, and HTTPS to them with SNI/cert for
api.kraken.com works. So: patch socket.getaddrinfo in THIS process for KRAKEN_HOSTS only. TLS still verifies the
api.kraken.com certificate (urllib keeps the hostname for SNI + verification); nothing else is rerouted.
No keys, no sudo, no system config. Kill switch: env TFO_KRAKEN_DOH=0 or file state/entry_engine/KRAKEN_DOH_OFF.
"""
import ipaddress, json, os, socket, threading, time, urllib.request

KRAKEN_HOSTS = frozenset({"api.kraken.com", "futures.kraken.com", "ws.kraken.com"})  # public hosts only
DOH_URLS = ("https://1.1.1.1/dns-query?name=%s&type=A", "https://8.8.8.8/resolve?name=%s&type=A")
BLOCK_IPS = frozenset({"202.169.44.80"})   # observed ISP sinkhole answer; never used even if DoH ever returned it
TTL_MIN, TTL_MAX, DOH_TIMEOUT = 60, 600, 5
_HERE = os.path.dirname(os.path.abspath(__file__))
OFF_FILE = os.path.join(_HERE, "state", "entry_engine", "KRAKEN_DOH_OFF")  # same kill file as the entry-engine copy

_orig_getaddrinfo = socket.getaddrinfo
_cache = {}          # host -> (ips, expiry_monotonic)
_lock = threading.Lock()
_installed = False
stats = {"doh_ok": 0, "doh_fail": 0, "fallback_system": 0, "stale_used": 0}


def _usable(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and ip not in BLOCK_IPS and a.is_global


def _fetch(url, timeout):
    req = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": "TFO-EntryEngine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def doh_lookup(host, fetch=_fetch):
    """Return (ips, ttl) from the first DoH provider that gives usable A records; raise if none do."""
    err = None
    for tmpl in DOH_URLS:
        try:
            d = fetch(tmpl % host, DOH_TIMEOUT)
            ans = [a for a in d.get("Answer", []) if a.get("type") == 1]
            ips = [a["data"] for a in ans if _usable(a.get("data", ""))]
            if ips:
                ttl = min(int(a.get("TTL", TTL_MIN)) for a in ans)
                return ips, max(TTL_MIN, min(TTL_MAX, ttl))
            err = RuntimeError("no usable A records from %s" % tmpl.split("/")[2])
        except Exception as e:
            err = e
    raise RuntimeError("DoH failed for %s: %s" % (host, err))


def resolve(host, fetch=_fetch, now=time.monotonic):
    with _lock:
        hit = _cache.get(host)
        if hit and hit[1] > now():
            return hit[0]
    try:
        ips, ttl = doh_lookup(host, fetch)
        with _lock:
            _cache[host] = (ips, now() + ttl); stats["doh_ok"] += 1
        return ips
    except Exception:
        with _lock:
            stats["doh_fail"] += 1
            if hit:                      # stale last-good beats the sinkhole
                stats["stale_used"] += 1
                return hit[0]
        return None


def _enabled():
    return os.environ.get("TFO_KRAKEN_DOH", "1") != "0" and not os.path.exists(OFF_FILE)


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    h = host.decode() if isinstance(host, bytes) else host
    if isinstance(h, str) and h.lower().rstrip(".") in KRAKEN_HOSTS and family in (0, socket.AF_INET) and _enabled():
        ips = resolve(h.lower().rstrip("."))
        if ips:
            out = []
            for ip in ips:
                out.extend(_orig_getaddrinfo(ip, port, socket.AF_INET, type, proto, flags | socket.AI_NUMERICHOST))
            if out:
                return out
        stats["fallback_system"] += 1
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def install():
    """Idempotent. Patches socket.getaddrinfo for this process only."""
    global _installed
    if not _installed:
        socket.getaddrinfo = _getaddrinfo
        _installed = True
    return _installed


def uninstall():
    global _installed
    socket.getaddrinfo = _orig_getaddrinfo
    _installed = False
