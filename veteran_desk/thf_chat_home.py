"""chatgb_20261103 (Brett P0 2 Nov 22:00 WITA, PAPER): Grok-Bot style chat for the THF dash.
Home list (pinned admins + one chat per seat with last message / unread) and 1:1 threads, built from the msgdesk thread log
state/thf/funder_thread.jsonl (rows: from/to/root/reply_to/mode/kind/text/ts_wita/images) plus the DESK advisor notes.
Pure functions over row lists (grouping, noise and unread rules) so they are unit-testable; the dash calls home()/thread().
READ ONLY except react() (heartreply_20261103: Brett's hearts -> state/thf/chat_imgs/chat_reacts.jsonl, the one dash-writable live dir); never touches msgdesk routing. Seat list is data-driven: veteran_desk/seats/*.json + ADMIN list
+ any seat id that appears in the thread, so new seats (Software Engineer, CFO) show up without a code change."""
import datetime as dt, glob, hashlib, json, os, re, time
from veteran_desk import thf_funder as TF

L = TF.L; THREAD = L + "/state/thf/funder_thread.jsonl"; SEATS_DIR = L + "/veteran_desk/seats"; HB = L + "/state/thf/seat_heartbeat.json"
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(CODE_ROOT, "tools")  # Keep accepted UI code separate from authoritative live chat data.
WITA = TF.WITA
PENDING_MIN = 20          # a reply still counts as "thinking" this long after Brett's message
HOME_IMP_N = 40           # important row keys kept per chat (client counts the ones it has not read)
PREVIEW_N = 110
# ---- seat registry -------------------------------------------------------------------------------------------------
DESK = dict(banks="Banks", crypto="Crypto", energy="Energy", essentials_utilities="Essentials", fx_commodities="FX", healthcare="Healthcare",
            macro="Macro", news_filings="News", price_action="Price action", tech_ai_infra="Tech/AI", trend="Trend",
            head_trader="Oversight, all AIs", head_of_desk="Head of desk", blind_challenger="Blind challenger", devils_advocate="Devil's advocate",
            fixer="Repairs", software_engineer="Software engineer", cfo="CFO", risk_gate="Risk gate")
ADMIN_ORDER = ["head_trader", "head_of_desk", "blind_challenger", "devils_advocate", "fixer", "software_engineer", "cfo"]
ADMIN_NAMES = dict(head_trader="Head Trader", fixer="Fixer", software_engineer="Simon", cfo="CFO")   # seats without a persona file (yet)
STAR = {"head_trader"}
NO_CHAT_ROLES = {"gate"}   # deterministic code seats (risk_gate) cannot chat
PSEUDO = {"brett", "ALL", "code_hands", "strategy_lab", "msgdesk", "Grok advisor", "Claude advisor", "ChatGPT advisor", "SuperGrok advisor"}
ADVISORS = [("adv:grok", "Grok advisor"), ("adv:claude", "Claude advisor"), ("adv:chatgpt", "ChatGPT")]
PALETTE = ["#e4572e", "#2b8ccf", "#7a3fa8", "#1f9e89", "#d2691e", "#c2185b", "#3f51b5", "#558b2f", "#00838f", "#ef6c00", "#6d4c41", "#8e24aa",
           "#039be5", "#43a047", "#f4511e", "#5e35b1"]
COLOR = dict(head_trader="#b0332f", head_of_desk="#7a3fa8", blind_challenger="#546e7a", devils_advocate="#8d6e63", fixer="#b8650a",
             banks="#2b5c9e", crypto="#f29f05", energy="#d84315", essentials_utilities="#2e7d32", fx_commodities="#00838f", healthcare="#c2185b",
             macro="#3949ab", news_filings="#6d4c41", price_action="#5e35b1", tech_ai_infra="#00acc1", trend="#43a047", ALL="#2a7a5a")
NAMES_EXTRA = dict(brett="You", code_hands="Code hands", strategy_lab="Strategy Lab", grok="Grok", claude="Claude", chatgpt="ChatGPT", ALL="All desk")
NOISE_RX = re.compile(r"^(\[[^\]]{0,80}\]\s*)*\[?code_hands (queued|started)", re.I)
RELAY_RX = re.compile(r"^FROM SEAT (\S+?)(?: to (\S+?))?(?: \[hop \d+\])?(?: \(relay[^)]*\))?:\s*", re.I)
CODE_RX = re.compile(r"^\[brain down[^\]]*\]\s*", re.I)
TRADE_RX = re.compile(r"\b(armed|arm(?:ing)?|fill(?:ed)?|placed|opened|closed|stopped out|cancel(?:led)?|half[- ]?bank|size[- ]?up|new position|entry|exit)\b", re.I)
FAULT_RX = re.compile(r"\b(fault|fail(?:ed|ure|ing)?|error|broken|outage|down|stale|alert|brain_down|timeout|crash)\b", re.I)
_CACHE = {}

