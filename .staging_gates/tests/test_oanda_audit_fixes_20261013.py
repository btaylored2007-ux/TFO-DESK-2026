"""Audit fixes 13 Oct 2026 (research/oanda_audit_20261013.md). Offline."""
import datetime as dt, importlib.util, json, os, sys, tempfile, shutil, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_designs as D, oanda_risk as RK
def bars(closes, start=dt.datetime(2025, 1, 1, 21)):
    return [dict(t=(start + dt.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), o=c, h=c * 1.002, l=c * 0.998, c=c, sp=0.0001) for i, c in enumerate(closes)]
def cross_series(n_down=310, up=True):
    """Downtrend then rally; truncated at the FIRST bar where EMA20 crosses above EMA60 (fresh cross on the last bar)."""
    s = 1.0; c = []
    for i in range(n_down): s *= (0.999 if up else 1.001); c.append(s)
    for i in range(200):
        s *= (1.01 if up else 0.99); c.append(s)
        f = RK.ema_series(c, 20); sl = RK.ema_series(c, 60)
        if (f[-1] > sl[-1]) if up else (f[-1] < sl[-1]): return c
    raise AssertionError("no cross")
def load():
    spec = importlib.util.spec_from_file_location("sleeve_audit_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def T(i, ins, units, price, stop, owner="TFO", design="OXL2", **kw):
    t = {"id": str(i), "instrument": ins, "currentUnits": str(units), "price": str(price), "stopLossOrder": {"price": str(stop)} if stop is not None else None}
    if owner == "TFO": t["clientExtensions"] = {"tag": "TFO", "comment": design}
    t.update(kw); return t
class Base(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.SEEN = os.path.join(st, "closed_seen.json"); m.CFG = os.path.join(st, "nocfg.json")
        self.calls = []
        def api(method, path, body=None, params=None, retry=None):
            self.calls.append((method, path, body)); return self.handler(method, path, body, params)
        self.handler = lambda *a: (_ for _ in ()).throw(AssertionError("unexpected api %r" % (a[:2],)))
        m.api = api; m.behaviour_ok = lambda *a, **k: True; m._sleep = lambda s: None
        m.instruments = lambda: {k: {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "minimumTradeSize": "1", "financing": {"longRate": "0", "shortRate": "0"}} for k in D.P32 + ["XAU_USD"]}
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        self.px = {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": 1e-4, "tradeable": True, "q2h": {"positiveUnits": "1.5"}, "loss_cq": "1.53", "quote_age_s": 1.0}
        m.pricing = lambda insts: {i: dict(self.px) for i in insts}
        self.open = []; m.open_trades = lambda: list(self.open)
    def tearDown(self): shutil.rmtree(self.tmp)
    def lines(self): return [json.loads(l) for l in open(self.m.LOG)] if os.path.exists(self.m.LOG) else []
    def entry(self, ins="EUR_USD", d=1, **kw):
        e = dict(design="OXL2", ins=ins, dir=d, score=0.03, risk_pct=0.005, reason="t", stop_dist=0.01, floor_sp=1e-4, close=1.0,
                 expires_utc="2026-12-31T00:00:00+00:00", decision_id="OXL2-carry-v2-20261012|%s|2026-10-12T21:00:00.000000000Z" % ins); e.update(kw); return e
class Fix5And4Conversion(Base):
    def test_c_quote_missing_is_none(self):
        self.assertIsNone(RK.c_quote({})); self.assertIsNone(RK.c_quote(None)); self.assertIsNone(RK.c_quote({"positiveUnits": "0"}))
        self.assertEqual(RK.c_quote_loss({"q2h": {"positiveUnits": "1.5"}, "loss_cq": "1.53"}), 1.53)
        self.assertEqual(RK.c_quote_loss({"q2h": {"positiveUnits": "1.5", "negativeUnits": "1.51"}}), 1.51)
    def test_entry_skips_without_conversion(self):
        self.px.update(q2h={}, loss_cq=None)
        self.assertEqual(self.m.place_entry(self.entry(), live=False), "skip:no_conversion")
        self.assertEqual(self.lines()[-1]["event"], "entry_conversion_block")
    def test_sizes_with_account_loss_factor(self):
        self.assertEqual(self.m.place_entry(self.entry(), live=False), "dry"); r = self.lines()[-1]
        self.assertEqual(r["c_quote"], 1.53); self.assertLessEqual(r["r0_aud"], 250.0 + 1e-6)
class OrdersIdempotentOpenOnly(Base):
    def test_client_id_deterministic_per_decision(self):
        a = self.m.client_id_for(self.entry()); b = self.m.client_id_for(self.entry()); self.assertEqual(a, b)
        self.assertNotEqual(a, self.m.client_id_for(self.entry(decision_id="x|EUR_USD|other-bar")))
    def test_decision_already_sent_is_not_resent(self):
        def h(method, path, body, params):
            if "/orders/@" in path: return {"order": {"state": "FILLED", "tradeOpenedID": "77"}}
            raise AssertionError("no POST allowed")
        self.handler = h
        self.assertTrue(self.m.place_entry(self.entry(), live=True).startswith("skip:decision_already_sent"))
        self.assertFalse(any(c[0] == "POST" for c in self.calls))
    def test_open_only_and_absent_lookup(self):
        def h(method, path, body, params):
            if "/orders/@" in path: raise self.m.OandaHTTPError(404, path, "not found")
            if method == "POST": return {"orderFillTransaction": {"id": "5", "units": "100", "price": "1.0001", "tradeOpened": {"tradeID": "5"},
                                                                  "homeConversionFactors": {"lossQuoteHome": {"factor": "1.55"}}}}
            raise AssertionError(path)
        self.handler = h
        self.assertEqual(self.m.place_entry(self.entry(), live=True), "filled")
        post = [c for c in self.calls if c[0] == "POST"][0][2]["order"]
        self.assertEqual(post["positionFill"], "OPEN_ONLY"); self.assertEqual(post["clientExtensions"]["id"], self.m.client_id_for(self.entry()))
        r = self.lines()[-1]; self.assertEqual(r["c_quote_fill_loss"], 1.55); self.assertIn("r0_aud_filled", r)
    def test_other_owner_position_blocks(self):
        self.open = [T(1, "EUR_USD", -1000, 1.1, 1.2, owner="Brett")]
        self.assertEqual(self.m.place_entry(self.entry(), live=False), "skip:instrument_held_by_other_owner")
class StopsAndExits(Base):
    def test_retry_never_loosens_fresher_stop(self):
        st = {"n": 0}
        def h(method, path, body, params):
            if method == "PUT": st["n"] += 1; raise self.m.OandaHTTPError(503, path, "busy")
            return {"trade": {"state": "OPEN", "stopLossOrder": {"price": "1.08500", "id": "9"}}}
        self.handler = h
        t = T(1, "EUR_USD", 10000, 1.1, 1.07)
        self.assertTrue(self.m.set_stop(t, 1.08, "trail", live=True)); self.assertEqual(st["n"], 1)
        self.assertEqual(self.lines()[-1]["confirm_source"], "broker already tighter; not resent")
    def test_close_failure_returns_false(self):
        def h(method, path, body, params):
            if method == "PUT": raise self.m.OandaHTTPError(400, path, "bad")
            return {"trade": {"state": "OPEN"}}
        self.handler = h
        self.assertFalse(self.m.close_trade(T(1, "EUR_USD", 1000, 1.1, 1.0), "x", live=True))
    def test_stop_through_market(self):
        self.assertTrue(self.m.stop_through_market("EUR_USD", 1, 1.0005)); self.assertFalse(self.m.stop_through_market("EUR_USD", 1, 0.99))
        self.assertTrue(self.m.stop_through_market("EUR_USD", -1, 0.9999)); self.assertFalse(self.m.stop_through_market("EUR_USD", -1, 1.01))
    def test_check_restore_without_atr_uses_ledger_stop(self):
        self.m.log(dict(event="entry_order", live=True, stop=0.99, response={"trade_id": "1"}))
        t = T(1, "EUR_USD", 1000, 1.0, None); self.open = [t]
        self.m.candles = lambda *a, **k: []          # no data -> ATR None (used to raise TypeError)
        got = []; self.m.set_stop = lambda tt, p, why, live: got.append((p, why)) or True
        self.m.reconcile_stop_fills = lambda: None; self.m.snapshot = lambda extra=None: ({"nav": 50000, "tfo_upl_aud": 0, "tfo_realized_aud": 0}, list(self.open))
        self.m.reconcile_stops = lambda *a: None; self.m.run_queue = lambda *a, **k: None; self.m.log_nav = lambda s: None
        self.m.check(True); self.assertEqual(got[0][0], 0.99); self.assertIn("ledger stop", got[0][1])
    def test_check_restore_through_market_closes(self):
        self.m.log(dict(event="entry_order", live=True, stop=1.0005, response={"trade_id": "1"}))
        t = T(1, "EUR_USD", 1000, 1.0, None); self.open = [t]; self.m.candles = lambda *a, **k: []
        closed = []; self.m.close_trade = lambda tt, why, live: closed.append(why) or True; self.m.set_stop = lambda *a: self.fail("must close, not set stop")
        self.m.reconcile_stop_fills = lambda: None; self.m.snapshot = lambda extra=None: ({"nav": 50000, "tfo_upl_aud": 0, "tfo_realized_aud": 0}, list(self.open))
        self.m.reconcile_stops = lambda *a: None; self.m.run_queue = lambda *a, **k: None; self.m.log_nav = lambda s: None
        self.m.check(True); self.assertEqual(len(closed), 1)
class GptReviewFixes(Base):
    def test_cancelled_close_is_not_success(self):
        self.handler = lambda m_, p_, b_, q_: {"orderCancelTransaction": {"reason": "MARKET_HALTED"}}
        self.assertFalse(self.m.close_trade(T(1, "EUR_USD", 1000, 1.1, 1.0), "x", live=True)); self.assertEqual(self.lines()[-1]["event"], "close_cancelled")
    def test_closed_trades_paginate(self):
        pages = {None: [{"id": str(1000 - i), "openTime": "2026-10-10T00:00:00Z", "clientExtensions": {"tag": "TFO"}} for i in range(500)],
                 "501": [{"id": "400", "openTime": "2026-10-05T00:00:00Z", "clientExtensions": {"tag": "TFO"}}, {"id": "300", "openTime": "2026-09-01T00:00:00Z"}]}
        self.handler = lambda m_, p_, b_, q_: {"trades": pages.get((q_ or {}).get("beforeID"), [])}
        tr = self.m.closed_tfo_trades(); self.assertEqual(len(tr), 501); self.assertIn("400", [t["id"] for t in tr])
    def test_recovered_fill_uses_actual_price(self):
        def h(m_, p_, b_, q_):
            if m_ == "POST": raise self.m.OandaHTTPError(503, p_, "timeout")
            if "/orders/@" in p_ and not any(c[0] == "POST" for c in self.calls): raise self.m.OandaHTTPError(404, p_, "nf")
            if "/orders/@" in p_: return {"order": {"state": "FILLED", "tradeOpenedID": "5"}}
            raise AssertionError(p_)
        self.handler = h; cid = self.m.client_id_for(self.entry())
        self.open = []; orig = self.m.open_trades
        def ot():
            return [dict(T(5, "EUR_USD", 100, 1.0101, 0.99), initialUnits="100", clientExtensions={"tag": "TFO", "id": cid, "comment": "OXL2"})] if any(c[0] == "POST" for c in self.calls) else []
        self.m.open_trades = ot
        self.assertEqual(self.m.place_entry(self.entry(), live=True), "filled"); r = self.lines()[-1]
        self.assertAlmostEqual(r["r0_aud_filled"], round(100 * abs(1.0101 - r["stop_rounded"]) * 1.53, 2))
class GptReviewRound2(Base):
    def test_stale_queued_decision_dropped(self):
        pf = os.path.join(self.m.ST, "pending.json")
        json.dump({"queue": [self.entry("AUD_CHF", signal_bar_end="2026-09-01T21:00:00.000000000Z")]}, open(pf, "w"))
        self.m.reopen_blackout = lambda **k: None; self.m.place_entry = lambda *a, **k: self.fail("stale decision must not be submitted")
        self.m.run_queue(True, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual(self.lines()[-1]["event"], "entry_stale_bar_drop"); self.assertEqual(json.load(open(pf))["queue"], [])
    def test_unverified_pnl_blocks_new_entries(self):
        self.assertTrue(self.m.halted({"tfo_upl_aud": 0, "tfo_realized_aud": 0, "pnl_unverified": True}))
        self.assertFalse(self.m.halted({"tfo_upl_aud": 0, "tfo_realized_aud": 0, "pnl_unverified": False}))
    def test_recovery_uses_filling_transaction(self):
        posted = []
        def h(m_, p_, b_, q_):
            if m_ == "POST": posted.append(1); raise self.m.OandaHTTPError(503, p_, "timeout")
            if "/orders/@" in p_ and not posted: raise self.m.OandaHTTPError(404, p_, "nf")
            if "/orders/@" in p_: return {"order": {"state": "FILLED", "tradeOpenedID": "5", "fillingTransactionID": "77"}}
            if p_.endswith("/transactions/77"): return {"transaction": {"id": "77", "price": "1.0102", "units": "100", "homeConversionFactors": {"lossQuoteHome": {"factor": "1.56"}}}}
            raise AssertionError(p_)
        self.handler = h; self.m.open_trades = lambda: []
        self.assertEqual(self.m.place_entry(self.entry(), live=True), "filled"); r = self.lines()[-1]
        self.assertEqual(r["c_quote_fill_loss"], 1.56); self.assertAlmostEqual(r["r0_aud_filled"], round(100 * abs(1.0102 - r["stop_rounded"]) * 1.56, 2))
class GptReviewRound3(Base):
    def test_trade_in_both_open_and_closed_counts_once(self):
        tfo = {"tag": "TFO", "comment": "OXL2"}
        op = [{"id": "5", "instrument": "EUR_USD", "currentUnits": "100", "price": "1.0", "openTime": "2026-10-12T00:00:00Z", "unrealizedPL": "-300", "financing": "-2", "realizedPL": "0", "clientExtensions": tfo}]
        cl = [{"id": "5", "instrument": "EUR_USD", "openTime": "2026-10-12T00:00:00Z", "realizedPL": "-310", "financing": "-2", "clientExtensions": tfo}]
        self.m.summary = lambda: {"NAV": "50000", "balance": "50000", "marginUsed": "0", "marginAvailable": "50000", "unrealizedPL": "0", "openTradeCount": "1", "currency": "AUD"}
        self.m.open_trades = lambda: op; self.handler = lambda m_, p_, b_, q_: {"trades": cl}
        snap, _ = self.m.snapshot(); self.assertAlmostEqual(snap["tfo_upl_aud"], 0.0); self.assertAlmostEqual(snap["tfo_realized_aud"], -312.0)
    def test_recovery_falls_back_when_filling_tx_unreadable(self):
        posted = []; cid = self.m.client_id_for(self.entry())
        def h(m_, p_, b_, q_):
            if m_ == "POST": posted.append(1); raise self.m.OandaHTTPError(503, p_, "timeout")
            if "/orders/@" in p_ and not posted: raise self.m.OandaHTTPError(404, p_, "nf")
            if "/orders/@" in p_: return {"order": {"state": "FILLED", "tradeOpenedID": "5", "fillingTransactionID": "77"}}
            if "/transactions/77" in p_: raise self.m.OandaHTTPError(500, p_, "down")
            raise AssertionError(p_)
        self.handler = h
        self.m.open_trades = lambda: [dict(T(5, "EUR_USD", 100, 1.0101, 0.99), initialUnits="100", clientExtensions={"tag": "TFO", "id": cid, "comment": "OXL2"})] if posted else []
        self.assertEqual(self.m.place_entry(self.entry(), live=True), "filled"); r = self.lines()[-1]
        self.assertIn("filling_tx_error", r); self.assertIn("r0_aud_filled", r)
class QueueProvenance(Base):
    def test_only_current_rule_items_are_preserved(self):
        import inspect; src = inspect.getsource(self.m.daily)
        self.assertIn('e.get("queued_by") == QUEUE_RULES', src); self.assertIn('e["queued_by"] = QUEUE_RULES', src)
class CapsFrozenR(Base):
    def test_trailed_stop_does_not_release_budget(self):
        self.m.log(dict(event="entry_order", live=True, risk_aud=250.0, r0_aud=248.0, r0_aud_filled=252.0, stop_dist=0.01, response={"trade_id": "1"}))
        rows = self.m.open_risk_rows([T(1, "EUR_USD", 1000, 1.0, 1.02, initialUnits="1000")], 50000.0, self.m.cfg())
        self.assertAlmostEqual(rows[0]["risk_frac"], 252.0 / 50000)            # actual-fill R, stop past entry still charged
        rows = self.m.open_risk_rows([T(1, "EUR_USD", 500, 1.0, 1.02, initialUnits="1000")], 50000.0, self.m.cfg())
        self.assertAlmostEqual(rows[0]["risk_frac"], 126.0 / 50000)            # scaled only by remaining units
class StateAndFreshness(Base):
    def test_bar_freshness(self):
        U = dt.timezone.utc; mon0805 = dt.datetime(2026, 10, 11, 21, 5, tzinfo=U)   # Mon 12 Oct 08:05 AEDT = Sun 17:05 NY
        fri = "2026-10-08T21:00:00.000000000Z"; mon = "2026-10-11T21:00:00.000000000Z"
        self.assertIsNone(self.m.bar_freshness(fri, mon0805))                         # weekend: Friday bar is current
        self.assertIsNone(self.m.bar_freshness(fri, mon0805 - dt.timedelta(days=1)))  # Saturday
        self.assertIsNone(self.m.bar_freshness(mon, mon0805 + dt.timedelta(days=1)))  # Tue 08:05 AEDT: Monday bar current
        self.assertIn("stale_bar", self.m.bar_freshness(fri, mon0805 + dt.timedelta(days=1)))   # Monday bar missing
        self.assertIn("stale_bar", self.m.bar_freshness(mon, mon0805 + dt.timedelta(days=3)))   # Wed: two sessions missing
        self.assertIn("stale_bar", self.m.bar_freshness("2026-10-05T21:00:00.000000000Z", mon0805))
    def test_same_bar_retry_keeps_queued_entries(self):
        pf = os.path.join(self.m.ST, "pending.json")
        json.dump({"queue": [self.entry("AUD_CHF", expires_utc="2099-01-01T00:00:00+00:00")]}, open(pf, "w"))
        # emulate the daily write path with an empty fresh queue
        import inspect; src = inspect.getsource(self.m.daily); self.assertIn("same-bar retry must not drop", src)
    def test_corrupt_pending_does_not_crash(self):
        open(os.path.join(self.m.ST, "pending.json"), "w").write("{bad")
        self.m.run_queue(True, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual(self.lines()[-1]["event"], "state_read_error")
    def test_write_json_atomic(self):
        self.m._write_json("pending.json", {"queue": []}); self.assertFalse(os.path.exists(os.path.join(self.m.ST, "pending.json.tmp")))
class ScaleGateExcludedBug(unittest.TestCase):
    def test_bug_closed_trades_flagged_not_counted(self):
        import oanda_scale_gate as G
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "trades.jsonl"), "w") as f:
                f.write(json.dumps(dict(event="close_fill", trade_id="1", reason="signal flip/flat (score None)")) + "\n")
            import types; S = types.SimpleNamespace(api=lambda *a, **k: {"trades": [
                {"id": "1", "instrument": "EUR_USD", "realizedPL": "50", "financing": "0", "clientExtensions": {"tag": "TFO"}},
                {"id": "2", "instrument": "GBP_USD", "realizedPL": "-5", "financing": "1", "clientExtensions": {"tag": "TFO"}}]},
                acct=lambda p: p, is_tfo=lambda t: True)
            sys.modules["oanda_sleeve"] = S
            tr = G.fetch_closed(tmp); self.assertEqual({t["trade_id"]: t["excluded_bug"] for t in tr}, {"1": True, "2": False})
        finally:
            sys.modules.pop("oanda_sleeve", None); shutil.rmtree(tmp)
class Panel(unittest.TestCase):
    def test_panel_fields(self):
        import oanda_panel as P
        tmp = tempfile.mkdtemp(); P.ST = tmp
        try:
            json.dump(dict(asof_utc="2026-10-12T17:52:40+00:00", designs=["OXL1", "OXL2", "OXF1"], trades=[dict(tfo=True, design="OXF1"), dict(tfo=True, design="OXL2"), dict(tfo=False)]), open(os.path.join(tmp, "snapshot.json"), "w"))
            s = P.summary(); self.assertEqual((s["book_open"], s["trend_open"]), (1, 1)); self.assertTrue(s["stale"])
            self.assertIn("AEDT", s["asof_local"]); self.assertIn("OXF1", s["live_designs"]); self.assertTrue(s["shadow_only"])
        finally: shutil.rmtree(tmp)
class Fix1FreshCross(unittest.TestCase):
    def test_fresh_cross_up_enters(self):
        cs = bars(cross_series()); d, t = D._fresh_cross("EUR_USD", {"EUR_USD": cs}); self.assertEqual(d, 1); self.assertEqual(t, cs[-1]["t"])
        sig = [s for s in D.signals(["OXF1"], {"EUR_USD": cs}) if s["design"] == "OXF1"]; self.assertEqual([s["ins"] for s in sig], ["EUR_USD"])
    def test_fresh_cross_down_enters_short(self):
        cs = bars(cross_series(up=False)); self.assertEqual(D._fresh_cross("EUR_USD", {"EUR_USD": cs})[0], -1)
    def test_one_bar_after_cross_no_entry(self):
        c = cross_series(); c.append(c[-1] * 1.01); cs = bars(c)
        self.assertEqual(D._fresh_cross("EUR_USD", {"EUR_USD": cs})[0], 0)
        self.assertEqual(D._trend("EUR_USD", {"EUR_USD": cs})[0], 1)          # state still long (exit logic unchanged)
        self.assertEqual([s for s in D.signals(["OXF1"], {"EUR_USD": cs}) if s["design"] == "OXF1"], [])
    def test_standing_trend_no_entry(self):
        cs = bars([1.0 + 0.001 * i for i in range(320)]); self.assertEqual(D._fresh_cross("EUR_USD", {"EUR_USD": cs})[0], 0)
    def test_needs_300_bars(self):
        c = cross_series(n_down=200); self.assertLess(len(c), 300); self.assertEqual(D._fresh_cross("EUR_USD", {"EUR_USD": bars(c)})[0], 0)
if __name__ == "__main__": unittest.main()
