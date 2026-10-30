"""Scoped DNS-over-HTTPS resolver for desk hosts hijacked on Indonesian Wi-Fi (2026-10-27 audit).

Same pattern as research/entry_engine/kraken_dns.py: patch socket.getaddrinfo in THIS process only for
DESK_DOH_HOSTS. TLS still verifies the original hostname (SNI + cert). No keys, no sudo, no system config.
Kill switch: env TFO_DESK_DOH=0 or file state/DESK_DOH_OFF.

Host groups (union is what install() patches):
  IBKR_HOSTS  — web + FTP short-stock file (borrow refresh) + dyn CDN login hosts
  OANDA_HOSTS — practice API + stream only (never live api-fxtrade)
  EXTRA_HOSTS — Binance / Coinbase / OKX / Alpaca / Polymarket (default ON since 2026-10-28; opt out TFO_DESK_DOH_EXTRA=0)
  DATA_HOSTS  — Kraken spot REST + Yahoo + SEC price/filing reads (default ON since 2026-10-28; Kraken
                order paths stay in kraken_dns.py). Opt out: install(data=False) or TFO_DESK_DOH_DATA=0.
"""
import ipaddress, json, os, socket, threading, time, urllib.request

IBKR_HOSTS = frozenset({
    "www.interactivebrokers.com",
    "interactivebrokers.com",
    "ndcdyn.interactivebrokers.com",
    "cdcdyn.interactivebrokers.com",
    "ftp2.interactivebrokers.com",
    "ftp3.interactivebrokers.com",
    "www.interactivebrokers.co.uk",
    "www.interactivebrokers.com.hk",
    "api.ibkr.com",
})
OANDA_HOSTS = frozenset({
    "api-fxpractice.oanda.com",
    "stream-fxpractice.oanda.com",
})
DATA_HOSTS = frozenset({
    "api.kraken.com",
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "www.sec.gov",
})
EXTRA_HOSTS = frozenset({
    "api.binance.com",
    "api.exchange.coinbase.com",
    "www.okx.com",
    "data.alpaca.markets",
    "paper-api.alpaca.markets",
    "gamma-api.polymarket.com",
    "clob.polymarket.com",
})
DOH_URLS = ("https://1.1.1.1/dns-query?name=%s&type=A", "https://8.8.8.8/resolve?name=%s&type=A")
BLOCK_IPS = frozenset({"202.169.44.80"})
TTL_MIN, TTL_MAX, DOH_TIMEOUT = 60, 600, 5
_HERE = os.path.dirname(os.path.abspath(__file__))
OFF_FILE = os.path.join(_HERE, "state", "DESK_DOH_OFF")

_orig_getaddrinfo = socket.getaddrinfo
_cache = {}
_lock = threading.Lock()
_installed = False
_hosts = frozenset()
stats = {"doh_ok": 0, "doh_fail": 0, "fallback_system": 0, "stale_used": 0}


def _usable(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and ip not in BLOCK_IPS and a.is_global


def _fetch(url, timeout):
    req = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": "TFO-DeskDoH/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def doh_lookup(host, fetch=_fetch):
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
            if hit:
                stats["stale_used"] += 1
                return hit[0]
        return None


def _enabled():
    return os.environ.get("TFO_DESK_DOH", "1") != "0" and not os.path.exists(OFF_FILE)


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    h = host.decode() if isinstance(host, bytes) else host
    if isinstance(h, str) and h.lower().rstrip(".") in _hosts and family in (0, socket.AF_INET) and _enabled():
        ips = resolve(h.lower().rstrip("."))
        if ips:
            out = []
            for ip in ips:
                out.extend(_orig_getaddrinfo(ip, port, socket.AF_INET, type, proto, flags | socket.AI_NUMERICHOST))
            if out:
                return out
        stats["fallback_system"] += 1
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def install(ibkr=True, oanda=True, extra=None, data=True):
    """Idempotent. Patches socket.getaddrinfo for this process only. extra=True (or TFO_DESK_DOH_EXTRA=1) adds exchange hosts."""
    global _installed, _hosts
    hosts = set()
    if ibkr: hosts |= set(IBKR_HOSTS)
    if oanda: hosts |= set(OANDA_HOSTS)
    if data and os.environ.get("TFO_DESK_DOH_DATA", "1") != "0":
        hosts |= set(DATA_HOSTS)
    if extra or (extra is None and os.environ.get("TFO_DESK_DOH_EXTRA", "1") != "0"):   # 2026-10-28 (Brett B4): venue hosts default ON (extra=None); explicit extra=False stays scoped; opt out TFO_DESK_DOH_EXTRA=0
        hosts |= set(EXTRA_HOSTS)
    _hosts = frozenset(h.lower() for h in hosts)
    if not _installed:
        socket.getaddrinfo = _getaddrinfo
        _installed = True
    return sorted(_hosts)


def uninstall():
    global _installed, _hosts
    socket.getaddrinfo = _orig_getaddrinfo
    _installed = False
    _hosts = frozenset()
