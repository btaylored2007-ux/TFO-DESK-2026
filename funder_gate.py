"""2026-10-28 Brett 10:37 WITA: FUNDER APPROVAL GATE for every hunt48 book (US main/GPT/Claude, ASX, JP, HK, SG, KR, TW, LSE)
incl. intraday-rescan arms. PAPER only. NOT applied to OANDA, crypto entry engine, TREND-CORE, ESSENTIALS, Trend18.

A setup that passes every engine gate becomes status 'awaiting_funder' (levels kept) instead of ARMED, and an entry is written to
state/funder_queue.json with an ELI10 description. Nothing fires until Brett ticks it:
    python3 funder_approve.py --approve ID [ID..] | --reject ID [ID..] | --list
The CLI only records the decision in the queue (no ledger race); the book's own engine applies it on its next tick (<= ~2 min):
  approve -> ARMED again only if live price is still BEFORE the trigger and on the right side of the stop and the day-arm is still
             valid; armed_utc = approval time (no earlier bar can fill = no backdated fills). Otherwise -> EXPIRED (never fires).
  reject  -> REJECTED_FUNDER.  Expired / rejected / never-approved -> missed_shadow record in the ledger (measured later).
Open (FILLED) trades are never touched."""
import datetime as dt, json, os, re
BASE = os.path.dirname(os.path.abspath(__file__)); UTC = dt.timezone.utc
QUEUE = os.path.join(BASE, "state", "thf", "funder_queue.json") if os.environ.get("THF_DESK") == "1" else os.path.join(BASE, "state", "funder_queue.json"); CFG = os.path.join(BASE, "state", "funder_gate.json")   # tfo_thffresh_20261030: THF desk has its own queue (CFG restored 19:37 WITA)
WAIT = "awaiting_funder"
BOOKS = {"us_main": "hunt48_short", "us_gpt": "hunt48_short_gpt", "us_claude": "hunt48_short_claude", "asx": "hunt48_asx", "jp": "hunt48_jp",
         "hk": "hunt48_hk", "sg": "hunt48_sg", "kr": "hunt48_kr", "tw": "hunt48_tw", "lse": "hunt48_lse"}
MKT_NAME = {"us_main": "US", "us_gpt": "US", "us_claude": "US", "asx": "Australian", "jp": "Japanese", "hk": "Hong Kong", "sg": "Singapore",
            "kr": "Korean", "tw": "Taiwanese", "lse": "London"}
ACTIVE = lambda: ("ARMED", WAIT)          # statuses that hold a slot / block a re-arm

def enabled(book, base=None):
    """Production desk (BASE): ON unless state/funder_gate.json says enabled:false. Sandbox bases (engine unit tests on a
    temp dir) default OFF unless their own state/funder_gate.json says enabled:true, so legacy engine tests keep testing arming."""
    prod = base is None or os.path.abspath(base) == BASE
    try: c = json.load(open(CFG if prod else os.path.join(base, "state", "funder_gate.json")))
    except Exception: c = {}
    return bool(c.get("enabled", prod)) and book in BOOKS and book not in (c.get("disabled_books") or [])

def auto_approve(book, base=None):
    """Brett 2026-10-28 19:07/19:09 WITA 'all running autonomously, hunt everything': state/funder_gate.json auto_approve:true makes every
    queued setup approved by the desk itself (no funder tick). The engine still re-checks live price vs trigger/stop before ARMED (no chase),
    and stops / sizing / firm rails / heat caps / kill switch are untouched. Sandbox bases default OFF so legacy tests keep testing the tick path."""
    prod = base is None or os.path.abspath(base) == BASE
    try: c = json.load(open(CFG if prod else os.path.join(base, "state", "funder_gate.json")))
    except Exception: c = {}
    return bool(c.get("auto_approve", False)) and book not in (c.get("manual_books") or [])
AUTO_BY = "auto: desk gates passed (Brett 2026-10-28 19:09 WITA, PAPER, no funder tick)"
def _lock():
    import desk_lock; return desk_lock.ledger_lock("funder_queue", timeout=60)
def qpath(base=None): return QUEUE if (base is None or os.path.abspath(base) == BASE) else os.path.join(base, "state", "funder_queue.json")
def load(path=None):
    try: return json.load(open(path or QUEUE))
    except Exception: return dict(paper_only=True, entries={})
