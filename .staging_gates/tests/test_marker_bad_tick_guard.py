"""Marker bad-tick guard (tfo_equity_refresh / shared bad_tick_guard) and flow-adjusted book risk (PAPER)."""
import copy, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bad_tick_guard as btg
import tfo_equity_refresh as ter

NOW = "2026-09-29T00:40:00Z"


def quote(px_usd, px=None):
    return {"px": px if px is not None else px_usd, "ccy": "USD", "px_usd": px_usd, "fx_usd": 1.0, "quote_utc": "2026-09-29T00:39:00Z",
            "fetched_at": NOW, "session": "regular", "status": "regular_current", "executable": True, "quote_current": True,
            "source": "test", "fx_status": "identity", "url": "https://example.test/q"}


def book():
    return {"cash": 100.0, "seed_usd": 100.0 + 256000.0, "realised_usd": 0, "costs_paid_usd": 0, "closed": {},
            "positions": {"8766.T": {"side": "long", "units": 4995.31, "notional_usd": 256000.0, "entry_px_usd": 51.248,
                                     "rail_orig_entry_usd": 51.248, "mark_usd": 51.2, "mark": 8075.0},
                          "AMD": {"side": "long", "units": 10, "notional_usd": 1000.0, "entry_px_usd": 100.0, "mark_usd": 100.0}}}


class GuardMathTests(unittest.TestCase):
    def test_mark_check_thresholds(self):
        self.assertIsNone(btg.mark_check("fundamental_book", 125, "long", 100, 100)[0])       # +25%: fine
        self.assertIsNotNone(btg.mark_check("fundamental_book", 131, "long", 100, 100)[0])    # +31% vs last mark
        self.assertIsNotNone(btg.mark_check("fundamental_book", 69, "long", 100, 100)[0])     # -31%
        self.assertIsNone(btg.mark_check("fundamental_book", 140, "long", None, 100)[0])      # no mark: only ADVERSE vs fill
        self.assertIsNotNone(btg.mark_check("fundamental_book", 60, "long", None, 100)[0])
        self.assertIsNotNone(btg.mark_check("fundamental_book", 140, "short", None, 100)[0])
        self.assertIsNone(btg.mark_check("fundamental_spec_book", 60, "long", 100, 100)[0])   # spec: 2.5x only
        self.assertIsNotNone(btg.mark_check("fundamental_spec_book", 39, "long", 100, 100)[0])

    def test_split_like_ratio_is_reported(self):
        why, _, ratio = btg.mark_check("fundamental_book", 3.3533, "long", 51.2, 51.248)
        self.assertEqual(ratio, 15)
        self.assertIn("15-for-1", why)
        self.assertEqual(btg.split_ratio(10, 100), 10)
        self.assertEqual(btg.split_ratio(500, 100), -5)
        self.assertEqual(btg.split_ratio(60, 100), 0)


class MarkerTests(unittest.TestCase):
    def test_split_print_is_not_written_into_the_mark(self):
        before = book()
        after, receipt = ter.value_book("fundamental_book", before, {"8766.T": quote(3.3533, 528.0), "AMD": quote(101.0)}, {}, NOW)
        p = after["positions"]["8766.T"]
        self.assertEqual(p["mark_usd"], 51.2)                         # last good mark carried
        self.assertTrue(p["mark_stale"])
        self.assertEqual(p["mark_rejected"]["split_like_ratio"], 15)
        self.assertIn("8766.T", receipt["unavailable"])
        self.assertEqual(receipt["mark_rejected"][0]["split_like_ratio"], 15)
        self.assertIn("bad tick", after["price_failures"]["8766.T"])
        self.assertEqual(after["positions"]["AMD"]["mark_usd"], 101.0)   # healthy names still mark
        ter.assert_preserved(before, after)

    def test_good_print_clears_a_previous_rejection(self):
        b = book()
        b["positions"]["AMD"]["mark_rejected"] = {"reason": "old"}
        after, _ = ter.value_book("fundamental_book", b, {"8766.T": quote(51.0), "AMD": quote(99.0)}, {}, NOW)
        self.assertNotIn("mark_rejected", after["positions"]["AMD"])
        self.assertEqual(after["positions"]["8766.T"]["mark_usd"], 51.0)

    def test_convexity_guard_is_per_share(self):
        b = {"cash": 0.0, "positions": {"DELL": {"units": 6.44, "premium_usd": 3375.0, "mark_usd": 3494.0, "rail_orig_entry_usd": 524.14}}}
        ok, _ = ter.value_book("convexity_sleeve", b, {"DELL": quote(545.0)}, {}, NOW)
        self.assertAlmostEqual(ok["positions"]["DELL"]["mark_usd"], round(545.0 * 6.44, 2))
        bad, r = ter.value_book("convexity_sleeve", b, {"DELL": quote(54.3)}, {}, NOW)
        self.assertEqual(bad["positions"]["DELL"]["mark_usd"], 3494.0)
        self.assertEqual(r["mark_rejected"][0]["split_like_ratio"], 10)

    def test_holdings_books_unaffected(self):
        b = {"cash": 0.0, "holdings": {"TQQQ": 10}, "mark_px_usd": {"TQQQ": 100.0}}
        after, _ = ter.value_book("gap_close_levered_book", b, {"TQQQ": quote(50.0)}, {}, NOW)
        self.assertEqual(after["mark_px_usd"]["TQQQ"], 50.0)

    def test_request_file_for_corporate_actions(self):
        path = os.path.join(tempfile.mkdtemp(), "req.json")
        self.assertTrue(btg.request_ca_check("fundamental_book", "8766.T", "bad tick", 15, path=path))
        self.assertEqual(json.load(open(path))["8766.T"]["ratio"], 15)


