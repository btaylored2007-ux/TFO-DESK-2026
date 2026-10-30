import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_rank as R
def test_tech_sector_and_names_excluded():
    assert R.tech_reason({"symbol": "XYZ", "sector": "Technology"})
    assert R.tech_reason({"symbol": "NVDA", "sector": "Consumer Cyclical"})
    assert R.tech_reason({"symbol": "XOM", "sector": "Energy"}) is None
def test_mcap_floor_2m():
    assert R.MIN_MCAP_USD == 2_000_000 and R.mcap_usd({"mcap": 3e6, "ccy": "AUD"}) < 3e6
def test_rank_end_to_end(tmp_path, monkeypatch):
    import json
    rows = [dict(symbol="A", sector="Energy", ccy="USD", mcap=1e9, pb=1, pe=10, ev_ebitda=5, roe=.2, de=50),
            dict(symbol="B", sector="Technology", ccy="USD", mcap=1e9, pb=1, pe=10, ev_ebitda=5, roe=.2, de=50),
            dict(symbol="C", sector="Industrials", ccy="USD", mcap=1e6, pb=1, pe=10, ev_ebitda=5, roe=.2, de=50)]
    src = tmp_path / "h.json"; src.write_text(json.dumps({"generated_utc": "x", "n_screened": 3, "n_kept": 3, "n_rejected": 0, "rows": rows}))
    monkeypatch.setattr(R, "SRC", str(src)); monkeypatch.setattr(R, "OUT", str(tmp_path / "o.json"))
    k = R.main(); assert [r["symbol"] for r in k] == ["A"] and k[0]["sector_bonus"] == R.PREFERRED_BONUS == 0.15
