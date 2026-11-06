"""bank07_20261106 (PAPER; fixture data only - temp books, injected prices, no network, no live state).
Brett funder override 5 Nov 2026 18:49 WITA ("Make the new rule to bank half at .7r"), 22:48 ("HARD RULE ACROSS ALL POSITIONS - .7r is half bank
profit"), 6 Nov 12:43/12:46 ("for all positions ... No patches of code for .7r needs to be permanent").
XEL (Nora, 5 Nov): the 1R bank triggered 08:16 ET pre-market (rail_1R_aud_scale_out @ 81.19) but was queued for the open, while the breakeven
hard stop acted on a pre-market print at 09:20 ET -> closed at +0.08R instead of banking half at +1.2R.
Rule now: half-bank at +0.7R NET (after costs, vs ORIGINAL risk) for every position (any grade, trend included), booked on the same fresh
prints the hard stop acts on, at any hour. T1/+2R level exits still wait for the open; a stale mark still queues."""
import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from veteran_desk import book as BK
from test_fillfix_20261103 import _book, _tick
U = dt.timezone.utc

XEL = dict(id="VET-XEL-20261104-1422-essentials_utilities", seat="essentials_utilities", ticker="XEL", side="LONG", venue="US", status="open",
           entry=78.76, fill=78.76, filled_utc="2026-11-04T14:46:05+00:00", armed_utc="2026-11-04T14:26:02+00:00", stop=78.832065, initial_stop=76.558663,
           target=83.162674, shares=1598, risk_aud=5012.52, r0=2.201337, cost_aud=116.01, fill_notional_aud=179317.51, fill_audusd=0.701875, cost_rt_rate=0.000915,
           order_type="limit", order="limit", off_hours_checked=True, rail_state="be", rail_hwp=81.5, peak_fav_R=1.1039, scale_t1=True, sleeve="core", grade="A",
           adv_aud=664740508.0, adv_day="2026-11-05", atr14=1.4821, atr14_day="2026-11-05", time_stop_deadline_utc="2026-11-13T23:59:59+00:00",
           cm_rebaseline=dict(utc="2026-11-04T14:48:06+00:00", at_px=78.76, delta_R=0.0012), gb_peak_net_R=0.9183, ts_peak_net_R=1.2044, revguard_peak_net_R=0.9183)
T_PRE = dt.datetime(2026, 11, 5, 13, 16, 8, tzinfo=U)     # 08:16:08 ET, pre-market: the real XEL bank trigger time
PRE = dict(last=81.19, hi=81.5, lo=80.9, open=81.0, asof="2026-11-05 13:10:00+00:00", window_start="2026-11-05T12:45:00+00:00", src="massive_aggs_5m")   # 6 min old
T_STOP = dt.datetime(2026, 11, 5, 14, 20, 7, tzinfo=U)    # 09:20:07 ET, still pre-market: the real stop time
STOPBAR = dict(last=78.80, hi=79.0, lo=78.70, open=78.95, asof="2026-11-05 14:15:00+00:00", window_start="2026-11-05T13:50:00+00:00", src="massive_aggs_5m")


class Constants(unittest.TestCase):
    def test_rule_values(self):
        self.assertEqual(BK.RAIL_BANK_R, 0.7); self.assertEqual(BK.RAIL_BANK_R_SEAT, {}); self.assertTrue(BK.RAIL_BANK_ALL_POSITIONS)
        self.assertEqual(BK._bank_reason(0.7), "rail_0_7R_aud_scale_out")
        self.assertTrue(any(r.get("id") == "bank07_20261106" for r in BK.RULE_LOG))

    def test_fresh_print_and_acts_now(self):
        self.assertTrue(BK._fresh_print(dict(venue="US", px_age_min=6.0)))
        self.assertFalse(BK._fresh_print(dict(venue="US", px_age_min=16.0)))
        self.assertFalse(BK._fresh_print(dict(venue="US")))
        p = dict(venue="US", px_age_min=6.0)
        self.assertTrue(BK._bank_acts_now(p, "rail_0_7R_aud_scale_out")); self.assertTrue(BK._bank_acts_now(p, "rail_giveback_protect"))
        self.assertFalse(BK._bank_acts_now(p, "target1_scale_out")); self.assertFalse(BK._bank_acts_now(p, "rail_2R_scale_out")); self.assertFalse(BK._bank_acts_now(p, None))
        self.assertFalse(BK._bank_acts_now(dict(venue="US", px_age_min=60.0), "rail_0_7R_aud_scale_out"))