class FlowAdjustedRiskTests(unittest.TestCase):
    def led(self, flows, equity, peak=600000.0, pf=0.0):
        return {"capital_events": [{"type": "capital_transfer", "usd": x} for x in flows],
                "book_risk": {"peak_equity": peak, "peak_net_flow_usd": pf, "kill_drawdown_pct": -18.0, "frozen": False,
                              "drawdown_basis": "flow-adjusted"}}

    def test_inflow_is_not_a_peak_or_a_loss(self):
        b = self.led([464000.0], 600000.0 + 464000.0 - 1000.0)          # HT buys fund +$464k; $1k market loss
        ter._risk_fields(b, 1063000.0, NOW)
        r = b["book_risk"]
        self.assertEqual(r["peak_equity"], 600000.0)                   # raw equity did NOT become the peak
        self.assertEqual(r["peak_net_flow_usd"], 0.0)
        self.assertAlmostEqual(r["drawdown_pct"], -1000 / 600000 * 100, 4)
        self.assertFalse(r["frozen"])
        # later proceeds leave (paired outflow): still no false drawdown
        b["capital_events"].append({"type": "capital_transfer", "usd": -464000.0})
        ter._risk_fields(b, 599000.0, NOW)
        self.assertAlmostEqual(b["book_risk"]["drawdown_pct"], -1000 / 600000 * 100, 4)

    def test_real_gain_resets_peak_with_its_flow_baseline(self):
        b = self.led([100000.0], 710000.0)
        ter._risk_fields(b, 710000.0, NOW)
        self.assertEqual(b["book_risk"]["peak_equity"], 710000.0)
        self.assertEqual(b["book_risk"]["peak_net_flow_usd"], 100000.0)
        self.assertEqual(b["book_risk"]["drawdown_pct"], 0.0)

    def test_real_loss_still_freezes(self):
        b = self.led([], 480000.0)
        ter._risk_fields(b, 480000.0, NOW)
        self.assertTrue(b["book_risk"]["frozen"])

    def test_raw_mode_books_unchanged(self):
        b = {"book_risk": {"peak_equity": 300, "frozen": True, "kill_drawdown_pct": -18}}
        ter._risk_fields(b, 290.0, NOW)
        self.assertEqual(b["book_risk"]["peak_equity"], 300)
        self.assertNotIn("peak_net_flow_usd", b["book_risk"])


class FundamentalBookMarkerTests(unittest.TestCase):
    """fundamental_book._mark_locked: the marker that wrote the 8766.T post-split print (harness: test_fundamental_mark_accounting)."""
    def setUp(self):
        import test_fundamental_mark_accounting as h
        from unittest import mock
        self.h, self.mock = h, mock
        self.case = h.MarkAccounting("test_long_partial_uses_remaining_units_before_risk_and_history")
        self.case.setUp()
        self.addCleanup(self.case.stack.close)
        self.req = os.path.join(tempfile.mkdtemp(), "req.json")
        self.case.stack.enter_context(mock.patch.object(btg, "REQUESTS", self.req))

    def test_split_like_print_rejected_carried_and_requested(self):
        import tfo_corporate_actions as tca
        calls = []
        self.case.stack.enter_context(self.mock.patch.object(tca, "process_book", lambda *a, **k: calls.append(k) or []))
        self.case.setup_main(self.h.position(), 100.0 / 15)
        self.h.main._mark_locked(False)
        p = self.case.ledger["positions"]["TEST"]
        self.assertEqual(p["mark_usd"], 100.0)
        self.assertTrue(p["mark_stale"])
        self.assertEqual(p["mark_rejected"]["split_like_ratio"], 15)
        self.assertEqual(p["units"], 40.0)                                  # no exit on a rejected print
        self.assertEqual(calls[0]["only"], {"TEST"})                        # corporate-actions check ran first (in memory)
        self.assertTrue(calls[0]["splits_only"])
        self.assertEqual(json.load(open(self.req))["TEST"]["ratio"], 15)
        self.assertEqual(self.case.ledger["n_unmarked"], 1)

    def test_split_found_by_corporate_actions_then_print_accepted(self):
        import tfo_corporate_actions as tca
        def fake(book, led, now, fetch, fx, only=None, splits_only=False, closed=True, **k):
            p = led["positions"]["TEST"]
            ca_pre = p["units"]
            p["units"] *= 15; p["entry_px_usd"] /= 15; p["mark_usd"] /= 15; p["mark"] /= 15
            return [{"type": "split", "book": book, "sym": "TEST", "ex_date": "2026-09-29", "ratio": 15, "split_ratio": "15:1",
                     "units": p["units"], "entry_px_usd": p["entry_px_usd"], "pre_units": ca_pre, "mark_rebased": True, "url": "u",
                     "utc": "x", "tag": "CA-SPLIT-test"}]
        recorded = []
        self.case.stack.enter_context(self.mock.patch.object(tca, "process_book", fake))
        self.case.stack.enter_context(self.mock.patch.object(tca, "record_actions", lambda acts, *a, **k: recorded.extend(acts)))
        self.case.setup_main(self.h.position(), 100.0 / 15)
        self.h.main._mark_locked(False)
        p = self.case.ledger["positions"]["TEST"]
        self.assertAlmostEqual(p["mark_usd"], 100.0 / 15)
        self.assertNotIn("mark_rejected", p)
        self.assertAlmostEqual(p["units"], 600.0)
        self.assertEqual([a["type"] for a in recorded], ["split"])


if __name__ == "__main__":
    unittest.main()
