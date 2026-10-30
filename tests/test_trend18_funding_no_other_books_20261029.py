"""TFO Risk 29 Oct 2026 14:08 WITA (Brett 3 Oct authority): Trend18 is funded from freed cash only, never by selling another
book's position, and only what is still needed at execution. Small gap (<= max(1% of deploy, A$2k)): trim the largest buy leg
or a reserve dip keeping >= 95% of the reserve; large gap: pro rata. PAPER ONLY."""
import os, sys, copy, types, datetime as dt, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend18 as T

FX = 0.7047

def row(inst, kind, side, qty, px, cur=0.0, leg="trend"):
    n = round(abs(qty) * px, 2)
    return {"inst": inst, "kind": kind, "leg": leg, "target_w": 0.1, "target_usd": n, "px": px, "target_qty": cur + qty,
            "current_qty": cur, "order_qty": qty, "side": side, "notional_usd": n, "cost_usd": round(n * T.COST_SIDE[kind], 2)}

def plan_28oct():
    """The 28 Oct 15:00 dry run: sell all UGL, buy ~1.478 BTC; cash need $51,039.39 vs $51,000 powder after the reserve."""
    return {"rows": [row("UPRO", "etf", "-", 0, 149.55, 1474), row("UGL", "etf", "SELL", -1587, 43.29, 1587),
                     row("XBT/USD", "crypto", "BUY", 1.47783, 80764.6, 1.15508), row("DBMF", "etf", "-", 0, 32.79, leg="mf")]}

def test_28oct_needs_39_and_closes_by_btc_trim():
    p = plan_28oct(); T._recalc(p, 0.0)
    assert abs(p["cash_need_usd"] - 51039.39) < 0.02
    p, d = T.resolve_funding(p, 0.0, 51000.0, 1745678.86 - 847956.49, FX)
    assert d["mode"] == "TRIM_LARGEST" and d["trim"]["inst"] == "XBT/USD"
    assert d["other_book_sales"] == [] and "sales" not in d
    assert p["cash_need_usd"] <= 51000.0 and d["reserve_dip_usd"] == 0.0 and d["reserve_intact_pct"] == 100.0
    btc = [r for r in p["rows"] if r["inst"] == "XBT/USD"][0]
    assert 1.4772 <= btc["order_qty"] < 1.47783          # ~0.0005 BTC (~$40) trimmed, nothing else touched
    assert [r for r in p["rows"] if r["inst"] == "UGL"][0]["order_qty"] == -1587
    assert d["small_threshold_usd"] == round(max(0.01 * 119356.35, 2000 * FX), 2)

def test_small_gap_reserve_dip_keeps_95pct():
    p, d = T.resolve_funding(plan_28oct(), 0.0, 51000.0, None, FX, prefer="dip")
    assert d["mode"] == "RESERVE_DIP" and 39 < d["reserve_dip_usd"] < 40
    assert d["reserve_after_usd"] >= 0.95 * T.RESERVE_USD
    assert [r for r in p["rows"] if r["inst"] == "XBT/USD"][0]["order_qty"] == 1.47783   # full buy kept

def test_dip_never_below_95pct_falls_back_to_trim():
    # gap ~$3,039 is small vs 1% of a bigger deploy but > 5% of the reserve: the dip is refused, the trim closes it
    p = plan_28oct(); p["rows"][2] = row("XBT/USD", "crypto", "BUY", 4.0, 80764.6, 1.15508)
    T._recalc(p, 0.0); avail = p["cash_need_usd"] - 3000.0
    p, d = T.resolve_funding(p, 0.0, avail, None, FX, prefer="dip")
    assert d["mode"] == "TRIM_LARGEST" and d["reserve_dip_usd"] == 0.0 and p["cash_need_usd"] <= avail

def test_large_gap_scales_buys_pro_rata():
    p = plan_28oct(); p["rows"].append(row("TQQQ", "etf", "BUY", 500, 71.04, 1875))
    p, d = T.resolve_funding(p, 0.0, 20000.0, None, FX)
    assert d["mode"] == "PRO_RATA" and 0 < d["fit_factor"] < 1
    assert p["cash_need_usd"] <= 20000.0 and d["reserve_dip_usd"] == 0.0 and d["other_book_sales"] == []
    for r in p["rows"]:
        if r["side"] == "BUY": assert r["fit_factor"] == d["fit_factor"]

def test_no_gap_untouched():
    p, d = T.resolve_funding(plan_28oct(), 0.0, 60000.0, None, FX)
    assert d["mode"] == "NONE" and [r for r in p["rows"] if r["inst"] == "XBT/USD"][0]["order_qty"] == 1.47783