class XelReplay(unittest.TestCase):
    """The 5 Nov XEL path, replayed with the new rule."""
    def test_premarket_bank_books_at_once_then_stop_closes_residual(self):
        path, q = _book(dict(XEL), "essentials_utilities")
        D = _tick(path, q, T_PRE, mark=PRE)
        S = D["seats"]["essentials_utilities"]; p = S["positions"][XEL["id"]]
        self.assertEqual(p["shares"], 799, "half of 1598 banked on the fresh pre-market print, not queued")
        self.assertTrue(p.get("t1_done")); self.assertNotIn("exit_queued", p)
        self.assertEqual([(c["exit"], c["exit_reason"], c["shares"]) for c in S["closed"]], [(81.19, "rail_0_7R_aud_scale_out", 799)])
        self.assertGreaterEqual(S["closed"][0]["net_aud"], 2500.0, "the banked half is real money: ~A$2.7k net on 799 sh from 78.76 to 81.19")
        self.assertGreaterEqual(p["stop"], 78.76, "residual stop sits at the fee floor / breakeven")
        D = _tick(path, q, T_STOP, mark=STOPBAR)   # the same pre-market stop print as on 5 Nov now only closes the residual half
        S = D["seats"]["essentials_utilities"]
        self.assertNotIn(XEL["id"], S["positions"]); self.assertEqual(len(S["closed"]), 2)
        self.assertEqual(S["closed"][1]["exit_reason"], "stop"); self.assertEqual(S["closed"][1]["shares"], 799)
        total = sum(c["net_aud"] for c in S["closed"])
        self.assertGreater(total, 2000.0, "5 Nov reality was +A$416 net on the whole position; the rule now keeps the banked half")

    def test_stale_mark_still_queues(self):
        path, q = _book(dict(XEL), "essentials_utilities")
        stale = dict(PRE, asof="2026-11-05 12:10:00+00:00")   # 66 min old > US stale limit 15 min
        D = _tick(path, q, T_PRE, mark=stale)
        p = D["seats"]["essentials_utilities"]["positions"][XEL["id"]]
        self.assertEqual(p["shares"], 1598); self.assertTrue(p.get("exit_queued")); self.assertEqual(p["exit_queued"]["reason"], "rail_0_7R_aud_scale_out")
        self.assertEqual(D["seats"]["essentials_utilities"]["closed"], [])

    def test_stop_overtaking_a_queued_exit_is_logged(self):
        pos = dict(XEL, exit_queued=dict(reason="rail_1R_aud_scale_out", px=81.19, kind="partial", shares=799, post="half", queued_utc="2026-11-05T13:16:08+00:00", note="queued"))
        path, q = _book(pos, "essentials_utilities")
        D = _tick(path, q, T_STOP, mark=STOPBAR)
        S = D["seats"]["essentials_utilities"]
        self.assertEqual(len(S["closed"]), 1); self.assertEqual(S["closed"][0]["exit_reason"], "stop")
        evs = [r["ev"] for r in S["closed"][0].get("rail_log") or []]
        self.assertIn("stop_overtook_queued_exit", evs)

    def test_grade_b_position_banks_too(self):
        path, q = _book(dict(XEL, grade="B"), "essentials_utilities")
        D = _tick(path, q, T_PRE, mark=PRE)
        self.assertEqual(D["seats"]["essentials_utilities"]["positions"][XEL["id"]]["shares"], 799)

    def test_below_0_7R_nothing_happens(self):
        path, q = _book(dict(XEL), "essentials_utilities")
        low = dict(PRE, last=80.0, hi=80.2, lo=79.8, open=79.9)   # ~+0.5R net
        D = _tick(path, q, T_PRE, mark=low)
        p = D["seats"]["essentials_utilities"]["positions"][XEL["id"]]
        self.assertEqual(p["shares"], 1598); self.assertNotIn("exit_queued", p); self.assertEqual(D["seats"]["essentials_utilities"]["closed"], [])


if __name__ == "__main__":
    unittest.main()
