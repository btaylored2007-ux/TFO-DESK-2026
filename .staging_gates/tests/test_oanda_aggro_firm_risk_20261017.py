"""17 Oct 2026 (Brett 10:29 AEDT, weekly cut review): OANDA snapshot labels AGGRO trades as AGGRO (not 'other (untouched)');
firm risk monitor counts (claims + reports) the AGGRO ledger without adding practice-account NAV to firm equity. Offline."""
import contextlib, importlib.util, io, json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import firm_risk


def load_sleeve():
    spec = importlib.util.spec_from_file_location("sleeve_label_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class SnapshotLabel(unittest.TestCase):
    def setUp(self): self.m = load_sleeve()

    def test_labels(self):
        L = self.m.owner_label
        self.assertEqual(L({"id": "61700", "clientExtensions": {"tag": "AGGRO", "comment": "OXT1"}}), "AGGRO")
        self.assertEqual(L({"id": "61697", "clientExtensions": {"tag": "TFO", "comment": "OXT1"}}), "TFO")
        self.assertEqual(L({"id": "61365", "clientExtensions": {}}), "Brett (pre-existing, untouched)")
        self.assertEqual(L({"id": "61368", "clientExtensions": {"tag": "AGGRO"}}), "Brett (pre-existing, untouched)")   # protected wins
        self.assertEqual(L({"id": "99999", "clientExtensions": {"tag": "X"}}), "other (untouched)")

    def test_snapshot_row_uses_label(self):
        m = self.m; tmp = tempfile.mkdtemp(); m.ST = tmp; m.LOG = os.path.join(tmp, "trades.jsonl"); m.CFG = os.path.join(tmp, "nocfg.json")
        m.summary = lambda: {"NAV": "50000", "balance": "50000", "marginUsed": "0", "marginAvailable": "50000", "unrealizedPL": "0", "openTradeCount": "1", "currency": "AUD"}
        m.open_trades = lambda: [{"id": "61700", "instrument": "BCO_USD", "currentUnits": "62", "price": "108.1", "openTime": "2026-10-16T10:48:49Z",
                                  "unrealizedPL": "308.8", "clientExtensions": {"tag": "AGGRO", "comment": "OXT1"}, "stopLossOrder": {"price": "91.38"}}]
        m.closed_tfo_trades = lambda: []
        def no_net(*a, **k): raise AssertionError("network")
        m.api = no_net
        snap = m.snapshot()
        if isinstance(snap, tuple): snap = snap[0]
        self.assertEqual(snap["trades"][0]["owner"], "AGGRO"); self.assertFalse(snap["trades"][0]["tfo"])


class AggroMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.stats = os.path.join(self.tmp.name, "stats.json")
        # 2026-10-27 audit: tests must not depend on live state/oanda reconcile health
        self._rg = mock.patch("oanda_reconcile_guard.board", return_value={
            "verified": True, "reconcile_status": "OK", "reconcile_first_fail_utc": None})
        self._rg.start(); self.addCleanup(self._rg.stop)

    def write(self, **kw):
        d = {"asof_utc": "2026-10-16T23:30:48+00:00", "nav": 50596.78, "start_nav": 50315.52, "total_ret_pct": 0.56, "week": "2026-W42",
             "week_ret_pct": 0.54, "week_target_pct": 5.0, "week_halted": False, "killed": False, "open_risk_pct": 4.98, "open_risk_cap_pct": 15.0,
             "margin_pct": 5.46, "aggro_upl_aud": 308.84,
             "open": [{"id": "61700", "ins": "BCO_USD", "dir": 1, "risk_frac": 0.0298, "owner": "AGGRO", "design": "OXT1", "risk_pct": 2.98},
                      {"id": "61697", "ins": "XCU_USD", "dir": 1, "risk_frac": 0.005, "owner": "TFO", "design": "OXT1", "risk_pct": 0.5}]}
        d.update(kw); json.dump(d, open(self.stats, "w"))

    def test_reads_rails_and_only_aggro_positions(self):
        self.write(); a = firm_risk.oanda_aggro_monitor(self.stats)
        self.assertFalse(a["in_firm_equity"]); self.assertEqual(a["warnings"], [])
        self.assertEqual([p["id"] for p in a["aggro_positions"]], ["61700"]); self.assertAlmostEqual(a["aggro_open_risk_pct"], 2.98)
        self.assertEqual(a["account_open_risk_pct"], 4.98); self.assertEqual(a["week_ret_pct"], 0.54)

    def test_warnings(self):
        self.write(open_risk_pct=16.0, killed=True, week_halted=True, margin_pct=70)
        w = " | ".join(firm_risk.oanda_aggro_monitor(self.stats)["warnings"])
        for s in ("16.00% > 15.00% cap", "HARD KILL", "weekly halt", "margin"): self.assertIn(s, w)
        self.write(); old = time.time() - 7 * 3600; os.utime(self.stats, (old, old))
        self.assertIn("stale", firm_risk.oanda_aggro_monitor(self.stats)["warnings"][0])

    def test_missing_or_bad_ledger_is_loud_not_raise(self):
        self.assertIn("missing", firm_risk.oanda_aggro_monitor(self.stats)["error"])
        open(self.stats, "w").write("{bad"); self.assertIn("unreadable", firm_risk.oanda_aggro_monitor(self.stats)["error"])

    def test_run_claims_ledger_and_firm_equity_unchanged(self):
        self.write()
        paths = {k: str(Path(self.tmp.name) / f) for k, f in [("STATE", "state.json"), ("REPORT", "report.json"), ("EQUITY_CSV", "equity.csv"),
                                                               ("LOCK", ".lock"), ("ERRORS", "errors.json")]}
        book = {"key": "a", "label": "A", "kind": "book", "error": None, "equity_usd": 100, "book_peak_usd": 100, "stale": False}
        g = {"books": [book], "errors": [], "firm_equity_usd": 100, "firm_equity_live_usd": 100, "stale_equity_usd": 0, "n_books_ok": 1, "n_books": 1, "n_positions": 0}
        cap = {"capital_event": False, "peak_shift_usd": 0, "capital_accounting_valid": True, "capital_accounting_errors": [], "capital_snapshot": {},
               "books_added": [], "books_removed": []}
        other = os.path.join(self.tmp.name, "other_ledger.json")
        with contextlib.ExitStack() as st:
            for k, v in paths.items(): st.enter_context(mock.patch.object(firm_risk, k, v))
            st.enter_context(mock.patch.object(firm_risk, "AGGRO_STATS", self.stats))
            st.enter_context(mock.patch.object(firm_risk.book_coverage, "gather", return_value=g))
            st.enter_context(mock.patch.object(firm_risk.book_coverage, "coverage_audit", return_value=([{"path": self.stats}, {"path": other}], [])))
            st.enter_context(mock.patch.object(firm_risk, "capital_event", return_value=cap))
            s = firm_risk._run_locked(False, previous={"firm_peak_usd": 100, "firm_peak_at_utc": "x", "book_keys": ["a"], "book_equity_usd": {"a": 100}})
            out = io.StringIO()
            with contextlib.redirect_stdout(out): firm_risk.report(s)
        self.assertEqual(s["unclaimed_ledgers"], [other])          # AGGRO claimed; a genuinely unclaimed ledger still reported
        self.assertEqual(s["firm_equity_usd"], 100)                 # practice-account NAV not added
        self.assertEqual(s["oanda_aggro"]["aggro_positions"][0]["ins"], "BCO_USD")
        self.assertIn("OANDA AGGRO", out.getvalue())


if __name__ == "__main__":
    unittest.main()
