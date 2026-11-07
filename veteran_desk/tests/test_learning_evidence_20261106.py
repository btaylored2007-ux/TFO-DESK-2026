"""Learning evidence: real canonical outcome schemas, temporary files, no providers."""
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "learning_evidence.py"
SPEC = importlib.util.spec_from_file_location("staged_learning_evidence", MODULE)
LE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LE)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-evidence-")
        self.root = Path(self.temp.name)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.serial = 0

    def tearDown(self):
        self.temp.cleanup()

    def leg(self, id="TRADE-1", net=8.0, gross=10.0, **changes):
        row = {
            "id": id, "ticker": "TEST", "seat": "trend", "side": "LONG", "venue": "US",
            "grade": "A", "status": "closed", "fill": 100.0, "shares": 10,
            "filled_utc": (self.now - dt.timedelta(days=2)).isoformat(),
            "closed_utc": (self.now - dt.timedelta(days=1)).isoformat(),
            "net_aud": net, "gross_aud": gross, "cost_aud": 1.0,
        }
        row.update(changes)
        return row

    def book(self, rows=(), positions=None, paper=True):
        return {"paper_only": paper, "updated": self.now.isoformat(), "seats": {
            "trend": {"closed": list(rows), "positions": positions or {}}}}

    def put(self, source, data, adapter="seated_aud_v1"):
        path = self.root / (source + ".json")
        path.write_text(json.dumps(data))
        self.serial += 1
        ns = int(self.now.timestamp() * 1e9) + self.serial * 1000000
        os.utime(path, ns=(ns, ns))
        return {"id": source, "desk": source, "path": path, "adapter": adapter}

    def run_sync(self, sources):
        return LE.sync(root=self.root, now=self.now, sources=sources)

    def test_winners_and_losses_both_count_after_costs(self):
        source = self.put("thf", self.book([self.leg("WIN", net=8, gross=10),
                                            self.leg("LOSS", net=-12, gross=-10)]))
        result = self.run_sync([source])
        totals = result["totals"]
        self.assertEqual((totals["wins"], totals["losses"], totals["completed_trades"]), (1, 1, 2))
        self.assertEqual(totals["net_after_costs_aud"], -4)
        self.assertEqual(totals["fees_aud"], 4)
        self.assertEqual(len(result["desks"]["thf"]["examples"]["wins"]), 1)
        self.assertEqual(len(result["desks"]["thf"]["examples"]["losses"]), 1)

    def test_same_ids_in_different_books_do_not_collide(self):
        a = self.put("thf", self.book([self.leg(net=8)]))
        b = self.put("veteran", self.book([self.leg(net=-12, gross=-10)]))
        result = self.run_sync([a, b])
        self.assertEqual(result["totals"]["completed_trades"], 2)
        self.assertEqual(result["seats"]["thf/trend"]["net_after_costs_aud"], 8)
        self.assertEqual(result["seats"]["veteran/trend"]["net_after_costs_aud"], -12)

    def test_idempotent_refresh_has_no_duplicate_events_or_versions(self):
        source = self.put("thf", self.book([self.leg()]))
        a = self.run_sync([source])
        b = self.run_sync([source])
        self.assertEqual(a["totals"], b["totals"])
        self.assertEqual(b["changes"]["new_legs"], 0)
        self.assertEqual(b["changes"]["corrected_legs"], 0)
        self.assertEqual(b["changes"]["unchanged_sources"], 1)
        self.assertEqual(b["totals"]["evidence_versions"], 1)

    def test_rewritten_same_evidence_does_not_create_versions(self):
        data = self.book([self.leg()])
        source = self.put("thf", data)
        self.run_sync([source])
        source = self.put("thf", data)
        result = self.run_sync([source])
        self.assertEqual(result["changes"]["changed_sources"], 1)
        self.assertEqual(result["changes"]["corrected_legs"], 0)
        self.assertEqual(result["totals"]["evidence_versions"], 1)

    def test_corrected_outcome_upserts_and_preserves_old_version(self):
        source = self.put("thf", self.book([self.leg(net=8)]))
        self.run_sync([source])
        source = self.put("thf", self.book([self.leg(net=-12, gross=-10)]))
        result = self.run_sync([source])
        self.assertEqual(result["changes"]["corrected_legs"], 1)
        self.assertEqual(result["totals"]["completed_trades"], 1)
        self.assertEqual(result["totals"]["net_after_costs_aud"], -12)
        self.assertEqual(result["totals"]["evidence_versions"], 2)

    def test_missing_loss_is_retained_with_coverage_warning(self):
        source = self.put("thf", self.book([self.leg("W"), self.leg("L", net=-12, gross=-10)]))
        self.run_sync([source])
        source = self.put("thf", self.book([self.leg("W")]))
        result = self.run_sync([source])
        self.assertEqual(result["totals"]["net_after_costs_aud"], -4)
        self.assertEqual(result["totals"]["completed_trades"], 2)
        self.assertEqual(result["sources"][0]["missing_historical_rows"], 1)
        self.assertEqual(result["status"], "attention")

    def test_explicit_invalidated_slice_quarantines_parent(self):
        partial = self.leg("P-T1", shares=5, shares_orig=10)
        final = self.leg("P", shares=5, shares_orig=10)
        source = self.put("thf", self.book([partial, final]))
        self.run_sync([source])
        source = self.put("thf", self.book([dict(partial, status="cancelled"), final]))
        result = self.run_sync([source])
        self.assertEqual(result["changes"]["invalidated_legs"], 1)
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["quarantined_parent_groups"], 1)

    def test_cancelled_unfilled_and_shadow_results_are_excluded(self):
        rows = [self.leg("CANCEL", status="cancelled"), self.leg("NOFILL", fill=None),
                self.leg("SHADOW", shadow=True), self.leg("LAB", kind="strategy_lab")]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["accepted_close_legs"], 0)
        self.assertEqual(result["sources"][0]["excluded_rows"], 4)

    def test_open_position_never_becomes_an_outcome(self):
        row = self.leg("OPEN", status="open", net=10000000, gross=10000002)
        result = self.run_sync([self.put("thf", self.book([], {"OPEN": row}))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["net_after_costs_aud"], 0)

    def test_active_residual_excludes_partial_from_completed_trade_stats(self):
        slice = self.leg("P-P1", shares=5, shares_orig=10)
        active = self.leg("P", status="open", shares=5, shares_orig=10)
        result = self.run_sync([self.put("thf", self.book([slice], {"P": active}))])
        self.assertEqual(result["totals"]["partial_parents"], 1)
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["desks"]["thf"]["partial_reported_net_aud"], 8)

    def test_queued_partial_and_final_residual_form_one_trade(self):
        slice = self.leg("P-Q", shares=5, shares_orig=10, borrow_accrued_aud=1.5)
        active = self.leg("P", status="open", shares=5, shares_orig=10)
        source = self.put("thf", self.book([slice], {"P": active}))
        first = self.run_sync([source])
        self.assertEqual(first["totals"]["completed_trades"], 0)
        final = self.leg("P", shares=5, shares_orig=10, borrow_accrued_aud=2.0)
        source = self.put("thf", self.book([slice, final]))
        result = self.run_sync([source])
        self.assertEqual(result["totals"]["completed_trades"], 1)
        self.assertEqual(result["totals"]["accepted_close_legs"], 2)
        self.assertEqual(result["totals"]["financing_aud"], 2.0)
        self.assertEqual(result["totals"]["net_after_costs_aud"], 14.0)

    def test_cumulative_borrow_is_not_duplicated_across_partial_slices(self):
        rows = [self.leg("P-T1", shares=5, shares_orig=10, borrow_accrued_aud=3),
                self.leg("P", shares=5, shares_orig=10, borrow_accrued_aud=3)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["financing_aud"], 3)
        self.assertEqual(result["totals"]["net_after_costs_aud"], 13)

    def test_duplicate_identical_leg_is_counted_once(self):
        row = self.leg()
        result = self.run_sync([self.put("thf", self.book([row, dict(row)]))])
        self.assertEqual(result["totals"]["accepted_close_legs"], 1)
        self.assertEqual(result["sources"][0]["duplicate_rows"], 1)

    def test_conflicting_duplicate_id_is_quarantined(self):
        result = self.run_sync([self.put("thf", self.book([self.leg(), self.leg(net=7)]))])
        self.assertEqual(result["totals"]["accepted_close_legs"], 0)
        self.assertEqual(result["sources"][0]["quarantined_rows"], 1)

    def test_conflicting_parent_symbol_is_quarantined(self):
        rows = [self.leg("P-T1", shares=5, shares_orig=10),
                self.leg("P", ticker="OTHER", shares=5, shares_orig=10)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["quarantined_parent_groups"], 1)

    def test_bad_cost_or_nonfinite_net_is_quarantined(self):
        rows = [self.leg("NET", net=float("nan")), self.leg("FEE", cost_aud=-1),
                self.leg("NEG", net=12, gross=10), self.leg("MISSING", gross_aud=None)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["accepted_close_legs"], 0)
        self.assertEqual(result["sources"][0]["quarantined_rows"], 4)
        json.dumps(result, allow_nan=False)

    def test_missing_or_future_close_time_is_quarantined(self):
        rows = [self.leg("MISSING", closed_utc=None),
                self.leg("FUTURE", closed_utc=(self.now + dt.timedelta(hours=1)).isoformat()),
                self.leg("NAIVE", closed_utc=self.now.replace(tzinfo=None).isoformat())]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["sources"][0]["quarantined_rows"], 3)

    def test_unverified_fundamental_schema_is_coverage_only(self):
        data = {"paper_only": True, "closed": {"A": {"realised_usd": 100}},
                "recovery_closes": [{"realised_usd": 100}]}
        result = self.run_sync([self.put("fundamental", data, "unverified_json")])
        self.assertEqual(result["sources"][0]["status"], "quarantined")
        self.assertEqual(result["sources"][0]["quarantined_rows"], 2)
        self.assertEqual(result["totals"]["net_after_costs_aud"], 0)

    def test_nonpaper_book_is_not_learned(self):
        result = self.run_sync([self.put("thf", self.book([self.leg()], paper=False))])
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertEqual(result["totals"]["accepted_close_legs"], 0)

    def test_lost_source_cannot_invent_completed_partial_trade(self):
        slice = self.leg("P-T1", shares=5, shares_orig=10)
        active = self.leg("P", status="open", shares=5, shares_orig=10)
        source = self.put("thf", self.book([slice], {"P": active}))
        self.run_sync([source])
        source["path"].unlink()
        result = self.run_sync([source])
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["partial_parents"], 1)

    def test_hypotheses_never_promote_or_change_rails(self):
        rows = [self.leg(str(i), net=-2, gross=0) for i in range(3)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        hypothesis = result["hypotheses"][0]
        self.assertEqual(hypothesis["status"], "requires_preregistered_forward_test")
        self.assertFalse(hypothesis["may_change_risk_limits"])
        self.assertFalse(hypothesis["may_auto_promote"])
        self.assertEqual(result["totals"]["losses"], 3)

    def test_source_books_are_never_written(self):
        source = self.put("thf", self.book([self.leg()]))
        path = source["path"]
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.run_sync([source])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_context_is_bounded_and_labels_peer_as_peer(self):
        a = self.put("thf", self.book([self.leg("A")]))
        b = self.put("veteran", self.book([self.leg("B", net=-12, gross=-10)]))
        self.run_sync([a, b])
        packet = LE.context_for("trend", root=self.root, max_chars=4000)
        self.assertLessEqual(len(packet), 4000)
        self.assertIn("NOT your own THF", packet)
        self.assertIn("peer_veteran_history", packet)
        self.assertIn('"losses":1', packet)
        for budget in (0, 10, 400, 1000):
            self.assertLessEqual(len(LE.context_for("trend", root=self.root, max_chars=budget)), budget)

    def test_missing_or_invalid_status_fails_to_no_claim(self):
        packet = LE.context_for("trend", root=self.root)
        self.assertIn("Evidence unavailable", packet)
        path = self.root / "state/thf/autonomy/learning_status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        self.assertIn("Evidence unavailable", LE.context_for("trend", root=self.root))

    def test_historical_status_staleness_is_explicit(self):
        source = self.put("thf", self.book([self.leg()]))
        self.run_sync([source])
        path = self.root / "state/thf/autonomy/learning_status.json"
        status = json.loads(path.read_text())
        status["generated_utc"] = (self.now - dt.timedelta(hours=1)).isoformat()
        path.write_text(json.dumps(status))
        self.assertIn('"stale":true', LE.context_for("trend", root=self.root))

    def test_invalid_request_limits_are_refused(self):
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                LE.context_for("trend", root=self.root, max_chars=limit)

    def test_long_stable_id_is_not_truncated_into_another_event(self):
        rows = [self.leg("X" * 201), self.leg("X" * 200)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["accepted_close_legs"], 1)
        self.assertEqual(result["sources"][0]["quarantined_rows"], 1)

    def test_oversized_slice_quantity_cannot_double_count(self):
        rows = [self.leg("P-T1", shares=7, shares_orig=10),
                self.leg("P", shares=7, shares_orig=10)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["quarantined_parent_groups"], 1)

    def test_output_is_paper_advisory_and_has_full_source_hash(self):
        source = self.put("thf", self.book([self.leg()]))
        result = self.run_sync([source])
        self.assertTrue(result["paper_only"])
        self.assertTrue(result["advisory_only"])
        self.assertEqual(len(result["sources"][0]["sha256"]), 64)
        db = self.root / "state/thf/autonomy/learning.sqlite"
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM event_versions").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_prepaid_minimum_borrow_can_turn_recorded_win_into_loss(self):
        row = self.leg(net=5, gross=7, borrow_min_charge_aud_est=7)
        result = self.run_sync([self.put("thf", self.book([row]))])
        self.assertEqual(result["totals"]["wins"], 0)
        self.assertEqual(result["totals"]["losses"], 1)
        self.assertEqual(result["totals"]["financing_aud"], 7)
        self.assertEqual(result["totals"]["net_after_costs_aud"], -2)

    def test_partial_copies_deduplicate_both_prepaid_and_daily_borrow(self):
        rows = [self.leg("P-T1", net=5, gross=7, shares=5, shares_orig=10,
                         borrow_min_charge_aud_est=7, borrow_accrued_aud=2),
                self.leg("P", net=5, gross=7, shares=5, shares_orig=10,
                         borrow_min_charge_aud_est=7, borrow_accrued_aud=2)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["financing_aud"], 9)
        self.assertEqual(result["totals"]["net_after_costs_aud"], 1)

    def test_new_invalid_sibling_does_not_make_valid_residual_a_complete_trade(self):
        rows = [self.leg("P-T1", net_aud=None, shares=5, shares_orig=10),
                self.leg("P", shares=5, shares_orig=10)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["quarantined_parent_groups"], 1)

    def test_base_id_alone_does_not_prove_quantity_completion(self):
        row = self.leg("P", shares=5, shares_orig=10)
        result = self.run_sync([self.put("thf", self.book([row]))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["partial_parents"], 1)

    def test_grouped_slices_must_share_fill_execution(self):
        rows = [self.leg("P-T1", shares=5, shares_orig=10),
                self.leg("P", fill=101, shares=5, shares_orig=10)]
        result = self.run_sync([self.put("thf", self.book(rows))])
        self.assertEqual(result["totals"]["completed_trades"], 0)
        self.assertEqual(result["totals"]["quarantined_parent_groups"], 1)

    def test_symlinked_source_alias_does_not_duplicate_evidence(self):
        source = self.put("thf", self.book([self.leg()]))
        alias = self.root / "alias.json"
        alias.symlink_to(source["path"])
        other = dict(source, id="alias", desk="veteran", path=alias)
        with self.assertRaises(ValueError):
            self.run_sync([source, other])

    def test_hardlinked_source_alias_does_not_duplicate_evidence(self):
        source = self.put("thf", self.book([self.leg()]))
        alias = self.root / "alias.json"
        os.link(source["path"], alias)
        other = dict(source, id="alias", desk="veteran", path=alias)
        with self.assertRaises(ValueError):
            self.run_sync([source, other])

    def test_reinstated_outcome_adds_a_new_audit_version(self):
        source = self.put("thf", self.book([self.leg()]))
        self.run_sync([source])
        source = self.put("thf", self.book([self.leg(status="cancelled")]))
        invalid = self.run_sync([source])
        self.assertEqual(invalid["totals"]["invalidated_legs"], 1)
        source = self.put("thf", self.book([self.leg()]))
        restored = self.run_sync([source])
        self.assertEqual(restored["totals"]["completed_trades"], 1)
        self.assertEqual(restored["totals"]["evidence_versions"], 3)
        self.assertEqual(restored["totals"]["invalidated_legs"], 0)

    def test_adapter_revision_reprocesses_unchanged_source(self):
        source = self.put("thf", self.book([self.leg()]))
        self.run_sync([source])
        old = LE.ADAPTER_ACCOUNTING_REVISION
        try:
            LE.ADAPTER_ACCOUNTING_REVISION = old + 1
            result = self.run_sync([source])
        finally:
            LE.ADAPTER_ACCOUNTING_REVISION = old
        self.assertEqual(result["changes"]["changed_sources"], 1)
        self.assertEqual(result["sources"][0]["adapter_accounting_revision"], old + 1)
        self.assertEqual(result["totals"]["completed_trades"], 1)

    def test_nonfinite_prepaid_financing_is_not_assumed_zero(self):
        row = self.leg(borrow_min_charge_aud_est=float("inf"))
        result = self.run_sync([self.put("thf", self.book([row]))])
        self.assertEqual(result["sources"][0]["quarantined_rows"], 1)
        self.assertEqual(result["totals"]["completed_trades"], 0)

    def test_unverified_desk_performance_is_unknown_not_zero(self):
        source = self.put("fundamental", {"closed": {"A": {"realised_usd": 100}}}, "unverified_json")
        result = self.run_sync([source])
        self.assertIsNone(result["desks"]["fundamental"]["net_after_costs_aud"])
        self.assertIsNone(result["desks"]["fundamental"]["fees_aud"])
        self.assertIsNone(result["desks"]["fundamental"]["wins"])
        self.assertIn("unknown", result["desks"]["fundamental"]["evidence"])

    def test_context_exposes_withheld_outcome_quality_counts(self):
        rows = [self.leg("P-T1", net_aud=None, shares=5, shares_orig=10),
                self.leg("P", shares=5, shares_orig=10)]
        self.run_sync([self.put("thf", self.book(rows))])
        packet = LE.context_for("trend", root=self.root)
        self.assertIn('"quarantined_parent_groups":1', packet)
        self.assertIn('"quality":', packet)

    def test_known_completed_history_survives_temporary_source_loss(self):
        source = self.put("thf", self.book([self.leg()]))
        first = self.run_sync([source])
        source["path"].unlink()
        result = self.run_sync([source])
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertEqual(result["totals"]["net_after_costs_aud"], first["totals"]["net_after_costs_aud"])
        self.assertEqual(result["totals"]["completed_trades"], 1)
        self.assertEqual(result["status"], "attention")

    def test_coordinator_without_own_ledger_receives_balanced_global_evidence(self):
        a = self.put("thf", self.book([self.leg("THF-WIN", net=8, gross=10)]))
        b = self.put("veteran", self.book([self.leg("VD-LOSS", net=-12, gross=-10)]))
        self.run_sync([a, b])
        text = LE.context_for("head_trader", root=self.root, max_chars=4000)
        packet = json.loads(text[text.index("{"):])
        self.assertEqual(packet["own_thf_history"]["completed_trades"], 0)
        self.assertEqual(packet["peer_veteran_history"]["completed_trades"], 0)
        shared = packet["shared_cross_book"]
        self.assertEqual(shared["desks"]["thf"]["wins"], 1)
        self.assertEqual(shared["desks"]["veteran"]["losses"], 1)
        self.assertEqual(shared["verified_win"]["source_id"], "thf")
        self.assertEqual(shared["verified_win"]["seat"], "trend")
        self.assertEqual(shared["verified_loss"]["source_id"], "veteran")
        self.assertEqual(shared["verified_loss"]["net_after_costs_aud"], -12)
        self.assertTrue(shared["verified_win"]["trace_id"].startswith("outcome-"))
        self.assertIn("closed_utc", shared["verified_loss"])

    def test_ordinary_seat_gets_cross_seat_evidence_separate_from_own_record(self):
        source = self.put("veteran", self.book([
            self.leg("W"), self.leg("L", net=-12, gross=-10)]))
        self.run_sync([source])
        text = LE.context_for("crypto", root=self.root)
        packet = json.loads(text[text.index("{"):])
        self.assertEqual(packet["own_thf_history"]["completed_trades"], 0)
        self.assertEqual(packet["peer_veteran_history"]["completed_trades"], 0)
        self.assertEqual(packet["shared_cross_book"]["verified_win"]["seat"], "trend")
        self.assertEqual(packet["shared_cross_book"]["verified_loss"]["seat"], "trend")

    def test_unverified_source_cannot_supply_global_example(self):
        a = self.put("thf", self.book([self.leg("W"), self.leg("L", net=-12, gross=-10)]))
        b = self.put("fundamental", {"closed": {"A": {"realised_usd": 99999}}}, "unverified_json")
        self.run_sync([a, b])
        path = self.root / "state/thf/autonomy/learning_status.json"
        status = json.loads(path.read_text())
        # Even an injected generic peer 'net' example is not a vetted adapter.
        status["desks"]["fundamental"]["examples"]["wins"] = [{
            "desk": "fundamental", "seat": "trend", "parent_id": "NOT-VERIFIED",
            "ticker": "FAKE", "net_after_costs_aud": 99999, "closed_utc": self.now.isoformat()}]
        path.write_text(json.dumps(status))
        text = LE.context_for("coordinator", root=self.root)
        packet = json.loads(text[text.index("{"):])
        shared = packet["shared_cross_book"]
        self.assertNotIn("fundamental", shared["desks"])
        self.assertEqual(shared["verified_win"]["source_id"], "thf")
        self.assertNotIn("FAKE", text)

    def test_public_trace_packet_passes_existing_desk_secret_guard(self):
        # Actual desk.SECRET_RX contract; the filter itself must stay strict.
        import re
        secret_rx = re.compile(r"(sk-[A-Za-z0-9]{12,}|api[_-]?key\s*[:=]|secret\s*[:=]|password\s*[:=]|BEGIN [A-Z ]*PRIVATE KEY)", re.I)
        raw_id = "api_key=REDACTED_TEST_TOKEN_NOT_A_KEY"  # archive sanitize
        self.assertIsNotNone(secret_rx.search(raw_id))
        source = self.put("thf", self.book([self.leg(raw_id)]))
        self.run_sync([source])
        text = LE.context_for("coordinator", root=self.root)
        packet = json.loads(text[text.index("{"):])
        self.assertIsNone(secret_rx.search(text))
        self.assertNotIn(raw_id, text)
        expected_trace = "outcome-" + LE._hash(["thf", "trend", raw_id])[:12]
        self.assertEqual(packet["shared_cross_book"]["verified_win"]["trace_id"], expected_trace)
        # Full original identity remains in durable evidence for audit.
        with sqlite3.connect(self.root / "state/thf/autonomy/learning.sqlite") as conn:
            payload = json.loads(conn.execute("SELECT payload FROM close_events").fetchone()[0])
        self.assertEqual(payload["id"], raw_id)

    def test_four_thousand_budget_preserves_both_examples_and_complete_json(self):
        rows = [self.leg("W" * 200, ticker="SYMBOL" * 12),
                self.leg("L" * 200, ticker="SYMBOL" * 12, net=-12, gross=-10)]
        source = self.put("thf", self.book(rows))
        self.run_sync([source])
        path = self.root / "state/thf/autonomy/learning_status.json"
        status = json.loads(path.read_text())
        status["hypotheses"] = [{"source_seat": "thf/head_trader", "question": "Q" * 10000}]
        path.write_text(json.dumps(status))
        text = LE.context_for("head_trader", root=self.root, max_chars=4000)
        self.assertLessEqual(len(text), 4000)
        packet = json.loads(text[text.index("{"):])
        self.assertIsNotNone(packet["shared_cross_book"]["verified_win"])
        self.assertIsNotNone(packet["shared_cross_book"]["verified_loss"])
        self.assertEqual(packet["hypotheses"], [])

    def test_small_context_budget_never_slices_json_evidence(self):
        source = self.put("thf", self.book([self.leg("W"), self.leg("L", net=-12, gross=-10)]))
        self.run_sync([source])
        for budget in (400, 1000, 1600, 2000, 3000, 4000):
            text = LE.context_for("head_trader", root=self.root, max_chars=budget)
            self.assertLessEqual(len(text), budget)
            if "{" in text:
                packet = json.loads(text[text.index("{"):])
                self.assertIn("shared_cross_book", packet)

    def test_default_registry_is_explicit_and_schemas_are_not_guessed(self):
        specs = LE.default_sources(self.root)
        self.assertEqual([s["id"] for s in specs], ["thf", "veteran", "fundamental", "oanda_aggro"])
        self.assertEqual([s["adapter"] for s in specs[:2]], ["seated_aud_v1", "seated_aud_v1"])
        self.assertTrue(all(s["adapter"].startswith("unverified") for s in specs[2:]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
