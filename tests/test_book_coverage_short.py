import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import book_coverage as coverage


class ShortPilotCoverageTests(unittest.TestCase):
    now = datetime(2026, 9, 20, 8, tzinfo=timezone.utc).timestamp()

    def payload(self):
        # $100k funding, $50 opening fee, $100k collateral, $1k price gain,
        # $10 funding paid and $49.50 estimated cover fee.
        return dict(paper_only=True, side="short", symbol="PF_SOLUSD",
                    quantity=1000., entry_price=100., mark_price=99., cash=50.,
                    collateral_usd=100000., equity=100990.50,
                    seed_usd=100100., start=100100.,
                    unrealised_usd=1000., realised_usd=0., costs_paid_usd=50.,
                    funding_accrued_usd=-10., funding_total_usd=-10.,
                    realised_funding_usd=0., fee_bps=5., estimated_exit_fee_usd=49.50,
                    mark_utc="2026-09-20T07:59:30Z")

    def load(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.json"
            path.write_text(json.dumps(payload))
            return coverage._load_short_pilot("pilot", "Pilot", str(path), now=self.now)

    def test_absent_optional_but_present_unreadable_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.json"
            self.assertIsNone(coverage._load_short_pilot("pilot", "Pilot", str(path)))
            path.write_text("{broken")
            got = coverage._load_short_pilot("pilot", "Pilot", str(path))
            self.assertIn("UNREADABLE", got["error"])
            self.assertIsNone(got["equity_usd"])
        for payload in (None, [], {}):
            self.assertIn("UNREADABLE", self.load(payload)["error"])

    def test_signed_exact_position_and_net_equity(self):
        got = self.load(self.payload())
        self.assertIsNone(got["error"])
        self.assertEqual(got["equity_usd"], 100990.50)
        self.assertEqual(got["cash_usd"], 50.)
        self.assertEqual(got["start_usd"], 100100.)
        self.assertEqual(got["mark_age_sec"], 30.)
        self.assertFalse(got["stale"])
        self.assertTrue(got["simulated_only"])
        self.assertIs(got["verified_account_access"], False)
        pos = got["positions"][0]
        self.assertEqual((pos["sym"], pos["ticker"], pos["side"], pos["sign"]),
                         ("PF_SOLUSD", "KRK:SOL", "short", -1.))
        self.assertEqual(pos["fallback_usd"], -99000.)

    def test_all_economic_identities_are_checked(self):
        for field in ("cash", "collateral_usd", "equity", "start", "seed_usd",
                      "unrealised_usd", "estimated_exit_fee_usd", "funding_accrued_usd",
                      "funding_total_usd", "realised_funding_usd", "costs_paid_usd"):
            with self.subTest(field=field):
                data = self.payload()
                data[field] += 1
                got = self.load(data)
                self.assertIn("UNREADABLE", got["error"])
                self.assertIsNone(got["equity_usd"])

    def test_nonfinite_boolean_and_missing_economics_rejected(self):
        for field in ("quantity", "mark_price", "entry_price", "seed_usd", "cash", "fee_bps"):
            for value in (float("nan"), float("inf"), True, None):
                with self.subTest(field=field, value=value):
                    self.assertIn("UNREADABLE", self.load({**self.payload(), field: value})["error"])
        for field in ("mark_price", "entry_price"):
            self.assertIn("UNREADABLE", self.load({**self.payload(), field: 0})["error"])

    def test_zero_negative_seed_survives_full_bank_without_profit_erasure(self):
        for seed, realised in ((0., 100.), (-500., 600.), (500., -400.)):
            with self.subTest(seed=seed):
                data = self.payload()
                data.update(quantity=0., cash=0., collateral_usd=0., equity=0.,
                            seed_usd=seed, start=seed, unrealised_usd=0.,
                            realised_usd=realised, costs_paid_usd=100.,
                            funding_accrued_usd=0., funding_total_usd=0.,
                            realised_funding_usd=0., estimated_exit_fee_usd=0.)
                got = self.load(data)
                self.assertIsNone(got["error"])
                self.assertEqual(got["start_usd"], seed)
                self.assertEqual(got["positions"], [])

    def test_mark_age_boundary_future_and_missing_timestamp(self):
        for stamp, stale in (("2026-09-20T07:30:00Z", False),
                             ("2026-09-20T07:29:59Z", True),
                             ("2026-09-20T08:00:01Z", True)):
            got = self.load({**self.payload(), "mark_utc": stamp})
            self.assertIsNone(got["error"])
            self.assertEqual(got["stale"], stale)
        for stamp in (None, "bad", "2026-09-20T08:00:00"):
            self.assertIn("UNREADABLE", self.load({**self.payload(), "mark_utc": stamp})["error"])

    def test_wrong_instrument_side_and_nonpaper_fail(self):
        for change in ({"symbol": "SOL/USD"}, {"symbol": "PF_ETHUSD"},
                       {"side": "long"}, {"paper_only": False}):
            self.assertIn("UNREADABLE", self.load({**self.payload(), **change})["error"])


if __name__ == "__main__":
    unittest.main()
