"""Isolated quote provenance and mark-only accounting regressions."""
import contextlib
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tfo_equity_refresh as refresh

NOW = "2026-09-21T10:40:00Z"
EPOCH = dt.datetime.fromisoformat(NOW.replace("Z", "+00:00")).timestamp()


def quote(price=12, ccy="USD", executable=True):
    return {"px": price, "ccy": ccy, "px_usd": price, "fx_usd": 1.0,
            "quote_utc": "2026-09-21T10:39:00Z", "fetched_at": NOW,
            "session": "regular" if executable else "pre", "status": "regular_current" if executable else "pre_current",
            "executable": executable, "quote_current": True, "source": "test",
            "fx_status": "identity", "url": "https://example.test/quote"}


def chart(regular_price=10, regular_epoch=EPOCH-60, ccy="USD", session="regular", bars=()):
    periods = {name: {"start": 0, "end": 0} for name in ("pre", "regular", "post")}
    periods[session] = {"start": EPOCH-1000, "end": EPOCH+1000}
    return {"chart": {"result": [{"meta": {"regularMarketPrice": regular_price,
            "regularMarketTime": regular_epoch, "currency": ccy, "currentTradingPeriod": periods},
            "timestamp": [x[0] for x in bars],
            "indicators": {"quote": [{"close": [x[1] for x in bars]}]}}]}}


class ProvenanceTests(unittest.TestCase):
    def test_premarket_bar_is_newer_valuation_but_never_executable(self):
        raw = chart(regular_epoch=EPOCH-3*86400, session="pre", bars=[(EPOCH-60, 12)])
        q = refresh.parse_chart("TEST", raw, NOW)
        self.assertEqual(q["px"], 12)
        self.assertEqual(q["regular"]["px"], 10)
        self.assertEqual(q["quote_utc"], "2026-09-21T10:39:00Z")
        self.assertEqual(q["fetched_at"], NOW)
        self.assertTrue(q["quote_current"])
        self.assertFalse(q["executable"])
        self.assertEqual(q["status"], "pre_current")

    def test_delayed_open_market_retains_timestamp_and_blocks_execution(self):
        q = refresh.parse_chart("TEST.L", chart(regular_epoch=EPOCH-900, ccy="GBp"), NOW)
        self.assertEqual(q["ccy"], "GBp")
        self.assertEqual(q["age_sec"], 900)
        self.assertEqual(q["status"], "regular_delayed")
        self.assertFalse(q["executable"])

    def test_old_price_with_new_fetch_never_becomes_current(self):
        q = refresh.parse_chart("TEST", chart(regular_epoch=EPOCH-86400, session="pre"), NOW)
        self.assertFalse(q["quote_current"])
        self.assertEqual(q["quote_utc"], "2026-09-20T10:40:00Z")

    def test_nan_missing_currency_or_future_quote_rejected(self):
        for raw in (chart(regular_price=float("nan")), chart(ccy=None),
                    chart(regular_epoch=EPOCH+600), chart(regular_epoch=None)):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                refresh.parse_chart("TEST", raw, NOW)

    def test_invalid_or_future_extended_bar_cannot_replace_regular(self):
        q = refresh.parse_chart("TEST", chart(session="pre", bars=[(EPOCH-10, None), (EPOCH+600, 100)]), NOW)
        self.assertEqual(q["px"], 10)
        self.assertIsNone(q["extended"])


def session_chart(bar_age, symbol_ccy="AUD", session="regular", reg_start_age=4*3600, reg_end_in=2*3600):
    """Regular session that opened reg_start_age ago; one 1m bar bar_age seconds old."""
    periods = {name: {"start": 0, "end": 0} for name in ("pre", "regular", "post")}
    periods["regular"] = {"start": EPOCH - reg_start_age, "end": EPOCH + reg_end_in}
    if session != "regular":  # market shut now: regular period was earlier
        periods["regular"] = {"start": EPOCH - reg_start_age, "end": EPOCH - 3600}
    return {"chart": {"result": [{"meta": {"regularMarketPrice": 10, "regularMarketTime": EPOCH - bar_age,
            "currency": symbol_ccy, "currentTradingPeriod": periods},
            "timestamp": [EPOCH - bar_age], "indicators": {"quote": [{"close": [10.5]}]}}]}}


