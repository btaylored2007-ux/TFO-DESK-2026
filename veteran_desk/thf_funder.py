"""tfo_funmsg_20261102 (PAPER, droplet overlay): Funder messages for THF. Token-free: dashboard POST appends JSONL,
the 2-min THF tick (no AI) logs unacked messages + writes state/thf/funder_inbox.json and FUNDER_OVERRIDE.md for seats.
Never touches ledgers/books. CLI: python -m veteran_desk.thf_funder {list|unacked [seat]|ack ID SEAT|tick}"""
import datetime as dt, fcntl, html, json, os, secrets, sys, time
L = "/opt/tfo/live/thf"; F = L + "/state/funder_messages.jsonl"; LK = F + ".lock"
INBOX = L + "/state/thf/funder_inbox.json"; OVR = L + "/state/thf/FUNDER_OVERRIDE.md"; EVLOG = L + "/logs/funder_msgs.log"
SEATS = [("head_of_desk", "Marcus (head)"), ("head_trader", "HT - Head Trader (oversight, all AIs)"), ("macro", "Theo (macro)"), ("energy", "Rex (energy)"), ("news_filings", "Quinn (news)"),
         ("tech_ai_infra", "Nico (tech/AI)"), ("trend", "Dex (trend)"), ("banks", "Victor (banks)"), ("crypto", "Kai (crypto)"),
         ("essentials_utilities", "Nora (essentials)"), ("fx_commodities", "Lena (FX/commod)"), ("healthcare", "Iris (health)"),
         ("price_action", "price_action seat"), ("blind_challenger", "Sloane (blind challenger)"), ("devils_advocate", "Devil's advocate (admin)"), ("fixer", "FIXER - health/repair (Claude, admin)")]
try:   # seatagents_20261103: data-driven roster - every veteran_desk/seats/*.json (+ fixer); a new seat json appears in the chat seat list automatically
    from veteran_desk import seat_registry as _SR
    _rs = _SR.chat_seats()
    if len(_rs) >= len(SEATS) - 1: SEATS = _rs
except Exception: pass
SEAT_IDS = {s for s, _ in SEATS}
WITA = dt.timezone(dt.timedelta(hours=8))
# chatimg_20261102: chat image attach (PAPER) — store bytes under state/thf/chat_imgs; messages carry ids only
IMG_DIR = L + "/state/thf/chat_imgs"; IMG_MAX = 8 * 1024 * 1024; IMG_MAX_N = 4   # chatfix_20261103: 8MB (iPhone PNG screenshots run 2-6MB)
IMG_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
IMG_EXT = set(IMG_MIME.values()); IMG_ID_RE = __import__("re").compile(r"^ci-\d{14}-[0-9a-f]{4,16}\.(?:jpg|png|webp|gif)$")
def _img_ensure():
    os.makedirs(IMG_DIR, mode=0o750, exist_ok=True)
    try: os.chmod(IMG_DIR, 0o750)
    except Exception: pass
def img_abs(iid):
    iid = str(iid or "").strip()
    if not IMG_ID_RE.match(iid): return None
    path = os.path.join(IMG_DIR, iid)
    return path if os.path.isfile(path) else None
def img_validate(ids):
    out = []
    for i in (ids or [])[:IMG_MAX_N]:
        if img_abs(i): out.append(str(i).strip())
    return out
