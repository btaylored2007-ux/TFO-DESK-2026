"""Brett 28 Oct 2026 13:15 WITA hunt fix 4: bench 15 -> 40 (configurable per market); MAX_OPEN unchanged."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_room as HR, asx_hunt48 as A, asia_hunt48 as AH

def test_default_40_and_config(tmp_path):
    assert HR.max_bench("asx", 40, str(tmp_path)) == 40
    (tmp_path / "hunt_bench.json").write_text(json.dumps({"max_bench": {"default": 25, "hk": 33}}))
    assert HR.max_bench("hk", 40, str(tmp_path)) == 33 and HR.max_bench("jp", 40, str(tmp_path)) == 25

def test_constants_and_max_open_unchanged():
    assert A.MAX_BENCH == 40 and AH.MAX_BENCH == 40 and A.MAX_OPEN == 3 and AH.MAX_OPEN == 3 and A.RISK_AUD == 2500.0 and AH.RISK_AUD == 2500.0

def test_live_config_file():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(base, "state", "hunt_bench.json")): assert HR.max_bench("asx", 15, os.path.join(base, "state")) == 40
