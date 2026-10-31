"""Claude review bridge client. PAPER ONLY. ADVISORY ONLY.

Brett hard rule: Claude is a CEO's advisor. It must never block, veto, delay or halt
a trade, arm, stop, exit or desk process, including an A+ / oversized "tiebreak".
GPT, Grok (veteran_desk.llm_backend / tools/llm_queue_cli.py) and TFO rules decide.
This module only enqueues advice. The caller gets the same decision back, immediately.

Every desk uses the same calls: Veteran Desk, Taylor Hedge Fund, hunts, OANDA,
crypto, entry engines, Fundamental, V2.

  from veteran_desk.claude_review import observe, shadow, tiebreak, review
  observe(candidate, decision)          # trade path. returns decision unchanged
  shadow(candidate)                     # async advice, returns at once
  tiebreak(candidate)                   # A+ tag only. also returns at once
  review({..., "mode": "code"})        # code/PR or postmortem. trade mode never waits
"""
import datetime as dt, hashlib, json, os, re, socket, threading, uuid
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKS = ("vd", "thf", "hunt", "oanda", "crypto", "entry", "fundamental", "v2")
_ALIASES = {"veteran": "vd", "veteran_desk": "vd", "taylor": "thf", "hedge": "thf", "fx": "oanda", "kraken": "crypto", "main": "entry"}
SECRET_RX = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|api[_-]?key\s*[:=]\s*\S+|secret\s*[:=]\s*\S+|password\s*[:=]\s*\S+|authorization\s*[:=]\s*\S+|BEGIN [A-Z ]*PRIVATE KEY)",
    re.I)
_FIELDS = ("ticker", "side", "entry", "stop", "target", "grade", "thesis", "risk_pct", "shares", "desk", "seat",
           "horizon_days", "note", "diff", "notes", "title", "trade_id", "mode", "role", "oversized", "paper_only",
           "venue", "request_id", "advisory_only")
_TRIM = {"thesis": 500, "note": 500, "diff": 12000, "notes": 12000, "title": 200, "reason": 240}
_LOCK = threading.Lock()
_PENDING = []
def now(): return dt.datetime.now(dt.timezone.utc)
def redact(s):
    return SECRET_RX.sub("[redacted]", s if isinstance(s, str) else str(s or ""))
def bridge_dir():
    return os.environ.get("TFO_CLAUDE_BRIDGE_DIR") or os.path.join(BASE, "state", "claude_bridge")
def calls_path(): return os.path.join(bridge_dir(), "calls.jsonl")
def _port():
    env = os.environ.get("TFO_CLAUDE_BRIDGE_PORT")
    if env and env != "0":
        try: return int(env)
        except ValueError: pass
    try: return int(open(os.path.join(bridge_dir(), "port")).read().strip())
    except Exception: return int(os.environ.get("TFO_CLAUDE_BRIDGE_PORT_DEFAULT", "8766"))
def _pytest_skip():
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.environ.get("VET_CLAUDE_IN_TESTS") != "1"
def norm_desk(d):
    d = _ALIASES.get(str(d or "other").lower(), str(d or "other").lower())
    return d if d in DESKS else "other"
def wants_tiebreak(c):
    """Label only. Never a reason to wait."""
    g = str((c or {}).get("grade") or "").upper().replace(" ", "")
    if g in ("A+", "APLUS", "A_PLUS"): return True
    if str((c or {}).get("oversized")).lower() in ("1", "true", "yes"): return True
    try: r = float(c.get("risk_pct"))
    except (TypeError, ValueError): return False
    if r > 1: r = r / 100.0
    return r >= 0.05 - 1e-12
def _has_secret(obj):
    try: blob = json.dumps(obj, default=str)
    except Exception: return False
    return bool(SECRET_RX.search(blob))
def _public(c):
    out = {}
    for k in _FIELDS:
        if k not in (c or {}): continue
        v = c[k]
        if isinstance(v, str) and k in _TRIM: v = v[:_TRIM[k]]
        out[k] = v
    out["desk"] = norm_desk(out.get("desk"))
    out["paper_only"] = False if c.get("paper_only") is False or c.get("live") is True else True
    out["advisory_only"] = True
    out.setdefault("request_id", uuid.uuid4().hex[:16])
    return out
def _ack(c):
    c = c or {}
    return dict(ok=True, queued=True, advisory_only=True, applied=False, blocks_arm=False, verdict=None,
                confidence=None, risks=[], reason=None, latency_s=0.0, skipped=False, mode=c.get("mode") or "trade",
                role=c.get("role") or "shadow", desk=norm_desk(c.get("desk")), seat=c.get("seat"), ticker=c.get("ticker"),
                trade_id=c.get("trade_id"), request_id=c.get("request_id"))
