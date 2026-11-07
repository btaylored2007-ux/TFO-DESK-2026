"""armfix_20261103 (P0 ch-1793714505-3f49, Marcus fix list for Brett 3 Nov 2026 21:54 WITA, PAPER, read-mostly): LAST ARM RESULT per seat.
Roll-call 3 Nov: nobody could see what the book did with an arm. SHELL.AS (energy) was withdrawn by energy's own chat action at
21:36:56 WITA and Marcus asked at 21:54 where it went; PFE/EURCAD/GC=F/NZDCAD expired "propose not acked within 10 min" (no
"confirm":true), BUD "insufficient_cash", XOM "firm_one_trade_per_theme_oil" - none of it reached the seat's next prompt.
Sources, newest first: seat_arm_last.json (every seat_cli arm / head arm attempt: the VET id read back from the book, or the plain
error), the THF funder queue (round proposals: ARMED id / FILLED / NOT ARMED + the book tick's rail reason / WITHDRAWN + why /
QUEUED) and the newest round run logs (gate_v2 refusals never reach the queue). record() is the only writer (seat_cli).
line()/desk_line() feed MY POSITIONS / DESK POSITIONS (my_positions.block) in round and chat prompts. Never raises."""
import datetime as dt, glob, json, os, re
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(BASE, "state", "thf")
LAST = os.path.join(S, "seat_arm_last.json"); QUEUE = os.path.join(S, "funder_queue.json"); RUNS = os.path.join(BASE, "state", "vd_engine", "runs")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
KEEP = 8; MAX_AGE_H = 24; RUN_FILES = 4; WAIT = "awaiting_funder"


