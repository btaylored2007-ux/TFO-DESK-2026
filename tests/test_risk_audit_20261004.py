"""Offline tests: TFO Risk audit 4 Oct 2026 (Head Trader adopted 21:30 AEDT). No network, temp ledgers, no live tickets/logs."""
import contextlib, datetime as dt, io, json, os, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "overnight2k"))
import risk_caps as rc
import veto_log as vl
import post_trade_entry_px as pte
from test_tfo_trail_rail import CryptoEngine, UTC

QUIET = lambda: contextlib.redirect_stdout(io.StringIO())


class Rule1PlannedLossCap(unittest.TestCase):
    def test_cap_at_5pct_stop_is_about_90k(self):
        self.assertAlmostEqual(rc.planned_loss_cap_usd(0.05), 5000 / 0.055)
        d = rc.size_entry(256000, 0.05)                        # the 8766.T clip
        self.assertTrue(d["bound"]); self.assertEqual(d["binds"], ["planned_loss_cap"])
        self.assertEqual(d["usd"], 90900.0); self.assertLessEqual(d["planned_loss_usd"], 5000)

    def test_unbound_clip_unchanged(self):
        d = rc.size_entry(37123.45, 0.05)
        self.assertFalse(d["bound"]); self.assertEqual(d["usd"], 37123.45); self.assertFalse(d["skip"])

    def test_measured_slip_widens_allowance_never_narrows(self):
        self.assertEqual(rc.slip_allowance(0.001), 0.005)
        self.assertEqual(rc.slip_allowance(0.012), 0.012)
        self.assertAlmostEqual(rc.planned_loss_cap_usd(0.05, 0.01), 5000 / 0.06)

    def test_sized_from_actual_stop_not_atr(self):
        d = rc.size_entry(200000, 0.12, atr_pct=0.06)           # C3 stop 2xATR: cap from the 12% stop
        self.assertEqual(d["usd"], 40000.0); self.assertNotIn("atr_above_stop", d["binds"])

    def test_unusable_stop_fails_closed(self):
        for bad in (None, 0, -1, "x"):
            d = rc.size_entry(10000, bad); self.assertTrue(d["skip"]); self.assertEqual(d["usd"], 0.0)

    def test_log_only_when_bound_or_skip(self):
        tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "log.jsonl")
        self.assertIsNone(rc.log_bind(rc.size_entry(10000, 0.05), "t", "X", True, path=p)); self.assertFalse(os.path.exists(p))
        rc.log_bind(rc.size_entry(200000, 0.05), "t", "X", True, path=p)
        rec = json.loads(open(p).read()); self.assertEqual((rec["source"], rec["usd"], rec["rule"]), ("t", 90900.0, rc.RULE_ID))


class Rule2HighAtr(unittest.TestCase):
    def test_hbar_case_scaled_by_stop_over_atr(self):
        d = rc.size_entry(50000, 0.05, atr_pct=0.094, apply_atr_rule=True, min_clip=5000)
        self.assertEqual(d["binds"], ["atr_above_stop"]); self.assertAlmostEqual(d["atr_scale"], 0.05 / 0.094, places=6)
        self.assertEqual(d["usd"], 26500.0); self.assertFalse(d["skip"])

    def test_both_rules_compound(self):
        d = rc.size_entry(150000, 0.05, atr_pct=0.10, apply_atr_rule=True, min_clip=5000)
        self.assertEqual(d["binds"], ["planned_loss_cap", "atr_above_stop"]); self.assertEqual(d["usd"], 45400.0)

    def test_skip_below_min_never_rounded_up(self):
        d = rc.size_entry(8000, 0.05, atr_pct=0.20, apply_atr_rule=True, min_clip=5000)
        self.assertTrue(d["skip"]); self.assertIn("desk minimum", d["skip_reason"]); self.assertEqual(d["usd"], 2000.0)

    def test_atr_below_stop_no_change_and_small_unbound_request_not_skipped(self):
        d = rc.size_entry(3000, 0.05, atr_pct=0.03, apply_atr_rule=True, min_clip=5000)
        self.assertFalse(d["bound"]); self.assertFalse(d["skip"]); self.assertEqual(d["usd"], 3000)


