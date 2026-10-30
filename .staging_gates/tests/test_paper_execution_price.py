import unittest
from unittest.mock import patch

import paper_execution_price as paper
import fundamental_book as book
from test_tfo_equity_refresh import chart, EPOCH, NOW
import tfo_equity_refresh as refresh


class DelayedPaperTests(unittest.TestCase):
    def delayed(self):
        return refresh.parse_chart('TEST', chart(regular_epoch=EPOCH-900,
                                   bars=[(EPOCH-900, 11)]), NOW)

    def test_actual_delayed_bar_is_paper_not_live(self):
        q = self.delayed()
        self.assertTrue(q['paper_executable'])
        self.assertFalse(q['executable'])
        self.assertFalse(q['quote_current'])
        self.assertEqual(q['paper_price_class'], 'DELAYED PAPER')
        self.assertEqual(q['px'], 11)
        self.assertEqual(q['age_sec'], 900)

    def test_metadata_without_bar_does_not_enable_delayed_paper(self):
        q = refresh.parse_chart('TEST', chart(regular_epoch=EPOCH-900), NOW)
        self.assertFalse(paper.eligible(q))

    def test_stale_or_extended_bar_does_not_enable_delayed_paper(self):
        for age, session in ((1201, 'regular'), (900, 'pre'), (900, 'post')):
            q = refresh.parse_chart('TEST', chart(regular_epoch=EPOCH-age,
                                    session=session, bars=[(EPOCH-age, 11)]), NOW)
            self.assertFalse(paper.eligible(q))

    def test_unknown_fx_still_blocks_paper_execution(self):
        q = self.delayed()
        q['ccy'] = 'EUR'
        quotes, _ = refresh.collect_quotes(['TEST'], lambda _: q,
                                         lambda _: ({'EUR': 1.1}, {}))
        self.assertFalse(paper.eligible(quotes['TEST']))

    def test_usd_delayed_price_passes_native_fetch_with_honest_metadata(self):
        q = self.delayed()
        quotes, failures = refresh.collect_quotes(['TEST'], lambda _: q,
                                                   lambda _: ({'USD': 1}, {}))
        with patch.object(refresh, 'collect_quotes', return_value=(quotes, failures)):
            result, _ = book.fetch_prices(['TEST'])
        self.assertIn('TEST', result)
        meta = book.quote_metadata(result['TEST'])
        self.assertFalse(meta['mark_stale'])
        self.assertFalse(meta['mark_executable'])
        self.assertTrue(meta['mark_paper_executable'])
        self.assertEqual(paper.receipt(q)['price_source_utc'], q['quote_utc'])
        self.assertFalse(paper.receipt(q)['live_execution'])

    def test_pre_entry_unknown_entry_and_repeated_bar_are_refused(self):
        q = self.delayed()
        self.assertFalse(paper.chronological(q, {}))
        self.assertFalse(paper.chronological(q, {'entry_utc': NOW}))
        p = {'entry_utc': '2026-09-01T00:00:00Z'}
        self.assertTrue(paper.chronological(q, p))
        p['last_paper_exit_source_utc'] = q['quote_utc']
        self.assertFalse(paper.chronological(q, p))

    def test_delayed_simulation_advances_from_fill_source_not_wall_clock(self):
        q = self.delayed()
        stamp = refresh.dt.datetime.fromisoformat(q['quote_utc'].replace('Z', '+00:00'))
        p = {'entry_utc': NOW, 'entry_price_evidence': {
            'price_source_utc': (stamp-refresh.dt.timedelta(minutes=1)).isoformat()}}
        self.assertTrue(paper.chronological(q, p))
        p['entry_price_evidence']['price_source_utc'] = q['quote_utc']
        self.assertFalse(paper.chronological(q, p))


if __name__ == '__main__':
    unittest.main()
