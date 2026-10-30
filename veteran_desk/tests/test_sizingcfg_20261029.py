"""sizingcfg (TFO 19:43 WITA 29 Oct 2026): no seat JSON carries a fixed *_risk_aud sizing key; code (book.risk_plan) is authoritative."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
SEATS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seats")
def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items(): yield k; yield from _keys(v)
    elif isinstance(o, list):
        for v in o: yield from _keys(v)
def test_no_risk_aud_keys():
    bad = [(os.path.basename(f), k) for f in glob.glob(os.path.join(SEATS, "*.json")) for k in _keys(json.load(open(f))) if k.endswith("_risk_aud")]
    assert not bad, bad
def test_rule_log(): assert any(r.get("id") == "tfo_sizingcfg_20261029_1943" for r in BK.RULE_LOG)