class CryptoBuyPath(CryptoEngine):
    def _run(self, usd, info, **kw):
        tc = self.tc; calls = []
        q = {"pair": "ETH/USD", "bid": 100.0, "ask": 100.0, "last": 100.0, "mid": 100.0, "spread_bp": 1.0, "tape": "LIVE"}
        def gates(pair, u, theme): calls.append(u); return [], q, dict(info)
        self.book(); self.w("tfo_gcl_powder", {"cash": 1e7, "positions": {}})
        out = io.StringIO()
        with mock.patch.object(tc, "gates", gates), mock.patch.object(tc, "xfer", lambda *a, **k: None), contextlib.redirect_stdout(out):
            tc.cmd_buy("ETH/USD", usd, "crypto-major", "beta", "t", True, **kw)
        return calls, out.getvalue()

    def test_c3_clip_capped_regated_ticketed_and_logged_off_live_desk(self):
        calls, out = self._run(100000, {"c3_stop_dist": 0.1, "c3_atr14_entry": 0.05})
        self.assertEqual(calls, [100000, 47600.0])            # re-gated at the risk-sized clip
        tk = self.r("tfo_ht_crypto")["head_trader_tickets"][-1]
        self.assertEqual(tk["principal_usd"], 47600.0); self.assertTrue(tk["risk_cap"]["bound"])
        self.assertIn("RISKCAP BOUND", out)
        log = os.path.join(self.tmp, "risk_cap_log.jsonl"); self.assertTrue(os.path.exists(log))
        self.assertEqual(json.loads(open(log).read())["source"], "tfo_crypto.buy")

    def test_caller_stop_tighter_and_atr_rule(self):           # overnight2k-like: -5% night stop, ATR 9.4% > stop
        calls, _ = self._run(50000, {"c3_stop_dist": 0.188, "c3_atr14_entry": 0.094}, stop_pct=0.05)
        self.assertEqual(calls[-1], 26500.0)

    def test_presized_does_not_reapply_atr_rule(self):
        calls, _ = self._run(26500, {"c3_stop_dist": 0.188, "c3_atr14_entry": 0.094}, stop_pct=0.05, slip_pct=0.001, presized=True)
        self.assertEqual(calls, [26500])
        self.assertEqual(self.r("tfo_ht_crypto")["head_trader_tickets"][-1]["principal_usd"], 26500)

    def test_unbound_clip_untouched(self):
        calls, _ = self._run(30000, {"c3_stop_dist": 0.1, "c3_atr14_entry": 0.05})
        self.assertEqual(calls, [30000]); self.assertEqual(self.r("tfo_ht_crypto")["head_trader_tickets"][-1]["principal_usd"], 30000)

    def test_no_stop_fails_closed_no_ticket(self):
        _, out = self._run(30000, {})                          # C3 with no ATR -> no stop -> refused
        self.assertIn("LOG none", out); self.assertEqual(self.r("tfo_ht_crypto")["head_trader_tickets"], [])


class EquityBuyPath(unittest.TestCase):
    def test_risk_size_and_execute_guard(self):
        import tfo_buy
        d = tfo_buy.risk_size(180000); self.assertEqual(d["usd"], 90900.0)
        with self.assertRaises(SystemExit): tfo_buy.execute("AAPL", 150000.0, "t", "beta", "", {"px_usd": 100.0, "tape": "LIVE"})

    def test_main_clamps_before_gates_and_execute(self):
        import tfo_buy
        seen = {}
        def gates(sym, usd, theme): seen["gates"] = usd; return [], {"px_usd": 100.0, "tape": "LIVE"}, {}
        def execute(sym, usd, theme, label, why, q, risk_cap=None): seen["exec"] = (usd, risk_cap["bound"]); return "LOG x"
        with mock.patch.object(tfo_buy, "gates", gates), mock.patch.object(tfo_buy, "execute", execute), \
             mock.patch.object(tfo_buy, "_cap_log_path", lambda: os.path.join(tempfile.mkdtemp(), "l.jsonl")), \
             mock.patch.object(sys, "argv", ["tfo_buy.py", "AAPL", "200000", "--theme", "t", "--execute"]), QUIET():
            tfo_buy.main()
        self.assertEqual(seen, {"gates": 90900.0, "exec": (90900.0, True)})


