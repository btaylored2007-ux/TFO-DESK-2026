"""2026-10-30 learning overlord: R must use the ORIGINAL initial stop, not the residual risk left after a partial bank."""
from veteran_desk import learning as L
def _p(**k):
    d = dict(id="X", side="SHORT", ticker="EURUSD=X", fill=1.1168192625045776, entry=1.1168192625045776, initial_stop=1.1245, stop=1.11682,
             shares=247739, risk_aud=0.52, status="closed", net_aud=933.81, venue="FX")
    d.update(k); return d
def test_residual_risk_not_used_as_1r():
    r = L._rmult(_p(), 933.81, 0.7026)
    assert r is not None and 0.2 < r < 0.5, r          # ~0.345R, was 1795.8
def test_partial_scaled_from_initial_stop():
    r = L._rmult(_p(stop=1.12, shares=495480, risk_aud=4470.14), 2303.64, 0.7026)
    assert 0.38 < r < 0.47, r                          # ~0.425R, was 0.515 (residual 1R)
def test_unmoved_stop_uses_recorded_risk():
    assert L._rmult(_p(stop=1.1245, initial_stop=1.1245, risk_aud=10000.0), -10000.0) == -1.0
def test_no_initial_stop_falls_back_to_risk_aud():
    assert L._rmult(_p(initial_stop=None, risk_aud=5000.0), 2500.0) == 0.5
def test_avg_r_excludes_suspect():
    assert L.R_SUSPECT <= 50
