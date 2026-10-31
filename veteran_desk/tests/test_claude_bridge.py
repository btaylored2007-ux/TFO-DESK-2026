"""Claude bridge. PAPER ONLY. Advice never changes a trade and never waits on the trade path."""
import importlib.util, json, os, signal, subprocess, sys, time
import datetime as dt
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from veteran_desk import claude_review as CR

def _load(name):
    path = os.path.join(ROOT, "tools", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
D = _load("claude_bridge_daemon")
W = _load("claude_bridge_watchdog")
S = _load("claude_scorecard")
FAKE = r'''#!/usr/bin/env python3
import os, sys, time, signal
mode = open(os.environ["CLAUDE_FAKE_MODE"]).read().strip()
prompt = sys.stdin.read()
count_path = os.environ.get("CLAUDE_FAKE_COUNT")
pid_path = os.environ.get("CLAUDE_FAKE_PID")
if pid_path:
    try: open(pid_path, "w").write(str(os.getpid()))
    except Exception: pass
def bump():
    if not count_path or "CANDIDATE" not in prompt: return 0
    try: n = int(open(count_path).read() or "0")
    except Exception: n = 0
    open(count_path, "w").write(str(n + 1))
    return n
if "TFO_CLAUDE_SELFTEST" in prompt or "TFO_CLAUDE_WARM" in prompt:
    if mode == "auth":
        sys.stderr.write("Error: not logged in. Please run /login\n"); sys.exit(1)
    sys.stdout.write('{"ok":true}\n'); sys.exit(0)
n = bump()
if mode == "auth":
    sys.stderr.write("not logged in. Please run /login\n"); sys.exit(1)
if mode == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30); sys.exit(0)
if mode == "slow_disagree":
    time.sleep(0.6)
    sys.stdout.write('{"verdict":"disagree","confidence":0.91,"risks":["gap through stop"],"reason":"advice only"}\n'); sys.exit(0)
if mode == "empty":
    if n == 0: sys.exit(0)
    sys.stdout.write('{"verdict":"agree","confidence":0.4,"risks":["thin book"],"reason":"second look"}\n'); sys.exit(0)
if mode == "ratelimit":
    if n == 0:
        sys.stderr.write("429 too many requests\n"); sys.exit(1)
    sys.stdout.write('{"verdict":"agree","confidence":0.7,"risks":[],"reason":"after backoff"}\n'); sys.exit(0)
if mode == "network":
    sys.stderr.write("network is unreachable\n"); sys.exit(1)
if "code/PR" in prompt:
    sys.stdout.write('{"verdict":"disagree","confidence":0.6,"risks":["untested edge"],"reason":"request changes"}\n'); sys.exit(0)
if "post-mortem" in prompt:
    sys.stdout.write('{"verdict":"agree","confidence":0.55,"risks":["open item"],"reason":"plan is sound"}\n'); sys.exit(0)
sys.stdout.write('{"verdict":"agree","confidence":0.5,"risks":[],"reason":"within rules"}\n')
'''
def _wait_until(fn, timeout=4):
    end = time.time() + timeout
    while time.time() < end:
        if fn(): return True
        time.sleep(0.02)
    return False
def _health(port):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.3) as r:
            return r.status == 200
    except Exception:
        return False
@pytest.fixture
def bridge(tmp_path, monkeypatch):
    held = []
    def make(mode="ok", **kw):
        d = tmp_path / f"b{len(held)}"; d.mkdir()
        modep, script, count, pidf = d / "mode", d / "claude", d / "count", d / "pid"
        modep.write_text(mode); script.write_text(FAKE); script.chmod(0o755); count.write_text("0")
        monkeypatch.setenv("CLAUDE_FAKE_MODE", str(modep))
        monkeypatch.setenv("CLAUDE_FAKE_COUNT", str(count))
        monkeypatch.setenv("CLAUDE_FAKE_PID", str(pidf))
        monkeypatch.setenv("TFO_CLAUDE_BRIDGE_DIR", str(d))
        monkeypatch.delenv("TFO_CLAUDE_BRIDGE_PORT", raising=False)
        kw.setdefault("timeout_s", 2); kw.setdefault("warm_s", 10 ** 9); kw.setdefault("selftest_s", 3)
        kw.setdefault("concurrency", 2); kw.setdefault("rss_exit", False)
        b = D.Bridge(str(d), bin_path=kw.pop("bin_path", str(script)), port=0, **kw); b.start(); held.append(b)
        assert _wait_until(lambda: b.self_test_state in ("ok", "fail")), b.self_test_state
        return b, modep, count, pidf
    yield make
    for b in held:
        try: b.stop()
        except Exception: pass
    CR.drain(0.2)