def test_cap_gap_small_trims():
    p, d = T.resolve_funding(plan_28oct(), 0.0, 1e6, 50000.0, FX)   # net buy ~$50,655 vs cap room $50,000
    assert d["mode"] == "TRIM_LARGEST" and p["buys_usd"] - p["sells_usd"] <= 50000.0

def test_other_book_sales_refused():
    with pytest.raises(SystemExit, match="never sells another book"): T.sell_staged("AX", 39.39, execute=True)
    with pytest.raises(SystemExit, match="never sells another book"): T.cmd_sell_staged_only(types.SimpleNamespace(venue="AX", need_usd=None, execute_sales=True))
    with pytest.raises(SystemExit, match="never sells another book"): T.build_funding_plan(39.39, 0, [{"sym": "WES.AX"}])

def test_stage_and_execute_wiring():
    src = open(T.__file__).read()
    stage = src[src.index("def cmd_stage"):src.index("DOUBLE_FIRE_SESSIONS = 3")]
    assert "funding_candidates()" not in stage and "build_funding_plan(" not in stage and "resolve_funding(plan" in stage
    ex = src[src.index("def cmd_execute"):src.index("def mark_check")]
    assert "sell_staged(" not in ex and "resolve_funding(plan" in ex and 'resolve_funding({"rows": sells + buys}' in ex
    assert ex.index("resolve_funding(plan") < ex.index("desk_lock.ledger_lock")
    assert "EXEC_STAGE" in ex

