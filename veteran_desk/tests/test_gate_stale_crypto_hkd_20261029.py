"""2026-10-29: stale-bar freshen, crypto mapping, HKD sizing reject, no-history hard reject, no-chase pullback."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("PYTEST_CURRENT_TEST", "1")  # disable live freshen in unit tests
from veteran_desk import gate_v2 as G, pit as P, desk as D, book as BK

def test_crypto_canon_maps_bare_btc_to_kraken():
    assert G.canon("BTC") == "KRAKEN:XBTUSD"
    assert G.canon("ETH") == "KRAKEN:ETHUSD"
    assert G.canon("XRP") == "KRAKEN:XRPUSD"
    assert G.canon("AAPL") == "AAPL"

def test_crypto_history_atr_is_coin_scale_not_etf():
    now = dt.datetime.now(dt.timezone.utc)
    rows, _ = G.history("BTC", now)
    A = G.arrays(rows)
    assert float(A["c"][-1]) > 1000, "BTC must resolve to coin price not ~$35 ETF"
    assert float(A["atr"][-1]) > 100

def test_hkd_sizing_hard_reject():
    # fxsize 2026-10-29: HKD/JPY now converted at live FX; the reject is a backstop for missing FX / unknown suffix only
    from veteran_desk import book as BK
    _lp, _ib = BK.live_px, BK._ibkr_fx; BK.live_px = lambda s: None; BK._ibkr_fx = lambda c: None; BK._FX.clear()
    try:
        assert G.ccy_issue("2800.HK", 0.70) and G.ccy_issue("8411.T", 0.70) and G.ccy_issue("FOO.ZZ", 0.70)
        g0 = G.gate(dict(ticker="2800.HK", side="SHORT", entry=23.85, stop=25.3, target=20.95, grade="A"), None, dict(call="SHORT"), dict(factual_kill=False),
                    dt.datetime.now(dt.timezone.utc), env=dict(book=([], None), firm="OK"), hist=([], "empty"), au=0.70)
        assert any("fail closed" in r for r in g0["reasons"])
    finally: BK.live_px, BK._ibkr_fx = _lp, _ib; BK._FX.clear()
    assert G.ccy_issue("FPH.AX") is None
    assert G.ccy_issue("AAPL") is None
    now = dt.datetime.now(dt.timezone.utc)
    g = G.gate(dict(ticker="2800.HK", side="SHORT", entry=23.85, stop=25.3, target=20.95, grade="A"),
               None, dict(call="SHORT"), dict(factual_kill=False), now, env=dict(book=([], None), firm="OK"),
               hist=([], "empty"))
    assert not g["passed"]
    assert not g["passed"]   # empty PIT history still hard-rejects

def test_no_pit_history_hard_reject():
    now = dt.datetime.now(dt.timezone.utc)
    g = G.gate(dict(ticker="ZZZZNOPE.AX", side="LONG", entry=10, stop=9, target=12, grade="A"),
               None, None, None, now, env=dict(book=([], None), firm="OK"), hist=([], "empty"))
    assert not g["passed"]
    assert any("no point-in-time" in r for r in g["reasons"])

def test_jp_knowable_is_tokyo_not_us_close():
    k = P.knowable("2026-10-28", "US", "8411.T")
    assert k.startswith("2026-10-28T07:00:00"), k  # 15:30 JST + 30m = 07:00 UTC

def test_live_check_allows_pullback_buy_limit():
    # ISRG case: buy-limit 436, live 444.19 (~1.9% above) must be allowed
    why = D.live_check(dict(ticker="ISRG", side="LONG", entry=436.0, stop=420.0, target=460.0),
                       px_fn=lambda t: dict(last=444.19))
    assert why is None, why
    # mirror sell-limit below market (rally wait)
    why = D.live_check(dict(ticker="ISRG", side="SHORT", entry=450.0, stop=460.0, target=420.0),
                       px_fn=lambda t: dict(last=440.0))
    assert why is None, why
    # through stop still rejected
    why = D.live_check(dict(ticker="ISRG", side="LONG", entry=436.0, stop=420.0, target=460.0),
                       px_fn=lambda t: dict(last=419.0))
    assert why and "stop" in why

def test_seat_cli_tick_minute_guard():
    from veteran_desk import seat_cli as SC
    assert SC.tick_minute(dt.datetime(2026, 10, 29, 10, 0))
    assert SC.tick_minute(dt.datetime(2026, 10, 29, 10, 5))
    assert not SC.tick_minute(dt.datetime(2026, 10, 29, 10, 3))

def test_live_check_blocks_stop_limit_through():
    # stop-entry already through by >1% must block
    why = D.live_check(dict(ticker="ISRG", side="LONG", entry=436.0, stop=420.0, target=460.0, order_type="stop_limit"),
                       px_fn=lambda t: dict(last=444.19))
    assert why and "through stop-entry" in why, why
    # sell stop-limit through
    why = D.live_check(dict(ticker="ISRG", side="SHORT", entry=450.0, stop=460.0, target=420.0, order_type="stop_limit"),
                       px_fn=lambda t: dict(last=440.0))
    assert why and "through stop-entry" in why, why
