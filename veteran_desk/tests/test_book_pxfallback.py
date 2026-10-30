import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, feeds as FD
def test_audusd_uses_fred_when_yahoo_empty():
    keep = (BK.live_px, FD.fred_px); BK.live_px = lambda s: None; FD.fred_px = lambda s: dict(last=0.6612) if s == "AUDUSD=X" else None
    try: assert BK.audusd() == 0.6612
    finally: BK.live_px, FD.fred_px = keep
def test_audusd_default_when_all_fail():
    keep = (BK.live_px, FD.fred_px); BK.live_px = lambda s: None; FD.fred_px = lambda s: None
    import pytest, tempfile, os as _o; kc = (dict(BK._AU_LAST), BK.AUDUSD_CACHE); BK._AU_LAST.clear(); BK.AUDUSD_CACHE = _o.path.join(tempfile.mkdtemp(), "au.json")
    try:
        with pytest.raises(BK.FxUnavailable): BK.audusd()   # tfo_review_20261030 GPT HIGH: no invented 0.65
    finally: BK.live_px, FD.fred_px = keep; BK._AU_LAST.clear(); BK._AU_LAST.update(kc[0]); BK.AUDUSD_CACHE = kc[1]
