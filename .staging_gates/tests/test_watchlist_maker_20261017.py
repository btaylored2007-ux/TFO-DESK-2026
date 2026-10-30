"""Brett 17 Oct 2026 item 1: watchlist + overnight2k crypto entries post-only maker; both read resting orders."""
import os, sys, types
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "overnight2k"))
import tfo_watchlist as W, tfo_crypto as TC
import overnight2k as O


def test_tool_argv_post_only_crypto_only(monkeypatch):
    monkeypatch.setattr(W, "POST_ONLY_CRYPTO", True)
    c = {"symbol": "SOLUSD", "theme": "t", "label": "beta", "why": "x"}
    assert "--post-only" in W.tool_argv("CRYPTO", c, 20000, True)
    assert "--post-only" not in W.tool_argv("US", dict(c, symbol="AAPL"), 20000, True)
    monkeypatch.setattr(W, "POST_ONLY_CRYPTO", False)
    assert "--post-only" not in W.tool_argv("CRYPTO", c, 20000, True)


def test_run_tool_reads_resting(monkeypatch):
    out = '{"pair": "SOLUSD", "gate_fails": []}\nRESTING tfo_ht_crypto SOLUSD post-only BUY qty 1 @ 100 (bid) usd 20000 ttl 900s ticket HT-abc\n'
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""))
    r = W.run_tool(["x"])
    assert r["passed"] and r["logs"] == [] and r["resting"][0].endswith("ticket HT-abc")


def _patch_attempt(monkeypatch):
    monkeypatch.setattr(W.v1_venues, "entry_block_reason", lambda s, n: None)
    monkeypatch.setattr(W, "legacy_held", lambda: (set(), None))
    monkeypatch.setattr(W, "fire_trend", lambda mk, c, n: ([], 0.03))
    monkeypatch.setattr(W, "firm_snapshot", lambda: {"firm_equity_usd": 1e6, "headroom_notional_usd": 1e6, "unreserved_powder_usd": 1e6})
    monkeypatch.setattr(W, "vol_size", lambda e, a: (50000.0, None))
    monkeypatch.setattr(W, "fee_est", lambda *a: (10.0, "x"))
    monkeypatch.setattr(W, "planned_loss_cap", lambda mk, a: None)
    monkeypatch.setattr(W, "fire_news", lambda mk, c, n: ([], [], {}))
    monkeypatch.setattr(W, "tag_fill", mock.Mock())


def test_plan_attempt_resting_counts_as_fired(monkeypatch):
    _patch_attempt(monkeypatch)
    rest = "RESTING tfo_ht_crypto SOLUSD post-only BUY qty 1 @ 100 (bid) usd 20000 ttl 900s ticket HT-abc"
    runner = lambda argv: {"passed": True, "fails": [], "info": {"px_usd": 100}, "logs": [], "resting": [rest] if "--execute" in argv else [], "stdout": ""}
    c = {"symbol": "SOLUSD", "theme": "t", "label": "beta", "why": "x", "ref_price_usd": None}
    st, logs = W._plan_attempt("CRYPTO", c, 20000, True, runner, W.now_utc(), "s")
    assert st == "FILLED" and logs == [rest]
    W.tag_fill.assert_not_called()


def test_resting_crypto_reads_book_and_fire_loop_skips(monkeypatch):
    fake = types.SimpleNamespace(book=lambda: {"resting_entries": {"SOL/USD": {}}, "positions": {}})
    monkeypatch.setattr(W, "desk", lambda name: fake)
    assert W._resting_crypto("SOL/USD") is True and W._resting_crypto("ETH/USD") is False
    src = open(W.__file__).read()   # gate F5: the guard sits in the CRYPTO fire loop, before any tool run
    i = src.index('if mk == "CRYPTO" and POST_ONLY_CRYPTO and _resting_crypto(c["symbol"])')
    assert i < src.index('fill_setup = setup or c.get("setup") or default_setup')


def test_pending_setup_attribution(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "PENDING_SETUPS", str(tmp_path / "p.jsonl"))
    monkeypatch.setattr(W, "wl_lock", lambda name: mock.MagicMock())
    W.note_pending_setup("CRYPTO", {"symbol": "SOL/USD", "theme": "t", "label": "beta"}, "RESTING tfo_ht_crypto SOL/USD post-only BUY ... ticket HT-xyz", "crypto_hunt", W.now_utc())
    assert W.pending_setups()["HT-xyz"]["setup"] == "crypto_hunt"


def test_heat_and_gates_count_resting():
    import tfo_heat
    cb = {"positions": {}, "resting_entries": {"SOL/USD": {"usd": 20000.0, "c3_stop_dist": 0.06}}}
    h, rows = tfo_heat.clip_heat({}, cb)
    assert abs(h - 1200.0) < 1e-6 and rows[0]["resting"]
    src = open(TC.__file__).read()
    assert 'if pair in b["positions"] or pair in rest: fails.append("already held")' in src


