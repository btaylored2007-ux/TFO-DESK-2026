"""fablefirst_20261103: ONE writer for state/thf/seat_heartbeat.json (trader AND admin seats), cross-process file lock.
update(seat, **fields) merges fields under the lock. mark(seat, ok, model, via) records a brain result from any path
(round | chat | review | code_hands) and returns dict(recovered=True) when the seat's previous CHAT reply was a code reply
(the seat must then say 'brain back' in chat, Brett 12:49 WITA 3 Nov). Never raises. PAPER ONLY."""
import datetime as dt, fcntl, json, os, threading
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(BASE, "state", "thf", "seat_heartbeat.json"); LOCKF = F + ".lock"; _TL = threading.Lock()
WITA = dt.timezone(dt.timedelta(hours=8))
def now_wita(): return dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M WITA")
def read():
    try: return json.load(open(F))
    except Exception: return {}
def _locked(fn):
    if os.environ.get("PYTEST_CURRENT_TEST"): return None   # never touch live state from tests
    with _TL:
        os.makedirs(os.path.dirname(F), exist_ok=True)
        with open(LOCKF, "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                H = read(); r = fn(H)
                tmp = F + ".%d.tmp" % os.getpid(); json.dump(H, open(tmp, "w"), indent=1); os.replace(tmp, F); return r
            finally: fcntl.flock(lk, fcntl.LOCK_UN)
def update(seat, **fields):
    def fn(H):
        h = H.get(seat) or {}; h.update(fields)
        if fields.get("mode") in ("brain","brain_down"):
            h["last_result_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
            if fields.get("mode")=="brain_down":h["last_fail_utc"]=h["last_result_utc"]
        if fields.get("via") == "round" and fields.get("mode") == "brain" and fields.get("model"): h["round_model"] = fields["model"]   # gptshare_20261103: desk rounds write here, not via mark()
        H[seat] = h; return h
    try: return _locked(fn)
    except Exception: return None
def mark(seat, ok, model=None, via="chat", why=None):
    """Record a brain result. ok=True -> mode brain + model; ok=False -> brain_down (model None). Chat path tracks chat_mode for 'brain back'."""
    out = dict(recovered=False, prev=None)
    def fn(H):
        h = H.get(seat) or {}; t = now_wita(); prev_chat = h.get("chat_mode")
        h["via"] = via; h["last_seen"] = t
        h["last_result_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
        if not ok:h["last_fail_utc"]=h["last_result_utc"]
        if ok:
            # gptshare_20261103 (Brett 17:35 "use chat gpt as well and not just overload Claude"): chat/review answers may now come from
            # ChatGPT first, so they must not relabel a TRADING seat's brain: 'model' = the last ROUND model when the seat has one;
            # seats without rounds (Marcus, HT, CFO, engineer, challengers) keep using 'model' for their chat brain.
            if via == "round": h.update(mode="brain", model=model, round_model=model, last_round_ok=t, last_brain_ok=t)
            elif h.get("round_model"):
                try:
                    from veteran_desk import seat_registry as SR
                    trading_seat = seat in SR.trade_ids()
                except Exception: trading_seat = True  # preserve identity if roster cannot be read
                h.update(mode="brain", last_brain_ok=t)
                if not trading_seat: h["model"] = model
            else: h.update(mode="brain", model=model, last_brain_ok=t)
        elif via == "round":
            h.update(mode="brain_down", model=None, last_fail=(str(why)[:160] if why else "brain down"))   # a trading-round failure IS brain down
        else:
            h["last_fail"] = (str(why)[:160] if why else "brain down")   # fablefirst_20261103: a transient CHAT/review timeout must NOT clobber the trading-brain status
        if via in ("chat", "review"): h.update(chat_mode="brain" if ok else "code", chat_model=model if ok else None, last_chat=t)
        H[seat] = h; out.update(prev=prev_chat, recovered=bool(ok and via in ("chat", "review") and prev_chat != "brain"))
    try: _locked(fn)
    except Exception: pass
    return out