def _save(Q, path=None):
    path = path or QUEUE; Q["updated_utc"] = dt.datetime.now(UTC).isoformat(); tmp = path + ".tmp"
    json.dump(Q, open(tmp, "w"), indent=1, default=str); os.replace(tmp, path)
def update(fn, path=None):
    path = path or QUEUE; os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock():
        Q = load(path); Q.setdefault("entries", {}); r = fn(Q); _save(Q, path); return r

def sym(t): return t.get("sym") or t.get("ticker")
def qid(book, t): return f"{book.upper().replace('US_', 'US-')}-{re.sub(r'[^A-Z0-9]', '', sym(t).upper())}-{t.get('id')}"
def fx_to_aud(book, t, audusd=None):
    if book == "asx": return 1.0
    if book.startswith("us"):
        a = audusd or t.get("audusd") or 0.70; return 1.0 / a
    return t.get("fx_aud")
def entry_px(t): return t.get("max_fill") if t.get("max_fill") is not None else t.get("trigger")
def target(t): return t.get("t2") if t.get("t2") is not None else t.get("t1")
def money(book, t, audusd=None):
    fx = fx_to_aud(book, t, audusd); e, st, tg, n = entry_px(t), t.get("stop"), target(t), t.get("shares") or 0
    if None in (fx, e, st, tg): return None, None
    return round(abs(tg - e) * n * fx), round(abs(e - st) * n * fx)

def eli10(book, t, audusd=None, industry=None):
    name = (t.get("name") or sym(t)).strip(); what = industry or t.get("sector") or "business"
    lg = (t.get("direction") or "long") == "long"; win, lose = money(book, t, audusd)
    s1 = f"{name} ({sym(t)}) is {('an' if MKT_NAME.get(book, 'x')[:1] in 'AEIOU' else 'a')} {MKT_NAME.get(book, '')} company that works in {what}."
    if t.get("strategy") == "rs_long_in_short_regime":
        s2 = "Most shares in its market are going down, but this one has been going up faster than the rest, so we bet it keeps climbing a little."
    elif t.get("catalyst") and (t["catalyst"].get("bonus") or 0) > 0:
        s2 = f"There was fresh {'good' if lg else 'bad'} news about it, and its price pattern says it may keep going {'up' if lg else 'down'} for a few days."
    else:
        s2 = f"Its price pattern and our scoring say it is more likely to go {'up' if lg else 'down'} a little over the next few days."
    s3 = (f"If it {'rises' if lg else 'falls'} to our target we win about A${win:,}; if it {'falls' if lg else 'rises'} to our stop we lose about A${lose:,}."
          if win is not None else "We win if it moves our way to the target and lose a set amount if it hits the stop.")
    return " ".join((s1, s2, s3))

def _tags(book, t):
    try: r = (json.load(open(os.path.join(BASE, "state", "trade_tags", "tags.json"))) or {}).get(f"{book}:{sym(t)}") or {}
    except Exception: r = {}
    return dict(news_risk=r.get("news_risk") or [], quality=(r.get("quality") or {}).get("verdict", "n/a"), catalyst=t.get("catalyst"),
                strategy=t.get("strategy"))

def entry_for(book, t, now, audusd=None):
    win, lose = money(book, t, audusd)
    return dict(id=qid(book, t), book=book, ledger=BOOKS[book], trade_id=t.get("id"), market=book.split("_")[0].upper(), ticker=sym(t),
                name=t.get("name"), side=(t.get("direction") or "long").upper(), entry=entry_px(t), trigger=t.get("trigger"), max_fill=t.get("max_fill"),
                stop=t.get("stop"), target=target(t), t1=t.get("t1"), shares=t.get("shares"), risk_aud=lose, win_aud=win,
                score=t.get("score"), tags=_tags(book, t), eli10=eli10(book, t, audusd), state=WAIT, decision=None,
                queued_utc=now.isoformat(), originally_armed_utc=t.get("armed_utc"), paper_only=True)

def missed(L, t, book, now, why):
    L.setdefault("missed_shadow", []).append(dict(kind="funder_gate", reason=why, book=book, trade_id=t.get("id"), ticker=sym(t), direction=t.get("direction"),
        trigger=t.get("trigger"), max_fill=t.get("max_fill"), stop=t.get("stop"), t1=t.get("t1"), t2=t.get("t2"), shares=t.get("shares"),
        score=t.get("score"), strategy=t.get("strategy"), catalyst=t.get("catalyst"), queued_utc=t.get("funder_queued_utc"), recorded_utc=now.isoformat(),
        note="setup passed all gates but was not traded (no funder approval) - shadow only, never a fill"))

