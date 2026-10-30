import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tfo_bank_rail_scanner as rail


class RailTests(unittest.TestCase):
    def test_funder_scope_lease_and_rail_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory)
            (reports/'private_wealth_gate.json').write_text(json.dumps({'active': {
                'cycle_id': 'test', 'expires_utc': '2026-09-24T08:34:25Z'}}))
            current = rail.datetime(2026,9,24,8,30,tzinfo=rail.UTC).timestamp()
            self.assertEqual(rail.funder_action('crypto_liq_beta_book.json','HOLD','test',reports,current), 'FUNDER_DIRECTED_CASH_RAISE')
            self.assertEqual(rail.funder_action('crypto_liq_beta_book.json','STOP','test',reports,current), 'STOP')
            self.assertEqual(rail.funder_action('soxl_weekend_probe.json','HOLD','test',reports,current), 'HOLD')
            self.assertEqual(rail.funder_action('crypto_liq_beta_book.json','HOLD',None,reports,current), 'HOLD')
            with self.assertRaises(RuntimeError):
                rail.funder_action('crypto_liq_beta_book.json','HOLD','wrong',reports,current)
            with self.assertRaises(RuntimeError):
                rail.funder_action('crypto_liq_beta_book.json','HOLD','test',reports,current+600)

    def test_funder_close_is_not_labelled_profit_bank(self):
        book={'cash':3,'holdings':{'ETH/USD':2}}
        legs=[{'pair':'ETH/USD','mark':98,'entry':100,'quote':{'px':98,'epoch':1}}]
        book,event,_,cash=rail.execute_full_flat(book,legs,199,200,-.5,rail.now_syd(),'FUNDER_DIRECTED_CASH_RAISE')
        self.assertEqual(book['status'],'CLOSED_FUNDER_DIRECTED')
        self.assertAlmostEqual(cash,198.902)
        self.assertLess(event['sold']['ETH/USD']['pnl_usd'],0)

    def test_price_age_and_invalid_values(self):
        for quote in (None, 2, {}, {"px": -1, "epoch": 100},
                      {"px": float("nan"), "epoch": 100},
                      {"px": 1, "epoch": 101}, {"px": 1, "epoch": 39}):
            self.assertFalse(rail.quote_valid(quote, current=100))
        self.assertTrue(rail.quote_valid({"px": 1, "epoch": 40}, current=100))

    def test_no_stored_mark_fallback(self):
        book = {"cash": 0, "holdings": {"ETH/USD": 1}, "mark_px_usd": {"ETH/USD": 2000}}
        _, _, _, missing = rail.mark_book(book, {}, None)
        self.assertEqual(missing, ["ETH/USD"])

    def test_dirty_units_block(self):
        now = rail.datetime.now(rail.UTC).timestamp()
        book = {"holdings": {"ETH/USD": 1}, "entry_px": {"ETH/USD": 1}}
        _, _, _, missing = rail.mark_book(book, {"XETHZUSD": {"px": 2000, "epoch": now}}, None)
        self.assertTrue(missing)

    def test_pair_preserves_equity_and_capital(self):
        sleeve = {"seed_usd": 100, "start": 95}
        gcl = {"cash": 20, "equity": 220, "holdings": {"RCL": 1}, "seed_usd": 200, "start": 190}
        rail.paired_transfer_sleeve_to_gcl(sleeve, "test", 30, "test", rail.now_syd(), "tag", gcl)
        self.assertEqual(gcl["equity"], 250)
        self.assertEqual(gcl["cash"], 50)
        self.assertEqual(sleeve["seed_usd"] + gcl["seed_usd"], 300)
        self.assertEqual(sleeve["start"] + gcl["start"], 285)
        self.assertEqual(sleeve["capital_events"][0]["usd"] + gcl["capital_events"][0]["usd"], 0)

    def test_rail_order(self):
        self.assertEqual(rail.decide(-5)[0], "STOP")
        self.assertEqual(rail.decide(5)[0], "FULL_BANK")
        self.assertEqual(rail.decide(2)[0], "HALF_BANK")
        self.assertEqual(rail.decide(1)[0], "HOLD")

    def test_flat_receipt_cash_and_costs(self):
        book = {"cash": 3, "holdings": {"ETH/USD": 2}}
        legs = [{"pair": "ETH/USD", "mark": 105, "entry": 100, "quote": {"px": 105, "epoch": 1}}]
        book, event, residual, banked = rail.execute_full_flat(book, legs, 213, 200, 6.5, rail.now_syd(), "FULL_BANK")
        sale = event["sold"]["ETH/USD"]
        self.assertAlmostEqual(banked, 212.895)
        self.assertAlmostEqual(sale["pnl_usd"], 9.895)
        self.assertAlmostEqual(sale["proceeds_usd"] - sale["principal_usd"], sale["pnl_usd"])
        self.assertEqual(book["holdings"], {})
        self.assertTrue(book["closed"])

    def test_kraken_requires_current_bar(self):
        now = rail.datetime.now(rail.UTC).timestamp()
        trade = {"result": {"XETHZUSD": [["100", "1", now]], "last": "cursor"}}
        stale = {"result": {"XETHZUSD": [[now-120, "100", "100", "100", "100"]]}}
        with patch.object(rail, "http_get_json", side_effect=[trade, stale]):
            self.assertEqual(rail.fetch_kraken(["XETHZUSD"]), {})

    def test_transaction_recovery_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rail, "REPORTS", Path(directory)):
            names = [rail.ACTIVE[0], rail.GCL_FILE]
            originals = {n: rail.encoded({"cash": 10, "equity": 10}) for n in names}
            for n, raw in originals.items():
                (rail.REPORTS / n).write_bytes(raw)
            staged = {names[0]: {"cash": 0, "equity": 0}, names[1]: {"cash": 20, "equity": 20}}
            real_atomic = rail.atomic_bytes
            def interrupt(path, data):
                if path.name == names[1]:
                    raise OSError("simulated power loss")
                real_atomic(path, data)
            with patch.object(rail, "atomic_bytes", side_effect=interrupt):
                with self.assertRaises(OSError):
                    rail.commit_books(staged, originals, [], "ticket")
            rail.recover_transaction()
            rail.recover_transaction()
            for n in names:
                self.assertEqual(json.loads((rail.REPORTS / n).read_bytes()), staged[n])
            self.assertEqual(json.loads((rail.REPORTS / ".tfo_bank_rail_transaction.json").read_bytes())["status"], "COMMITTED")

    def test_concurrent_edit_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rail, "REPORTS", Path(directory)):
            name = rail.ACTIVE[0]
            (rail.REPORTS / name).write_bytes(b"{}")
            with self.assertRaisesRegex(RuntimeError, "Ledger changed"):
                rail.commit_books({name: {}}, {name: b'{"cash": 0}'}, [], "ticket")
            self.assertEqual((rail.REPORTS / name).read_bytes(), b"{}")

    def test_stale_at_commit_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rail, "REPORTS", Path(directory)):
            with self.assertRaisesRegex(RuntimeError, "Quote aged"):
                rail.commit_books({}, {}, [{"px": 1, "epoch": 0}], "ticket")
            self.assertEqual(list(rail.REPORTS.iterdir()), [])

    def test_shared_tick_lock_rejected(self):
        from tfo_equity_refresh import writer_locks
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".tick.lock").mkdir()
            with self.assertRaisesRegex(RuntimeError, "desk tick"):
                with writer_locks(directory):
                    self.fail("lock not honoured")


if __name__ == "__main__":
    unittest.main()