def _decision():
    return dict(ok=True, passed=True, shares=50, state="armed", stop=96.0, status="open", exit=None, risk_pct=0.04)
def _cand(**k):
    c = dict(desk="vd", seat="banks", ticker="XOM", side="LONG", grade="A+", entry=100, stop=96, target=110, risk_pct=0.04, trade_id="T1", paper_only=True)
    c.update(k); return c
def test_parse_redact_and_desks():
    assert CR.parse_model('note {"verdict":"approve","confidence":1.4,"risks":"gap","reason":"ok"}')["verdict"] == "agree"
    assert CR.parse_model('{"verdict":"request_changes","confidence":0.2,"risks":["a"],"reason":"no"}')["verdict"] == "disagree"
    assert CR.parse_model("") is None
    assert CR.redact("sk-TESTONLYNOTAREALKEY123456") == "[redacted]"
    assert CR.norm_desk("taylor") == "thf" and CR.norm_desk("kraken") == "crypto" and CR.norm_desk("nope") == "other"
    for desk in CR.DESKS:
        ack = CR.shadow(dict(desk=desk, ticker="ABC", seat="s", paper_only=True))
        assert ack["desk"] == desk and ack["blocks_arm"] is False and ack["applied"] is False and ack["latency_s"] == 0.0 and ack["verdict"] is None
def test_disagree_timeout_and_crash_change_nothing_and_add_no_latency(bridge, monkeypatch):
    """Brett: a disagree, a timeout or a crash does not change the trade and does not wait."""
    monkeypatch.setenv("VET_CLAUDE_IN_TESTS", "1")
    b, mode, _count, _pid = bridge(mode="slow_disagree", timeout_s=1, cache_s=0)
    assert b.self_test_state == "ok"
    decision, before = _decision(), None
    before = json.loads(json.dumps(decision))
    cand = _cand()
    def path(c, d):
        # The desk has already decided. Claude is notified and the stop/exit path does not consult it.
        return CR.observe(c, d, tiebreak_ok=True)
    t0 = time.perf_counter(); out = path(cand, decision); elapsed = time.perf_counter() - t0
    assert elapsed < 0.1, elapsed
    assert out is decision and decision == before
    logp = os.path.join(b.dir, "calls.jsonl")
    def _hit(tid, **want):
        return any(all(r.get(k) == v for k, v in want.items()) and r.get("trade_id") == tid for r in CR.read_calls(logp))
    assert _wait_until(lambda: _hit("T1", verdict="disagree"), 4)
    logged = next(r for r in CR.read_calls(logp) if r.get("verdict") == "disagree")
    assert logged["applied"] is False and logged["blocks_arm"] is False and logged["advisory_only"] is True
    assert logged["role"] == "tiebreak" and decision == before
    mode.write_text("hang")
    hang = _cand(trade_id="T-HANG")
    t0 = time.perf_counter(); path(hang, decision); assert time.perf_counter() - t0 < 0.1
    assert decision == before
    assert _wait_until(lambda: any(r.get("trade_id") == "T-HANG" and r.get("error") == "timeout" for r in CR.read_calls(os.path.join(b.dir, "calls.jsonl"))), 4)
    assert decision == before
    # explicit tiebreak() and review(wait=True) are also non-blocking for a trade
    t0 = time.perf_counter(); ack = CR.tiebreak(_cand(trade_id="T-TB")); assert time.perf_counter() - t0 < 0.1
    assert ack["applied"] is False and ack["blocks_arm"] is False and ack["verdict"] is None
    t0 = time.perf_counter(); ack = CR.review(_cand(trade_id="T-WAIT", mode="trade"), wait=True, timeout=30)
    assert time.perf_counter() - t0 < 0.1 and ack["queued"] is True and ack["verdict"] is None
    b.stop()
    monkeypatch.setenv("TFO_CLAUDE_BRIDGE_PORT", "1")
    try: os.remove(os.path.join(b.dir, "bridge.sock"))
    except OSError: pass
    t0 = time.perf_counter(); path(_cand(trade_id="T-CRASH"), decision); assert time.perf_counter() - t0 < 0.1
    assert decision == before
