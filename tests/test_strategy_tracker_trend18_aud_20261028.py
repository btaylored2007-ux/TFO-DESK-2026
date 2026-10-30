"""Trend18 ledger is US$: strategy_tracker must report pnl_aud = US$ P&L / desk FX (USD per AUD)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy_tracker as S

def test_trend18_row_converted_to_aud(monkeypatch):
    real = S._j
    monkeypatch.setattr(S, "_j", lambda p: {"start": 500000.0, "equity": 507022.0} if p == "state/trend18.json" else real(p))
    row = [r for r in S.rows() if r["strategy"].startswith("Trend18")][0]
    assert row["pnl_usd"] == 7022 and row["pnl_aud"] == round(7022 / S.FX, 0) and row["pnl_aud"] > row["pnl_usd"]
