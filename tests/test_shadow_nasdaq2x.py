"""Offline tests for shadow_nasdaq2x (avenues candidate #1 shadow ledger). No network, no live files."""
import datetime as dt, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shadow_nasdaq2x as S

UTC = dt.timezone.utc
START = dt.date(2026, 10, 2)


def make_px(qqq_fn=None, qld_fn=None, spy_fn=None, end=dt.date(2027, 1, 29)):
    """Synthetic {sym: {date: (open, close, adj)}} on S.sessions from 2025-06-02."""
    days = S.sessions(dt.date(2025, 6, 2), end)
    px = {k: {} for k in ("QQQ", "QLD", "SPY", "^IRX", "AAA", "BBB")}
    for i, d in enumerate(days):
        q = qqq_fn(i, d) if qqq_fn else 500 + i
        l = qld_fn(i, d) if qld_fn else 50 + 0.2 * i
        s = spy_fn(i, d) if spy_fn else 600 + 0.5 * i
        px["QQQ"][d] = (q, q, q); px["QLD"][d] = (l, l, l); px["SPY"][d] = (s * 0.999, s, s)
        px["^IRX"][d] = (4.0, 4.0, 4.0); px["AAA"][d] = (100.0, 100.0, 100.0); px["BBB"][d] = (10 + 0.01 * i,) * 3
    return px, days


def loader(sig):
    return {"AAA": 0.5, "BBB": 0.5}, "2026-09-30", False


def adv(st, px, upto):
    return S.advance(st, px, upto, dt.datetime(2027, 2, 1, tzinfo=UTC), value_loader=loader)


class Calendar(unittest.TestCase):
    def test_nyse_holidays(self):
        h26 = S.nyse_holidays(2026)
        for d in (dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3), dt.date(2026, 5, 25),
                  dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25)):
            self.assertIn(d, h26, d)
        self.assertNotIn(dt.date(2021, 12, 31), S.nyse_holidays(2022))   # Sat New Year: no Friday holiday
        self.assertIn(dt.date(2027, 3, 26), S.nyse_holidays(2027))       # Good Friday 2027

    def test_tom_dates_oct_2026(self):
        self.assertEqual(S.tom_dates(dt.date(2026, 10, 5)), (dt.date(2026, 10, 28), dt.date(2026, 10, 29), dt.date(2026, 11, 4)))
        self.assertEqual(S.first_session_of_month(dt.date(2026, 11, 15)), dt.date(2026, 11, 2))

    def test_final_session(self):
        self.assertEqual(S.final_session(dt.datetime(2026, 10, 2, 12, 0, tzinfo=UTC)), dt.date(2026, 10, 1))
        self.assertEqual(S.final_session(dt.datetime(2026, 10, 2, 20, 30, tzinfo=UTC)), dt.date(2026, 10, 2))
        self.assertEqual(S.final_session(dt.datetime(2026, 10, 4, 2, 0, tzinfo=UTC)), dt.date(2026, 10, 2))   # Saturday