class Jsonl:
    """Crash-safe append: one full line, then fsync. A torn tail has no newline and readers skip it. Rotates by size."""
    def __init__(self, path, max_bytes=8_000_000, keep=5):
        self.path, self.max_bytes, self.keep = path, max_bytes, keep
        self.lock = threading.Lock()
    def write(self, obj):
        data = (redact(json.dumps(obj, default=str, separators=(",", ":"))) + "\n").encode()
        with self.lock:
            d = os.path.dirname(self.path)
            if d: os.makedirs(d, exist_ok=True)
            self._rotate(len(data))
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data); os.fsync(fd)
            finally:
                os.close(fd)
    def _rotate(self, incoming):
        try: sz = os.path.getsize(self.path)
        except OSError: return
        if sz + incoming < self.max_bytes: return
        for i in range(self.keep, 1, -1):
            src, dst = f"{self.path}.{i - 1}", f"{self.path}.{i}"
            if os.path.exists(src):
                try: os.replace(src, dst)
                except OSError: pass
        try: os.replace(self.path, self.path + ".1")
        except OSError: pass
def read_calls(path=None):
    path = path or calls_path()
    rows, files = [], [path] + [f"{path}.{i}" for i in range(1, 8)]
    for p in files:
        if not os.path.exists(p): continue
        try: fh = open(p)
        except OSError: continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                try: rows.append(json.loads(line))
                except Exception: continue
    return rows
def _log_local(rec):
    try: Jsonl(calls_path()).write(rec)
    except Exception: pass
def _local_abstain(c, error):
    return dict(utc=now().isoformat(), desk=norm_desk(c.get("desk")), seat=c.get("seat"), ticker=c.get("ticker"),
                trade_id=c.get("trade_id"), mode=c.get("mode") or "trade", role=c.get("role") or "shadow",
                verdict="abstain", confidence=None, risks=[], reason=error, latency_s=0.0, advisory_only=True,
                applied=False, blocks_arm=False, paper_only=True, skipped=True, error=error, cached=False,
                request_id=c.get("request_id"), outcome=None)
def _rpc_unix(path, data, connect_s, read_s):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(connect_s); s.connect(path); s.settimeout(read_s)
    try:
        s.sendall(data + b"\n"); buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk: break
            buf += chunk
            if len(buf) > 1_000_000: break
    finally:
        s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode() or "{}")
def _rpc_http(port, data, connect_s, read_s, path="/v1/review"):
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=connect_s); s.settimeout(read_s)
    try:
        head = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode()
        s.sendall(head + data); buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk: break
            buf += chunk
            if len(buf) > 2_000_000: break
    finally:
        s.close()
    _h, _, body = buf.partition(b"\r\n\r\n")
    return json.loads(body.decode() or "{}")
def _rpc(payload, connect_s, read_s, path="/v1/review"):
    data = json.dumps(payload, default=str).encode()
    sock = os.path.join(bridge_dir(), "bridge.sock")
    if os.path.exists(sock):
        try: return _rpc_unix(sock, data, connect_s, read_s)
        except Exception: pass
    return _rpc_http(_port(), data, connect_s, read_s, path)
def _deliver(c):
    """Background only. Never called on a trade thread."""
    try:
        if c.get("live") is True or c.get("paper_only") is False:
            _log_local(_local_abstain(c, "refused_live")); return
        if _has_secret(c):
            _log_local(_local_abstain(c, "prompt_blocked")); return
        payload = _public(c); payload["shadow"] = True; payload["wait"] = False
        connect_s = float(os.environ.get("TFO_CLAUDE_CONNECT_S", "0.3"))
        read_s = float(os.environ.get("TFO_CLAUDE_ACK_S", "1.0"))
        resp = _rpc(payload, connect_s, read_s)
        if isinstance(resp, dict) and (resp.get("queued") or resp.get("verdict")): return
        _log_local(_local_abstain(c, "bridge_down"))
    except Exception:
        try: _log_local(_local_abstain(c, "bridge_down"))
        except Exception: pass
def _spawn(c):
    t = threading.Thread(target=_deliver, args=(c,), name="claude-advice", daemon=True)
    with _LOCK: _PENDING.append(t)
    t.start()
    return t
def drain(timeout=2.0):
    with _LOCK: ts = list(_PENDING)
    for t in ts: t.join(timeout)
def _enqueue(c):
    c = _public(c)
    if not _pytest_skip(): _spawn(c)
    return _ack(c)