def test_fill_entry_maker_exits_only_for_maker_themes():
    src = open(TC.__file__).read()
    assert '"maker_exits": r["theme"] in MAKER_THEMES}' in src


def test_resting_expires_on_clock_when_feed_down(monkeypatch):
    import datetime as dt
    now = dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc)
    placed = now - dt.timedelta(seconds=3 * TC.MAKER_ENTRY_TTL_S)
    b = {"resting_entries": {"SOL/USD": {"ticket_id": "HT-1", "placed_utc": placed.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "expires_utc": (placed + dt.timedelta(seconds=TC.MAKER_ENTRY_TTL_S)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "limit_px": 100.0, "qty": 1.0, "usd": 100.0, "reserved_usd": 100.25}}}
    monkeypatch.setattr(TC, "trades_since", mock.Mock(side_effect=RuntimeError("down")))
    xf = mock.Mock(); monkeypatch.setattr(TC, "xfer", xf)
    TC.resting_entries(b, {}, now, True, [])
    assert b["resting_entries"] == {} and b["resting_log"][-1]["outcome"] == "expired" and xf.called


def test_tfo_crypto_post_only_flag_routes_to_post_entry(monkeypatch):
    monkeypatch.setattr(TC, "gates", lambda p, u, t: ([], {"bid": 1, "ask": 1}, {}))
    monkeypatch.setattr(TC, "risk_size", lambda *a, **k: {"bound": False, "skip": False, "usd_requested": 20000})
    monkeypatch.setattr(TC.risk_caps, "describe", lambda rk: ""); monkeypatch.setattr(TC.risk_caps, "log_bind", lambda *a, **k: None)
    pe = mock.Mock(); monkeypatch.setattr(TC, "_post_entry", pe)
    TC.cmd_buy("SOLUSD", 20000, "watch-theme", "beta", "w", True, post_only=True)
    assert pe.called


def _state(status="resting"):
    return {"clips": [{"pair": "SOLUSD", "ticket_id": "HT-abc", "status": status, "principal_usd": 20000}]}


def _tk():
    return {"ticket_id": "HT-abc", "action": "BUY", "utc": "2026-10-17T03:00:00Z", "px_usd": 100.0, "qty": 200.0, "principal_usd": 20000, "fees_usd": 50.0}


def test_overnight_reconcile_promotes_on_fill(monkeypatch):
    monkeypatch.setattr(O, "audit", lambda *a, **k: None); monkeypatch.setattr(O, "event", lambda *a, **k: None)
    b = {"head_trader_tickets": [_tk()], "resting_entries": {}, "positions": {"SOLUSD": {"entry_ticket": _tk(), "entry_utc": "2026-10-17T03:00:00Z", "entry_px": 100.0, "qty": 200.0}}}
    s = _state(); O.reconcile(s, b); c = s["clips"][0]
    assert c["status"] == "open" and c["qty_open"] == 200.0 and c["entry_fees_usd"] == 50.0 and c["stop_px"] == round(100 * (1 - O.CLIP_STOP), 10)


def test_overnight_fill_then_stopped_is_not_unfilled(monkeypatch):
    """gate F1: filled AND already stopped out before overnight2k looked -> closed with its real loss, never 'unfilled'."""
    monkeypatch.setattr(O, "audit", lambda *a, **k: None); monkeypatch.setattr(O, "event", lambda *a, **k: None)
    stop = {"ticket_id": "HT-s", "action": "STOP", "pair": "SOLUSD", "utc": "2026-10-17T03:05:00Z", "qty": 200.0, "px_usd": 95.0, "pnl_usd": -1000.0, "fees_usd": 76.0}
    b = {"head_trader_tickets": [_tk(), stop], "resting_entries": {}, "positions": {}}
    s = _state(); O.reconcile(s, b); c = s["clips"][0]
    assert c["status"] == "closed" and c["realised_usd"] == round(-1000.0 - 76.0 - 50.0, 2)


def test_overnight_reconcile_keeps_and_retires(monkeypatch):
    monkeypatch.setattr(O, "audit", lambda *a, **k: None); monkeypatch.setattr(O, "event", lambda *a, **k: None)
    s = _state(); O.reconcile(s, {"positions": {}, "resting_entries": {"SOLUSD": {"ticket_id": "HT-abc"}}, "head_trader_tickets": []})
    assert s["clips"][0]["status"] == "resting" and O.resting_clips(s)
    O.reconcile(s, {"positions": {}, "resting_entries": {}, "head_trader_tickets": []})
    assert s["clips"][0]["status"] == "unfilled" and not O.resting_clips(s)


def test_cmd_fire_crypto_rests_then_skips_on_rerun(monkeypatch, tmp_path):
    """gate N5: behavioural - the live CRYPTO fire path rests once, records the setup, and a re-run never re-posts."""
    _patch_attempt(monkeypatch)
    monkeypatch.setattr(W, "POST_ONLY_CRYPTO", True)
    monkeypatch.setattr(W, "PENDING_SETUPS", str(tmp_path / "p.jsonl"))
    monkeypatch.setattr(W, "wl_lock", lambda *a, **k: mock.MagicMock())
    monkeypatch.setattr(W.v1_venues, "market_block_reason", lambda mk, n: None)
    now = W.now_utc()
    m = {"status": "BUILT", "built_at": {"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
         "candidates": [{"symbol": "SOL/USD", "theme": "t", "label": "beta", "why": "x", "clip_usd": 20000.0, "fees_est_usd": 50.0, "ref_price_usd": None}]}
    monkeypatch.setattr(W, "read_doc", lambda: {"markets": {"CRYPTO": m}})
    monkeypatch.setattr(W, "firm_snapshot", lambda: {"firm_equity_usd": 1e6, "headroom_notional_usd": 1e6, "unreserved_powder_usd": 1e6, "crypto_stack_room_usd": 1e6})
    book = {"resting_entries": {}, "positions": {}}
    monkeypatch.setattr(W, "desk", lambda name: types.SimpleNamespace(book=lambda: book))
    calls = []
    rest = "RESTING tfo_ht_crypto SOL/USD post-only BUY qty 200 @ 100 (bid) usd 20000 ttl 900s ticket HT-n5"
    def runner(argv):
        calls.append(argv)
        if "--execute" in argv: book["resting_entries"]["SOL/USD"] = {"ticket_id": "HT-n5"}
        return {"passed": True, "fails": [], "info": {"px_usd": 100}, "logs": [], "resting": [rest] if "--execute" in argv else [], "stdout": ""}
    out = W.cmd_fire("CRYPTO", True, 1, runner=runner)
    assert out == [rest] and "--post-only" in calls[-1] and W.pending_setups()["HT-n5"]["setup"] == "watchlist_momentum_crypto"
    n = len(calls)
    assert W.cmd_fire("CRYPTO", True, 1, runner=runner) == [] and len(calls) == n   # re-run: DONE skip, no tool call


def test_cancel_resting_returns_reservation(monkeypatch):
    b = {"resting_entries": {"SOL/USD": {"ticket_id": "HT-1", "placed_utc": "x", "limit_px": 100.0, "qty": 1.0, "usd": 100.0, "reserved_usd": 100.25}},
         "positions": {}, "holdings": {}, "mark_px_usd": {}, "entry_px": {}, "cash": 500.0, "equity": 600.25}
    monkeypatch.setattr(TC, "book", lambda: b); monkeypatch.setattr(TC, "load", lambda k: {})
    monkeypatch.setattr(TC, "save", lambda *a: None); xf = mock.Mock(); monkeypatch.setattr(TC, "xfer", xf)
    monkeypatch.setattr(TC.desk_lock, "ledger_lock", lambda *a, **k: mock.MagicMock())
    assert TC.cmd_cancel_resting("SOL/USD", "overnight2k night_stop", True) is True
    assert b["resting_entries"] == {} and b["resting_log"][-1]["outcome"] == "caller_cancel" and xf.call_args.args[2] == 100.25
    assert b["equity"] == 500.0   # gate M2: re-struck from cash (xfer is mocked, so cash is unchanged here)
    assert TC.cmd_cancel_resting("SOL/USD", "again", True) is False


def test_overnight_flatten_cancels_resting_first():
    src = open(O.__file__).read()
    assert src.index("for c in resting_clips(state):   # gate N1") < src.index('sell(state, c, "overnight2k %s: night P&L')
    assert "(open_clips(state) or resting_clips(state)) and state.get(\"flatten_reason\") is None" in src
    assert "gate r4#3" in src


def test_post_entry_restrikes_equity(monkeypatch):
    """gate r4#1: placing a resting entry leaves crypto book equity whole (cash moved in == reservation)."""
    b = {"resting_entries": {}, "positions": {}, "holdings": {}, "mark_px_usd": {}, "entry_px": {}, "cash": 0.0, "equity": 0.0}
    monkeypatch.setattr(TC, "book", lambda: b); monkeypatch.setattr(TC, "load", lambda k: {}); monkeypatch.setattr(TC, "save", lambda *a: None)
    monkeypatch.setattr(TC.desk_lock, "ledger_lock", lambda *a, **k: mock.MagicMock())
    monkeypatch.setattr(TC.desk_costs, "fill_fees", lambda *a, **k: {"fees_usd": 50.0})
    def xfer(b_, g_, amt, to_powder, *a): b_["cash"] = b_["cash"] + (-amt if to_powder else amt)
    monkeypatch.setattr(TC, "xfer", xfer)
    TC._post_entry("SOL/USD", 20000.0, "t", "beta", "w", {"bid": 100.0}, {})
    assert "SOL/USD" in b["resting_entries"] and b["equity"] == b["cash"] == 20050.0