def still_valid(t, lp):
    if lp is None: return None
    lg = (t.get("direction") or "long") == "long"
    return (t["stop"] < lp < t["trigger"]) if lg else (t["trigger"] < lp < t["stop"])

def gate_tick(book, L, now, live_fn, expired_fn, audusd=None, base=None):
    """Called by the engine every tick BEFORE fills. Returns list of (ticker, action)."""
    if not enabled(book, base): return []
    qp = qpath(base); T = L["trades"]; acts = []; Q = load(qp).get("entries", {}); new = []; upd = {}; autoq = {}; auto = auto_approve(book, base)
    for t in T:
        if t.get("status") == "ARMED" and not t.get("funder_approved"):      # any arm path (incl. intraday rescan / manual) -> held for the funder
            t.update(status=WAIT, funder_queued_utc=now.isoformat(), funder_id=qid(book, t)); new.append(entry_for(book, t, now, audusd)); acts.append((sym(t), "queued"))
    for t in T:
        if t.get("status") != WAIT: continue
        e = Q.get(t.get("funder_id") or qid(book, t)) or {}
        # tfo_qid_restage_20261030: a decision recorded BEFORE this row was (re-)queued, or on a terminal entry, never fires on a re-staged qid
        if e and (e.get("state") not in (None, WAIT) or (e.get("decided_utc") and t.get("funder_queued_utc") and str(e["decided_utc"]) < str(t["funder_queued_utc"]))):
            e = {}
        dec = e.get("decision")
        if dec is None and auto:
            dec = "approve"; e = dict(e, id=e.get("id") or t.get("funder_id") or qid(book, t)); autoq[e["id"]] = dict(decision="approve", decided_by=AUTO_BY, decided_utc=now.isoformat()); t["funder_decided_by"] = AUTO_BY
        if dec == "reject":
            t.update(status="REJECTED_FUNDER", funder_decision_utc=now.isoformat()); missed(L, t, book, now, "rejected by funder")
            upd[e["id"]] = dict(state="rejected"); acts.append((sym(t), "rejected")); continue
        if expired_fn(t, now):
            t.update(status="EXPIRED", note="awaiting funder: day-arm window ended before approval/arm"); missed(L, t, book, now, "funder approval not given in time" if dec != "approve" else "approved too late")
            upd[t.get("funder_id") or qid(book, t)] = dict(state="expired", why="day-arm window ended"); acts.append((sym(t), "expired")); continue
        if dec == "approve":
            try: lp = live_fn(t)
            except Exception: lp = None
            ok = still_valid(t, lp)
            if ok is None: upd[e["id"]] = dict(note=f"approved; no live price at {now.isoformat()[:16]}Z - retrying next tick (not fired)"); continue
            if not ok:
                t.update(status="EXPIRED", note=f"funder approved but price {lp:g} no longer before trigger/inside stop -> not fired"); missed(L, t, book, now, f"approved but entry no longer valid (px {lp:g})")
                upd[e["id"]] = dict(state="expired", why=f"price {lp:g} past trigger or stop at approval"); acts.append((sym(t), "expired")); continue
            arm = max(dt.datetime.fromisoformat(t["armed_utc"]), now) if t.get("armed_utc") else now
            t.update(status="ARMED", funder_approved=True, funder_approved_utc=now.isoformat(), armed_utc=arm.isoformat(), live_px_at_approval=lp)
            upd[e["id"]] = dict(state="armed", armed_utc=arm.isoformat(), px_at_approval=lp); acts.append((sym(t), "armed"))
    if new or upd or autoq:
        def f(Q):
            for n in new:
                o = Q["entries"].get(n["id"])
                if o is None: Q["entries"][n["id"]] = n
                elif o.get("state") != WAIT: Q["entries"][n["id"]] = dict(n, prior=dict(state=o.get("state"), decision=o.get("decision"), decided_utc=o.get("decided_utc")))   # tfo_qid_restage_20261030: fresh entry, old approve cleared
            for k, v in autoq.items():
                if k in Q["entries"]: Q["entries"][k].update(v)
            for k, v in upd.items():
                if k in Q["entries"]: Q["entries"][k].update(v, applied_utc=now.isoformat())
        update(f, qp)
    return acts
