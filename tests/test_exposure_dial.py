"""Exposure dial (spec 21e05244c53d4e77) — the cap-form math and the gate's
fail-closed behaviour. No network, no ledger writes."""
import json
import os
import sys
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import exposure_dial as ed  # noqa: E402
import autonomous_deploy as ad  # noqa: E402


class TestDialMath(unittest.TestCase):
    def test_cap_form_never_levers_up(self):
        # calm regime: vol below target -> exactly 1.0, never above
        for vol in (0.01, 0.05, 0.0999, 0.10):
            self.assertEqual(ed.scale_for(vol), 1.0)

    def test_throttles_proportionally_above_target(self):
        self.assertAlmostEqual(ed.scale_for(0.20), 0.5)
        self.assertAlmostEqual(ed.scale_for(0.40), 0.25)

    def test_measured_zero_is_full_but_unknown_risk_is_closed(self):
        self.assertEqual(ed.scale_for(0.0), 1.0)
        for bad in (-1.0, None, float("nan"), float("inf")):
            self.assertEqual(ed.scale_for(bad), 0.0)


class TestGateFailClosed(unittest.TestCase):
    """_dial_ok() against a scratch dial file — never touches the real one."""

    def setUp(self):
        self._orig = ad.DIAL_FILE
        self.tmp = os.path.join(BASE, "reports", ".test_dial.json")

    def tearDown(self):
        ad.DIAL_FILE = self._orig
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def _write(self, scale, age_sec=0):
        with open(self.tmp, "w") as f:
            json.dump({"scale": scale}, f)
        if age_sec:
            t = time.time() - age_sec
            os.utime(self.tmp, (t, t))
        ad.DIAL_FILE = self.tmp

    def test_open_at_full(self):
        self._write(1.0)
        ok, _ = ad._dial_ok()
        self.assertTrue(ok)

    def test_blocks_when_throttled(self):
        self._write(0.79)
        ok, why = ad._dial_ok()
        self.assertFalse(ok)
        self.assertIn("THROTTLED", why)

    def test_blocks_when_stale(self):
        self._write(1.0, age_sec=49 * 3600)
        ok, why = ad._dial_ok()
        self.assertFalse(ok)
        self.assertIn("STALE", why)

    def test_blocks_when_missing(self):
        ad.DIAL_FILE = os.path.join(BASE, "reports", ".no_such_dial.json")
        ok, why = ad._dial_ok()
        self.assertFalse(ok)
        self.assertIn("fail closed", why)

    def test_blocks_when_degraded_even_at_full_scale(self):
        # audit hardening: soft failure (partial data) must never open the gate
        with open(self.tmp, "w") as f:
            json.dump({"scale": 1.0, "degraded": True}, f)
        ad.DIAL_FILE = self.tmp
        ok, why = ad._dial_ok()
        self.assertFalse(ok)
        self.assertIn("DEGRADED", why)

    def test_blocks_scale_outside_registered_range(self):
        # NaN, inf, 0, negative, >1: all outside (0, 1] -> fail closed
        for bad in (float("nan"), float("inf"), 0.0, -0.5, 1.5):
            self._write(bad)
            ok, why = ad._dial_ok()
            self.assertFalse(ok, "scale %r must fail closed" % bad)


if __name__ == "__main__":
    unittest.main()