def _color(cid):
    if cid in COLOR: return COLOR[cid]
    return PALETTE[int(hashlib.md5(str(cid).encode()).hexdigest()[:6], 16) % len(PALETTE)]
def _initial(name): return (str(name or "?").strip()[:1] or "?").upper()
def seats(rows=None):
    """Registry: id -> dict(id,name,desk,label,admin,star,pinned,color,can_send). Data-driven from the seats dir + ADMIN list + thread."""
    reg = {}
    for p in sorted(glob.glob(os.path.join(SEATS_DIR, "*.json"))):
        if ".bak" in os.path.basename(p): continue
        try:
            with open(p) as fh: d = json.load(fh)
        except Exception: continue
        sid = str(d.get("seat") or os.path.basename(p)[:-5]).strip(); role = str(d.get("role") or "").lower()
        if not sid or role in NO_CHAT_ROLES: continue
        reg[sid] = dict(id=sid, name=d.get("trader_name") or sid, desk=d.get("desk") or DESK.get(sid) or role.title(), admin=(role != "proposer") or sid in ADMIN_ORDER)
    for sid, nm in ADMIN_NAMES.items():
        if sid not in reg and (sid in TF.SEAT_IDS or sid in ADMIN_ORDER[:5]): reg[sid] = dict(id=sid, name=nm, desk=DESK.get(sid, sid), admin=True)
    for sid in TF.SEAT_IDS:
        if sid not in reg: reg[sid] = dict(id=sid, name=dict(TF.SEATS).get(sid, sid).split(" (")[0], desk=DESK.get(sid, sid.replace("_", " ")), admin=sid in ADMIN_ORDER)
    for r in rows or []:   # a seat that talks but has no file yet still gets a chat
        for sid in (r.get("from"), r.get("to")):
            if str(sid or "").lower() in ("supergrok", "adv:supergrok"): continue
            if sid and sid not in reg and sid not in PSEUDO and not str(sid).startswith("adv:") and re.match(r"^[a-z][a-z0-9_]{1,40}$", str(sid)):
                reg[sid] = dict(id=sid, name=sid.replace("_", " ").title(), desk=DESK.get(sid, "seat"), admin=sid in ADMIN_ORDER)
    for sid, s in reg.items():
        s.update(label="%s (%s)" % (s["name"], s["desk"]), star=sid in STAR, pinned=bool(s["admin"]), color=_color(sid), av=_initial(s["name"]), can_send=sid in TF.SEAT_IDS)
    return reg
def name_of(sid, reg):
    if sid in reg: return reg[sid]["name"]
    return NAMES_EXTRA.get(sid) or str(sid or "?")
# ---- rows ------------------------------------------------------------------------------------------------------------
def _read_rows():
    """Thread rows with k (1-based file index), cached on (mtime, size) so 3s polls are cheap."""
    try: st = os.stat(THREAD); key = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError: return []
    c = _CACHE.get("rows")
    if c and c[0] == key: return c[1]
    rows = []
    for i, l in enumerate(open(THREAD, encoding="utf-8", errors="replace")):
        if not l.strip(): continue
        try: r = json.loads(l)
        except Exception: continue
        if isinstance(r, dict): r["k"] = len(rows) + 1; rows.append(r)
    _CACHE["rows"] = (key, rows); return rows
def adv_rows():
    """DESK advisor notes in thread-row shape (chat adv:<advisor>); k = index in the advisor file."""
    out = []
    try: rows = [r for r in TF._aread() if r.get("trade_id") == "DESK"]
    except Exception: rows = []
    for i, r in enumerate(rows):
        a = str(r.get("advisor") or r.get("role") or "").strip().lower(); me = r.get("role") == "brett"
        if "adv:" + a not in {sid for sid, _ in ADVISORS}: continue  # historical retired notes stay in the audit file
        out.append({"k": i + 1, "id": r.get("id"), "from": "brett" if me else a, "to": "adv:" + a if me else "brett", "text": r.get("text") or "",
                    "ts_wita": r.get("ts_wita"), "mode": "advisor", "images": r.get("images") or [], "reply_to": r.get("reply_to"), "root": r.get("reply_to") or r.get("id"), "chat": "adv:" + a})
    return out
