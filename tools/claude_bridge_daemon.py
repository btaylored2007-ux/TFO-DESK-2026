#!/usr/bin/env python3
"""Persistent Claude review bridge. PAPER ONLY. ADVISORY ONLY.

Localhost HTTP + unix socket. Worker pool, queue, timeout, empty-answer retry,
subscription rate-limit backoff, short identical-prompt cache, circuit breaker,
login-expiry alert, warm ping, sleep/wake recovery, stale-child cleanup.
Trade requests are queued and acked immediately. Nothing here arms, stops or exits.

  python3 tools/claude_bridge_daemon.py
Env: TFO_CLAUDE_BRIDGE_DIR, TFO_CLAUDE_BIN (default ~/.local/bin/claude),
     TFO_CLAUDE_BRIDGE_PORT (default 8766; 0 = ephemeral), TFO_CLAUDE_TIMEOUT (60),
     TFO_CLAUDE_CONCURRENCY, TFO_CLAUDE_CACHE_S, TFO_CLAUDE_CB_FAILS, TFO_CLAUDE_CB_COOLDOWN,
     TFO_CLAUDE_BACKOFF (comma seconds), TFO_CLAUDE_WARM_S, TFO_CLAUDE_RSS_LIMIT_MB.
"""
import fcntl, hashlib, json, os, re, signal, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path: sys.path.insert(0, BASE)
from veteran_desk import claude_review as CR
AUTH_RX = re.compile(r"(please run /login|not logged in|login expired|invalid api key|authentication required|unauthorized)", re.I)
RATE_RX = re.compile(r"(rate limit|too many requests|\b429\b|overloaded)", re.I)
NET_RX = re.compile(r"(network is unreachable|connection reset|temporary failure|could not resolve|EAI_AGAIN|ENETDOWN|network drop)", re.I)
def iso(): return CR.now().isoformat()
def rss_mb():
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024.0
    except Exception: return None
def cmdline(pid):
    try: return open(f"/proc/{int(pid)}/cmdline", "rb").read().replace(b"\x00", b" ").decode(errors="replace")
    except Exception: pass
    try: return subprocess.check_output(["ps", "-p", str(int(pid)), "-o", "command="], text=True, timeout=2)
    except Exception: return ""
def reap_zombies():
    n = 0
    while True:
        try: pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError: break
        except OSError: break
        if not pid: break
        n += 1
    return n
def killpg(pid):
    try: os.killpg(int(pid), signal.SIGKILL); return True
    except Exception:
        try: os.kill(int(pid), signal.SIGKILL); return True
        except Exception: return False
def cleanup_stale(pids, bin_path):
    """Kill leftover CLI children recorded by a dead bridge. Never touches the bridge process itself."""
    base = os.path.basename(bin_path or "claude")
    killed = []
    for pid in pids or []:
        try: os.kill(int(pid), 0)
        except OSError: continue
        cmd = cmdline(pid)
        if not cmd or "claude_bridge_" in cmd: continue
        if not any(os.path.basename(p) == base for p in cmd.split()): continue
        killpg(pid)
        try: os.kill(int(pid), signal.SIGKILL)
        except OSError: pass
        killed.append(int(pid))
    return killed
def clock_jumped(prev_wall, prev_mono, wall, mono, slack=15.0):
    dw, dm = wall - prev_wall, mono - prev_mono
    return dw > dm + slack or dw < -2
