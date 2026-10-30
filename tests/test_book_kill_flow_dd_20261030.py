"""book_kill flow-adjusted DD: deposit 0, withdrawal 0, real loss counted; no amount_usd re-count (custodian 2026-10-30)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import book_kill as K
_store = {}
K._load_peaks = lambda: _store; K._save_peaks = lambda s: None; K._log = lambda r: None

def run(label, steps):
    _store.clear(); led = {}
    for eq, flow in steps:
        br = K.evaluate(led, eq, label, verbose=False, net_flow_usd=flow)
        _store[label] = dict(br)
    return br["drawdown_pct"]

def test_all():
    assert run("dep", [(1000.0, 0.0), (1100.0, 100.0)]) == 0.0
    assert run("wd", [(1000.0, 0.0), (900.0, -100.0)]) == 0.0
    assert run("loss", [(1000.0, 0.0), (900.0, 0.0)]) == -10.0
    assert run("mix", [(1000.0, 0.0), (1050.0, 100.0)]) == -5.0
    led = {"capital_events": [{"type": "injection", "amount_usd": 100000.0}, {"type": "capital_transfer", "usd": -5.0}]}
    assert K.net_capital_flow(led) == -5.0
    print("OK deposit 0, withdrawal 0, loss -10, deposit+loss -5, amount_usd-only not re-counted")

if __name__ == "__main__":
    test_all()
