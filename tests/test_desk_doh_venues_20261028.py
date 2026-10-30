"""28 Oct 2026 Brett B4: venue hosts (Binance/Coinbase/OKX/Alpaca/Polymarket) + IBKR regional default ON in desk_doh."""
import desk_doh as D
def test_default_includes_venues():
    hs = D.install(); D.uninstall()
    for h in ("api.binance.com", "api.exchange.coinbase.com", "www.okx.com", "paper-api.alpaca.markets", "clob.polymarket.com", "www.interactivebrokers.com.hk"):
        assert h in hs
def test_explicit_false_scoped():
    hs = D.install(ibkr=True, oanda=False, extra=False); D.uninstall(); assert "api.binance.com" not in hs
def test_env_opt_out(monkeypatch):
    monkeypatch.setenv("TFO_DESK_DOH_EXTRA", "0"); hs = D.install(); D.uninstall(); assert "www.okx.com" not in hs
def test_hooks_present():
    for f in ("asia_markets", "crypto_rumour_desk", "crypto_rumour_fill_verifier", "crypto_rumour_lab", "desk_costs", "maker_fill_lab", "maker_fill_probe", "shark_slippage", "universe_asia", "veto_log"):
        assert "_doh.install(extra=True)" in open(f + ".py").read()