def kind(r):
    """me | noise | relay | them.  noise = queue/system lines (hidden unless the toggle is on); relay = seat-to-seat 'FROM SEAT'
    rows (collapsed 'Message from X' line in the recipient's chat); them = a reply worth reading."""
    t = str(r.get("text") or ""); f = r.get("from")
    if f == "brett": return "me"
    if r.get("kind") == "deferred" or r.get("mode") == "advisor_idea" or f == "code_hands" or NOISE_RX.match(t): return "noise"
    if t.startswith("FROM SEAT "): return "relay"
    return "them"
def chat_of(r, roots, reg):
    """Which 1:1 chat a row belongs to (a seat reply to a broadcast also shows in ALL - see chats_of)."""
    f, to = r.get("from"), r.get("to"); kd = kind(r)
    if r.get("chat"): return r["chat"]
    if kd == "me": return "ALL" if to in (None, "", "ALL") else to
    if kd == "relay" or r.get("mode") == "advisor_idea": return to if to and to != "ALL" and to != "brett" else (f if f in reg else "ALL")
    if f in reg: return f
    root = roots.get(r.get("root") or r.get("reply_to") or "")
    if root is not None: return chat_of(root, roots, reg)
    return "fixer" if f in ("code_hands", "fixer") else "ALL"
def chats_of(r, roots, reg):
    c = chat_of(r, roots, reg); out = [c]
    # Colleague conversations appear for both participants, never unrelated seats.
    frm, to = r.get("from"), r.get("to")
    if frm in reg and to in reg and frm != to:
        for participant in (frm, to):
            if participant not in out: out.append(participant)
    if kind(r) != "me" and c != "ALL":
        root = roots.get(r.get("root") or r.get("reply_to") or "")
        if root is not None and root.get("from") == "brett" and root.get("to") in (None, "", "ALL"): out.append("ALL")
    return out
def important(r, kd=None):
    """Unread-dot rule (Brett 21:10 WITA 2 Nov): only trades, faults, or messages addressed to Brett. Noise/relay chatter never."""
    kd = kd or kind(r)
    if kd in ("me", "noise"): return False
    if kd == "relay": t = RELAY_RX.sub("", str(r.get("text") or "")); return bool(TRADE_RX.search(t) or FAULT_RX.search(t))
    return r.get("to") == "brett" or bool(TRADE_RX.search(str(r.get("text") or "")) or FAULT_RX.search(str(r.get("text") or "")))
def _ts(s):
    try: return dt.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WITA)
    except Exception: return None
def shape(r, reg, roots=None, chat=None):
    """Compact row for the client."""
    kd = kind(r); t = str(r.get("text") or ""); f = r.get("from"); out = dict(k=r.get("k"), id=r.get("id"), f=f, to=r.get("to"), nm=name_of(f, reg), kd=kd,
                                                                            ts=r.get("ts_wita"), text=t, mode=r.get("mode"), root=r.get("root"), rt=r.get("reply_to"))
    if r.get("images"): out["images"] = list(r["images"])[:4]
    if r.get("ai"): out["ai"] = r["ai"]
    if kd == "relay":
        m = RELAY_RX.match(t); out["nm"] = name_of(m.group(1), reg) if m else out["nm"]; out["text"] = RELAY_RX.sub("", t, 1)
    elif kd == "them" and (r.get("mode") == "code" or CODE_RX.match(t)):
        out["code"] = True
        out["text"] = CODE_RX.sub("", t, 1)
    if roots is not None and kd != "me":
        root = roots.get(r.get("root") or r.get("reply_to") or "")
        if root is not None and (chat is None or chat_of(root, roots, reg) != chat): out["q"] = str(root.get("text") or "")[:160]
    return out
