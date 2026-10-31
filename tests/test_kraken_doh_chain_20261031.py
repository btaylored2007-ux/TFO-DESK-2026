"""Kraken futures stays on DoH even if desk_doh installs after kraken_dns (or the other way round)."""
import socket
import desk_doh as D
import kraken_dns as K


def test_futures_host_pinned_and_chain_skips_system_dns():
    saved_sock = socket.getaddrinfo
    saved_k = (K._orig_getaddrinfo, K._installed, dict(K._cache))
    saved_d = (D._orig_getaddrinfo, D._installed, D._hosts, dict(D._cache))
    calls = []
    def fake(*a, **k):
        calls.append(a[0])
        return [("fam", "ty", "pr", "", (a[0], a[1] if len(a) > 1 else 0))]
    try:
        K._installed = False; D._installed = False
        K._cache.clear(); D._cache.clear()
        socket.getaddrinfo = fake
        K.install()
        hs = D.install(ibkr=False, oanda=False, extra=False, data=True)
        assert "futures.kraken.com" in hs and "api.kraken.com" in hs and "ws.kraken.com" in hs
        D._cache["futures.kraken.com"] = (["104.17.1.1"], float("inf"))
        calls.clear()
        socket.getaddrinfo("futures.kraken.com", 443, socket.AF_INET)
        assert "futures.kraken.com" not in calls and "104.17.1.1" in calls
        # desk_doh with data off still falls through to the kraken_dns hook, not system DNS
        D._hosts = frozenset()
        K._cache["futures.kraken.com"] = (["9.9.9.9"], float("inf"))
        calls.clear()
        socket.getaddrinfo("futures.kraken.com", 443, 0)
        assert "futures.kraken.com" not in calls and "9.9.9.9" in calls
    finally:
        socket.getaddrinfo = saved_sock
        K._orig_getaddrinfo, K._installed = saved_k[0], saved_k[1]
        K._cache.clear(); K._cache.update(saved_k[2])
        D._orig_getaddrinfo, D._installed, D._hosts = saved_d[0], saved_d[1], saved_d[2]
        D._cache.clear(); D._cache.update(saved_d[3])


def test_public_fetchers_install_doh():
    import inspect
    import kraken_futures_risk_prices as F
    import tfo_sol_short as S
    import carry_deepening_lab as C
    assert "kraken_dns.install" in inspect.getsource(F.public_json)
    assert "kraken_dns.install" in inspect.getsource(S.get)
    assert "kraken_dns.install" in inspect.getsource(C._fetch)
