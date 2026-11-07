"""seatmail_20261102 + seatagents_20261103 (PAPER, code only): seat-to-seat / seat<->Marcus / seat<->Head Trader messages with loop + rate limits.
A seat addresses colleagues with "to_seats": {"<seat id>|all|marcus|head_trader": "msg"} in its JSON answer (chat AND engine rounds) or @seat in its reply.
Delivery: (1) msgdesk/desk post "FROM SEAT a to b [hop n]: ..." to b's thread so b's own brain answers within ~1-5 min, and b's answer is
returned to a's mailbox; (2) every message also lands in state/thf/seat_mail/<b>.jsonl, which desk.py shows to b on its next engine round
and msgdesk shows in b's chat prompt (separate read cursors); (3) a round-time message also WAKES b (push row in state/thf/hunt_requests.jsonl,
honoured by the vd_engine rotation). "all" = mailbox broadcast to every seat (no brain wake-ups: they read it on their next round/chat).
Guardrails (Head Trader 21:10 WITA 2 Nov): max hop 2 (a->b->c, no further relays), same pair once per 15 min, a sender 6/hour (Marcus and
Head Trader 20/hour), whole desk 30/hour, no repeats (same text to the same colleague within 6 h), 600 chars. seatagents_20261103 16:35:
at most 2 messages per engine round; a seat ANSWERING a colleague may forward to 1 more seat (hubs 3), never 'all'; answers to a hub
fan-out (Marcus roll-call / HT ask: one rate-counted send) are reply-only; a round that answers a colleague's message is a mailbox REPLY
(no chat brain call, no wake), so two seats' rounds can never ping-pong.
REPORTING CHAIN (Brett 21:45 WITA 2 Nov): formal reports ("report_to_marcus") go seat -> Marcus (kind=report, no rate count); Marcus -> Head Trader;
Head Trader -> Brett. Seats can still DM each other. Roster is data-driven (seat_registry: every veteran_desk/seats/*.json)."""
import fcntl, hashlib, json, math, os, re, secrets, time, datetime as dt
L = "/opt/tfo/live/thf"; D = L + "/state/thf/seat_mail"; RL = D + "/_rate.json"; LK = D + "/.lock"
HUNT = L + "/state/thf/hunt_requests.jsonl"; THREAD = L + "/state/thf/funder_thread.jsonl"
MARCUS = "head_of_desk"; HT = "head_trader"
try:
    from veteran_desk import seat_registry as SR
    IDS = SR.mail_ids()
except Exception:   # pragma: no cover - registry unreadable: legacy roster
    SR = None
    IDS = ["head_of_desk", "blind_challenger", "devils_advocate", "banks", "crypto", "energy", "essentials_utilities", "fx_commodities",
           "healthcare", "macro", "news_filings", "price_action", "tech_ai_infra", "trend", "cfo", "software_engineer", "head_trader"]
MAX_HOP = 2; PAIR_S = 900; SENDER_H = 6; DESK_H = 30; MAXC = 600; DEDUPE_S = 6 * 3600   # seatagents_20261103 16:35: desk 60 -> 30/h (seat chatter only gets the LOW Claude tier, ~2 slots = ~20-30 answers/h)
SENDER_LIMIT = {MARCUS: 20, HT: 20}   # hub seats talk more (Head Trader 21:10 WITA 2 Nov)
REPORT_KINDS = ("report", "rollup", "eod", "problem")
# seatagents_20261103 16:35 (master: "one roll-call should fan out once and replies should not trigger further broadcasts", 15:47-16:12 flood):
ROUND_MAX = 2                              # at most 2 colleague messages per engine round (6/h per seat still applies)
REPLY_FWD = {MARCUS: 3, HT: 3}; REPLY_FWD_DEFAULT = 1   # a seat ANSWERING a colleague may forward to at most 1 other seat (hubs 3), never 'all'
FANOUT_RX = re.compile(r"\bROLL-CALL\b|\bREPLY ONLY\b|\bHT ASK\b", re.I)   # answers to a hub fan-out (roll-call / HT ask) are reply-only: no new messages
def reply_forwards(seat, text):
    """How many NEW colleague messages a seat may send while it is answering `text` (a FROM SEAT message): 0 for a roll-call / HT ask fan-out."""
    return 0 if FANOUT_RX.search(str(text or "")) else int(REPLY_FWD.get(seat, REPLY_FWD_DEFAULT))