class Ledger(unittest.TestCase):
    def test_pending_before_start_close(self):
        px, _ = make_px()
        st, rows = adv(None, px, dt.date(2026, 10, 1))
        self.assertEqual(st["phase"], "PENDING_FIRST_MARK"); self.assertEqual(rows, [])
        S._guard(st)

    def test_start_allocation_and_idempotent(self):
        px, _ = make_px()
        st, rows = adv(None, px, START)
        self.assertEqual(st["phase"], "LIVE_SHADOW"); self.assertEqual(len(rows), 1)
        v = S.shadow_value(st)
        self.assertAlmostEqual(st["qld"]["usd"], 0.30 * S.NOTIONAL_USD, delta=1)
        self.assertAlmostEqual(S._value_total(st), 0.20 * S.NOTIONAL_USD, delta=1)
        self.assertTrue(st["qld"]["on"])
        self.assertLess(v, S.NOTIONAL_USD); self.assertGreater(v, S.NOTIONAL_USD * 0.998)   # entry costs only
        S._guard(st)
        st2, rows2 = adv(json.loads(json.dumps(st)), px, START)
        self.assertEqual(rows2, []); self.assertEqual(st2["last_mark_date"], st["last_mark_date"])

    def test_qld_off_below_sma(self):
        px, days = make_px(qqq_fn=lambda i, d: 1000 - i)        # falling: below SMA200
        st, _ = adv(None, px, START)
        self.assertFalse(st["qld"]["on"]); self.assertEqual(st["qld"]["usd"], 0.0)

    def test_qld_switches_off_when_filter_flips(self):
        k = S.sessions(dt.date(2025, 6, 2), dt.date(2026, 10, 9)).index(dt.date(2026, 10, 9))
        px, _ = make_px(qqq_fn=lambda i, d: 500 + i if i < k else 300)
        st, _ = adv(None, px, dt.date(2026, 10, 8)); self.assertTrue(st["qld"]["on"])
        st, rows = adv(st, px, dt.date(2026, 10, 9))
        self.assertFalse(st["qld"]["on"]); self.assertEqual(st["qld"]["usd"], 0.0)
        self.assertIn("QLD OFF", rows[-1]["events"])

    def test_marks_follow_prices_and_cash_accrues(self):
        px, _ = make_px()
        st, _ = adv(None, px, START); q0, c0 = st["qld"]["usd"], st["shadow_cash_usd"]
        st, rows = adv(st, px, dt.date(2026, 10, 5))         # Mon: 3 calendar days of T-bill
        l0, l1 = px["QLD"][START][2], px["QLD"][dt.date(2026, 10, 5)][2]
        self.assertAlmostEqual(st["qld"]["usd"], q0 * l1 / l0, places=4)
        self.assertAlmostEqual(st["shadow_cash_usd"], c0 * (1 + 0.04 / 365) ** 3, places=2)

    def test_brake_cuts_and_rearms_after_42_sessions(self):
        days = S.sessions(dt.date(2025, 6, 2), dt.date(2027, 6, 30)); k = days.index(dt.date(2026, 10, 6))
        # QLD collapses 80% on 6 Oct (QQQ stays above SMA): shadow DD ~ -24% -> x0.25
        px, _ = make_px(qld_fn=lambda i, d: 100.0 if i < k else 20.0, end=dt.date(2027, 6, 30))
        st, _ = adv(None, px, START)
        st, rows = adv(st, px, dt.date(2026, 10, 6))
        self.assertEqual(st["brake"]["mult"], 0.25)
        self.assertAlmostEqual(st["qld"]["usd"], 0.30 * 0.25 * S.shadow_value(st), delta=0.001 * S.shadow_value(st))
        self.assertIn("BRAKE", rows[-1]["events"])
        self.assertLess(st["max_drawdown_pct"], -20)
        st, rows = adv(st, px, days[k + 45])
        self.assertEqual(st["brake"]["mult"], 1.0); self.assertGreaterEqual(st["brake"]["resets"], 1)

    def test_tom_overlay_enters_open_and_exits_third_session(self):
        days = S.sessions(dt.date(2025, 6, 2), dt.date(2027, 1, 29)); sig = days.index(dt.date(2026, 10, 28))
        px, _ = make_px(spy_fn=lambda i, d: 700 - 2 * (i - (sig - 10)) if sig - 10 <= i <= sig else 600 + 0.5 * i)
        st, _ = adv(None, px, START)
        st, rows = adv(st, px, dt.date(2026, 10, 28))
        self.assertEqual(st["tom"]["pending_entry"], "2026-10-29"); self.assertEqual(st["tom"]["exit_date"], "2026-11-04")
        st, rows = adv(st, px, dt.date(2026, 10, 29))
        self.assertTrue(st["tom"]["active"]); self.assertGreater(st["tom"]["usd"], 0)
        st, rows = adv(st, px, dt.date(2026, 11, 4))
        self.assertFalse(st["tom"]["active"]); self.assertEqual(st["tom"]["usd"], 0.0)
        self.assertTrue(any("TOM exit" in r["events"] for r in rows))

    def test_monthly_value_rebalance_first_session(self):
        px, _ = make_px()
        st, _ = adv(None, px, START)
        st, rows = adv(st, px, dt.date(2026, 11, 2))
        self.assertEqual(st["value_sleeve"]["rebalanced_month"], "2026-11")
        self.assertAlmostEqual(st["qld"]["usd"], 0.30 * S.shadow_value(st), delta=0.001 * S.shadow_value(st))   # QLD reset monthly
        self.assertTrue(any("VALUE rebalance" in r["events"] for r in rows if r["date"] == "2026-11-02"))


