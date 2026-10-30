"""Offline tests for shark_slippage (logging only). No network, no live ledgers or logs."""
import contextlib, csv, inspect, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shark_slippage as SS


def rows(path):
    with open(path) as f: return list(csv.DictReader(f))


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.log = os.path.join(self.tmp, "ht_log.txt")
        self.reader = lambda sym: ({"bid": 99.9, "ask": 100.1, "ts": "2026-10-05T13:30:01Z", "source": "nasdaq"}, [])

    def test_buy_row_spread_and_slip(self):
        r = SS.record("BUY", "fundamental_book", "XYZ", "long", 10, {"px": 100.05, "px_usd": 100.05, "tape": "LIVE"},
                      {"ticket_id": "HT-1", "exit_variant": "V7"}, ledger_log=self.log, quote_reader=self.reader)
        self.assertEqual(r["spread_bp"], 20.0); self.assertEqual(r["half_spread_bp"], 10.0); self.assertEqual(r["slip_vs_mid_bp"], 5.0)
        got = rows(os.path.join(self.tmp, "shark_slippage.csv"))
        self.assertEqual(len(got), 1); self.assertEqual(got[0]["ticket_id"], "HT-1"); self.assertEqual(got[0]["quote_source"], "nasdaq")

    def test_sell_and_short_cover_signs(self):
        r = SS.record("STOP", "fundamental_book", "XYZ", "long", 10, {"px": 99.95}, ledger_log=self.log, quote_reader=self.reader)
        self.assertEqual(r["slip_vs_mid_bp"], 5.0)                      # sold 5bp below mid = 5bp cost
        r = SS.record("FLAT", "fundamental_book", "XYZ", "short", 10, {"px": 100.05}, ledger_log=self.log, quote_reader=self.reader)
        self.assertEqual(r["slip_vs_mid_bp"], 5.0)                      # covered 5bp above mid = 5bp cost

    def test_never_raises(self):
        def boom(sym): raise RuntimeError("down")
        r = SS.record("BUY", "b", "XYZ", "long", 1, {"px": 1.0}, ledger_log=self.log, quote_reader=boom)
        self.assertIsNone(r)                                            # swallowed
        r = SS.record("BUY", "b", "XYZ", "long", 1, {"px": 1.0}, ledger_log=self.log, quote_reader=lambda s: (None, ["nasdaq: stale"]))
        self.assertEqual(r["quote_source"], "unavailable"); self.assertIn("stale", r["note"])

    def test_offline_log_path_reads_no_quote(self):
        with mock.patch.object(SS, "read_quote", side_effect=AssertionError("network in a test")):
            r = SS.record("BUY", "b", "XYZ", "long", 1, {"px": 1.0}, ledger_log=self.log)
        self.assertIn("offline", r["note"]); self.assertTrue(os.path.exists(os.path.join(self.tmp, "shark_slippage.csv")))

    def test_live_path_is_logs_dir(self):
        p, live = SS._path_for(SS.DEFAULT_LEDGER_LOG)
        self.assertTrue(live); self.assertTrue(p.endswith(os.path.join("logs", "shark_slippage.csv")))

    def test_read_quote_order_and_no_key_output(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APCA_API_KEY_ID", None); os.environ.pop("APCA_API_SECRET_KEY", None)
            self.assertIsNone(SS._alpaca_iex("XYZ"))                     # no keys in env -> skipped, no file reads


class Wiring(unittest.TestCase):
    def test_tfo_buy_logs_after_save_and_order_math_unchanged(self):
        import tfo_buy
        book = {"seed_usd": 1000.0, "cash": 10.0, "costs_paid_usd": 5.0, "equity": 1010.0, "positions": {}}
        gcl = {"cash": 100000.0, "seed_usd": 100000.0, "start": 100000.0, "equity": 100000.0}
        store = {tfo_buy.BOOK: book, tfo_buy.GCL: gcl}; order = []
        q = {"px_usd": 5.0, "px": 5.0, "ccy": "USD", "fx_usd": 1.0, "tape": "LIVE", "quote_utc": "2026-10-05T13:30:00Z"}
        def save(k, v): order.append("save"); store.__setitem__(k, v)
        def rec(*a, **k): order.append("record"); raise RuntimeError("logger down")   # must not affect the fill
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(tfo_buy, "load", side_effect=lambda k: store[k]), mock.patch.object(tfo_buy, "save", side_effect=save), \
             mock.patch.object(tfo_buy, "LOG", os.path.join(tmp, "log")), mock.patch.object(tfo_buy, "FACTS", os.path.join(tmp, "facts")), \
             mock.patch.object(tfo_buy, "receipt", return_value={}), \
             mock.patch.object(tfo_buy.desk_lock, "ledger_lock", side_effect=lambda *a, **k: contextlib.nullcontext()), \
             mock.patch.object(SS, "record", side_effect=rec):
            line = tfo_buy.execute("XYZ", 10000.0, "tech", "edge", "test", q)
        self.assertTrue(line.startswith("LOG ")); self.assertEqual(order[-1], "record"); self.assertIn("save", order)
        self.assertAlmostEqual(store[tfo_buy.BOOK]["positions"]["XYZ"]["units"], 2000.0)

    def test_tfo_buy_writes_slippage_row_offline(self):
        import tfo_buy
        store = {tfo_buy.BOOK: {"seed_usd": 0.0, "cash": 0.0, "costs_paid_usd": 0.0, "positions": {}},
                 tfo_buy.GCL: {"cash": 100000.0, "seed_usd": 100000.0, "start": 100000.0, "equity": 100000.0}}
        q = {"px_usd": 5.0, "px": 5.0, "ccy": "USD", "fx_usd": 1.0, "tape": "LIVE", "quote_utc": "2026-10-05T13:30:00Z"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(tfo_buy, "load", side_effect=lambda k: store[k]), mock.patch.object(tfo_buy, "save", side_effect=lambda k, v: store.__setitem__(k, v)), \
             mock.patch.object(tfo_buy, "LOG", os.path.join(tmp, "log")), mock.patch.object(tfo_buy, "FACTS", os.path.join(tmp, "facts")), \
             mock.patch.object(tfo_buy, "receipt", return_value={}), \
             mock.patch.object(tfo_buy.desk_lock, "ledger_lock", side_effect=lambda *a, **k: contextlib.nullcontext()):
            tfo_buy.execute("XYZ", 10000.0, "tech", "edge", "test", q)
            got = rows(os.path.join(tmp, "shark_slippage.csv"))
        self.assertEqual(len(got), 1); self.assertEqual(got[0]["event"], "BUY"); self.assertEqual(got[0]["sym"], "XYZ")
        self.assertEqual(got[0]["exit_variant"], "V7")

    def test_tfo_ticket_hook_is_after_save_and_guarded(self):
        import tfo_ticket
        src = inspect.getsource(tfo_ticket.execute)
        i_save, i_rec = src.index("save(k, d); save(GCL, gcl)"), src.index("shark_slippage.record(")
        self.assertGreater(i_rec, i_save)
        self.assertIn("except Exception", src[i_rec - 200:i_rec + 400])


if __name__ == "__main__":
    unittest.main()
