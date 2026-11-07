"""dashctx_20261103 (master fixer; Brett 2 Nov 20:46 WITA A21/B12): every AI (seat brains, Claude, ChatGPT, Grok, Super Power, Strategy Lab,
code_hands) can READ THE WHOLE DASHBOARD. Read-only: builds a compact snapshot of every dash card from /api/state (dash on 127.0.0.1:28870),
the paper book, seat heartbeat, AI cooldowns, scan summary, news stream and the chat thread.
  refresh()            -> writes state/thf/dash_context.json (full detail, sections) + state/thf/dash_context.md (summary) ; timer every 2 min
  block(seat=None, n)  -> the summary text (<= n chars, default 2500) to inject into a prompt; seat=... adds that seat's own lines first
  section(name)        -> one section of the full detail on demand (for AIs with tools / code_hands)
  shot()               -> headless screenshot of the dash into state/thf/chat_imgs/dash_shot.png (vision AIs); timer every 5 min
Never writes anything else; never raises (returns "" / {} on any error). PAPER ONLY."""
import datetime as dt, json, os, time, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); S = os.path.join(BASE, "state", "thf")
OUT_JSON = os.path.join(S, "dash_context.json"); OUT_MD = os.path.join(S, "dash_context.md"); SHOT = os.path.join(S, "chat_imgs", "dash_shot.png")
API = os.environ.get("THF_DASH_API", "http://127.0.0.1:28870/api/state"); DASH_URL = os.environ.get("THF_DASH_URL", "http://127.0.0.1:28870/")
WITA = dt.timezone(dt.timedelta(hours=8))
SEED = 500000.0
def _j(p, fb=None):
    try: return json.load(open(p))
    except Exception: return fb
def _age_min(u):
    try: return round((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(str(u).replace("Z", "+00:00"))).total_seconds() / 60)
    except Exception: return None
def _a(x):
    try: return "%+.0f" % float(x)
    except Exception: return "?"
def _api():
    try: return json.loads(urllib.request.urlopen(API, timeout=25).read())
    except Exception as e: return {"_err": "dash api unreachable: %s" % type(e).__name__}
def _positions(book):
    out = []
    for seat, B in sorted((book.get("seats") or {}).items()):
        P = B.get("positions") or {}
        for p in (P.values() if isinstance(P, dict) else P):
            if not isinstance(p, dict) or p.get("status") not in ("open", "armed", "queued"): continue
            out.append(dict(seat=seat, status=p.get("status"), ticker=p.get("ticker"), side=p.get("side"), entry=p.get("entry"), fill=p.get("fill"), stop=p.get("stop"),
                            target=p.get("target"), grade=p.get("grade"), risk_aud=p.get("risk_aud"), mark=p.get("mark"), unreal_aud=p.get("unreal_aud") or p.get("upnl_aud"),
                            armed=str(p.get("armed_utc") or "")[:16], id=p.get("id")))
    return out
def _seat_money(book):
    rows = []
    try:   # dayclock_20261103 (Brett 16:01 "Figures are not right"): 'today' = the dashboard's 06:00 WITA clock; book day (00:00 WITA) kept, labelled
        from veteran_desk import day_clock as DC; _DAYS = DC.seat_days(book)
    except Exception: _DAYS = {}
    for seat, B in sorted((book.get("seats") or {}).items()):
        eq = B.get("equity_aud"); seed = B.get("seed_aud") or SEED
        if eq is None: continue
        _dd = (_DAYS.get("seats") or {}).get(seat) or {}
        rows.append(dict(seat=seat, equity_aud=round(float(eq), 2), pnl_since_seed_aud=round(float(eq) - float(seed), 2), today_pnl_aud=(_dd.get("day_aud") if _dd.get("day_aud") is not None else B.get("today_pnl_aud")), book_day_aud=B.get("today_pnl_aud"),
                         n_open=sum(1 for p in (B.get("positions") or {}).values() if isinstance(p, dict) and p.get("status") == "open"),
                         n_armed=sum(1 for p in (B.get("positions") or {}).values() if isinstance(p, dict) and p.get("status") == "armed")))
    return rows