class DelayedVenueFreshnessTests(unittest.TestCase):
    """2026-09-28 mandate: current-session delayed prints on non-US venues are DELAYED PAPER up to 45 min."""
    def test_asx_bar_25min_old_in_current_session_is_delayed_paper(self):
        q = refresh.parse_chart("STO.AX", session_chart(1500), NOW)
        self.assertTrue(q["paper_executable"])
        self.assertEqual(q["paper_price_class"], "DELAYED PAPER")
        self.assertFalse(q["executable"])
        self.assertEqual(q["px"], 10.5)

    def test_other_delayed_suffixes_share_the_window(self):
        for sym in ("BITTI.HE", "WPP.L", "SAP.DE", "1093.HK", "6902.T", "BAKKA.OL", "ENEA.ST", "TD.TO"):
            self.assertTrue(refresh.parse_chart(sym, session_chart(2600), NOW)["paper_executable"], sym)

    def test_delayed_venue_bar_older_than_45min_rejected(self):
        self.assertFalse(refresh.parse_chart("STO.AX", session_chart(2800), NOW)["paper_executable"])

    def test_us_window_not_loosened(self):
        q = refresh.parse_chart("AAPL", session_chart(1500, "USD"), NOW)
        self.assertFalse(q["paper_executable"])
        self.assertFalse(q["executable"])
        self.assertTrue(refresh.parse_chart("AAPL", session_chart(900, "USD"), NOW)["paper_executable"])

    def test_prior_session_or_shut_print_rejected(self):
        q = refresh.parse_chart("STO.AX", session_chart(1500, session="closed"), NOW)
        self.assertEqual(q["session"], "closed")
        self.assertFalse(q["paper_executable"])
        # bar before today's regular open (prior session) is never a paper print
        q = refresh.parse_chart("STO.AX", session_chart(1500, reg_start_age=600), NOW)
        self.assertFalse(q["paper_executable"])

    def test_fx_and_index_symbols_keep_default_window(self):
        self.assertEqual(refresh.paper_bar_max_age("AUDUSD=X"), 1200)
        self.assertEqual(refresh.paper_bar_max_age("^AXJO"), 1200)
        self.assertEqual(refresh.paper_bar_max_age("CBA.AX"), 2700)


