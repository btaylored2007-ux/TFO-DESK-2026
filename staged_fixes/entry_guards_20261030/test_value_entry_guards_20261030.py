import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_entry_guards as g
T = dt.date(2026, 10, 30)
def test_reentry_block_and_override():
    st = {"NWG": ("2026-10-30", "t")}
    assert g.reentry_block("NWG", {}, st, today=T)[0]
    assert g.reentry_block("NWG", {"new_thesis": "  "}, st, today=T)[0]
    assert g.reentry_block("NWG", {"new_thesis": "x"}, st, today=T)[0] is None
    assert g.reentry_block("AAA", {}, st, today=T)[0] is None
def test_earnings():
    assert g.earnings_block("U", {"date": "2026-11-03"}, today=T)[0]
    assert g.earnings_block("U", {"date": "2026-11-20"}, today=T)[0] is None
    r, i = g.earnings_block("U", None, today=T); assert r is None and i["earnings_unknown"]
