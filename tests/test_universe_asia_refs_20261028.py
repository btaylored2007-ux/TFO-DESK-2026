import os, sys, unittest, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import universe_asia as U
def frame(cols, idx):
    return pd.concat({f: pd.DataFrame({c: (np.nan if c == "^X" else 1.0) for c in cols}, index=idx) for f in ("Close","High","Low","Open","Volume")}, axis=1)
class Refs(unittest.TestCase):
    idx = pd.date_range("2026-01-01", periods=5)
    def test_refill(self):
        d = frame(["A", "^X"], self.idx); one = frame(["Z"], self.idx).xs("Z", axis=1, level=1)
        d = U.ensure_refs(d, ["^X", "FX=X"], fetch=lambda s: one)
        self.assertEqual(int(d["Close"]["^X"].notna().sum()), 5); self.assertEqual(int(d["Close"]["FX=X"].notna().sum()), 5)
    def test_fail_closed(self):
        d = frame(["A", "^X"], self.idx)
        with self.assertRaises(RuntimeError): U.ensure_refs(d, ["^X"], fetch=lambda s: pd.DataFrame())
if __name__ == "__main__": unittest.main()