def _ai_status():
    cd = _j(os.path.join(S, "ai_cooldown.json"), {}) or {}; cg = _j(os.path.join(S, "chatgpt_status.json"), {}) or {}; now = time.time(); out = {}
    for k in ("claude", "chatgpt", "grok"):
        c = cd.get(k) or {}
        if float(c.get("until", 0) or 0) > now: out[k] = dict(state="cooling" if c.get("kind") in ("timeout", "overload") else "down", until=c.get("until_wita"), why=str(c.get("why") or "")[:90])
        else: out[k] = dict(state="up")
    if cg: out["chatgpt"].update(web=cg.get("state"), model=cg.get("model_pick"))
    return out
def _brains():
    hb = _j(os.path.join(S, "seat_heartbeat.json"), {}) or {}; rows = {}
    for s, h in sorted(hb.items()):
        if isinstance(h, dict): rows[s] = dict(mode=h.get("mode") or h.get("chat_mode"), model=h.get("model") or h.get("chat_model"), last=h.get("last_round") or h.get("last_chat"))
    return rows
def _news():
    hb = _j(os.path.join(BASE, "state", "catalyst_stream", "heartbeat.json"), {}) or {}; nj = _j(os.path.join(BASE, "state", "veteran_desk", "news.json"), {}) or {}
    out = dict(stream_age_min=_age_min(hb.get("utc") or hb.get("ts") or hb.get("updated")), regime=nj.get("regime") or nj.get("news_regime"), news_age_min=_age_min(nj.get("utc") or nj.get("updated")))
    heads = []
    try:
        for f in sorted(os.listdir(os.path.join(BASE, "state", "catalyst_stream", "queues")))[:14]:
            p = os.path.join(BASE, "state", "catalyst_stream", "queues", f)
            for l in open(p).readlines()[-3:]:
                try: d = json.loads(l); heads.append(dict(seat=f.rsplit(".", 1)[0], t=str(d.get("ts") or d.get("utc") or "")[:16], h=str(d.get("headline") or d.get("title") or d.get("text") or "")[:110]))
                except Exception: pass
    except Exception: pass
    out["recent"] = heads[-12:]; return out
def _feeds_extra():
    """oanduw_20261103 (fixer; Brett 20:10 WITA 'Oanda and Unusual Whales is up the health feed needs to be updated'): HT/Marcus read
    only this HEALTH line, which never named either feed. OANDA = feed keeper row oanda_practice:oanda (state/thf/feed_status.json);
    Unusual Whales = catalyst_stream vendors uw_news + uw_flow (state/catalyst_stream/heartbeat.json). Missing file -> 'no status'."""
    try: return _feeds_extra0()
    except Exception: return {}
def _feeds_extra0():
    out = {}
    fs = ((_j(os.path.join(S, "feed_status.json"), {}) or {}).get("feeds") or {})
    o = fs.get("oanda_practice:oanda") if isinstance(fs, dict) else None
    out["oanda"] = dict(status=o.get("status"), age_s=o.get("age_s"), detail=str(o.get("detail") or "")[:160]) if isinstance(o, dict) else None
    hb = _j(os.path.join(BASE, "state", "catalyst_stream", "heartbeat.json"), {}) or {}; v = hb.get("vendors") or {}
    u = {k: v[k] for k in ("uw_news", "uw_flow") if isinstance(v.get(k), dict)}
    out["unusual_whales"] = dict(status="ok" if all(x.get("status") == "ok" for x in u.values()) else "DOWN", age_min=_age_min(hb.get("utc")),
                                 vendors={k: dict(status=x.get("status"), n=x.get("n"), err=x.get("err")) for k, x in u.items()}) if u else None
    # healthdesk_20261103 (builds on oanduw_20261103; HT P0 ch-1793706250-713e status rules, Marcus 20:54 + Massive/EODHD): the feed keeper's
    # paid data VENDOR rows (feeds['vendor:*'], veteran_desk/vendor_health.py) - status ok|stale|down|closed|not_configured, last good WITA, age, last error
    vr = {k.split(":", 1)[1]: v for k, v in fs.items() if isinstance(v, dict) and v.get("kind") == "vendor"} if isinstance(fs, dict) else {}
    if vr: out["vendors"] = {k: dict(name=v.get("vendor"), status=v.get("status"), last_good=v.get("last_good_wita"), age_min=v.get("age_min"), err=str(v.get("last_error") or "")[:200] or None,
                                     needs_brett=bool(v.get("needs_brett")), resets=(v.get("quota") or {}).get("resets_wita") if (v.get("quota") or {}).get("exhausted") else None) for k, v in vr.items()}
    return out