FROM_RX = re.compile(r"FROM SEAT (\w+) to (\w+)(?: \[hop (\d)\])?")
WITA = dt.timezone(dt.timedelta(hours=8))
def _now(): return dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")
def policy_limits():
    """Bounded internal message budget; old installations keep legacy limits.
    Hop, duplicate, per-round and reply-only loop guards are never configurable.
    """
    out = dict(pair_s=PAIR_S, sender_h=SENDER_H, hub_h=dict(SENDER_LIMIT), desk_h=DESK_H)
    try:
        with open(os.path.join(D, "policy.json")) as stream: cfg = json.load(stream)
        if cfg.get("version") != 1 or cfg.get("enabled") is not True or cfg.get("owner_authorized") is not True: return out
        def bounded(value, lo, hi, default):
            return value if type(value) is int and lo <= value <= hi else default
        out["pair_s"] = bounded(cfg.get("pair_s"), 60, PAIR_S, PAIR_S)
        out["sender_h"] = bounded(cfg.get("sender_h"), SENDER_H, 12, SENDER_H)
        out["desk_h"] = bounded(cfg.get("desk_h"), DESK_H, 240, DESK_H)
        for seat, value in (cfg.get("sender_overrides") or {}).items():
            if seat in IDS:
                out["hub_h"][seat] = bounded(value, out["hub_h"].get(seat, out["sender_h"]), 40, out["hub_h"].get(seat, out["sender_h"]))
    except (OSError, ValueError, TypeError, AttributeError): pass
    return out
def sender_limit(frm):
    limits = policy_limits()
    return int(limits["hub_h"].get(frm, limits["sender_h"]))
def limits_text():
    p = policy_limits()
    return ("%d/h per seat; sender overrides %s/h; desk %d/h; 1 per colleague per %d sec; no repeats for 6h; max 2 hops; max 2 sends per round; fan-out answers reply-only" %
            (p["sender_h"], json.dumps(p["hub_h"], sort_keys=True), p["desk_h"], p["pair_s"]))
def aliases():
    if SR is not None:
        try: return SR.aliases()
        except Exception: pass
    A = {s: s for s in IDS}; A.update({"marcus": "head_of_desk", "head": "head_of_desk", "hod": "head_of_desk", "ht": "head_trader", "news": "news_filings",
         "tech": "tech_ai_infra", "essentials": "essentials_utilities", "fx": "fx_commodities", "pa": "price_action", "all": "all", "everyone": "all", "desk": "all"})
    for s in IDS:
        try:
            nm = (json.load(open(L + "/veteran_desk/seats/%s.json" % s)).get("trader_name") or "").split()
            if nm: A.setdefault(nm[0].lower(), s)
        except Exception: pass
    return A
MENTION = re.compile(r"@([A-Za-z_]+)")
def targets(frm, d, text=None):
    """{to_seat|'all': body} from a seat answer: explicit to_seats dict + @mentions in reply (body = the reply). text='' = no mention parsing."""
    A = aliases(); out = {}
    ts = d.get("to_seats") if isinstance(d, dict) else None
    if isinstance(ts, dict):
        for k, v in ts.items():
            s = A.get(str(k).lstrip("@").lower().replace("-", "_").replace(" ", "_"))
            if s and s != frm and str(v).strip(): out[s] = str(v).strip()
    rep = text if text is not None else (d.get("reply") if isinstance(d, dict) else "") or ""
    for m in MENTION.finditer(rep):
        s = A.get(m.group(1).lower())
        if s and s != frm and s != "all" and s not in out: out[s] = rep.strip()
    return out
