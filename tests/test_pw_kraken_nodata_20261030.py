import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import private_wealth_monitor as pw


class KrakenNoDataTest(unittest.TestCase):
    def test_no_data_raises_in_strict_fetch(self):
        with mock.patch.object(pw, 'request_json', return_value={'error': ['EGeneral:No data']}):
            with self.assertRaises(ValueError):
                pw.kraken_trade()

    def test_no_data_degrades_to_stale(self):
        with mock.patch.object(pw, 'request_json', return_value={'error': ['EGeneral:No data']}):
            tape = pw.kraken_trade_safe()
        q = tape['ETH/USD']
        self.assertEqual(q['class'], 'STALE')
        self.assertIsNone(q['price'])
        self.assertIn('No data', q['feed_error'])
        self.assertNotEqual(pw.quote_class(q, pw.utcnow()), 'LIVE')

    def test_network_error_degrades(self):
        with mock.patch.object(pw, 'request_json', side_effect=OSError('down')):
            self.assertEqual(pw.kraken_trade_safe()['ETH/USD']['class'], 'STALE')

    def test_good_payload_passes_through(self):
        import time
        t = time.time()
        payload = {'error': [], 'result': {'XETHZUSD': [['2500.1', '0.1', str(t), 'b', 'm', '']], 'last': '1'}}
        with mock.patch.object(pw, 'request_json', return_value=payload):
            q = pw.kraken_trade_safe()['ETH/USD']
        self.assertEqual(q['class'], 'LIVE'); self.assertEqual(q['price'], 2500.1)

    def test_receipt_uses_safe_fetch(self):
        src = (Path(pw.__file__).parent / 'private_wealth_cycle_receipt.py').read_text()
        self.assertIn('monitor.kraken_trade_safe()', src)
        self.assertNotIn('monitor.kraken_trade()', src)


if __name__ == '__main__':
    unittest.main()