def _roots(rows): return {r["id"]: r for r in rows if r.get("id")}
def _pending(chat_rows, now=None):
    """Brett asked recently and nobody answered yet -> 'thinking' line. For ALL: how many seats have replied so far."""
    now = now or dt.datetime.now(WITA); me = [r for r in chat_rows if kind(r) == "me"]
    if not me: return None
    last = me[-1]; t = _ts(last.get("ts_wita"))
    if t is None or (now - t).total_seconds() > PENDING_MIN * 60: return None
    later = [r for r in chat_rows if kind(r) == "them" and r.get("mode") not in ("code","note","deferred","work_queued") and (r.get("k") or 0) > (last.get("k") or 0)]
    if last.get("to") in (None, "", "ALL"): got = [r for r in later if r.get("root") == last.get("id")]; want = max(1, len([1 for s in TF.SEAT_IDS if s != "fixer"]))
    else: got = later; want = 1
    answered=len({r.get("from") for r in got})
    if answered >= want: return None
    return dict(since=last.get("ts_wita"),got=answered,want=want)
def group(rows, reg=None):
    """rows -> {chat id: [rows]} (each row keeps its k). The ALL chat also gets seat replies to broadcasts."""
    reg = reg or seats(rows); roots = _roots(rows); g = {}
    for r in rows:
        for c in chats_of(r, roots, reg): g.setdefault(c, []).append(r)
    return g
def summarize(cid, chat_rows, reg, hb=None, now=None):
    vis = [r for r in chat_rows if kind(r) != "noise"]; last = vis[-1] if vis else None
    imp = [r["k"] for r in chat_rows if important(r)][-HOME_IMP_N:]
    meta = reg.get(cid)
    if meta: d = dict(id=cid, name=meta["name"], desk=meta["desk"], label=meta["label"], av=meta["av"], color=meta["color"], pinned=meta["pinned"], star=meta["star"], admin=meta["admin"], can_send=meta["can_send"])
    elif cid == "ALL": d = dict(id="ALL", name="All desk", desk="every seat", label="All desk", av="A", color=COLOR["ALL"], pinned=False, star=False, admin=False, can_send=True)
    else:
        nm = dict(ADVISORS).get(cid) or cid; d = dict(id=cid, name=nm, desk="advisor", label=nm + " (advisor)", av=_initial(nm), color=_color(cid), pinned=False, star=False, admin=False, can_send=cid.startswith("adv:"))
    pv = ""
    if last is not None:
        kd = kind(last); s = shape(last, reg)
        preview=s["text"]
        if s.get("code"):
            # Preserve the full audit text in the thread; make the chat list show useful facts first.
            preview=re.sub(r"^\[[^\]]*\]\s*","",preview)
            preview="Cached desk update: "+preview
        pv = ("You: " if kd == "me" else "" if kd == "them" else "Message from %s: " % s["nm"]) + re.sub(r"\s+", " ", preview).strip()[:PREVIEW_N]
        if not s["text"].strip() and s.get("images"): pv += "[image]"
    d.update(last=dict(k=last["k"], ts=last.get("ts_wita"), f=last.get("from")) if last is not None else None, preview=pv, imp=imp, n=len(vis), pending=_pending(chat_rows, now))
    h = (hb or {}).get(cid) if hb else None
    if h: d["status"] = dict(mode=h.get("mode"), model=h.get("model") or h.get("chat_model"), seen=h.get("last_seen") or h.get("last_round"))
    return d
def _hb():
    try: return json.load(open(HB))
    except Exception: return {}
def home(rows=None, adv=None, reg=None, hb=None, now=None):
    """Home list: pinned (admins, Head Trader starred) + every seat chat + All desk + advisors, newest first after pinned."""
    rows = _read_rows() if rows is None else rows; adv = adv_rows() if adv is None else adv
    reg = reg or seats(rows); hb = _hb() if hb is None else hb
    g = group(rows, reg)
    for r in adv:
        if r.get("chat") in {sid for sid, _ in ADVISORS}: g.setdefault(r["chat"], []).append(r)
    ids = list(reg) + ["ALL"] + [a for a, _ in ADVISORS]
    for c in g:
        if c.startswith("adv:") and c not in {sid for sid, _ in ADVISORS}: continue
        if c not in ids: ids.append(c)
    chats = [summarize(c, g.get(c, []), reg, hb, now) for c in ids]
    pinned = sorted([c for c in chats if c["pinned"]], key=lambda d: (ADMIN_ORDER.index(d["id"]) if d["id"] in ADMIN_ORDER else 99, d["name"]))
    rest = sorted(chats, key=lambda d: (str(d["last"]["ts"]) if d["last"] else ""), reverse=True)   # newest chat first; never-used chats last
    return dict(pinned=[dict(id=c["id"], name=c["name"], desk=c["desk"], av=c["av"], color=c["color"], star=c["star"], status=c.get("status")) for c in pinned],
                chats=rest, next=len(rows), ts=dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), paper=True)