def shadow(candidate):
    """Async advice for any desk. Returns immediately. blocks_arm is always false."""
    c = dict(candidate or {}); c["mode"] = c.get("mode") or "trade"; c["role"] = "shadow"; return _enqueue(c)
def tiebreak(candidate):
    """A+ / oversized tag. ADVISORY ONLY. Returns immediately. Does not consult synchronously and cannot veto."""
    c = dict(candidate or {}); c["mode"] = "trade"; c["role"] = "tiebreak"; return _enqueue(c)
def review(candidate, wait=False, timeout=None):
    """Trade mode never waits, even if wait=True. wait=True is only for explicit code/PR or postmortem CLI use."""
    c = dict(candidate or {}); c["mode"] = c.get("mode") or "trade"
    if c["mode"] == "trade" or not wait or _pytest_skip():
        c.setdefault("role", "shadow"); return _enqueue(c)
    c = _public(c); c["shadow"] = False; c["wait"] = True; c["timeout_s"] = timeout or float(os.environ.get("TFO_CLAUDE_TIMEOUT", "60"))
    path = "/v1/code" if c["mode"] == "code" else "/v1/postmortem" if c["mode"] == "postmortem" else "/v1/review"
    try:
        resp = _rpc(c, float(os.environ.get("TFO_CLAUDE_CONNECT_S", "0.3")), float(c["timeout_s"]) + 1.0, path)
    except Exception:
        resp = None
    if not isinstance(resp, dict): resp = _local_abstain(c, "bridge_down")
    resp["blocks_arm"] = False; resp["advisory_only"] = True; resp["applied"] = False
    return resp
def observe(candidate, decision=None, tiebreak_ok=False):
    """Trade-path hook. Returns the same decision object. Adds no wait. Never writes the decision."""
    if _pytest_skip(): return decision
    try:
        c = dict(candidate or {})
        c["mode"] = "trade"
        c["role"] = "tiebreak" if tiebreak_ok and wants_tiebreak(c) else "shadow"
        c["advisory_only"] = True
        _spawn(_public(c))
    except Exception:
        pass
    return decision
def record_outcome(trade_id, net_aud=None, win=None, note=None):
    """Later join. Appends an outcome line. Does not touch the book or any open order."""
    if not trade_id: return False
    rec = dict(kind="outcome", utc=now().isoformat(), trade_id=trade_id, net_aud=net_aud, win=win, note=redact(note or "")[:200], paper_only=True, advisory_only=True)
    if _has_secret(rec): rec["note"] = "[redacted]"
    try: Jsonl(calls_path()).write(rec); return True
    except Exception: return False
def parse_model(text):
    """Pull one verdict object out of a Claude reply. None if it is empty or not the schema."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m: return None
    try: j = json.loads(m.group(0))
    except Exception: return None
    if not isinstance(j, dict): return None
    v = str(j.get("verdict") or "").lower().strip()
    if v in ("approve", "yes", "pass", "lgtm", "ship"): v = "agree"
    if v in ("request_changes", "reject", "no", "block", "veto", "fail"): v = "disagree"
    if v not in ("agree", "disagree", "abstain"): return None
    try: conf = max(0.0, min(1.0, float(j.get("confidence"))))
    except (TypeError, ValueError): conf = None
    risks = j.get("risks") if j.get("risks") is not None else j.get("key_risks") or []
    if isinstance(risks, str): risks = [risks]
    if not isinstance(risks, list): risks = []
    risks = [redact(str(x))[:240] for x in risks[:8]]
    reason = redact(str(j.get("reason") or "")).replace("\n", " ")[:240]
    return dict(verdict=v, confidence=conf, risks=risks, reason=reason)
def prompt_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
def main(argv):
    import sys
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: python3 -m veteran_desk.claude_review shadow|tiebreak|review|status JSON"); return 2
    cmd = argv[1]
    if cmd == "status":
        p = os.path.join(bridge_dir(), "status.json")
        print(open(p).read() if os.path.exists(p) else "{}"); return 0
    payload = json.loads(argv[2]) if len(argv) > 2 else {}
    if cmd == "shadow": print(json.dumps(shadow(payload))); return 0
    if cmd == "tiebreak": print(json.dumps(tiebreak(payload))); return 0
    if cmd == "review":
        wait = payload.get("mode") in ("code", "postmortem")
        print(json.dumps(review(payload, wait=wait), default=str)); return 0
    print("unknown", cmd); return 2
if __name__ == "__main__":
    import sys; sys.exit(main(sys.argv) or 0)
