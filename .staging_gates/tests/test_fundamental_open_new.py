#!/usr/bin/env python3
"""Focused, isolated tests for the single-position fundamental entry path."""
import datetime as dt
import contextlib
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fundamental_open_new as entry  # noqa: E402
import fundamental_session_queue as queue_mod  # noqa: E402


class TestCatalystDatePersistence(unittest.TestCase):
    def setUp(self):
        import v1_venues   # 2026-10-17 triage: these tests cover sizing/ledger rules, not the 29 Sep venue directive (tests/test_v1_venues.py owns that)
        _p = mock.patch.object(v1_venues, "require_entry_allowed", lambda *a, **k: None); _p.start(); self.addCleanup(_p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, "fundamental_book.json")
        self.entry_log = os.path.join(self.tmp.name, "fundamental_new_entries.jsonl")
        self.existing_position = {"side": "long", "notional_usd": 1_000.0,
                                  "sector": "energy", "entry_ccy": "USD",
                                  "marker": "must remain unchanged"}
        self.old_entry = {"symbol": "OLD", "marker": "must remain unchanged"}
        with open(self.ledger, "w") as f:
            json.dump({"seed_usd": 600_000.0,
                       "contributed_capital_usd": 600_000.0,
                       "cash": 100_000.0,
                       "positions": {"EXIST": self.existing_position},
                       "new_entries": [self.old_entry]}, f)

        self.old_ledger_path, self.old_entry_log_path = entry.LEDGER, entry.ENTRY_LOG
        entry.LEDGER, entry.ENTRY_LOG = self.ledger, self.entry_log
        self.fx_rates = {"USD": 1.0, "ZAc": 0.0006, "ZAR": 0.06}
        fake_book = SimpleNamespace(
            fx_to_usd=lambda ccy: self.fx_rates.get(ccy))
        self.live_info = {"marketState": "REGULAR", "currency": "USD",
                          "currentPrice": 125.0, "regularMarketTime": dt.datetime.now(dt.timezone.utc).timestamp()-1}
        self.gate_result = {
            "ok": True,
            "reasons": [],
            "measured": {
                "spread_pct": 0.125,
                "adv_usd": 8_000_000.0,
                "position_pct_of_adv": 0.00125,
                "volume_trend": {"ratio_10d_over_3m": 1.2,
                                 "state": "building"},
            },
        }
        fake_yf = SimpleNamespace(
            Ticker=lambda _symbol: SimpleNamespace(info=self.live_info))
        fake_liquidity = SimpleNamespace(
            assess=lambda _info, _notional, _side, fx_usd=1.0: self.gate_result)
        self.risk_gate = mock.Mock(return_value=(True, "ok"))
        fake_risk = SimpleNamespace(entries_allowed=self.risk_gate)
        self.book_patch = mock.patch.dict(
            sys.modules,
            {"fundamental_book": fake_book,
             "fundamental_liquidity": fake_liquidity,
             "book_kill": fake_risk,
             "yfinance": fake_yf},
        )
        self.book_patch.start()
        self.lock_patch = mock.patch.object(
            entry.desk_lock, "ledger_lock", return_value=contextlib.nullcontext())
        self.lock_patch.start()
        self.catalyst_date = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        self.default_thesis = (
            "Dated synthetic test thesis. KILLS IT: synthetic test condition."
        )
        self.research_manifest_file = os.path.join(self.tmp.name, "research.json")
        self.allocator_receipt_file = os.path.join(self.tmp.name, "allocator.json")
        with open(self.research_manifest_file, "w") as handle:
            json.dump({"bound": "research"}, handle)
        with open(self.allocator_receipt_file, "w") as handle:
            json.dump({"bound": "allocator"}, handle)

    def tearDown(self):
        self.lock_patch.stop()
        self.book_patch.stop()
        entry.LEDGER, entry.ENTRY_LOG = self.old_ledger_path, self.old_entry_log_path
        self.tmp.cleanup()

    def test_missing_or_stale_source_time_refuses_without_mutation(self):
        before = open(self.ledger, 'rb').read()
        for stamp in (None, 1, float('nan')):
            self.live_info['regularMarketTime'] = stamp
            self.assertEqual(self._open(), 2)
            self.assertEqual(open(self.ledger, 'rb').read(), before)

    def test_delayed_entry_requires_bar_and_persists_its_source_clock(self):
        self.live_info['regularMarketTime'] = dt.datetime.now(dt.timezone.utc).timestamp()-900
        q = {'px': 124.0, 'ccy': 'USD', 'fx_usd': 1.0, 'px_usd': 124.0,
             'paper_executable': True, 'executable': False,
             'paper_price_class': 'DELAYED PAPER', 'quote_utc': '2026-09-24T13:31:00Z',
             'fetched_at': '2026-09-24T13:46:00Z', 'paper_price_model': 'test delayed bar'}
        fake = sys.modules['fundamental_book']
        fake.fetch_prices = lambda symbols: ({'TEST.N': q}, {})
        self.assertEqual(self._open(), 0)
        with open(self.ledger) as stream:
            p = json.load(stream)['positions']['TEST.N']
        self.assertEqual(p['entry_px_usd'], 124.0)
        self.assertEqual(p['mark_utc'], q['quote_utc'])
        self.assertEqual(p['entry_price_evidence']['price_class'], 'DELAYED PAPER')
        self.assertFalse(p['entry_price_evidence']['live_execution'])

    def test_nonfinite_or_nonpositive_size_refused_without_lock_or_quote(self):
        with mock.patch.object(entry.desk_lock, "ledger_lock") as lock, \
             mock.patch.object(entry, "live_entry_gate") as quote:
            for size in [float("nan"), float("inf"), -float("inf"), 0, -1, True, "bad"]:
                with self.subTest(size=size):
                    self.assertEqual(self._open(notional=size), 2)
            lock.assert_not_called()
            quote.assert_not_called()

    def test_book_or_firm_halt_refuses_before_quote_and_preserves_ledger(self):
        before = open(self.ledger, "rb").read()
        for reason in ["BOOK FROZEN", "FIRM-WIDE HALT"]:
            with self.subTest(reason=reason), mock.patch.object(entry, "live_entry_gate") as quote:
                self.risk_gate.return_value=(False, reason)
                self.assertEqual(self._open(), 2)
                quote.assert_not_called()
                self.assertEqual(open(self.ledger, "rb").read(), before)
                self.assertFalse(os.path.exists(self.entry_log))

    def test_risk_exception_fails_closed_before_quote(self):
        self.risk_gate.side_effect=OSError("risk state unavailable")
        with mock.patch.object(entry, "live_entry_gate") as quote:
            self.assertEqual(self._open(), 2)
            quote.assert_not_called()
        self.assertFalse(os.path.exists(self.entry_log))

    def test_halt_arriving_during_quote_refuses_at_commit(self):
        before=open(self.ledger,"rb").read()
        self.risk_gate.side_effect=[(True,"ok"),(False,"FIRM-WIDE HALT")]
        self.assertEqual(self._open(),2)
        self.assertEqual(self.risk_gate.call_count,2)
        self.assertEqual(open(self.ledger,"rb").read(),before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_imported_entry_checks_risk_while_lock_is_held(self):
        held=[]
        @contextlib.contextmanager
        def lock(_name):
            held.append(True)
            try: yield
            finally: held.pop()
        def check(ledger,label,verbose=False):
            self.assertEqual(held,[True])
            self.assertEqual(label,"TFO Fundamental Desk")
            return True,"ok"
        self.risk_gate.side_effect=check
        with mock.patch.object(entry.desk_lock,"ledger_lock",side_effect=lock):
            self.assertEqual(self._open(dry_run=True),0)
        self.assertEqual(held,[])
        self.assertEqual(self.risk_gate.call_count,2)

    def _open(self, dry_run=False, notional=10_000.0, symbol="TEST.N",
              side="long", sector="technology", thesis=None,
              catalyst_date=None, **kwargs):
        kwargs.setdefault("sizing_policy", "registered")
        return entry.open_new(
            symbol, side, notional,
            thesis or self.default_thesis,
            catalyst_date or self.catalyst_date,
            dry_run=dry_run, sector=sector, **kwargs,
        )

    def _queue_kwargs(self, features, stable_id, *, state="READY_FOR_DRY_RUN",
                      thesis=None, symbol="TEST.N", notional=10_000.0,
                      sector="technology", catalyst_date=None):
        features = sorted(features)
        thesis = thesis or self.default_thesis
        catalyst_date = catalyst_date or self.catalyst_date
        source_candidate = {
            "stable_issuer_id": stable_id,
            "symbol": symbol,
            "economic_direction": "LONG",
            "execution_status": "ready",
            "execution_symbol": symbol,
            "execution_venue": "TEST",
            "quote_currency": "USD",
            "notional_usd": notional,
            "sector": sector,
            "catalyst_date": catalyst_date,
            "review_date": (dt.date.today() + dt.timedelta(days=45)).isoformat(),
            "event_date": catalyst_date,
            "measurement_period_end": dt.date.today().isoformat(),
            "thesis_review_date": (dt.date.today() + dt.timedelta(days=45)).isoformat(),
            "thesis": thesis,
            "execution_route": "direct_local",
            "research_symbol": "TEST",
            "research_features": features,
            "research_feature_receipts": [],
        }
        manifest = {
            "schema": entry.EXECUTION_MANIFEST_SCHEMA,
            "status": "complete",
            "paper_only": True,
            "as_of": dt.date.today().isoformat(),
            "batch_id": "open-test",
            "batch_sha256": "aa" * 32,
            "research_manifest_sha256": "bb" * 32,
            "allocator_receipt_sha256": "cc" * 32,
            "research_manifest_file": {
                "path": self.research_manifest_file,
                "sha256": entry._file_sha256(self.research_manifest_file),
            },
            "allocator_receipt_file": {
                "path": self.allocator_receipt_file,
                "sha256": entry._file_sha256(self.allocator_receipt_file),
            },
            "candidates": [source_candidate],
        }
        manifest_sha = entry._json_sha256(manifest)
        queued = {
            **source_candidate,
            "source_candidate_sha256": entry._json_sha256(source_candidate),
            "state": state,
            "queued_utc": entry._utc(),
            "last_check": {
                "utc": entry._utc(),
                "price": {"px": 125.0},
                "liquidity_receipt": {
                    "market_state": "REGULAR",
                    "measured": {"spread_pct": 0.1},
                },
            },
        }
        queue = {
            "schema": entry.QUEUE_SCHEMA,
            "status": "complete",
            "paper_only": True,
            "as_of": dt.date.today().isoformat(),
            "verified_utc": entry._utc(),
            "batch_id": manifest["batch_id"],
            "batch_sha256": manifest["batch_sha256"],
            "research_manifest_sha256": manifest["research_manifest_sha256"],
            "allocator_receipt_sha256": manifest["allocator_receipt_sha256"],
            "source_execution_manifest_sha256": manifest_sha,
            "execution_manifest": manifest,
            "candidates": [queued],
        }
        queue["queue_receipt_sha256"] = entry._json_sha256(
            entry._queue_binding_payload(queue)
        )
        path = os.path.join(self.tmp.name, "queue.json")
        with open(path, "w") as handle:
            json.dump(queue, handle, indent=2)
        return queue, {
            "research_features": features,
            "manifest_sha256": manifest_sha,
            "stable_issuer_id": stable_id,
            "queue_receipt_path": path,
            "queue_receipt_sha256": entry._file_sha256(path),
        }

    def _read_ledger(self):
        with open(self.ledger) as f:
            return json.load(f)

    def _write_ledger(self, ledger):
        with open(self.ledger, "w") as f:
            json.dump(ledger, f)

    def test_sourced_date_is_written_to_position_and_both_entry_records(self):
        self.assertEqual(self._open(), 0)

        with open(self.ledger) as f:
            ledger = json.load(f)
        with open(self.entry_log) as f:
            log_records = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(ledger["positions"]["TEST.N"]["catalyst_date"],
                         self.catalyst_date)
        self.assertEqual(ledger["positions"]["TEST.N"]["sector"], "technology")
        self.assertEqual(ledger["new_entries"][0]["catalyst_date"],
                         self.catalyst_date)
        self.assertEqual(ledger["new_entries"][0]["sector"], "technology")
        self.assertEqual(log_records[0]["catalyst_date"], self.catalyst_date)
        self.assertEqual(log_records[0]["sector"], "technology")
        self.assertEqual(ledger["positions"]["TEST.N"]["entry_liquidity"]
                         ["market_state"], "REGULAR")
        self.assertEqual(log_records[0]["entry_liquidity"]["measured"]
                         ["volume_trend"]["state"], "building")
        self.assertEqual(log_records[0], ledger["new_entries"][0])
        self.assertEqual(ledger["positions"]["EXIST"], self.existing_position)
        self.assertEqual(ledger["new_entries"][1], self.old_entry)

    def test_feature_provenance_is_written_to_position_and_entry_records(self):
        _queue, kwargs = self._queue_kwargs(
            ["filing_diff", "bank_v1"], "lei:549300TESTISSUER")
        with mock.patch.object(queue_mod, "validate_queue_for_open", return_value=True):
            self.assertEqual(self._open(**kwargs), 0)

        ledger = self._read_ledger()
        with open(self.entry_log) as f:
            log_record = json.loads(next(line for line in f if line.strip()))
        expected = ledger["positions"]["TEST.N"]["research_provenance"]
        self.assertEqual(expected["features"], ["bank_v1", "filing_diff"])
        self.assertEqual(expected["manifest_sha256"], kwargs["manifest_sha256"])
        self.assertEqual(expected["queue_receipt_sha256"], kwargs["queue_receipt_sha256"])
        self.assertEqual(expected["stable_issuer_id"], "lei:549300TESTISSUER")
        self.assertEqual(ledger["new_entries"][0]["research_provenance"], expected)
        self.assertEqual(log_record["research_provenance"], expected)

    def test_partial_or_unknown_feature_provenance_refuses_before_live_gate(self):
        before = self._read_ledger()
        self.assertEqual(self._open(research_features=["filing_diff"]), 2)
        self.assertEqual(self._open(
            research_features=["filing_diff"], manifest_sha256="zz" * 32,
            stable_issuer_id="lei:issuer"), 2)
        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_general_research_cannot_supply_a_partial_receipt(self):
        before = self._read_ledger()
        with mock.patch.object(entry, "live_entry_gate") as gate:
            self.assertEqual(self._open(research_features=[], stable_issuer_id="CIK:1"), 2)
            self.assertEqual(self._open(research_features=[], stable_issuer_id="CIK:1",
                                        manifest_sha256="ab" * 32), 2)
            gate.assert_not_called()
        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_feature_entry_requires_authoritative_queue_revalidation(self):
        _queue, kwargs = self._queue_kwargs(["filing_diff"], "CIK:1")
        with mock.patch.object(
            queue_mod, "validate_queue_for_open",
            side_effect=ValueError("manifest recomputation failed"),
        ):
            self.assertEqual(self._open(**kwargs), 2)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_modified_queue_file_and_nonready_or_duplicate_rows_refuse(self):
        queue, kwargs = self._queue_kwargs(["filing_diff"], "CIK:1")
        with open(kwargs["queue_receipt_path"], "a") as handle:
            handle.write(" ")
        with mock.patch.object(queue_mod, "validate_queue_for_open", return_value=True):
            self.assertEqual(self._open(**kwargs), 2)

        queue, kwargs = self._queue_kwargs(
            ["filing_diff"], "CIK:1", state="PENDING_SESSION")
        with mock.patch.object(queue_mod, "validate_queue_for_open", return_value=True):
            self.assertEqual(self._open(**kwargs), 2)

        queue, kwargs = self._queue_kwargs(["filing_diff"], "CIK:1")
        queue["candidates"].append(dict(queue["candidates"][0]))
        queue["queue_receipt_sha256"] = entry._json_sha256(
            entry._queue_binding_payload(queue)
        )
        with open(kwargs["queue_receipt_path"], "w") as handle:
            json.dump(queue, handle, indent=2)
        kwargs["queue_receipt_sha256"] = entry._file_sha256(kwargs["queue_receipt_path"])
        with mock.patch.object(queue_mod, "validate_queue_for_open", return_value=True):
            self.assertEqual(self._open(**kwargs), 2)

    def test_ready_row_exact_matches_include_thesis_and_all_execution_fields(self):
        _queue, base = self._queue_kwargs(["filing_diff"], "CIK:1")
        variants = [
            {"notional": 10_001.0},
            {"sector": "energy"},
            {"catalyst_date": (dt.date.today() + dt.timedelta(days=31)).isoformat()},
            {"thesis": "Replacement prose. KILLS IT: a different condition."},
            {"stable_issuer_id": "CIK:2"},
            {"research_features": ["event_queue"]},
            {"manifest_sha256": "dd" * 32},
        ]
        for changed in variants:
            with self.subTest(changed=changed), mock.patch.object(
                    queue_mod, "validate_queue_for_open", return_value=True):
                kwargs = dict(base)
                kwargs.update(changed)
                call = {
                    key: kwargs.pop(key)
                    for key in list(kwargs)
                    if key in {
                        "research_features", "manifest_sha256", "stable_issuer_id",
                        "queue_receipt_path", "queue_receipt_sha256",
                    }
                }
                self.assertEqual(self._open(**kwargs, **call), 2)

    def test_dry_run_leaves_temporary_ledger_and_log_untouched(self):
        with open(self.ledger, "rb") as f:
            before = f.read()

        self.assertEqual(self._open(dry_run=True), 0)

        with open(self.ledger, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_live_600k_ledger_sets_48k_name_cap_not_stale_40k(self):
        self.assertEqual(self._open(dry_run=True, notional=47_000.0), 0)

    def test_missing_registered_capital_refuses_before_price_or_write(self):
        ledger = self._read_ledger()
        ledger.pop("contributed_capital_usd")
        self._write_ledger(ledger)
        before = self._read_ledger()

        self.assertEqual(self._open(), 1)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_non_usd_bloc_uses_actual_quote_currency_and_existing_notional(self):
        ledger = self._read_ledger()
        ledger["positions"]["EXIST"].update(
            {"notional_usd": 115_000.0, "entry_ccy": "ZAc"})
        self._write_ledger(ledger)
        self.live_info.update({"currency": "ZAc", "currentPrice": 125.0})

        self.assertEqual(self._open(dry_run=True, symbol="TEST.JO"), 2)

    def test_missing_actual_quote_currency_refuses_closed(self):
        self.live_info.pop("currency")
        self.assertEqual(self._open(dry_run=True), 1)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_provider_listing_currency_conflict_refuses_closed(self):
        self.live_info.update({"currency": "USD", "currentPrice": 125.0})
        self.assertEqual(self._open(dry_run=True, symbol="TEST.JO"), 1)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_major_minor_currency_conflict_refuses_closed(self):
        self.live_info.update({"currency": "ZAR", "currentPrice": 125.0})
        self.assertEqual(self._open(dry_run=True, symbol="TEST.JO"), 1)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_missing_existing_sector_refuses_before_price_or_write(self):
        ledger = self._read_ledger()
        ledger["positions"]["EXIST"].pop("sector")
        self._write_ledger(ledger)
        before = self._read_ledger()

        self.assertEqual(self._open(), 1)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_unknown_candidate_sector_refuses_before_write(self):
        self.assertEqual(self._open(sector="consumer defensive"), 2)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_existing_opposite_side_counts_toward_combined_sector_cap(self):
        ledger = self._read_ledger()
        ledger["positions"]["EXIST"].update({
            "side": "short", "sector": "consumer", "notional_usd": 115_000.0,
        })
        self._write_ledger(ledger)

        self.assertEqual(
            self._open(dry_run=True, notional=10_000.0, side="long",
                       sector="consumer"),
            2,
        )
        self.assertFalse(os.path.exists(self.entry_log))

    def test_existing_over_cap_sector_does_not_block_unrelated_sector(self):
        ledger = self._read_ledger()
        ledger["positions"]["EXIST"].update({
            "sector": "consumer", "notional_usd": 120_000.02,
        })
        self._write_ledger(ledger)

        self.assertEqual(self._open(dry_run=True, sector="consumer"), 2)
        self.assertEqual(self._open(dry_run=True, sector="technology"), 0)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_closed_venue_refuses_without_write(self):
        self.live_info["marketState"] = "CLOSED"
        before = self._read_ledger()

        self.assertEqual(self._open(), 2)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_authoritative_liquidity_failure_refuses_without_write(self):
        self.gate_result.update({
            "ok": False,
            "reasons": ["ADV below the authoritative floor"],
        })
        before = self._read_ledger()

        self.assertEqual(self._open(), 2)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_unmeasured_spread_refuses_even_when_research_gate_waives_it(self):
        self.gate_result["measured"]["spread_pct"] = None
        before = self._read_ledger()

        self.assertEqual(self._open(), 2)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))

    def test_previous_close_is_never_used_as_an_entry_fill(self):
        self.live_info.pop("currentPrice")
        self.live_info["previousClose"] = 125.0
        before = self._read_ledger()

        self.assertEqual(self._open(), 1)

        self.assertEqual(self._read_ledger(), before)
        self.assertFalse(os.path.exists(self.entry_log))


if __name__ == "__main__":
    unittest.main(verbosity=2)
