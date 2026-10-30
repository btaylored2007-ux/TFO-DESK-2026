"""tfo_fastllm_20261030 (Brett 23:2x WITA): our own local model service. PAPER ONLY.
ask(prompt) -> runs Cursor CLI headless (Grok model, ask mode = no edits, empty sandbox workspace), one fast-path retry.
Key loaded from ~/.tfo_secrets/cursor_api_key at call time (never inlined/logged). Returns (text, status) status in ok|err.
Callers (llm_backend.grok_call) fall back to the state/llm_queue file queue on err."""
import json, os, subprocess, time, threading, fcntl, contextlib, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.expanduser(os.environ.get("TFO_CURSOR_CLI", "~/.local/bin/cursor-agent"))
KEYF = os.path.expanduser("~/.tfo_secrets/cursor_api_key")
MODEL = os.environ.get("TFO_FAST_MODEL", "grok-4.7-low-fast")   # measured 23:5x: 23KB prompt 56s low-fast; medium-fast timed out at 90s
TIMEOUT = int(os.environ.get("TFO_FAST_TIMEOUT", "120"))
WS = os.path.expanduser("~/.tfo_llm_ws")   # empty sandbox dir: CLI sees no repo, no ledgers
LOG = os.path.join(BASE, "reports", "queue_answerer.jsonl")
# per-desk lanes so VD and THF never block each other
_LANES = {"vd": int(os.environ.get("TFO_LANE_VD", "3")), "thf": int(os.environ.get("TFO_LANE_THF", "2")), "other": 2}
_SEM = {k: threading.BoundedSemaphore(v) for k, v in _LANES.items()}
def enabled(): return os.environ.get("TFO_FAST_LLM", "1") == "1" and os.path.exists(CLI) and os.path.exists(KEYF)
def _env():
    e = dict(os.environ); e["CURSOR_API_KEY"] = open(KEYF).read().strip(); return e
def _log(rec):
    try: os.makedirs(os.path.dirname(LOG), exist_ok=True); open(LOG, "a").write(json.dumps(rec) + "\n")
    except Exception: pass
def _once(prompt, timeout):
    os.makedirs(WS, exist_ok=True)
    cmd = [CLI, "-p", "--output-format", "text", "--model", MODEL, "--mode", "ask", "--trust", "--workspace", WS]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=_env(), cwd=WS)
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out: raise RuntimeError(f"rc={p.returncode} {(p.stderr or out)[-200:]}")
    return out
GLOBAL_CAP = int(os.environ.get("TFO_CLI_GLOBAL_CAP", "3"))
CAP_CFG = os.path.join(BASE, "state", "llm_cap.json")   # tfo_fastllm4: {"cap": N} live-tunable without redeploy
def _cap():
    try: return max(1, min(6, int(json.load(open(CAP_CFG)).get("cap", GLOBAL_CAP))))
    except Exception: return GLOBAL_CAP   # tfo_fastllm3: Mac-wide cap on concurrent cursor-agent calls
SLOTS = os.path.join(WS, ".slots")
@contextlib.contextmanager
def _global_slot(deadline):
    """File-lock semaphore across all processes: wait (queued, not failed) for one of GLOBAL_CAP slots until deadline."""
    os.makedirs(SLOTS, exist_ok=True); fh = None
    while fh is None:
        for i in range(_cap()):
            f = open(os.path.join(SLOTS, f"slot{i}.lock"), "a+")
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); fh = f; break
            except OSError: f.close()
        if fh is None:
            if time.time() >= deadline: yield False; return
            time.sleep(0.25)
    try: yield True
    finally:
        try: fcntl.flock(fh, fcntl.LOCK_UN); fh.close()
        except Exception: pass
def ask(prompt, desk="other", seat=None, timeout=None, retries=1, rid=None, src="direct"):
    if not enabled(): return "__ERR__ fast_llm_disabled", "err"
    lane = desk if desk in _SEM else "other"; t0 = time.time(); err = None; tries = 0
    with _SEM[lane]:
        budget = timeout or TIMEOUT; deadline = t0 + budget; tw = None
        for tries in range(1, retries + 2):
            try:
                with _global_slot(deadline - 10) as got:
                    tw = round(time.time() - t0, 1)
                    if not got: raise RuntimeError(f"global_cap_wait {tw}s (cap {_cap()})")
                    out = _once(prompt, max(10, deadline - time.time()))
                _log(dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), src=src, id=rid, desk=desk, seat=seat, model=MODEL, ok=True, tries=tries, wait_lane_s=tw, secs=round(time.time() - t0, 1), prompt_chars=len(prompt), chars=len(out)))
                return out, "ok"
            except Exception as e:
                err = str(e)[:200]
                if time.time() - t0 > 30: break   # fast-path retry only after a quick failure; a timeout goes straight to the queue
    _log(dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), src=src, id=rid, desk=desk, seat=seat, model=MODEL, ok=False, tries=tries, secs=round(time.time() - t0, 1), prompt_chars=len(prompt), err=err))
    return "__ERR__ fast_llm " + (err or ""), "err"
