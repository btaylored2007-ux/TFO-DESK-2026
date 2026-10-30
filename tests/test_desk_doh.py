import os, sys, unittest, socket
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desk_doh as D

class T(unittest.TestCase):
    def tearDown(self):
        D.uninstall(); D._cache.clear()
        for k in list(D.stats): D.stats[k] = 0
    def test_install_ibkr_only(self):
        hs = D.install(ibkr=True, oanda=False, extra=False)
        self.assertIn("ftp2.interactivebrokers.com", hs)
        self.assertNotIn("api-fxpractice.oanda.com", hs)
        self.assertNotIn("api.binance.com", hs)
    def test_install_oanda(self):
        hs = D.install(ibkr=False, oanda=True, extra=False)
        self.assertIn("api-fxpractice.oanda.com", hs)
        self.assertIn("stream-fxpractice.oanda.com", hs)
    def test_kill_switch_env(self):
        D.install(ibkr=True)
        os.environ["TFO_DESK_DOH"] = "0"
        try:
            # with kill switch, _getaddrinfo must fall through (we just check _enabled)
            self.assertFalse(D._enabled())
        finally:
            os.environ.pop("TFO_DESK_DOH", None)
    def test_doh_lookup_ibkr_ftp(self):
        ips, ttl = D.doh_lookup("ftp2.interactivebrokers.com")
        self.assertTrue(ips); self.assertNotIn("202.169.44.80", ips)
    def test_patched_getaddrinfo_not_sinkhole(self):
        D.install(ibkr=True, oanda=False, extra=False)
        infos = socket.getaddrinfo("ftp2.interactivebrokers.com", 21, socket.AF_INET, socket.SOCK_STREAM)
        ips = {i[4][0] for i in infos}
        self.assertTrue(ips); self.assertNotIn("202.169.44.80", ips)

if __name__ == "__main__": unittest.main()
