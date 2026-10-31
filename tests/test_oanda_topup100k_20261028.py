"""28 Oct 2026 Brett: +A$100k paper top-up on the OANDA AGGRO capital base; margin stays on broker real figures."""
import json, os, importlib
import oanda_aggro as A

def _setup(tmp_path, monkeypatch, amt=100000.0):
    p = tmp_path / "t.jsonl"; p.write_text(json.dumps(dict(utc="2026-10-28T05:00:00+00:00", amount_aud=amt)) + "\n")
    monkeypatch.setattr(A, "TOPUPS", str(p))

def test_capital_base(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert A.topup_total() == 100000.0
    assert abs(A.capital_nav({"NAV": "51545.22"}) - 151545.22) < 1e-6
    c = A.cfg(); assert c["start_nav_aud"] >= 100000

def test_future_topup_not_counted(tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"; p.write_text(json.dumps(dict(utc="2099-01-01T00:00:00+00:00", amount_aud=5.0)) + "\n")
    monkeypatch.setattr(A, "TOPUPS", str(p)); assert A.topup_total() == 0.0

def test_margin_uses_real_broker_room(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    c = A.cfg(); a = {"NAV": "51545", "marginUsed": "1730", "marginAvailable": "49822"}
    room = A.real_margin_room(a, c, A.capital_nav(a))
    assert room <= 0.90 * 49822 + 1e-6 and room < 0.60 * 151545 - 1730

def test_caps_scale_with_base(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    c = A.cfg(); assert c["risk_pct"] == 0.02 and c["open_risk_cap_pct"] == 0.25
    assert c["group_cap"]["METALS"] == 0.06 and c["usd_cap_pct"] == 0.09
    assert round(c["risk_pct"] * A.capital_nav({"NAV": "51545.22"}), 2) == 3030.90
