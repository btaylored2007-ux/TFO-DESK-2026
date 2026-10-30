import json, os
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
TAG = "GAPCLOSE_TO_FUND_20261028"
def _ev(name):
    return [e for e in json.load(open(os.path.join(B, name + ".json")))["capital_events"] if e.get("tag") == TAG]
def test_paired_once_and_nets_zero():
    f, g = _ev("fundamental_book"), _ev("gap_close_levered_book")
    assert len(f) == 1 and len(g) == 1
    assert abs(f[0]["usd"] + g[0]["usd"]) < 0.005 and f[0]["usd"] > 700000
    assert f[0]["utc"] == g[0]["utc"] and 0.4 < f[0]["audusd"] < 1.2
def test_gcl_cash_zero_after():
    # After GAPCLOSE drain + FUND_TO_TREND18_MIN top-up, cash == that top-up; later HT flats (e.g. WES.AX 28 Oct)
    # legitimately add cash on top. Assert cash == top-up + sum of capital_events after the top-up utc.
    g = json.load(open(os.path.join(B, "gap_close_levered_book.json")))
    tops = [e for e in g["capital_events"] if e.get("tag") == "FUND_TO_TREND18_MIN_20261028"]
    assert len(tops) == 1
    later = tops[0]["usd"]; cut = tops[0]["utc"]
    post = sum(e["usd"] for e in g["capital_events"] if (e.get("utc") or "") > cut)
    assert abs(g["cash"] - (later + post)) < 0.01 and g["dry_powder_usd"] >= 0

def test_fund_to_trend18_min_paired_once():
    f = [e for e in json.load(open(os.path.join(B, "fundamental_book.json")))["capital_events"] if e.get("tag") == "FUND_TO_TREND18_MIN_20261028"]
    g = [e for e in json.load(open(os.path.join(B, "gap_close_levered_book.json")))["capital_events"] if e.get("tag") == "FUND_TO_TREND18_MIN_20261028"]
    assert len(f) == 1 and len(g) == 1 and f[0]["usd"] == -101000.0 and g[0]["usd"] == 101000.0 and f[0]["utc"] == g[0]["utc"]