def test_seat_and_book_hooks_add_no_latency(bridge, monkeypatch):
    monkeypatch.setenv("VET_CLAUDE_IN_TESTS", "1")
    b, mode, _c, _p = bridge(mode="hang", timeout_s=1)
    from veteran_desk import seat_cli as SC, book as BK
    d = dict(ok=True, passed=True, shares=10, risk_pct=0.04, id="Z", stop=96.0)
    before = dict(d)
    t0 = time.perf_counter()
    out = SC._claude_note("banks", dict(ticker="XOM", side="LONG", grade="A+", entry=100, stop=96, target=110), dict(risk_pct=0.04), d)
    assert time.perf_counter() - t0 < 0.1 and out == before and d["shares"] == 10 and d["stop"] == 96.0
    t0 = time.perf_counter()
    BK._claude_shadow_arm("banks", dict(id="Z", ticker="XOM", side="LONG", target=110), "A+", 0.04, 100, 96, 10)
    assert time.perf_counter() - t0 < 0.1
    def boom(*a, **k): raise RuntimeError("claude down")
    monkeypatch.setattr(CR, "observe", boom)
    assert SC._claude_note("banks", dict(ticker="XOM", grade="A"), {}, dict(d))["shares"] == 10
    BK._claude_shadow_arm("banks", dict(id="Z", ticker="XOM"), "A+", 0.05, 1, 0.9, 1)  # must not raise
    src = open(os.path.join(ROOT, "veteran_desk", "book.py")).read()
    assert src.count("_claude_shadow_arm(") == 2
    assert src.find("_claude_shadow_arm(seat, e, _grade, pct, entry, stop, q)") > src.find('status="armed", queued_for_open')
def test_circuit_auth_hung_cache_retry_and_network(bridge):
    b, mode, count, pidf = bridge(mode="ok", cb_fails=2, cb_cooldown=0.3, backoffs=(0.05, 0.05), cache_s=30)
    secret = "sk-TESTONLYNOTAREALKEY123456"
    blocked = b.handle(dict(mode="trade", desk="vd", ticker="XOM", thesis="leak " + secret))
    assert blocked["error"] == "prompt_blocked" and blocked["blocks_arm"] is False
    assert secret not in open(os.path.join(b.dir, "calls.jsonl")).read()
    code = b.handle(dict(mode="code", wait=True, desk="vd", diff="parser fix", title="PR"))
    post = b.handle(dict(mode="postmortem", wait=True, desk="thf", notes="weekly recovery"))
    assert code["verdict"] == "disagree" and code["blocks_arm"] is False and "request changes" in code["reason"]
    assert post["verdict"] == "agree" and post["advisory_only"] is True
    n = int(count.read_text() or "0")
    again = b.handle(dict(mode="code", wait=True, desk="vd", diff="parser fix", title="PR"))
    assert again.get("cached") is True and int(count.read_text() or "0") == n
    mode.write_text("empty"); count.write_text("0")
    empty = b.handle(dict(mode="code", wait=True, desk="entry", diff="empty-case-unique", timeout_s=3))
    assert empty["verdict"] == "agree" and int(count.read_text()) == 2
    mode.write_text("ratelimit"); count.write_text("0")
    limited = b.handle(dict(mode="code", wait=True, desk="crypto", diff="rate-unique", timeout_s=3))
    assert limited["verdict"] == "agree" and limited["reason"] == "after backoff"
    mode.write_text("network")
    net = b.handle(dict(mode="code", wait=True, desk="oanda", diff="net1", timeout_s=2))
    assert net["error"] == "network" and _health(b.port)
    mode.write_text("auth")
    auth = b.handle(dict(mode="code", wait=True, desk="fundamental", diff="auth1", timeout_s=2))
    assert auth["error"] == "login_expired" and b.login_expired and "claude_login_expired" in b.status()["alerts"]
    opened = b.handle(dict(mode="code", wait=True, desk="fundamental", diff="auth2", timeout_s=2))
    assert opened["error"] == "circuit_open"
    mode.write_text("ok"); time.sleep(0.4)
    recovered = b.handle(dict(mode="code", wait=True, desk="v2", diff="recovered-unique", timeout_s=2))
    assert recovered["verdict"] == "disagree" and recovered.get("error") is None and b.login_expired is False
    mode.write_text("hang"); count.write_text("0")
    t0 = time.perf_counter()
    ack = b.handle(dict(mode="trade", desk="hunt", ticker="BHP", trade_id="HANG1", grade="A"))
    assert time.perf_counter() - t0 < 0.1 and ack["queued"] is True and ack["blocks_arm"] is False
    assert _wait_until(lambda: any(r.get("trade_id") == "HANG1" and r.get("error") == "timeout" for r in CR.read_calls(os.path.join(b.dir, "calls.jsonl"))), 4)
    hung = int(pidf.read_text() or "0")
    def dead():
        try: os.kill(hung, 0); return False
        except OSError: return True
    assert _wait_until(dead, 2)
    st = b.status()
    for key in ("up", "queue_depth", "p50_latency_s", "p95_latency_s", "error_rate", "circuit", "alerts"):
        assert key in st
    assert st["paper_only"] is True and st["blocks_arm"] is False
