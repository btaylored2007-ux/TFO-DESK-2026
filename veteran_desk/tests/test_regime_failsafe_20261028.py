import sys, types
import veteran_desk.news_desk as N
class _DeadTicker:
    def __init__(self, s): pass
    def history(self, *a, **k): raise RuntimeError("yfinance down (simulated)")
def test_yfinance_down_is_unknown():
    old_yf = sys.modules.get("yfinance"); old_get = N._get
    sys.modules["yfinance"] = types.SimpleNamespace(Ticker=_DeadTicker)
    N._get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down"))
    import veteran_desk.feeds as F; old_fred = F.fred_last; F.fred_last = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        f, g = N.regime(); assert f == "unknown", (f, g); assert set(g["missing"]) == set(N.REGIME_REQUIRED)
        N._get = old_get   # partial: only credit back, prices still down -> still unknown (never 'calm')
        f, g = N.regime(); assert f == "unknown" and "vix" in g["missing"], (f, g)
    finally:
        N._get = old_get; F.fred_last = old_fred
        if old_yf is not None: sys.modules["yfinance"] = old_yf
        else: sys.modules.pop("yfinance", None)
def test_inputs_back_clears():
    f, g = N.regime(); assert f != "unknown" or g.get("missing"), (f, g)   # live: a real label when every input is present