class Circuit:
    def __init__(self, limit=5, cooldown=30.0):
        self.limit, self.cooldown = limit, cooldown
        self.fails, self.state, self.open_until, self._probe = 0, "closed", 0.0, False
        self.lock = threading.Lock()
    def allow(self):
        with self.lock:
            now = time.monotonic()
            if self.state == "closed": return True
            if self.state == "open":
                if now >= self.open_until:
                    self.state, self._probe = "half_open", True
                    return True
                return False
            if not self._probe:
                self._probe = True; return True
            return False
    def ok(self):
        with self.lock:
            self.fails, self.state, self._probe = 0, "closed", False
    def fail(self):
        with self.lock:
            self.fails += 1; self._probe = False
            if self.state == "half_open" or self.fails >= self.limit:
                self.state = "open"; self.open_until = time.monotonic() + self.cooldown
    def force_open(self):
        with self.lock:
            self.state, self._probe = "open", False
            self.open_until = time.monotonic() + self.cooldown
            self.fails = max(self.fails, self.limit)
    def half_open(self):
        with self.lock:
            self.state, self._probe = "half_open", False
class Bridge:
    def __init__(self, directory, bin_path=None, port=0, timeout_s=60, concurrency=2, cache_s=90, queue_max=32,
                 backoffs=(20.0, 60.0), cb_fails=5, cb_cooldown=30.0, warm_s=300, selftest_s=15, rss_limit_mb=1024, rss_exit=False):
        self.dir = directory; self.bin = bin_path or os.path.expanduser(os.environ.get("TFO_CLAUDE_BIN", "~/.local/bin/claude"))
        self.port_req, self.timeout_s, self.cache_s = int(port), float(timeout_s), float(cache_s)
        self.queue_max, self.backoffs = int(queue_max), tuple(float(x) for x in backoffs) or (20.0, 60.0)
        self.warm_s, self.selftest_s = float(warm_s), float(selftest_s)
        self.rss_limit_mb, self.rss_exit = rss_limit_mb, bool(rss_exit)
        self.workers_n = max(1, int(concurrency))
        self.circuit = Circuit(int(cb_fails), float(cb_cooldown))
        self.q = __import__("queue").Queue(maxsize=self.queue_max)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.cache, self.lats, self.inflight = {}, [], {}
        self.served = self.errors = 0
        self.alerts, self.login_expired = set(), False
        self.self_test_state, self.last_warm_ok, self.last_warm_utc = "pending", False, None
        self.children = {}
        self.http = self.sock = self._lock_fd = None
        self.port = None
        self._prev_wall, self._prev_mono = time.time(), time.monotonic()
        self.log = CR.Jsonl(os.path.join(self.dir, "calls.jsonl"))
    @classmethod
    def from_env(cls):
        d = os.environ.get("TFO_CLAUDE_BRIDGE_DIR") or os.path.join(BASE, "state", "claude_bridge")
        bo = os.environ.get("TFO_CLAUDE_BACKOFF", "20,60")
        return cls(d, port=int(os.environ.get("TFO_CLAUDE_BRIDGE_PORT", "8766")),
                   timeout_s=float(os.environ.get("TFO_CLAUDE_TIMEOUT", "60")),
                   concurrency=int(os.environ.get("TFO_CLAUDE_CONCURRENCY", "2")),
                   cache_s=float(os.environ.get("TFO_CLAUDE_CACHE_S", "90")),
                   queue_max=int(os.environ.get("TFO_CLAUDE_QUEUE", "32")),
                   backoffs=tuple(float(x) for x in bo.split(",") if x.strip()),
                   cb_fails=int(os.environ.get("TFO_CLAUDE_CB_FAILS", "5")),
                   cb_cooldown=float(os.environ.get("TFO_CLAUDE_CB_COOLDOWN", "30")),
                   warm_s=float(os.environ.get("TFO_CLAUDE_WARM_S", "300")),
                   selftest_s=float(os.environ.get("TFO_CLAUDE_SELFTEST_S", "15")),
                   rss_limit_mb=float(os.environ.get("TFO_CLAUDE_RSS_LIMIT_MB", "1024")),
                   rss_exit=os.environ.get("TFO_CLAUDE_RSS_EXIT", "1") == "1")
    def start(self):
        os.makedirs(self.dir, exist_ok=True)
        self._lock_fd = os.open(os.path.join(self.dir, "bridge.lock"), os.O_CREAT | os.O_RDWR, 0o644)
        try: fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("claude bridge already running")
        self._cleanup_recorded()
        self._bind_http(); self._bind_unix()
        open(os.path.join(self.dir, "port"), "w").write(str(self.port))
        self._write_pid(); self._write_status()
        for _ in range(self.workers_n):
            threading.Thread(target=self._worker, name="claude-worker", daemon=True).start()
        threading.Thread(target=self._http_loop, name="claude-http", daemon=True).start()
        threading.Thread(target=self._unix_loop, name="claude-unix", daemon=True).start()
        threading.Thread(target=self._status_loop, name="claude-status", daemon=True).start()
        threading.Thread(target=self._clock_loop, name="claude-clock", daemon=True).start()
        if self.warm_s > 0:
            threading.Thread(target=self._warm_loop, name="claude-warm", daemon=True).start()
        threading.Thread(target=self.self_test, name="claude-selftest", daemon=True).start()
        return self
    def _bind_http(self):
        bridge = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args): return
            def _send(self, obj, code=200):
                b = json.dumps(obj, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                try: self.wfile.write(b)
                except Exception: pass
            def do_GET(self):
                if self.path.startswith("/health"): self._send(dict(ok=True, up=True, pid=os.getpid(), paper_only=True, advisory_only=True))
                elif self.path.startswith("/status"): self._send(bridge.status())
                else: self._send(dict(ok=False, error="not_found"), 404)
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 262144: self._send(bridge._abstain({}, "too_large"), 413); return
                raw = self.rfile.read(n) if n else b"{}"
                try: req = json.loads(raw.decode() or "{}")
                except Exception: self._send(bridge._abstain({}, "bad_json"), 400); return
                p = self.path.split("?")[0]
                if p.endswith("/code"): req["mode"] = "code"
                elif p.endswith("/postmortem"): req["mode"] = "postmortem"
                try: self._send(bridge.handle(req))
                except Exception: self._send(bridge._abstain(req if isinstance(req, dict) else {}, "internal"))
        class Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True
        self.http = Server(("127.0.0.1", self.port_req), H)
        self.port = self.http.server_address[1]
    def _http_loop(self):
        try: self.http.serve_forever(poll_interval=0.3)
        except Exception: return
    def _bind_unix(self):
        path = os.path.join(self.dir, "bridge.sock")
        try:
            if os.path.exists(path): os.remove(path)
        except OSError: pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path); os.chmod(path, 0o600); s.listen(64); s.settimeout(0.5); self.sock = s
    def _unix_loop(self):
        while not self.stopped.is_set():
            try: c, _ = self.sock.accept()
            except socket.timeout: continue
            except Exception:
                if self.stopped.is_set(): return
                time.sleep(0.2)
                try: self._bind_unix()
                except Exception: return
                continue
            threading.Thread(target=self._unix_client, args=(c,), daemon=True).start()
    def _unix_client(self, c):
        try:
            c.settimeout(2.0); buf = b""
            while b"\n" not in buf and len(buf) < 262144:
                chunk = c.recv(65536)
                if not chunk: break
                buf += chunk
            try: req = json.loads(buf.split(b"\n", 1)[0].decode() or "{}")
            except Exception: req = {}
            resp = self.handle(req if isinstance(req, dict) else {})
            c.sendall(json.dumps(resp, default=str).encode() + b"\n")
        except Exception: pass
        finally:
            try: c.close()
            except Exception: pass
    def _write_pid(self):
        tmp = os.path.join(self.dir, f"bridge.pid.{os.getpid()}.tmp")
        open(tmp, "w").write(str(os.getpid())); os.replace(tmp, os.path.join(self.dir, "bridge.pid"))
    def _note_child(self, pid):
        with self.lock:
            self.children[int(pid)] = time.monotonic()
            self._save_children()
    def _forget_child(self, pid):
        with self.lock:
            self.children.pop(int(pid), None)
            self._save_children()
    def _save_children(self):
        try:
            p = os.path.join(self.dir, "children.json")
            tmp = p + f".{os.getpid()}.tmp"
            json.dump(list(self.children), open(tmp, "w")); os.replace(tmp, p)
        except Exception: pass
    def _cleanup_recorded(self):
        try: pids = json.load(open(os.path.join(self.dir, "children.json")))
        except Exception: pids = []
        cleanup_stale(pids, self.bin)
        reap_zombies()
    def _kill_all(self):
        with self.lock: pids = list(self.children)
        for pid in pids: killpg(pid)
        reap_zombies()
    def run_cli(self, prompt, deadline):
        if not self.bin or not os.path.exists(self.bin):
            return "", "missing", 127
        timeout = max(0.1, deadline - time.monotonic())
        try:
            p = subprocess.Popen([self.bin, "-p", "--output-format", "text"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True, text=True)
        except Exception as e:
            return "", CR.redact(str(e))[:160], 127
        self._note_child(p.pid)
        try:
            try:
                out, err = p.communicate(prompt, timeout=timeout); rc = p.returncode
            except subprocess.TimeoutExpired:
                killpg(p.pid)
                try: out, err = p.communicate(timeout=1)
                except Exception: out, err = "", "timeout"
                rc = -9; err = "timeout"
        finally:
            if p.poll() is None: killpg(p.pid)
            try: p.wait(timeout=1)
            except Exception: killpg(p.pid)
            self._forget_child(p.pid); reap_zombies()
        return out or "", err or "", rc
    def _prompt(self, req):
        mode = req.get("mode") or "trade"
        body = {k: req.get(k) for k in CR._FIELDS if k in req and k not in ("request_id",)}
        if CR._has_secret(body): raise RuntimeError("prompt_blocked")
        blob = json.dumps(body, default=str)[:14000]
        if mode == "code":
            ask = "PAPER desk code/PR review. You cannot merge or deploy. Reply with ONE JSON object only: {\"verdict\":\"agree|disagree|abstain\",\"confidence\":0-1,\"risks\":[\"...\"],\"reason\":\"one line\"}. agree=approve, disagree=request changes."
        elif mode == "postmortem":
            ask = "PAPER desk post-mortem (recovery plan or weekly review). You cannot change positions. Reply with ONE JSON object only: {\"verdict\":\"agree|disagree|abstain\",\"confidence\":0-1,\"risks\":[\"open items\"],\"reason\":\"one line\"}. agree=plan is sound."
        else:
            ask = "PAPER-ONLY trade advisor. You cannot arm, veto, size, stop or exit. The desk ignores you if you are slow. Reply with ONE JSON object only: {\"verdict\":\"agree|disagree|abstain\",\"confidence\":0-1,\"risks\":[\"...\"],\"reason\":\"one line\"}. Use only the fields given."
        return ask + "\nCANDIDATE " + blob
    def _abstain(self, req, error, skipped=True):
        return self._pack(req or {}, "abstain", None, [], error, 0.0, error=error, skipped=skipped)
    def _pack(self, req, verdict, confidence, risks, reason, latency, error=None, skipped=False, cached=False):
        return dict(utc=iso(), desk=CR.norm_desk(req.get("desk")), seat=req.get("seat"), ticker=req.get("ticker"),
                    trade_id=req.get("trade_id"), mode=req.get("mode") or "trade", role=req.get("role") or "shadow",
                    verdict=verdict, confidence=confidence, risks=risks or [], reason=CR.redact(reason or "")[:240],
                    latency_s=round(float(latency or 0), 3), advisory_only=True, applied=False, blocks_arm=False,
                    paper_only=True, skipped=bool(skipped), error=error, cached=bool(cached),
                    request_id=req.get("request_id"), outcome=None, prompt_chars=req.get("_prompt_chars"),
                    prompt_hash=req.get("_prompt_hash"))
    def _log(self, rec):
        try: self.log.write(rec)
        except Exception: pass
        with self.lock:
            self.served += 1
            self.lats.append(rec.get("latency_s") or 0.0)
            if len(self.lats) > 200: self.lats = self.lats[-200:]
            if rec.get("error"): self.errors += 1
    def _cache_key(self, req):
        body = {k: req.get(k) for k in ("mode", "role", "desk", "seat", "ticker", "side", "entry", "stop", "target", "grade", "thesis", "diff", "notes", "title", "risk_pct")}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    def _cache_get(self, req):
        hit = self.cache.get(self._cache_key(req))
        if not hit: return None
        if hit[0] < time.monotonic():
            self.cache.pop(self._cache_key(req), None); return None
        return dict(hit[1], cached=True, latency_s=0.0, blocks_arm=False, advisory_only=True, applied=False)
    def _cache_put(self, req, packed):
        if packed.get("verdict") in ("agree", "disagree") and not packed.get("error"):
            self.cache[self._cache_key(req)] = (time.monotonic() + self.cache_s, dict(packed, cached=False))
    def handle(self, req):
        req = dict(req or {})
        mode = req.get("mode") or "trade"
        if mode not in ("trade", "code", "postmortem"): mode = "trade"
        req["mode"] = mode
        req.setdefault("request_id", CR.uuid.uuid4().hex[:16])
        if req.get("live") is True or req.get("paper_only") is False:
            rec = self._abstain(req, "refused_live"); self._log(rec); return rec
        if CR._has_secret(req):
            rec = self._abstain(req, "prompt_blocked"); self._log(rec); return rec
        hit = self._cache_get(req)
        if hit:
            hit["request_id"] = req.get("request_id"); self._log(hit); return hit
        # Trade path is always queued. Code/postmortem may wait only when the caller asked.
        shadow = mode == "trade" or bool(req.get("shadow")) or req.get("wait") is not True
        if mode == "trade": shadow = True
        if shadow:
            try: self.q.put_nowait(dict(req=req, box=None, ev=None))
            except Exception:
                rec = self._abstain(req, "queue_full"); self._log(rec); return rec
            return dict(ok=True, queued=True, advisory_only=True, applied=False, blocks_arm=False, verdict=None,
                        confidence=None, risks=[], reason=None, latency_s=0.0, mode=mode, role=req.get("role") or "shadow",
                        desk=req.get("desk"), seat=req.get("seat"), ticker=req.get("ticker"), request_id=req.get("request_id"))
        box, ev = {}, threading.Event()
        try: self.q.put_nowait(dict(req=req, box=box, ev=ev))
        except Exception:
            rec = self._abstain(req, "queue_full"); self._log(rec); return rec
        ev.wait(float(req.get("timeout_s") or self.timeout_s) + 1.0)
        return box.get("result") or self._abstain(req, "timeout")
    def _worker(self):
        while not self.stopped.is_set():
            try: item = self.q.get(timeout=0.4)
            except Exception: continue
            try: self._run_item(item)
            except Exception:
                rec = self._abstain(item.get("req") or {}, "internal"); self._log(rec)
                if item.get("box") is not None: item["box"]["result"] = rec
                if item.get("ev") is not None: item["ev"].set()
            finally:
                try: self.q.task_done()
                except Exception: pass
    def _run_item(self, item):
        req = item["req"]; t0 = time.monotonic(); req["_t0"] = t0
        rid = req.get("request_id")
        with self.lock: self.inflight[rid] = t0
        try:
            timeout = float(req.get("timeout_s") or self.timeout_s)
            deadline = t0 + timeout
            if not self.circuit.allow():
                rec = self._abstain(req, "circuit_open")
            else:
                rec = self._call(req, deadline, t0)
            rec["blocks_arm"] = False; rec["advisory_only"] = True; rec["applied"] = False
            self._log(rec)
            if item.get("box") is not None: item["box"]["result"] = rec
        finally:
            with self.lock: self.inflight.pop(rid, None)
            if item.get("ev") is not None: item["ev"].set()
            self._write_status()
    def _call(self, req, deadline, t0):
        try: prompt = self._prompt(req)
        except RuntimeError:
            return self._pack(req, "abstain", None, [], "prompt_blocked", 0.0, error="prompt_blocked", skipped=True)
        req["_prompt_chars"] = len(prompt); req["_prompt_hash"] = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        last = "empty"
        for i in range(2):
            if time.monotonic() >= deadline or self.stopped.is_set():
                self.circuit.fail(); return self._pack(req, "abstain", None, [], "timeout", time.monotonic() - t0, error="timeout", skipped=True)
            out, err, rc = self.run_cli(prompt, deadline)
            blob = (out or "") + "\n" + (err or "")
            if err == "timeout" or rc == -9:
                self.circuit.fail(); return self._pack(req, "abstain", None, [], "timeout", time.monotonic() - t0, error="timeout", skipped=True)
            if AUTH_RX.search(blob):
                self.login_expired = True; self.alerts.add("claude_login_expired"); self.circuit.fail()
                return self._pack(req, "abstain", None, [], "claude login expired", time.monotonic() - t0, error="login_expired", skipped=True)
            if RATE_RX.search(blob):
                delay = self.backoffs[min(i, len(self.backoffs) - 1)]
                if i == 0 and deadline - time.monotonic() > delay + 0.5:
                    time.sleep(delay); continue
                self.circuit.fail(); return self._pack(req, "abstain", None, [], "rate_limited", time.monotonic() - t0, error="rate_limited", skipped=True)
            if rc != 0 and NET_RX.search(blob):
                if i == 0 and time.monotonic() + 0.2 < deadline:
                    time.sleep(0.05); continue
                self.circuit.fail(); return self._pack(req, "abstain", None, [], "network", time.monotonic() - t0, error="network", skipped=True)
            parsed = CR.parse_model(out)
            if parsed:
                self.circuit.ok(); self.login_expired = False; self.alerts.discard("claude_login_expired")
                rec = self._pack(req, parsed["verdict"], parsed["confidence"], parsed["risks"], parsed["reason"], time.monotonic() - t0)
                self._cache_put(req, rec); return rec
            last = "empty" if not (out or "").strip() else "unparsed"
        self.circuit.fail()
        return self._pack(req, "abstain", None, [], last, time.monotonic() - t0, error=last, skipped=True)
    def self_test(self):
        self.self_test_state = "running"
        if not self.bin or not os.path.exists(self.bin):
            self.self_test_state = "fail"; self.alerts.add("claude_cli_missing"); self.circuit.force_open(); self._write_status(); return
        out, err, rc = self.run_cli('TFO_CLAUDE_SELFTEST reply with {"ok":true} only', time.monotonic() + self.selftest_s)
        blob = (out or "") + "\n" + (err or "")
        if AUTH_RX.search(blob):
            self.login_expired = True; self.alerts.add("claude_login_expired"); self.self_test_state = "fail"; self.circuit.force_open()
        elif rc == 0 and (out or "").strip() and err != "timeout":
            self.self_test_state = "ok"; self.circuit.ok(); self.alerts.discard("claude_cli_missing")
        else:
            self.self_test_state = "fail"; self.circuit.fail()
        self._write_status()
    def warm_once(self):
        if self.stopped.is_set() or not self.circuit.allow(): return
        out, err, rc = self.run_cli("TFO_CLAUDE_WARM reply with the single word pong", time.monotonic() + min(20.0, self.selftest_s))
        blob = (out or "") + "\n" + (err or "")
        if AUTH_RX.search(blob):
            self.login_expired = True; self.alerts.add("claude_login_expired"); self.last_warm_ok = False; self.circuit.fail()
        elif rc == 0 and err != "timeout":
            self.last_warm_ok = True; self.last_warm_utc = iso(); self.login_expired = False; self.alerts.discard("claude_login_expired"); self.circuit.ok()
        else:
            self.last_warm_ok = False
        self._write_status()
    def _warm_loop(self):
        while not self.stopped.wait(self.warm_s):
            try: self.warm_once()
            except Exception: pass
    def on_wake(self):
        """Sleep, wake, or a network drop: drop hung CLI children and probe again. The socket stays up."""
        self._kill_all(); self.circuit.half_open(); self._write_status()
        if not self.stopped.is_set():
            threading.Thread(target=self.self_test, daemon=True).start()
    def _clock_loop(self):
        while not self.stopped.wait(2.0):
            w, m = time.time(), time.monotonic()
            if clock_jumped(self._prev_wall, self._prev_mono, w, m):
                try: self.on_wake()
                except Exception: pass
            self._prev_wall, self._prev_mono = w, m
    def oldest(self):
        with self.lock:
            if not self.inflight: return 0.0
            return time.monotonic() - min(self.inflight.values())
    def status(self):
        with self.lock:
            lats = sorted(self.lats); n = len(lats); served, errors = self.served, self.errors
        rss = rss_mb()
        return dict(up=not self.stopped.is_set(), paper_only=True, advisory_only=True, blocks_arm=False, pid=os.getpid(), utc=iso(),
                    queue_depth=self.q.qsize(), inflight=len(self.inflight), inflight_oldest_s=round(self.oldest(), 3),
                    p50_latency_s=(lats[n // 2] if n else None), p95_latency_s=(lats[min(n - 1, int(n * 0.95))] if n else None),
                    error_rate=(round(errors / served, 4) if served else 0.0), circuit=self.circuit.state, alerts=sorted(self.alerts),
                    login_expired=bool(self.login_expired), self_test=self.self_test_state, warm=bool(self.last_warm_ok),
                    last_warm_utc=self.last_warm_utc, rss_mb=round(rss, 2) if rss is not None else None,
                    concurrency=self.workers_n, cache_entries=len(self.cache), served=served, errors=errors)
    def _write_status(self):
        try:
            p = os.path.join(self.dir, "status.json"); tmp = p + f".{os.getpid()}.{threading.get_ident()}.tmp"
            json.dump(self.status(), open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
        except Exception: pass
    def _status_loop(self):
        while not self.stopped.wait(2.0):
            self._write_status(); reap_zombies()
            rss = rss_mb()
            if self.rss_exit and self.rss_limit_mb and rss is not None and rss > self.rss_limit_mb:
                self._kill_all(); os._exit(75)
    def stop(self):
        self.stopped.set(); self._kill_all()
        try:
            if self.http: threading.Thread(target=self.http.shutdown, daemon=True).start()
        except Exception: pass
        try:
            if self.sock: self.sock.close()
        except Exception: pass
        try: os.remove(os.path.join(self.dir, "bridge.sock"))
        except OSError: pass
        if self._lock_fd is not None:
            try: fcntl.flock(self._lock_fd, fcntl.LOCK_UN); os.close(self._lock_fd)
            except Exception: pass
            self._lock_fd = None
        self._write_status()
def serve_forever():
    b = Bridge.from_env()
    def _stop(signum, frame):
        b.stop(); sys.exit(0)
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
    try: b.start()
    except RuntimeError:
        # Another live bridge holds the lock. Stand by and take over if it dies (KeepAlive also restarts us).
        lockp = os.path.join(b.dir, "bridge.lock"); fd = os.open(lockp, os.O_CREAT | os.O_RDWR, 0o644)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); os.close(fd); break
            except BlockingIOError: time.sleep(1)
        b.start()
    while not b.stopped.is_set(): time.sleep(0.5)
if __name__ == "__main__": serve_forever()