class AccountingTests(unittest.TestCase):
    def fundamental(self):
        return {"cash": 50.0, "seed_usd": 250.0, "realised_usd": 0,
                "costs_paid_usd": 0, "closed": {"OLD": {"entry_px": 999}},
                "trade_fills_this_tick": [{"keep": "historic"}],
                "book_risk": {"peak_equity": 300, "frozen": True, "kill_drawdown_pct": -18},
                "positions": {
                    "LONG": {"side": "long", "units": 10, "notional_usd": 100, "entry_px_usd": 10, "entry_utc": "old", "mark_usd": 10},
                    "SHORT": {"side": "short", "units": 5, "notional_usd": 100, "entry_px_usd": 20, "mark_usd": 20}}}

    def test_long_short_collateral_full_total_and_protected_history(self):
        before = self.fundamental()
        after, receipt = refresh.value_book("fundamental_book", before,
                                            {"LONG": quote(12), "SHORT": quote(16)}, {}, NOW)
        # 50 cash + 120 long + 100 short collateral + 20 short gain.
        self.assertEqual(after["equity"], 290)
        self.assertEqual(after["unrealised_usd"], 40)
        self.assertEqual(after["equity_identity_gap_usd"], 0)
        self.assertEqual(after["positions"]["SHORT"]["unrealised_usd"], 20)
        self.assertEqual(after["positions"]["LONG"]["mark_utc"], "2026-09-21T10:39:00Z")
        self.assertEqual(after["positions"]["LONG"]["mark_checked_utc"], NOW)
        self.assertEqual(after["book_risk"]["peak_equity"], 300)
        self.assertTrue(after["book_risk"]["frozen"])
        self.assertEqual(before["positions"]["LONG"]["mark_usd"], 10)
        refresh.assert_preserved(before, after)

    def test_fundamental_identity_matches_canonical_writer_borrow_addback(self):
        # 2026-09-28: fundamental_book.mark adds back borrow_released_cash_correct_usd; the mark refresh must too.
        book = {"cash": 1000.0, "seed_usd": 1000.0, "realised_usd": 10.0, "costs_paid_usd": 16.3, "cash_interest_accrued": 0.0,
                "positions": {"L": {"side": "long", "units": 1.0, "entry_px_usd": 100.0, "notional_usd": 100.0, "mark_usd": 100.0}},
                "closed": {"S": {"side": "short", "units": 0.0, "borrow_released_cash_correct_usd": 6.3}}}
        book["cash"] = 1000.0 + 10.0 + 6.3 - 16.3 - 100.0          # cash consistent with the identity terms
        q = {"L": {"px": 101.0, "px_usd": 101.0, "fx_usd": 1.0, "ccy": "USD", "quote_utc": "2026-09-28T07:00:00Z", "session": "regular",
                   "quote_current": True, "executable": True, "status": "regular_current"}}
        out, _ = refresh.value_book("fundamental_book", book, q, {}, "2026-09-28T07:00:05Z")
        self.assertAlmostEqual(out["equity_identity_gap_usd"], 0.0, places=2)
        self.assertAlmostEqual(out["equity_identity_usd"], 1000 + 10 + 6.3 - 16.3 + 1.0, places=2)

    def test_convexity_mark_is_total_position_value(self):
        before = {"cash": 50, "positions": {"A": {"units": 5, "premium_usd": 40,
                  "mark_usd": 40, "entry_px": 8, "mark_ticker": "A"}}}
        # 2026-09-29: 8 -> 9 per unit (+12.5%); the old 8 -> 12 (+50%) fixture is now a bad tick (>30% guard)
        after, _ = refresh.value_book("convexity_sleeve", before, {"A": quote(9, executable=False)}, {}, NOW)
        self.assertEqual(after["positions"]["A"]["mark_usd"], 45)
        self.assertEqual(after["equity"], 95)
        self.assertEqual(after["unrealised_usd"], 5)
        self.assertTrue(after["positions"]["A"]["mark_stale"])
        self.assertTrue(after["positions"]["A"]["mark_quote_current"])

    def test_missing_quote_retains_last_good_and_marks_unavailable(self):
        before = self.fundamental()
        after, receipt = refresh.value_book("fundamental_book", before, {"LONG": quote(12)}, {"SHORT": "offline"}, NOW)
        self.assertEqual(after["positions"]["SHORT"]["mark_usd"], 20)
        self.assertTrue(after["positions"]["SHORT"]["mark_stale"])
        self.assertEqual(after["equity"], 270)
        self.assertEqual(receipt["unavailable"], ["SHORT"])

    def test_spec_book_recalculates_risk_without_lowering_peak_or_unfreezing(self):
        before = {"cash": 5, "positions": {"A": {"units": 10, "notional_usd": 100, "entry_px_usd": 10, "mark_usd": 10}},
                  "sleeve_risk": {"peak_equity": 200, "new_entries_frozen": True, "kill_drawdown_pct": -40}}
        after, _ = refresh.value_book("fundamental_spec_book", before, {"A": quote(12)}, {}, NOW)
        self.assertEqual(after["equity"], 125)
        self.assertEqual(after["sleeve_risk"]["drawdown_pct"], -37.5)
        self.assertTrue(after["sleeve_risk"]["new_entries_frozen"])
        self.assertEqual(after["sleeve_risk"]["peak_equity"], 200)

    def test_spec_partial_sale_preserves_realised_cash_and_remaining_cost_identity(self):
        # Original cost is $2,500. Released principal is already in cash and
        # must not be subtracted again from the remaining holding's value.
        for released in (0.33, 0.66):
            for current_quote in (True, False):
                with self.subTest(released=released, current_quote=current_quote):
                    units = 100 * (1 - released)
                    before = {
                        "seed_usd": 2500, "cash": 2500 * released + 100,
                        "realised_usd": 100, "costs_paid_usd": 0,
                        "capital_events": [{"type": "historic"}],
                        "trade_fills_this_tick": [{"type": "historic"}],
                        "positions": {"A": {
                            "side": "long", "units": units, "notional_usd": 2500,
                            "entry_px_usd": 25, "mark_usd": 30,
                            "exit_state": {"released": released, "orig_units": 100}}}}
                    original = copy.deepcopy(before)
                    quotes = {"A": quote(30)} if current_quote else {}
                    failures = {} if current_quote else {"A": "offline"}
                    after, receipt = refresh.value_book(
                        "fundamental_spec_book", before, quotes, failures, NOW)
                    expected_unrealised = round(units * 5, 2)
                    self.assertEqual(after["unrealised_usd"], expected_unrealised)
                    self.assertEqual(after["positions"]["A"]["unrealised_usd"], expected_unrealised)
                    self.assertEqual(after["equity"], 2600 + expected_unrealised)
                    self.assertEqual(receipt["unavailable"], [] if current_quote else ["A"])
                    self.assertEqual(after["positions"]["A"]["exit_state"], before["positions"]["A"]["exit_state"])
                    refresh.assert_preserved(before, after)
                    self.assertEqual(before, original)

    def test_flat_value_equity_equals_existing_cash_without_creating_profit_flow(self):
        before = {"cash": 40177.95, "equity": 40169.26, "holdings": {}, "costs_paid": 50,
                  "cash_interest_accrued": 27.62, "peak_equity": 50129.0}
        after, receipt = refresh.value_book("value_book", before, {}, {}, NOW)
        self.assertEqual(after["equity"], before["cash"])
        self.assertTrue(receipt["flat_cash_equity_repair"])
        refresh.assert_preserved(before, after)

    def test_soxl_extended_value_updates_without_entry_or_units_change(self):
        before = {"cash": 0, "seed_usd": 75000, "entry_px": {"SOXL": 123.67},
                  "holdings": {"SOXL": 600}, "deploy_basis_usd": 75000, "equity": 74202}
        after, _ = refresh.value_book("soxl_weekend_probe", before, {"SOXL": quote(132, executable=False)}, {}, NOW)
        self.assertEqual(after["equity"], 79200)
        self.assertTrue(after["mark_stale"])
        self.assertEqual(after["last_mark_utc"], "2026-09-21T10:39:00Z")
        refresh.assert_preserved(before, after)

    def test_economic_invariant_guard_detects_changed_units_or_cash(self):
        before = self.fundamental()
        for key in ("cash", "units"):
            after = copy.deepcopy(before)
            if key == "cash":
                after["cash"] += 1
            else:
                after["positions"]["SHORT"]["units"] += 1
            with self.subTest(key=key), self.assertRaises(AssertionError):
                refresh.assert_preserved(before, after)

    def test_backup_is_exact_and_report_records_applied_valuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = json.dumps(self.fundamental()).encode()
            path = root / "fundamental_book.json"
            path.write_bytes(original)
            with mock.patch.object(refresh, "writer_locks", return_value=contextlib.nullcontext()):
                result = refresh.refresh(True, root, lambda symbol: quote(12 if symbol == "LONG" else 16),
                                         lambda currencies: ({"USD": 1}, {}))
            self.assertTrue(result["writes_applied"])
            self.assertEqual((Path(result["backup"]) / path.name).read_bytes(), original)
            self.assertEqual(json.loads(path.read_text())["equity"], 290)
            self.assertTrue((root / "price_refresh_equities_latest.json").exists())

    def test_existing_tick_lock_refuses_without_touching_ledgers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tick.lock").mkdir()
            with self.assertRaisesRegex(RuntimeError, "desk tick"):
                with refresh.writer_locks(root):
                    self.fail("must not enter")
            self.assertTrue((root / ".tick.lock").exists())


if __name__ == "__main__":
    unittest.main()
