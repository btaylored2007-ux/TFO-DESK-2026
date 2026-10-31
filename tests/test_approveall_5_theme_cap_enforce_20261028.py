"""Brett 28 Oct 13:40 approve-all #5: theme cap ENFORCE (was warn-only). Blocks new buys only; never forces sales. PAPER."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme_exposure as te

def _d(cfg):
    pos = [te.pos("X", "UPRO", 400000.0), te.pos("X", "XLE", 1500000.0, staged=True), te.pos("X", "UGL", 10000.0)]
    return te.build(cfg=cfg, positions=pos, nav={"firm": 7_000_000.0})

def _cfg(mode="enforce"):
    c = te.load_cfg(); c["mode"] = mode; c["firm_nav_aud_override"] = None; return c

def test_live_config_enforces_20pct_of_budget():
    c = json.load(open(te.CFG_PATH))
    assert c["mode"] == "enforce" and c["theme_cap_frac_of_budget"] == 0.2

def test_blocks_capped_theme_and_passes_others():
    c = _cfg(); d = _d(c)
    assert d["mode"] == "enforce" and "us_tech" in d["blocks_new_buys_in"] and "energy_essentials" not in d["blocks_new_buys_in"]  # 2026-10-28 14:10: staged XLE = info only
    assert not te.check_buy("TQQQ", 5000, d=d, cfg=c)[0]
    assert te.check_buy("XLE", 5000, d=d, cfg=c)[0]   # staged no longer blocks (holdings only)
    assert te.check_buy("UGL", 5000, d=d, cfg=c)[0]
    assert te.check_buy("XBT/USD", 5000, d=d, cfg=c)[0]
    for mf in ("DBMF", "KMLM", "CTA"): assert te.check_buy(mf, 300000, d=d, cfg=c)[0]   # MF shock 0 -> never blocked

def test_would_push_over_is_blocked():
    c = _cfg(); d = _d(c)
    assert not te.check_buy("UGL", 5_000_000, d=d, cfg=c)[0]

def test_warn_mode_never_blocks():
    c = _cfg("warn"); d = _d(c)
    assert d["warn_only"] and d["blocks_new_buys_in"] == [] and te.check_buy("TQQQ", 5000, d=d, cfg=c)[0]

def test_trend18_deploy_legs_pass_now():
    # Live books are not in this tree, and a full live book can already sit on the theme cap.
    # This fixture has room under the cap. ENFORCE itself is unchanged (see test_blocks_capped_theme_and_passes_others).
    c = _cfg()
    pos = [te.pos("Trend18", "UGL", 10000.0), te.pos("Trend18", "XBT/USD", 10000.0)]
    d = te.build(cfg=c, positions=pos, nav={"Trend18": 7_000_000.0})
    for s, v in (("UGL", 100000), ("XBT/USD", 170000), ("DBMF", 332000), ("KMLM", 332000), ("CTA", 332000)):
        ok, why = te.check_buy(s, v, d=d, cfg=c)
        assert ok, (s, why)

def test_lse_pence_not_read_as_aud():
    d = te.build()
    big = [p for p in d["positions"] if "awaiting funder" in p["book"] and str(p["sym"]).endswith(".L")]
    assert all(p["value_aud"] < 200000 for p in big)

def test_essentials_fill_blocked_by_enforce():
    import tempfile, essentials_book as eb
    orig = te.check_buy; te.check_buy = lambda *a, **k: (False, "theme cap ENFORCE test")
    fills, lg = eb.FILLS, eb.log; eb.FILLS = os.path.join(tempfile.mkdtemp(), "f.csv"); eb.log = lambda m: None
    try:
        L = dict(cash_aud=eb.NAV0, positions={"U": dict(qty=1, mark_aud=200000.0, entry_px_aud=1.0, sector="Utilities")}, staged=[], fills=[], costs_paid_aud=0.0)
        assert eb._fill(L, dict(tk="Y", mkt="ASX", sleeve="core", target_aud=5000.0, ccy="AUD"), 10.0, {"AUD": 1.0}, "t") is None
        assert set(L["positions"]) == {"U"} and L["cash_aud"] == eb.NAV0   # nothing sold, nothing bought
    finally: te.check_buy = orig; eb.FILLS, eb.log = fills, lg