VORDER = ("oanda", "unusual_whales", "massive", "eodhd")
VSHORT = dict(oanda="OANDA", unusual_whales="Unusual Whales", massive="Massive", eodhd="EODHD")
def _vendor_bits(V):
    """healthdesk_20261103: 'OANDA ok (last good 22:14 WITA, 13 min)' per vendor; down/stale say why; one entry per vendor."""
    out = []
    for k in sorted(V, key=lambda k: VORDER.index(k) if k in VORDER else 9):
        v = V[k] if isinstance(V[k], dict) else {}; st = str(v.get("status") or "?"); nm = VSHORT.get(k, str(v.get("name") or k))
        if st in ("ok", "closed"): out.append("%s %s (last good %s, %s min)" % (nm, st if st == "ok" else "closed (market shut)", v.get("last_good") or "?", v.get("age_min") if v.get("age_min") is not None else "?"))
        elif st == "down" and v.get("resets"): out.append("%s DOWN (daily call limit used up, HTTP 402; back %s or when Brett buys calls; book marks + EU live prices unaffected, ASX/Asia paid quotes fall back to Yahoo)" % (nm, v.get("resets")))
        else: out.append("%s %s (last good %s%s%s)" % (nm, st.upper(), v.get("last_good") or "?", ("; " + str(v.get("err"))[:120]) if v.get("err") else "", "; needs Brett" if v.get("needs_brett") else ""))
    return out
def _vendor_broken(X):
    """healthdesk_20261103 (713e rule): down / stale / not_configured paid data vendors go into 'broken:'."""
    V = (X or {}).get("vendors") if isinstance(X, dict) else None
    if not isinstance(V, dict): return []
    return ["%s %s%s" % (VSHORT.get(k, k), str(v.get("status")).upper().replace("NOT_CONFIGURED", "has no key"), (" - " + ("daily call limit used up (needs Brett)" if v.get("resets") else str(v.get("err") or "")[:60])) if v.get("status") == "down" else "")
            for k, v in sorted(V.items(), key=lambda kv: VORDER.index(kv[0]) if kv[0] in VORDER else 9) if isinstance(v, dict) and v.get("status") in ("down", "stale", "not_configured")]
def _feeds_line(X):
    X = X if isinstance(X, dict) else {}
    if isinstance(X.get("vendors"), dict) and X["vendors"]: return "paid data " + "; ".join(_vendor_bits(X["vendors"]))   # healthdesk_20261103
    o = X.get("oanda"); u = X.get("unusual_whales")
    o = o if isinstance(o, dict) else None; u = u if isinstance(u, dict) else None
    a = ("OANDA practice %s (%ss old)" % ("ok" if o.get("status") == "ok" else "DOWN (%s)" % o.get("status"), o.get("age_s"))) if o else "OANDA practice: no status"
    b = ("Unusual Whales %s (%s; stream %s min old)" % (u["status"], ", ".join("%s %s%s" % (k.replace("uw_", ""), x.get("status"), (" " + str(x.get("err"))[:30]) if x.get("err") else "") for k, x in (u.get("vendors") or {}).items()), u.get("age_min"))) if u else "Unusual Whales: no status"
    return a + "; " + b
def _chat(n=8):
    rows = []
    try:
        for l in open(os.path.join(S, "funder_thread.jsonl")).readlines()[-60:]:
            try: r = json.loads(l)
            except Exception: continue
            if str(r.get("text") or "").startswith("FROM SEAT") or r.get("mode") == "code": continue
            rows.append(dict(t=str(r.get("ts_wita") or "")[11:16], frm=r.get("from"), to=r.get("to"), text=str(r.get("text") or "")[:140].replace("\n", " ")))
    except Exception: pass
    return rows[-n:]
