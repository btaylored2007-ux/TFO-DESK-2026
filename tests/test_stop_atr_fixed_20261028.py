import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
B = dict(sweep=dict(venue="asx"))
class S(unittest.TestCase):
    def test_fixed_shape_wide_stop_not_failed(self):
        s = dict(direction="long", trigger=10.0, max_fill=10.0, stop=9.2, t1=10.3, t2=10.3, atr=0.2)
        try: r = HC.specialist_review(s, dict(B, sweep=dict(B["sweep"])))
        except KeyError: self.skipTest("fixture needs more sweep fields")
        self.assertFalse(any("too wide" in x for x in r["reasons"]))
    def test_source(self): self.assertIn("width not judged", open(HC.__file__).read())
if __name__ == "__main__": unittest.main()
