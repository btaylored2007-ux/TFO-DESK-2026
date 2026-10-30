"""Remaining audit fixes 13 Oct 2026 (#3 deferred exits, #9 OXF1 trail, #10 252/EWMA, GPT N07/N08/N12/N13). Offline."""
import datetime as dt, importlib.util, json, math, os, sys, tempfile, shutil, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import oanda_designs as D, oanda_risk as RK
from test_oanda_audit_fixes_20261013 import Base, T, bars
UTC = dt.timezone.utc
class Vol252Ewma(unittest.TestCase):
    def test_blueprint_ewma_example(self):
        c = [1.0]
        for i in range(20): c.append(c[-1] * math.exp(0.006 if i % 2 == 0 else -0.006))
        c.append(c[-1] * 1.100 / 1.095)
        self.assertAlmostEqual(RK.ewma_sigma_d([dict(c=x) for x in c], arith=False), 0.005985390678, places=9)
        self.assertAlmostEqual(RK.LAMBDA_VOL, 0.988514020353, places=12); self.assertEqual(RK.VOL_DAYS, 252)
    def test_sigma_ann_uses_252(self):
        cs = [dict(c=1.0 + 0.001 * (i % 3)) for i in range(100)]
        self.assertAlmostEqual(RK.sigma_ann(cs), RK.ewma_sigma_d(cs) * math.sqrt(252))
    def test_cov_needs_252_rows(self):
        self.assertIsNone(RK.ewma_cov([[0.001, 0.002]] * 251)); self.assertIsNotNone(RK.ewma_cov([[0.001, 0.002]] * 252))
class ShadowCoverage(unittest.TestCase):
    def test_missing_factor_is_not_zero_risk(self):
        S = [[1e-4]]
        self.assertIsNone(RK.forecast_vol({"EUR": 0.5, "JPY": -0.5}, ["EUR"], S))
        r = RK.batch_a_star([], [dict(ins="EUR_JPY", dir=1, risk_frac=0.005, notional_frac=0.5)], ["EUR"], {"shrunk": S})
        self.assertEqual(r["a_star"], 0.0); self.assertIn("vol_coverage_missing:JPY", r["limiting"])
        self.assertIn("vol_unavailable", " ".join(RK.gpt_caps_check([dict(ins="EUR_JPY", dir=1, risk_frac=0.005, notional_frac=0.5)], ["EUR"], S, require_vol=True)))
class MissingFinancing(Base):
    def test_carry_entry_needs_financing(self):
        R = {"AUD": [("2026-08-01", 4.5)], "JPY": [("2026-08-01", 0.5)]}; cs = [dict(t="2026-10-08T21:00:00.000000000Z", c=110.0)]
        D.LIVE_FIN = {}; self.assertIsNone(D._carry("AUD_JPY", {"AUD_JPY": cs}, R))
        D.LIVE_FIN = {"AUD_JPY": {"longRate": "0.02", "shortRate": "-0.04"}}; self.assertAlmostEqual(D._carry("AUD_JPY", {"AUD_JPY": cs}, R), 0.04)
        D.LIVE_FIN = {}
    def test_cost_gate_skips_without_financing(self):
        self.m.instruments = lambda: {k: {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "minimumTradeSize": "1"} for k in D.P32}
        self.assertEqual(self.m.place_entry(self.entry(), live=False), "skip:no_financing_data")