def thread(cid, after=0, before=0, limit=60, rows=None, adv=None, reg=None, now=None):
    """One chat. after=k -> only newer rows (incremental poll); before=k -> older page; else the last `limit` rows."""
    cid = str(cid or "ALL"); rows = _read_rows() if rows is None else rows; reg = reg or seats(rows)
    if cid.startswith("adv:"):
        adv = adv_rows() if adv is None else adv; mine = [r for r in adv if r["chat"] == cid]; roots = {}; total = len(adv)
    else:
        roots = _roots(rows); mine = [r for r in rows if cid in chats_of(r, roots, reg)]; total = len(rows)
    if after: sel = [r for r in mine if r["k"] > int(after)]; more = False
    elif before: older = [r for r in mine if r["k"] < int(before)]; sel = older[-limit:]; more = len(older) > limit
    else: sel = mine[-limit:]; more = len(mine) > limit
    out = [shape(r, reg, roots, cid) for r in sel]
    try:   # heartreply_20261103
        _lk = likes(); _src = "a" if cid.startswith("adv:") else "t"
        for o in out:
            if _lk.get(_src + str(o.get("k"))): o["lk"] = True
    except Exception: pass
    meta = summarize(cid, mine, reg, _hb(), now)
    return dict(chat={k: meta[k] for k in ("id", "name", "desk", "label", "av", "color", "pinned", "star", "admin", "can_send", "status") if k in meta},
                rows=out, next=total, more=more, first=not (after or before), pending=meta.get("pending"), n=meta.get("n"))
# ---- hearts (heartreply_20261103, Brett 3 Nov 18:00 WITA): append-only log, last write per row wins -------------------------
REACTS = L + "/state/thf/chat_imgs/chat_reacts.jsonl"
def likes():
    """{"t<k>"|"a<k>": True} for rows Brett hearted (t = funder_thread row k, a = advisor row k). Cached on (mtime, size)."""
    try: st = os.stat(REACTS); key = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError: return {}
    c = _CACHE.get("likes")
    if c and c[0] == key: return c[1]
    d = {}
    for l in open(REACTS, encoding="utf-8", errors="replace"):
        try: r = json.loads(l); d[str(r["src"]) + str(int(r["k"]))] = bool(r.get("on"))
        except Exception: continue
    d = {k: True for k, v in d.items() if v}; _CACHE["likes"] = (key, d); return d
def react(b):
    """POST /api/chat_react {k, src t|a, on, chat, f, id, root, ts} -> append one row (flock). PAPER chat only: never feeds gate/book/engine."""
    import fcntl
    b = b if isinstance(b, dict) else {}; k = int(b.get("k") or 0); src = str(b.get("src") or "t")
    if k <= 0 or src not in ("t", "a"): raise ValueError("need k>0 and src t|a")
    r = dict(ts_wita=dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), k=k, src=src, on=bool(b.get("on")), by="brett", emoji="heart",
             **{x: (str(b.get(x))[:80] if b.get(x) is not None else None) for x in ("chat", "f", "id", "root", "ts")})
    with open(REACTS, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX); fh.write(json.dumps(r) + "\n"); fh.flush(); fcntl.flock(fh, fcntl.LOCK_UN)
    return dict(ok=True, k=k, src=src, on=r["on"])
# ---- AI status chips (B20a, master fixer 3 Nov): one chip per AI, ELI10, read-only ---------------------------------------
CD = L + "/state/thf/ai_cooldown.json"; GPT = L + "/state/thf/chatgpt_status.json"; SGW = L + "/state/thf/grok_web_status.json"; PROBE = L + "/state/thf/ai_probe.json"
AI_NAMES = (("claude", "Claude"), ("chatgpt", "ChatGPT"), ("grok", "Grok"))
AI_COLOR = dict(up="#2ea043", cooling="#d29922", busy="#d29922", down="#f85149", unknown="#6e7681")
def _jload(p, d=None):
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return d if d is not None else {}
def _short_why(w):
    w = re.sub(r"\s+", " ", str(w or "")).strip()
    w = re.split(r"\. Re-enable:|; browser stopped|\. Relays stopped", w)[0]
    if "Cursor" in w and ("limit" in w or "key" in w): return "needs Brett (Cursor limit / key)"
    if "captcha" in w.lower() or "sign-in" in w.lower() or "sign in" in w.lower(): return "needs Brett (grok.com sign-in)"
    return w[:90]