class Isolation(unittest.TestCase):
    def test_files_outside_reports_and_skipped_by_coverage_audit(self):
        import book_coverage
        for p in (S.STATE_PATH, S.HISTORY_PATH):
            self.assertNotIn(os.sep + "reports" + os.sep, p)
            self.assertTrue(any(tok in p for tok in book_coverage.AUDIT_SKIP), p)

    def test_not_wired_into_firm_equity_risk_or_fee(self):
        base = os.path.dirname(S.__file__)
        for f in ("funder_fee.py", "firm_risk.py", "book_coverage.py", "desk_caps.py", "capital_accounting.py", "tfo_equity_refresh.py"):
            with open(os.path.join(base, f)) as fh: src = fh.read()
            self.assertNotIn("shadow_nasdaq2x", src, f)

    def test_no_order_paths(self):
        with open(S.__file__) as fh: src = fh.read()
        for bad in ("import tfo_buy", "import tfo_ticket", "desk_lock", "fundamental_book", "capital_transfer", "alpaca"):
            self.assertNotIn(bad, src)

    def test_state_write_and_summary(self):
        px, _ = make_px()
        with tempfile.TemporaryDirectory() as tmp:
            sp, hp = os.path.join(tmp, "shadow.json"), os.path.join(tmp, "shadow_history.csv")
            self.assertEqual(S.summary(sp)["status"], "NOT_STARTED")
            st, rows = adv(None, px, dt.date(2026, 10, 7)); S.save_state(st, sp, rows, hp)
            s = S.summary(sp)
            self.assertEqual(s["status"], "OK"); self.assertFalse(s["in_firm_equity"]); self.assertTrue(s["qld_on"])
            self.assertEqual(s["start_date"], "2026-10-02"); self.assertEqual(s["notional_usd"], S.NOTIONAL_USD)
            self.assertEqual(s["last_mark_date"], "2026-10-07"); self.assertIn("NORMAL", s["brake_state"])
            with open(hp) as fh: self.assertEqual(len(fh.read().strip().splitlines()), 1 + 4)
            with self.assertRaises(ValueError): S.save_state({"cash": 1}, sp)

    def test_run_uses_injected_fetch_and_is_idempotent(self):
        px, _ = make_px()
        with tempfile.TemporaryDirectory() as tmp:
            sp, hp = os.path.join(tmp, "shadow.json"), os.path.join(tmp, "h_shadow.csv")
            calls = []
            def fetch(syms, period): calls.append(tuple(syms)); return {k: v for k, v in px.items() if k in syms}
            now = dt.datetime(2026, 10, 6, 21, 0, tzinfo=UTC)
            with mock.patch.object(S, "VALUE_TARGET", os.path.join(tmp, "missing.json")):
                st, rows = S.run(now, fetch, sp, hp, value_loader=loader)
                self.assertEqual([r["date"] for r in rows], ["2026-10-02", "2026-10-05", "2026-10-06"])
                n = len(calls); st, rows = S.run(now, fetch, sp, hp, value_loader=loader)
                self.assertEqual(rows, []); self.assertEqual(len(calls), n)       # nothing new: no fetch, no rows


if __name__ == "__main__":
    unittest.main()
