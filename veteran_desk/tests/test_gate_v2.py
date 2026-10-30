import datetime as dt, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import gate_v2 as G
UTC = dt.timezone.utc; NOW = dt.datetime(2026, 10, 28, 9, tzinfo=UTC)
def rows(n=1500, drift=.0004, seed=1, vol=1e6):
    r = np.random.RandomState(seed); c = 100 * np.exp(np.cumsum(drift + .01 * r.randn(n))); d0 = dt.date(2020, 1, 1); out = []
    for i in range(n):
        D = d0 + dt.timedelta(days=i); o = c[i - 1] if i else c[0]
        out.append((D.isoformat(), o, max(o, c[i]) * 1.005, min(o, c[i]) * .995, c[i], vol, dt.datetime(D.year, D.month, D.day, 20, 30, tzinfo=UTC).isoformat()))
    return out
HIST = (rows(), "test"); ENV = dict(book=([], "OK"), firm="NORMAL"); CH = dict(call="LONG"); DA = dict(factual_kill=False)
def asof(): return dt.datetime.fromisoformat(HIST[0][-1][6]) + dt.timedelta(hours=1)
def P0(**k):
    c = HIST[0][-1][4]; A = G.arrays(HIST[0]); a = A["atr"][-1]
    d = dict(ticker="TEST", side="LONG", entry=c, stop=c - 2 * a, target=c + 4 * a, grade="A"); d.update(k); return d
def g(p=None, ch=CH, da=DA, used=0, env=ENV, hist=HIST, th=None): return G.gate(p or P0(), None, ch, da, asof(), used, hist=hist, env=env, th=th)
def test_never_arms():
    r = g(); assert r["armed"] is False and r["can_arm"] is False and r["needs_brett_tick"] is True
def test_hard_rules_kept():
    c = HIST[0][-1][4]
    assert not g(P0(stop=0))["passed"]; assert not g(P0(stop=c + 1))["passed"]                  # stop on every trade, right side
    assert not g(P0(ticker="BBCA.JK"))["passed"]; assert not g(P0(grade="B"))["passed"]
    a = G.arrays(HIST[0])["atr"][-1]; assert not g(P0(stop=c - .2 * a))["passed"]               # inside noise
    assert not g(P0(stop=c - 8 * a, target=c + 20 * a))["passed"]                               # not a stop
    assert g(used=3900)["passed"]   # A$4k run cap removed (Brett 19:00 WITA); heat cap still blocks
    assert not g(env=dict(book=([], "FLAT"), firm="NORMAL"))["passed"]                          # kill switch
    assert not g(env=dict(book=([], "OK"), firm="HALT"))["passed"]                              # firm rail
    assert g(ch=dict(call="SHORT"), da=dict(factual_kill=True, fact="x"))["passed"]             # TFO 19:18: LLM controls advisory only
    assert not g(P0(side="SHORT", ticker="KRAKEN:XBTUSD", stop=HIST[0][-1][4] * 1.03, target=HIST[0][-1][4] * .9))["passed"]
def test_missing_data_never_fails_closed():
    r = g(hist=([], "none"), ch=None, da=None); assert not r["passed"] and any("no point-in-time" in x for x in r["reasons"])  # 2026-10-29: empty PIT is HARD reject
    r = g(P0(grade="A+"), ch=None); assert r["passed"] and r["size_mult"] == 1.0   # debatefix 2026-10-29: model outage = no size change
    r = g(env=dict(book=([], "OK"), firm=None)); assert r["passed"]
def test_single_llm_objection_sizes_down_not_blocks():
    r = g(P0(grade="A+"), ch=dict(call="SHORT")); assert r["passed"] and r["size_mult"] == 1.0   # TFO 19:18 advisory
    r = g(P0(grade="A+"), da=dict(factual_kill=True, fact="x")); assert r["passed"] and r["size_mult"] == 1.0
def test_base_rate_negative_blocks():
    r = g(th=dict(base_block_exp=5.0)); assert not r["passed"] and any("history" in x for x in r["reasons"])
    assert g()["history"]["base_rate"]["n"] > 40
def test_heat_and_adv():
    big = dict(ticker="TEST2", side="LONG", risk_aud=80000)
    def hist2(t, a): return HIST
    G_hist = G.history; G.history = hist2
    try: r = g(env=dict(book=([big], "OK"), firm="NORMAL")); assert not r["passed"] and any("heat" in x for x in r["reasons"])
    finally: G.history = G_hist
    r = g(hist=(rows(vol=50), "t")); assert not r["passed"] and any("ADV" in x for x in r["reasons"])
def test_no_lookahead_sqlite_filter():
    r = G._sqlite_rows("XOM", "2020-01-10T00:00:00+00:00"); assert all(x[6] <= "2020-01-10T00:00:00+00:00" for x in r)
    s = G._staging_rows("EURUSD=X", "2020-01-10T00:00:00+00:00"); assert all(x[0] < "2020-01-10" for x in s)
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