def snapshot():
    A = _api(); book = _j(os.path.join(S, "thf_book.json"), {}) or {}; t = dt.datetime.now(WITA)
    H = A.get("health") or {}; eng = (H.get("engine") or {}) if isinstance(H, dict) else {}
    snap = dict(asof_wita=t.strftime("%Y-%m-%d %H:%M WITA"), paper_only=True, dash_built_utc=A.get("built_utc"), dash_err=A.get("_err"),
                today=A.get("today") or {k: book.get(k) for k in ("today_pnl_aud", "equity_aud", "pnl_since_inception_aud", "cash_aud")},
                seats=_seat_money(book), positions=_positions(book), leaderboard=(A.get("leaderboard") or {}).get("rows") or (A.get("leaderboard") or {}).get("standings"),
                health=dict(engine_hb_age_min=_age_min(eng.get("utc")), broken=A.get("broken") or [], info=[x.get("what") if isinstance(x, dict) else x for x in (A.get("info") or [])][:8],
                            paid_feeds={k: (v.get("healthy") if isinstance(v, dict) and "healthy" in v else (v.get("alive") if isinstance(v, dict) and "alive" in v else None)) for k, v in (A.get("paid_feeds") or {}).items()}, borrow=(A.get("borrow") or {}).get("status"),
                            coverage=(A.get("coverage") or {}).get("totals"), feeds_extra=_feeds_extra()),
                gates=A.get("gate_overlord") or {}, wake_counts=A.get("wake_counts") or {}, wakes=[dict(id=w.get("wake_id"), status=w.get("status"), seats=w.get("seats") if isinstance(w.get("seats"), list) else list((w.get("seats") or {}).keys()), outcome=w.get("outcome") or w.get("result")) for w in (A.get("wakes") or [])[-4:]],
                brains=_brains(), ai=_ai_status(), scan=(_j(os.path.join(S, "scan", "_summary.json"), {}) or {}), news=_news(), chat_recent=_chat(),
                cards=[{k: c.get(k) for k in ("seat", "trader", "equity_aud", "day_pnl_aud", "pnl_since_aud", "pnl_light", "rank", "open", "armed", "last_action", "last_wake")} for c in (A.get("cards") or []) if isinstance(c, dict)])
    return snap
