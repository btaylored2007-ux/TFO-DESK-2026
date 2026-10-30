#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import short_expression_shadow as shadow  # noqa: E402


class TestShortExpressionShadow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "floor.json")
        self.state = os.path.join(self.tmp.name, "shadow.json")
        self.payload = {"desks": {"Test": {"closed_trades": [
            {"t": "before", "sym": "A/USD", "side": "short", "pnl": -50.0}
        ]}}}
        self._write()
        self.sources = {"floor": self.source}

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self):
        with open(self.source, "w") as target:
            json.dump(self.payload, target)

    def test_history_is_quarantined_and_future_short_is_idempotent(self):
        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(additions, [])
        self.assertEqual(state["pre_start_context"]["n_shorts"], 1)
        self.assertEqual(state["forward_stats"]["n_shorts"], 0)

        self.payload["desks"]["Test"]["closed_trades"].append(
            {"t": "after", "sym": "B/USD", "side": "short", "pnl": -100.0})
        self._write()
        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(len(additions), 1)
        self.assertEqual(state["forward_stats"]["short_actual_pnl_usd"], -100.0)
        self.assertEqual(state["forward_stats"]["cash_minus_short_actual_usd"], 100.0)

        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(additions, [])
        self.assertEqual(state["forward_stats"]["n_shorts"], 1)

    def test_invalid_source_does_not_replace_last_good_state(self):
        shadow.run(self.sources, self.state)
        with open(self.state, "rb") as source:
            before = source.read()
        self.payload = {"desks": {"Test": {"closed_trades": "bad"}}}
        self._write()
        with self.assertRaises(ValueError):
            shadow.run(self.sources, self.state)
        with open(self.state, "rb") as source:
            self.assertEqual(source.read(), before)

    def test_nonfinite_pnl_is_quarantined_without_replacing_state(self):
        shadow.run(self.sources, self.state)
        with open(self.state, "rb") as source:
            before = source.read()
        for pnl in (float("nan"), float("inf"), float("-inf"), "NaN"):
            with self.subTest(pnl=pnl):
                self.payload["desks"]["Test"]["closed_trades"][0]["pnl"] = pnl
                self._write()
                with self.assertRaisesRegex(ValueError, "non-finite pnl"):
                    shadow.run(self.sources, self.state)
                with open(self.state, "rb") as source:
                    self.assertEqual(source.read(), before)

    def test_nonfinite_pnl_in_persisted_forward_state_is_rejected(self):
        state, _ = shadow.run(self.sources, self.state)
        state["forward"] = [{"side": "short", "actual_pnl_usd": float("nan"),
                             "id": "poisoned-state"}]
        with open(self.state, "w") as target:
            json.dump(state, target)
        with self.assertRaisesRegex(ValueError, "non-finite actual_pnl_usd"):
            shadow.run(self.sources, self.state)

    def test_identity_survives_reordering_and_front_insertion(self):
        self.payload["desks"]["Test"]["closed_trades"].append(
            {"t": "before-2", "sym": "B/USD", "side": "long", "pnl": 25.0})
        self._write()
        shadow.run(self.sources, self.state)

        trades = self.payload["desks"]["Test"]["closed_trades"]
        trades.reverse()
        self._write()
        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(additions, [])
        self.assertEqual(state["forward_stats"]["n_all"], 0)

        trades.insert(0, {"t": "after", "sym": "C/USD", "side": "short", "pnl": 10.0})
        self._write()
        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(len(additions), 1)
        stable_id = additions[0]["id"]

        trades[:] = [trades[2], trades[0], trades[1]]
        self._write()
        state, additions = shadow.run(self.sources, self.state)
        self.assertEqual(additions, [])
        self.assertEqual(state["forward"][0]["id"], stable_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
