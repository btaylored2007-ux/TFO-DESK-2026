"""Offline tests for the 5 Oct 2026 OANDA API retry change (09:05 AEDT daily run died on a transient 401 from /summary).
READS retry 401/429/5xx/timeouts (3 tries, 5s + 15s); WRITES are never auto-retried: entries are looked up by client id before anything
else happens, closes / stop changes are read back before at most one resend. No network (_urlopen/_api_once faked), sleeps faked,
temp state dir, fake token. Run (repo root): python3 tests/test_oanda_api_retry_20261005.py -v"""
import io, json, os, shutil, socket, sys, tempfile, unittest, urllib.error, importlib.util
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
ACC = "/v3/accounts/101-011-39617916-001"
def load():
    spec = importlib.util.spec_from_file_location("sleeve_retry_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
class Resp:
    def __init__(self, obj): self.b = json.dumps(obj).encode()
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def http_err(code, body='{"errorMessage":"Insufficient authorization to perform request."}'):
    return urllib.error.HTTPError("https://api-fxpractice.oanda.com/x", code, "err", {}, io.BytesIO(body.encode()))

class Base(unittest.TestCase):
    def setUp(self):
        self._tok = os.environ.get("OANDA_PRACTICE_TOKEN"); os.environ["OANDA_PRACTICE_TOKEN"] = "fake-test-token"
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.SEEN = os.path.join(st, "closed_seen.json"); m.CFG = os.path.join(st, "nocfg.json")
        m.BEH_BASE = self.tmp; self.sleeps = []; m._sleep = self.sleeps.append; self.calls = []
        def no_network(*a, **k): raise AssertionError("unexpected real HTTP in test")
        m._urlopen = no_network
    def tearDown(self):
        shutil.rmtree(self.tmp)
        if self._tok is None: os.environ.pop("OANDA_PRACTICE_TOKEN", None)
        else: os.environ["OANDA_PRACTICE_TOKEN"] = self._tok
    def script(self, *outcomes):
        """fake _api_once: each outcome is an Exception (raised) or a dict (returned), consumed in order."""
        q = list(outcomes)
        def once(method, path, body, params):
            self.calls.append((method, path)); o = q.pop(0)
            if isinstance(o, Exception): raise o
            return o
        self.m._api_once = once
    def lines(self, name="trades.jsonl"):
        p = os.path.join(self.m.ST, name)
        if not os.path.exists(p): return []
        with open(p) as f: return [json.loads(l) for l in f]
    def E(self, code): return self.m.OandaHTTPError(code, "/p", "x")

class TestReadRetry(Base):
    def test_replay_0905_summary_401_then_ok(self):
        """The 5 Oct 09:05 failure: /summary 401. Now retried and the run continues."""
        seq = [http_err(401), Resp({"account": {"NAV": "50807.1", "currency": "AUD"}})]
        def uo(req, timeout=30):
            self.calls.append((req.get_method(), req.full_url, req.get_header("Authorization"))); o = seq.pop(0)
            if isinstance(o, Exception): raise o
            return o
        self.m._urlopen = uo
        a = self.m.summary()
        self.assertEqual(a["NAV"], "50807.1"); self.assertEqual(len(self.calls), 2); self.assertEqual(self.sleeps, [5.0])
        self.assertTrue(all(c[1].startswith("https://api-fxpractice.oanda.com" + ACC) for c in self.calls))
        self.assertEqual(self.calls[0][2], "Bearer fake-test-token")
        r = self.lines("api_retries.jsonl"); self.assertEqual(len(r), 1); self.assertIn("HTTP 401", r[0]["error"])
    def test_persistent_401_raises_after_3_tries_about_20s(self):
        self.script(self.E(401), self.E(401), self.E(401))
        with self.assertRaises(self.m.OandaHTTPError) as cm: self.m.api("GET", ACC + "/summary")
        self.assertEqual(cm.exception.code, 401); self.assertIn("HTTP 401", str(cm.exception))
        self.assertEqual(len(self.calls), 3); self.assertEqual(self.sleeps, [5.0, 15.0]); self.assertAlmostEqual(sum(self.sleeps), 20.0)
    def test_5xx_429_timeout_conn_retried(self):
        for exc in (self.E(503), self.E(500), self.E(429), socket.timeout("read timed out"), TimeoutError(), urllib.error.URLError("dns"),
                    ConnectionResetError()):
            self.calls.clear(); self.sleeps.clear(); self.script(exc, {"ok": 1})
            self.assertEqual(self.m.api("GET", ACC + "/openTrades"), {"ok": 1}, exc); self.assertEqual(len(self.calls), 2)
    def test_non_transient_4xx_not_retried(self):
        for code in (400, 403, 404):
            self.calls.clear(); self.script(self.E(code), {"ok": 1})
            with self.assertRaises(self.m.OandaHTTPError): self.m.api("GET", ACC + "/x")
            self.assertEqual(len(self.calls), 1)
    def test_writes_never_auto_retried(self):
        for method in ("POST", "PUT"):
            for exc in (self.E(503), self.E(401), socket.timeout()):
                self.calls.clear(); self.script(exc, {"ok": 1})
                with self.assertRaises(Exception): self.m.api(method, ACC + "/orders", {"order": {}})
                self.assertEqual(len(self.calls), 1, (method, exc)); 
        self.assertEqual(self.sleeps, [])
    def test_practice_host_rail_kept(self):
        self.assertEqual(self.m.HOST, "https://api-fxpractice.oanda.com"); self.m.HOST = "https://api-fxtrade.oanda.com"
        with self.assertRaises(AssertionError): self.m._api_once("GET", ACC, None, None)

class EntryBase(Base):
    def setUp(self):
        super().setUp(); m = self.m
        m.instruments = lambda: {k: {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "financing": {"longRate": "0", "shortRate": "0"}} for k in ("USD_CHF", "GBP_CHF")}
        m.summary = lambda: {"NAV": "50000", "marginUsed": "100", "currency": "AUD"}
        self.open = []; m.open_trades = lambda: list(self.open)
        m.pricing = lambda insts: {i: {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": 1e-4, "tradeable": True, "q2h": {"positiveUnits": "1.5"}} for i in insts}
        m.behaviour_ok = lambda *a, **k: True
    def entry(self): return dict(design="OXL2", ins="USD_CHF", dir=1, score=0.04, risk_pct=0.005, reason="t", stop_dist=0.01, floor_sp=1e-4,
                                 close=1.0, expires_utc="2099-01-01T00:00:00+00:00")
    def posts(self): return [c for c in self.calls if c[0] == "POST"]

class TestEntryNoBlindRetry(EntryBase):
    def test_timeout_but_order_filled_is_recovered_not_resent(self):
        self.script(socket.timeout(), {"order": {"state": "FILLED", "tradeOpenedID": "70001"}})
        self.assertEqual(self.m.place_entry(self.entry(), True), "filled")
        self.assertEqual(len(self.posts()), 1); self.assertTrue(self.calls[1][1].startswith(ACC + "/orders/@TFO-OXL2-"))
        r = self.lines()[-1]; self.assertEqual(r["event"], "entry_order"); self.assertEqual(r["response"]["trade_id"], "70001")
    def test_open_trade_with_client_id_counts_as_filled(self):
        def once(method, path, body, params):
            self.calls.append((method, path))
            if method == "POST":
                self.open.append({"id": "70002", "instrument": "USD_CHF", "price": "1.0001", "clientExtensions": {"id": body["order"]["clientExtensions"]["id"], "tag": "TFO"}})
                raise self.E(502)
            raise self.E(404)
        self.m._api_once = once
        self.assertEqual(self.m.place_entry(self.entry(), True), "filled"); self.assertEqual(len(self.posts()), 1)
    def test_confirmed_absent_transient_defers_to_next_run(self):
        self.script(self.E(503), self.E(404))
        res = self.m.place_entry(self.entry(), True)
        self.assertTrue(res.startswith("defer:order_not_placed"), res); self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.lines()[-1]["event"], "entry_not_placed")
    def test_deferred_entry_stays_queued(self):
        with open(os.path.join(self.m.ST, "pending.json"), "w") as f: json.dump({"queue": [self.entry()]}, f)
        self.m.reopen_blackout = lambda *a, **k: None
        self.script(self.E(401), self.E(404))
        self.m.run_queue(True, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual(len(json.load(open(os.path.join(self.m.ST, "pending.json")))["queue"]), 1); self.assertEqual(len(self.posts()), 1)
    def test_lookup_unreadable_never_resends(self):
        self.script(socket.timeout(), self.E(401), self.E(401), self.E(401))
        res = self.m.place_entry(self.entry(), True)
        self.assertTrue(res.startswith("error:unverified"), res); self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.lines()[-1]["event"], "entry_unverified")
    def test_non_transient_rejection_absent_is_error(self):
        self.script(self.E(400), self.E(404))
        self.assertEqual(self.m.place_entry(self.entry(), True), "error"); self.assertEqual(len(self.posts()), 1)
    def test_cancelled_order_found(self):
        self.script(socket.timeout(), {"order": {"state": "CANCELLED", "cancelledReason": "MARKET_HALTED"}})
        self.assertEqual(self.m.place_entry(self.entry(), True), "cancelled:MARKET_HALTED")
    def test_dry_never_posts(self):
        self.script(); self.assertEqual(self.m.place_entry(self.entry(), False), "dry"); self.assertEqual(self.calls, [])

class TestCloseAndStop(Base):
    T = {"id": "61594", "instrument": "USD_CHF", "currentUnits": "14260", "clientExtensions": {"tag": "TFO", "comment": "OXL2"}}
    def setUp(self):
        super().setUp(); self.m.instruments = lambda: {"USD_CHF": {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "financing": {"longRate": "0", "shortRate": "0"}}}
    def puts(self): return [c for c in self.calls if c[0] == "PUT"]
    def test_close_retried_once_only_after_trade_confirmed_open(self):
        self.script(self.E(503), {"trade": {"state": "OPEN"}}, {"orderFillTransaction": {"price": "0.83", "pl": "12.5"}})
        self.m.close_trade(self.T, "t", True)
        self.assertEqual(len(self.puts()), 2); r = self.lines()[-1]; self.assertEqual(r["event"], "close_fill"); self.assertTrue(r["retried"])
    def test_close_not_resent_when_already_closed_by_stop(self):
        self.script(self.E(404), {"trade": {"state": "CLOSED", "closingTransactionIDs": ["9"]}}, {"transaction": {"type": "ORDER_FILL", "reason": "STOP_LOSS_ORDER"}})
        self.m.close_trade(self.T, "t", True)
        self.assertEqual(len(self.puts()), 1); self.assertEqual(self.lines()[-1]["event"], "close_already_closed")   # not double counted
    def test_close_own_fill_found_after_timeout(self):
        self.script(socket.timeout(), {"trade": {"state": "CLOSED", "averageClosePrice": "0.83", "realizedPL": "7", "closingTransactionIDs": ["9"]}},
                    {"transaction": {"type": "ORDER_FILL", "reason": "MARKET_ORDER_TRADE_CLOSE"}})
        self.m.close_trade(self.T, "t", True)
        self.assertEqual(len(self.puts()), 1); r = self.lines()[-1]; self.assertEqual(r["event"], "close_fill"); self.assertEqual(r["realized_pl_aud"], "7")
    def test_close_max_two_puts(self):
        self.script(self.E(503), {"trade": {"state": "OPEN"}}, self.E(503), {"trade": {"state": "OPEN"}})
        self.m.close_trade(self.T, "t", True); self.assertEqual(len(self.puts()), 2); self.assertIn("error", self.lines()[-1])
    def test_protected_trades_refused_before_any_call(self):
        self.script()
        for tid in ("61365", "61368"):
            t = dict(self.T, id=tid)
            with self.assertRaises(RuntimeError): self.m.close_trade(t, "t", True)
            with self.assertRaises(RuntimeError): self.m.set_stop(t, 1.0, "t", True)
        self.assertEqual(self.calls, [])

class TestStopConfirmed(Base):
    """5 Oct 2026 follow-up: a stop change only counts (event=stop_update, confirmed) once the broker shows it; tighten-only; dry = stop_dry."""
    T = {"id": "61594", "instrument": "USD_CHF", "currentUnits": "14260", "price": "0.82906", "clientExtensions": {"tag": "TFO", "comment": "OXL2"},
         "stopLossOrder": {"price": "0.81878"}}
    def setUp(self):
        super().setUp(); self.m.instruments = lambda: {"USD_CHF": {"displayPrecision": 5}, "XAU_USD": {"displayPrecision": 3}}
    def t(self): return json.loads(json.dumps(self.T))
    def puts(self): return [c for c in self.calls if c[0] == "PUT"]
    def test_confirmed_by_readback(self):
        self.script({}, {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.82000", "id": "8"}}})
        t = self.t(); self.assertTrue(self.m.set_stop(t, 0.82, "trail", True))
        r = self.lines()[-1]; self.assertEqual((r["event"], r["confirmed"], r["broker_stop"]), ("stop_update", True, 0.82)); self.assertEqual(t["stopLossOrder"]["price"], "0.82000")
    def test_put_ok_but_broker_differs_is_unconfirmed(self):
        self.script({}, {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.81878"}}})
        self.assertFalse(self.m.set_stop(self.t(), 0.82, "trail", True))
        r = self.lines()[-1]; self.assertEqual(r["event"], "stop_update_unconfirmed"); self.assertEqual(len(self.puts()), 1)
    def test_timeout_but_stop_at_target(self):
        self.script(socket.timeout(), {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.82000"}}})
        self.assertTrue(self.m.set_stop(self.t(), 0.82, "t", True)); r = self.lines()[-1]
        self.assertEqual(r["event"], "stop_update"); self.assertIn("recovered", r); self.assertNotIn("error", r); self.assertEqual(len(self.puts()), 1)
    def test_retried_once_when_not_applied(self):
        self.script(self.E(502), {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.81878"}}}, {}, {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.82000"}}})
        self.assertTrue(self.m.set_stop(self.t(), 0.82, "t", True)); self.assertEqual(len(self.puts()), 2); self.assertTrue(self.lines()[-1]["retried"])
    def test_max_two_puts_then_failed(self):
        old = {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.81878"}}}
        self.script(self.E(502), old, self.E(502), old)
        self.assertFalse(self.m.set_stop(self.t(), 0.82, "t", True)); self.assertEqual(len(self.puts()), 2); self.assertEqual(self.lines()[-1]["event"], "stop_update_failed")
    def test_non_transient_not_retried(self):
        self.script(self.E(400), {"trade": {"state": "OPEN", "stopLossOrder": {"price": "0.81878"}}})
        self.assertFalse(self.m.set_stop(self.t(), 0.82, "t", True)); self.assertEqual(len(self.puts()), 1); self.assertEqual(self.lines()[-1]["event"], "stop_update_failed")
    def test_readback_down_put_response_confirms(self):
        self.script({"stopLossOrderTransaction": {"price": "0.82000", "id": "9"}}, self.E(401), self.E(401), self.E(401))
        self.assertTrue(self.m.set_stop(self.t(), 0.82, "t", True)); self.assertEqual(self.lines()[-1]["confirm_source"], "PUT response stopLossOrderTransaction")
    def test_readback_down_no_response_is_unconfirmed_not_resent(self):
        self.script(socket.timeout(), self.E(401), self.E(401), self.E(401))
        self.assertFalse(self.m.set_stop(self.t(), 0.82, "t", True)); self.assertEqual(len(self.puts()), 1); self.assertEqual(self.lines()[-1]["event"], "stop_update_failed")
    def test_tighten_only(self):
        self.script()
        self.assertFalse(self.m.set_stop(self.t(), 0.81, "t", True)); self.assertEqual(self.calls, [])
        self.assertEqual(self.lines()[-1]["event"], "stop_update_refused")
        short = dict(self.t(), instrument="XAU_USD", currentUnits="-1.9", stopLossOrder={"price": "4323.568"})
        self.assertFalse(self.m.set_stop(short, 4330.0, "t", True)); self.assertEqual(self.calls, [])
    def test_dry_is_stop_dry_and_no_calls(self):
        self.script(); self.assertFalse(self.m.set_stop(self.t(), 0.82, "t", False))
        self.assertEqual(self.lines()[-1]["event"], "stop_dry"); self.assertEqual(self.calls, [])

class TestStopReconcile(Base):
    XAU = {"id": "61591", "instrument": "XAU_USD", "currentUnits": "-1.9", "price": "4144.395", "clientExtensions": {"tag": "TFO", "comment": "OXL1"},
           "stopLossOrder": {"price": "4323.568"}}
    def setUp(self):
        super().setUp(); self.m.instruments = lambda: {"XAU_USD": {"displayPrecision": 3}, "SPX500_USD": {"displayPrecision": 1}}
        self.write({"event": "entry_order", "live": True, "instrument": "XAU_USD", "stop": 4323.568, "response": {"trade_id": "61591"}})
    def write(self, *recs):
        with open(self.m.LOG, "a") as f:
            for r in recs: f.write(json.dumps(r) + "\n")
    def xau(self, stop="4323.568"): return dict(json.loads(json.dumps(self.XAU)), stopLossOrder={"price": stop})
    def test_replay_0910_dry_trail_is_not_a_ledger_claim(self):
        """The 11:41 report: stop_update 4319.8035 live=false (my 09:10 dry run) vs broker 4323.568. Dry lines never count -> match, nothing sent."""
        self.write({"event": "stop_update", "trade_id": "61591", "instrument": "XAU_USD", "new_stop": 4319.8035, "live": False})
        self.script(); n = len(self.lines())
        rows = self.m.reconcile_stops([self.xau()], True)
        self.assertEqual(rows[0]["status"], "match"); self.assertEqual(self.calls, []); self.assertEqual(len(self.lines()), n)
        self.assertEqual(json.load(open(self.m.stop_state_path()))['trades'][0]['status'], 'match')
    def test_broker_looser_than_confirmed_ledger_is_repushed_tighten_only(self):
        self.write({"event": "stop_update", "trade_id": "61591", "live": True, "confirmed": True, "new_stop": 4319.804, "broker_stop": 4319.804})
        self.script({}, {"trade": {"state": "OPEN", "stopLossOrder": {"price": "4319.804"}}})
        rows = self.m.reconcile_stops([self.xau()], True)
        self.assertEqual(rows[0]["status"], "broker_looser_than_confirmed_ledger"); self.assertTrue(rows[0]["pushed_confirmed"])
        self.assertEqual([c[0] for c in self.calls], ["PUT", "GET"])
    def test_unconfirmed_or_failed_ledger_lines_do_not_count(self):
        self.write({"event": "stop_update", "trade_id": "61591", "live": True, "new_stop": 4300.0, "error": "HTTP 503"},
                   {"event": "stop_update_unconfirmed", "trade_id": "61591", "live": True, "new_stop": 4300.0})
        self.script(); rows = self.m.reconcile_stops([self.xau()], True)
        self.assertEqual(rows[0]["status"], "match"); self.assertEqual(self.calls, [])
    def test_broker_tighter_corrects_ledger_without_sending(self):
        self.script(); rows = self.m.reconcile_stops([self.xau("4300.000")], True)
        self.assertEqual(rows[0]["status"], "ledger_corrected_to_broker"); self.assertEqual(self.calls, [])
        r = self.lines()[-1]; self.assertEqual((r["event"], r["ledger_stop_now"]), ("stop_reconcile", 4300.0))
        self.assertEqual(self.m.ledger_stops()["61591"]["stop"], 4300.0)
        self.assertEqual(self.m.reconcile_stops([self.xau("4300.000")], True)[0]["status"], "match")   # idempotent
    def test_missing_broker_stop_left_to_restore_path(self):
        self.script(); t = self.xau(); t.pop("stopLossOrder")
        self.assertEqual(self.m.reconcile_stops([t], True)[0]["status"], "broker_stop_missing"); self.assertEqual(self.calls, [])
    def test_brett_trades_never_looked_at(self):
        self.script()
        brett = [{"id": "61365", "instrument": "SPX500_USD", "currentUnits": "0.17", "clientExtensions": {"tag": "TFO"}, "stopLossOrder": {"price": "1.0"}},
                 {"id": "61368", "instrument": "NAS100_USD", "currentUnits": "0.02", "stopLossOrder": {"price": "1.0"}}]
        self.assertEqual(self.m.reconcile_stops(brett, True), []); self.assertEqual(self.calls, [])
    def test_dry_writes_nothing(self):
        self.script(); n = len(self.lines())
        self.m.reconcile_stops([self.xau("4300.000")], False)
        self.assertEqual(len(self.lines()), n); self.assertFalse(os.path.exists(self.m.stop_state_path()))

if __name__ == "__main__": unittest.main()