def _fresh_status(record, now_t, max_age=300):
    if not isinstance(record,dict):return False
    from veteran_desk.thf_home import _parse
    seen=_parse(record.get("checked_utc") or record.get("ts") or record.get("utc") or record.get("ts_wita") or record.get("last_ask"))
    return seen is not None and -120 <= now_t-seen.timestamp() <= max_age
CLAUDE_LOG = L + "/logs/claude_brain.jsonl"
def _claude_recent(now_t, window_s=3600, path=None, tail_bytes=200000):
    """aichip_20261106 (master fixer, G3/G11): what Claude actually did in the last hour, from logs/claude_brain.jsonl (every CLI call is logged
    there with ok/err/model). Returns dict(ok=n, fail=n, last_model, last_err, last_utc) or None when the log is unreadable."""
    import datetime as _d
    try:
        with open(path or CLAUDE_LOG, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - tail_bytes)); chunk = f.read().decode("utf-8", "ignore")
    except OSError: return None
    ok = fail = 0; last_model = last_err = last_utc = None
    for ln in chunk.splitlines()[1:] if size > tail_bytes else chunk.splitlines():
        try: r = json.loads(ln)
        except Exception: continue
        try: t = _d.datetime.strptime(str(r.get("utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_d.timezone.utc).timestamp()
        except Exception: continue
        if now_t - t > window_s or t > now_t + 120: continue
        if r.get("ok"): ok += 1; last_model = "claude %s %s" % (r.get("model"), r.get("effort") or ""); last_utc = r.get("utc")
        else: fail += 1; last_err = r.get("err"); last_utc = last_utc or r.get("utc")
    return dict(ok=ok, fail=fail, last_model=last_model, last_err=last_err, last_utc=last_utc)
def ai_status(now=None, cd=None, gpt=None, sgw=None, hb=None, probe=None, recent=None, grok_provider=None):
    """[{id,name,state(up|busy|cooling|down|unknown),color,model,note,until}] - ELI10 one-liners for the chat chip row."""
    now = now or dt.datetime.now(WITA); now_t = now.timestamp(); cd = _jload(CD) if cd is None else cd
    if gpt is None:
        observation=_jload(os.path.join(os.path.dirname(GPT),"chatgpt_health.json"))
        gpt=observation if observation else _jload(GPT)
    sgw = _jload(SGW) if sgw is None else sgw; hb = _hb() if hb is None else hb; probe = (_jload(PROBE).get("ais") or {}) if probe is None else probe
    out = []
    from veteran_desk import deepseek_bridge as DS
    names=AI_NAMES+(("deepseek_api","DeepSeek API"),) if DS.enabled() else AI_NAMES
    for aid, nm in names:
        c = cd.get(aid) or {}; cooling = float(c.get("until") or 0) > now_t; auth = str(c.get("kind") or "") in ("auth", "down")
        st, model, note, until = "unknown", "", "no recent check", None
        if aid == "claude":
            from veteran_desk.thf_home import _brain_observation
            seats_on = [v for v in (hb or {}).values() if isinstance(v,dict) and _brain_observation(v,now)[2] and "claude" in str(v.get("model") or "")]
            if seats_on:
                models = {}
                for v in seats_on: models[v["model"]] = models.get(v["model"], 0) + 1
                model = max(models, key=models.get); st, note = "up", "%d/%d seats on it" % (len(seats_on), len(hb or {}))
            elif (probe.get("claude") or {}).get("ok") and _fresh_status(probe.get("claude"),now_t): st, model, note = "up", str(probe["claude"].get("model") or "claude"), "last probe ok"
            else:   # aichip_20261106: seats run DeepSeek/ChatGPT first by policy (G4), so "no seat on Claude" is normal - judge Claude by its own recent calls, not a 3 Nov last_fail string
                rc = _claude_recent(now_t) if recent is None else recent
                if rc and rc.get("ok"): st, model, note = "up", str(rc.get("last_model") or "claude"), "answered %d of %d calls in the last hour (seats use it as the fallback)" % (rc["ok"], rc["ok"] + rc["fail"])
                elif rc and rc.get("fail"): st, note = "down", _short_why(rc.get("last_err"))
                else: st, note = "unknown", "idle - no Claude call in the last hour and not cooling; seats go DeepSeek/ChatGPT first"
        elif aid == "chatgpt":
            g = gpt or {}; model = re.sub(r"^Thinking effort\s*", "", str(g.get("model_pick") or "")).strip()
            if g.get("state") == "logged_in":
                if not _fresh_status(g,now_t): st, note = "unknown", "last bridge check is old; availability unverified"
                elif g.get("busy") or int(g.get("queue") or 0) > 0: st, note = "busy", "busy; a request is in progress"
                elif g.get("last_ask_ok") is False:
                    err = str(g.get("last_err") or "")
                    st, note = "unknown", ("signed in; last ask timed out" if "timeout" in err.lower() else "signed in; last ask failed")
                elif g.get("last_delivery") == "client_disconnected":
                    st, note = "up", "signed in; last result was not delivered (client disconnected)"
                elif g.get("last_delivery") == "timeout":
                    st, note = "unknown", "signed in; the waiting client timed out"
                else: st, note = "up", "signed in" + (" (last ask ok)" if g.get("last_ask_ok") else "")
            elif g.get("state") in ("needs_signin", "captcha"): st, note = "down", "needs Brett (ChatGPT sign-in)"
            elif g.get("state"): st, note = "down", "web bridge %s" % g.get("state")
        elif aid == "grok":
            pr = probe.get("grok") or {}
            provider = grok_provider
            if provider is None:
                try:
                    from veteran_desk import thf_grok as GR
                    provider = GR.state() or {}
                except Exception:
                    provider = {}
            if pr.get("ok") and _fresh_status(pr, now_t): st, model, note = "up", str(pr.get("model") or "grok relay"), "relay ok"
            elif provider.get("ok") is True and _fresh_status(provider, now_t): st, model, note = "up", str(provider.get("model") or "grok"), "authenticated route checked"
            elif provider.get("ok") is False and _fresh_status(provider, now_t): st, note = "down", _short_why(provider.get("detail") or provider.get("last_transient_error"))
            else:
                detail = provider.get("detail") or provider.get("last_transient_error") or pr.get("err") or ""
                st, note = "unknown", ("no recent check; last note: " + _short_why(detail)) if detail else "no recent check"
        elif aid=="deepseek_api":
            # The sandboxed dashboard cannot inspect private credentials or
            # write provider locks. Show the real cached model health instead.
            why=DS.reason(check_key=False);model="deepseek-flash API"
            st,note=("up","Verified API; %d authorised seats; %d parallel slots"%(len(DS.allowed_seats()),DS.capacity())) if not why else (("busy",why) if "busy" in why.lower() else (("cooling",why) if "cooling" in why.lower() else ("down",why)))
        if cooling:   # a cooldown overrides: auth/down kinds need Brett (red), quota/overload pauses recover by themselves (amber)
            until = c.get("until_wita"); st = "down" if auth else "cooling"
            note = ("off - " if auth else "cooling until %s - " % str(until or "")[11:16]) + _short_why(c.get("why"))
        out.append(dict(id=aid, name=nm, state=st, color=AI_COLOR[st], model=model, note=note, until=until))
    return out
# ---- html -------------------------------------------------------------------------------------------------------------
def _file(name):
    p = os.path.join(TOOLS, name)
    try: st = os.stat(p)
    except FileNotFoundError: return ""
    c = _CACHE.get(p)
    if c and c[0] == (st.st_mtime_ns, st.st_size): return c[1]
    with open(p, encoding="utf-8") as stream:
        s = stream.read()
    _CACHE[p] = ((st.st_mtime_ns, st.st_size), s)
    return s
def js(): return _file("thf_chat.js")
def inline(html): return html.replace("<!--THF_CHAT_JS-->", "<script>" + js() + "</script>", 1)
def page_html(): return inline(_file("thf_chat_page.html"))
def embed_html(): return inline(_file("thf_chat.html"))
if __name__ == "__main__":
    import sys
    a = sys.argv[1:] or ["home"]
    if a[0] == "home": print(json.dumps(home(), indent=1, default=str)[:6000])
    else: print(json.dumps(thread(a[0], limit=int(a[1]) if len(a) > 1 else 10), indent=1, default=str)[:6000])
