import contextlib
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import tfo_discretionary_sizing as sizing
import fundamental_open_new as entry
import test_fundamental_open_new as route_tests

class SizingTests(unittest.TestCase):
    def test_boundaries_and_no_long_short_netting(self):
        for amount in [50000,400000]:
            self.assertEqual(sizing.check_range(amount,[])['aggregate_entry_usd'],amount)
        for amount in [49999.99,400000.01,0,-1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError): sizing.check_range(amount,[])
        self.assertEqual(sizing.check_range(50000,[350000])['aggregate_entry_usd'],400000)
        with self.assertRaises(ValueError): sizing.check_range(50000,[350001])
    def test_cross_book_native_entry_not_marks(self):
        with tempfile.TemporaryDirectory() as d:
            books=[]
            for i,side in enumerate(['long','short']):
                p=Path(d)/str(i);p.write_text(json.dumps({'positions':{'XYZ':{'side':side,'notional_usd':175000}}}))
                books.append({'path':str(p),'key':str(i),'positions':[{'sym':'XYZ','ticker':'XYZ'}]})
            amounts,_,_=sizing.aggregate_evidence('XYZ',books)
            self.assertEqual(amounts,[175000,175000])
            self.assertEqual(sizing.check_range(50000,amounts)['aggregate_entry_usd'],400000)
            with self.assertRaises(ValueError): sizing.check_range(50001,amounts)
            books[0]['positions'][0]['fallback_basis']='option premium'
            with self.assertRaises(ValueError): sizing.aggregate_evidence('XYZ',books)
    def test_unknown_basis_and_unreadable_book_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'book';p.write_text('{"positions":{"XYZ":{"premium_usd":100}}}')
            b={'path':str(p),'key':'b','positions':[{'sym':'XYZ','ticker':'XYZ'}]}
            with self.assertRaises(ValueError): sizing.aggregate_evidence('XYZ',[b])
            b['error']='unreadable'
            with self.assertRaises(ValueError): sizing.aggregate_evidence('ABC',[b])
    def test_daily_expiry_date_and_loss(self):
        now=dt.datetime(2026,9,24,23,tzinfo=dt.timezone.utc)
        r={'asof':now.isoformat(),'sydney_date':'2026-09-25','daily_risk_basis_allowed':True,'daily_risk_basis':'FUNDER_APPROVED_FIXED_PROXY','daily_risk_authorization_expires_utc':'2026-09-25T14:00:00Z','pnl_pct':0}
        sizing.daily_gate(r,now)
        for field,value in [('sydney_date','2026-09-24'),('daily_risk_authorization_expires_utc','2026-09-24T14:00:00Z'),('pnl_pct',-3)]:
            with self.assertRaises(ValueError): sizing.daily_gate(dict(r,**{field:value}),now)
    def test_sector_repairs_and_etf_upper_bound(self):
        p={s:{'notional_usd':100} for s in ['AMAT','AMD','AVGO','CDNS','LRCX','MRVL','NVDA','SNPS','SMH','QQQ']}
        e=entry.sector_exposure_from_positions(p)
        self.assertEqual(e['technology'],1000)
        self.assertEqual(e['energy'],100)
        with self.assertRaises(ValueError):entry.sector_exposure_from_positions({'UNKNOWN':{'notional_usd':100}})
    def test_occupied_lock_not_removed(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sizing,'BASE',Path(d)):
            p=Path(d)/'reports/.tick.lock';p.mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                with sizing.writer_scope('discretionary'): pass
            self.assertTrue(p.exists())

class DiscretionaryRouteTests(route_tests.TestCatalystDatePersistence):
    def test_discretionary_replaces_only_name_cap_and_preserves_cash_sector(self):
        before=Path(self.ledger).read_bytes()
        with mock.patch.object(sizing,'writer_scope',return_value=contextlib.nullcontext()), mock.patch.object(sizing,'admit',side_effect=lambda s,n:sizing.check_range(n,[])):
            self.assertEqual(self._open(dry_run=True,notional=50000,sizing_policy='discretionary'),0)
            self.assertEqual(self._open(dry_run=True,notional=49999,sizing_policy='discretionary'),2)
            self.assertEqual(self._open(dry_run=True,notional=100001,sizing_policy='discretionary'),2)
            self.assertEqual(self._open(dry_run=True,notional=120001,sizing_policy='discretionary'),2)
        self.assertEqual(Path(self.ledger).read_bytes(),before)
    def test_second_check_refuses_changed_aggregate(self):
        before=Path(self.ledger).read_bytes()
        with mock.patch.object(sizing,'writer_scope',return_value=contextlib.nullcontext()), mock.patch.object(sizing,'admit',side_effect=[sizing.check_range(50000,[]),ValueError('changed aggregate')]):
            self.assertEqual(self._open(notional=50000,sizing_policy='discretionary'),2)
        self.assertEqual(Path(self.ledger).read_bytes(),before)