def _lock():
    os.makedirs(D, exist_ok=True); f = open(LK, "a"); fcntl.flock(f, fcntl.LOCK_EX); return f
def _h(text): return hashlib.sha1(re.sub(r"\s+", " ", str(text or "").strip().lower()).encode()).hexdigest()[:12]
def allowed(frm, to, hop, R, t, text=None, pair=True):
    limits = policy_limits(); sender_h = int(limits["hub_h"].get(frm, limits["sender_h"]))
    if hop > MAX_HOP: return False, "hop %d > %d" % (hop, MAX_HOP)
    if frm not in IDS: return False, "bad sender"
    if to != "all" and (to not in IDS or frm == to): return False, "bad target"
    if pair and to != "all" and t - R.get("pair", {}).get("%s>%s" % (frm, to), 0) < limits["pair_s"]: return False, "pair limit (1 per %d min)" % (limits["pair_s"] // 60)
    if text is not None and t - R.get("dup", {}).get("%s>%s:%s" % (frm, to, _h(text)), 0) < DEDUPE_S: return False, "duplicate (same message to %s within %dh)" % (to, DEDUPE_S // 3600)
    if sum(1 for x in R.get("sent", []) if x[0] == frm and t - x[1] < 3600) >= sender_h: return False, "sender limit %d/h" % sender_h
    if {frm, to} != {MARCUS, HT} and sum(1 for x in R.get("sent", []) if t - x[1] < 3600) >= limits["desk_h"]: return False, "desk limit %d/h" % limits["desk_h"]   # seatagents_20261103 17:02: the hub link Marcus<->Head Trader (the reporting chain) is never blocked by the desk-wide chatter cap (per-sender limits still apply)
    return True, "ok"
def _load_rate():
    try: return json.load(open(RL))
    except Exception: return {}
def _save_rate(R, t):
    R["sent"] = [x for x in R.get("sent", []) if t - x[1] < 3600]
    R["dup"] = {k: v for k, v in (R.get("dup") or {}).items() if t - v < DEDUPE_S}
    tmp = RL + ".tmp"; json.dump(R, open(tmp, "w")); os.replace(tmp, RL)
def send(frm, to, text, hop=1, kind="msg", t=None, pair=True):
    """Rate-limited send (counts toward limits; 'all' = mailbox broadcast). Returns (ok, why). pair=False: no 15-min pair limit (relays of Brett's own words)."""
    t = t or time.time()
    if SR is not None and to not in IDS and to != "all": to = SR.resolve(to) or to
    lk = _lock()
    try:
        R = _load_rate(); ok, why = allowed(frm, to, hop, R, t, text, pair)
        if not ok: return ok, why
        R.setdefault("pair", {})["%s>%s" % (frm, to)] = t; R.setdefault("dup", {})["%s>%s:%s" % (frm, to, _h(text))] = t
        R["sent"] = R.get("sent", []) + [[frm, t]]; _save_rate(R, t)
        if to == "all":
            n = 0
            for s in IDS:
                if s != frm: _append(frm, s, text, hop, "broadcast" if kind == "msg" else kind, t); n += 1
            return True, "broadcast to %d seats (mailbox; they read it on their next round/chat)" % n
        _append(frm, to, text, hop, kind, t); return True, "ok"
    finally: lk.close()
def broadcast(frm, text, hop=1, kind="broadcast", t=None): return send(frm, "all", text, hop, kind, t)
def fanout(frm, seats, text, hop=1, kind="rollcall", t=None):
    """seatagents_20261103: a hub fan-out (Marcus roll-call / HT ask) = ONE rate-counted send (like 'all': no pair limit, dedupe on the text),
    then the text lands in each listed seat's mailbox. Returns (ok, why, [seats delivered]). The caller posts the chat DMs (paced)."""
    t = t or time.time(); seats = [s for s in dict.fromkeys(seats) if s in IDS and s != frm]
    lk = _lock()
    try:
        R = _load_rate(); ok, why = allowed(frm, "all", hop, R, t, text)
        if not ok: return False, why, []
        R.setdefault("dup", {})["%s>%s:%s" % (frm, "all", _h(text))] = t; R["sent"] = R.get("sent", []) + [[frm, t]]; _save_rate(R, t)
        for s in seats: _append(frm, s, text, hop, kind, t)
        return True, "fan-out to %d seats (one send)" % len(seats), seats
    finally: lk.close()
def deliver(frm, to, text, hop=1, kind="reply", **extra):
    """Answer / formal report to a seat: mailbox only (no new thread post -> cannot loop), not rate counted. extra: re=<message id it answers>."""
    if to not in IDS or frm == to: return False
    lk = _lock()
    try: _append(frm, to, text, hop, kind, time.time(), **extra); return True
    finally: lk.close()
def _append(frm, to, text, hop, kind, t, **extra):
    with open(D + "/%s.jsonl" % to, "a") as f:
        f.write(json.dumps(dict(id="m-" + secrets.token_hex(12), ts=t, ts_wita=_now(), frm=frm, to=to, hop=hop, kind=kind, text=str(text)[:MAXC], **{k: v for k, v in extra.items() if v is not None})) + "\n")
def asked_me(seat, frm, within_s=6 * 3600, now=None):
    """The newest message `frm` sent to `seat` (msg/broadcast/rollcall) within within_s, else None. Used to turn a round-time to_seats into a REPLY."""
    now = now or time.time()
    rows = [r for r in _rows(seat) if r.get("frm") == frm and r.get("kind") in ("msg", "broadcast", "rollcall") and now - float(r.get("ts") or 0) < within_s]
    return rows[-1] if rows else None
def _rows(seat):
    # Keep one logical record per nonblank line so quarantine never shifts ACK cursors.
    # Raw evidence stays in the original log; reads never repair or rewrite a mailbox.
    rows = []
    try:
        with open(D + "/%s.jsonl" % seat, "rb") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip(): continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict): raise ValueError("object required")
                    if not isinstance(row.get("id"), str) or not row["id"]: raise ValueError("message ID required")
                    stamp = row.get("ts")
                    if type(stamp) not in (int, float) or not math.isfinite(stamp):
                        raise ValueError("finite timestamp required")
                except (ValueError, TypeError, UnicodeDecodeError):
                    row = dict(id="invalid-%s-%s" % (number, hashlib.sha256(line).hexdigest()[:16]),
                               ts=0, kind="quarantined", text="", _quarantined=True, source_line=number)
                rows.append(row)
    except FileNotFoundError: pass
    return rows

def mailbox_health():
    """Current read-only quarantine counts; raw record content is never exposed."""
    lk = _lock()
    try:
        counts = {}; details = []
        for seat in IDS:
            bad = [row for row in _rows(seat) if row.get("_quarantined")]
            if bad: counts[seat] = len(bad)
            for row in bad[:3]:
                if len(details) < 12: details.append(dict(seat=seat, line=row["source_line"], id=row["id"]))
        return dict(status="needs_review" if counts else "ok", quarantined_records=sum(counts.values()),
                    by_seat=counts, records=details, note="Unreadable records remain in their original mailbox logs; valid mail continues.")
    finally: lk.close()

def _cursor(seat, consumer, size):
    cp = D + "/%s.%s.cursor" % (seat, consumer)
    try:
        with open(cp) as stream: cur = max(0, min(size, int(stream.read().strip() or 0)))
    except (OSError, ValueError): cur = 0
    return cp, cur

def _write_cursor(cp, cur):
    tmp = cp + ".%s.%s.tmp" % (os.getpid(), secrets.token_hex(3))
    with open(tmp, "w") as stream: stream.write(str(cur))
    os.replace(tmp, cp)

def ack(seat, ids, consumer="desk", max_age=86400):
    """Commit only a successfully processed, contiguous inbox batch; never skip unseen mail or rewind a cursor."""
    done = set(ids or []); lk = _lock()
    try:
        rows = _rows(seat); cp, cur = _cursor(seat, consumer, len(rows)); start = cur; now = time.time()
        while cur < len(rows):
            row = rows[cur]
            if now - float(row.get("ts") or 0) < max_age and row.get("id") not in done: break
            cur += 1
        if cur != start: _write_cursor(cp, cur)
        return cur - start
    finally: lk.close()

def inbox(seat, consumer="desk", n=6, mark=True, max_age=86400):
    """Oldest unread batch. Prompts peek with mark=False and acknowledge only after a successful answer."""
    lk = _lock()
    try:
        rows = _rows(seat); cp, cur = _cursor(seat, consumer, len(rows)); now = time.time()
        batch = [(i, row) for i, row in enumerate(rows[cur:], cur) if now - float(row.get("ts") or 0) < max_age][:max(0, n)]
        if mark and batch: _write_cursor(cp, batch[-1][0] + 1)
        return [{k: row.get(k) for k in ("id", "ts", "ts_wita", "frm", "to", "kind", "hop", "text", "re", "root", "reply_to")} for _, row in batch]
    finally: lk.close()
def read_since(seat, since_ts, frm=None, kinds=None):
    """Collector view (no cursor): rows for seat newer than since_ts, optionally from one sender / of given kinds."""
    return [r for r in _rows(seat) if r.get("ts", 0) > since_ts and (frm is None or r.get("frm") == frm) and (kinds is None or r.get("kind") in kinds)]
def name(seat):
    try: return SR.name(seat) if SR is not None else seat
    except Exception: return seat
def report_target(seat):
    """Formal reports go UP the chain: seat -> Marcus -> Head Trader -> Brett."""
    try: return SR.report_target(seat) if SR is not None else (HT if seat == MARCUS else "brett" if seat == HT else MARCUS)
    except Exception: return MARCUS
def wake(seat, why="seat_mail"):
    """Wake a trader seat next engine tick: push row in hunt_requests.jsonl (vd_engine rotation bypasses the idle wait for pushed seats)."""
    try:
        if SR is not None and seat not in SR.trade_ids(): return False   # admin/staff seats have no rotation slot
        with open(HUNT, "a") as f: f.write(json.dumps(dict(ts_wita=_now(), seat=seat, msg=why, push=True, via="seat_mail")) + "\n")
        return True
    except Exception: return False
def post_thread(frm, to, text, hop=1, kind="seat_mail", mode="round", root=None, **extra):
    """Dashboard row 'From X -> To Y' (same file/format as thf_msgdesk.tadd) for mail that does not go through the funder chat."""
    r = dict(root=root or "sm-%s-%s" % (dt.datetime.now(WITA).strftime("%Y%m%d%H%M%S"), secrets.token_hex(2)), ts_wita=_now(), text=str(text or "")[:2000], mode=mode, kind=kind, hop=hop, via="seat_mail", **extra)
    r["from"] = frm; r["to"] = to
    try:
        with open(THREAD, "a") as f: f.write(json.dumps(r) + "\n")
    except Exception: pass
    return r
def chat_post(frm, to, text, hop=1):
    """Post 'FROM SEAT a to b [hop n]: ...' into b's funder chat: b's own brain answers and the answer comes back to a's mailbox (msgdesk)."""
    from veteran_desk import thf_funder as TF
    return TF.post("seat", "FROM SEAT %s to %s [hop %d]: %s" % (frm, to, hop, str(text)[:900]), to)
def deliver_round(seat, j, poster=None, waker=None, thread=None, t=None):
    """ROUND-TIME delivery (desk.run after a seat's JSON): to_seats -> seat_mail.send + chat post (recipient's brain answers) + wake;
    'all' -> mailbox broadcast + one thread row; report_to_marcus -> Marcus mailbox (kind=report) + thread row. Returns [dict(to, ok, why)]."""
    out = []
    if not isinstance(j, dict): return out
    poster = poster or chat_post; waker = waker or wake; thread = thread or post_thread
    for i, (to, body) in enumerate(targets(seat, j, text="").items()):
        body = str(body)[:MAXC]
        if i >= ROUND_MAX: out.append(dict(to=to, ok=False, why="round limit %d messages" % ROUND_MAX)); continue   # seatagents_20261103 16:35
        q = asked_me(seat, to, now=t) if to != "all" else None
        if q is not None:   # seatagents_20261103 16:35: answering a colleague who messaged me = a REPLY (mailbox + one row), no chat brain call, no wake -> no round-to-round ping-pong
            ok = deliver(seat, to, body, hop=min(MAX_HOP, int(q.get("hop") or 1) + 1), kind="reply", re=q.get("id"))
            if ok:
                try: thread(seat, to, body, min(MAX_HOP, int(q.get("hop") or 1) + 1), "seat_mail_reply", "round")
                except Exception: pass
            out.append(dict(to=to, ok=bool(ok), why="reply to %s's %s message (mailbox, no wake)" % (to, str(q.get("ts_wita") or "")[11:16]))); continue
        if to == "all":
            ok, why = send(seat, "all", body, hop=1, kind="broadcast", t=t)
            if ok:
                try: thread(seat, "all", body, 1, "broadcast", "round")
                except Exception: pass
            out.append(dict(to="all", ok=ok, why=why)); continue
        ok, why = send(seat, to, body, hop=1, kind="msg", t=t)
        if ok:
            try: poster(seat, to, body, 1)
            except Exception as x: why = "mailbox only (chat post failed: %s)" % type(x).__name__
            try: waker(to, "seat_mail from %s" % seat)
            except Exception: pass
        out.append(dict(to=to, ok=ok, why=why))
    rep = str(j.get("report_to_marcus") or "").strip()[:MAXC]
    if rep and seat != MARCUS:
        tgt = report_target(seat); deliver(seat, tgt, rep, hop=1, kind="report")
        try: thread(seat, tgt, rep, 1, "report", "round")
        except Exception: pass
        out.append(dict(to=tgt, ok=True, why="report"))
    return out
def prompt_lines(seat, consumer="desk", n=6, ids=True):
    """Compact inbox + send_to instructions for a prompt (<= ~900 chars)."""
    ib = inbox(seat, consumer, n)
    s = ("YOUR INBOX (%d unread from desk colleagues; answer them with to_seats):\n" % len(ib) + "".join("- %s from %s [%s]: %s\n" % (r.get("ts_wita", "")[5:16], name(r.get("frm")), r.get("kind"), str(r.get("text"))[:220]) for r in ib)) if ib else "YOUR INBOX: empty.\n"
    return s[:900]
def status():
    R = _load_rate(); t = time.time()
    return dict(ids=IDS, mailbox_health=mailbox_health(), sent_last_hour={s: sum(1 for x in R.get("sent", []) if x[0] == s and t - x[1] < 3600) for s in IDS if any(x[0] == s for x in R.get("sent", []))},
                desk_last_hour=sum(1 for x in R.get("sent", []) if t - x[1] < 3600),
                limits=dict(policy_limits(), max_hop=MAX_HOP, dedupe_h=DEDUPE_S // 3600, round_max=ROUND_MAX,
                            reply_forwards=dict(REPLY_FWD, default=REPLY_FWD_DEFAULT, fanout_answers=0)))
if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "send": print(send(a[1], a[2], " ".join(a[3:])))
    elif a and a[0] == "inbox": print(json.dumps(inbox(a[1], a[2] if len(a) > 2 else "cli", 20, mark=False), indent=1))
    else: print(json.dumps(status(), indent=1))
