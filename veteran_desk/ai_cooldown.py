"""maxeffort_20261102 (Brett 20:05 WITA): shared AI cooldown. Any AI that is out of tokens / usage-limited / not logged in /
429 / timing out is marked cooling-down so every caller (seat replies, seat-to-seat, advisors, code_hands planning) skips
straight to the next AI. State: state/thf/ai_cooldown.json (no secrets). Never raises."""
import json, os, re, time, threading, fcntl
F = "/opt/tfo/live/thf/state/thf/ai_cooldown.json"
_LIVE_F=F
class _CooldownLock:
    def __init__(self):self.thread=threading.Lock();self.file=None
    def __enter__(self):
        self.thread.acquire()
        try:
            if not (os.environ.get("PYTEST_CURRENT_TEST") and F==_LIVE_F):
                os.makedirs(os.path.dirname(F),exist_ok=True)
                self.file=open(F+".lock","a+")
                fcntl.flock(self.file,fcntl.LOCK_EX)
            return self
        except BaseException:
            self.thread.release();raise
    def __exit__(self,*exc):
        try:
            if self.file is not None:
                fcntl.flock(self.file,fcntl.LOCK_UN);self.file.close();self.file=None
        finally:self.thread.release()
_LK=_CooldownLock()
# brainsafe_20261103: word-bounded (a PID with "401", a seat saying "bank-credit" or "insufficient volume" must never set a desk-wide cooldown)
QUOTA = re.compile('(?:\\bquota\\b|rate.?limit|\\b429\\b|usage.?limit|out of (tokens|credits)|no credits|insufficient (credit|quota|balance|funds)|limit reached|exhausted|too many requests|hit your .{0,16}limit|weekly usage|monthly .{0,24}limit|ActionRequiredError)|out of tokens|no tokens remaining|tokens? (?:exhausted|depleted)|token quota|message limit reached', re.I)
OVER = re.compile(r"overloaded|\b529\b", re.I)   # brainsafe_20261103: an overload is a 5-min pause, not a 30-min quota cooldown
AUTH = re.compile(r"not logged in|\blogin\b|\b401\b|unauthori[sz]ed|invalid api key|(token|session|credential\w*|cookie\w*|oauth\w*) (has |is )?expired|captcha", re.I)
TMO = re.compile(r"timeout|timed out|hard timeout", re.I)
KILLED = re.compile(r"\brc=(-9|137|-15|143)\b|\bKilled\b|SIGKILL|\bTerminated\b", re.I)   # brainsafe_20261103: an external kill (OOM/pkill) is not an AI outage
RESET = re.compile(r"(?:resets?|try again)\s*(?:at|in)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|(?:in|after)\s+(\d+)\s*(min|minute|hour|hr|day)", re.I)   # fablefirst_20261103: + days
NO_TIMEOUT_CD = {"claude", "chatgpt"}   # gptshare_20261103: ChatGPT now carries seat chatter first; a bridge 504/timeout under load is not an outage (its busy() queue check already sheds load)   # brainsafe_20261103: never a desk-wide cooldown for a Claude timeout/overload (quota/auth still cool down until the stated reset)
def _load():
    try: return json.load(open(F))
    except Exception: return {}
def _save(d):
    try:
        d={k:v for k,v in d.items() if k!="supergrok"}
        t=F+".%d.%d.tmp"%(os.getpid(),time.time_ns())
        with open(t,"w") as stream:json.dump(d,stream,indent=1)
        os.replace(t,F)
    except Exception: pass
def cooling(name):
    """True if name is cooling down now."""
    return float(_load().get(name, {}).get("until", 0)) > time.time()