class Overnight2k(unittest.TestCase):
    def test_risk_size_clip(self):
        import overnight2k as o2
        d = o2.risk_size_clip("HBAR/USD", 50000.0, 0.2, atr_fetch=lambda: 0.094)
        self.assertEqual(d["usd"], 26500.0); self.assertEqual(d["stop_dist"], 0.05)
        d = o2.risk_size_clip("X/USD", 25000.0, 0.1, atr_fetch=lambda: 0.02)   # low ATR: C3 stop 4% < 5% night stop
        self.assertEqual(d["stop_dist"], 0.04); self.assertFalse(d["bound"])
        d = o2.risk_size_clip("X/USD", 25000.0, 0.1, atr_fetch=lambda: None)
        self.assertTrue(d["skip"]); self.assertIn("fail closed", d["skip_reason"])
        d = o2.risk_size_clip("X/USD", 25000.0, 0.1, atr_fetch=lambda: 0.40)   # 25k x 0.05/0.40 = 3.1k < 5k min
        self.assertTrue(d["skip"])


class Watchlist(unittest.TestCase):
    def test_planned_loss_cap(self):
        import tfo_watchlist as wl
        self.assertAlmostEqual(wl.planned_loss_cap("US", 2.7), 5000 / 0.055)
        self.assertAlmostEqual(wl.planned_loss_cap("CRYPTO", 6.0), 5000 / 0.125)    # C3 stop 12%


class VetoLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for k, v in (("VDIR", self.tmp), ("LEDGER", os.path.join(self.tmp, "v.jsonl")), ("COUNTER", os.path.join(self.tmp, "c.json")), ("LOCK", os.path.join(self.tmp, ".l"))):
            p = mock.patch.object(vl, k, v); p.start(); self.addCleanup(p.stop)

    def test_log_dedupe_fail_closed_counter(self):
        t = dt.datetime(2026, 10, 4, 11, 0, tzinfo=UTC)
        with QUIET():
            a = vl.log_veto("PUMP", "H108 shorts paying", "overnight2k", ref_px=1.0, kraken_key="PUMPUSD", ts=t)
            b = vl.log_veto("PUMP", "H108 shorts paying", "overnight2k", ref_px=1.01, kraken_key="PUMPUSD", ts=t + dt.timedelta(hours=1))
            c = vl.log_veto("ABC", "veto data unavailable (fail closed): x", "overnight2k", veto=True, ts=t)
        self.assertTrue(a["counts_toward_review"]); self.assertFalse(b["counts_toward_review"]); self.assertEqual(b["repeat_of"], a["id"])
        self.assertEqual(c["veto_kind"], "fail_closed"); self.assertFalse(c["counts_toward_review"])
        cnt = json.load(open(vl.COUNTER)); self.assertEqual((cnt["counted_vetoes"], cnt["total_logged"], cnt["review_due"]), (1, 3, False))
        self.assertEqual(set(a), set(a) | {"symbol", "utc", "veto_reason", "outcome"})

    def test_review_due_at_30(self):
        t = dt.datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
        with QUIET():
            for i in range(30): vl.log_veto("C%02d" % i, "top gainer", "overnight2k", ts=t)
        self.assertTrue(json.load(open(vl.COUNTER))["review_due"])

    def test_outcome_and_fill(self):
        t0 = dt.datetime(2026, 10, 1, 0, 0, tzinfo=UTC); ts0 = int(t0.timestamp())
        bars = [(ts0 + 3600 * i, 100.0, 101.0, 99.0, 100.0 + 0.1 * i) for i in range(-1, 80)]
        o = vl.outcome_from_bars(bars, ts0, 100.0, 24)
        self.assertAlmostEqual(o["ret"], 0.023); self.assertFalse(o["stop_hit"]); self.assertAlmostEqual(o["ret_stop_adj_net"], 0.023 - vl.ROUND_TRIP_COST)  # 17 Oct 2026: stale 0.52%; real fees 13 Oct = 0.80% RT
        bars2 = [(b[0], b[1], b[2], 94.0 if i == 5 else b[3], b[4]) for i, b in enumerate(bars)]
        self.assertTrue(vl.outcome_from_bars(bars2, ts0, 100.0, 24)["stop_hit"])
        self.assertIsNone(vl.outcome_from_bars(bars[:10], ts0, 100.0, 24))        # window not covered yet
        with QUIET(): vl.log_veto("PUMP", "H108", "overnight2k", ref_px=100.0, kraken_key="PUMPUSD", ts=t0)
        n, c = vl.fill_outcomes(now=t0 + dt.timedelta(hours=30), fetch_kraken=lambda k, s: bars)
        self.assertEqual(n, 1); r = vl.read_all()[0]; self.assertIsNotNone(r["outcome"]["24h"]); self.assertIsNone(r["outcome"]["72h"])
        n, c = vl.fill_outcomes(now=t0 + dt.timedelta(hours=80), fetch_kraken=lambda k, s: bars)
        r = vl.read_all()[0]; self.assertEqual(r["outcome_status"], "complete"); self.assertEqual(c["outcomes_72h"]["n"], 1)