def _fmt(snap, seat=None, n=2500):
    T = snap.get("today") or {}; L = []
    _lbsum = None
    try:
        _lb = snap.get("leaderboard"); _lb = _lb.get("rows") if isinstance(_lb, dict) else _lb
        _lbsum = round(sum(float(r.get("day_pnl_aud") or 0) for r in _lb if isinstance(r, dict)), 2) if _lb else None
    except Exception: _lbsum = None
    L.append("DASHBOARD SNAPSHOT (%s, PAPER, read-only): book A$%s (since start %s; today %s since 06:00 WITA = the big number on Brett's dashboard; book day since 00:00 WITA %s). Dash data %s." % (snap.get("asof_wita"), ("%.0f" % float(T.get("equity_aud") or 0)), _a(T.get("pnl_since_inception_aud")), _a(_lbsum if _lbsum is not None else T.get("today_pnl_aud")), _a(T.get("today_pnl_aud")), ("UNREACHABLE: " + snap["dash_err"]) if snap.get("dash_err") else "ok"))   # dayclock_20261103
    sm = sorted(snap.get("seats") or [], key=lambda r: -abs(r["pnl_since_seed_aud"]))
    if seat: sm = [r for r in sm if r["seat"] == seat] + [r for r in sm if r["seat"] != seat]
    L.append("SEATS (A$ vs 500k seed): " + " | ".join("%s %s%s" % (r["seat"], _a(r["pnl_since_seed_aud"]), " (%d open/%d armed)" % (r["n_open"], r["n_armed"]) if (r["n_open"] or r["n_armed"]) else "") for r in sm[:11]))
    P = snap.get("positions") or []
    if seat: P = [p for p in P if p["seat"] == seat] + [p for p in P if p["seat"] != seat]
    L.append("OPEN/ARMED: " + ("; ".join("%s %s %s %s e%s st%s %s%s" % (p["seat"], p["status"], p["ticker"], p["side"], p.get("fill") or p.get("entry"), p.get("stop"), p.get("grade") or "", (" upnl" + _a(p["unreal_aud"])) if p.get("unreal_aud") is not None else "") for p in P[:16]) or "none"))
    H = snap.get("health") or {}; br = list(H.get("broken") or []) + [dict(what=w) for w in _vendor_broken(H.get("feeds_extra"))]   # healthdesk_20261103: down/stale vendors go into 'broken:' 
    feeds = H.get("paid_feeds") or {}
    reg = (snap.get("news") or {}).get("regime"); reg = reg.get("flag") if isinstance(reg, dict) else reg   # dashctx fix: flag only, never the raw gauges dict
    fl = ", ".join("%s %s" % (k, "ok" if v else "DOWN") for k, v in list(feeds.items())[:6] if isinstance(v, bool)) or "none reported"   # only feeds that report a health flag; {} = no status, not DOWN
    L.append("HEALTH: engine heartbeat %s min; broken: %s; paid feeds %s; %s; borrow %s; news stream %s min old%s." % (H.get("engine_hb_age_min"), ("; ".join((b.get("what") if isinstance(b, dict) else str(b))[:70] for b in br[:4]) or "none"),
             fl, _feeds_line(H.get("feeds_extra")), H.get("borrow"), (snap.get("news") or {}).get("stream_age_min"), (" (regime %s)" % reg) if reg else ""))   # oanduw_20261103
    B = {k: v for k, v in (snap.get("brains") or {}).items() if k != "fixer"}; nb = sum(1 for v in B.values() if v.get("mode") == "brain"); nc = sum(1 for v in B.values() if v.get("mode") == "brain" and "claude" in str(v.get("model") or "").lower())
    ai = snap.get("ai") or {}
    L.append("BRAINS: %d/%d seats on an AI brain (%d on Claude); down: %s. AI: %s." % (nb, len(B), nc, ", ".join(s for s, v in B.items() if v.get("mode") == "brain_down") or "none",
             "; ".join("%s %s%s" % (k, v.get("state"), (" (" + str(v.get("model") or v.get("why") or "")[:40] + ")") if (v.get("model") or v.get("why")) else "") for k, v in ai.items())))
    G = snap.get("gates") or {}
    L.append("GATES: stuck %s; waiting %s. Wakes today %s." % (G.get("stuck") or "none", [x.get("seat") if isinstance(x, dict) else x for x in (G.get("waiting_closed") or [])][:5] or "none", (snap.get("wake_counts") or {}).get("total")))
    sc = (snap.get("scan") or {}).get("seats") or {}
    if sc: L.append("SCAN (%s): " % str((snap.get("scan") or {}).get("wita") or "")[:16] + " | ".join("%s %s/%s ok" % (s, v.get("ok"), v.get("scanned")) for s, v in list(sc.items())[:11]))
    C = snap.get("chat_recent") or []
    if C: L.append("RECENT CHAT: " + " || ".join("%s %s->%s: %s" % (c["t"], c["frm"], c["to"], c["text"][:70]) for c in C[-4:]))
    L.append("Full detail (sections today/seats/positions/leaderboard/health/gates/wakes/brains/ai/scan/news/chat_recent/cards): state/thf/dash_context.json - ask for a section by name.")
    out = "\n".join(L)
    return out if len(out) <= n else out[:n - 3] + "..."
def refresh(write=True):
    snap = snapshot(); md = _fmt(snap)
    if write:
        try:
            for p, body in ((OUT_JSON, json.dumps(snap, default=str, indent=0)), (OUT_MD, md)):
                tmp = p + ".tmp"; open(tmp, "w").write(body); os.replace(tmp, p)
        except Exception: pass
    return md
def block(seat=None, n=2500, max_age_s=240):
    """Summary for a prompt. Uses the cached snapshot when fresh (<= max_age_s), else rebuilds. Never raises."""
    try:
        if os.path.exists(OUT_JSON) and time.time() - os.path.getmtime(OUT_JSON) <= max_age_s:
            snap = _j(OUT_JSON, None)
            if snap: return _fmt(snap, seat, n)
        return _fmt(snapshot(), seat, n)
    except Exception: return ""
def section(name, n=6000):
    try: return json.dumps((_j(OUT_JSON, {}) or {}).get(name), default=str)[:n]
    except Exception: return ""
def shot(url=None, path=None, width=430, height=1400):
    """Headless screenshot of the dash for vision AIs (phone width). Returns the path or None."""
    try:
        from playwright.sync_api import sync_playwright
        path = path or SHOT; os.makedirs(os.path.dirname(path), exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"]); pg = b.new_page(viewport=dict(width=width, height=height)); pg.goto(url or DASH_URL, timeout=30000, wait_until="networkidle")
            pg.screenshot(path=path + ".tmp.png", full_page=False); b.close()
        os.replace(path + ".tmp.png", path); return path
    except Exception: return None
if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "shot": print(shot())
    elif a and a[0] == "section": print(section(a[1]))
    else: print(refresh())
