"""tfo_usbook_cap_20261030: main-book US beta cap (40% of main-book equity), arm + fill + pre-trade gates. PAPER."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import us_book_cap as U

def test_cap_is_40pct_of_main_equity(monkeypatch):
    monkeypatch.setattr(U, "equity_parts", lambda: {"firm_ex_vd": 1_000_000.0, "tfo_v2": 500_000.0})
    assert U.cap_usd() == 900_000.0 and U.US_BETA_CAP_PCT == 0.60

def test_block_only_when_over_and_growing():
    ok, why, info = U.check("AAPL", 50_000, "long", cur=590_000, cap=600_000, others_usd=0)
    assert not ok and "us_book_cap" in why and info["total_usd"] == 640_000
    assert U.check("AAPL", 5_000, "long", cur=590_000, cap=600_000, others_usd=0)[0]
    assert U.check("SPY", 50_000, "short", cur=900_000, cap=600_000, others_usd=0)[0]      # reduces |beta| -> allowed
    assert U.check("SH", 50_000, "long", cur=900_000, cap=600_000, others_usd=0)[0]        # inverse ETF reduces
    assert not U.check("SOXL", 10_000, "long", cur=590_000, cap=600_000, others_usd=0)[0]  # 3x -> 30k
    assert U.check("BHP.AX", 10**9, "long", cur=10**9, cap=1, others_usd=0)[0]             # non-US -> n/a
    assert U.check("BTC/USD", 10**9, "long", cur=10**9, cap=1, others_usd=0)[0]

def test_run_accumulates_earlier_passes():
    U.reset_run()
    assert U.check("AAPL", 300_000, "long", cur=0, cap=500_000)[0]
    ok, why, _ = U.check("MSFT", 300_000, "long", cur=0, cap=500_000)
    assert not ok and "US$300000" in why.replace(",", "")
    U.reset_run()

def test_fail_closed(monkeypatch):
    def boom(*a, **k): raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(U, "rows", boom)
    ok, why, _ = U.check("AAPL", 1, "long", cap=10**9, others_usd=0)
    assert not ok and "fail closed" in why

def test_rows_scope(monkeypatch):
    import theme_exposure as te
    P = [te.pos("Trend18 (US$)", "UPRO", 100_000, ccy="USD"), te.pos("TREND-CORE", "SPY", 100_000, ccy="USD"),
         te.pos("ESSENTIALS", "XLU", 100_000, ccy="USD"), te.pos("OANDA", "NAS100_USD", 100_000, ccy="USD"),
         te.pos("Hunt short (armed)", "MORN", 10_000, staged=True), te.pos("Hunt x", "AMD", 1, src="state/funder_queue.json"),
         te.pos("Fundamental", "7203.T", 50_000), te.pos("Crypto x", "BTC", 1, ccy="USD"), te.pos("TFO V2", "XLE", 20_000, side="short", ccy="USD")]
    monkeypatch.setattr(te, "collect", lambda cfg, fx: (P, {}, []))
    monkeypatch.setattr(te, "fx_audusd", lambda cfg: 0.5)
    R = U.rows()
    assert sorted((b, s, round(u)) for b, s, u, *_ in R) == sorted([("Trend18 (US$)", "UPRO", 150_000), ("Hunt short (armed)", "MORN", 5_000), ("TFO V2", "XLE", -10_000)])
    assert [r for r in U.rows(exclude=[("Hunt short", "MORN")]) if r[1] == "MORN"] == []
    monkeypatch.setattr(te, "collect", lambda cfg, fx: ([], {}, ["hunts: boom"]))
    try: U.rows(); assert False
    except RuntimeError: pass

def test_hunt_arm_and_fill_hooks(monkeypatch):
    import hunt48_short_paper as H
    calls = []
    def fake(sym, usd, side, exclude, others):
        calls.append((sym, usd, side, list(exclude), others))
        tot = others + usd
        return (tot <= 100_000, "us_book_cap: blocked", dict(new_usd=usd))
    monkeypatch.setattr(H, "US_CAP_FN", fake)
    L = {"trades": [dict(ticker="OLD", status="ARMED", direction="long")]}
    run = [0.0]
    assert H.us_cap_arm_block(dict(ticker="AAA", max_fill=100, shares=600, direction="long"), L, run) is None and run[0] == 60_000
    assert H.us_cap_arm_block(dict(ticker="BBB", max_fill=100, shares=600, direction="long"), L, run) == "us_book_cap: blocked"
    assert run[0] == 60_000
    H.us_cap_arm_block(dict(ticker="OLD", max_fill=10, shares=1), L, [0.0])
    assert calls[-1][3] == [(H._hunt_book_label(), "OLD")] and calls[-1][2] == "long"   # re-level excludes own ARMED row
    assert H.us_cap_fill_block(dict(ticker="CCC", entry=10.0, shares=100, direction="short")) is None
    assert H.us_cap_fill_block(dict(ticker="CCC", entry=1000.0, shares=1000)) == "us_book_cap: blocked"
    assert calls[-1][3] == [(H._hunt_book_label(), "CCC")]

def test_source_hooks_present():
    for f, needle in (("tfo_buy.py", "_ubc.check(sym, usd"), ("tfo_v2.py", "us_book_cap.check(sym, usd"), ("trend18.py", "_ubc.check(r[\"inst\"]"),
                      ("fundamental_open_new.py", "us_book_cap.check(symbol"), ("hunt48_short_paper.py", "us_cap_fill_block(t)")):
        assert needle in open(os.path.join(ROOT, f)).read(), f

def test_v2_theme_gate_blocks(monkeypatch):
    import tfo_v2
    monkeypatch.setattr(tfo_v2, "us_tech_red", lambda: False)
    monkeypatch.setattr(U, "check", lambda *a, **k: (False, "us_book_cap: over", {})); import theme_exposure as te; monkeypatch.setattr(te, "check_buy", lambda *a, **k: (True, "ok"))
    ok, why = tfo_v2.theme_gate("XLE", 10_000, "energy")
    assert not ok and "us_book_cap" in why

# ---- tfo_capfix_20261030 (approved by HT 20:12 WITA) ----
def _rows_with(monkeypatch, P):
    import theme_exposure as te
    monkeypatch.setattr(te, "collect", lambda cfg, fx: (P, {}, []))
    monkeypatch.setattr(te, "fx_audusd", lambda cfg: 1.0)
    return {s: round(u) for b, s, u, *_ in U.rows()}

def test_capfix_shorts_reduce_exposure(monkeypatch):
    import theme_exposure as te
    R = _rows_with(monkeypatch, [te.pos("Hunt short (armed)", "MORN", 80_000, side="short", staged=True, ccy="USD"),
                                 te.pos("Fundamental", "AAPL", 100_000, ccy="USD")])
    assert R == {"MORN": -80_000, "AAPL": 100_000} and sum(R.values()) == 20_000
    assert U.leg_usd("SPY", 10_000, "short") == -10_000

def test_capfix_ugl_tmf_excluded(monkeypatch):
    import theme_exposure as te
    R = _rows_with(monkeypatch, [te.pos("Trend18 (US$)", "UGL", 67_000, ccy="USD"), te.pos("Trend18 (US$)", "TMF", 50_000, ccy="USD"),
                                 te.pos("Trend18 (US$)", "UPRO", 100_000, ccy="USD")])
    assert R == {"UPRO": 300_000}
    assert U.check("UGL", 10**9, "long", cur=10**9, cap=1, others_usd=0)[0] and U.check("TMF", 10**9, "long", cur=10**9, cap=1, others_usd=0)[0]

def test_capfix_equity_longs_unchanged(monkeypatch):
    import theme_exposure as te
    R = _rows_with(monkeypatch, [te.pos("Trend18 (US$)", "TQQQ", 100_000, ccy="USD"), te.pos("Fundamental", "SPY", 50_000, ccy="USD"),
                                 te.pos("TFO V2", "XLE", 20_000, ccy="USD"), te.pos("Fundamental", "BRK.B", 10_000, ccy="USD")])
    assert R == {"TQQQ": 300_000, "SPY": 50_000, "XLE": 20_000, "BRK.B": 10_000}

def test_capfix_missing_side_conservative(monkeypatch):
    import theme_exposure as te
    p = te.pos("Hunt short (armed)", "VDE", 40_000, side=None, staged=True, ccy="USD")
    assert _rows_with(monkeypatch, [p]) == {"VDE": 40_000}
    assert U.leg_usd("AAPL", 1_000, None) == 1_000
