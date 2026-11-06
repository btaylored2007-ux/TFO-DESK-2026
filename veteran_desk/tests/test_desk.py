import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import desk as D
from veteran_desk.tests.isolated_pit import open_pit, seed_bars
import funder_gate as FG
UTC = dt.timezone.utc; NOW = dt.datetime.now(UTC)
F = dict(sym="XOM", venue="US", close=100.0, atr14=2.0, sma20=98.0, sma50=95.0, ret5=.02, ret20=.05, efficiency_ratio20=.5, choppy=False, knowable_utc=(NOW - dt.timedelta(days=1)).isoformat())
P0 = dict(action="propose", ticker="XOM", side="LONG", entry=100.0, stop=96.0, target=108.0, grade="A+", horizon_days=10, thesis="t")
CH = dict(call="LONG", conviction=4); DA = dict(kill_case="x", factual_kill=False)
def g(p=None, f=None, ch=CH, da=DA, used=0): return D.risk_gate_v1(dict(P0, **(p or {})), dict(F, **(f or {})), ch, da, NOW, used)
def test_clean_pass(): r = g(); assert r["passed"] and r["risk_aud"] == 1000, r
def test_blocks():
    assert not g({"ticker": "BBCA.JK"})["passed"]; assert not g({"grade": "B"})["passed"]; assert not g({"stop": 101})["passed"]
    assert not g(f={"choppy": True, "efficiency_ratio20": .1})["passed"]; assert not g(f={"ret5": .2})["passed"]
    assert not g(ch=dict(call="SHORT"))["passed"]; assert not g(da=dict(factual_kill=True, fact="f"))["passed"]
    assert not g(ch=None)["passed"]; assert not g(f={"knowable_utc": (NOW - dt.timedelta(days=9)).isoformat()})["passed"]
    assert not g({"stop": 99.5, "target": 101})["passed"]; assert not g(used=3500)["passed"]
def test_aplus_without_agreeing_challenger_sized_down(): assert g(ch=dict(call="STAND_ASIDE"))["risk_aud"] == 500
def test_veteran_book_not_armable_by_engines(): assert not FG.enabled("veteran")
def test_full_pipeline_mocked_llm_temp_queue(monkeypatch):
    tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "state", "funder_queue.json")
    seed_bars(open_pit(tmp, monkeypatch), "AUDUSD=X", close=0.65, spread=0.001)
    try: real_before = open(FG.QUEUE).read()
    except FileNotFoundError: real_before = '{"entries":{}}'
    def fake(engine, prompt, timeout=None):
        if "Marcus Hale" in prompt or "DATA:\n" not in prompt or "Reply in under 80 words" in prompt: return "note"
        if "blind challenger" in prompt: return json.dumps(dict(call="LONG", conviction=3, reason="trend"))
        if "devil's advocate" in prompt: return json.dumps(dict(kill_case="oil could fall", factual_kill=False, fact=""))
        pk = json.JSONDecoder().raw_decode(prompt.split("DATA:\n", 1)[1])[0]; ok = [f for f in pk["instruments"] if not f["choppy"] and f["close"] > f["sma20"] and f["ret5"] < .1 and f["ret20"] < .2]   # raw_decode: prompt has lessons text after DATA json
        if not ok: return '{"action":"stand_aside"}'
        f = ok[0]; e = f["close"]; return json.dumps(dict(action="propose", ticker=f["sym"], side="LONG", entry=e, stop=round(e - 2 * f["atr14"], 4), target=round(e + 4 * f["atr14"], 4), grade="A", horizon_days=10, thesis="trend"))
    orig = D.llm; D.llm = fake; keep = (D.SD, D.SCORE, D.HEALTH, D.CARD, D.REPORTS)
    D.SD, D.SCORE, D.HEALTH, D.CARD, D.REPORTS = (os.path.join(tmp, x) for x in ('sd', 'score.json', 'h.json', 'card.json', 'rep'))
    try: log = D.run(use_llm=True, queue=True, queue_path=q)
    finally: D.llm = orig; D.SD, D.SCORE, D.HEALTH, D.CARD, D.REPORTS = keep
    _ids = lambda txt: set(json.loads(txt).get("entries", {}))
    try: after = open(FG.QUEUE).read()
    except FileNotFoundError: after = '{"entries":{}}'
    new_real = _ids(after) - _ids(real_before); mine = {r.get("id") for r in log["proposals"]}
    assert not (new_real & mine), new_real & mine   # test never writes its proposals to the real queue (real queue legitimately holds live VET- entries now)
    Q = json.load(open(q)) if os.path.exists(q) else {"entries": {}}
    for e in Q["entries"].values(): assert e["book"] == "veteran" and e["info_only"] and e["state"] == FG.WAIT and e["paper_only"]
    assert all(r["info_only"] for r in log["proposals"]); assert len(Q["entries"]) == sum(r["gate"]["passed"] for r in log["proposals"])
    print("mock run proposals", [(r["seat"], r["proposal"]["ticker"], r["gate"]["passed"], r["gate"]["reasons"]) for r in log["proposals"]])
def test_scorecard_shape(monkeypatch):
    tmp = tempfile.mkdtemp(); seed_bars(open_pit(tmp, monkeypatch), "AUDUSD=X", close=0.65, spread=0.001); keep = (D.SD, D.SCORE); D.SD, D.SCORE = tmp, os.path.join(tmp, 's.json')
    try: s = D.score()
    finally: D.SD, D.SCORE = keep
    s = s; assert s["promotion_rule"]["min_forward_trades"] == 100 and s["promotion_rule"]["max_risk_raise_per_review"] == .25 and not s["promotion_rule"]["replay_counts"]
    assert len(s["seats"]) == 15 and "crypto" in s["seats"]
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
