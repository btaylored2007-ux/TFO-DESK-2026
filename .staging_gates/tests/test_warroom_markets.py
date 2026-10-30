"""Tests for warroom_markets.py (War Room ALL MARKETS v1, read-only). 2026-10-27."""
import datetime as dt, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warroom_markets as W

U = dt.timezone.utc


class T(unittest.TestCase):
    def test_session_us(self):
        now = dt.datetime(2026, 10, 27, 6, 30, tzinfo=U)            # 14:30 WITA Tue, NY 02:30
        s = W.session(now, "America/New_York", "09:30", "16:00")
        self.assertFalse(s["open"]); self.assertEqual(s["next_open_wita"], "Tue 27 Oct 21:30 WITA")
        s = W.session(dt.datetime(2026, 10, 27, 14, 0, tzinfo=U), "America/New_York", "09:30", "16:00")
        self.assertTrue(s["open"])

    def test_session_weekend_rolls_to_monday(self):
        sat = dt.datetime(2026, 10, 31, 3, 0, tzinfo=U)
        s = W.session(sat, "Australia/Sydney", "10:00", "16:00")
        self.assertFalse(s["open"]); self.assertTrue(s["next_open_wita"].startswith("Mon 02 Nov 07:00"))

    def test_fx_session(self):
        self.assertTrue(W.fx_session(dt.datetime(2026, 10, 27, 6, 0, tzinfo=U))["open"])
        self.assertFalse(W.fx_session(dt.datetime(2026, 10, 31, 12, 0, tzinfo=U))["open"])

    def test_fresh_levels(self):
        now = dt.datetime(2026, 10, 27, 6, 0, tzinfo=U)
        self.assertEqual(W.fresh("2026-10-27T05:55:00+00:00", "us", now)["level"], "green")
        self.assertEqual(W.fresh("2026-10-27T05:00:00+00:00", "us", now)["level"], "amber")
        self.assertEqual(W.fresh("2026-10-26T05:00:00Z", "us", now)["level"], "red")
        self.assertEqual(W.fresh(None, "us", now)["level"], "red")

    def test_us_book_risk_and_realized(self):
        now = dt.datetime(2026, 10, 27, 6, 0, tzinfo=U)
        st = {"audusd": 0.7, "open": 1, "max_open": 3, "pnl_today_aud": 10.0, "trades": [
            {"ticker": "AAA", "status": "FILLED", "direction": "SHORT", "entry": 10.0, "stop": 11.0, "t1": 9.5, "t2": 9.0,
             "open_qty": 70, "price": 9.9, "pnl_aud": 10.0},
            {"ticker": "BBB", "status": "ARMED", "direction": "SHORT", "trigger": 5.0, "stop": 5.5, "risk_aud": 2500}]}
        led = {"trades": [{"open_qty": 0, "realised_usd": 100.0, "fees_usd": 2.0, "borrow_usd": 1.0,
                           "exits": [{"utc": "2026-10-27T01:00:00+00:00"}]},
                          {"open_qty": 0, "realised_usd": -7.0, "fees_usd": 0.0, "borrow_usd": 0.0,
                           "exits": [{"utc": "2026-10-20T01:00:00+00:00"}]},
                          {"open_qty": 70, "realised_usd": 0.0, "exits": []}]}
        b = W.us_book("x", "X", st, led, now)
        self.assertEqual(b["risk_aud"], 100.0)            # 70 * (11-10) / 0.7
        self.assertEqual(b["realized_today_aud"], round(97 / 0.7, 2))
        self.assertEqual(b["realized_total_aud"], round(90 / 0.7, 2))
        self.assertEqual(len(b["armed"]), 1); self.assertEqual(b["positions"][0]["upl_aud"], 10.0)

    def test_missing_data_is_none(self):
        b = W.us_book("x", "X", None, None, dt.datetime.now(U))
        self.assertIsNone(b["realized_total_aud"]); self.assertIsNone(b["open_count"])
        a = W.asia_book("jp", "JP", None, None, dt.datetime.now(U))
        self.assertIsNone(a["realized_today_aud"])

    def test_asia_filled_after_costs(self):
        now = dt.datetime(2026, 10, 27, 6, 0, tzinfo=U)
        st = {"max_open": 3, "armed": [{"sym": "1.T", "status": "FILLED", "direction": "short", "fill": 100.0, "mark": 98.0,
                                         "qty_open": 100, "fx_aud": 0.01, "stop_live": 103.0, "est_cost_aud": 0.5}]}
        b = W.asia_book("jp", "JP", st, {"trades": []}, now)
        p = b["positions"][0]
        self.assertEqual(p["upl_aud"], 1.5)                # (100-98)*100*0.01 - 0.5
        self.assertEqual(p["risk_aud"], 3.0)

    def test_build_live_shape_readonly(self):
        d = W.build()
        keys = [m["key"] for m in d["markets"]]
        for k in ("us_main", "us_claude", "us_gpt", "asx", "jp", "hk", "sg", "kr", "tw", "crypto", "trend18", "oanda"):
            self.assertIn(k, keys)
        self.assertTrue(d["paper_only"] and d["read_only"])
        self.assertNotIn('"account"', json.dumps(d, default=str))   # OANDA account id never exposed
        for k in ("open_risk_aud", "day_pnl_aud", "open_positions", "health"):
            self.assertIn(k, d["firm"])

    def test_firm_asia_breakout(self):
        d = W.build()
        self.assertIn("asia", d["firm"]); self.assertIn("us", d["firm"]); self.assertIn("crypto", d["firm"])
        a = d["firm"]["asia"]
        self.assertEqual(a["count"], 6)
        for k in ("open_risk_aud", "day_pnl_aud", "open_positions", "armed", "bench", "markets_open"):
            self.assertIn(k, a)
        asia = [m for m in d["markets"] if m["kind"] == "asia"]
        self.assertEqual([m["key"] for m in asia], ["asx", "jp", "hk", "sg", "kr", "tw"])
        for m in asia:
            self.assertIn("hours_wita", m)

    def test_route_and_assets_wired(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertIn('"/api/markets"', open(os.path.join(base, "command_server.py")).read())
        html = open(os.path.join(base, "command_dashboard.html")).read()
        self.assertIn("/static/warroom_markets.js", html); self.assertIn("/static/warroom_markets.css", html)
        self.assertLess(html.index("warroom_clean.js"), html.index("warroom_markets.js"))

    # --- crypto display baseline + scan wording (2026-10-27) ---
    def test_since_reset_zero_at_reset_and_tracks_new_pnl(self):
        bl = {"reset_at": "2026-10-27T18:53:09+08:00", "realized_at_reset_aud": -3383.47, "realized_at_reset_usd": -2380.27}
        self.assertEqual(W.since_reset(-3383.47, -2380.27, bl, 0.7035), 0.0)
        self.assertEqual(W.since_reset(None, -2380.27 + 70.35, bl, 0.7035), 100.0)
        # AUD/USD move alone is not P&L
        self.assertEqual(W.since_reset(-2380.27 / 0.66, -2380.27, bl, 0.66), 0.0)

    def test_since_reset_aud_fallback_and_no_baseline(self):
        self.assertEqual(W.since_reset(-3000.0, None, {"realized_at_reset_aud": -3383.47}, 0.7), 383.47)
        self.assertEqual(W.since_reset(-3383.47, -2380.27, None, 0.7035), -3383.47)

    def test_baseline_file_reader(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"crypto_kraken": {"reset_at": "x", "realized_at_reset_aud": -1.0}}, f)
        try:
            self.assertEqual(W.baseline("crypto_kraken", f.name)["realized_at_reset_aud"], -1.0)
            self.assertIsNone(W.baseline("other", f.name))
        finally:
            os.unlink(f.name)
        self.assertIsNone(W.baseline("crypto_kraken", "/nonexistent/x.json"))

    def test_scan_summary_no_none_wording(self):
        ee = {"utc": "2026-10-27T10:50:06+00:00",
              "latest_md": "TFO entry engine\nPaper only\n\nThis scan\nNone. No name scored 55 or higher. Do nothing.\n\nRejected\nXRP."}
        line, w = W.scan_summary(ee)
        self.assertEqual(w, "Tue 27 Oct 18:50 WITA")
        self.assertTrue(line.startswith("Last scan Tue 27 Oct 18:50 WITA"))
        self.assertNotIn("None", line); self.assertNotIn("This scan", line)
        self.assertEqual(W.scan_summary({}), ("no scan yet", None))
        self.assertEqual(W.scan_summary(None)[0], "no scan yet")

    def test_live_crypto_card_since_reset(self):
        d = W.build()
        c = [m for m in d["markets"] if m["key"] == "crypto"][0]
        self.assertIn("realized_since_reset_aud", c)
        self.assertNotIn("This scan", c["scan_line"] or "")
        if c.get("baseline"):
            self.assertIsNotNone(c["realized_total_aud"])        # history total still reported
            self.assertIsNotNone(c["baseline"]["prior_aud"])

    def test_js_shows_since_reset_and_prior(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js = open(os.path.join(base, "static", "warroom_markets.js")).read()
        self.assertIn("Realized since reset", js); self.assertIn("Prior to reset:", js)
        self.assertIn("(kept in history)", js); self.assertIn("no scan yet", js)


    # --- OANDA strategy lab card (2026-10-27) ---
    def _lab(self, L):
        orig = W._j
        W._j = lambda rel, default=None: L if rel == "state/oanda_lab/lab.json" else orig(rel, default)
        try:
            return W.oanda_lab_block(dt.datetime(2026, 10, 27, 12, 0, tzinfo=U))
        finally:
            W._j = orig

    def test_oanda_lab_missing(self):
        b = self._lab(None)
        self.assertFalse(b["available"])

    def test_oanda_lab_block(self):
        L = {"updated_utc": "2026-10-27T11:50:00+00:00", "updated_wita": "Tue 27 Oct 19:50 WITA", "verdict": "none passed",
             "search": {"configs_tested": 900000, "universe": 127, "survivors_count": 0},
             "survivors": [{"name": "s%d" % i, "holdout_aud": 10} for i in range(7)],
             "hunt": {"picks": [{"ins": "EUR_USD", "side": "LONG", "entry": 1.1, "stop": 1.09, "targets": [1.11, 1.12, 1.13], "risk_aud": 516.08}] * 4,
                      "regime": {"label": "mixed"}, "scorecard": {"n_closed": 2, "net_aud": -100.5, "target_n": 30, "secret": 1}}}
        b = self._lab(L)
        self.assertTrue(b["available"]); self.assertTrue(b["shadow_only"]); self.assertFalse(b["places_orders"])
        self.assertEqual(b["configs_tested"], 900000); self.assertEqual(b["survivors_count"], 0)
        self.assertEqual(len(b["survivors"]), 5); self.assertEqual(len(b["hunt"]["picks"]), 3)
        self.assertEqual(b["hunt"]["regime"], "mixed"); self.assertEqual(b["hunt"]["scorecard"]["net_aud"], -100.5)
        self.assertNotIn("secret", b["hunt"]["scorecard"]); self.assertEqual(b["freshness"]["level"], "green")

    def test_build_has_oanda_lab_key(self):
        self.assertIn("oanda_lab", W.build())

    def test_js_has_lab_card(self):
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "warroom_markets.js")).read()
        self.assertIn("Today's FX hunt: top 3", js); self.assertIn("Hunt scorecard", js); self.assertIn("labcard(d.oanda_lab)", js)

if __name__ == "__main__":
    unittest.main()
