"""gptweb_20261102 ChatGPT bridge (PAPER, advisory). Uses Brett's own chatgpt.com session in tfo-chatgpt-web (page UI only,
no API key, no cookie/token handling). Auto-enabled only when the health probe says the composer is logged in.
CHATGPT_BRIDGE_ENABLED=0 in env forces it off. Rate limit 30/h, 1 at a time (server serialises).
chatimg_20261102: optional local image paths attached via Playwright file input."""
import json, os, time, threading, urllib.request, urllib.error, uuid
from veteran_desk import bridge_maintenance as _BM
_LK = threading.Lock(); _T = []; MAX_PER_HOUR = int(os.environ.get("CHATGPT_MAX_PER_HOUR", "30"))
URL = "http://127.0.0.1:18891"; ST = "/opt/tfo/live/thf/state/thf/chatgpt_status.json"
def status():
    try: return json.load(open(ST))
    except Exception: return {"state": "down"}
def enabled():
    if os.environ.get("CHATGPT_BRIDGE_ENABLED") == "0": return False
    s = status(); return s.get("state") == "logged_in"
def health():
    """Live bridge state incl. queue depth / busy (fablefirst_20261103)."""
    try: return json.loads(urllib.request.urlopen(URL + "/health", timeout=5).read())
    except Exception: return {}
HEALTH_SNAPSHOT=os.path.join(os.path.dirname(ST),"chatgpt_health.json")
_HEALTH_START_LOCK=threading.Lock()
_HEALTH_THREAD=None
def observe_health():
    """Only local bridge liveness; never submit a model prompt or alter account settings."""
    if os.environ.get("PYTEST_CURRENT_TEST"):return None
    import datetime
    observed=health()
    record=dict(observed) if isinstance(observed,dict) and observed.get("state") else {"state":"unavailable","note":"Local bridge health did not answer"}
    record["checked_utc"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    record["observation_source"]="live local bridge health"
    os.makedirs(os.path.dirname(HEALTH_SNAPSHOT),exist_ok=True)
    temporary=HEALTH_SNAPSHOT+".%d.%d.tmp"%(os.getpid(),time.time_ns())
    try:
        with open(temporary,"w") as stream:json.dump(record,stream)
        os.replace(temporary,HEALTH_SNAPSHOT)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass
    return record
def _health_loop(interval_s):
    while True:
        try:observe_health()
        except Exception:pass
        time.sleep(interval_s)
def start_health_loop(interval_s=15):
    if os.environ.get("PYTEST_CURRENT_TEST"):return None
    global _HEALTH_THREAD
    with _HEALTH_START_LOCK:
        if _HEALTH_THREAD is None or not _HEALTH_THREAD.is_alive():
            _HEALTH_THREAD=threading.Thread(target=_health_loop,args=(max(10,int(interval_s)),),daemon=True,name="thf-chatgpt-health")
            _HEALTH_THREAD.start()
        return _HEALTH_THREAD
def busy(limit=None):
    """True when the serial chatgpt.com session already has >= limit jobs waiting: callers skip to the next AI instead of queueing the whole desk."""
    lim = int(limit or os.environ.get("CHATGPT_BUSY_QUEUE", "2")); h = health()
    return bool(h.get("busy") or h.get("inflight")) or int(h.get("queue") or 0) >= lim
class Reply(str):
    def __new__(cls, text, metadata):
        obj = str.__new__(cls, text)
        obj.request_id = metadata.get("request_id")
        obj.accepted_at = metadata.get("accepted_at")
        obj.started_at = metadata.get("started_at")
        obj.finished_at = metadata.get("finished_at")
        return obj

def ask(prompt, timeout=280, images=None, skip_if_busy=True, seat=None, workload="other", deadline=None, request_id=None, wait_for_slot=False):   # maxeffort_20261102: Pro model needs headroom
    _BM.client_guard()
    if not enabled(): raise RuntimeError("chatgpt bridge off (%s)" % status().get("state"))
    if skip_if_busy and busy(): raise RuntimeError("chatgpt busy (queue %s) -> next AI" % health().get("queue"))   # fablefirst_20261103
    deadline = min(float(deadline or (time.time() + timeout + 45)), time.time() + timeout + 45)
    if deadline - time.time() < 65: raise RuntimeError("chatgpt skipped: insufficient deadline budget")
    with _LK:
        now = time.time(); _T[:] = [t for t in _T if now - t < 3600]
        if len(_T) >= MAX_PER_HOUR: raise RuntimeError("chatgpt rate limit")
        _T.append(now)
    IMG_ROOT="/opt/tfo/live/thf/state/thf/chat_imgs"; root=os.path.realpath(IMG_ROOT); imgs=[]
    for pth in (images or [])[:4]:
        if not isinstance(pth, str): continue
        rp=os.path.realpath(pth)
        if (rp==root or rp.startswith(root+os.sep)) and os.path.isfile(rp): imgs.append(rp)
    rid = request_id or uuid.uuid4().hex
    body = dict(prompt=prompt, timeout=timeout, skip_if_busy=bool(skip_if_busy),
                seat=seat, workload=workload, deadline=deadline, request_id=rid,
                wait_for_slot=bool(not skip_if_busy and (workload == "trader" or (workload in ("chat", "learning") and wait_for_slot))))
    if imgs: body["images"] = imgs   # absolute paths under chat_imgs (or probe dir); web worker attaches via file input
    use_ack = False
    def _poll():
        import urllib.parse
        while time.time() < deadline:
            req = urllib.request.Request(URL + "/result?request_id=" + urllib.parse.quote(rid))
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    payload = json.loads(resp.read())
            except urllib.error.HTTPError as error:
                if error.code == 404: return None
                raise
            if payload.get("state") == "done" or payload.get("text"): return payload
            if payload.get("state") != "pending": return payload
            time.sleep(0.05 if os.environ.get("PYTEST_CURRENT_TEST") else 1)
        raise RuntimeError("chatgpt poll deadline; request was not submitted again")
    try:
        _BM.client_guard()  # A hold here is before the delivery probe, so that probe is not opened.
        use_ack = health().get("delivery") == "ack_poll_v1"
        if use_ack: body["ack"] = True
        rq = urllib.request.Request(URL + "/ask", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        _BM.client_guard()  # Recheck immediately before submission, after preparation.
        _BM.mark_submission()
        d = json.loads(urllib.request.urlopen(rq, timeout=20 if use_ack else timeout + 60).read())
        if use_ack and d.get("state") == "pending": d = _poll() or d
    except _BM.MaintenanceHold:
        with _LK:
            try: _T.remove(now)
            except ValueError: pass
        raise
    except urllib.error.HTTPError as error:
        if error.code == 409:
            with _LK:
                try: _T.remove(now)   # Rejected busy requests never spend the local hourly allowance.
                except ValueError: pass
            raise RuntimeError("chatgpt busy -> next AI") from None
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        if not use_ack: raise
        d = _poll()
        if not d: raise RuntimeError("chatgpt ack failed and the request was not stored") from error
    if not d or not d.get("ok"): raise RuntimeError("chatgpt " + str((d or {}).get("error")))
    if time.time() >= deadline: raise RuntimeError("chatgpt late result discarded")
    return Reply(d["text"], d)
