import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import v1_venues as v, value_deploy as vd
NOW = dt.datetime(2026, 10, 28, 6, 0, tzinfo=dt.timezone.utc)
def test_fundamental_allows_t_l_only():
    for s in ("7203.T", "TSCO.L", "BHP.AX", "HCI"): assert v.fundamental_entry_block_reason(s, NOW) is None
    for s in ("0700.HK", "SAP.DE", "MC.PA"): assert v.fundamental_entry_block_reason(s, NOW)
def test_other_books_unchanged():
    assert v.entry_block_reason("7203.T", NOW) and v.entry_block_reason("TSCO.L", NOW)
def test_delayed_ok_for_jp_lse():
    q = {"px_usd": 1.0, "executable": False, "paper_executable": True, "paper_price_class": "DELAYED PAPER", "age_sec": 900}
    assert vd.fillable("7203.T", q)[0] and vd.fillable("TSCO.L", q)[0] and not vd.fillable("SAP.DE", q)[0]
