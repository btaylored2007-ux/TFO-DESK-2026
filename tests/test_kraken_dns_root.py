"""Offline tests for kraken_dns (no network)."""
import os, socket, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kraken_dns as K

GOOD = {"Status": 0, "Answer": [{"type": 1, "TTL": 120, "data": "104.17.186.205"}, {"type": 1, "TTL": 300, "data": "104.17.187.205"}]}
SINK = {"Status": 0, "Answer": [{"type": 1, "TTL": 120, "data": "202.169.44.80"}]}


class T(unittest.TestCase):
    def setUp(self):
        K._cache.clear(); os.environ.pop("TFO_KRAKEN_DOH", None)

    def test_doh_parses_and_clamps_ttl(self):
        ips, ttl = K.doh_lookup("api.kraken.com", fetch=lambda u, t: GOOD)
        self.assertEqual(ips, ["104.17.186.205", "104.17.187.205"]); self.assertEqual(ttl, 120)

    def test_sinkhole_and_private_rejected(self):
        self.assertFalse(K._usable("202.169.44.80")); self.assertFalse(K._usable("10.0.0.1")); self.assertFalse(K._usable("127.0.0.1"))
        with self.assertRaises(RuntimeError):
            K.doh_lookup("api.kraken.com", fetch=lambda u, t: SINK)

    def test_second_provider_used_when_first_fails(self):
        calls = []
        def f(u, t):
            calls.append(u)
            if "1.1.1.1" in u: raise OSError("down")
            return GOOD
        ips, _ = K.doh_lookup("api.kraken.com", fetch=f)
        self.assertEqual(len(calls), 2); self.assertTrue(ips)

    def test_cache_and_stale_fallback(self):
        clock = [0.0]
        self.assertTrue(K.resolve("api.kraken.com", fetch=lambda u, t: GOOD, now=lambda: clock[0]))
        clock[0] = 10_000
        def boom(u, t): raise OSError("down")
        self.assertEqual(K.resolve("api.kraken.com", fetch=boom, now=lambda: clock[0]), ["104.17.186.205", "104.17.187.205"])

    def test_total_failure_returns_none(self):
        def boom(u, t): raise OSError("down")
        self.assertIsNone(K.resolve("api.kraken.com", fetch=boom))

    def test_scope_non_kraken_untouched(self):
        seen = []
        orig = K._orig_getaddrinfo
        K._orig_getaddrinfo = lambda *a, **k: seen.append(a[0]) or [("x",)]
        try:
            K._getaddrinfo("query1.finance.yahoo.com", 443)
            self.assertEqual(seen, ["query1.finance.yahoo.com"])
            K._cache["api.kraken.com"] = (["104.17.186.205"], float("inf"))
            seen.clear(); K._getaddrinfo("api.kraken.com", 443)
            self.assertEqual(seen, ["104.17.186.205"])
            os.environ["TFO_KRAKEN_DOH"] = "0"; seen.clear(); K._getaddrinfo("api.kraken.com", 443)
            self.assertEqual(seen, ["api.kraken.com"])
        finally:
            K._orig_getaddrinfo = orig

    def test_install_idempotent(self):
        # 2026-10-28 isolation fix: earlier tests may import modules that already installed the
        # hook (or replaced socket.getaddrinfo), leaving _installed=True. Start from a clean
        # state and restore whatever was there afterwards so test order no longer matters.
        saved_fn, saved_flag = socket.getaddrinfo, K._installed
        socket.getaddrinfo = K._orig_getaddrinfo; K._installed = False
        try:
            try:
                K.install(); K.install(); self.assertIs(socket.getaddrinfo, K._getaddrinfo)
            finally:
                K.uninstall()
            self.assertIs(socket.getaddrinfo, K._orig_getaddrinfo)
        finally:
            socket.getaddrinfo, K._installed = saved_fn, saved_flag


if __name__ == "__main__":
    unittest.main()
