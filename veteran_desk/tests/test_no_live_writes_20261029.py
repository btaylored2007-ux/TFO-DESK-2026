import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.synthetic_book import skeleton
U = dt.timezone.utc
LIVE = os.path.join(BK.BASE, "state", "veteran_desk")
WATCH = ["KILL_ALERT.json", "price_alerts.jsonl", os.path.join("archive", "hedge_20261029.json")]
def _sig(): return {w: (os.stat(os.path.join(LIVE, w)).st_mtime_ns if os.path.exists(os.path.join(LIVE, w)) else None) for w in WATCH}
def test_temp_book_never_writes_live_state():
    """Forces a kill-switch FLAT and a price alert on a temp book: outputs must land beside the temp book, never in the live state dir."""
    tmp = tempfile.mkdtemp(); old = (BK.BOOK, BK.now, BK.fallback_quote); before = _sig()
    try:
        D = copy.deepcopy(skeleton())
        for B in D["seats"].values(): B["positions"] = {}; B["cash_aud"] = 1000.0   # equity collapses -> dd far below -12%
        D["hedge"] = {}; D["kill_switch"] = dict(state="OK"); D["peak_equity_aud"] = 5_000_000.0
        p = os.path.join(tmp, "b.json"); json.dump(D, open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
        BK.BOOK = p; BK.now = lambda: dt.datetime(2026, 10, 28, 13, 5, tzinfo=U); BK.fallback_quote = lambda s: None
        assert BK._state_dir() == tmp
        BK._price_alert(dict(level="TEST", msg="probe"))
        BK.tick(price_fn=lambda s: None, au=0.65, queue_path=q)
        assert os.path.exists(os.path.join(tmp, "KILL_ALERT.json")) and os.path.exists(os.path.join(tmp, "price_alerts.jsonl"))
    finally: BK.BOOK, BK.now, BK.fallback_quote = old
    assert _sig() == before, (before, _sig())
    o = BK.BOOK; BK.BOOK = BK._REAL_BOOK
    try: assert BK._state_dir() == LIVE
    finally: BK.BOOK = o