def test_execute_sell_staged_flag_refused(monkeypatch):
    monkeypatch.setattr(T, "now_utc", lambda: dt.datetime(2026, 10, 30, 19, 50, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(T, "cmd_mark", lambda *a, **k: None)
    L = {"rebalances": [], "kill": {}, "holdings": {"XBT/USD": 1.15508, "UGL": 1587, "UPRO": 1474}, "scale_current": 0.5, "cash": 0.0}
    monkeypatch.setattr(T, "load_ledger", lambda: L)
    monkeypatch.setattr(T, "compute_signals", lambda asof, s: {"asof": asof, "sleeves": []})
    monkeypatch.setattr(T, "scale_up_guard", lambda *a: None)
    monkeypatch.setattr(T, "get_prices", lambda *a, **k: ({s: {"px": 1.0} for s in T.TREND_ETFS + T.MF}, "test"))
    monkeypatch.setattr(T, "kraken_btc", lambda *a, **k: {"bid": 80000.0, "ask": 80001.0, "tape": "LIVE", "spread_bp": 1, "dollar_vol24": 1e12})
    monkeypatch.setattr(T, "load_powder", lambda: {"cash": 101000.0, "reserved_usd": 0})
    monkeypatch.setattr(T, "firm_inputs", lambda g: {"avail": 51000.0, "longs": 0, "shorts": 0, "overlap": [], "E": 1.8e6, "src": "t", "cap_room": 1.7e6})
    monkeypatch.setattr(T, "plan_orders", lambda *a, **k: dict(T._recalc(plan_28oct(), 0.0), mf_target_usd=0.0, mf_cap_usd=0.0, mf_current_usd=0.0))
    monkeypatch.setattr(T, "room_report", lambda *a, **k: {"powder_available_after_reserve_usd": 51000.0, "cash_need_usd": 51039.39, "firm_long_after_usd": 0,
                                                          "invested_cap_usd": 1.7e6, "needed_from_sales_usd": 39.39, "firm_long_notional_usd": 0})
    monkeypatch.setattr(T, "print_signals", lambda *a: None); monkeypatch.setattr(T, "print_plan", lambda *a: None)
    a = types.SimpleNamespace(reason="manual", force=False, scale=1.0, signal_asof=None, base_usd=None, mf_powder=None, sell_staged=True, dry_run=True)
    with pytest.raises(SystemExit, match="never sells another book"): T.cmd_execute(a)
    out = {}
    monkeypatch.setattr(T, "atomic_json", lambda p, o: out.__setitem__(p, o))
    a.sell_staged = False
    T.cmd_execute(a)    # dry run: live re-computed stage written, BTC trimmed, nothing booked
    d = out[T.EXEC_STAGE]["funding_plan"]
    assert d["mode"] == "TRIM_LARGEST" and d["other_book_sales"] == [] and out[T.EXEC_STAGE]["dry_run"] is True

def _sandbox(monkeypatch, tmp_path, powder_cash, plan_fn):
    for k in ("LEDGER", "HIST", "FILLS", "PLAN", "ALERTS", "EXPO_PATH", "LOGF", "EXEC_STAGE", "STAGE_OUT"):
        monkeypatch.setattr(T, k, str(tmp_path / (k.lower() + ".json")))
    L = T.new_ledger(); L.update(units=100000.0, start=100000.0, scale_current=0.5)
    L["holdings"] = {"XBT/USD": 1.15508, "UGL": 1587.0, "UPRO": 1474.0}
    L["entry_px"] = {"XBT/USD": 70000.0, "UGL": 40.0, "UPRO": 140.0}; L["mark_px_usd"] = dict(L["entry_px"])
    G = {"cash": powder_cash, "reserved_usd": 0.0, "seed_usd": 1e6, "start": 1e6, "equity": powder_cash, "holdings": {}}
    monkeypatch.setattr(T, "now_utc", lambda: dt.datetime(2026, 10, 30, 19, 50, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(T, "cmd_mark", lambda *a, **k: None)
    monkeypatch.setattr(T, "load_ledger", lambda: L)
    monkeypatch.setattr(T, "compute_signals", lambda asof, s: {"asof": asof, "sleeves": []})
    monkeypatch.setattr(T, "scale_up_guard", lambda *a: None)
    monkeypatch.setattr(T, "get_prices", lambda *a, **k: ({s: {"px": 1.0} for s in T.TREND_ETFS + T.MF}, "test"))
    monkeypatch.setattr(T, "kraken_btc", lambda *a, **k: {"bid": 80764.5, "ask": 80764.6, "tape": "LIVE", "spread_bp": 1, "dollar_vol24": 1e12})
    monkeypatch.setattr(T, "load_powder", lambda: G); monkeypatch.setattr(T, "save_powder", lambda g: None)
    monkeypatch.setattr(T, "firm_inputs", lambda g: {"avail": float(g["cash"]) - T.RESERVE_USD, "longs": 0, "shorts": 0, "overlap": [], "E": 1.8e6, "src": "t", "cap_room": 1.7e6})
    monkeypatch.setattr(T, "plan_orders", lambda *a, **k: dict(T._recalc(plan_fn(), 0.0), mf_target_usd=0.0, mf_cap_usd=0.0, mf_current_usd=0.0))
    monkeypatch.setattr(T, "room_report", lambda *a, **k: {"powder_available_after_reserve_usd": 0, "cash_need_usd": 0, "firm_long_after_usd": 0,
                                                          "invested_cap_usd": 1.7e6, "needed_from_sales_usd": 0, "firm_long_notional_usd": 0})
    monkeypatch.setattr(T, "print_signals", lambda *a: None); monkeypatch.setattr(T, "print_plan", lambda *a: None)
    monkeypatch.setattr(T, "hist_append", lambda *a: None); monkeypatch.setattr(T, "exposure_snapshot", lambda *a, **k: {})
    monkeypatch.setitem(sys.modules, "theme_exposure", types.SimpleNamespace(enforcing=lambda: False, build=lambda: None, DESK_FX=0.7022, fx_audusd=lambda c: FX))
    return L, G

def test_full_execute_small_gap_books_trim_reserve_intact(monkeypatch, tmp_path):
    L, G = _sandbox(monkeypatch, tmp_path, 101000.0, plan_28oct)
    a = types.SimpleNamespace(reason="manual", force=False, scale=1.0, signal_asof=None, base_usd=None, mf_powder=None, sell_staged=False, dry_run=False, fit=True)
    T.cmd_execute(a)
    assert G["cash"] >= T.RESERVE_USD - 0.01                 # reserve fully intact after the trim
    assert L["cash"] >= -0.01 and L["holdings"].get("UGL") is None
    rb = L["rebalances"][-1]
    assert rb["funding"]["mode"] == "TRIM_LARGEST" and rb["funding_sales"] == []
    assert 1.15508 + 1.4772 <= L["holdings"]["XBT/USD"] < 1.15508 + 1.47783

def test_full_execute_large_gap_pro_rata_logged(monkeypatch, tmp_path):
    L, G = _sandbox(monkeypatch, tmp_path, 70000.0, plan_28oct)    # only $20k above the reserve -> large gap
    a = types.SimpleNamespace(reason="manual", force=False, scale=1.0, signal_asof=None, base_usd=None, mf_powder=None, sell_staged=False, dry_run=False, fit=True)
    T.cmd_execute(a)
    assert G["cash"] >= T.RESERVE_USD - 0.01
    rb = L["rebalances"][-1]
    assert rb["funding"]["mode"] == "PRO_RATA" and 0 < rb["fit_factor"] < 1 and rb["funding_sales"] == []
    assert "FUNDING_PRO_RATA" in open(T.ALERTS).read() and "FUNDING PRO_RATA" in open(T.LOGF).read()
