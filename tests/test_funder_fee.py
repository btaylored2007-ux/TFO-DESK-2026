"""Offline tests for the PAPER funder-fee ledger, scheme FLAT_DAILY (2026-10-02; rate 0.0274%/day from the 4 Oct 2026 charge). Temp reports dir only."""
import datetime as dt, json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import funder_fee as ff

UTC = dt.timezone.utc
H, D = dt.timedelta(hours=1), dt.timedelta(days=1)
T0 = dt.datetime(2026, 10, 2, 7, 35, tzinfo=UTC)       # Fri 2 Oct 17:35 AEST (inception)
SAT1 = dt.datetime(2026, 10, 2, 23, 0, tzinfo=UTC)     # Sat 3 Oct 09:00 AEST (first daily accrual)
SUN = dt.datetime(2026, 10, 3, 22, 0, tzinfo=UTC)      # Sun 4 Oct 09:00 AEDT (DST started 02:00 that morning: 23h day)
MON = dt.datetime(2026, 10, 4, 22, 0, tzinfo=UTC)      # Mon 5 Oct 09:00 AEDT
SAT2 = dt.datetime(2026, 10, 9, 22, 0, tzinfo=UTC)     # Sat 10 Oct 09:00 AEDT


class FunderFee(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp); self.rows = []

    def row(self, t, eq, peak=None, event="no"):
        self.rows.append((t, eq, peak or max([eq] + [r[2] for r in self.rows]), event))
        with open(os.path.join(self.tmp, "firm_equity.csv"), "w") as f:
            f.write("utc,firm_equity_usd,firm_equity_live_usd,stale_equity_usd,firm_peak_usd,firm_drawdown_pct,level,books_readable,books_total,positions,capital_event\n")
            for t_, e, p, ev in self.rows: f.write("%s,%.2f,%.2f,0,%.2f,0,OK,11,11,14,%s\n" % (t_.strftime("%Y-%m-%dT%H:%M:%SZ"), e, e, p, ev))

    def tick(self, now): return ff.tick(now=now, reports=self.tmp)
    def ledger(self): return ff.load(self.tmp)
    def acc(self): return [e for e in self.ledger() if e["event"] == "accrual"]

    def test_rates(self):
        # 2026-10-03 19:17 AEST Brett: 0.047619%/day -> 0.0274%/day from the 4 Oct charge; history kept
        self.assertAlmostEqual(100 * ff.RATE_DAY, 0.0274, places=6)
        self.assertAlmostEqual(100 * ff.RATE_DAY_V1, 0.047619, places=6)
        self.assertAlmostEqual(ff.RATE_DAY * 7, ff.RATE_TARGET_WK)
        self.assertAlmostEqual(ff.DRAG_SIMPLE_PCT, 10.0, places=2)
        self.assertAlmostEqual(ff.BREAK_EVEN_PRE_FEE_PCT, 10.52, places=2)
        self.assertEqual(ff.rate_for("2026-10-03"), ff.RATE_DAY_V1); self.assertEqual(ff.rate_for("2026-10-02"), ff.RATE_DAY_V1)
        self.assertEqual(ff.rate_for("2026-10-04"), 0.000274); self.assertEqual(ff.rate_for("2027-01-01"), 0.000274)
        self.assertAlmostEqual(ff.after_fee_cagr_pct(20.7), 9.21, places=2)
        self.assertEqual(ff.SCHEME, "FLAT_DAILY")

    def test_rate_change_applies_from_4_oct_and_never_restates(self):
        self.row(T0, 1.8e6); self.tick(T0)
        self.row(SAT1 - H, 1.8e6); self.tick(SAT1 + H)
        first = self.acc()[0]
        self.row(SUN - H, 1.8e6); self.row(MON - H, 1.8e6); self.tick(MON + H)
        a = self.acc()
        self.assertEqual([x["rate"] for x in a], [ff.RATE_DAY_V1, 0.000274, 0.000274])
        self.assertEqual(a[0], first)                                           # 3 Oct charge untouched
        self.assertAlmostEqual(a[1]["fee_usd"], round(0.000274 * a[1]["equity_net_before_usd"], 2), places=2)

    def test_boundaries_daily_and_dst(self):
        self.assertEqual(ff.next_boundary_after(T0), SAT1)
        self.assertEqual(ff.next_boundary_after(SAT1), SUN)                 # 23h later: AEDT starts 4 Oct
        self.assertEqual(ff.next_boundary_after(SUN), MON)
        self.assertEqual(ff.last_boundary(SAT1 + dt.timedelta(minutes=1)), SAT1)
        self.assertEqual(ff.last_boundary(SAT1 - dt.timedelta(minutes=1)), SAT1 - D)
        self.assertEqual(ff.last_boundary(SUN - dt.timedelta(minutes=1)), SAT1)
        self.assertEqual(ff.last_weekday_boundary(MON + H, 5), SAT1)
        self.assertEqual(ff.last_weekday_boundary(SAT2 + H, 5), SAT2)
        self.assertEqual(ff.last_month_boundary(MON), dt.datetime(2026, 9, 30, 23, 0, tzinfo=UTC))  # 1 Oct 09:00 AEST

    def test_inception_then_prorated_first_accrual_paid_in_full(self):
        self.row(T0, 1.8e6); s = self.tick(T0 + dt.timedelta(minutes=5))
        self.assertEqual(s["cum_fee_paid_usd"], 0); self.assertEqual(s["hwm_usd"], 1.8e6); self.assertFalse(self.acc())
        self.assertEqual(s["today"]["next_accrual_utc"], "2026-10-02T23:00:00Z")
        self.row(SAT1 - H, 1.81e6); self.tick(SAT1 - dt.timedelta(minutes=1)); self.assertFalse(self.acc())  # not before 09:00
        s = self.tick(SAT1 + dt.timedelta(minutes=10))
        a = self.acc(); self.assertEqual(len(a), 1); a = a[0]
        stub = (SAT1 - T0).total_seconds() / 86400
        self.assertEqual(a["sydney_date"], "2026-10-03"); self.assertEqual(a["boundary_sydney"], "2026-10-03 09:00 AEST")
        self.assertAlmostEqual(a["stub_fraction"], round(stub, 4))
        self.assertAlmostEqual(a["fee_usd"], round(ff.RATE_DAY_V1 * 1.81e6 * stub, 2), places=2)   # 3 Oct charge: old rate
        self.assertEqual(a["payout_usd"], a["fee_usd"])                     # paid the same day, no reserve
        self.assertAlmostEqual(a["hwm_after_usd"], round(1.81e6 - a["fee_usd"], 2), places=2)
        self.assertEqual(s["today"]["fee_paid_usd"], a["fee_usd"])
        self.tick(SAT1 + 2 * H); self.tick(SAT1 + 20 * H); self.assertEqual(len(self.acc()), 1)  # idempotent within the day
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "funder_fee_summary.json")))

    def test_pays_full_rate_below_hwm_and_hwm_display_never_drops(self):
        self.row(T0, 1.8e6); self.tick(T0)
        self.row(SAT1 - H, 1.7e6); self.tick(SAT1 + dt.timedelta(minutes=1))
        self.row(SUN - H, 1.69e6); self.tick(SUN + dt.timedelta(minutes=1))
        a = self.acc()[-1]
        self.assertFalse(a["at_or_above_hwm"]); self.assertEqual(a["rate"], ff.RATE_DAY); self.assertEqual(a["stub_fraction"], 1.0)
        self.assertAlmostEqual(a["fee_usd"], round(ff.RATE_DAY * (1.69e6 - a["cum_fee_before_usd"]), 2), places=2)
        self.assertEqual(a["hwm_after_usd"], 1.8e6)
        self.assertGreater(a["fee_usd"], 0)

    def test_hwm_ratchets_daily(self):
        self.row(T0, 1.8e6); self.tick(T0)
        self.row(SAT1 - H, 1.82e6); self.tick(SAT1 + dt.timedelta(minutes=1))
        self.row(SUN - H, 1.85e6); self.tick(SUN + dt.timedelta(minutes=1))
        a = self.acc()
        self.assertAlmostEqual(a[0]["hwm_after_usd"], a[0]["equity_net_after_usd"]); self.assertAlmostEqual(a[1]["hwm_after_usd"], a[1]["equity_net_after_usd"])
        self.assertGreater(a[1]["hwm_after_usd"], a[0]["hwm_after_usd"])

    def test_catch_up_one_accrual_per_day_across_dst(self):
        self.row(T0, 1.8e6); self.tick(T0)
        for t, e in ((SAT1, 1.80e6), (SUN, 1.81e6), (MON, 1.79e6)): self.row(t - H, e)
        self.tick(MON + 3 * H)
        a = self.acc()
        self.assertEqual([x["sydney_date"] for x in a], ["2026-10-03", "2026-10-04", "2026-10-05"])
        self.assertEqual([x["boundary_utc"] for x in a], ["2026-10-02T23:00:00Z", "2026-10-03T22:00:00Z", "2026-10-04T22:00:00Z"])
        self.assertEqual([x["equity_gross_usd"] for x in a], [1.80e6, 1.81e6, 1.79e6])  # each day uses its own boundary row
        self.tick(MON + 4 * H); self.assertEqual(len(self.acc()), 3)
        self.assertAlmostEqual(a[-1]["cum_fee_after_usd"], sum(x["fee_usd"] for x in a), delta=0.02)
        self.assertEqual(a[-1]["cum_payout_after_usd"], a[-1]["cum_fee_after_usd"])

    def test_waits_for_fresh_equity_then_flags(self):
        self.row(T0, 1.8e6); self.tick(T0)
        self.tick(SAT1 + H); self.assertFalse(self.acc())                    # last row 15h before the boundary, nothing after -> wait
        self.row(SAT1 + 2 * H, 1.81e6); self.tick(SAT1 + 3 * H); self.assertFalse(self.acc())  # < 6h after the boundary -> still wait
        self.tick(SAT1 + 7 * H)
        a = self.acc(); self.assertEqual(len(a), 1); self.assertIn("flag", a[0]); self.assertEqual(a[0]["equity_gross_usd"], 1.81e6)

    def test_capital_flow_shifts_hwm_not_pnl(self):
        self.row(T0, 1.8e6); self.tick(T0)
        self.row(T0 + 5 * H, 2.0e6, peak=2.0e6, event="YES")  # +200k injection
        self.row(SAT1 - H, 2.0e6); self.tick(SAT1 + dt.timedelta(minutes=1))
        a = self.acc()[-1]
        self.assertAlmostEqual(a["flows_usd"], 200000.0); self.assertAlmostEqual(a["pnl_gross_day_usd"], 0.0); self.assertAlmostEqual(a["hwm_before_usd"], 2.0e6)

    def test_summary_windows_and_no_book_writes(self):
        self.row(T0, 1.8e6); self.tick(T0)
        for t, e in ((SAT1, 1.82e6), (SUN, 1.83e6), (MON, 1.81e6)): self.row(t - H, e)
        self.tick(MON + dt.timedelta(minutes=5)); self.row(MON + 2 * H, 1.815e6)
        s = ff.summary(MON + 3 * H, self.tmp); a = self.acc()
        for k in ("equity_net_usd", "hwm_usd", "cum_fee_paid_usd", "today", "day", "week", "month", "pace", "since_inception", "last_day", "annual_fee_drag"):
            self.assertIn(k, s)
        self.assertEqual(s["today"]["fee_paid_usd"], a[-1]["fee_usd"]); self.assertEqual(s["today"]["sydney_date"], "2026-10-05")
        proj = s["today"]["next_fee_projected_usd"]
        self.assertAlmostEqual(proj, round(ff.RATE_DAY * s["equity_net_usd"], 2), places=2)
        self.assertAlmostEqual(s["day"]["pnl_gross_usd"], 5000.0); self.assertAlmostEqual(s["day"]["pnl_net_usd"], round(5000.0 - proj, 2), places=2)
        self.assertAlmostEqual(s["week"]["pnl_gross_usd"], 1.815e6 - 1.82e6)                       # since Sat 3 Oct 09:00
        self.assertAlmostEqual(s["week"]["fees_paid_usd"], a[1]["fee_usd"] + a[2]["fee_usd"], places=2)
        self.assertAlmostEqual(s["month"]["pnl_gross_usd"], 15000.0); self.assertEqual(s["month"]["month"], "2026-10")
        self.assertAlmostEqual(s["month"]["fees_paid_usd"], s["cum_fee_paid_usd"], places=1)
        self.assertAlmostEqual(s["month"]["pnl_net_usd"], round(15000.0 - s["cum_fee_paid_usd"] - proj, 2), delta=0.02)
        self.assertEqual(s["annual_fee_drag"]["simple_pct"], 10.0); self.assertEqual(s["rate_pct_day"], 0.0274); self.assertEqual(s["cum_payout_usd"], s["cum_fee_paid_usd"])
        self.assertEqual(sorted(os.listdir(self.tmp)), ["firm_equity.csv", "funder_fee_ledger.jsonl", "funder_fee_summary.json"])

    def test_live_style_ledger_with_legacy_scheme_events(self):
        # the live ledger has a weekly-era inception + a 'scheme' event; FLAT_DAILY must read it unchanged
        self.row(T0, 1794744.54)
        with open(os.path.join(self.tmp, "funder_fee_ledger.jsonl"), "w") as f:
            f.write(json.dumps({"event": "inception", "utc": "2026-10-02T07:35:00Z", "equity_gross_usd": 1794744.54, "hwm_usd": 1794744.54, "scheme": "HWM_FLEX"}) + "\n")
            f.write(json.dumps({"event": "scheme", "utc": "2026-10-02T08:17:25Z", "scheme": "HWM_FLEX_RESERVE"}) + "\n")
        self.row(SAT1 - H, 1797160.76); self.tick(SAT1 + dt.timedelta(minutes=1))
        a = self.acc(); self.assertEqual(len(a), 1)
        self.assertAlmostEqual(a[0]["fee_usd"], round(ff.RATE_DAY_V1 * 1797160.76 * (SAT1 - T0).total_seconds() / 86400, 2), places=2)

    def test_missing_files_unavailable(self):
        self.assertEqual(ff.summary(T0, self.tmp)["status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