def test_jsonl_rotation_clock_stale_and_zombies(tmp_path):
    path = str(tmp_path / "calls.jsonl")
    j = CR.Jsonl(path, max_bytes=280, keep=3)
    for i in range(15): j.write(dict(i=i, pad="y" * 30, advisory_only=True))
    with open(path, "a") as fh: fh.write("{torn-no-close")
    rows = CR.read_calls(path)
    assert len(rows) >= 8 and all("i" in r for r in rows)
    assert os.path.exists(path + ".1")
    assert D.clock_jumped(0, 0, 100, 1) is True and D.clock_jumped(0, 0, 5, 4) is False
    script = tmp_path / "claude"; script.write_text("#!/bin/sh\nsleep 30\n"); script.chmod(0o755)
    child = subprocess.Popen([str(script)], start_new_session=True)
    other = subprocess.Popen(["sleep", "30"])
    try:
        assert child.pid in D.cleanup_stale([child.pid, other.pid], str(script))
        assert _wait_until(lambda: child.poll() is not None, 2)
        assert other.poll() is None
    finally:
        for p in (child, other):
            try: p.kill()
            except Exception: pass
    z = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"], start_new_session=True)
    time.sleep(0.15); D.reap_zombies()
    proc = f"/proc/{z.pid}/status"
    if os.path.exists(proc):
        assert "zombie" not in open(proc).read().lower()
    z.wait(timeout=1)
def test_wake_kills_inflight_without_stopping_the_socket(bridge):
    b, mode, _c, pidf = bridge(mode="hang", timeout_s=30, selftest_s=2)
    # self-test is fast (hang mode still answers self-test). Then park a CLI child via the worker.
    assert b.self_test_state == "ok"
    b.handle(dict(mode="trade", desk="vd", ticker="XOM", trade_id="WAKE", grade="B"))
    assert _wait_until(lambda: pidf.exists() and pidf.read_text().strip().isdigit(), 2)
    pid = int(pidf.read_text())
    b.on_wake()
    assert _wait_until(lambda: _dead(pid), 2)
    assert _health(b.port)  # wake drops the hung CLI and leaves the socket up
def _dead(pid):
    try: os.kill(pid, 0); return False
    except OSError: return True
def test_watchdog_restarts_hang_not_login_alert():
    now = time.time(); fresh = dt.datetime.now(dt.timezone.utc).isoformat()
    assert W.needs_restart(False, {"utc": fresh})[0] is True
    need, why = W.needs_restart(True, {"utc": fresh, "inflight_oldest_s": 0, "login_expired": True})
    assert need is False and why == "ok"
    assert W.needs_restart(True, {"utc": fresh, "inflight_oldest_s": 200})[1] == "hung_inflight"
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)).isoformat()
    assert W.needs_restart(True, {"utc": stale, "inflight_oldest_s": 0}, now=now)[1] == "stale_heartbeat"
    assert W.needs_restart(True, {"utc": fresh, "inflight_oldest_s": 0, "rss_mb": 2000}, rss_limit=1024)[1] == "memory"
def test_scorecard_two_week_window(tmp_path):
    now = dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc)
    new, old = (now - dt.timedelta(days=2)).isoformat(), (now - dt.timedelta(days=20)).isoformat()
    calls = [
        dict(utc=new, trade_id="T1", verdict="agree", desk="vd"),
        dict(utc=new, trade_id="T2", verdict="disagree", desk="hunt"),
        dict(utc=new, trade_id="T3", verdict="abstain", desk="oanda", error="timeout"),
        dict(utc=old, trade_id="T9", verdict="agree", desk="vd"),
        dict(utc=new, trade_id="T4", verdict="disagree", desk="v2"),
        dict(kind="outcome", utc=new, trade_id="T4", net_aud=15, win=True),
    ]
    book = {"seats": {"banks": {"closed": [
        dict(id="T1", ticker="XOM", net_aud=12, status="closed"),
        dict(id="T2", ticker="BHP", net_aud=-3, status="closed")]}}}
    bp = tmp_path / "book.json"; json.dump(book, open(bp, "w"))
    rep = S.score(calls, S.outcomes_from_book(str(bp)), now=now, days=14)
    assert rep["agree_and_won"] == 1 and rep["disagree_and_lost"] == 1 and rep["disagree_and_won"] == 1
    assert rep["abstain"] == 1 and rep["calls"] == 4 and rep["window_days"] == 14
    assert "T9" not in json.dumps(rep)
