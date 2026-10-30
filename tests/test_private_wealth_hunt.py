import unittest
from unittest.mock import patch
import private_wealth_hunt as hunt


class HuntTests(unittest.TestCase):
    def delayed_fixture(self):
        stamp = 1790256600
        iso = lambda t: hunt.dt.datetime.fromtimestamp(t, hunt.dt.timezone.utc).isoformat()
        q = {'session': 'regular', 'price_session': 'regular', 'px': 100,
             'quote_utc': iso(stamp), 'fetched_at': iso(stamp+900)}
        payload = {'chart': {'result': [{'meta': {'currentTradingPeriod': {
            'regular': {'start': stamp-60, 'end': stamp+20000}}},
            'timestamp': [stamp], 'indicators': {'quote': [{'close': [100]}]}}]}}
        return q, payload

    def test_delayed_regular_bar_is_eligible_research_evidence(self):
        q, payload = self.delayed_fixture()
        self.assertTrue(hunt.paper_bar_eligible(q, payload))

    def test_delay_allowance_does_not_admit_missing_bar_or_premarket(self):
        q, payload = self.delayed_fixture()
        q['session'] = 'pre'
        self.assertFalse(hunt.paper_bar_eligible(q, payload))
        q['session'] = 'regular'
        payload['chart']['result'][0]['timestamp'] = []
        self.assertFalse(hunt.paper_bar_eligible(q, payload))

    def test_stale_future_and_prior_session_bars_fail(self):
        q, payload = self.delayed_fixture()
        stamp = hunt.dt.datetime.fromisoformat(q['quote_utc'])
        for seconds in (1201, -1):
            q['fetched_at'] = (stamp+hunt.dt.timedelta(seconds=seconds)).isoformat()
            self.assertFalse(hunt.paper_bar_eligible(q, payload))
        q, payload = self.delayed_fixture()
        payload['chart']['result'][0]['meta']['currentTradingPeriod']['regular']['start'] += 120
        self.assertFalse(hunt.paper_bar_eligible(q, payload))

    def test_native_position_identifiers_are_checked(self):
        cycle = {'boards': {'Positions': {'data': {'positions': [
            {'ticker': 'FANG', 'sym': 'FANG'}, {'symbol': 'BKR'}]}}}}
        self.assertEqual(hunt.held_symbols(cycle), {'FANG', 'BKR'})

    def test_fresh_last_price_cannot_substitute_for_spread_evidence(self):
        quote={'px':10,'ccy':'EUR','session':'regular','executable':True,
               'age_sec':1,'quote_utc':'2026-09-24T08:00:00Z',
               'fetched_at':'2026-09-24T08:00:01Z','status':'regular_current'}
        payload={'chart':{'result':[{'meta':{'previousClose':9.9,'exchangeDataDelayedBy':0}}]}}
        with patch.object(hunt,'parse_chart',return_value=quote):
            row=hunt.assess('TTE.PA','energy',payload,set(),[])
        self.assertFalse(row['entry_approved'])
        self.assertIsNone(row['spread_bp'])
        self.assertIn('Timestamped bid/ask spread unavailable',row['reasons'])

    def test_held_closed_and_global_blocks_remain_explicit(self):
        quote={'px':59,'ccy':'AUD','session':'closed','executable':False,
               'age_sec':1000,'quote_utc':'2026-09-24T06:00:00Z',
               'fetched_at':'2026-09-24T08:00:01Z','status':'closed_last'}
        payload={'chart':{'result':[{'meta':{'previousClose':60}}]}}
        with patch.object(hunt,'parse_chart',return_value=quote):
            row=hunt.assess('XRO.AX','ASX',payload,{'XRO.AX'},['PROXY accounting'])
        self.assertFalse(row['entry_approved'])
        for reason in ('Already held locally','Outside regular continuous session','PROXY accounting'):
            self.assertIn(reason,row['reasons'])
