"""17 Oct 2026 (Brett 10:29 AEDT, weekly cut review): OXL2 carry cut - no new OXL2 entries; open OXL2 trades still managed. No network."""
import json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_oanda_widen_caps_20261012 import Base, T, CS


class OXL2Cut(Base):
    def cut(self): json.dump({"entry_disabled_designs": ["OXL2"]}, open(self.m.CFG, "w"))

    def test_live_config_disables_oxl2_only(self):
        c = json.load(open(os.path.join(ROOT, "state", "oanda", "config.json")))
        self.assertEqual(c["entry_disabled_designs"], ["OXL2"]); self.assertIn("OXL2", c["designs"])   # kept in designs so exits still run

    def test_place_entry_refuses_queued_oxl2(self):
        self.cut(); self.assertEqual(self.m.place_entry(self.entry("EUR_USD"), live=True), "skip:design_entries_disabled")
        self.assertEqual(self.lines(), [])   # nothing sent, no API call

    def test_other_designs_still_enter(self):
        self.cut(); e = dict(self.entry("EUR_USD"), design="OXF1")
        self.assertEqual(self.m.place_entry(e, live=False), "dry")

    def _daily(self, plan, score=0.02):
        m = self.m; self.open = [T(61663, "GBP_CHF", 11752, 1.09915, 1.09211)]
        m.reconcile_stop_fills = lambda: []; m.reconcile_stops = lambda *a: []
        m.snapshot = lambda extra=None: ({"nav": 50000.0, "tfo_upl_aud": 0, "tfo_realized_aud": 0}, list(self.open))
        m.candles = lambda ins, n=320: CS * 30
        m.designs.signals = lambda names, data: list(plan)
        m.designs.score_for = lambda design, data, ins, *a: score
        m.designs.LIVE_FIN = {}; closed = []
        m.close_trade = lambda t, reason, live: closed.append(reason)
        m.set_stop = lambda *a, **k: True; m.run_queue = lambda *a, **k: None
        m.daily(live=False); return closed

    def test_daily_queues_no_oxl2_and_logs(self):
        self.cut()
        self._daily([dict(design="OXL2", ins="EUR_USD", dir=1, score=0.03, risk_pct=0.005, reason="carry")])
        ev = [l for l in self.lines() if l.get("event") == "design_disabled_skip"]
        self.assertEqual([(l["design"], l["instrument"]) for l in ev], [("OXL2", "EUR_USD")])
        pend = os.path.join(self.m.ST, "pending_dry.json")
        q = json.load(open(pend)).get("queue", []) if os.path.exists(pend) else []
        self.assertFalse(any(e.get("design") == "OXL2" for e in q))

    def test_open_oxl2_trade_still_exits_on_signal(self):
        self.cut(); self.assertEqual(len(self._daily([], score=-0.02)), 1)


if __name__ == "__main__":
    unittest.main()