def img_sniff(raw):   # chatfix_20261103: magic bytes -> mime (browser/iOS declared types are unreliable)
    if raw[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if raw[:2] == b"\xff\xd8": return "image/jpeg"
    if raw[:3] == b"GIF": return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP": return "image/webp"
    return None
def img_save(data_url=None, b64=None, mime=None, raw=None):   # chatfix_20261103: raw= bytes path for multipart uploads
    import base64, re as _re
    _img_ensure()
    if raw is not None:
        if not raw or len(raw) > IMG_MAX: raise ValueError("image empty or >%dMB" % (IMG_MAX // 1048576))
        mime = img_sniff(raw) or str(mime or "").lower().strip()
        b64 = base64.b64encode(raw).decode()
    if data_url:
        m = _re.match(r"^data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", str(data_url).strip(), _re.S)
        if not m: raise ValueError("bad data_url")
        mime, b64 = m.group(1), m.group(2)
    mime = str(mime or "").lower().strip()
    if mime == "image/jpg": mime = "image/jpeg"
    if mime not in IMG_MIME: raise ValueError("mime must be jpeg|png|webp|gif")
    try: raw = base64.b64decode(b64 or "", validate=False)
    except Exception: raise ValueError("bad b64")
    if not raw or len(raw) > IMG_MAX: raise ValueError("image empty or >%dMB" % (IMG_MAX // 1048576))
    ok = (mime == "image/png" and raw.startswith(b"\x89PNG")) or (mime == "image/jpeg" and raw[:2] == b"\xff\xd8") or (mime == "image/gif" and raw[:3] == b"GIF") or (mime == "image/webp" and raw[0:4] == b"RIFF" and raw[8:12] == b"WEBP")
    if not ok: raise ValueError("bytes do not match mime")
    n = dt.datetime.now(dt.timezone.utc)
    iid = "ci-%s-%s.%s" % (n.strftime("%Y%m%d%H%M%S"), secrets.token_hex(4), IMG_MIME[mime])
    path = os.path.join(IMG_DIR, iid); tmp = path + ".tmp"
    open(tmp, "wb").write(raw); os.chmod(tmp, 0o640); os.replace(tmp, path)
    return dict(ok=True, id=iid, url="/api/chat_img/" + iid, bytes=len(raw))
def img_save_multipart(ctype, body):
    """chatfix_20261103: multipart/form-data upload (iPhone Safari FormData / curl -F file=@x.png). First part with a filename wins.
    Returns img_save() dict. Declared part Content-Type is ignored when the magic bytes say otherwise (HEIC etc. -> clear error)."""
    import re as _re
    m = _re.search(r'boundary="?([^";,\s]+)"?', str(ctype or ""), _re.I)
    if not m: raise ValueError("no multipart boundary")
    bnd = b"--" + m.group(1).encode("latin-1")
    for part in (body or b"").split(bnd)[1:]:
        if part[:2] == b"--": break
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep: head, sep, data = part.partition(b"\n\n")
        if not sep: continue
        h = head.decode("latin-1", "replace")
        if "filename=" not in h.lower(): continue
        if data.endswith(b"\r\n"): data = data[:-2]
        elif data.endswith(b"\n"): data = data[:-1]
        cm = _re.search(r"content-type:\s*([\w/.+-]+)", h, _re.I); mime = (cm.group(1).lower() if cm else "")
        if not img_sniff(data): raise ValueError("not a PNG/JPEG/WEBP/GIF image (got %s)" % (mime or "unknown"))
        return img_save(raw=data, mime=mime)
    raise ValueError("no file part (field name 'file')")
def img_read(iid):
    path = img_abs(iid)
    if not path: raise FileNotFoundError("no such image")
    ext = path.rsplit(".", 1)[-1]
    mime = {v: k for k, v in IMG_MIME.items()}[ext]
    return open(path, "rb").read(), mime
class _Lock:
    def __init__(s, ex): s.ex = ex
    def __enter__(s): s.f = open(LK, "a"); fcntl.flock(s.f, fcntl.LOCK_EX if s.ex else fcntl.LOCK_SH); return s
    def __exit__(s, *a): fcntl.flock(s.f, fcntl.LOCK_UN); s.f.close()
def _read():
    try: return [json.loads(l) for l in open(F) if l.strip()]
    except FileNotFoundError: return []
def _rewrite(rows):   # in-place under exclusive lock (dash sandbox may write this file only, not the dir)
    with open(F, "r+") as f: f.seek(0); f.write("".join(json.dumps(r) + "\n" for r in rows)); f.truncate(); f.flush(); os.fsync(f.fileno())
def load(n=50):
    with _Lock(False): return _read()[-n:]
def post(kind, text, seat=None, images=None, ai=None, command=None, dedupe_key=None):   # aipick_20261102 ai: auto|claude|chatgpt|grok|super
    kind=str(kind or "").lower();text=str(text or "").strip();seat=(seat or "").strip() or None
    if len(text)>8000:raise ValueError("Message is longer than 8,000 characters; split it into smaller requests. Nothing was queued.")
    imgs = img_validate(images)
    if kind not in ("override", "all", "seat", "task"): raise ValueError("kind must be override|all|seat|task")
    if not text and not imgs: raise ValueError("empty text")
    if kind == "all": seat = None
    if kind in ("seat", "task") and seat is not None and seat not in SEAT_IDS: raise ValueError("unknown seat")
    if kind == "seat" and seat is None: raise ValueError("unknown seat")
    if kind == "override" and seat not in (None, "all") and seat not in SEAT_IDS: raise ValueError("unknown seat")
    if seat == "all": seat = None
    n = dt.datetime.now(dt.timezone.utc)
    r = dict(id="fm-" + n.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2), ts_utc=n.isoformat(timespec="seconds"),
             ts_wita=n.astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), kind=kind, priority="override" if kind == "override" else "normal",
             seat=seat, text=text, acked_by=[])
    r["delivery"] = {target: {"state": "received", "ts_wita": r["ts_wita"]} for target in targets(r)}
    if command is not None:
        if not isinstance(command, dict) or command.get("mode") not in ("ask", "push", "learn", "control"): raise ValueError("unknown command mode")
        if command.get("mode") == "control" and kind != "task": raise ValueError("task control needs the task queue")
        if len(json.dumps(command)) > 6000: raise ValueError("command too large")
        r["command"] = command
    if dedupe_key:
        key = str(dedupe_key)
        if len(key) > 180: raise ValueError("request identity too long")
        r["dedupe_key"] = key
    if imgs: r["images"] = imgs
    _ai = str(ai or "").strip().lower()
    if _ai=="deepseek": _ai="deepseek_api"
    if _ai == "sg": raise ValueError("sg is not a provider")
    if _ai == "supergrok": raise ValueError("SuperGrok is not connected")
    if _ai=='deepseek_api':
        from veteran_desk import deepseek_bridge as DS
        if not set(targets(r)).issubset(DS.allowed_seats()):raise ValueError('DeepSeek API restricted by authorised seat policy')
    if _ai in ("claude", "chatgpt", "grok", "deepseek", "deepseek_api", "super"): r["ai"] = _ai   # aipick_20261102 (auto/unknown -> omitted = auto)
    with _Lock(True):
        if dedupe_key:
            existing = next((row for row in _read() if row.get("dedupe_key") == str(dedupe_key)), None)
            if existing:
                fields=("kind","seat","text")
                same=all(existing.get(k)==r.get(k) for k in fields)
                if kind=="task":
                    before=(existing.get("command") or {}).get("control") or {};after=(r.get("command") or {}).get("control") or {}
                    same=same and before.get("_body_hash")==after.get("_body_hash") and before.get("op")==after.get("op") and before.get("task_id")==after.get("task_id")
                else:same=same and existing.get("command")==r.get("command")
                if not same:raise ValueError("request identity already used for different content")
                return existing
        with open(F, "a") as f: f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
    return r
def ack(mid, by, remove=False):
    by = str(by or "").strip()[:40] or "funder"
    with _Lock(True):
        rows = _read(); hit = None
        for r in rows:
            if r.get("id") == mid: hit = r; (by in r["acked_by"]) or r["acked_by"].append(by)
        if hit is None: raise ValueError("no such id")
        if remove: rows = [r for r in rows if r.get("id") != mid]
        _rewrite(rows)
    return hit
def delivery(mid, seat, state, detail=""):
    with _Lock(True):
        rows = _read()
        for row in rows:
            if row.get("id") == mid:
                entry=dict((row.get("delivery") or {}).get(seat) or {})
                entry.update(state=state,detail=str(detail)[:300],ts_wita=dt.datetime.now(WITA).strftime("%F %T WITA"))
                if state.startswith("hunt_"):entry["execution_state"]=state
                if state.startswith("work_") or state=="applied":entry["work_state"]=state
                if state in ("answered","brain_pending","cached_reply","learned"):entry["response_state"]=state
                row.setdefault("delivery",{})[seat]=entry
                _rewrite(rows); return True
    return False

def targets(r): return ["msgdesk"] if r.get("kind") == "task" else sorted(SEAT_IDS - {"fixer"}) if not r.get("seat") else [r["seat"]]   # fixer only on direct message
def unacked(seat=None):
    out = []
    for r in load(500):
        if seat: 
            if (r.get("seat") in (None, seat)) and seat not in r.get("acked_by", []) and "funder" not in r.get("acked_by", []): out.append(r)
        elif not r.get("acked_by"): out.append(r)
    return out
EXPIRE_PENDING_S = 24 * 3600   # expired_20261106 (master, G8): a receipt still waiting for an AI reply after a day is reported as expired, not pending
def _row_age_s(row, now=None):
    try:
        t = dt.datetime.fromisoformat(str(row.get("ts_utc")).replace("Z", "+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
        return (now or dt.datetime.now(dt.timezone.utc)).timestamp() - t.timestamp()
    except Exception: return 0.0
def delivery_counts(rows, retry=None, now=None):
    retry = retry if isinstance(retry,dict) else {}
    pending=set(retry); work=set();blocked=set();cached=set();hunts=set();expired=set()
    for row in rows:
        mid=row.get("id"); old = _row_age_s(row, now) > EXPIRE_PENDING_S
        for seat,entry in (row.get("delivery") or {}).items():
            if not isinstance(entry,dict):continue
            key="%s|%s"%(mid,seat);state=entry.get("state");response=entry.get("response_state") or state
            if response in ("received","brain_pending"):
                if old and key not in retry: expired.add(key)   # expired_20261106: the 4-5 Nov Claude outage left receipts waiting forever
                else: pending.add(key)
            elif response in ("answered","learned","cached_reply"):pending.discard(key);expired.discard(key)
            if response=="cached_reply":cached.add(key)
            ws=entry.get("work_state") or state
            if ws in ("work_queued","work_started","work_waiting"):work.add(key)
            if ws=="work_blocked":blocked.add(key)
            if (entry.get("execution_state") or state) in ("hunt_queued","hunt_running"):hunts.add(key)
    # The durable retry queue is authoritative even if another channel updated the receipt.
    pending.update(retry)
    return dict(pending_ai_replies=len(pending),pending_work=len(work),blocked_work=len(blocked),cached_replies=len(cached),pending_hunts=len(hunts),expired_ai_replies=len(expired))
def work_counts():
    def rows(name):
        try:
            out=[]
            for line in open(L+"/state/thf/"+name):
                try:item=json.loads(line)
                except ValueError:continue
                if isinstance(item,dict):out.append(item)
            return out
        except OSError:return []
    results={row.get("id"):row for row in rows("code_hands_results.jsonl")}
    terminal={key for key,row in results.items() if row.get("outcome") in ("applied","reviewed")}
    queued={row.get("id") for row in rows("code_hands_queue.jsonl") if row.get("id") and row.get("id") not in terminal}
    parked={row.get("id") for row in rows("code_hands_parked.jsonl") if row.get("id") and row.get("id") not in terminal}
    return dict(pending_work=len(queued),blocked_work=len(parked))
def summary():
    all_rows=load(500);rows=all_rows[-30:];u=unacked()
    try:retry=json.load(open(L+"/state/thf/chat_retry.json"))
    except (OSError,ValueError):retry={}
    counts=delivery_counts(all_rows,retry)
    counts["pending_work_receipts"]=counts["pending_work"]
    counts["blocked_work_receipts"]=counts["blocked_work"]
    counts.update(work_counts())
    return dict(recent=rows[::-1],unacked=len(u),overrides=[r for r in u if r.get("kind")=="override"],file=F,**counts)
def tick():
    """Token-free: called by run_thf_tick_sgp.sh every 2 min. Logs + publishes unacked messages for seats."""
    u = unacked(); now = dt.datetime.now(WITA).strftime("%F %T WITA")
    per = {s: [r for r in u if r.get("seat") in (None, s) and s not in r.get("acked_by", [])] for s in SEAT_IDS}
    doc = dict(updated_wita=now, unacked=u, per_seat={s: [r["id"] for r in v] for s, v in per.items() if v},
               how_to_ack="python -m veteran_desk.thf_funder ack <id> <seat>  (cwd /opt/tfo/live/thf)")
    t = INBOX + ".tmp"; json.dump(doc, open(t, "w"), indent=1); os.replace(t, INBOX)
    ov = [r for r in u if r.get("kind") == "override"]
    t = OVR + ".tmp"
    open(t, "w").write("# FUNDER OVERRIDE (Brett) - read first on every wake. PAPER.\n" + (("".join("- [%s] %s scope=%s: %s\n" % (r["id"], r["ts_wita"], r.get("seat") or "all", r["text"]) for r in ov)) if ov else "(none active)\n"))
    os.replace(t, OVR)
    with open(EVLOG, "a") as f:
        f.write("%s funder_msgs unacked=%d overrides=%d %s\n" % (now, len(u), len(ov), " ".join("%s:%s:%s" % (r["id"], r["kind"], r.get("seat") or "all") for r in u)))
    return doc
def check_pin(p):
    # tfo_nopin_20261102: PIN removed per Brett (2 Nov 2026 07:52 WITA "Just remove the pin, we don't need it anyway")
    return True
E = lambda s: html.escape(str(s if s is not None else ""), quote=True)
def panel():
    try: S = summary()
    except Exception as x: S = dict(recent=[], unacked=0, error=type(x).__name__)
    opts = "".join('<option value="%s">%s</option>' % (E(s), E(n)) for s, n in SEATS)
    rows = "".join('<div class="fmr"><b>%s</b> <span class="fmk fm-%s">%s</span> → %s · %s<br>%s</div>' % (
        E(r.get("ts_wita")), E(r.get("kind")), E(r.get("kind")).upper(), E(r.get("seat") or "ALL"),
        ("acked: " + E(", ".join(r.get("acked_by") or []))) if r.get("acked_by") else '<span style="color:#f55">UNREAD</span>',
        E(r.get("text"))) for r in S["recent"][:15]) or "<i>no messages</i>"
    try:
        from veteran_desk import thf_msgdesk as _MD; rows = _MD.thread_html() + '<b>Raw inbox</b>' + rows
    except Exception: pass
    return ('<section id="funder-msgs" style="border:2px solid #c90;border-radius:8px;padding:8px;margin:8px;font:13px sans-serif;background:#1b1b10;color:#eee">'
        '<style>#funder-msgs textarea,#funder-msgs select,#funder-msgs input{width:100%%;box-sizing:border-box;margin:3px 0;background:#222;color:#eee;border:1px solid #555;font-size:14px}'
        '#desk-adv textarea,.tnw textarea{display:block;width:100%%;box-sizing:border-box;margin:3px 0;background:#222;color:#eee;border:1px solid #555;border-radius:4px;font-size:14px;padding:6px}#desk-adv .tnw>div,.tnw>div{margin:6px 0}#desk-adv button,.tnw button{display:block;margin:3px 0;padding:6px 12px;font-weight:bold}#desk-adv .fmr{border-top:1px solid #444;padding:4px 0}'
        '#funder-msgs button{margin:3px 0;padding:6px 10px;font-weight:bold}.fmr{border-top:1px solid #444;padding:4px 0}.fm-override{color:#f80;font-weight:bold}</style>'
        '<b>📨 FUNDER MESSAGES</b> (PAPER · token-free · unread %d)<br>'
        '<div><b style="color:#f80">FUNDER OVERRIDE</b><textarea id="fm-ovr" rows="2"></textarea><select id="fm-ovr-seat"><option value="">scope: ALL seats</option>%s</select><button onclick="fmSend(\'override\',\'fm-ovr\',\'fm-ovr-seat\')">Send OVERRIDE</button></div>'
        '<div><b>Message ALL traders</b><textarea id="fm-all" rows="2"></textarea><button onclick="fmSend(\'all\',\'fm-all\')">Send to ALL</button></div>'
        '<div><b>Message one trader</b><select id="fm-seat">%s</select><textarea id="fm-one" rows="2"></textarea><button onclick="fmSend(\'seat\',\'fm-one\',\'fm-seat\')">Send to trader</button></div>'
        '<div id="fm-st"></div><div>%s</div>'
        '<script>async function fmSend(k,t,s){const b={kind:k,text:document.getElementById(t).value,seat:s?document.getElementById(s).value:""};'
        'const st=document.getElementById("fm-st");st.textContent="sending...";try{const r=await fetch("/api/funder_msg",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});'
        'const j=await r.json();st.textContent=r.ok?("saved "+j.id):("error: "+(j.error||r.status));if(r.ok){document.getElementById(t).value="";setTimeout(()=>{const q=new URLSearchParams(location.search);q.set("fm",Date.now());location.search=q.toString()},800)}}catch(e){st.textContent="error "+e}}</script>'
        # tfo_deskadv_20261102: desk-level Grok/Claude boxes (trade_id DESK, advisory only); moved to top of page by JS
        '<div id="desk-adv" style="border:2px solid #39f;border-radius:8px;padding:8px;margin:8px;font:13px sans-serif;background:#101826;color:#eee"><b>🧠 DESK ADVISORS → Grok / Claude</b> <i>(whole THF desk: book, positions, seats, risk · advisory only, never feeds gate/book/engine; optional “send reply to” forwards it as an idea to a seat thread, seat reviews under its own gates/rails)</i><div id="desk-adv-w"></div></div>'
        '<script>window.addEventListener("DOMContentLoaded",()=>{const d=document.getElementById("desk-adv");if(d){const fm=document.getElementById("funder-msgs");if(fm)fm.parentNode.insertBefore(d,fm);const h=document.querySelector("h1");if(h&&!document.getElementById("jump-lnk")){const j=document.createElement("div");j.id="jump-lnk";j.className="sub";j.innerHTML=\'<a href="/desk/chat">💬 Chat (full screen)</a> · <a href="#dchat">💬 Desk chat</a>\';h.after(j)};tnOpen("DESK",document.getElementById("desk-adv-w"))}})</script>'
        '<div style="margin-top:6px"><b>🤖 Trade notes → Grok / Claude</b> <i>(advisory only, never blocks)</i> <button onclick="tnList()">Show trades</button><div id="tn-list"></div></div>'
        '<script>let TN={};async function tnList(){const b=document.getElementById("tn-list");b.textContent="loading...";const j=await (await fetch("/api/opps",{cache:"no-store"})).json();'
        'const L=(Array.isArray(j)?j:(j.opps||[])).filter(o=>/open|armed|queued/.test(o.kind||""));b.innerHTML="";L.forEach(o=>{const id=[o.seat,o.sym,o.side].join("|");TN[id]=o;const d=document.createElement("div");d.className="fmr";'
        'const h=document.createElement("div");h.style.cursor="pointer";h.textContent="▸ "+(o.kind||"")+" · "+(o.desk_name||o.seat)+" · "+o.sym+" "+o.side;h.onclick=()=>tnOpen(id,d);d.appendChild(h);b.appendChild(d)});if(!L.length)b.textContent="no open/armed/queued trades"}'
        'function tnE(s){const x=document.createElement("span");x.textContent=s;return x.innerHTML}'
        'async function tnOpen(id,d){let w=d.querySelector(".tnw");if(w){w.remove();return}w=document.createElement("div");w.className="tnw";w.innerHTML=["grok","claude"].map(a=>`<div><b>${a=="grok"?"Grok":"Claude"}</b><textarea rows="3" data-a="${a}"></textarea><button data-s="${a}">Send to ${a=="grok"?"Grok":"Claude"}</button></div>`).join("")+"<div class=tnt>loading thread...</div>";d.appendChild(w);'
        'w.querySelectorAll("button[data-s]").forEach(bt=>{if(id!="DESK"||w.querySelector(`select[data-r=${bt.dataset.s}]`))return;const s=document.createElement("select");s.dataset.r=bt.dataset.s;s.title="Send this advisor reply to:";s.innerHTML=`<option value="">none (advisory only)</option><option value="ALL">ALL traders</option><option value="head_of_desk">admin: Marcus (head)</option><option value="head_trader">admin: HT - Head Trader (oversight, all AIs)</option><option value="macro">trader: Theo (macro)</option><option value="energy">trader: Rex (energy)</option><option value="news_filings">trader: Quinn (news)</option><option value="tech_ai_infra">trader: Nico (tech/AI)</option><option value="trend">trader: Dex (trend)</option><option value="banks">trader: Victor (banks)</option><option value="crypto">trader: Kai (crypto)</option><option value="essentials_utilities">trader: Nora (essentials)</option><option value="fx_commodities">trader: Lena (FX/commod)</option><option value="healthcare">trader: Iris (health)</option><option value="price_action">trader: price_action seat</option><option value="blind_challenger">admin: Sloane (blind challenger)</option><option value="devils_advocate">admin: Devil’s advocate (admin)</option><option value="fixer">admin: FIXER - health/repair (Claude, admin)</option>`;const lb=document.createElement("span");lb.textContent=" send reply to → ";bt.parentNode.insertBefore(lb,bt);bt.parentNode.insertBefore(s,bt)});w.querySelectorAll("button[data-s]").forEach(bt=>bt.onclick=async()=>{const a=bt.dataset.s,ta=w.querySelector(`textarea[data-a=${a}]`),rs=w.querySelector(`select[data-r=${a}]`);const r=await fetch("/api/advisor_note",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({trade:id,advisor:a,text:ta.value,ctx:TN[id],route:rs?rs.value:""})});const j=await r.json();if(r.ok){ta.value="";tnLoad(id,w)}else alert("error: "+(j.error||r.status))});tnLoad(id,w)}'
        'async function tnLoad(id,w){const t=await (await fetch("/api/advisor_thread?trade="+encodeURIComponent(id),{cache:"no-store"})).json();w.querySelector(".tnt").innerHTML=t.length?t.map(r=>`<div class=fmr><b>${tnE(r.ts_wita)}</b> ${tnE(r.role=="brett"?"Brett → "+r.advisor:r.role.toUpperCase()+" reply")}<br>${tnE(r.text)}</div>`).join(""):"<i>no notes yet - replies appear here (newest first)</i>"}</script>'
        '</section>') % (S["unacked"], opts, opts, rows)
# ---- tfo_advnotes_20261102: per-trade notes to Grok / Claude (ADVISORY ONLY - never read by book/gate) ----
ADV = L + "/state/advisor_msgs.jsonl"; ADVLK = ADV + ".lock"
class _ALock(_Lock):
    def __enter__(s): s.f = open(ADVLK, "a"); fcntl.flock(s.f, fcntl.LOCK_EX if s.ex else fcntl.LOCK_SH); return s
def _aread():
    try: return [json.loads(l) for l in open(ADV) if l.strip()]
    except FileNotFoundError: return []
def adv_add(trade, advisor, text, role="brett", reply_to=None, ctx=None, route=None, images=None, command=None):
    trade = str(trade or "").strip()[:120]; advisor = str(advisor or "").lower(); text = str(text or "").strip()
    if role=="brett" and len(text)>8000:raise ValueError("Message is longer than 8,000 characters; split it into smaller requests. Nothing was queued.")
    if role!="brett":text=text[:2000]
    if command is not None:
        if role!="brett" or not isinstance(command,dict) or command.get("mode") not in ("ask","push","learn"):raise ValueError("unknown advisor command mode")
        if len(json.dumps(command))>6000:raise ValueError("command too large")
    imgs = img_validate(images) if role == "brett" else []
    if str(advisor) == "supergrok": raise ValueError("SuperGrok is not connected")
    if not trade or advisor not in ("grok", "claude", "chatgpt") or (not text and not imgs): raise ValueError("need trade, advisor grok|claude|chatgpt, text or images")
    n = dt.datetime.now(dt.timezone.utc)
    r = dict(id="an-" + n.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2), ts_utc=n.isoformat(timespec="seconds"),
             ts_wita=n.astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), trade_id=trade, advisor=advisor, role=role, text=text,
             reply_to=reply_to, advisory_only=True)
    if command is not None:r["command"]=command
    if imgs: r["images"] = imgs   # chatimg_20261102
    if route and role == "brett" and (route == "ALL" or route in SEAT_IDS): r["route"] = route   # feeds_20261102: msgdesk forwards the advisor reply as an idea
    if ctx and role == "brett": r["ctx"] = {k: ctx.get(k) for k in ("seat", "sym", "side", "kind", "entry", "stop", "target", "grade") if isinstance(ctx, dict)}
    with _ALock(True):
        if role!="brett" and reply_to:
            existing=next((row for row in _aread() if row.get("reply_to")==reply_to and row.get("role")==role and row.get("advisor")==advisor),None)
            if existing:return existing
        with open(ADV, "a") as f: f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
    return r
def adv_thread(trade):
    with _ALock(False): rows = [r for r in _aread() if r.get("trade_id") == trade]
    return rows[::-1]   # newest first
def adv_pending():
    with _ALock(False): rows = _aread()
    done = {r.get("reply_to") for r in rows if r.get("role") != "brett"}
    return [r for r in rows if r.get("role") == "brett" and r["id"] not in done]
if __name__ == "__main__":
    a = sys.argv[1:] or ["list"]
    if a[0] == "tick": d = tick(); print("funder_msgs unacked=%d" % len(d["unacked"]))
    elif a[0] == "unacked": print(json.dumps(unacked(a[1] if len(a) > 1 else None), indent=1))
    elif a[0] == "ack": print(json.dumps(ack(a[1], a[2])))
    else: print(json.dumps(load(), indent=1))