def test_missing_cli_stays_up_and_refuses_live(bridge):
    b, _m, _c, _p = bridge(mode="ok", bin_path="/nonexistent/claude", cb_fails=1, cb_cooldown=5)
    # factory passes bin_path via kw into Bridge, overriding the fake script. self-test fails.
    assert b.self_test_state == "fail" and "claude_cli_missing" in b.status()["alerts"]
    r = b.handle(dict(mode="code", wait=True, desk="vd", diff="x"))
    assert r["error"] == "circuit_open" and r["blocks_arm"] is False and _health(b.port)
    live = b.handle(dict(mode="trade", desk="vd", ticker="XOM", live=True))
    assert live["error"] == "refused_live"
def test_chaos_kill9_hung_and_auth_recovers(tmp_path, monkeypatch):
    d = tmp_path / "run"; d.mkdir()
    mode, script, count = d / "mode", d / "claude", d / "count"
    mode.write_text("slow_disagree"); script.write_text(FAKE); script.chmod(0o755); count.write_text("0")
    err = open(d / "err", "w")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(TFO_CLAUDE_BRIDGE_DIR=str(d), TFO_CLAUDE_BIN=str(script), TFO_CLAUDE_BRIDGE_PORT="0",
               TFO_CLAUDE_TIMEOUT="1", TFO_CLAUDE_WARM_S="100000", TFO_CLAUDE_SELFTEST_S="3",
               TFO_CLAUDE_RSS_EXIT="0", CLAUDE_FAKE_MODE=str(mode), CLAUDE_FAKE_COUNT=str(count),
               CLAUDE_FAKE_PID=str(d / "pid"), VET_CLAUDE_IN_TESTS="1")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "claude_bridge_daemon.py")], cwd=ROOT, env=env,
                            stdin=subprocess.DEVNULL, stdout=err, stderr=err, start_new_session=True)
    monkeypatch.setenv("VET_CLAUDE_IN_TESTS", "1")
    monkeypatch.setenv("TFO_CLAUDE_BRIDGE_DIR", str(d))
    monkeypatch.delenv("TFO_CLAUDE_BRIDGE_PORT", raising=False)
    try:
        assert _wait_until(lambda: _health(_port_of(d)), 5), open(d / "err").read()[-800:]
        decision = _decision(); before = json.loads(json.dumps(decision))
        t0 = time.perf_counter(); CR.observe(_cand(trade_id="CHAOS"), decision, tiebreak_ok=True)
        assert time.perf_counter() - t0 < 0.1 and decision == before
        os.kill(proc.pid, signal.SIGKILL); proc.wait(timeout=3)
        t0 = time.perf_counter(); CR.observe(_cand(trade_id="DEAD"), decision, tiebreak_ok=True)
        assert time.perf_counter() - t0 < 0.1 and decision == before
        monkeypatch.setenv("TFO_CLAUDE_WATCHDOG_SPAWN", "1")
        monkeypatch.setenv("TFO_CLAUDE_WATCHDOG_LOG", str(d / "err"))
        monkeypatch.setenv("PYTHONUNBUFFERED", "1")
        for k, v in env.items():
            if k.startswith("TFO_") or k.startswith("CLAUDE_"): monkeypatch.setenv(k, v)
        assert W.check_once(str(d)) == "health_down"
        assert _wait_until(lambda: _health(_port_of(d)), 5), open(d / "err").read()[-800:]
        mode.write_text("hang")
        t0 = time.perf_counter(); CR.observe(_cand(trade_id="HUNG2"), decision, tiebreak_ok=True)
        assert time.perf_counter() - t0 < 0.1 and decision == before
        assert _wait_until(lambda: any(r.get("error") == "timeout" and r.get("trade_id") == "HUNG2" for r in CR.read_calls(str(d / "calls.jsonl"))), 4)
        assert decision == before
        mode.write_text("auth")
        CR.shadow(_cand(trade_id="AUTH", desk="fundamental"))
        assert _wait_until(lambda: (W.read_status(str(d)) or {}).get("login_expired") is True, 4)
        assert decision == before
    finally:
        err.close()
        for pid in (proc.pid, _pid_of(d)):
            if not pid: continue
            try: os.kill(pid, signal.SIGKILL)
            except OSError: pass
def _port_of(d):
    try: return int(open(os.path.join(str(d), "port")).read().strip())
    except Exception: return 0
def _pid_of(d):
    try: return int(open(os.path.join(str(d), "bridge.pid")).read().strip())
    except Exception: return None
