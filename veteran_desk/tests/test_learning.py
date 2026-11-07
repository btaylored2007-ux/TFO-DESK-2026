"""Learning loop tests (PAPER). Fake models; temp dirs; never touches the real book."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import learning as L
def _setup():
    d = tempfile.mkdtemp(); L.LD = os.path.join(d, "learning"); L.REP = os.path.join(d, "rep"); L.WEEKLY = os.path.join(L.REP, "w.json")
    L.BOOK = os.path.join(d, "book.json"); L.RUNS = os.path.join(d, "runs"); L.trader_seats = lambda: ["energy"]; return d
def _pos(i, net, t, venue="US", side="LONG", rr_t=72.6, xr=None, slip=1.0):
    return dict(id=f"VET-X{i}-20261001-0900-energy", seat="energy", ticker=f"X{i}", side=side, venue=venue, entry=68.2, stop=66.0, target=rr_t, shares=100, risk_aud=1000.0,
                status="closed", fill=68.2, filled_utc=t, closed_utc=t, exit=70, exit_reason=xr or ("target" if net > 0 else "stop"), gross_aud=net + 20, net_aud=net, slippage_bps=slip, sleeve="core")
def ok_llm(e, p, t): return '{"verdict":"take","one_line":"fine","size_view":"ok","risks":["gap"]}'
def down_llm(e, p, t): return "__ERR__ timeout" if e == "grok_queue" else ok_llm(e, p, t)
def test_journal_sync_idempotent_and_fields():
    _setup(); now = L.now().isoformat()
    json.dump(dict(seats=dict(energy=dict(positions={}, closed=[_pos(1, -1000, now), _pos(2, 1500, now, slip=9)]))), open(L.BOOK, "w"))
    assert L.sync()["new_entries"] == 4 and L.sync()["new_entries"] == 0
    J = [json.loads(x) for x in open(os.path.join(L.LD, "energy", "journal.jsonl"))]; c = [x for x in J if x["event"] == "close"]
    for k in ("setup", "thesis", "ai_input", "entry", "stop", "exit", "pnl_aud_net", "r_multiple", "mistake_tag"): assert k in c[0]
    assert c[0]["r_multiple"] == -1.0 and c[0]["mistake_tag"] == "thesis_wrong_stopped" and c[1]["mistake_tag"] == "chased_entry"
    assert c[0]["model_missing"] == ["grok"]   # active sounding-board model; a miss is recorded and does not block the journal
def test_sound_board_model_down_never_blocks_and_tags():
    _setup(); rec = dict(id="VET-A-1", seat="energy", proposal=dict(ticker="A", side="LONG", entry=1, stop=.9, target=1.3, thesis="t"), sizing=dict(shares=1, risk_aud=10), gate=dict(passed=True))
    g0 = json.dumps(rec, sort_keys=True); v = L.sound_board(rec, llm=down_llm)
    assert "claude" not in v["verdicts"] and v["verdicts"]["grok"]["verdict"] == "missing" and v["tags"] == ["grok_missing"]
    assert json.dumps(rec, sort_keys=True) == g0   # advisory: gate/sizing untouched
    def boom(e, p, t): raise RuntimeError("x")
    r = L.sound_board_all([rec], llm=boom); assert r["VET-A-1"]["tags"] == ["grok_missing"]
def test_prompts_carry_no_secrets():
    from veteran_desk import desk as D
    seen = []
    def spy(e, p, t): seen.append(p); return ok_llm(e, p, t)
    L.sound_board(dict(id="i", seat="energy", proposal=dict(ticker="A", thesis="t"), sizing={}, gate={}), llm=spy)
    assert seen and not any(D.SECRET_RX.search(p) for p in seen)
    assert D._llm("claude", "api_key = sk-abcdefghijklmnop", 5).startswith("__ERR__ prompt blocked")
def test_daily_rolling_30_days():
    _setup(); p = os.path.join(L._sd("energy"), "lessons.md")
    open(p, "w").write("# x\n\n## 2026-08-01\n- TOP: old\n\n## 2026-10-20\n- TOP: recent\n")
    L.daily(use_llm=True, llm=down_llm, today=dt.date(2026, 10, 28)); t = open(p).read()
    assert "2026-08-01" not in t and "## 2026-10-20" in t and t.index("## 2026-10-28") < t.index("## 2026-10-20")
    assert "GROK: MISSING" in t and L.top_lesson("energy") and "YOUR OWN LESSONS" in L.lessons_for_prompt("energy")
def test_weekly_heldout_filters_only_and_approval_shadow():
    _setup(); base = L.now() - dt.timedelta(days=20); C = []
    for i in range(20):   # FX trades lose, US trades win -> skip_venue FX should beat held-out
        v = "FX" if i % 2 else "US"; C.append(_pos(i, -800 if v == "FX" else 600, (base + dt.timedelta(hours=i)).isoformat(), venue=v))
    json.dump(dict(seats=dict(energy=dict(positions={}, closed=C))), open(L.BOOK, "w")); L.sync()
    W = L.weekly(use_llm=False); P = W["seats"]["energy"]["proposals"]; assert W["auto_apply"] is False
    best = [p for p in P if p["status"] == "pending_tfo_approval"]; assert best and best[0]["kind"] == "skip_venue" and best[0]["value"] == "FX"
    assert all(p["heldout_net_aud"] > p["heldout_baseline_aud"] for p in best)
    for p in P: assert all(L.keeps(p, x) in (True, False) for x in L.closes("energy"))   # filters only
    rej = [p for p in P if p["status"] != "pending_tfo_approval"]
    if rej: assert not L.approve("energy", rej[0]["id"], "TFO")["ok"]
    assert not L.approve("energy", best[0]["id"], None)["ok"] and L.approve("energy", best[0]["id"], "TFO test")["ok"]
    assert not L.promote("energy", best[0]["id"], "TFO")["ok"]   # 0 shadow trades
    later = L.now() + dt.timedelta(minutes=1)
    C2 = C + [_pos(100 + i, -800 if i % 2 else 600, (later + dt.timedelta(minutes=i)).isoformat(), venue="FX" if i % 2 else "US") for i in range(19)]
    json.dump(dict(seats=dict(energy=dict(positions={}, closed=C2))), open(L.BOOK, "w")); L.sync()
    assert not L.promote("energy", best[0]["id"], "TFO")["ok"]   # 19 < 20
    C2.append(_pos(200, 600, (later + dt.timedelta(minutes=30)).isoformat())); json.dump(dict(seats=dict(energy=dict(positions={}, closed=C2))), open(L.BOOK, "w")); L.sync()
    assert L.promote("energy", best[0]["id"], "TFO")["ok"] and "TFO-PROMOTED FILTERS" in L.lessons_for_prompt("energy")
def test_card_shape():
    _setup(); json.dump(dict(seats=dict(energy=dict(positions={}, closed=[]))), open(L.BOOK, "w"))
    L.sound_board(dict(id="i", seat="energy", proposal={}, sizing={}, gate={}), llm=down_llm); L.sound_board(dict(id="j", seat="energy", proposal={}, sizing={}, gate={}), llm=ok_llm)
    c = L.card()["seats"][0]   # GPT-only card (Claude retired 2026-10-29)
    assert "claude" not in c["agreement"] and c["agreement"]["grok"]["missing"] == 1 and c["agreement"]["grok"]["answered"] == 1 and c["agreement"]["grok"]["agree_pct"] == 100
def test_parse_codex_transcript():
    raw = 'OpenAI Codex v0\n--------\nuser\nReply ONE JSON {"x":1}\ncodex\n{"verdict":"skip","one_line":"no"}\ntokens used\n123'
    assert L._parse(raw) == {"verdict": "skip", "one_line": "no"} and L._parse("blah") is None

def test_critique_async_one_model_never_blocks():
    import time as _t
    _setup(); asked = []
    def spy(e, p, t): asked.append((e, t)); return ok_llm(e, p, t)
    t0 = _t.time(); r = L.critique_async([dict(id="x")], "t"); assert _t.time() - t0 < 2 and not r.get("spawned")   # temp desk: no real spawn
    pf = os.path.join(L.LD, "pc.json"); L._wj(pf, [dict(id="a", seat="energy", proposal={}, sizing={}, gate={}), dict(id="b", seat="energy", proposal={}, sizing={}, gate={})])
    L.critique_file(pf, llm=spy); assert [e for e, _ in asked] == ["grok_queue", "grok_queue"] and all(t == L.SB_TIMEOUT_S <= 60 for _, t in asked)