def _ts(u):
    try:
        t = dt.datetime.fromisoformat(str(u).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception: return None


def _rj(p, d):
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return d


def _clip(x, n=170):
    s = " ".join(str(x or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def record(seat, res, path=None, now=None):
    """Store one arm attempt for SEAT (newest first, KEEP per seat). res: ticker, side, grade, ok, id, status, error, via, actor."""
    path = path or LAST
    try:
        import fcntl
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        row = dict(utc=(now or dt.datetime.now(UTC)).isoformat(), seat=seat, **{k: res.get(k) for k in ("ticker", "side", "grade", "ok", "id", "status", "error", "via", "actor", "entry", "stop", "target") if res.get(k) is not None})
        with open(path + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            D = _rj(path, {})
            D = D if isinstance(D, dict) else {}
            D[seat] = ([row] + [x for x in (D.get(seat) or []) if isinstance(x, dict)])[:KEEP]
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w") as fh: json.dump(D, fh, indent=1, default=str)
            os.replace(tmp, path)
        return row
    except Exception: return None


def _q_event(e):
    st = str(e.get("state") or "?"); why = e.get("why") or e.get("note") or ""
    if st == "armed": out, t = "ARMED %s" % e.get("id"), e.get("armed_utc") or e.get("applied_utc")
    elif st == "filled": out, t = "FILLED %s" % e.get("id"), e.get("filled_utc") or e.get("armed_utc")
    elif st == WAIT:
        if e.get("ack_required") and not e.get("acked"): out = "QUEUED, NOT ARMED YET: waiting for a confirm (no \"confirm\":true) - expires 10 min after it was queued"
        else: out = "QUEUED %s: the book tick arms or refuses it within ~2 min%s" % (e.get("id"), (" (" + _clip(e.get("note"), 90) + ")") if e.get("note") and "auto-arm" not in str(e.get("note")) else "")
        t = e.get("queued_utc")
    elif st == "withdrawn":
        w = re.sub(r"^seat_cli withdraw by [\w-]+: ", "", str(why)); w = re.sub(r"\s*\(msg [\w-]+\)", "", w)
        out, t = "WITHDRAWN %s: %s" % (e.get("id"), _clip(w, 130)), e.get("withdrawn_utc") or e.get("queued_utc")
    elif "not acked" in str(why): out, t = "NOT ARMED: no \"confirm\":true in the reply that proposed it - expired unarmed after 10 min", e.get("queued_utc")
    else: out, t = "NOT ARMED (%s): %s" % (st, _clip(why or "no reason on the queue entry")), e.get("queued_utc")
    return dict(utc=t or e.get("queued_utc"), seat=e.get("seat") or e.get("owner_seat"), ticker=e.get("ticker"), side=e.get("side"), grade=e.get("grade"), id=e.get("id"), out=out, src="queue")


def events(seat=None, now=None, max_age_h=MAX_AGE_H, last_path=None, queue_path=None, runs_dir=None):
    """Arm outcomes (newest first) for SEAT (None = every seat) within max_age_h. One row per VET id (newest source wins)."""
    now = now or dt.datetime.now(UTC); lo = now - dt.timedelta(hours=max_age_h); E = []
    try:
        for s, rows in (_rj(last_path or LAST, {}) or {}).items():
            if seat and s != seat: continue
            for r in rows if isinstance(rows, list) else []:
                if not isinstance(r, dict): continue
                ok = bool(r.get("ok"))
                out = ("ARMED %s (book read-back%s)" % (r.get("id"), ", " + str(r.get("status")) if r.get("status") not in (None, "armed") else "")) if ok else "NOT ARMED: %s" % _clip(r.get("error") or "no reason")
                E.append(dict(utc=r.get("utc"), seat=s, ticker=r.get("ticker"), side=r.get("side"), grade=r.get("grade"), id=r.get("id"), out=out, src="seat_cli " + str(r.get("via") or "arm")))
    except Exception: pass
    try:
        for e in ((_rj(queue_path or QUEUE, {}) or {}).get("entries") or {}).values():
            if not isinstance(e, dict) or e.get("book") != "veteran": continue
            if seat and (e.get("seat") or e.get("owner_seat")) != seat: continue
            E.append(_q_event(e))
    except Exception: pass
    try:
        fs = sorted(glob.glob(os.path.join(runs_dir or RUNS, "THF-*.json")), key=lambda p: os.path.getmtime(p), reverse=True)[:RUN_FILES]
        for f in fs:
            L = _rj(f, {}) or {}
            for r in L.get("proposals") or []:
                if not isinstance(r, dict) or (seat and r.get("seat") != seat): continue
                g = r.get("gate") or {}; p = r.get("proposal") or {}
                if g.get("passed") and not r.get("live_reject"): continue   # passed -> its queue entry tells the story
                why = r.get("live_reject") or "; ".join(str(x) for x in (g.get("reasons") or [])[:2]) or "gate refused"
                E.append(dict(utc=r.get("created_utc"), seat=r.get("seat"), ticker=p.get("ticker"), side=p.get("side"), grade=p.get("grade"), id=r.get("id"),
                              out="NOT ARMED - %s refused it: %s" % ("live check" if r.get("live_reject") else "gate_v2", _clip(why)), src="round"))
    except Exception: pass
    seen = {}; out = []
    for e in sorted(E, key=lambda x: str(x.get("utc") or ""), reverse=True):
        t = _ts(e.get("utc"))
        if t is None or t < lo: continue
        k = e.get("id") or (e.get("seat"), e.get("ticker"), e.get("utc"))
        if k in seen: continue
        seen[k] = 1; out.append(dict(e, t=t))
    return out


def _fmt(e, show_seat=False, now=None):
    side = str(e.get("side") or "?").upper()[:1]; w = e["t"].astimezone(WITA); today = (now or dt.datetime.now(UTC)).astimezone(WITA).date()
    return "%s%s %s %s%s %s" % ((str(e.get("seat")) + " ") if show_seat else "", w.strftime("%H:%M") if w.date() == today else w.strftime("%m-%d %H:%M"), e.get("ticker") or "?", side,
                                (" " + str(e.get("grade"))) if e.get("grade") else "", e.get("out"))


def line(seat, now=None, n=3, **kw):
    """Trader seat: its last n arm outcomes in the last 24h (newest first), or '' when none."""
    try:
        E = events(seat, now=now, **kw)[:n]
        if not E: return ""
        return "LAST ARM RESULTS (newest first, WITA; ARMED = the VET id is in the book, else the reason): " + " | ".join(_fmt(e, now=now) for e in E)
    except Exception: return ""


def desk_line(now=None, max_age_h=6, n=6, **kw):
    """Marcus/HT/admins: the newest arm outcome per seat in the last max_age_h hours (max n seats), or ''."""
    try:
        per = {}
        for e in events(None, now=now, max_age_h=max_age_h, **kw):
            if e.get("seat") not in per: per[e.get("seat")] = e
        if not per: return ""
        return "RECENT ARM RESULTS (newest per seat, %dh, WITA): " % max_age_h + " | ".join(_fmt(e, show_seat=True, now=now) for e in list(per.values())[:n])
    except Exception: return ""


if __name__ == "__main__":   # python -m veteran_desk.arm_last [SEAT]  - read-only check
    import sys
    a = sys.argv[1:]
    print(line(a[0]) if a else desk_line())
