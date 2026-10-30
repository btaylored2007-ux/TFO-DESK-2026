"""2026-10-29 Brett: sleeve sizing excludes veteran_desk (books never merged); firm risk keeps full firm equity."""
import datetime as dt, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desk_caps

BOOKS = {"veteran_desk": 3880637.97, "fundamental_book": 1002142.17, "gap_close_levered_book": 208442.29, "trend18": 520194.74,
         "kraken_floor": 33536.71}
TOTAL = round(sum(BOOKS.values()), 2)

def _write(d, utc=None, books=BOOKS, total=TOTAL, name="firm_risk_state.json"):
    utc = utc or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    st = {"utc": utc, "firm_equity_usd": total}
    if books is not None: st["book_equity_usd"] = books
    json.dump(st, open(os.path.join(d, name), "w"))

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
    def test_excludes_vd(self):
        _write(self.d)
        se = desk_caps.sizing_equity(self.d)
        self.assertAlmostEqual(se["sizing_equity_usd"], TOTAL - 3880637.97, places=2)
        self.assertEqual(list(se["excluded_usd"]), ["veteran_desk"])
        self.assertAlmostEqual(se["firm_equity_usd"], TOTAL, places=2)
    def test_floats_with_books(self):
        b = dict(BOOKS); b["trend18"] += 10000.0
        _write(self.d, books=b, total=TOTAL + 10000.0)
        self.assertAlmostEqual(desk_caps.sizing_equity(self.d)["sizing_equity_usd"], TOTAL - 3880637.97 + 10000.0, places=2)
    def test_no_vd_key_is_full(self):
        b = {k: v for k, v in BOOKS.items() if k != "veteran_desk"}; t = round(sum(b.values()), 2)
        _write(self.d, books=b, total=t)
        self.assertAlmostEqual(desk_caps.sizing_equity(self.d)["sizing_equity_usd"], t, places=2)
    def test_fail_closed_no_breakdown(self):
        _write(self.d, books=None)
        with self.assertRaises(RuntimeError): desk_caps.sizing_equity(self.d)
    def test_fail_closed_stale(self):
        _write(self.d, utc=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with self.assertRaises(RuntimeError): desk_caps.sizing_equity(self.d)
    def test_trend18_base_ex_vd(self):
        import trend18
        _write(self.d)
        with mock.patch.object(trend18, "REPORTS", self.d):
            E, src, _ = trend18.firm_equity()
        self.assertAlmostEqual(E, TOTAL - 3880637.97, places=2)
        self.assertIn("veteran_desk", src)
        self.assertLess(E, 2_000_000)
    def test_firm_exposure_exclude(self):
        _write(self.d, name="firm_risk.json")
        full = desk_caps.firm_exposure(self.d, use_registry=False)
        ex = desk_caps.firm_exposure(self.d, use_registry=False, exclude_books=desk_caps.SIZING_EXCLUDED_BOOKS)
        self.assertAlmostEqual(full["firm_equity_usd"] - ex["firm_equity_usd"], 3880637.97, places=1)
    def test_tfo_buy_shark_uses_sizing_firm_state_full(self):
        import tfo_buy
        _write(self.d)
        real = desk_caps.sizing_equity
        with mock.patch.object(desk_caps, "sizing_equity", lambda *a, **k: real(self.d)):
            seq = tfo_buy.sizing_eq()
        self.assertAlmostEqual(seq, TOTAL - 3880637.97, places=2)
        self.assertAlmostEqual(tfo_buy.shark_max_clip(seq), 0.1 * seq, places=2)
        eq, _, _ = tfo_buy.firm_state()      # firm risk metric unchanged: full firm (live csv, includes VD)
        self.assertGreater(eq, 3_000_000)

if __name__ == "__main__":
    unittest.main()