def dated_reset_secs(error):
    import datetime as dt
    from zoneinfo import ZoneInfo
    match=re.search(r"(?:resets?|try again)\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:,\s*(\d{4}))?,?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?",str(error or ""),re.I)
    if not match:return None
    months={name:i for i,name in enumerate(["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"],1)}
    try:
        zone=ZoneInfo(match.group(7) or "Asia/Makassar");now=dt.datetime.fromtimestamp(time.time(),zone)
        month=months[match.group(1)[:3].lower()];year=int(match.group(3) or now.year)
        if not match.group(3) and now.month==12 and month==1:year+=1
        hour=int(match.group(4));minute=int(match.group(5) or 0);ampm=(match.group(6) or "").lower()
        if ampm=="pm" and hour<12:hour+=12
        if ampm=="am" and hour==12:hour=0
        target=dt.datetime(year,month,int(match.group(2)),hour,minute,tzinfo=zone)
        seconds=target.timestamp()-time.time()
        return int(seconds)+60 if seconds>0 else None
    except (ValueError,KeyError):return None

def _reset_secs(e):
    dated=dated_reset_secs(e)
    if dated:return dated
    m = RESET.search(e or "")
    if not m: return None
    if m.group(4): return int(m.group(4)) * (86400 if m.group(5).lower().startswith("d") else 3600 if m.group(5).lower().startswith("h") else 60)
    h, mi = int(m.group(1)), int(m.group(2) or 0); ap = (m.group(3) or "").lower()
    if ap == "pm" and h < 12: h += 12
    if ap == "am" and h == 12: h = 0
    lt = time.localtime(); tgt = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
    if tgt <= time.time(): tgt += 86400
    return int(tgt - time.time()) + 60
def mark_fail(name, err, secs=None, kind=None):
    """Classify err; set cooldown. quota/usage -> stated reset time else 30 min (clamped 15-360 min); auth -> 30 min; overload/timeout -> 5 min.
    secs overrides the length (fablefirst_20261103: the Grok relay flaps on Cursor credits, so callers pass a short 5-min cooldown).
    kind (brainsafe_20261103) lets a caller that already classified the error (claude_brain) force the kind; a killed process never cools down."""
    if name=="supergrok":return None
    e = str(err or "")
    if not kind:
        if KILLED.search(e): return None
        if QUOTA.search(e): kind = "quota"
        elif AUTH.search(e): kind = "auth"
        elif OVER.search(e): kind = "overload"
        elif TMO.search(e): kind = "timeout"
        else: return None
    if name in NO_TIMEOUT_CD and kind in ("timeout", "overload"): return None   # brainsafe_20261103: Claude is the primary brain with a per-call budget; one slow call under load is not an outage (3 Nov 14:35: one 449s chat timeout cooled Claude for the whole desk and the 14:37 round skipped it)
    if kind == "quota":
        stated=_reset_secs(e);s0=max(60,min(stated,31*86400)) if stated else 1800
    elif kind == "auth": s0 = 1800
    else: s0 = 300
    secs = int(secs) if secs else s0
    with _LK:
        d = _load()
        previous=d.get(name) or {}
        if kind=="quota" and previous.get("kind")=="quota" and float(previous.get("until") or 0)>time.time()+secs:return kind
        d[name] = dict(until=time.time() + secs, kind=kind, why=e[:240], since=time.strftime("%Y-%m-%d %H:%M:%S WITA"),
                                    until_wita=time.strftime("%Y-%m-%d %H:%M WITA", time.localtime(time.time() + secs))); _save(d)
    return kind
def mark_down(name, why, secs, kind="down"):
    """brainsafe_20261103: explicit cooldown (an AI switched off on purpose, e.g. a relay whose account needs Brett): every caller skips it for secs."""
    if name=="supergrok":return None
    with _LK:
        d = _load(); d[name] = dict(until=time.time() + int(secs), kind=kind, why=str(why)[:200], since=time.strftime("%Y-%m-%d %H:%M:%S WITA"),
                                    until_wita=time.strftime("%Y-%m-%d %H:%M WITA", time.localtime(time.time() + int(secs)))); _save(d)
    return kind
def mark_ok(name):
    with _LK:
        d = _load()
        if name in d: d.pop(name); _save(d)