class PostTradeEntryPx(unittest.TestCase):
    def test_8766_split_uses_exit_basis_fill(self):
        buy = {"sym": "8766.T", "action": "BUY", "utc": "2026-09-28T04:36:51Z", "qty": 4995.310654197686, "px_usd": 51.24806397873908, "principal_usd": 256000.0}
        stop = {"sym": "8766.T", "action": "STOP", "utc": "2026-10-01T00:48:02Z", "qty": 74929.65981296528, "px_usd": 3.241304037126369, "principal_usd": 256000.0}
        px, d = pte.entry_px_from_tickets(buy, [stop])
        self.assertAlmostEqual(px, 256000 / 74929.65981296528); self.assertEqual(d["split_factor"], 15)
        row = {"symbol": "8766.T", "book": "equity", "ts_utc": stop["utc"], "entry_px": 51.24806397873908, "qty": stop["qty"]}
        rows, corr = pte.fix_closes([row], [buy, stop], [])
        self.assertEqual(len(corr), 1); self.assertAlmostEqual(rows[0]["entry_px"], 3.41654, places=4); self.assertEqual(rows[0]["entry_px_raw_report"], 51.24806397873908)
        self.assertAlmostEqual((stop["px_usd"] / rows[0]["entry_px"] - 1) * 100, -5.13, places=1)   # the designed -5% stop, not -94%

    def test_no_split_no_correction(self):
        buy = {"sym": "A", "action": "BUY", "utc": "1", "qty": 10, "px_usd": 100.0}; ex = {"sym": "A", "action": "FLAT", "utc": "2", "qty": 10, "px_usd": 95.0, "principal_usd": 1000.0}
        rows, corr = pte.fix_closes([{"symbol": "A", "book": "equity", "ts_utc": "2", "entry_px": 100.0}], [buy, ex], [])
        self.assertEqual(corr, [])


class Labels(unittest.TestCase):
    def test_dashboard_says_hypothetical_notional(self):
        h = open(os.path.join(ROOT, "private_wealth_dashboard.html")).read()
        self.assertIn("hypothetical notional ${usd(t.hypothetical_notional_usd??t.shadow_value_usd)}", h)
        self.assertNotIn("· value ${usd(t.shadow_value_usd)}", h)
        src = open(os.path.join(ROOT, "deep19_fwd.py")).read()
        self.assertEqual(src.count("hypothetical_notional_usd=round(v, 2)"), 2); self.assertIn("shadow_value_usd=round(v, 2)", src)


if __name__ == "__main__":
    unittest.main()
