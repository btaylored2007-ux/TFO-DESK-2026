import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_deploy as V
def test_jpy_not_booked_as_usd():
    ccy, f = V.price_ccy_and_fx("8766.T", {"USD": 1.0, "JPY": 0.0066})
    assert ccy == "JPY" and abs(f - 0.0066) < 1e-12
def test_aud():
    assert V.price_ccy_and_fx("NAB.AX", {"USD": 1.0, "AUD": 0.70})[1] == 0.70
def test_us_is_usd():
    assert V.price_ccy_and_fx("ISRG", {"USD": 1.0}) == ("USD", 1.0)
def test_missing_rate_refuses():
    import fx, pytest
    with pytest.raises(fx.FXUnavailable):
        V.price_ccy_and_fx("NAB.AX", {"USD": 1.0})
def test_atomic_dump(tmp_path):
    p = str(tmp_path / "l.json"); V._atomic_dump({"a": 1}, p)
    assert json.load(open(p)) == {"a": 1} and not os.path.exists(p + ".tmp-value_deploy")
def test_gate_fails_closed(monkeypatch, capsys):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "book_kill": raise ImportError("x")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.setattr(V.json, "load", lambda f: {"positions": {}, "book_risk": {}, "ranked": [], "cash": 0})
    V._main(dry=True)
    assert "REFUSED: firm halt gate unavailable" in capsys.readouterr().out
