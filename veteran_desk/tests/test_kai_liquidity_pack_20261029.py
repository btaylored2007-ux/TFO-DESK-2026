"""2026-10-29 kailiq: every sweep-liquid Kraken pair gets vol24h_aud in Kai's pack; missing feeds row never marks a sweep-liquid pair thin."""
import datetime as dt, json
from veteran_desk import desk as D, scan_universe as SU, feeds as FD
ROWS = [dict(pair="XXBTZUSD", alt="XBTUSD", last=80000.0, vol24h_usd=1.6e8, vol24h_aud=2.4e8, liquid=True, open=81000.0, bid=79999.9, ask=80000.1, chg_pct_since_open=-1.235, spread_bp=0.02),
        dict(pair="SOLUSD", alt="SOLUSD", last=150.0, vol24h_usd=9e6, vol24h_aud=1.3e7, liquid=True),   # old-format row: no o/b/a
        dict(pair="FOOUSD", alt="FOOUSD", last=1.0, vol24h_usd=1e6, vol24h_aud=1.5e6, liquid=True, open=None, bid=None, ask=None, chg_pct_since_open=None, spread_bp=None),
        dict(pair="USDCUSD", alt="USDCUSD", last=1.0, vol24h_usd=9e7, vol24h_aud=1.3e8, liquid=True),
        dict(pair="THINUSD", alt="THINUSD", last=1.0, vol24h_usd=1e4, vol24h_aud=1.5e4, liquid=False)]
def _fx(tmp_path, monkeypatch):
    p = tmp_path / "kraken_24h_all.json"; p.write_text(json.dumps(dict(utc="2026-10-29T06:00:00+00:00", src="kraken REST via DoH", n=len(ROWS), pairs=ROWS)))
    monkeypatch.setattr(D, "KRAKEN_24H", str(p)); return p
def test_every_liquid_pair_has_vol24h_aud(tmp_path, monkeypatch):
    p = _fx(tmp_path, monkeypatch)
    monkeypatch.setattr(SU, "seat_list", lambda k: (["KRAKEN:" + r["alt"] for r in ROWS if r["liquid"] and not r["alt"].startswith(SU.STABLE)], len(ROWS), 3))
    monkeypatch.setattr(D, "feats", lambda c, s, a: None)
    seat = dict(seat="crypto", universe_groups=["crypto"], data_inputs=[])
    out = D.pack(None, seat, dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
    K = out["kraken_24h"]
    for s in ("KRAKEN:XBTUSD", "KRAKEN:SOLUSD", "KRAKEN:FOOUSD"):
        assert K[s]["vol24h_aud"] is not None and K[s]["sweep_utc"] and "kraken_sweep" in K[s]["liquidity_src"]
    assert K["KRAKEN:XBTUSD"]["spread_bp"] == 0.02 and K["KRAKEN:XBTUSD"]["chg_pct_since_utc_open"] == -1.235
    assert K["KRAKEN:SOLUSD"]["spread_bp"] is None   # never invented
    assert "KRAKEN:USDCUSD" not in K and "KRAKEN:THINUSD" not in K
def test_missing_feed_row_not_thin(tmp_path, monkeypatch):
    _fx(tmp_path, monkeypatch)
    r = D.sweep_row_for("KRAKEN:FOOUSD"); assert r and float(r["vol24h_aud"]) >= FD.MIN_24H_AUD
    t = D.sweep_row_for("KRAKEN:THINUSD"); assert t and float(t["vol24h_aud"]) < FD.MIN_24H_AUD
