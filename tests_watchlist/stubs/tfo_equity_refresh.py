"""TEST STAND-IN for the V1 module missing from the tarball: collect_quotes(symbols) -> (quotes, failed) from $TFO_WL_MOCK."""
import json, os

def collect_quotes(symbols):
    with open(os.environ["TFO_WL_MOCK"]) as f:
        d = json.load(f)
    q, fail = {}, {}
    for s in symbols:
        if s in d["quotes"]: q[s] = dict(d["quotes"][s])
        else: fail[s] = "mock: no quote"
    return q, fail
