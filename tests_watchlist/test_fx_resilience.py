#!/usr/bin/env python3
"""HT decision 2026-09-26 #3: FX retries + last-good cache. Offline (urlopen patched to time out); temp cache only."""
import datetime as dt, importlib, io, json, os, socket, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
UTC = dt.timezone.utc
TUE = dt.datetime(2026, 9, 29, 3, 0, tzinfo=UTC)      # Mon 23:00 NY: FX open
SAT = dt.datetime(2026, 9, 26, 6, 0, tzinfo=UTC)      # Sat 02:00 NY: FX closed

def iso(t): return t.isoformat().replace("+00:00", "Z")
def jload(p):
    with open(p) as f: return json.load(f)

class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FX(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module("tfo_equity_refresh")
        self.d = tempfile.TemporaryDirectory()
        self.old = (self.m.FX_CACHE_PATH, self.m._fx_sleep)
        self.m.FX_CACHE_PATH = Path(self.d.name) / "fx_last_good.json"
        self.sleeps = []; self.m._fx_sleep = self.sleeps.append
        self.calls = 0
    def tearDown(self):
        self.m.FX_CACHE_PATH, self.m._fx_sleep = self.old; self.d.cleanup()

    def _timeout(self, *a, **k):
        self.calls += 1; raise socket.timeout("timed out")
    def _seed(self, quote_time, rate=0.66):
        with open(self.m.FX_CACHE_PATH, "w") as f:
            json.dump({"rates": {"AUD": {"rate": rate, "quote_utc": iso(quote_time), "fetched_at": iso(quote_time), "source": "Yahoo public 1m FX bar"}}}, f)
    def _run(self, now, ccys=("AUD",)):
        with mock.patch.object(self.m.urllib.request, "urlopen", self._timeout), \
             mock.patch.object(self.m.dt, "datetime", wraps=dt.datetime) as fake, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            fake.now = lambda tz=None: now
            rates, fails = self.m.fetch_fx_evidence(list(ccys))
        return rates, fails, err.getvalue()

    def test_timeout_retries_twice_then_uses_fresh_cache(self):
        self._seed(TUE - dt.timedelta(hours=2))
        rates, fails, err = self._run(TUE)
        self.assertEqual(self.calls, 3); self.assertEqual(self.sleeps, [0.5, 1.5])
        self.assertEqual(fails, {})
        ev = rates["AUD"]
        self.assertEqual((ev["rate"], ev["status"], ev["paper_usable"]), (0.66, "cached", True))
        self.assertIn("FX CACHE USED AUD", err)

    def test_cache_older_than_6h_on_weekday_fails(self):
        self._seed(TUE - dt.timedelta(hours=7))
        rates, fails, _ = self._run(TUE)
        self.assertNotIn("AUD", rates); self.assertIn("cache too old", fails["AUD"])

    def test_weekend_allows_72h_cache(self):
        self._seed(SAT - dt.timedelta(hours=40))
        rates, fails, _ = self._run(SAT)
        self.assertEqual(fails, {}); self.assertEqual(rates["AUD"]["status"], "cached")
        self._seed(SAT - dt.timedelta(hours=73))
        self.assertIn("AUD", self._run(SAT)[1])

    def test_no_cache_fails(self):
        rates, fails, _ = self._run(TUE)
        self.assertIn("no cached rate", fails["AUD"]); self.assertEqual(self.calls, 3)

    def test_success_writes_last_good_and_old_bar_inside_window_is_paper_usable(self):
        bar = int((SAT - dt.timedelta(hours=10)).timestamp())
        body = json.dumps({"chart": {"result": [{"meta": {"currency": "USD"}, "timestamp": [bar],
                           "indicators": {"quote": [{"close": [0.65]}]}}]}}).encode()
        attempts = []
        def flaky(*a, **k):
            attempts.append(1)
            if len(attempts) == 1: raise socket.timeout("timed out")
            return FakeResp(body)
        with mock.patch.object(self.m.urllib.request, "urlopen", flaky), \
             mock.patch.object(self.m.dt, "datetime", wraps=dt.datetime) as fake, \
             mock.patch.object(self.m, "utc_now", lambda: iso(SAT)):
            fake.now = lambda tz=None: SAT
            fake.fromisoformat = dt.datetime.fromisoformat
            rates, fails = self.m.fetch_fx_evidence(["AUD"])
        self.assertEqual(len(attempts), 2); self.assertEqual(fails, {})
        self.assertEqual(rates["AUD"]["status"], "stale"); self.assertTrue(rates["AUD"]["paper_usable"])
        saved = jload(self.m.FX_CACHE_PATH)["rates"]["AUD"]
        self.assertEqual(saved["rate"], 0.65)
        import book_coverage
        self.assertFalse(set(jload(self.m.FX_CACHE_PATH)) & set(book_coverage.LEDGER_HINTS))

    def test_market_calendar(self):
        self.assertTrue(self.m.fx_market_closed(SAT)); self.assertFalse(self.m.fx_market_closed(TUE))
        self.assertTrue(self.m.fx_market_closed(dt.datetime(2026, 12, 25, 15, tzinfo=UTC)))
        self.assertTrue(self.m.fx_market_closed(dt.datetime(2026, 9, 25, 21, 30, tzinfo=UTC)))   # Fri 17:30 NY
        self.assertFalse(self.m.fx_market_closed(dt.datetime(2026, 9, 27, 21, 30, tzinfo=UTC)))  # Sun 17:30 NY

    def test_collect_quotes_cached_fx_keeps_exit_paper_executable(self):
        self._seed(TUE - dt.timedelta(hours=1))
        q = {"ccy": "AUD", "px": 10.0, "executable": True, "paper_executable": True}
        with mock.patch.object(self.m.urllib.request, "urlopen", self._timeout), \
             mock.patch.object(self.m.dt, "datetime", wraps=dt.datetime) as fake, mock.patch("sys.stderr", new_callable=io.StringIO):
            fake.now = lambda tz=None: TUE
            quotes, fails = self.m.collect_quotes(["STO.AX"], fetcher=lambda s: dict(q))
        from paper_execution_price import eligible
        self.assertEqual(fails, {}); self.assertTrue(eligible(quotes["STO.AX"]))
        self.assertEqual(quotes["STO.AX"]["paper_price_class"], "DELAYED PAPER")
        self.assertAlmostEqual(quotes["STO.AX"]["px_usd"], 6.6)

if __name__ == "__main__":
    unittest.main()