class OXF1Trail(Base):
    def test_trail_includes_entry_bar_and_fill(self):
        cs = bars([1.0 + 0.001 * i for i in range(60)])
        ot = (dt.datetime.strptime(cs[50]["t"][:19], "%Y-%m-%dT%H:%M:%S") + dt.timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")   # inside bar 50
        t = T(1, "EUR_USD", 1000, 1.0600, 1.0, design="OXF1", openTime=ot)
        ns, why = self.m.trail_stop(t, cs, self.m.cfg())
        self.assertAlmostEqual(ns, 1.0600 - 3 * RK.wilder_atr(cs))      # fill 1.0600 > every close since entry (max 1.059) -> fill seeds
        t2 = T(2, "EUR_USD", 1000, 1.0, 1.0, design="OXF1", openTime=ot)
        cs2 = bars([1.0 + 0.001 * i for i in range(51)] + [0.9] * 9); ns2, _ = self.m.trail_stop(t2, cs2, self.m.cfg())
        self.assertAlmostEqual(ns2, 1.050 - 3 * RK.wilder_atr(cs2))       # bar 50 (entry bar, close 1.050) now counts
class DeferredExits(Base):
    def setUp(self):
        super().setUp(); self.closed = []; self.m.close_trade = lambda t, why, live: self.closed.append((t["id"], why)) or True
    def test_wide_spread_defers_then_normal_spread_closes(self):
        t = T(1, "EUR_USD", 1000, 1.1, 1.0)
        self.px.update(bid=1.0, ask=1.0030, sp=0.003)                     # 30bp rollover spread
        self.assertTrue(self.m.request_exit(t, "signal flip", 1e-4, self.m.cfg(), True)); self.assertEqual(self.closed, [])
        x = json.load(open(os.path.join(self.m.ST, "pending_exits.json")))["exits"]; self.assertEqual(x[0]["trade_id"], "1")
        self.m.run_exits([t], self.m.cfg(), True); self.assertEqual(self.closed, [])     # still wide, before deadline
        self.px.update(ask=1.0001, sp=1e-4); self.m.run_exits([t], self.m.cfg(), True)
        self.assertEqual(len(self.closed), 1); self.assertEqual(json.load(open(os.path.join(self.m.ST, "pending_exits.json")))["exits"], [])
    def test_deadline_forces_close_and_gone_trade_dropped(self):
        t = T(1, "EUR_USD", 1000, 1.1, 1.0); self.px.update(ask=1.0030, sp=0.003)
        self.m._save_exits([dict(trade_id="1", instrument="EUR_USD", reason="flip", floor_sp=1e-4, deadline_utc="2000-01-01T00:00:00+00:00"),
                            dict(trade_id="2", instrument="GBP_USD", reason="flip", floor_sp=1e-4, deadline_utc="2099-01-01T00:00:00+00:00")])
        self.m.run_exits([t], self.m.cfg(), True)
        self.assertEqual([c[0] for c in self.closed], ["1"]); self.assertEqual(json.load(open(os.path.join(self.m.ST, "pending_exits.json")))["exits"], [])
    def test_normal_spread_closes_immediately(self):
        self.assertTrue(self.m.request_exit(T(1, "EUR_USD", 1000, 1.1, 1.0), "flip", 1e-4, self.m.cfg(), True)); self.assertEqual(len(self.closed), 1)
class NavReconWatermark(Base):
    def test_transfer_after_watermark_not_counted_and_checkpoint_after_row(self):
        p = os.path.join(self.m.ST, "nav_recon.jsonl"); open(p, "w").write(json.dumps(dict(date="2000-01-01", nav=20000)) + "\n")
        self.handler = lambda m_, p_, b_, q_: {"lastTransactionID": "12", "transactions": [{"id": "8", "type": "TRANSFER_FUNDS", "amount": "5000"},
                                                                                         {"id": "11", "type": "TRANSFER_FUNDS", "amount": "999"}]}
        self.m.summary = lambda: {"lastTransactionID": "9", "NAV": "25100", "balance": "25000", "unrealizedPL": "100"}
        self.m.nav_reconcile(dict(nav=1, balance=1, upl=0)); r = [json.loads(l) for l in open(p)][-1]
        self.assertEqual(r["external_flow_aud"], 5000.0); self.assertAlmostEqual(r["r_nav"], 0.005); self.assertEqual(r["nav"], 25100.0)
        st = json.load(open(os.path.join(self.m.ST, "transfer_state.json"))); self.assertEqual(st["last_id"], "9"); self.assertNotIn("11", st["seen"])
    def test_failed_append_does_not_consume_flow(self):
        p = os.path.join(self.m.ST, "nav_recon.jsonl"); open(p, "w").write(json.dumps(dict(date="2000-01-01", nav=20000)) + "\n")
        self.handler = lambda m_, p_, b_, q_: {"lastTransactionID": "9", "transactions": [{"id": "8", "type": "TRANSFER_FUNDS", "amount": "5000"}]}
        self.m.summary = lambda: {"lastTransactionID": "9", "NAV": "25100", "balance": "25000", "unrealizedPL": "100"}
        orig = self.m._append; self.m._append = lambda *a: (_ for _ in ()).throw(OSError("disk"))
        self.m.nav_reconcile(dict(nav=1, balance=1, upl=0)); self.m._append = orig
        self.assertFalse(os.path.exists(os.path.join(self.m.ST, "transfer_state.json")))
class GptRound7(Base):
    def _recon(self, txns, wm="9", nav="25100"):
        self.handler = lambda m_, p_, b_, q_: {"lastTransactionID": "99", "transactions": txns}
        self.m.summary = lambda: {"lastTransactionID": wm, "NAV": nav, "balance": nav, "unrealizedPL": "0"}
    def test_summary_first_then_flows_to_watermark(self):
        order = []; p = os.path.join(self.m.ST, "nav_recon.jsonl"); open(p, "w").write(json.dumps(dict(date="2000-01-01", nav=20000)) + "\n")
        self.m.summary = lambda: order.append("summary") or {"lastTransactionID": "9", "NAV": "25100", "balance": "25100", "unrealizedPL": "0"}
        self.handler = lambda m_, p_, b_, q_: order.append("txns") or {"transactions": [{"id": "8", "type": "TRANSFER_FUNDS", "amount": "5000"}]}
        self.m.nav_reconcile({}); self.assertEqual(order, ["summary", "txns"])
    def test_checkpoint_recovered_from_recon_row(self):
        p = os.path.join(self.m.ST, "nav_recon.jsonl")
        open(p, "w").write(json.dumps(dict(date="2000-01-01", nav=25100, watermark_txn=9, transfer_ids=["8"])) + "\n")   # row written, checkpoint lost
        self._recon([{"id": "8", "type": "TRANSFER_FUNDS", "amount": "5000"}])
        self.m.nav_reconcile({}); r = [json.loads(l) for l in open(p)][-1]
        self.assertEqual(r["external_flow_aud"], 0.0); self.assertAlmostEqual(r["r_nav"], 0.0)
    def test_rerequest_keeps_earliest_deadline(self):
        self.m.close_trade = lambda *a: True; t = T(1, "EUR_USD", 1000, 1.1, 1.0); self.px.update(ask=1.0030, sp=0.003)
        self.m._save_exits([dict(trade_id="1", instrument="EUR_USD", reason="flip", floor_sp=1e-4, deadline_utc="2026-01-01T07:05:00+00:00")])
        self.m.request_exit(t, "flip again", 1e-4, self.m.cfg(), True)
        self.assertEqual(json.load(open(os.path.join(self.m.ST, "pending_exits.json")))["exits"][0]["deadline_utc"], "2026-01-01T07:05:00+00:00")
    def test_reversal_defers_while_exit_pending(self):
        self.open = [T(1, "EUR_USD", 1000, 1.1, 1.0)]
        self.m._save_exits([dict(trade_id="1", instrument="EUR_USD", reason="flip", floor_sp=1e-4, deadline_utc="2099-01-01T00:00:00+00:00")])
        self.assertEqual(self.m.place_entry(self.entry(d=-1), live=False), "defer:exit_pending")
        self.m._save_exits([]); self.assertEqual(self.m.place_entry(self.entry(d=-1), live=False), "skip:already_open")
    def test_reversal_defers_before_slot_check_when_full(self):   # GPT r8 F3
        self.open = [T(1, "EUR_USD", 1000, 1.1, 1.0, design="OXF1")] + [T(i, x, 1000, 1.1, 1.0, design="OXF1") for i, x in ((2, "GBP_USD"), (3, "AUD_USD"), (4, "NZD_USD"))]
        self.m._save_exits([dict(trade_id="1", instrument="EUR_USD", reason="flip", floor_sp=1e-4, deadline_utc="2099-01-01T00:00:00+00:00")])
        self.assertEqual(self.m.place_entry(self.entry(d=-1, design="OXF1"), live=False), "defer:exit_pending")
    def test_cap_block_defers_while_other_exit_pending(self):   # GPT r9 F3
        self.open = [T(i, x, 1000, 1.1, 1.0, design="OXF1") for i, x in ((2, "GBP_USD"), (3, "AUD_USD"), (4, "NZD_USD"), (5, "USD_CAD"))]
        self.m._save_exits([dict(trade_id="2", instrument="GBP_USD", reason="flip", floor_sp=1e-4, deadline_utc="2099-01-01T00:00:00+00:00")])
        r = self.m.place_entry(self.entry(d=-1, design="OXF1"), live=False); self.assertTrue(r.startswith("defer:") and r.endswith("_exit_pending"), r)
        self.m._save_exits([]); self.assertTrue(self.m.place_entry(self.entry(d=-1, design="OXF1"), live=False).startswith("skip:"))
    def test_all_none_covariances_are_unavailable(self):
        r = RK.batch_a_star([], [dict(ins="EUR_JPY", dir=1, risk_frac=0.005, notional_frac=0.5)], ["EUR", "JPY"], {"raw": None, "shrunk": None})
        self.assertEqual(r["a_star"], 0.0); self.assertIn("vol_unavailable", r["limiting"])
    def test_raw_vol_unavailable_is_breach(self):
        self.assertEqual(self.m._raw_vol_breach([dict(ins="EUR_JPY", dir=1, risk_frac=0.005, notional_frac=0.5)], ["EUR"], [[1e-4]]), ["vol_raw_unavailable"])
        self.assertEqual(self.m._raw_vol_breach([dict(ins="EUR_JPY", dir=1, risk_frac=0.005, notional_frac=0.5)], ["EUR"], None), ["vol_raw_unavailable"])
if __name__ == "__main__": unittest.main()
