import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def _hwm(prev, pnl):   # mirrors the book.py update rule
    return round(max(0.0, prev or 0.0, pnl), 2)
def test_rule_in_code():
    src = open(BK.__file__).read(); assert 'B["pnl_hwm_aud"] = round(max(0.0, B.get("pnl_hwm_aud") or 0.0, B["pnl_since_inception_aud"]), 2)' in src
def test_floor():
    assert _hwm(-54.32, -139.55) == 0.0 and _hwm(None, -10) == 0.0 and _hwm(0.0, 25.5) == 25.5 and _hwm(25.5, 10) == 25.5
def test_tick_floors_stored_negative_hwm():
    import json, tempfile, datetime as dt
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    old = (BK.BOOK, BK.now); BK.BOOK = os.path.join(tmp, "book.json"); BK.now = lambda: dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)
    try:
        D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}); D["seats"]["energy"]["pnl_hwm_aud"] = -54.32; D["seats"]["energy"]["cash_aud"] = 499860.45
        json.dump(D, open(BK.BOOK, "w"))
        D = BK.tick(price_fn=lambda s: None, au=0.7, queue_path=q)
        assert all(v["pnl_hwm_aud"] >= 0.0 for v in D["seats"].values()) and D["seats"]["energy"]["pnl_hwm_aud"] == 0.0, D["seats"]["energy"]["pnl_hwm_aud"]
    finally: BK.BOOK, BK.now = old
