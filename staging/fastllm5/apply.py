import re
S="staging/fastllm5/"
s=open("veteran_desk/desk.py").read()
a='"DATA:\\n" + json.dumps(compact_pk(pk)) + _lessons(seat)'
assert a in s, "anchor"
s=s.replace(a,'"DATA:\\n" + json.dumps(_slim(compact_pk(pk)), separators=(",", ":"), default=str) + _lessons_slim(seat)',1)
helper='''_SLIM_DROP = ("last_date", "adv20", "vol24h_aud", "liquidity_src", "sweep_utc", "knowable_utc")   # tfo_fastllm5: redundant with adv20_value / vol24h_usd / asof_utc
def _r4(v):
    if isinstance(v, float): return float(f"{v:.5g}")
    if isinstance(v, list): return [_r4(x) for x in v]
    if isinstance(v, dict): return {k: _r4(x) for k, x in v.items()}
    return v
def _slim(q):   # tfo_fastllm5 (head trader 03:09 WITA): dedupe + 5-sig-fig numbers; all feed fields the seat uses kept
    if os.environ.get("VET_PROMPT_SLIM", "1") != "1" or not isinstance(q, dict): return q
    try:
        q = dict(q)
        if isinstance(q.get("instruments"), list): q["instruments"] = [{k: v for k, v in x.items() if k not in _SLIM_DROP} if isinstance(x, dict) else x for x in q["instruments"]]
        return _r4(q)
    except Exception: return q
def _lessons_slim(seat):   # advisory sounding-board critiques trimmed to 240 chars per line; lessons/rails text untouched
    t = _lessons(seat)
    if os.environ.get("VET_PROMPT_SLIM", "1") != "1" or "Sounding boards" not in t: return t
    try:
        head, _, rest = t.partition("### Sounding boards")
        return head + "### Sounding boards" + "\\n".join((l[:240] + "...") if len(l) > 240 else l for l in rest.split("\\n"))
    except Exception: return t
def prop_prompt(seat, pk):'''
assert s.count("def prop_prompt(seat, pk):")==1
s=s.replace("def prop_prompt(seat, pk):",helper,1)
open(S+"desk.py","w").write(s)
v=open("veteran_desk/vd_engine.py").read()
assert "LLM_TIMEOUT_2L = 60\n" in v
v=v.replace("LLM_TIMEOUT_2L = 60\n","LLM_TIMEOUT_2L = 120   # tfo_fastllm5 (head trader 03:09 WITA): match VD 120s fast-path budget\n",1)
open(S+"vd_engine.py","w").write(v); print("ok")
