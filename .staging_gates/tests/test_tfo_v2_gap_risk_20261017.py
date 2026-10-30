"""Brett 17 Oct 2026 item 10: TFO V2 sizes stocks so a 2x-stop-distance opening gap stays within planned loss (risk_pct)."""
import os, sys
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_v2 as V


def _run(venue, eq=1_000_000.0):
    cfg = V.config(); cfg["law"]["gap_mult_stock"] = 2.0
    b = {"equity": eq, "cash": eq, "positions": {}}
    o = {"sym": "AAPL" if venue != "KRAKEN" else "XBTUSD", "venue": venue, "atr": 2.0, "init_k": 2.0, "setup": "S3"}
    q = {"ask": 100.0, "avg10_vol": 1e9, "day_vol": 1e9, "spread_bp": 1, "dollar_vol24": 1e12}
    with mock.patch.object(V.desk_costs, "fill_fees", return_value={"fees_usd": 1.0}):
        return V.size_order(o, b, cfg, q)[0], cfg


def test_stock_gap_of_2x_stop_within_planned_loss():
    sz, cfg = _run("US")
    planned = cfg["law"]["risk_pct"] * 1_000_000.0
    assert sz["gap_mult"] == 2.0
    assert sz["gap_risk_usd"] <= planned + 1e-6
    assert abs(sz["risk_usd"] - planned / 2) < 1e-6   # stop-out loses half the budget, a 2x gap the whole of it


def test_crypto_unchanged():
    sz, cfg = _run("KRAKEN")
    assert sz["gap_mult"] == 1.0 and abs(sz["risk_usd"] - cfg["law"]["risk_pct"] * 1_000_000.0) < 1e-6


def test_config_cannot_loosen_gap_rule(tmp_path, monkeypatch):
    p = tmp_path / "c.json"; p.write_text('{"law": {"gap_mult_stock": 1.0}}')
    monkeypatch.setattr(V, "CONFIG", str(p))
    assert V.config()["law"]["gap_mult_stock"] == 2.0
    p.write_text('{"law": {"gap_mult_stock": 3.0}}')
    assert V.config()["law"]["gap_mult_stock"] == 3.0
