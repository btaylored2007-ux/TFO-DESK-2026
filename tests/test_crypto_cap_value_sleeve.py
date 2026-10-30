"""2026-10-02 Brett: crypto stack cap $1M, desk invested cap 100% (no leverage, $50k reserve kept), value+prof sleeve 20% (staged)."""
import json, os, sys, tempfile, unittest
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import desk_caps
import value_prof_sleeve as vps


class Caps(unittest.TestCase):
    def test_values(self):
        self.assertEqual(desk_caps.INVESTED_CAP_PCT, 1.00)
        self.assertEqual(desk_caps.CASH_RESERVE_USD, 50000.0)
        self.assertEqual(desk_caps.CRYPTO_STACK_CAP_USD, 1000000.0)
        self.assertEqual(desk_caps.VALUE_PROF_SLEEVE_PCT, 0.20)

    def test_tfo_crypto_uses_1m_stack(self):
        import tfo_crypto
        self.assertEqual(tfo_crypto.STACK_CAP, 1000000.0)
        self.assertEqual(tfo_crypto.STACK_CAP, desk_caps.CRYPTO_STACK_CAP_USD)
        self.assertEqual(tfo_crypto.CLIP_CAP, 300000.0)          # unchanged
        self.assertEqual(tfo_crypto.C3_MAX_RISK_PCT, 0.01)       # unchanged
        self.assertEqual(tfo_crypto.CRYPTO_EXIT_VARIANT, "C3")   # unchanged

    def test_closed_names_unchanged(self):
        from tfo_ticket import CLOSED_NAMES
        for n in ("NEAR", "SUI", "ZEC", "TAO", "HYPE", "AVAX", "XRP", "SOXL", "UNI_SUI", "IAG", "RCL"):
            self.assertIn(n, CLOSED_NAMES)

    def test_room_never_leverage(self):
        # fully invested firm: no room even though cap is 100%
        self.assertEqual(desk_caps.invested_room(1.8e6, 1.8e6, 0.0), 0.0)
        # cash minus reserve binds before the 100% cap
        self.assertAlmostEqual(desk_caps.invested_room(1.8e6, 1.4e6, 400000.0), 350000.0)
        # cap above 100% is clamped (no leverage)
        self.assertAlmostEqual(desk_caps.invested_room(1e6, 0.0, 1e6, cap_pct=1.5), 950000.0)
        # old 80% cap for comparison
        self.assertAlmostEqual(desk_caps.invested_room(1.8e6, 1.4e6, 400000.0, cap_pct=0.80), 40000.0)

    def test_firm_exposure_reads_books(self):
        d = tempfile.mkdtemp()
        json.dump({"utc": "t", "firm_equity_usd": 1000.0, "book_equity_usd": {"a": 600.0, "b": 300.0, "c": 100.0}, "level": "OK"}, open(os.path.join(d, "firm_risk.json"), "w"))
        json.dump({"cash": 100.0}, open(os.path.join(d, "a.json"), "w"))
        json.dump({"cash": 300.0}, open(os.path.join(d, "b.json"), "w"))
        json.dump({"marks": {}}, open(os.path.join(d, "c.json"), "w"))   # no cash field -> counted invested
        x = desk_caps.firm_exposure(d, use_registry=False)
        self.assertEqual(x["firm_cash_usd"], 400.0); self.assertEqual(x["invested_usd"], 600.0); self.assertEqual(x["invested_pct"], 60.0)
        self.assertIn("c", x["books_without_cash_field_counted_invested"])
        self.assertEqual(x["invested_room_usd"], 0.0)   # cash 400 - reserve 50k < 0


class Sleeve(unittest.TestCase):
    def test_sizing_target_binds(self):
        s = vps.sleeve_dollars(1.8e6, 1.0e6, 800000.0, 800000.0)
        self.assertEqual(s["target_usd"], 360000.0); self.assertEqual(s["move_usd"], 360000.0); self.assertEqual(s["binding"], "target")

    def test_sizing_reserve_binds(self):
        s = vps.sleeve_dollars(1.8e6, 1.4e6, 400000.0, 370000.0)
        self.assertEqual(s["move_usd"], 320000.0); self.assertEqual(s["binding"], "room/powder-reserve")

    def test_sizing_trim_and_no_negative_buy(self):
        self.assertEqual(vps.sleeve_dollars(1.8e6, 1.8e6, 0.0, 0.0)["move_usd"], 0.0)
        self.assertEqual(vps.sleeve_dollars(1.0e6, 0.9e6, 100000.0, 100000.0, sleeve_value=250000.0)["move_usd"], -50000.0)

    def test_cap_weights(self):
        import pandas as pd
        w = vps.cap_weights(pd.Series([0.5, 0.2, 0.1, 0.1, 0.1]), 0.25)
        self.assertAlmostEqual(w.sum(), 1.0); self.assertLessEqual(w.max(), 0.25 + 1e-9)

    def test_overlays_exclude_closed_and_held(self):
        ex = vps.overlays(["RCL", "ZZZZ"])
        self.assertIn("RCL", ex); self.assertNotIn("ZZZZ", ex)

    def test_no_ledger_keys(self):
        with self.assertRaises(ValueError): vps._guard_no_ledger_keys({"cash": 1})
        vps._guard_no_ledger_keys({"sizing": {}, "targets": []})

    def test_schedule(self):
        import datetime as dt
        s = vps.schedule(dt.date(2026, 10, 2))
        self.assertEqual(s["next_signal_us_close"], "2026-10-30"); self.assertEqual(s["next_trade_us_close"], "2026-11-02")

    def test_no_execute_path(self):
        src = open(vps.__file__).read()
        self.assertNotIn("--execute", src.split("def main")[1])
        self.assertEqual(vps.STATUS, "STAGED_TARGET_ONLY")


if __name__ == "__main__":
    unittest.main()
