import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tfo_sol_short as s


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.r = Path(self.temp.name)
        self.paths = patch.multiple(s, R=self.r, BOOK=self.r/'crypto_sol_short_pilot.json',
                                   POWDER=self.r/'gap_close_levered_book.json',
                                   PLAN=self.r/'plan.json', JOURNAL=self.r/'journal.json',
                                   REPORT=self.r/'report.json')
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.addCleanup(self.temp.cleanup)
        s.atomic(s.POWDER, {'book_key':'gap_close_levered_book','cash':100000.,
                           'equity':100000.,'seed_usd':100000.,'start':100000.,'holdings':{},'capital_events':[]})
        s.atomic(s.PLAN, {})

    def snapshot(self, bid, asks):
        utc=s.stamp()
        hour=int(s.epoch(utc)//3600)*3600
        rates=[{'timestamp':datetime.fromtimestamp(t,timezone.utc).isoformat(), 'fundingRate':.0001}
               for t in (hour-3600,hour)]
        return {'book':{'serverTime':utc,'orderBook':{'bids':[[bid,10000]],'asks':asks}},
                'ticker':{'serverTime':utc,'tickers':[{'symbol':s.SYMBOL,'suspended':False,
                          'markPrice':asks[0][0],'fundingRate':.0001,'fundingRatePrediction':999.}]},
                'funding':{'rates':rates}}

    def opening(self):
        snap=self.snapshot(100,[[100.1,10000]])
        quote={'quantity':700.,'entry':100.,'cover':100.1,'lot':.01,'conservative_maintenance_fraction':.25}
        with patch.object(s,'market',return_value=snap), patch.object(s,'entry_quote',return_value=quote), \
             patch.object(s,'entry_admission',return_value={}):
            return s.run('open')

    def test_open_half_stop_and_duplicate_preserve_economics(self):
        self.opening()
        b=s.read(s.BOOK); p=s.read(s.POWDER)
        self.assertEqual(p['cash'],25000)
        self.assertAlmostEqual(b['seed_usd']+p['seed_usd'],100000)
        with self.assertRaisesRegex(ValueError,'already exists'):
            s.run('open')
        # First half consumes cheaper asks. Residual must be valued at the
        # higher marginal price, not re-use those same cheap displayed units.
        with patch.object(s,'market',return_value=self.snapshot(95.9,[[96,350],[97,10000]])):
            result=s.run('tick')
        self.assertEqual(result['action'],'HALF_BANK')
        b=s.read(s.BOOK)
        self.assertEqual(b['quantity'],350)
        self.assertAlmostEqual(b['mark_price'],97)
        self.assertAlmostEqual(b['deploy_basis_usd'],b['equity'])
        with patch.object(s,'market',return_value=self.snapshot(110,[[110.1,10000]])):
            result=s.run('tick')
        self.assertEqual(result['action'],'STOP')
        b=s.read(s.BOOK);p=s.read(s.POWDER)
        self.assertEqual(b['quantity'],0)
        self.assertAlmostEqual(b['seed_usd']+p['seed_usd'],100000)
        total=b['equity']+p['equity']-100000
        economic=b['realised_usd']+b['funding_total_usd']-b['costs_paid_usd']
        self.assertAlmostEqual(total,economic,places=7)
        self.assertAlmostEqual(b['equity']-b['seed_usd'],economic,places=7)
        self.assertEqual(s.run('tick')['action'],'CLOSED')

    def test_conflicting_funding_does_not_change_ledgers(self):
        self.opening()
        before={p:p.read_bytes() for p in (s.BOOK,s.POWDER)}
        bad=self.snapshot(110,[[110.1,10000]])
        bad['funding']['rates'][-1]['fundingRate']=.0002
        with patch.object(s,'market',return_value=bad), self.assertRaisesRegex(ValueError,'funding'):
            s.run('tick')
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def history_outage(self, snapshot):
        def public_get(url):
            if 'historical-funding-rates' in url:
                raise OSError('history endpoint unavailable')
            if 'orderbook?' in url:
                return snapshot['book']
            if 'tickers?' in url:
                return snapshot['ticker']
            return {}
        return patch.object(s, 'get', side_effect=public_get)

    def test_history_outage_current_actual_rate_still_allows_stop_without_cache(self):
        self.opening()
        book=s.read(s.BOOK)
        book.pop('observed_hourly_funding_rates',None)
        snapshot=self.snapshot(110,[[110.1,10000]])
        end=s.epoch(snapshot['book']['serverTime'])
        start=max(math.floor(end/3600)*3600,end-5)
        book['funding_through_utc']=datetime.fromtimestamp(start,timezone.utc).isoformat()
        s.atomic(s.BOOK,book)
        with self.history_outage(snapshot):
            result=s.run('tick')
        self.assertEqual(result['action'],'STOP')
        after=s.read(s.BOOK)
        self.assertEqual(after['quantity'],0)
        self.assertAlmostEqual(after['funding_total_usd'],700*.0001*(end-start)/3600)
        self.assertEqual(after['funding_history_status'],'ACTUAL_RATE_FALLBACK')
        self.assertEqual(after['observed_hourly_funding_rates'][-1]['fundingRate'],.0001)
        self.assertNotEqual(after['observed_hourly_funding_rates'][-1]['fundingRate'],999.)

    def test_history_outage_cross_hour_uses_cached_previous_actual_rate(self):
        self.opening()
        book=s.read(s.BOOK);snapshot=self.snapshot(100,[[100.1,10000]])
        end=s.epoch(snapshot['book']['serverTime']);hour=math.floor(end/3600)*3600
        book['funding_through_utc']=datetime.fromtimestamp(hour-1800,timezone.utc).isoformat()
        book['observed_hourly_funding_rates']=[{
            'timestamp':datetime.fromtimestamp(hour-3600,timezone.utc).isoformat(), 'fundingRate':.02}]
        snapshot['ticker']['tickers'][0]['fundingRate']=-.01
        s.atomic(s.BOOK,book)
        with self.history_outage(snapshot):
            result=s.run('tick')
        after=s.read(s.BOOK)
        self.assertEqual(result['action'],'HOLD')
        self.assertAlmostEqual(after['funding_total_usd'],700*(.02*.5-.01*(end-hour)/3600))
        self.assertEqual(len(after['observed_hourly_funding_rates']),2)

    def test_history_outage_missing_intermediate_hour_leaves_ledgers_unchanged(self):
        self.opening()
        book=s.read(s.BOOK);snapshot=self.snapshot(110,[[110.1,10000]])
        hour=math.floor(s.epoch(snapshot['book']['serverTime'])/3600)*3600
        book['funding_through_utc']=datetime.fromtimestamp(hour-5400,timezone.utc).isoformat()
        book['observed_hourly_funding_rates']=[{
            'timestamp':datetime.fromtimestamp(hour-7200,timezone.utc).isoformat(),'fundingRate':.02}]
        s.atomic(s.BOOK,book)
        before={p:p.read_bytes() for p in (s.BOOK,s.POWDER)}
        with self.history_outage(snapshot),self.assertRaisesRegex(ValueError,'missing actual funding interval'):
            s.run('tick')
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_fallback_never_accepts_stale_ticker_or_history_outage_on_entry(self):
        snapshot=self.snapshot(100,[[100.1,10000]])
        snapshot['ticker']['serverTime']='2000-01-01T00:00:00Z'
        with self.history_outage(snapshot),self.assertRaisesRegex(ValueError,'stale'):
            s.market(entry=False)
        snapshot=self.snapshot(100,[[100.1,10000]])
        with self.history_outage(snapshot),self.assertRaisesRegex(OSError,'history endpoint'):
            s.market(entry=True)

    def test_observed_cache_is_bounded_to_eight_actual_hours(self):
        snapshot=self.snapshot(100,[[100.1,10000]])
        hour=math.floor(s.epoch(snapshot['ticker']['serverTime'])/3600)*3600
        snapshot['funding']['rates']=[{
            'timestamp':datetime.fromtimestamp(hour-i*3600,timezone.utc).isoformat(),'fundingRate':.0001}
            for i in range(12)]
        rates,cache=s.observed_funding_rates(snapshot)
        self.assertEqual(len(rates),12)
        self.assertEqual(len(cache),8)

    def test_history_outage_cannot_substitute_prediction_for_invalid_actual_rate(self):
        self.opening()
        snapshot=self.snapshot(110,[[110.1,10000]])
        snapshot['ticker']['tickers'][0]['fundingRate']=float('nan')
        before={p:p.read_bytes() for p in (s.BOOK,s.POWDER)}
        with self.history_outage(snapshot),self.assertRaisesRegex(ValueError,'numeric'):
            s.run('tick')
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_interrupted_pair_rolls_forward_once(self):
        old=s.read(s.POWDER);expected={p.name:s.digest(p) for p in (s.BOOK,s.POWDER)}
        new=copy.deepcopy(old);new['cash']-=500;new['equity']-=500;new['seed_usd']-=500;new['start']-=500
        b={'cash':500,'equity':500,'seed_usd':500,'start':500}
        original=s.atomic
        def fail_second(path,data):
            if path==s.POWDER:raise OSError('injected interrupted second write')
            original(path,data)
        with patch.object(s,'atomic',side_effect=fail_second), self.assertRaises(OSError):
            s.commit(b,new,expected,'TEST')
        self.assertEqual(s.read(s.JOURNAL)['status'],'PREPARED')
        s.recover_transaction();s.recover_transaction()
        self.assertEqual(s.read(s.POWDER)['cash'],99500)
        self.assertEqual(s.read(s.BOOK)['cash'],500)
        self.assertEqual(s.read(s.JOURNAL)['status'],'COMMITTED')

    def test_concurrent_change_refuses_commit(self):
        old=s.read(s.POWDER);expected={p.name:s.digest(p) for p in (s.BOOK,s.POWDER)}
        modified=copy.deepcopy(old);modified['cash']+=1;s.atomic(s.POWDER,modified)
        with self.assertRaisesRegex(RuntimeError,'concurrent'):
            s.commit({},old,expected,'TEST')
        self.assertEqual(s.read(s.POWDER)['cash'],100001)
        self.assertFalse(s.BOOK.exists())

    def test_funding_hour_split_signed_and_gap(self):
        rates=[{'timestamp':'2026-09-20T06:00:00Z','fundingRate':.02},
               {'timestamp':'2026-09-20T07:00:00Z','fundingRate':-.01}]
        self.assertAlmostEqual(s.funding_delta('2026-09-20T06:30:00Z','2026-09-20T07:15:00Z',100,rates),.75)
        with self.assertRaisesRegex(ValueError,'missing'):
            s.funding_delta('2026-09-20T05:59:00Z','2026-09-20T06:15:00Z',100,rates)
        with self.assertRaises(ValueError):s.funding_delta('2026-09-20T07:00:00Z','2026-09-20T06:00:00Z',100,rates)


if __name__=='__main__':unittest.main()
