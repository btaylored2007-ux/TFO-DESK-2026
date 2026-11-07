"""dasheli10_20261103 (Brett 2 Nov 20:43 WITA HARD; checklist A19/B10/C9/A65, master list 10b/10c; PAPER): the ELI10 dashboard.
Pure builders over the dash state dict (what /api/state returns) + seat heartbeat + AI status -> plain-English rows for the
phone-first tabs Home / Trades / Seats / Health (the Chat tab is the existing tools/thf_chat.js renderer).
Served by the dashboard's direct routes using the accepted module tree:
  GET /  /desk/phone  /desk/m  /desk/<seat>      -> page_html(S, path)  (tools/thf_eli10.html + js inlined; Home JSON inlined
                                                   so the first paint needs ONE request)
  GET /api/home                                  -> home(S, ...)        (< 10 KB)
  GET /api/eli10?tab=home|trades|seats|health|all -> eli10(S, tab)
  GET /classic                                   -> the old card page (every old link keeps working)
READ ONLY: never writes state. Every row is built inside a guard so one bad field shows as '—', never a blank tab.
Words rule (ELI10): no code word without a plain explanation next to it - see GLOSSARY and the *_words helpers."""
import datetime as dt, json, math, os, re, time
L = "/opt/tfo/live/thf"  # Authoritative runtime data; never use this path to serve code assets.
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDS_JSON = os.path.join(L, "state", "thf", "feed_status.json")   # dasheli10_20261103 feed keeper output (every paid/free feed, one row each)
TOOLS = os.path.join(CODE_ROOT, "tools")  # Accepted code assets follow this module, including in the dashboard mirror.
HB = os.path.join(L, "state", "thf", "seat_heartbeat.json")
NEWS_HB = os.path.join(L, "state", "catalyst_stream", "heartbeat.json")
NEWS_JSON = os.path.join(L, "state", "veteran_desk", "news.json")
WITA = dt.timezone(dt.timedelta(hours=8))
TAG = "dasheli10_20261103"
TRADER_SEATS = ["crypto", "trend", "price_action", "essentials_utilities", "macro", "energy", "banks", "healthcare", "news_filings", "tech_ai_infra", "fx_commodities"]
ADMIN_SEATS = ["head_of_desk", "head_trader", "blind_challenger", "devils_advocate", "software_engineer", "cfo", "fixer"]
DESK = dict(banks="Banks", crypto="Crypto", energy="Energy", essentials_utilities="Essentials", fx_commodities="FX", healthcare="Healthcare",
            macro="Macro", news_filings="News", price_action="Price action", tech_ai_infra="Tech / AI", trend="Trend",
            head_of_desk="Head of desk", head_trader="oversight of all AIs", blind_challenger="Blind challenger", devils_advocate="Devil's advocate",
            software_engineer="Software engineer", cfo="CFO", fixer="Repairs")
NAMES = dict(banks="Victor Crane", crypto="Kai Navarro", energy="Rex Calder", essentials_utilities="Nora Whitlock", fx_commodities="Lena Voss",
             healthcare="Iris Mendel", macro="Theo Ashby", news_filings="Quinn Mercer", price_action="Dex Romano", tech_ai_infra="Nico Hart", trend="Dex Romano",
             head_of_desk="Marcus Hale", head_trader="Head Trader", blind_challenger="Sloane Pike", devils_advocate="Jonah Grieve",
             software_engineer="Simon", cfo="Helen Park", fixer="Fixer")
GRADE = {"A+": "A+ grade = the best kind of idea", "A": "A grade = a very good idea", "B": "B grade = a decent idea", "C": "C grade = a weak idea"}
GLOSSARY = {
    "PAPER": "pretend money only - nothing here is a real trade",
    "long": "betting the price goes UP (we buy first, sell later)",
    "short": "betting the price goes DOWN (we borrow and sell first, buy back later)",
    "stop": "the price where we give up and get out, so one bad trade stays small",
    "target": "the price where we take the win",
    "R": "one R = the money we would lose if the stop is hit; +2R means we won twice that",
    "grade": "how good an idea looks: A+ best, then A, B, C",
    "armed": "an order is placed and waiting for the market to fill it (if its market is closed it simply waits for the open; it is still ONE order)",
    "lined up for the open": "an A/A+ idea whose market is closed - NOT an order yet; near the open the engine may wake the trader to decide",   # dasheli10_20261103 (HT 16:20: '0 armed / 13 queued' vs '3 waiting')
    "wake-up": "the engine waking a trader to look at fresh ideas. 40 a day come from the shared budget (max 6 per seat); Kai's crypto has its own budget; free check-ins every ~30 min while a seat's market is open and pushes from Brett or Marcus are extra",
    "seat": "one trader (an AI persona) with its own A$500,000 paper book",
    "equity": "what the seat's book is worth right now",
    "since start": "profit or loss since the fresh book began on 30 Oct 2026",
    "today": "money made since 06:00 WITA (just after the US market closes) - the same clock as the leaderboard and every trader card, so the traders' 'today' numbers add up to the big number. The book's own day still starts at midnight WITA; the risk rails and the CFO report use that one",   # ht0600_20261103
    "week": "profit or loss since Monday 06:00 WITA",
    "profit light": "green = seat is up since start, red = down, grey = flat or not traded yet (net of fees, own ledger)",
    "borrow list": "the broker's list of shares we are allowed to borrow (needed for a short)",
    "paid marks": "live prices from the paid data feed (Yahoo is the slower free backup)",
    "watchdog": "a small program that restarts the engine if it stops answering",
    "dash check": "a checker that compares every dashboard number with the book file",
    "second look": "another AI reads the idea and says yes/no (advice only, never blocks the trade)",
    "gate": "a safety check an order has to pass before it goes in",
    "cool-down": "a short wait before the same idea can wake a trader again",
    "WITA": "Bali time (UTC+8); every time on this page is WITA",
}
_CACHE = {}

# ---- words ---------------------------------------------------------------------------------------------------------------------
def shown(v):
    """dasheli10_20261103 FIGURES: a dollar amount rounded the way every page shows it (half away from zero, like the classic page's
    Math.round / toLocaleString), as an int. Python's own round-half-even made A$x.50 read A$1 apart on the two pages."""
    try: v = float(v)
    except (TypeError, ValueError): return None
    if not math.isfinite(v): return None
    n = int(math.floor(abs(v) + 0.5)); return -n if v < 0 else n
def shown_sum(rows, key):
    """Sum of a column AS SHOWN (each row rounded to A$1 first), so the rows on screen add up exactly to the total on screen."""
    return sum(shown(r.get(key) or 0) or 0 for r in rows if isinstance(r, dict))
def aud(v, sign=False):
    """A$1,234 (no cents, same rounding on every page - see shown()). sign=True -> +A$1,234 / -A$1,234. None / NaN -> '—'."""
    n = shown(v)
    if n is None: return "—"
    s = "-" if n < 0 else ("+" if sign and n > 0 else "")
    return "%sA$%s" % (s, "{:,}".format(abs(n)))
def px(v):
    """Price with sensible decimals: 50.01 and 51.86 for prices over 10; 6.29, 1.0845, 0.0834 below (up to 4 decimals, never fewer than 2)."""
    try: v = float(v)
    except (TypeError, ValueError): return "—"
    if v != v: return "—"
    s = ("{:,.2f}" if abs(v) >= 10 else "{:,.4f}").format(v)
    while s.endswith("0") and len(s.split(".")[-1]) > 2: s = s[:-1]
    return s
def num(v, d=0):
    try: return ("{:,.%df}" % d).format(float(v))
    except (TypeError, ValueError): return "—"
def _parse(u):
    s = str(u or "").strip()
    if not s: return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?", s)
    if not m: return None
    try:
        if s.endswith("WITA") or ("T" not in s and "+" not in s[10:] and not s.endswith("Z")):
            return dt.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=WITA)
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def when(u, now=None):
    """'today 15:20' / 'yesterday 21:30' / '2 Nov 21:30' (WITA) from a UTC iso or a 'YYYY-MM-DD HH:MM WITA' string."""
    t = _parse(u)
    if t is None: return "—"
    now = now or dt.datetime.now(WITA); t = t.astimezone(WITA); d = (now.date() - t.date()).days; hm = t.strftime("%H:%M")
    if d == 0: return "today " + hm
    if d == 1: return "yesterday " + hm
    if d == -1: return "tomorrow " + hm
    return "%d %s %s" % (t.day, t.strftime("%b"), hm)
def ago(u, now=None):
    """'12 seconds ago' / '3 minutes ago' / '2 hours ago' / '3 days ago'."""
    t = _parse(u)
    if t is None: return "never"
    now = now or dt.datetime.now(WITA); s = (now - t.astimezone(WITA)).total_seconds()
    if s < 0: s = 0
    if s < 60: return "%d seconds ago" % s
    if s < 3600: return "%d minute%s ago" % (s // 60, "" if s // 60 == 1 else "s")
    if s < 86400: return "%.1f hours ago" % (s / 3600)
    return "%.1f days ago" % (s / 86400)
def mins_words(m):
    """'0.2 min' -> '12 seconds ago' style for the engine_age_min fields."""
    try: m = float(m)
    except (TypeError, ValueError): return "never"
    s = m * 60
    if s < 60: return "%d seconds ago" % s
    if m < 60: return "%d minute%s ago" % (m, "" if int(m) == 1 else "s")
    if m < 1440: return "%.1f hours ago" % (m / 60)
    return "%.1f days ago" % (m / 1440)
def hours_words(h):
    try: h = float(h)
    except (TypeError, ValueError): return "—"
    if h < 1: return "%d minutes" % (h * 60)
    if h < 48: return "%.0f hours" % h
    return "%.1f days" % (h / 24)
def first_name(seat, trader=None):
    n = str(trader or NAMES.get(seat) or seat or "").split(" (")[0].strip()
    if n.lower() in ("head trader", "fixer"): return n
    return n.split(" ")[0] if n else str(seat)
def who(seat, trader=None):
    """'Victor (Banks)' - first name + desk in plain words."""
    return "%s (%s)" % (first_name(seat, trader), DESK.get(seat, str(seat).replace("_", " ")))
def side_word(side):
    s = str(side or "").upper()
    return "short" if s == "SHORT" else "long" if s == "LONG" else (s.lower() or "?")
def grade_words(g):
    return GRADE.get(str(g or ""), ("%s grade" % g) if g else "no grade yet")
def plain_why(w):
    """Engine reason codes -> plain words."""
    w = str(w or "").strip()
    if not w: return ""
    m = re.match(r"^debounce\s+([\d.]+)h\s*<\s*([\d.]+)h", w)
    if m: return "cool-down: only %s of %s hours since the last wake-up for this idea" % (m.group(1), m.group(2))
    if w.startswith("closed market"): return "its market is closed; the engine looks again about an hour before the open"
    if "earnings in window" in w: return "blocked: the company reports earnings soon (we do not trade into earnings)"
    if w.startswith("wake gate"): return "blocked by a safety check: " + w.split(":", 1)[-1].strip()
    if w.startswith("refunded"): return "the AI round failed, the wake-up budget was given back, no trade impact"
    return "engine says: " + w
def dot_rank(c): return {"red": 0, "amber": 1, "green": 2, "grey": 3, "info": 3}.get(c, 3)

# ---- inputs (files; every reader is injectable for tests) --------------------------------------------------------------------
def _jload(p, d=None):
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return d if d is not None else {}
def read_funder_delivery(S):
    f = S.get("funder_msgs") or {}
    if not f and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            cached=_CACHE.get("funder_delivery")
            if cached and time.time()-cached[0]<15:f=cached[1]
            else:
                from veteran_desk import thf_funder
                f=thf_funder.summary();_CACHE["funder_delivery"]=(time.time(),f)
        except Exception as error:f=dict(error=type(error).__name__)
    return f

def read_hb():
    hb = _jload(HB) or {}
    if os.environ.get("PYTEST_CURRENT_TEST"): return hb
    # Memory is independent of the last model reply. Read it without creating or updating a brain.
    for seat in TRADER_SEATS + ADMIN_SEATS:
        row = hb.setdefault(seat, {})
        try:
            path = os.path.join(L, "state", "thf", "seat_brain", seat, "facts.md")
            with open(path) as f: row["memory_ok"] = any(line.startswith("- ") for line in f)
        except OSError: row["memory_ok"] = False
    return hb
def read_ais():
    try:
        from veteran_desk import thf_chat_home as CH; return CH.ai_status()
    except Exception: return []
def read_tick(S):
    try:
        from veteran_desk import thf_ticker as TK; T = TK.ticker(S); return dict(clocks=T.get("clocks") or [], urgent=kill_fix(T.get("urgent") or []))   # killsw_20261103
    except Exception: return dict(clocks=[], urgent=[])
# ---- killsw_20261103 (Brett 19:37 WITA "SAYS THE KILL SWITCH IS ON please check the HEALTH PAGE AND FIX"; PAPER, display only) ----------------
# 19:36:10-19:36:55 WITA a planned engine restart (touch + rm state/thf/ENGINE_KILL) showed on Health/Home as a RED urgent row
# "KILL SWITCH ON: state/thf/ENGINE_KILL" (thf_ticker reads the dash mirror, which also lags the live tree by 15-45 s). ENGINE_KILL is the engine
# PAUSE switch (the restart lever; while it is on, the 2-min book tick and its stops wait too). The LOSS kill switch is the book's kill_switch
# (8% below the desk's peak halves sizes, 12% closes every trade). Say which one it is, read from the live tree.
BOOK_JSON = os.path.join(L, "state", "thf", "thf_book.json")
EK_RELS = ("state/thf/ENGINE_KILL", "state/vd_engine/ENGINE_KILL", "ENGINE_KILL")
KA_RELS = ("state/thf/KILL_ALERT.json", "state/veteran_desk/KILL_ALERT.json")
EK_PLANNED_S = 300   # a planned restart removes ENGINE_KILL within ~1 min; still on after 5 min -> red (the tick and its stops are paused)
def engine_pause():
    """Live ENGINE_KILL files -> [(rel, seconds on)]; [] = the pause switch is off; None = the live tree could not be read."""
    if "engine_pause" in _INJECT: return _INJECT["engine_pause"]
    if os.environ.get("PYTEST_CURRENT_TEST"): return None
    out = []
    for rel in EK_RELS:
        try: out.append((rel, max(0.0, time.time() - os.path.getmtime(os.path.join(L, rel)))))
        except FileNotFoundError: pass
        except OSError: return None
    return out
def loss_kill():
    """The LOSS kill switch from the THF book: dict(state=OK|HALVED|FLAT|UNKNOWN, dd_pct, alert=<KILL_ALERT file or None>)."""
    B = _live("book_kill", BOOK_JSON) or {}; ks = B.get("kill_switch") or {}
    if "kill_alert" in _INJECT: al = _INJECT["kill_alert"]
    elif os.environ.get("PYTEST_CURRENT_TEST"): al = None
    else: al = next((r for r in KA_RELS if os.path.exists(os.path.join(L, r))), None)
    return dict(state=str(ks.get("state") or "UNKNOWN").upper(), dd_pct=ks.get("dd_pct", B.get("drawdown_pct")), alert=al)
def kill_words(k):
    """'OFF - not tripped (the desk is 0.28% below its peak)' and the HALVED / FLAT / unknown versions."""
    try: dd = float(k.get("dd_pct")); ddw = ("%.2f%% below its peak" % abs(dd)) if dd < 0 else "at its peak"
    except (TypeError, ValueError): ddw = "an unknown distance from its peak"
    st = k.get("state")
    if st == "OK": return "OFF - not tripped (the desk is %s)" % ddw
    if st == "HALVED": return "HALF ON - the desk is %s, so new trade sizes are halved (it closes everything at 12%%)" % ddw
    if st == "FLAT": return "TRIPPED - the desk fell to %s: every trade was closed and new trades are stopped until Brett resets it" % ddw
    return "unknown - the book could not be read"
def kill_fix(urgent):
    """thf_ticker 'KILL SWITCH ON: <ENGINE_KILL path>' -> plain words + dot (amber for a planned restart's first 5 min, red after); dropped once
    the live file is gone (mirror lag); kept raw if the live tree cannot be read. Every other urgent item passes through. Idempotent."""
    out = []; ek = "?"
    for u in urgent or []:
        tx = str(u.get("text") if isinstance(u, dict) else u)
        rel = tx[len("KILL SWITCH ON: "):].strip() if tx.startswith("KILL SWITCH ON: ") else None
        if rel not in EK_RELS: out.append(u); continue
        if ek == "?": ek = engine_pause()
        if ek is None: out.append(u); continue
        on = next((s for r, s in ek if r == rel), None)
        if on is None: continue
        out.append(dict(u if isinstance(u, dict) else {}, k="pause", dot="amber" if on <= EK_PLANNED_S else "red",
                        text="ENGINE PAUSED on purpose (%s, switched on %s): the engine and the 2-minute book tick, stops included, wait until it is removed - a planned engine restart removes it within about a minute. This is NOT the loss kill switch, which is %s." % (rel, mins_words(on / 60.0), kill_words(loss_kill()))))
    return out
def read_opps(S):
    """thf_charts.opps() costs ~0.8s: cached 20s in memory (same as the dash state cache)."""
    c = _CACHE.get("opps")
    if c and time.time() - c[0] < 20: return c[1]
    try:
        from veteran_desk import thf_charts as TCH
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        L_ = TCH.opps(os.path.join(base, ((S or {}).get("today") or {}).get("book_path") or "state/thf/thf_book.json"))
    except Exception: L_ = []
    _CACHE["opps"] = (time.time(), L_); return L_
def read_news():
    hb = _jload(NEWS_HB) or {}; nj = _jload(NEWS_JSON) or {}
    v = hb.get("vendors") or {}
    return dict(utc=hb.get("utc") or hb.get("ts"), ok=sum(1 for x in v.values() if isinstance(x, dict) and x.get("status") == "ok"), n=len(v),
                regime=nj.get("regime") or nj.get("news_regime"), news_utc=nj.get("utc") or nj.get("updated"),
                off=sum(1 for x in v.values() if isinstance(x, dict) and x.get("status") == "disabled"),   # healthdesk_20261103: retired vendors, switched off for good
                refused=[(k, str(x.get("err") or x.get("status"))) for k, x in sorted(v.items()) if isinstance(x, dict) and x.get("status") in ("fail", "backoff")])

# ---- Home --------------------------------------------------------------------------------------------------------------------
def _cards(S): return [c for c in ((S or {}).get("cards") or []) if isinstance(c, dict)]
def _counts(cards):
    o = a = 0
    for c in cards:
        if c.get("seat") == "head_of_desk": continue
        for p in c.get("open_positions") or []:
            if p.get("status") == "open": o += 1
            elif p.get("status") == "armed": a += 1
    return o, a
def markets_words(tick, now=None):
    now = now or dt.datetime.now(WITA); cl = (tick or {}).get("clocks") or []
    op = [c.get("label") for c in cl if c.get("open")]
    nxt = sorted([c for c in cl if not c.get("open") and c.get("next_kind") == "open" and _parse(c.get("next_change_utc"))], key=lambda c: _parse(c.get("next_change_utc")))
    s = ("Open now: " + ", ".join(op) + ".") if op else "No market is open right now."
    if nxt: s += " Next to open: %s at %s." % (nxt[0].get("label"), when(nxt[0].get("next_change_utc"), now).replace("today ", ""))
    return s
BRAIN_FRESH_S = 3600
def _brain_observation(h, now=None):
    now = now or dt.datetime.now(WITA)
    mode = h.get("mode") or h.get("chat_mode")
    candidates = [h.get("last_brain_ok"), h.get("last_round_ok")]
    if not any(candidates) and mode == "brain": candidates += [h.get("last_round_utc"), h.get("last_round"), h.get("last_chat"), h.get("last_seen")]
    times = [t for t in (_parse(x) for x in candidates) if t is not None]
    seen = max(times) if times else None
    age = (now - seen).total_seconds() if seen else None
    return mode, seen, bool(mode == "brain" and age is not None and -120 <= age <= BRAIN_FRESH_S)
def brains_words(hb, now=None):
    rows = {k:v for k,v in (hb or {}).items() if isinstance(v,dict) and (v.get("mode") or v.get("chat_mode") or "memory_ok" in v)}
    on = [k for k,v in rows.items() if _brain_observation(v,now)[2]]
    down = [k for k,v in rows.items() if (v.get("mode") or v.get("chat_mode")) in ("brain_down","code")]
    stale = [k for k,v in rows.items() if (v.get("mode") or v.get("chat_mode")) == "brain" and k not in on]
    missing = [k for k,v in rows.items() if v.get("memory_ok") is False or (k in down and v.get("memory_ok") is not True)]
    if not rows:return dict(txt="No seat has reported its brain yet.",color="grey",on=0,n=0,down=[],stale=[],missing=[])
    txt = "%d of %d seats are thinking with an AI brain (a successful reply in the last 60 minutes)." % (len(on),len(rows))
    if down:txt += " Brain down for AI replies; code fallback: " + ", ".join(first_name(s) for s in down) + "."
    if stale:txt += " Older AI replies are historical: " + ", ".join(first_name(s) for s in stale) + "."
    if missing:txt += " Persistent memory needs verification: " + ", ".join(first_name(s) for s in missing) + "."
    elif down or stale:txt += " Persistent memories remain readable; PAPER execution continues through its existing checks."
    color="red" if missing else "amber" if down else "grey" if stale else "green" if on else "grey"
    return dict(txt=txt,color=color,on=len(on),n=len(rows),down=down,stale=stale,missing=missing)
def doing_words(S, cards, now=None):
    now = now or dt.datetime.now(WITA); W = [w for w in ((S or {}).get("wakes") or []) if isinstance(w, dict)]
    names = {c.get("seat"): c.get("trader") for c in cards}
    run = [w for w in W if w.get("status") == "running"]
    budget = wake_words(S)["short"]
    if run:
        w = run[0]; seats = list(w.get("seats") or []); n = sum(len(v) for v in (w.get("cands") or {}).values() if isinstance(v, list))
        return "Right now %s %s looking at %d fresh idea%s (wake-up started %s). %s" % (names_list([first_name(s, names.get(s)) for s in seats[:4]]) or "a trader", "is" if len(seats) == 1 else "are", n, "" if n == 1 else "s", when(w.get("utc"), now).replace("today ", ""), budget)
    fin = [w for w in W if w.get("status") == "finished"]
    if fin:
        w = fin[0]; seats = list(w.get("seats") or [])
        return "Nobody is mid-think. The last wake-up finished %s (%s looked at ideas). The engine keeps scanning and wakes a trader when a good idea shows up. %s" % (when(w.get("utc"), now), names_list([first_name(s, names.get(s)) for s in seats[:4]]) or "a trader", budget)
    return "The engine is scanning; no trader has been woken yet today. " + budget
def wake_words(S):
    """dasheli10_20261103 FIGURES: 'total' (vd_engine wake_counts.json) counts only wake-ups paid from the shared daily budget (cap 40,
    max 6 per seat); Kai's crypto has its own budget ('crypto'); the per-seat counts ALSO include free rotation check-ins and Brett /
    Marcus pushes. The old words '12 of 40 used (max 6 per seat)' next to 'Kai 14' looked like a broken cap - now the words say which is which."""
    wc = (S or {}).get("wake_counts") or {}; caps = (S or {}).get("caps") or {}; by = {k: v for k, v in (wc.get("seat") or {}).items() if isinstance(v, (int, float))}
    tot, allw, cr = wc.get("total", 0), int(sum(by.values())), wc.get("crypto")
    short = "%s of %s wake-ups used today from the shared budget." % (tot, caps.get("total", "?"))
    long_ = short[:-1] + " (max %s per seat)." % caps.get("per_seat", "?")
    if cr is not None: long_ += " Kai's crypto has its own budget: %s used." % cr
    if allw: long_ += " Counting free check-ins and pushes too, the seats were woken %d times today." % allw
    return dict(short=short, long=long_, total=tot, cap=caps.get("total"), per_seat=caps.get("per_seat"), crypto=cr, all_seat_wakes=allw, by_seat=by)
def armed_waiting_open(S):
    """(seat, ticker) of armed orders whose market is closed right now (gate overlord 'waiting_closed'): still ONE order each."""
    return {(x.get("seat"), str(x.get("ticker"))) for x in (((S or {}).get("gate_overlord") or {}).get("waiting_closed") or []) if isinstance(x, dict)}
_AUTONOMY_METRICS = ("completed_trades", "winning_trades", "losing_trades", "known_books", "provider_count", "parked_requests", "open_circuits")
_AUTONOMY_SUMMARIES = ("learning", "teamwork", "recovery", "trading")


def autonomy_status(now=None):
    """Read the collector's live data without claiming that a missing/stale check is ready."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    waiting = dict(schema_version=1, paper=True, status="amber", available=False, fresh=False,
                   updated_utc=None, age_s=None, headline="Waiting for a current paper-desk check",
                   checked="No current check is available yet.",
                   summary={key: "Waiting for a current report." for key in _AUTONOMY_SUMMARIES},
                   metrics={key: None for key in _AUTONOMY_METRICS}, details={})
    path = os.path.join(L, "state", "thf", "autonomy", "status.json")
    try:
        with open(path, encoding="utf-8") as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            raise ValueError("status report is too large")
        record = json.loads(raw)
        if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
            raise ValueError("status format is not supported")
        stamp = record.get("updated_utc")
        if not isinstance(stamp, str):
            raise ValueError("check time is missing")
        updated = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if updated.tzinfo is None or updated.utcoffset() is None:
            raise ValueError("check time needs a timezone")
        age = (now - updated).total_seconds()
        waiting.update(available=True, updated_utc=stamp, age_s=round(max(age, 0), 1))
        summaries = record.get("summary")
        counts = record.get("metrics")
        if not isinstance(summaries, dict) or not isinstance(counts, dict):
            raise ValueError("check summaries or counts are missing")
        metrics = {}
        for key in _AUTONOMY_METRICS:
            value = counts.get(key)
            metrics[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        waiting["metrics"] = metrics
        if any(value is None for value in metrics.values()):
            raise ValueError("some check counts are missing")
        if metrics["winning_trades"] + metrics["losing_trades"] > metrics["completed_trades"]:
            waiting["metrics"].update(completed_trades=None, winning_trades=None, losing_trades=None)
            raise ValueError("trade counts do not agree")
        if any(not isinstance(summaries.get(key), str) or not summaries[key].strip() for key in _AUTONOMY_SUMMARIES):
            raise ValueError("some check summaries are missing")
        detail = record.get("details") or {}
        detail_text = json.dumps(detail, allow_nan=False, default=str)
        waiting["details"] = detail if len(detail_text) <= 4000 else {"note": "More details are available in the full status view."}
        if age < -30:
            waiting["checked"] = "The check's clock is ahead. Waiting for a current check."
            return waiting
        if age > 180:
            waiting["checked"] = "Last report was %d minutes ago. Waiting for a fresh check." % max(1, int(age // 60))
            return waiting
        status = record.get("status")
        if status not in ("green", "amber", "red"):
            raise ValueError("check state is missing")
        waiting.update(status=status, fresh=True,
                       headline={"green": "Automatic paper-desk checks look healthy",
                                 "amber": "The automatic paper desk needs a look",
                                 "red": "The automatic paper desk needs attention"}[status],
                       checked="Checked %s." % when(updated.isoformat(), now),
                       summary={key: summaries[key].strip()[:300] for key in _AUTONOMY_SUMMARIES})
        return waiting
    except (OSError, ValueError, TypeError, OverflowError) as error:
        waiting["details"] = {"check_note": str(error)[:160]}
        return waiting


def home(S, hb=None, ais=None, tick=None, now=None, news=None):
    """The Home tab (< 10 KB): one-line status, today / since-start money, open + armed, one health dot with a reason, what the desk is doing."""
    now = now or dt.datetime.now(WITA); S = S or {}; T = S.get("today") or {}; cards = _cards(S)
    hb = read_hb() if hb is None else hb; ais = read_ais() if ais is None else ais; tick = read_tick(S) if tick is None else tick
    n_open, n_armed = _counts(cards)
    hr = health_rows(S, hb=hb, ais=ais, tick=tick, now=now, news=news); ov = overall(hr)
    lb = [r for r in ((S.get("leaderboard") or {}).get("rows") or []) if isinstance(r, dict)]
    book_tp = T.get("today_pnl_aud")   # ht0600_20261103 (Brett 16:01 WITA "Figures are not right", DO NOT REVERT): the book's own day starts at MIDNIGHT WITA but the leaderboard + trader cards count from 06:00 WITA (Brett 28 Oct) -> the big number is the leaderboard total (06:00 clock) so the traders add up to it; the midnight figure stays in note_today
    tp = round(sum(float(r.get("day_pnl_aud") or 0) for r in lb), 2) if lb else book_tp; up = (tp or 0) >= 0
    tps = shown_sum(lb, "day_pnl_aud") if lb else tp; up = (tps or 0) >= 0   # dasheli10_20261103 FIGURES: the big number = the leaderboard 'today' column AS SHOWN (each row rounded to A$1, same rule as the classic headline), so the classic page and this page show the same dollar and the rows add up to it exactly
    clock = "since 06:00 WITA" if lb else "since midnight WITA - the leaderboard is not ready yet"
    word = {"green": "All good", "amber": "Mostly fine, %s need%s a look" % ("one thing" if ov.get("n_amber") == 1 else "%d things" % (ov.get("n_amber") or 0), "s" if ov.get("n_amber") == 1 else ""), "red": "Something needs fixing"}[ov["color"]]   # healthdesk_20261103: said 'one thing' with 6 ambers
    status = "%s. %d trade%s open, %d order%s waiting to fill. The paper book is %s %s today (%s)." % (word, n_open, "" if n_open == 1 else "s", n_armed, "" if n_armed == 1 else "s", "up" if up else "down", aud(abs(tps or 0)), clock)
    wo = armed_waiting_open(S); n_wait = sum(1 for c in cards if c.get("seat") != "head_of_desk" for p in c.get("open_positions") or [] if p.get("status") == "armed" and (c.get("seat"), str(p.get("ticker"))) in wo)
    names = {c.get("seat"): c.get("trader") for c in cards}
    srt = sorted(lb, key=lambda r: -(r.get("day_pnl_aud") or 0))
    best = [dict(seat=r.get("seat"), txt="%s %s" % (who(r.get("seat"), names.get(r.get("seat"))), aud(r.get("day_pnl_aud"), sign=True)), aud=r.get("day_pnl_aud")) for r in srt[:3] if (r.get("day_pnl_aud") or 0) > 0.5]
    worst = [dict(seat=r.get("seat"), txt="%s %s" % (who(r.get("seat"), names.get(r.get("seat"))), aud(r.get("day_pnl_aud"), sign=True)), aud=r.get("day_pnl_aud")) for r in srt[::-1][:2] if (r.get("day_pnl_aud") or 0) < -0.5]
    br = brains_words(hb, now)
    return dict(paper=True, tag=TAG, ts=when(now, now), ts_wita=now.strftime("%Y-%m-%d %H:%M:%S WITA"), status=status,
                today_aud=tp, today_shown_aud=tps, today_txt=aud(tps, sign=True), today_words=("up " if up else "down ") + aud(abs(tps or 0)) + " today " + clock.split(" - ")[0], today_clock=clock,
                book_today_txt=aud(book_tp, sign=True), armed_wait_open=n_wait, built_utc=S.get("built_utc"), numbers=numbers_light(S),
                since_aud=T.get("pnl_since_inception_aud"), since_txt=aud(T.get("pnl_since_inception_aud"), sign=True), equity_aud=T.get("equity_aud"), equity_txt=aud(T.get("equity_aud")),
                seed_txt=aud(T.get("seed_aud")), cash_txt=aud(T.get("cash_aud")), book_updated=when(T.get("book_updated"), now), book_day=T.get("day"),
                open=n_open, armed=n_armed, seats=len([c for c in cards if c.get("seat") != "head_of_desk"]),
                health=dict(color=ov["color"], reason=ov["reason"], n_red=ov["n_red"], n_amber=ov["n_amber"]),
                doing=doing_words(S, cards, now), markets=markets_words(tick, now), brains=br, autonomy=autonomy_status(now),
                ais=[dict(name=a.get("name"), state=a.get("state"), word=ai_word(a), color=a.get("color"), note=a.get("note")) for a in (ais or []) if isinstance(a, dict)],
                best=best, worst=worst, urgent=[str(u.get("text") if isinstance(u, dict) else u) for u in kill_fix((tick or {}).get("urgent") or [])][:5],
                wakes=dict(used=(S.get("wake_counts") or {}).get("total", 0), cap=(S.get("caps") or {}).get("total")),
                book_today_aud=book_tp, note_today=GLOSSARY["today"] + (". The book's own midnight-WITA count right now: %s." % aud(book_tp, sign=True) if lb else ""), links=dict(chat="/desk/chat", classic="/classic", hub="/hub", raw="/api/state", vd=(S.get("links") or {}).get("vd_dash"), war_room=(S.get("links") or {}).get("war_room")))
def plain_note(n):
    """Technical AI notes -> plain words (HTTP 504 -> 'its web link did not answer')."""
    n = re.sub(r"\s+", " ", str(n or "")).strip()
    n = re.sub(r"HTTP Error (\d+)[^.;]*", r"its web link did not answer (error \1)", n)
    n = n.replace("Gateway Timeout", "").replace("rc=", "exit code ").replace("brain_down", "brain down")
    return n.strip(" -.")
def names_list(ns):
    ns = [n for n in ns if n]
    return ns[0] if len(ns) == 1 else (", ".join(ns[:-1]) + " and " + ns[-1]) if ns else ""
def ai_word(a):
    st = a.get("state"); note = plain_note(a.get("note"))
    if st == "up": return "working" + ((" (" + str(a.get("model")) + ")") if a.get("model") else "")
    if st == "cooling": return note if note.startswith("busy") else "paused - " + (note or "capacity unavailable; retry pending")
    if st == "down": return "off - " + (note.replace("off - ", "") or "not answering")
    return note or "no recent check"

# ---- Trades ------------------------------------------------------------------------------------------------------------------
def _pnl_words(v, pct=None, R=None):
    try: v = float(v)
    except (TypeError, ValueError): return ("no price yet", "grey")
    col = "green" if v > 0.5 else "red" if v < -0.5 else "grey"
    s = ("Up " if v > 0.5 else "Down " if v < -0.5 else "Flat ") + aud(abs(v)) + " after fees"
    ex = []
    if pct is not None: ex.append("%+.1f%%" % float(pct))
    if R is not None: ex.append("%+.2fR" % float(R))
    return (s + (" (" + ", ".join(ex) + ")" if ex else ""), col)
def open_row(c, p, grade=None, now=None):
    seat = c.get("seat"); w = who(seat, c.get("trader")); side = side_word(p.get("side"))
    pnl, col = _pnl_words(p.get("upnl_net_aud"), p.get("pct"), p.get("R"))
    live = p.get("live_data") or {}
    return dict(kind="open", seat=seat, name=c.get("trader"), who=w, sym=p.get("ticker"), side=side, grade=grade,
                head="%s is %s %s at %s" % (w, side, p.get("ticker"), px(p.get("entry"))),
                sub="stop %s · target %s · now %s%s" % (px(p.get("stop")), px(p.get("target")), px(p.get("live")), (" · " + grade_words(grade)) if grade else ""),
                pnl=pnl, pnl_aud=p.get("upnl_net_aud"), color=col, since="in the trade %s" % hours_words(p.get("hours_in")),
                size="%s units = %s" % (num(p.get("units"), 2 if (p.get("units") or 0) < 10 else 0), aud(p.get("notional_aud"))), fees=aud(p.get("fees_aud")),
                rail=("stop rule: %s" % p.get("rail")) if p.get("rail") else "", live_src=str(live.get("src") or ""), mark=when(p.get("mark_utc"), now), d=p)
def armed_row(c, a, now=None, waits_open=False):
    seat = c.get("seat"); w = who(seat, c.get("trader")); side = side_word(a.get("side"))
    return dict(kind="armed", seat=seat, name=c.get("trader"), who=w, sym=a.get("ticker"), side=side, grade=a.get("grade"), waits_open=bool(waits_open),
                head="%s wants to go %s %s at %s" % (w, side, a.get("ticker"), px(a.get("limit"))),
                sub="%s · stop %s · target %s · %s" % ("order placed, waiting to fill when its market opens (it is closed now)" if waits_open else "order placed, waiting to fill", px(a.get("stop")), px(a.get("target")), grade_words(a.get("grade"))),   # dasheli10_20261103 one order, not 'armed' + 'queued'
                pnl="risking %s%s if the stop is hit" % (aud(a.get("risk_aud")), (" (%.1f%% of the book)" % (float(a.get("risk_pct")) * 100)) if a.get("risk_pct") else ""), pnl_aud=None, color="grey",
                since="armed %s" % when(a.get("armed_utc"), now), size="%s units" % num(a.get("units"), 2 if (a.get("units") or 0) < 10 else 0), note=str(a.get("note") or ""), d=a)
def closed_row(c, x):
    seat = c.get("seat"); w = who(seat, c.get("trader")); side = side_word(x.get("side")); pnl, col = _pnl_words(x.get("pnl_aud"), None, x.get("R"))
    return dict(kind="closed", seat=seat, name=c.get("trader"), who=w, sym=x.get("ticker"), side=side, grade=x.get("grade"),
                head="%s closed a %s on %s" % (w, side, x.get("ticker")), sub=(str(x.get("status") or "closed").replace("_", " ") + (" · " + plain_why(x.get("why")) if x.get("why") else "")),
                pnl=pnl, pnl_aud=x.get("pnl_aud"), color=col, since="closed today", d=x)
KIND_WORDS = dict(queued_for_open=("queued", "order queued; goes in when the market opens"), engine_queued_for_open=("queued", "an idea, NOT an order yet: its market is closed; near the open the engine may wake the trader to decide"),
                  pending_wake=("idea", "waiting for the trader's wake-up to decide"), engine_candidate=("idea", "the engine is watching this one"), engine_blocked=("blocked", "blocked by a safety check"))
def opp_row(o, now=None):
    k, words = KIND_WORDS.get(o.get("kind"), ("idea", str(o.get("kind") or "")))
    seat = o.get("seat"); w = who(seat, o.get("desk_name")); side = side_word(o.get("side"))
    return dict(kind=k, seat=seat, name=o.get("desk_name"), who=w, sym=o.get("sym"), side=side, grade=o.get("grade"),
                head="%s %s go %s %s near %s" % (w, "would" if k != "queued" else "might", side, o.get("sym"), px(o.get("entry"))),
                sub="%s · stop %s · target %s · %s%s" % (words, px(o.get("stop")), px(o.get("target")), grade_words(o.get("grade")), " · levels are the engine's guess" if o.get("indicative") else ""),
                pnl=("risking %s" % aud(o.get("risk_aud"))) if o.get("risk_aud") else "", pnl_aud=None, color="grey",
                since=("since %s" % when(o.get("since"), now)) if o.get("since") else "", why=plain_why(o.get("why")), lift=str(o.get("lift_tag") or "").replace("_", " "), d=o)
def trades(S, opps=None, now=None):
    """Every open trade, armed order, queued order, engine idea and today's closes as one plain sentence each."""
    now = now or dt.datetime.now(WITA); cards = _cards(S); opps = read_opps(S) if opps is None else opps; rows = []
    grade = {(o.get("seat"), o.get("sym")): o.get("grade") for o in opps if isinstance(o, dict) and o.get("src") == "book"}
    wo = armed_waiting_open(S)
    for c in cards:
        bv = c.get("book_view") or {}
        for p in bv.get("positions") or []:
            try: rows.append(open_row(c, p, grade.get((c.get("seat"), p.get("ticker"))), now))
            except Exception as x: rows.append(dict(kind="open", seat=c.get("seat"), head="%s has an open trade (row error %s)" % (who(c.get("seat"), c.get("trader")), type(x).__name__), sub="", pnl="", color="grey", d=p))
        for a in bv.get("armed") or []:
            try: rows.append(armed_row(c, a, now, (c.get("seat"), str(a.get("ticker"))) in wo))
            except Exception as x: rows.append(dict(kind="armed", seat=c.get("seat"), head="%s has an armed order (row error %s)" % (who(c.get("seat"), c.get("trader")), type(x).__name__), sub="", pnl="", color="grey", d=a))
        for x_ in bv.get("closed_today") or []:
            try: rows.append(closed_row(c, x_))
            except Exception: pass
    for o in opps:
        # dasheli10_20261103 FIGURES (HT 16:20 '0 armed / 13 queued' vs Home '3 waiting'): a book order whose market is closed comes back from
        # thf_charts.opps() as kind 'queued_for_open'; it is already an 'armed' row above (from the book), so it was counted twice. Book rows come from the book only.
        if not isinstance(o, dict) or o.get("src") == "book" or o.get("kind") in ("open", "armed", "queued_for_open"): continue
        try: rows.append(opp_row(o, now))
        except Exception: pass
    order = dict(open=0, armed=1, queued=2, closed=3, idea=4, blocked=5)
    rows.sort(key=lambda r: (order.get(r.get("kind"), 9), -(abs(r.get("pnl_aud") or 0))))
    sc = (S or {}).get("small_coin") or {}
    small = dict(txt="Kai's small-coin side book (PAPER): %s coins in the tier, %s open, %s armed, %s closed (won %s / lost %s), realised %s, open risk %s." % (
        sc.get("members") or 0, sc.get("open") or 0, sc.get("armed") or 0, sc.get("closed") or 0, sc.get("wins") or 0, sc.get("losses") or 0, aud(sc.get("realised_aud")), aud(sc.get("open_risk_aud"))),
        rule="Small coins = 20-day average traded value A$100k-500k. Rules: quarter risk, limit orders only, tiny size, shorts only with a perp.", rows=sc.get("rows") or [])
    n = {k: sum(1 for r in rows if r.get("kind") == k) for k in order}; n["armed_wait_open"] = sum(1 for r in rows if r.get("kind") == "armed" and r.get("waits_open"))
    return dict(rows=rows, counts=n, small_coin=small, ts=when(now, now), glossary=GLOSSARY, built_utc=(S or {}).get("built_utc"),
                summary="%d open · %d order%s waiting to fill%s · %d idea%s lined up for the market open (not orders yet) · %d closed today (since midnight WITA) · %d ideas watched · %d blocked" % (
                    n["open"], n["armed"], "" if n["armed"] == 1 else "s", (" (%d of them wait%s for %s market to open)" % (n["armed_wait_open"], "s" if n["armed_wait_open"] == 1 else "", "its" if n["armed_wait_open"] == 1 else "their")) if n["armed_wait_open"] else "",
                    n["queued"], "" if n["queued"] == 1 else "s", n["closed"], n["idea"], n["blocked"]))

# ---- Seats -------------------------------------------------------------------------------------------------------------------
def brain_words(h, now=None):
    """Seat heartbeat -> (plain words, color, detail, last action)."""
    if not isinstance(h, dict) or not h: return dict(txt="no brain report yet", color="grey", detail="", last="")
    mode = h.get("mode") or h.get("chat_mode"); model = str(h.get("model") or h.get("chat_model") or "")
    ml = model.lower(); ai = "Claude" if "claude" in ml else "ChatGPT" if "gpt" in ml else "Grok" if "grok" in ml else (model or "an AI")
    if mode == "brain":
        _, observed, fresh = _brain_observation(h, now)
        txt, col = ("thinking with %s" % ai, "green") if fresh else ("Last AI reply: %s (%s); no recent reply" % (ai, when(observed, now)), "grey")
    elif mode == "brain_down":
        txt, col = "AI reply unavailable - answering with plain code", ("amber" if h.get("memory_ok") is True else "red")
    elif mode == "code": txt, col = "answering with plain code (no AI right now)", "amber"
    else: txt, col = "brain: %s" % (mode or "unknown"), "grey"
    r = _parse(h.get("last_round_utc") or h.get("last_round")); c = _parse(h.get("last_chat")); seen = _parse(h.get("last_seen"))
    acts = [(t, w) for t, w in ((r, "looked at ideas in an engine wake-up"), (c, "answered in the chat"), (seen, "checked in")) if t]
    last = ""
    if acts:
        t, w = max(acts, key=lambda a: a[0]); last = "%s (%s)" % (w, when(t, now))
    detail = (model + " · " if model else "") + ("last good AI answer %s" % when(h.get("last_brain_ok"), now) if h.get("last_brain_ok") else "")
    if h.get("last_fail"): detail += " · last hiccup: " + plain_note(h.get("last_fail"))[:120]
    return dict(txt=txt, color=col, detail=detail.strip(" ·"), last=last)
def state_words(c):
    b = c.get("best") or {}; st = c.get("status")
    idea = ("%s %s (%s)" % (b.get("sym"), side_word(b.get("side")), grade_words(b.get("grade")))) if b.get("sym") else "no idea on the list"
    if st == "red": return ("AI call failed today (%d failed call(s), %d failed wake-up(s))" % (c.get("model_fails_today") or 0, c.get("failed_wakes_today") or 0), "red", idea)
    if st == "green": return ("Has a trade-ready idea: " + idea, "green", idea)
    if st == "amber": return ("Has a strong idea, waiting for a safe moment: %s%s" % (idea, (" - " + plain_why(b.get("why_no_wake"))) if b.get("why_no_wake") else ""), "amber", idea)
    return ("Nothing good right now - sitting tight (best on the list: %s)" % idea, "grey", idea)
def top10_rows(c):
    out = []
    for r in (c.get("top10") or [])[:10]:
        if not isinstance(r, dict): continue
        h = r.get("history") or {}
        out.append(dict(rank=r.get("rank"), sym=r.get("sym"), side=side_word(r.get("side")), grade=r.get("grade"), score=r.get("score"), exp_R=h.get("exp_R"), size_pct=h.get("natural_pct"),
                        open="yes" if r.get("market_open") else "no", wake=plain_why(r.get("why_no_wake")) if r.get("why_no_wake") else ("ready" if r.get("wake_ok") else "")))
    return out
def _second_look(g, label):
    if not isinstance(g, dict): return None
    notes = g.get("notes") or g.get("raw") or ""
    return dict(label=label, status=g.get("status"), when=when(g.get("utc")), model=g.get("model"), text=re.sub(r"\s+", " ", json.dumps(notes, default=str) if not isinstance(notes, str) else notes)[:400])
def seats(S, hb=None, now=None, reg=None):
    """One plain row per seat (11 traders + head of desk + admin seats): money, brain, last action, state, top-10 and second looks."""
    now = now or dt.datetime.now(WITA); S = S or {}; cards = _cards(S); hb = read_hb() if hb is None else (hb or {})
    lb = {r.get("seat"): r for r in ((S.get("leaderboard") or {}).get("rows") or []) if isinstance(r, dict)}
    acc = S.get("accountability") or {}; R_ = acc.get("rounds") or {}; P_ = acc.get("plans") or {}; ST = ((S.get("seat_stats") or {}).get("seats") or {})
    rows = []; seen = set()
    for c in cards:
        seat = c.get("seat"); seen.add(seat)
        try:
            r = lb.get(seat) or {}; b = brain_words(hb.get(seat), now); sw, scol, idea = state_words(c); ar = R_.get(seat) or {}; pl = P_.get(seat) or {}; st = ST.get(seat) or {}
            o = sum(1 for p in c.get("open_positions") or [] if p.get("status") == "open"); a = sum(1 for p in c.get("open_positions") or [] if p.get("status") == "armed")
            money = "%s today (since 06:00 WITA) · %s since start" % (aud(c.get("day_pnl_aud"), sign=True), aud(c.get("pnl_since_aud") if c.get("pnl_since_aud") is not None else r.get("pnl_since_inception_aud"), sign=True)) if seat != "head_of_desk" else "runs the desk, no own book"   # dasheli10_20261103 FIGURES: name the clock
            rows.append(dict(seat=seat, name=c.get("trader") or NAMES.get(seat, seat), first=first_name(seat, c.get("trader")), desk=DESK.get(seat, seat), admin=seat == "head_of_desk", rank=c.get("rank"),
                             today_aud=c.get("day_pnl_aud"), today_txt=aud(c.get("day_pnl_aud"), sign=True), since_aud=c.get("pnl_since_aud"), since_txt=aud(c.get("pnl_since_aud"), sign=True),
                             week_txt=aud(r.get("wtd_pnl_aud"), sign=True), equity_txt=aud(c.get("equity_aud")), money=money, light=c.get("pnl_light") or "grey", light_why=c.get("pnl_light_why") or "",
                             brain=b["txt"], brain_color=b["color"], brain_detail=b["detail"], last_action=b["last"] or ("engine wake-up %s" % when((c.get("last_wake") or {}).get("utc"), now) if c.get("last_wake") else ""),
                             state=sw, state_color=scol, idea=idea, open=o, armed=a, positions=[dict(sym=p.get("ticker"), side=side_word(p.get("side")), status=p.get("status")) for p in c.get("open_positions") or []],
                             wakes_today=c.get("wakes_today") or 0, fails=c.get("model_fails_today") or 0, scanned=c.get("scanned"), n_A=c.get("n_A") or 0, n_Aplus=c.get("n_Aplus") or 0, updated=when(c.get("updated"), now),
                             red_streak=ar.get("red_streak") or 0, half_risk=bool(ar.get("half_risk")), rounds=ar.get("rounds"),
                             plan=(dict(when=pl.get("wita"), status=pl.get("status"), what=str((pl.get("plan") or {}).get("what_went_wrong") or "")[:300], fix=str((pl.get("plan") or {}).get("mitigation") or "")[:300]) if pl else None),
                             stats=(dict(trades=st.get("trades"), win_rate=st.get("win_rate"), avg_R=st.get("avg_R"), net_aud=st.get("net_aud")) if st.get("trades") else None),
                             top10=top10_rows(c), last_wake=(dict(id=(c.get("last_wake") or {}).get("wake_id"), status=(c.get("last_wake") or {}).get("status"), when=when((c.get("last_wake") or {}).get("utc"), now)) if c.get("last_wake") else None),
                             looks=[x for x in (_second_look(c.get("gpt"), "Grok second look (advice only)"), _second_look(c.get("grok"), "Grok")) if x], mark_missing=r.get("mark_missing") or []))
        except Exception as x:
            rows.append(dict(seat=seat, name=c.get("trader") or seat, first=first_name(seat, c.get("trader")), desk=DESK.get(seat, seat), admin=False, state="row error %s" % type(x).__name__, state_color="grey", brain="", brain_color="grey", money="", top10=[], looks=[], positions=[]))
    reg = reg if reg is not None else _registry()
    for seat in ADMIN_SEATS:
        if seat in seen: continue
        h = hb.get(seat); meta = reg.get(seat) or {}
        if not h and not meta: continue
        b = brain_words(h, now)
        rows.append(dict(seat=seat, name=meta.get("name") or NAMES.get(seat, seat), first=first_name(seat, meta.get("name")), desk=meta.get("desk") or DESK.get(seat, seat), admin=True, rank=None, money="admin seat, no own book",
                         brain=b["txt"], brain_color=b["color"], brain_detail=b["detail"], last_action=b["last"], state="", state_color="grey", open=0, armed=0, positions=[], top10=[], looks=[]))
    rows.sort(key=lambda r: (1 if r.get("admin") else 0, r.get("rank") or 99, r.get("seat") or ""))
    try:   # pushhealth_20261103 A55 pts 4+6: each seat's own schedule (editable) + brain / last runs / lessons / AI in use (seat_brain.cards())
        _pi = push_info(); _cs = seat_cards()
        for r in rows:
            try: r["page"] = seat_page(r.get("seat"), _cs.get(r.get("seat")) or {}, _pi, now)
            except Exception: r["page"] = None
    except Exception: _pi = {}
    lbx = S.get("leaderboard") or {}
    tr = [r for r in rows if not r.get("admin")]
    return dict(rows=rows, ts=when(now, now), glossary=GLOSSARY, prizes=(lbx.get("prizes_aud") or []), window="Leaderboard day runs from %s" % (lbx.get("window_start_wita") or "06:00 WITA"),
                rules=[str(g) for g in (lbx.get("guards") or [])][:4], accountability_rule=str(acc.get("rule") or ""), brains=brains_words(hb, now), built_utc=S.get("built_utc"),
                total=dict(today_txt=aud(shown_sum(tr, "today_aud"), sign=True), since_txt=aud(sum(float(r.get("since_aud") or 0) for r in tr), sign=True), open=sum(r.get("open") or 0 for r in tr), armed=sum(r.get("armed") or 0 for r in tr)),   # dasheli10_20261103 the column totals (= Home)
                wakes=wake_words(S)["long"], push=push_words(_pi) if _pi else None)   # pushhealth_20261103 scheduled pushes card
def _registry():
    try:
        from veteran_desk import thf_chat_home as CH; return CH.seats()
    except Exception: return {s: dict(name=NAMES.get(s, s), desk=DESK.get(s, s)) for s in ADMIN_SEATS}

# ---- pushhealth_20261103 (A95 scheduled pushes + Singapore health; A46 AI cost per seat; A55 pts 4+6 seat schedule + seat page) ----
# The dash is a read-only mirror: these readers only READ live-tree files written by the timers (veteran_desk/push_prompts.py +
# veteran_desk/sgp_health.py every 5 min, cfo_report aicost every 10 min). The one write is sched_request(): Brett's schedule edit goes to
# state/thf/chat_imgs/schedule_edits.jsonl (the dash's one writable live dir); push_prompts re-validates and applies it within 5 min.
PUSH_STATUS = os.path.join(L, "state", "thf", "push_status.json"); SEAT_CARDS = os.path.join(L, "state", "thf", "seat_cards.json")
SGP_JSON = os.path.join(L, "state", "thf", "sgp_health.json"); AICOST_JSON = os.path.join(L, "state", "thf", "seat_ai_cost.json")
SCHED_REQ = os.path.join(L, "state", "thf", "chat_imgs", "schedule_edits.jsonl"); SCHED_DONE = os.path.join(L, "state", "thf", "schedule_edits_applied.jsonl")
_INJECT = {}   # tests: _INJECT["push"|"cards"|"sgp"|"aicost"] = dict (live files are never read under PYTEST_CURRENT_TEST)
SCHED_SESSIONS = ("ASIA", "EU", "US", "US_OPEN", "US_CLOSE", "24/7")
SESSION_WORDS = {"ASIA": "Asia (05:00-18:30 WITA)", "EU": "Europe (15:30-00:30 WITA)", "US": "US (22:00-05:30 WITA)", "US_OPEN": "US open (22:00-00:30 WITA)", "US_CLOSE": "US close (03:00-05:30 WITA)", "24/7": "all day, every day", "ROUNDS": "inside every trading round"}
def _live(key, path):
    if key in _INJECT: return _INJECT[key]
    if os.environ.get("PYTEST_CURRENT_TEST"): return {}
    return _jload(path) or {}
def push_info(): return _live("push", PUSH_STATUS)
def seat_cards(): return (_live("cards", SEAT_CARDS) or {}).get("cards") or {}
def sgp_info(): return _live("sgp", SGP_JSON)
def aicost_info(): return _live("aicost", AICOST_JSON)
def _sched_rows(path, n=200):
    if os.environ.get("PYTEST_CURRENT_TEST") and "sched" not in _INJECT: return []
    if "sched" in _INJECT: return list(_INJECT["sched"].get(os.path.basename(path)) or [])
    try:
        with open(path, encoding="utf-8", errors="replace") as f: rows = f.read().splitlines()[-n:]
    except FileNotFoundError: return []
    out = []
    for l in rows:
        try: out.append(json.loads(l))
        except Exception: pass
    return out
def sched_check(b, seats=None):
    """Validate a Seats-tab schedule edit (same rules as push_prompts.check_sched, which re-checks before applying). (clean, None) | (None, why)."""
    b = b if isinstance(b, dict) else {}; seat = re.sub(r"[^a-z_]", "", str(b.get("seat") or ""))
    if not seat or (seats is not None and seat not in seats): return None, "unknown seat"
    out = dict(seat=seat)
    if b.get("every_min") not in (None, ""):
        try: e = int(b.get("every_min"))
        except Exception: return None, "every_min must be a whole number of minutes"
        if not 10 <= e <= 240: return None, "every_min must be 10-240 minutes"
        out["every_min"] = e
    if b.get("sessions") is not None:
        ss = b.get("sessions"); ss = [x.strip() for x in ss.split(",")] if isinstance(ss, str) else ss
        if not isinstance(ss, list) or not [x for x in ss if str(x).strip()]: return None, "pick at least one session"
        ss = [str(x).strip().upper() for x in ss if str(x).strip()]; bad = [x for x in ss if x not in SCHED_SESSIONS]
        if bad: return None, "unknown session %s" % ",".join(bad)
        out["sessions"] = list(dict.fromkeys(ss))
    if b.get("quiet") is not None:
        q = b.get("quiet"); q = [x.strip() for x in q.split(",")] if isinstance(q, str) else q
        if not isinstance(q, list) or len(q) > 4: return None, "quiet = up to 4 windows like 12:00-13:00"
        qq = []
        for x in q:
            x = str(x).strip()
            if not x: continue
            m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$", x)
            if not m: return None, "quiet window %r must look like 12:00-13:00" % x
            qq.append(x)
        out["quiet"] = qq
    if len(out) == 1: return None, "nothing to change"
    return out, None
def sched_request(b, seats=None, path=None):
    """POST /api/seat_schedule -> one validated request row (flock). Applied by tfo-thf-push-prompts.timer within 5 min (backup + journal)."""
    import fcntl
    if seats is None:
        sd = os.path.join(L, "veteran_desk", "seats"); seats = {f[:-5] for f in os.listdir(sd) if f.endswith(".json")} if os.path.isdir(sd) else None
    clean, why = sched_check(b, seats)
    if clean is None: raise ValueError(why)
    r = dict(clean, ts_wita=dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), by="brett", via="dash", tag="pushhealth_20261103")
    p = path or SCHED_REQ
    with open(p, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX); fh.write(json.dumps(r) + "\n"); fh.flush(); fcntl.flock(fh, fcntl.LOCK_UN)
    return dict(ok=True, queued=r, note="Saved. The desk applies it within 5 minutes (checked again, old schedule backed up) and the engine uses it at its next wake-up slot.")
def sched_words(sc):
    """A seat json 'schedule' dict -> one plain sentence."""
    sc = sc if isinstance(sc, dict) else {}; ses = [str(x).upper() for x in (sc.get("sessions") or [])]
    if sc.get("every_min") and "24/7" in ses: w = "Looks for trades every %s min, around the clock (its market never closes)" % sc.get("every_min")
    elif sc.get("every_min"): w = "Looks for trades every %s min while %s are open" % (sc.get("every_min"), " / ".join(SESSION_WORDS.get(x, x).split(" (")[0] for x in ses) or "its markets")
    elif "ROUNDS" in ses: w = "Works inside every trading round (no own timer)"
    else: w = "Works when someone messages it (chat + seat mail)"
    if sc.get("quiet"): w += "; quiet %s" % ", ".join(map(str, sc["quiet"]))
    ex = []
    for k, v in sorted(sc.items()):
        if str(k).endswith("_wita"): ex.append("%s %s" % (str(k)[:-5].replace("_", " "), ", ".join(v) if isinstance(v, list) else v))
    if sc.get("review_every_min"): ex.append("trade review every %s min" % sc["review_every_min"])
    return w + (" · " + " · ".join(ex) if ex else "") + " (times WITA)"
def seat_page(seat, card=None, push=None, now=None):
    """Brett 21:43 point 6: brain, schedule, last runs, lessons and AI in use - one plain block per seat (from seat_brain.cards())."""
    c = card if card is not None else (seat_cards().get(seat) or {}); push = push if push is not None else push_info()
    if not c: return None
    br = c.get("brain") or {}; ai = c.get("ai") or {}; sc = (c.get("schedule") or {}).get("raw") or {}
    lr = when(c.get("last_round_utc"), now) if c.get("last_round_utc") else ""
    slots = [dict(label=x.get("label"), open=x.get("open"), note=x.get("note"), today=x.get("today"), woke=seat in (x.get("woke") or []), skipped=(x.get("skipped") or {}).get(seat))
             for x in (push.get("slots") or []) if seat in (x.get("seats") or [])]
    done = [r for r in _sched_rows(SCHED_DONE) if r.get("seat") == seat]; reqs = [r for r in _sched_rows(SCHED_REQ) if r.get("seat") == seat]
    last_req = reqs[-1] if reqs else None; last_any = done[-1] if done else None
    pending = bool(last_req and (not last_any or str(last_any.get("req_wita") or "") < str(last_req.get("ts_wita") or "")))
    real = [r for r in done if r.get("why") != "no change"]; last_done = real[-1] if real else None   # a save that changed nothing is not shown as a change
    editable = bool(sc.get("every_min") or any(str(x).upper() in SCHED_SESSIONS for x in (sc.get("sessions") or [])))
    return dict(name=c.get("name"), role=c.get("role"), reports_to=c.get("reports_to"),
                schedule=sched_words(sc), schedule_raw={k: sc.get(k) for k in ("every_min", "sessions", "quiet") if k in sc}, editable=editable,
                sessions_all=list(SCHED_SESSIONS), pending=(dict(at=str(last_req.get("ts_wita"))[11:16], what={k: last_req.get(k) for k in ("every_min", "sessions", "quiet") if k in last_req}) if pending else None),
                last_change=(dict(at=str(last_done.get("wita"))[11:16], ok=last_done.get("ok"), why=last_done.get("why")) if last_done else None),
                brain_txt="%s standing facts · %s journal lines · %s lessons" % (br.get("facts", 0), br.get("journal_lines", 0), br.get("lessons", 0)),
                lessons=[str(x) for x in (br.get("last_lessons") or [])][:2], journal=[str(x) for x in (br.get("last_journal") or [])][:3],
                ai_txt=("%s (%s%s)" % (ai.get("model") or "no AI yet", {"brain": "thinking with it", "brain_down": "AI down - plain code", "code": "plain code"}.get(ai.get("mode"), ai.get("mode") or "?"), (", last seen " + str(ai.get("last_seen"))[11:16]) if ai.get("last_seen") else "")),
                last_round=lr, pushes=slots, cards_at=(push.get("last_run") or ""))
def push_words(push=None):
    """One line + detail for the Scheduled pushes card / Health row."""
    p = push if push is not None else push_info()
    if not p: return None
    mode = {"live": "ON", "dry_run": "DRY RUN (nothing sent yet)", "off": "OFF"}.get(str(p.get("mode")), str(p.get("mode")))
    rows = []
    for x in p.get("slots") or []:
        nm = ", ".join((x.get("names") or [])[:12])
        lab = str(x.get("label") or x.get("id")).split(" (")[0]; nd = x.get("note_done"); nw = len(x.get("woke") or [])
        if x.get("today"):
            st = (("sent %s, woke %d" % (nd, nw)) if nd else "nothing sent") if x.get("state") == "done" else (("sent %s, woke %d so far" % (nd, nw)) if nd else "starting") if x.get("state") == "now" else "later today"
            rows.append([lab, "note %s, wake-ups %s-%s" % (x.get("note"), x.get("open"), x.get("until")), nm, st])
        else: rows.append([lab, "not today (next %s)" % (x.get("next_open") or "-"), nm, "-"])
    return dict(mode=mode, live=p.get("mode") == "live", next=p.get("next"), error=p.get("error"), file=p.get("file") or "state/thf/push_schedule.json", last_run=p.get("last_run"),
                head="Scheduled pushes %s: %s." % (mode, p.get("next") or "no more today"), rows=rows,
                how="Each push = one short note from Marcus to the traders before the market opens (hunt A/A+, B at half size, report blocks), then at most one wake-up per trader after the open if it has not looked yet. To change times, seats or switch it off: edit the plain file %s, or ask Simon in the chat." % (p.get("file") or "state/thf/push_schedule.json"))
def aicost_rows(cost=None):
    """A46 (HT 21:10 2 Nov 'count AI token cost per seat on the Health tab'): seat_ai_cost.json -> table rows (biggest first)."""
    c = cost if cost is not None else aicost_info(); seats = (c or {}).get("seats") or {}
    tot = sum(int(v.get("est_tokens") or 0) for v in seats.values() if isinstance(v, dict)) or 1; out = []
    for s, v in sorted(seats.items(), key=lambda kv: -int((kv[1] or {}).get("est_tokens") or 0)):
        if not isinstance(v, dict): continue
        out.append([who(s) if s in NAMES or s in DESK else {"advisor_claude": "Desk advisor (Claude)", "fixer": "Fixer / code_hands", "strategy_lab": "Strategy Lab", "gpt2l": "GPT second looks", "cli": "Command line", "llm": "Other AI calls"}.get(s, s),
                    "%d (%d ok)" % (int(v.get("calls") or 0), int(v.get("ok") or 0)), "%.0fk" % (int(v.get("est_tokens") or 0) / 1000.0), "%.0f%%" % (100.0 * int(v.get("est_tokens") or 0) / tot), "%.0f min" % float(v.get("ai_minutes") or 0)])
    return out
# pushhealth_20261103 (master 19:10 WITA): the SuperGrok sign-in link vanished from every served page since the chatgb rebuild -> one plain
# "Needs Brett" box on the Health tab (AI group): SuperGrok sign-in, Grok/Cursor relay, the Mac switch-off list. Cooldowns are read from
# state/thf/ai_cooldown.json by NAME only (kind + until; never the 'why' text). Mac jobs still seen = fresh files the Mac still pushes.
COOLDOWN_JSON = os.path.join(L, "state", "thf", "ai_cooldown.json"); MAC_DATA = "/opt/tfo/data"
MAC_JOBS = ["com.tfo.catalyst-stream", "com.tfo.eodhd-eu-ws", "com.tfo.intraday-bars", "com.tfo.firm-risk-service", "com.tfo.oanda-check", "com.tfo.oanda-daily"]
MAC_CMD = ("U=$(id -u); for j in %s; do launchctl bootout gui/$U/$j 2>/dev/null; launchctl disable gui/$U/$j; done; "
           "launchctl list | grep -E 'catalyst-stream|eodhd-eu-ws|intraday-bars|firm-risk|oanda-(check|daily)' || echo ALL OFF") % " ".join(MAC_JOBS)
CLEAR_CD = 'cd /opt/tfo/live/thf && /opt/tfo/venv/bin/python -c "from veteran_desk import ai_cooldown as C; %s"'
# decisions_20261103 (master fixer, checklist F17/F22/E6): the Mac switch-off list and Brett's open decisions are DATA, written by the
# fixers (live tree owns state/thf, so the 15 s dash mirror never clobbers them): state/thf/mac_switchoff.json {"labels": [...], "keep": "..."}
# replaces MAC_JOBS when present; state/thf/needs_brett_decisions.json = [{id, title, status, why, steps, keep, open}] extra Needs Brett items.
MAC_SWITCHOFF_JSON = os.path.join(L, "state", "thf", "mac_switchoff.json"); DECISIONS_JSON = os.path.join(L, "state", "thf", "needs_brett_decisions.json")
def mac_switchoff():
    """(labels, cmd, keep_text or None) - the data file when present, else the built-in 6 jobs."""
    j = _INJECT["mac_switchoff"] if "mac_switchoff" in _INJECT else ({} if os.environ.get("PYTEST_CURRENT_TEST") else (_jload(MAC_SWITCHOFF_JSON) or {}))
    labs = [str(x) for x in (j.get("labels") or []) if re.match(r"^com\.tfo\.[\w.-]+$", str(x))] or list(MAC_JOBS)
    cmd = ("U=$(id -u); for j in %s; do launchctl bootout gui/$U/$j 2>/dev/null; launchctl disable gui/$U/$j; done; "
           "launchctl list | grep -E '%s' || echo ALL OFF") % (" ".join(labs), "|".join(re.escape(x) for x in labs))
    return labs, cmd, (str(j["keep"]) if j.get("keep") else None)
def decisions():
    j = _INJECT["decisions"] if "decisions" in _INJECT else ([] if os.environ.get("PYTEST_CURRENT_TEST") else (_jload(DECISIONS_JSON, []) or []))
    out = []
    for d in j if isinstance(j, list) else []:
        if isinstance(d, dict) and d.get("id") and d.get("title") and not d.get("done"):
            out.append(dict(id=str(d["id"]), open=bool(d.get("open", True)), title=str(d["title"]), status=str(d.get("status") or "waiting for your decision"),
                            why=str(d.get("why") or ""), steps=[str(x) for x in (d.get("steps") or [])], keep=str(d.get("keep") or ""), scope=d.get("scope","thf"), optional=bool(d.get("optional"))))
    return out
def cooldowns():
    """{name: dict(kind, until_wita, active)} - names, kind and time only."""
    if "cooldown" in _INJECT: c = _INJECT["cooldown"]
    elif os.environ.get("PYTEST_CURRENT_TEST"): c = {}
    else: c = _jload(COOLDOWN_JSON) or {}
    out = {}
    for k, v in (c or {}).items():
        if isinstance(v, dict):
            try: act = float(v.get("until") or 0) > time.time()
            except Exception: act = False
            out[str(k)] = dict(kind=str(v.get("kind") or ""), until_wita=str(v.get("until_wita") or ""), active=act)
    return out
def mac_seen(now=None):
    """Mac jobs that still push fresh files to the droplet (proof they still run on the Mac)."""
    if "mac" in _INJECT: return list(_INJECT["mac"])
    if os.environ.get("PYTEST_CURRENT_TEST"): return []
    now = now or time.time(); out = []
    def age(p):
        try: return now - os.path.getmtime(p)
        except OSError: return None
    a = age(os.path.join(MAC_DATA, "state", "catalyst_stream", "heartbeat.json"))
    if a is not None and a < 900:
        mp = (_jload(os.path.join(MAC_DATA, "state", "catalyst_stream", "heartbeat.json")) or {}).get("pid"); lp = (_jload(NEWS_HB) or {}).get("pid")
        if mp != lp: out.append("news stream (com.tfo.catalyst-stream)")
    a = age(os.path.join(MAC_DATA, "state", "eodhd_eu", "health.json"))
    if a is not None and a < 900: out.append("EU price feed (com.tfo.eodhd-eu-ws)")
    a = age(os.path.join(MAC_DATA, "sqlite", "intraday_bars.sqlite"))
    if a is not None and a < 2700: out.append("intraday bars (com.tfo.intraday-bars)")
    return out
def _needs_brett_base(cd=None, mac=None):   # healthdesk_20261103: renamed; needs_brett() below adds the paid data vendor items
    """The things only Brett can do, each with the link / steps and what Simon or the Head Trader runs afterwards."""
    cd = cooldowns() if cd is None else cd; mac = mac_seen() if mac is None else mac; _ms = mac_switchoff()   # decisions_20261103
    def cdw(*names): return "; ".join("%s: %s until %s%s" % (n, cd[n].get("kind") or "off", cd[n].get("until_wita") or "?", "" if cd[n].get("active") else " (expired, still off)") for n in names if n in cd) or "no cooldown left"
    # An old relay authentication record must not override a newer scheduled quota pause.
    grok = cd.get("grok") or cd.get("seat") or {}
    gr = grok.get("kind") in ("auth", "down")
    cfg = _INJECT.get("mac_switchoff") if "mac_switchoff" in _INJECT else ({} if os.environ.get("PYTEST_CURRENT_TEST") else (_jload(MAC_SWITCHOFF_JSON) or {}))
    mac_action = (cfg or {}).get("requires_owner_action", True)
    return [dict(id="grok_relay", open=gr, title="Grok relay (Cursor) - account limit", status=("OFF - waiting for the Cursor account" if gr else ("scheduled pause; next retry "+str(grok.get("until_wita") or "pending") if grok.get("kind")=="quota" else "no owner action recorded; see current provider status")),
                 why="The Cursor account hit its US$160 monthly limit and the key file ~/.tfo_secrets/cursor_api_key is missing, so the Grok relay is switched off.",
                 steps=["1. Raise the Cursor monthly limit, or add the key file.", "2. Tell Simon or the Head Trader. They switch it back on with:"],
                 cmd="sudo systemctl enable --now tfo-grok-relay-thf tfo-grok-relay\n" + CLEAR_CD % "C.mark_ok('grok'); C.mark_ok('seat')", cooldown=cdw("grok", "seat")),
            dict(id="mac", open=mac_action and (bool(mac) or bool(_ms[2])), title="Your Mac - switch off %d old jobs Singapore already runs" % len(_ms[0]),
                 status=("still running on the Mac: " + ", ".join(mac)) if mac else "not seen on the Mac any more",
                 why="They use the same API keys as Singapore (the Mac EU price feed alone holds 30 of our 50 EODHD live slots). Singapore runs all six itself.",
                 steps=["On the Mac, open Terminal and paste this one line (it prints ALL OFF when done):"], cmd=_ms[1],
                 keep=_ms[2] or "Leave ON for now (Singapore does not run them yet): the hunt48 Asia desks (com.tfo.hunt48.*, they move when you say go), the Veteran Desk, the trend/essentials books and the Mac-to-Singapore sync.")]
def needs_brett(cd=None, mac=None, vend=None):
    """healthdesk_20261103: the base list + one item per paid data vendor that only Brett can fix (EODHD daily call limit, a refused OANDA token).
    vend = vendor_rows(...) list; None -> the live feed keeper file (tests: _INJECT['vendors'] / PYTEST_CURRENT_TEST -> none)."""
    it = _needs_brett_base(cd, mac)
    if vend is None:
        if "vendors" in _INJECT: vend = _INJECT["vendors"]
        elif os.environ.get("PYTEST_CURRENT_TEST"): vend = []
        else: vend = vendor_rows(read_feeds())
    for v in vend or []:
        if not isinstance(v, dict) or not v.get("needs_brett"): continue
        q = v.get("quota") or {}
        if q.get("exhausted") or "402" in str(v.get("err") or ""):
            rs = q.get("resets_wita") or "08:00 WITA"
            it.append(dict(id="eodhd", open=True, title="EODHD - today's data calls are used up", status="OFF until %s, unless you buy extra calls now" % rs,
                           why="EODHD refuses every data call (HTTP 402) because the key's daily limit is used up: %s of %s calls on %s. The key is shared with your Mac's jobs. Meanwhile: no European or Asian minute bars, "
                               "no paid live data for ASX trades on the dash (seat packs use Yahoo, 15-20 minutes late), no EODHD news or earnings dates (FMP, Massive and Unusual Whales news still arrive). "
                               "NOT affected: live European prices (separate EODHD live feed) and the book's P/L marks." % ("{:,}".format(int(q.get("used") or 0)), "{:,}".format(int(q.get("limit") or 0) + int(q.get("extra") or 0)), q.get("date") or "?"),
                           steps=["1. To fix it now: EODHD account page -> buy extra API calls for today (or a bigger plan). It works again at once.",
                                  "2. So it does not happen again: switch off the Mac jobs that use the same key ('Your Mac' box).",
                                  "3. If you do nothing, it comes back by itself at %s." % rs],
                           keep="From EODHD's own account counter (checked %s, at most once every 10 minutes). Nothing for Simon to fix in code." % (q.get("checked_wita") or "?")))
        else:
            it.append(dict(id=str(v.get("id") or "vendor").split(":")[-1], open=True, title="%s - needs you" % v.get("name"), status=v.get("short") or "", why=str(v.get("needs_brett")), steps=[],
                           keep="Last error: %s" % (v.get("err") or "—")))
    return it + [d for d in decisions() if d.get("scope") == "thf" and not d.get("optional")]   # decisions_20261103: Brett's open policy decisions (state/thf/needs_brett_decisions.json)
# ---- Health ------------------------------------------------------------------------------------------------------------------
def _row(id_, dot, head, sub="", d=None, group="checks"): return dict(id=id_, dot=dot, head=head, sub=sub, d=d, group=group)
CALLS_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "llm_queue", "calls.jsonl")   # what thf_dash.build() counts 'model_calls' from
FEED_NAMES = dict(yahoo_quotes="Yahoo prices (free backup)", coingecko="CoinGecko crypto prices", kraken_ticker="Kraken live crypto prices", kraken_tape="Kraken live crypto trades",
                  thf_wstape="Kraken trade recorder", news_rss="News headlines (Fed, MarketWatch, CoinDesk, CNBC)", thf_newsbot="Crypto news reader", announcements="Company announcements (SEC + ASX)",
                  news_latency="News speed log", thf_msgdesk="Chat inbox reader", feed_ok="Mac backup copy of inputs", intraday_bars="Mac backup copy of minute bars", audusd="AUD/USD rate",
                  eodhd_eu="EODHD live European prices (paid)", us_stream_fmp="FMP live US quotes (paid, research only)", paid_bars="Paid price bars (EODHD + FMP)",
                  pit_eod_daily="Daily closing prices, every market (EODHD paid + Yahoo)", pit_fx_daily="Daily FX + futures prices", asia_intraday="Asia 5-minute prices (EODHD paid + Yahoo)",
                  bars_sqlite="Minute-price store", coinalyze="Coinalyze crypto futures data (paid)", catalyst_stream="Company news stream (FMP, ASX, Unusual Whales, EDGAR; paid)",
                  news_desk="News mood + headlines", event_calendar="Earnings + economic calendar", edgar_8k="SEC 8-K filings", intraday_pull="US/EU minute prices (Massive + EODHD, paid)",
                  kraken_sweep="Kraken 24h volume sweep", feed_delay="Price-delay labels", ibkr_borrow="IBKR borrow list", earnings_cache="Earnings calendar cache", sector_fill="Sector tags",
                  sbx_feed_ok="Sandbox copy (not live)", oanda_practice="OANDA practice FX account")
VENDOR_WORDS = dict(uw_news="Unusual Whales news", uw_flow="Unusual Whales options flow", fmp_news="FMP news", fmp_press="FMP press releases", massive_news="Massive/Benzinga news", eodhd_news="EODHD news", asx_announcements="ASX announcements",
                    eodhd_earnings="EODHD earnings dates", fmp_earnings="FMP earnings dates", fmp_economic="FMP economic calendar", fmp_8k="FMP 8-K filings", edgar_form4="EDGAR insider filings", tdnet="Japan TDnet filings", fda_pdufa="FDA dates", eia_wpsr="EIA oil stocks")   # healthdesk_20261103: every news-stream vendor in plain words
def read_feeds(): return _jload(FEEDS_JSON) or {}
def plain_feed_detail(d):
    """Feed keeper detail -> plain words ('streams down: ['eu-quote']' -> 'its live price stream is down')."""
    d = str(d or "").strip()
    if not d: return ""
    m = re.search(r"streams down:\s*\[([^\]]*)\]", d)
    if m: return "its live %s stream is down" % ", ".join(x.strip(" '\"").replace("eu-", "") for x in m.group(1).split(",") if x.strip())
    m = re.search(r"vendors not ok:\s*\{(.*)\}", d)
    if m:
        bits = re.findall(r"'([a-z_]+)':\s*'([^']*)'", m.group(1))
        return "%s refusing us (%s)" % ((names_list([VENDOR_WORDS.get(k, k.replace("_", " ")) for k, _ in bits]) or "a source").replace("Unusual Whales news and Unusual Whales options flow", "Unusual Whales news + options flow"), ", ".join(sorted({v.replace("HTTP 429", "HTTP 429 = too many requests").replace("HTTP 401", "HTTP 401 = key refused").replace("HTTP 403", "HTTP 403 = key refused").replace("HTTP 402", "HTTP 402 = daily call limit used up").replace("cycle budget", "no answer within 22 s") for _, v in bits})))   # healthdesk_20261103: 402 + cycle budget in plain words
    return d[:140]
def err_words(e):   # healthdesk_20261103: vendor error codes in plain words (same words as plain_feed_detail)
    e = str(e or "").strip() or "no answer"
    for x, w in (("HTTP 429", "HTTP 429 = too many requests"), ("HTTP 401", "HTTP 401 = key refused"), ("HTTP 403", "HTTP 403 = key refused"), ("HTTP 402", "HTTP 402 = daily call limit used up"), ("cycle budget", "no answer within 22 s")):
        e = e.replace(x, w)
    return e[:120]
_PROBLEM = re.compile(r"\b(down|not ok|fail|failed|error|HTTP [45]\d\d|refus|stale|missing)\b", re.I)
def feed_rows(F, now=None):
    """One plain row per feed: dot green = working, amber = working but a part of it is broken (the keeper still says 'ok'), red = not working, grey = idle on purpose."""
    now = now or dt.datetime.now(WITA); out = []
    for k, v in ((F or {}).get("feeds") or {}).items():
        if not isinstance(v, dict) or v.get("kind") == "vendor": continue   # healthdesk_20261103: paid data vendors (feeds['vendor:*']) have their own Health row
        st = str(v.get("status") or "?"); det = str(v.get("detail") or ""); k0 = k.split(":")[0]; name = FEED_NAMES.get(k0, k0.replace("_", " ")) + ((" - " + k.split(":", 1)[1].replace("_", " ")) if ":" in k else "")
        if st == "idle": dot, problem = "grey", "idle on purpose (%s)" % (det or "observe-only")
        elif st == "paused": dot, problem = "amber", "paused, not running anywhere: " + (det[len("paused:"):].strip() if det.startswith("paused:") else det)[:160]
        elif st == "backup_idle": dot, problem = "grey", "backup copy from the Mac, not needed here: " + (det or "Mac not pushing")[:200]   # healthdesk_20261103: was red (unknown status)
        elif st == "closed": dot, problem = "grey", "market closed, waiting for the open" + ((" (" + det[:120] + ")") if det and "closed" not in det else "")   # healthdesk_20261103: weekend closes were red
        elif st in ("disabled", "off"): dot, problem = "grey", "switched off on purpose" + ((": " + det[:140]) if det else "")
        elif st == "degraded": dot, problem = "amber", "working badly: " + (plain_feed_detail(det) or "degraded")
        elif st == "ok" and det and _PROBLEM.search(re.sub(r"\b0 (failed|fails?|errors?)\b", "", det)) and not det.startswith("coverage"): dot, problem = "amber", plain_feed_detail(det)   # '0 failed' is not a problem
        elif st == "ok": dot, problem = "green", ""
        else: dot, problem = "red", "%s%s" % (st, (": " + plain_feed_detail(det)) if det else "")
        facts = v.get("facts") if isinstance(v.get("facts"), dict) else {}
        fact_label = ""
        if facts:
            failure = facts.get("last_failure") or {}
            fact_label = "process %s; session %s; heuristic session %s; entitlement %s; current %s; %s%s" % (
                {True: "running", False: "not running"}.get(facts.get("process_running"), "unknown"),
                {True: "expected", False: "closed"}.get(facts.get("session_expected"), "unknown"),
                {True: "expected", False: "closed"}.get(facts.get("heuristic_session_expected"), "unknown"),
                facts.get("entitlement") or "unknown",
                facts.get("current_health") if facts.get("current_health") is not None else "unknown",
                facts.get("label") or "unknown",
                ("; last failure %s" % failure.get("code")) if failure else "")
        out.append(dict(id=k, name=name, dot=dot, status=st, problem=problem, facts=fact_label, age=hours_words((v.get("age_s") or 0) / 3600.0) + " ago" if v.get("age_s") is not None else "—", paid="paid" in name, usable=None if not facts else facts.get("fresh_usable")))
    out.sort(key=lambda x: (dot_rank(x["dot"]), x["name"])); return out
# healthdesk_20261103 (Brett 20:10 WITA 3 Nov "Oanda and Unusual Whales is up the health feed needs to be updated", 20:16 "CHECK THE HEALTH DESK";
# HT P0 ch-1793706250-713e): one line per PAID DATA VENDOR from the feed keeper rows feeds['vendor:*'] (veteran_desk/vendor_health.py):
# status ok | stale | down | closed | not_configured, last good time (WITA), age, last error, who uses it. A vendor's own problem (quota, outage,
# Brett login) is amber with its fix named; only our side (a check that crashed, a stream that stopped, a missing key) is red.
VENDOR_ORDER = ("vendor:oanda", "vendor:unusual_whales", "vendor:massive", "vendor:eodhd")
VENDOR_SHORT = {"vendor:oanda": "OANDA", "vendor:unusual_whales": "Unusual Whales", "vendor:massive": "Massive", "vendor:eodhd": "EODHD"}
def vendor_rows(F, now=None):
    now = now or dt.datetime.now(WITA); out = []; fs = (F or {}).get("feeds") or {}
    for k in sorted((k for k, v in fs.items() if isinstance(v, dict) and v.get("kind") == "vendor"), key=lambda k: (VENDOR_ORDER.index(k) if k in VENDOR_ORDER else 9, k)):
        v = fs[k]; st = str(v.get("status") or "unknown"); nm = VENDOR_SHORT.get(k, str(v.get("vendor") or k.split(":", 1)[-1]))
        ext = bool(v.get("vendor_side") or v.get("needs_brett"))
        dot = {"ok": "green", "closed": "grey", "unknown": "grey"}.get(st) or ("amber" if (st == "down" and ext) else "red")
        lg = v.get("last_good_wita") or "—"; age = mins_words(v.get("age_min")) if v.get("age_min") is not None else "—"
        q = v.get("quota") or {}
        if st == "ok": short = "%s up (last data %s)" % (nm, age)
        elif st == "closed": short = "%s idle - its market is closed (last data %s)" % (nm, lg)
        elif st == "stale": short = "%s STALE - no new data for %s min (last %s)" % (nm, v.get("age_min"), lg)
        elif st == "not_configured": short = "%s has no key on this server" % nm
        elif st == "down" and q.get("exhausted"): short = "%s DOWN - its daily call limit is used up, back by itself %s%s" % (nm, q.get("resets_wita") or "?", " (or Brett buys extra calls)" if v.get("needs_brett") else "")
        elif st == "down": short = "%s DOWN - %s%s" % (nm, plain_feed_detail(v.get("last_error"))[:90] or "the last request failed", " (needs Brett)" if v.get("needs_brett") else (" (their side)" if ext else ""))
        else: short = "%s: no status yet" % nm
        out.append(dict(id=k, name=nm, vendor=v.get("vendor") or nm, dot=dot, status=st, short=short, last_good=lg, age=age, err=v.get("last_error") or "", detail=v.get("detail") or "",
                        readers=[str(x) for x in (v.get("readers") or [])], needs_brett=v.get("needs_brett"), quota=q, source=v.get("source") or ""))
    return out
EU_SUFFIX = (".L", ".PA", ".DE", ".AS", ".MI", ".MC", ".SW", ".ST", ".CO", ".HE", ".OL", ".BR", ".LS", ".VI", ".IR", ".F", ".XETRA")
def eu_trades(S):
    """Open / armed European tickers (they need the EU price feed)."""
    out = []
    for c in _cards(S):
        for p in c.get("open_positions") or []:
            t = str(p.get("ticker") or "")
            if t.upper().endswith(EU_SUFFIX) and t not in out: out.append(t)
    return out
def mood_words(rg):
    """news_desk regime -> 'news mood: choppy (VIX 21.7, S&P 500 -1.8% over 5 days)'; it became a dict on 3 Nov and showed as raw text."""
    if not rg: return ""
    if not isinstance(rg, dict): return "news mood: %s" % rg
    g = rg.get("gauges") or {}; bits = []
    if g.get("vix") is not None: bits.append("VIX %s" % g.get("vix"))
    if g.get("spx_5d_pct") is not None: bits.append("S&P 500 %+.1f%% over 5 days" % float(g.get("spx_5d_pct")))
    return "news mood: %s%s%s" % (rg.get("flag") or "?", (" (" + ", ".join(bits) + ")") if bits else "", (" · " + str(rg.get("sizing_effect"))) if rg.get("sizing_effect") else "")
def plain_check(m):
    """Dash overlord notes -> plain words ('top10 trend stale 10 min' is an old idea list, not a number that disagrees with the book)."""
    m = str(m or ""); x = re.match(r"^top10 ([a-z_]+) stale (\d+(?:\.\d+)?) ?min", m)
    if x: return "%s's idea list is %s minutes old" % (first_name(x.group(1)), x.group(2))
    return m
def plain_broken(w):
    """Engine/dash 'broken' + 'info' strings -> plain words (no 'websocket', 'EU/LSE', 'PIT', 'OANDA-book' without an explanation)."""
    w = str(w or "")
    w = w.replace("EU/LSE websocket not healthy (no usable EU quote); Yahoo is the EU mark fallback", "The paid live European price feed is not sending usable prices; European trades use Yahoo prices (15-20 minutes late) until it is back")
    w = w.replace("EU/UK marks: EODHD Cboe Europe websocket is primary when healthy; Yahoo (15-20 min late) is the fallback - see Paid marks card", "European prices come from the paid EODHD live feed when it works, otherwise from Yahoo (15-20 minutes late) - see 'Data feeds'")
    w = w.replace("intraday bars are not stored in pit by design", "minute-by-minute prices are kept in a separate store on purpose").replace("(newest bar;", "(newest day;")
    w = w.replace("websocket", "live feed").replace("PIT daily bars", "Daily price history").replace("OANDA-book", "OANDA")
    return w
def health_rows(S, hb=None, ais=None, tick=None, now=None, news=None, feeds_in=None):
    """Every health fact as one plain sentence with a green / amber / red / grey dot (details keep the raw numbers).
    feeds_in = feed keeper dict (state/thf/feed_status.json); None -> read the file (tests: PYTEST_CURRENT_TEST -> none unless injected)."""
    now = now or dt.datetime.now(WITA); S = S or {}; H = S.get("health") or {}; rows = []
    if feeds_in is None and os.environ.get("PYTEST_CURRENT_TEST"): feeds_in = {}
    hb = read_hb() if hb is None else (hb or {}); ais = read_ais() if ais is None else (ais or []); tick = read_tick(S) if tick is None else (tick or {}); news = read_news() if news is None else (news or {})
    try: F_all = (read_feeds() if feeds_in is None else feeds_in) or {}; VEN = vendor_rows(F_all, now)   # healthdesk_20261103: the feed keeper file read once (feeds, vendors, Needs Brett)
    except Exception: F_all, VEN = {}, []
    VD = {v["id"]: v for v in VEN}; EOD_OUT = (VD.get("vendor:eodhd") or {}).get("status") == "down"
    def safe(fn):
        try: fn()
        except Exception as x: rows.append(_row("err", "grey", "A health row could not be built (%s)" % type(x).__name__))
    def engine():
        e = H.get("engine") or {}; age = H.get("engine_age_min"); stale = age is None or age > 5
        dot = "red" if stale else ("amber" if e.get("fails") else "green")
        head = ("The engine has stopped checking in (last seen %s) - the watchdog should restart it." % mins_words(age)) if stale else ("The engine is running (checked in %s)." % mins_words(age))
        if not stale and e.get("fails"): head = "The engine is running but %s cycle(s) in a row failed (checked in %s)." % (e.get("fails"), mins_words(age))
        tot = ((S.get("coverage") or {}).get("totals") or {})
        rows.append(_row("engine", dot, head, "%s cycles since it started %s · last good cycle %s · %s of %s instruments scored · version %s" % (num(e.get("cycles")), when(e.get("boot"), now), when(e.get("last_cycle_ok_utc"), now), num(e.get("scored")), num(tot.get("universe")), e.get("version") or "?"), e))
    def killsw():   # killsw_20261103: the LOSS kill switch on its own row (always shown) + the engine pause switch (ENGINE_KILL)
        k = loss_kill(); ek = engine_pause() or []; dot = {"OK": "green", "HALVED": "amber", "FLAT": "red"}.get(k["state"], "grey")
        if k.get("alert") and dot == "green": dot = "amber"
        rows.append(_row("killsw", dot, "Loss kill switch: %s." % kill_words(k),
                         "rule: 8%% below the desk's peak halves trade sizes; 12%% closes every trade and stops new ones until Brett resets it · engine pause switch (ENGINE_KILL, used for restarts): %s%s" % (
                             ("ON - %s, switched on %s" % (ek[0][0], mins_words(ek[0][1] / 60.0))) if ek else "off", (" · alert file %s is present" % k["alert"]) if k.get("alert") else ""),
                         dict(k, engine_pause=[r for r, _ in ek])))
    def watchdog():
        d = H.get("deadman") or {}; age = H.get("deadman_age_min"); stale = age is None or age > 6; bad = d.get("state") == "DEGRADED"
        dot = "red" if (stale or bad) else "green"
        head = ("The watchdog has not checked in for %s." % mins_words(age)) if stale else ("The watchdog says the engine is in trouble: %s" % d.get("why")) if bad else ("The watchdog (restarts the engine if it stops) is happy: %s." % (d.get("why") or "engine alive"))
        rows.append(_row("watchdog", dot, head, "state %s · checked %s · engine restarts in the last 30 min: %s" % (d.get("state") or "?", mins_words(age), d.get("restarts_30m") or 0), d))
    def overlord():
        o = H.get("overlord") or {}; age = H.get("overlord_age_min"); mm = o.get("mismatches") or []
        dot = "red" if mm else ("amber" if (age is None or age > 15) else "green")
        head = ("The dashboard checker flagged %d thing(s): %s" % (len(mm), "; ".join(plain_check(m) for m in mm[:3]))) if mm else ("The dashboard checker says every number matches the book (checked %s)." % mins_words(age))
        rows.append(_row("dashcheck", dot, head, "repairs: %s · rule: %s" % (", ".join(str(r) for r in (o.get("repairs") or [])[:3]) or "none", o.get("rule") or ""), o))
    def brains():
        b = brains_words(hb, now); rows.append(_row("brains", b["color"], b["txt"], "", dict(on=b["on"], n=b["n"], down=b["down"], stale=b["stale"], missing=b["missing"]), "ai"))
    def needsbrett():   # pushhealth_20261103 (master 19:10): SuperGrok sign-in link + Grok/Cursor relay + Mac switch-off list
        it = needs_brett(vend=VEN); op = [x for x in it if x["open"]]   # healthdesk_20261103: + EODHD daily call limit when used up
        rows.append(_row("needs_brett", "amber" if op else "green", ("Needs Brett: %d thing%s only you can do - %s." % (len(op), "" if len(op) == 1 else "s", ", ".join(x["title"].split(" - ")[0] for x in op))) if op else "Nothing is waiting for Brett.",
                         "tap for the current account actions" if op else "Scheduled provider pauses and optional policy choices are shown separately.", dict(items=op), "ai"))
    def ai_rows():
        for a in ais:
            if not isinstance(a, dict): continue
            st = a.get("state"); dot = {"up": "green", "cooling": "amber", "down": ("red" if a.get("id") == "claude" else "amber")}.get(st, "grey")   # only the primary brain (Claude) can turn the desk red
            nb_ = "needs brett" in ai_word(a).lower()   # healthdesk_20261103: 'back <time>' read as 'comes back by itself' - a Brett-only fix does not end with the retry clock
            rows.append(_row("ai_" + str(a.get("id")), dot, "%s is %s." % (a.get("name"), ai_word(a)), (("retry clock ends %s, but it stays off until Brett does the step in the Needs Brett box" % a.get("until")) if nb_ else ("next retry %s; recovery is checked then" % a.get("until"))) if a.get("until") else "", {k: a.get(k) for k in ("id", "state", "model", "note", "until")}, "ai"))
    def borrow():
        b = S.get("borrow") or {}; fresh = bool(b.get("fresh_24h")); dot = "green" if fresh else ("amber" if b.get("status") == "missing" else "red")
        head = ("The borrow list (shares we may borrow for a short) is fresh - %s hours old." % b.get("age_h")) if fresh else ("No borrow list on disk yet - shorts are not allowed until it arrives." if b.get("status") == "missing" else "The borrow list is stale (%s hours old) - shorts are blocked until a fresh one arrives." % b.get("age_h"))
        rows.append(_row("borrow", dot, head, "updated %s · %s US names · %s ASX names" % (when(b.get("updated_utc"), now), num(b.get("us_names")), num(b.get("asx_names"))), b, "feeds"))
    def paid():
        pf = S.get("paid_feeds") or {}; eu = pf.get("eu_ws") or {}; iv = pf.get("intraday") or {}
        if eu.get("healthy"): rows.append(_row("eu", "green", "Live European prices are flowing from the paid feed (%s)." % ", ".join(eu.get("venues_live") or [])[:80], "%s symbols · heartbeat %ss ago%s" % (eu.get("symbols"), eu.get("age_sec"), (" · " + plain_note(eu.get("why"))) if eu.get("why") else ""), eu, "feeds"))   # dasheli10_20261103: the feed's own plain words (quota, candles off) under the green line
        elif eu.get("markets_open") is False: rows.append(_row("eu", "grey", "European markets are closed, so the paid European price feed is idle until they open.", str(eu.get("why") or ""), eu, "feeds"))
        else:   # dasheli10_20261103 (Brett 16:22 "We have all paid feeds please make sure they are working"): say exactly what is broken and which trades it touches
            st = eu.get("streams") or {}; dn = [k for k, v in st.items() if str(v).startswith("down")]; eut = eu_trades(S)   # 'off' / 'off: quota' = switched off on purpose (eufeed_20261103 quota), not down
            why = plain_note(eu.get("why")) or ("connected, but its live price stream is down, so no usable European price is coming in" if (eu.get("alive") and dn) else "down")   # the feed's own why text first (master 17:22)
            rows.append(_row("eu", "amber", "The paid live European price feed (EODHD) is NOT working: %s. %s" % (why, ("European trades (%s) are priced from Yahoo instead, about 15-20 minutes late." % ", ".join(eut)) if eut else "Yahoo (15-20 minutes late) is the backup."),
                             "%s symbols live · feed heartbeat %ss ago · streams down: %s · reason code: %s" % (eu.get("symbols") or 0, eu.get("age_sec"), ", ".join(dn) or "none", eu.get("why") or "?"), eu, "feeds"))
        n = (iv.get("n_1m") or 0) + (iv.get("n_5m") or 0) + (iv.get("n_15m") or 0)
        rows.append(_row("bars", "green" if n else "amber", ("Minute-by-minute price bars are stored (%s one-minute bars, %s symbols, newest %s)." % (num(iv.get("n_1m")), iv.get("symbols"), when(iv.get("newest_utc"), now))) if n else "No minute-by-minute price bars stored yet.",
                         ("US minute bars (Massive) keep coming; European and Asian minute bars (EODHD) are paused until EODHD's daily call limit resets (%s) - see 'Paid data'" % (((VD.get("vendor:eodhd") or {}).get("quota") or {}).get("resets_wita") or "08:00 WITA")) if EOD_OUT else "", iv, "feeds"))   # healthdesk_20261103
    def newsrow():
        age = ago(news.get("utc"), now) if news.get("utc") else None; t = _parse(news.get("utc")); stale = t is None or (now - t.astimezone(WITA)).total_seconds() > 600
        off = int(news.get("off") or 0); live_n = (news.get("n") or 0) - off   # healthdesk_20261103: retired vendors (switched off for good) are not counted as 'not answering'
        head = ("The news stream has gone quiet (last check %s)." % (age or "never")) if stale else ("The news stream is live (last check %s; %s of %s sources answering%s)." % (age, news.get("ok"), live_n, (", %d retired source%s switched off" % (off, "" if off == 1 else "s")) if off else ""))
        rf = news.get("refused") or []; why = ("not answering: %s" % "; ".join("%s (%s)" % (VENDOR_WORDS.get(k, k.replace("_", " ")), err_words(e)) for k, e in rf[:5])) if rf else ""
        rows.append(_row("news", "red" if stale else ("amber" if live_n and news.get("ok", 0) < max(1, int(live_n * 0.6)) else "green"), head, " · ".join(x for x in (mood_words(news.get("regime")), why) if x), news, "feeds"))
    def urgent():
        for u in kill_fix(tick.get("urgent") or [])[:5]:
            if isinstance(u, dict) and u.get("k") in ("stop", "fill"):   # healthdesk_20261103: a stop hit / big fill is the desk working, not a fault - it turned the whole desk red for 2 h
                rows.append(_row("urgent", "grey", ("Stop hit (a normal exit - the stop did its job, not a fault): " if u.get("k") == "stop" else "Big fill (not a fault): ") + str(u.get("text") or "").replace("STOP HIT ", "").replace("BIG FILL ", ""), "shown for 2 hours after it happens", u, "checks")); continue
            rows.append(_row("urgent", (u.get("dot") if isinstance(u, dict) else None) or "red", str(u.get("text") if isinstance(u, dict) else u), "", u, "broken"))   # killsw_20261103
    def gates():
        g = S.get("gate_overlord") or {}; st = g.get("stuck") or []; wc = g.get("waiting_closed") or []; rs = g.get("restage") or []
        for alert in st:
            if alert.get("display_age_review"): rows.append(_row("order_age", "amber", alert["display_age_review"], "No trading data or execution settings changed.", alert, "checks"))
        st = [alert for alert in st if not alert.get("display_age_review")]
        if st: rows.append(_row("stuck", "amber", "%d order(s) look stuck - market open but not filled: %s." % (len(st), ", ".join("%s (%s)" % (x.get("ticker"), first_name(x.get("seat"))) for x in st[:6])), "", st, "checks"))
        rows.append(_row("waiting", "grey", ("%d order(s) are waiting for their market to open: %s." % (len(wc), ", ".join("%s (%s)" % (x.get("ticker"), first_name(x.get("seat"))) for x in wc[:8]))) if wc else "No order is waiting for a closed market.", ("%d flagged to be re-placed" % len(rs)) if rs else "", g, "checks"))
    def wakes():
        ww = wake_words(S)   # dasheli10_20261103: 'max 6 per seat' next to 'Kai 14' looked like a broken cap; the words now say what each count includes
        rows.append(_row("wakes", "green", ww["long"], "every seat, all wake-ups today: " + (", ".join("%s %s" % (first_name(k), v) for k, v in sorted(ww["by_seat"].items(), key=lambda kv: -kv[1])[:11]) or "none yet"), S.get("wake_counts") or {}, "checks"))
    def model_calls():
        m = S.get("model_calls") or {}
        if m.get("failed"): rows.append(_row("calls", "red", "%s of %s AI calls failed today (%s)." % (m.get("failed"), m.get("today"), ", ".join("%s %s" % (first_name(k), v) for k, v in (m.get("by_seat_failed") or {}).items())), "", m, "ai"))
        elif not m.get("today") and not os.path.exists(CALLS_LOG):   # dasheli10_20261103: no call log on this server -> 'AI calls today: 0, none failed' was a false green
            c_ = aicost_info() or {}; ss_ = [v for v in (c_.get("seats") or {}).values() if isinstance(v, dict)]; n_ = sum(int(v.get("calls") or 0) for v in ss_); ok_ = sum(int(v.get("ok") or 0) for v in ss_)
            if n_: rows.append(_row("calls", "grey", "AI calls in the last %s hours: %s, %s answered, %s not answered by that AI (busy or capped, switched off, or out of time - the seat moves on to the next AI; the 'AI brains' line shows how each seat is thinking right now)." % (c_.get("hours") or 24, num(n_), num(ok_), num(n_ - ok_)),
                                    "from the seats' AI call logs, %s (the AI use row below has them per seat)" % (c_.get("wita") or "?"), m, "ai"))   # healthdesk_20261103: the counts exist (seat_ai_cost.json) - 'cannot count' was untrue
            else: rows.append(_row("calls", "grey", "This server keeps no shared AI call log, so it cannot count today's AI calls. The 'AI brains' line shows how each seat is thinking right now.", "", m, "ai"))
        else: rows.append(_row("calls", "green", "AI calls today: %s, none failed." % m.get("today", 0), "", m, "ai"))
    def feeds():   # dasheli10_20261103 (Brett 16:13 + 16:22): every feed the feed keeper watches, in plain words; 'ok' with a broken part shows amber, never green
        F = F_all   # healthdesk_20261103: read once at the top
        if not F.get("feeds"): return
        fr = feed_rows(F, now); bad = [x for x in fr if x["dot"] == "red"]; amb = [x for x in fr if x["dot"] == "amber"]; idle = [x for x in fr if x["dot"] == "grey"]; good = [x for x in fr if x["dot"] == "green"]
        paused = [x for x in amb if x["status"] == "paused"]; part = [x for x in amb if x["status"] != "paused"]; sn = lambda x: x["name"].split(" (")[0]
        usable = sum(1 for x in fr if x.get("usable") is True)
        head = "Data feeds: %d of %d keeper-ok. Usable now: %s. A keeper ok is not fresh usable data." % (
            len(good), len(fr), usable if any("usable" in x and x.get("facts") for x in fr) else "unknown")
        if bad: head += " NOT working: %s." % "; ".join("%s - %s" % (sn(x), x["problem"][:80]) for x in bad[:4])
        if part: head += " Working, but a part is broken: %s." % "; ".join("%s - %s" % (sn(x), x["problem"][:90]) for x in part[:4])
        if paused: head += " Paused: %s." % ", ".join(sn(x) for x in paused[:4])
        if idle: head += " Idle on purpose: %s." % ", ".join(sn(x) for x in idle[:4])
        rows.append(_row("feeds", "red" if bad else ("amber" if amb else "green"), head, "checked by the feed keeper %s · tap for every feed" % when(F.get("updated_utc"), now), dict(feeds=fr), "feeds"))
    def vendors():   # healthdesk_20261103 (Brett 20:10 "Oanda and Unusual Whales is up the health feed needs to be updated"; HT P0 713e; Marcus 20:54 + Massive/EODHD): one line per paid data vendor
        if not VEN: return
        rd = [v for v in VEN if v["dot"] == "red"]; am = [v for v in VEN if v["dot"] == "amber"]
        dot = "red" if rd else ("amber" if am else ("green" if any(v["dot"] == "green" for v in VEN) else "grey"))
        tb = [[v["name"], v["status"].upper() if v["status"] in ("down", "stale") else v["status"].replace("_", " "), v["last_good"], v["age"].replace(" ago", "")] for v in VEN]   # phone-first: 4 short columns
        ln = ["%s: %s.%s Who uses it: %s." % (v["vendor"], v["detail"].rstrip(".") or v["status"], (" Last error: %s." % v["err"].rstrip(".")) if v["err"] else "", "; ".join(v["readers"]) or "?") for v in VEN]
        rows.append(_row("vendors", dot, "Paid data: " + "; ".join(v["short"] for v in VEN) + ".", "checked by the feed keeper %s · tap for last good time, last error and who uses each one" % when(F_all.get("updated_utc"), now),
                         dict(table=dict(cols=["vendor", "state", "last good (WITA)", "age"], rows=tb),
                              lines=ln + ["ok = answering · stale = no new data while its market is open · closed = its market is shut · DOWN = the last request failed · amber dot = the vendor's own problem (nothing to fix in our code), red dot = ours"]), "feeds"))
    def trade_data():   # dasheli10_20261103: per-trade paid live data (Massive US, EODHD EU/ASX, Coinalyze crypto) shown under every open trade
        ps = [p for c in _cards(S) for p in ((c.get("book_view") or {}).get("positions") or []) if isinstance(p, dict)]
        if not ps: return
        src = [str((p.get("live_data") or {}).get("src") or "") for p in ps]; ok = [s for s in src if s and s not in ("no paid data", "error") and not s.startswith("warming")]
        warm = [s for s in src if s.startswith("warming")]
        dot = "green" if len(ok) == len(ps) else ("grey" if warm and len(ok) + len(warm) == len(ps) else "amber")   # amber at worst: P/L marks come from the engine, this is the extra per-trade data
        head = ("Paid live data (day move, volume, news) is coming in for all %d open trades." % len(ps)) if dot == "green" else ("Paid live data is loading for the open trades (first fetch takes about a minute)." if dot == "grey" else "Paid live data is missing for %d of %d open trades (no paid answer: %s)." % (len(ps) - len(ok), len(ps), ", ".join(str(p.get("ticker")) for p, s in zip(ps, src) if s in ("no paid data", "error"))[:120]))
        miss = [str(p.get("ticker")) for p, s in zip(ps, src) if s in ("no paid data", "error")]   # healthdesk_20261103: name the cause when EODHD refuses calls
        if dot == "amber" and EOD_OUT and any("." in t and not t.upper().endswith(EU_SUFFIX) for t in miss): head += " Their paid live data comes from EODHD, whose daily call limit is used up (back by itself 08:00 WITA). Their P/L marks are not affected: the book marks these trades with Yahoo prices either way."
        rows.append(_row("tradedata", dot, head, "sources: " + ", ".join(sorted(set(ok))) if ok else "", None, "feeds"))
    def coverage():
        cv = S.get("coverage") or {}; bm = cv.get("by_market") or {}; tot = cv.get("totals") or {}
        rows.append(_row("coverage", "green", "We scan %s of %s instruments across %d markets." % (num(tot.get("scored")), num(sum((v.get("available") or 0) for v in bm.values() if isinstance(v, dict))), len(bm)), "universe %s · filtered out %s · errors %s · scan built %s" % (num(tot.get("universe")), num(tot.get("filtered")), tot.get("errors", 0), when(cv.get("store_built_utc"), now)), dict(by_market=bm, totals=tot), "checks"))
    def grokq():
        q = S.get("grok_queue") or []
        if q:
            last = q[-1] if isinstance(q[-1], dict) else {}
            rows.append(_row("grokq", "grey", "Grok second looks (advice only, never block a trade): %d recent, last one '%s'." % (len(q), str(last.get("status") or "").replace("_", " ")), str(last.get("note") or "")[:120], q[-5:], "ai"))
    def funder():
        f=read_funder_delivery(S)
        if not f or f.get("error"):
            rows.append(_row("funder","grey","Message delivery status is unavailable.","",None,"checks")); return
        pending=f.get("pending_ai_replies"); work=f.get("pending_work",0); blocked=f.get("blocked_work",0)
        if pending is None:
            head="Inbox acknowledgement is recorded; answer and task completion are not verified."
            dot="grey"
        else:
            exp=int(f.get("expired_ai_replies") or 0)   # expired_20261106: old unanswered receipts are not "pending"
            head="Chat delivery: %d AI replies pending; %d engineering jobs waiting; %d blocked%s." % (pending,work,blocked,("; %d unanswered messages older than a day are expired (the 4-5 Nov AI outage), not retried" % exp) if exp else "")
            dot="amber" if pending or work or blocked else "grey"
        rows.append(_row("funder",dot,head,"Acknowledgement, cached replies, task execution and applied code are tracked separately.",f,"checks"))
    def broken():
        for b in S.get("broken") or []:
            if isinstance(b, dict): rows.append(_row("broken", b.get("sev") if b.get("sev") in ("red", "amber") else "amber", plain_broken(b.get("what") or b.get("gap") or b), "", None, "broken"))
        for b in S.get("info") or []:
            if isinstance(b, dict): rows.append(_row("info", "grey", plain_broken(b.get("what") or b), "", None, "info"))
    def numbers():   # dasheli10_20261103 FIGURES (Brett 16:01 "Figures are not right"): every number that shows on two cards must agree
        nl = numbers_light(S)
        rows.append(_row("numbers", "green" if nl["ok"] else "red", nl["txt"], "full list: /api/numbers", dict(bad=nl.get("bad") or []), "checks"))
    def sgp():   # pushhealth_20261103 (Brett 17:49 "someone checking on Singapore's health and fixing as it goes"): veteran_desk/sgp_health.py every 5 min
        g = sgp_info()
        if not g: return
        try: age = (time.time() - dt.datetime.fromisoformat(str(g.get("utc"))).timestamp()) / 60.0
        except Exception: age = None
        if age is None or age > 15:
            rows.append(_row("sgp", "amber", "The Singapore server watcher last checked in %s - it should check every 5 minutes." % mins_words(age), "tfo-thf-sgp-health.timer (every 5 min)", None, "sgp")); return
        fx = g.get("fixes_today") or []; jb = g.get("jobs_today") or []
        lines = ["%s %s" % (str(x.get("wita"))[11:16], x.get("words")) for x in fx[-6:]] + ["%s Simon got a fix job for %s" % (x.get("wita"), x.get("id")) for x in jb[-4:]]
        rows.append(_row("sgp", "green" if g.get("color") == "green" else "grey",   # the red/amber facts below carry the colour (no double count)
                         "Singapore server: %s Checked %s by the watcher%s." % ("all clear." if g.get("color") == "green" else _sgp_counts(g), mins_words(age), " (dry run: it only reports, fixes nothing yet)" if g.get("mode") != "live" else ", which fixes safe things itself"),
                         "safe fixes today: %d · Simon's fix jobs today: %d · tap for the list" % (len(fx), len(jb)), dict(lines=lines or ["no fixes needed today"]), "sgp"))
        for r in g.get("rows") or []:
            if not isinstance(r, dict) or r.get("dup") or r.get("dot") == "green": continue
            rows.append(_row("sgp_" + str(r.get("id")), r.get("dot") if r.get("dot") in ("red", "amber") else "grey", str(r.get("head") or ""), " · ".join(x for x in (str(r.get("sub") or ""), str(r.get("auto") or "")) if x)[:220], None, "sgp"))
        ok = [r for r in g.get("rows") or [] if isinstance(r, dict) and r.get("dot") == "green" and not r.get("dup")]
        if ok: rows.append(_row("sgp_ok", "green", "Also fine: " + "; ".join(str(r.get("head") or "").rstrip(".") for r in ok[:9]) + ".", "", None, "sgp"))
    def pushrow():   # pushhealth_20261103 (A95 "arm scheduled prompts to the traders and admin to push hard to get profit")
        w = push_words()
        if not w: return
        rows.append(_row("push", "grey" if not w["live"] else "green", w["head"], "edit %s (or ask Simon in chat) · last run %s" % (w["file"], w["last_run"] or "-"), dict(table=dict(cols=["push", "when (WITA)", "who", "today"], rows=w["rows"]), lines=[w["how"]]), "checks"))
    def aicost():   # pushhealth_20261103 A46 (HT 21:10 2 Nov "Count AI token cost per seat on the Health tab")
        c = aicost_info(); rs = aicost_rows(c)
        if not rs: return
        t = (c.get("totals") or {}); top = ", ".join("%s %s" % (r[0].split(" (")[0], r[2]) for r in rs[:3])
        rows.append(_row("aicost", "grey", "AI use, last 24 h: %s calls, about %.1f million tokens (estimate). Biggest: %s." % (t.get("calls", "?"), int(t.get("est_tokens") or 0) / 1e6, top),
                         "Claude is on Brett's flat Ultra plan and ChatGPT on its own plan, so no dollar price per call is invented: tokens = text size / 4 · updated %s" % (c.get("wita") or "?"),
                         dict(table=dict(cols=["seat", "AI calls (ok)", "tokens", "share", "AI time"], rows=rs)), "ai"))
    for fn in (engine, killsw, watchdog, overlord, numbers, brains, needsbrett, ai_rows, model_calls, aicost, borrow, paid, feeds, vendors, trade_data, newsrow, urgent, gates, wakes, pushrow, coverage, grokq, funder, broken, sgp): safe(fn)
    return rows
def _sgp_counts(g):
    """healthdesk_20261103: 'N red, M amber (listed below)' counted only the watcher's own facts; its dup facts (feeds, numbers, engine ...) are the rows above."""
    rs = [r for r in (g.get("rows") or []) if isinstance(r, dict)]
    if not rs: return "%d red, %d amber (listed below)." % (g.get("n_red") or 0, g.get("n_amber") or 0)
    own = [r for r in rs if not r.get("dup")]; dup = [r for r in rs if r.get("dup") and r.get("dot") in ("red", "amber")]
    nr = sum(1 for r in own if r.get("dot") == "red"); na = sum(1 for r in own if r.get("dot") == "amber")
    bits = (["%d red, %d amber (listed below)" % (nr, na)] if (nr or na) else ["nothing of its own is red or amber"]) + (["%d more shown in the rows above (%s)" % (len(dup), ", ".join(str(r.get("id")) for r in dup))] if dup else [])
    return "; ".join(bits) + "."
def overall(rows):
    """One dot + one reason for the whole desk: red if anything is red, amber if anything is amber, else green."""
    reds = [r for r in rows if r.get("dot") == "red"]; ambs = [r for r in rows if r.get("dot") == "amber"]
    if reds: return dict(color="red", reason=reds[0]["head"], n_red=len(reds), n_amber=len(ambs))
    if ambs: return dict(color="amber", reason=ambs[0]["head"], n_red=0, n_amber=len(ambs))
    eng = next((r for r in rows if r.get("id") == "engine"), None)
    return dict(color="green", reason=(eng["head"] if eng else "Everything is running.") + " Nothing is broken.", n_red=0, n_amber=0)
def wake_rows(S, now=None):
    now = now or dt.datetime.now(WITA); out = []
    for w in ((S or {}).get("wakes") or [])[:30]:
        if not isinstance(w, dict): continue
        ideas = []
        for s, v in (w.get("cands") or {}).items():
            for x in (v if isinstance(v, list) else [v]):
                ideas.append("%s %s (%s)" % (x[0], side_word(x[1]), x[2]) if isinstance(x, (list, tuple)) and len(x) >= 3 else str(x))
        out.append(dict(id=w.get("wake_id"), when=when(w.get("utc"), now), seats=", ".join(first_name(s) for s in (w.get("seats") or [])) or "—", status=str(w.get("status") or ""), result=str(w.get("result") or "")[:60], ideas=", ".join(ideas)[:160]))
    return out
def health(S, hb=None, ais=None, tick=None, now=None, news=None):
    now = now or dt.datetime.now(WITA); rows = health_rows(S, hb, ais, tick, now, news); ov = overall(rows)
    return dict(rows=rows, color=ov["color"], reason=ov["reason"], n_red=ov["n_red"], n_amber=ov["n_amber"], wakes=wake_rows(S, now), ts=when(now, now), glossary=GLOSSARY,
                links=dict(classic="/classic", raw="/api/state", hub="/hub", vd=((S or {}).get("links") or {}).get("vd_dash"), war_room=((S or {}).get("links") or {}).get("war_room")), built=when((S or {}).get("built_utc"), now), build_secs=(S or {}).get("build_secs"))

# ---- numbers agree (dasheli10_20261103 FIGURES: Brett 16:01 "Figures are not right", 16:13 "Fix the dashboard discrepancies") ------
def _n(v):
    try: v = float(v)
    except (TypeError, ValueError): return None
    return v if math.isfinite(v) else None
def _chk(out, cid, words, a, b, tol=0.5, soft=False):
    a_, b_ = _n(a), _n(b); ok = a_ is not None and b_ is not None and abs(a_ - b_) <= tol
    out.append(dict(id=cid, ok=ok, soft=soft, words=words, a=a, b=b)); return ok
def _trader_cards(S): return [c for c in _cards(S) if c.get("seat") and c.get("seat") != "head_of_desk"]
def numbers_light(S):
    """Cheap checks over ONE /api/state snapshot (no extra file reads): the numbers that appear on two or more cards must be equal.
    today (06:00 WITA clock): leaderboard column as shown = the trader cards as shown (= the big number on Home and on /classic, same formula);
    per seat: card today / since start / book worth = its leaderboard row; since start + book worth: top line = sum of the 11 seats
    (only when the leaderboard lists every trader seat); trades open + orders waiting: Home/Seats (book positions) = Trades tab/classic
    cards (book view) = leaderboard open column."""
    S = S or {}; T = S.get("today") or {}; checks = []; tc = _trader_cards(S)
    lb = [r for r in ((S.get("leaderboard") or {}).get("rows") or []) if isinstance(r, dict)]; L = {r.get("seat"): r for r in lb}
    if not lb or not tc: return dict(ok=True, n=0, n_bad=0, bad=[], checks=[], txt="Number check: nothing to compare yet (no leaderboard rows or no trader cards).")
    miss = sorted({c.get("seat") for c in tc} ^ set(L))
    checks.append(dict(id="lb_rows", ok=not miss, words="every trader card has a leaderboard row" + ((" - missing: %s" % ", ".join(map(str, miss))) if miss else ""), a=len(tc), b=len(L)))
    t_lb = shown_sum(lb, "day_pnl_aud"); t_c = shown_sum(tc, "day_pnl_aud")
    _chk(checks, "today", "today since 06:00 WITA: big number + leaderboard column %s = trader cards %s" % (aud(t_lb, True), aud(t_c, True)), t_lb, t_c, 0)
    bad_seat = []
    for c in tc:
        r = L.get(c.get("seat")) or {}
        for kc, kl, nm in (("day_pnl_aud", "day_pnl_aud", "today"), ("pnl_since_aud", "pnl_since_inception_aud", "since start"), ("equity_aud", "equity_aud", "book worth")):
            a, b = _n(c.get(kc)), _n(r.get(kl))
            if a is None and b is None: continue
            if a is None or b is None or abs(a - b) > 0.5: bad_seat.append("%s %s: card %s vs leaderboard %s" % (first_name(c.get("seat"), c.get("trader")), nm, aud(a, True), aud(b, True)))
    checks.append(dict(id="per_seat", ok=not bad_seat, words="each trader card = its leaderboard row (today, since start, book worth)" + ((": " + "; ".join(bad_seat[:4])) if bad_seat else ""), a=len(tc), b=len(bad_seat)))
    if set(TRADER_SEATS) <= set(L):
        s_lb = sum(float(r.get("pnl_since_inception_aud") or 0) for r in lb); e_lb = sum(float(r.get("equity_aud") or 0) for r in lb)
        _chk(checks, "since", "since start: top line %s = the 11 seats %s" % (aud(T.get("pnl_since_inception_aud"), True), aud(s_lb, True)), T.get("pnl_since_inception_aud"), s_lb, 1.0)
        _chk(checks, "equity", "book worth: top line %s = the 11 seats %s" % (aud(T.get("equity_aud")), aud(e_lb)), T.get("equity_aud"), e_lb, 1.0)
    o1 = sum(1 for c in tc for p in c.get("open_positions") or [] if p.get("status") == "open"); a1 = sum(1 for c in tc for p in c.get("open_positions") or [] if p.get("status") == "armed")
    o2 = sum(len((c.get("book_view") or {}).get("positions") or []) for c in tc); a2 = sum(len((c.get("book_view") or {}).get("armed") or []) for c in tc)
    o3 = sum(int(r.get("open_trades") or 0) for r in lb) if lb and all(r.get("open_trades") is not None for r in lb) else o1
    checks.append(dict(id="open", ok=o1 == o2 == o3, words="trades open: Home %d = Trades tab %d = leaderboard %d" % (o1, o2, o3), a=o1, b=o2))
    checks.append(dict(id="armed", ok=a1 == a2, words="orders waiting to fill: Home %d = Trades tab %d" % (a1, a2), a=a1, b=a2))
    bad = [c for c in checks if not c["ok"]]
    txt = ("Numbers agree (%d checks): today %s since 06:00 WITA = leaderboard column = trader cards; since start %s; book worth %s; %d trades open; %d orders waiting to fill." % (
        len(checks), aud(t_lb, True), aud(T.get("pnl_since_inception_aud"), True), aud(T.get("equity_aud")), o1, a1)) if not bad else ("Numbers DISAGREE on %d of %d checks: %s." % (len(bad), len(checks), "; ".join(c["words"] for c in bad[:3])))
    return dict(ok=not bad, n=len(checks), n_bad=len(bad), bad=[c["words"] for c in bad], checks=checks, txt=txt, today_shown_aud=t_lb, open=o1, armed=a1)
def numbers_check(S, opps=None, now=None, ticker=None):
    """/api/numbers: numbers_light + the tab payloads themselves (Home, Trades, Seats) + the classic page's own formulas + the ticker strip,
    all built from the SAME snapshot S. opps (/api/opps, a fresh read of the book) can be up to ~20 s newer than S: those checks are 'soft'."""
    now = now or dt.datetime.now(WITA); S = S or {}; nl = numbers_light(S); checks = list(nl["checks"])
    try:
        h = home(S, hb={}, ais=[], tick={}, now=now, news={}); t = trades(S, opps=opps, now=now); s = seats(S, hb={}, now=now, reg={})
        checks.append(dict(id="home_today", ok=h["today_txt"] == aud(nl["today_shown_aud"], True) == s["total"]["today_txt"], words="Home big number %s = Seats tab total %s = leaderboard column %s" % (h["today_txt"], s["total"]["today_txt"], aud(nl["today_shown_aud"], True)), a=h["today_txt"], b=s["total"]["today_txt"]))
        checks.append(dict(id="tabs_open", ok=h["open"] == t["counts"]["open"] == s["total"]["open"], words="trades open: Home %s = Trades tab %s = Seats tab %s" % (h["open"], t["counts"]["open"], s["total"]["open"]), a=h["open"], b=t["counts"]["open"]))
        checks.append(dict(id="tabs_armed", ok=h["armed"] == t["counts"]["armed"] == s["total"]["armed"], words="orders waiting to fill: Home %s = Trades tab %s = Seats tab %s" % (h["armed"], t["counts"]["armed"], s["total"]["armed"]), a=h["armed"], b=t["counts"]["armed"]))
        arm = {(r.get("seat"), str(r.get("sym"))) for r in t["rows"] if r.get("kind") == "armed"}; dup = sorted("%s %s" % k for k in arm & {(r.get("seat"), str(r.get("sym"))) for r in t["rows"] if r.get("kind") in ("queued", "idea", "blocked")})
        checks.append(dict(id="no_double", ok=not dup, words="no order is counted twice (waiting to fill AND lined up for the open)" + ((": " + ", ".join(dup)) if dup else ""), a=len(arm), b=len(dup)))
        if opps is not None:
            ko = sum(1 for o in opps if isinstance(o, dict) and o.get("kind") == "open"); ka = sum(1 for o in opps if isinstance(o, dict) and o.get("src") == "book" and o.get("kind") in ("armed", "queued_for_open"))
            checks.append(dict(id="classic_charts", ok=ko == h["open"] and ka == h["armed"], soft=True, words="classic page trades card: %d open / %d armed = Home %s / %s" % (ko, ka, h["open"], h["armed"]), a=[ko, ka], b=[h["open"], h["armed"]]))
        if ticker is not None:
            tx = " ".join(str(i.get("text")) for i in ((ticker.get("summary") or {}).get("items") or []) if isinstance(i, dict) and i.get("k") == "book")
            checks.append(dict(id="ticker", ok=("THF today %s" % h["today_txt"]) in tx, words="ticker strip says 'THF today %s'" % h["today_txt"], a=tx[:160], b=h["today_txt"]))
    except Exception as x: checks.append(dict(id="build", ok=False, words="could not build a tab for the check (%s)" % type(x).__name__))
    hard = [c for c in checks if not c["ok"] and not c.get("soft")]; soft = [c for c in checks if not c["ok"] and c.get("soft")]
    return dict(paper=True, tag=TAG, ok=not hard, built_utc=S.get("built_utc"), checked_wita=now.strftime("%Y-%m-%d %H:%M:%S WITA"), n=len(checks), n_bad=len(hard), n_soft_bad=len(soft),
                txt=nl["txt"] if not hard else "Numbers DISAGREE on %d of %d checks: %s." % (len(hard), len(checks), "; ".join(c["words"] for c in hard[:3])),
                soft_note=("The classic trades card reads the book file directly and can be up to ~20 seconds newer than the rest: " + "; ".join(c["words"] for c in soft)) if soft else "", checks=checks)
def ticker_fresh(T, S):
    """dasheli10_20261103 FIGURES: thf_ticker caches its summary for 30 min and showed 'THF book day -A$2,323' (midnight clock, up to 30 min old)
    under a -A$1,734 headline (06:00 clock). Swap the THF money line, the per-seat lines and the leader line for fresh ones built from the same
    /api/state snapshot as the cards (both clocks named); label the old Veteran Desk line as a separate book with its time."""
    try:
        T = dict(T or {}); sm = dict(T.get("summary") or {}); S = S or {}; Tt = S.get("today") or {}
        lb = [r for r in ((S.get("leaderboard") or {}).get("rows") or []) if isinstance(r, dict)]; fresh = []
        if Tt:
            fresh.append(dict(k="book", text=("THF today %s since 06:00 WITA (the leaderboard total) · book day since midnight WITA %s · book worth %s" % (aud(shown_sum(lb, "day_pnl_aud"), True), aud(Tt.get("today_pnl_aud"), True), aud(Tt.get("equity_aud"))))
                              if lb else ("THF book day since midnight WITA %s · book worth %s" % (aud(Tt.get("today_pnl_aud"), True), aud(Tt.get("equity_aud"))))))
        for c in _cards(S):
            b = c.get("book_view") or {}; a = len(b.get("armed") or []); p = len(b.get("positions") or [])
            if a or p: fresh.append(dict(k="seat", text="%s: %d open, %d order%s waiting to fill (%s)" % (c.get("trader") or c.get("seat"), p, a, "" if a == 1 else "s", ", ".join(str(x.get("ticker")) for x in ((b.get("positions") or []) + (b.get("armed") or []))[:4]))))
        if lb:
            top = sorted(lb, key=lambda r: r.get("rank") or 99)[0]; nm = next((c.get("trader") for c in _cards(S) if c.get("seat") == top.get("seat")), top.get("seat"))
            fresh.append(dict(k="lb", text="Leaderboard leader: %s, today %s since 06:00 WITA" % (nm, aud(top.get("day_pnl_aud"), True))))
        keep = []
        for i in (sm.get("items") or []):
            if not isinstance(i, dict): continue
            i = dict(i); tx = str(i.get("text") or "")
            if i.get("k") in ("seat", "lb") or (i.get("k") == "book" and tx.startswith("THF")): continue
            if i.get("k") == "book" and tx.startswith("VD "): i["text"] = "Old Veteran Desk book (separate from THF): day since midnight WITA" + tx[3:].replace("book day", "", 1) + " · as of " + when(sm.get("built_utc"))
            keep.append(i)
        sm["items"] = fresh[:1] + [i for i in keep if i.get("k") == "book"] + fresh[1:] + [i for i in keep if i.get("k") != "book"]; sm["fresh_utc"] = S.get("built_utc"); T["summary"] = sm; T["urgent"] = kill_fix(T.get("urgent") or [])   # killsw_20261103
        return T
    except Exception: return T

# ---- api / page --------------------------------------------------------------------------------------------------------------
def eli10(S, tab="all", now=None):
    tab = str(tab or "all").lower(); out = dict(paper=True, tag=TAG, built_utc=(S or {}).get("built_utc"))   # dasheli10_20261103: built_utc = which /api/state snapshot these rows came from
    if tab in ("home", "all"): out["home"] = home(S, now=now)
    if tab in ("trades", "all"): out["trades"] = trades(S, now=now)
    if tab in ("seats", "all"): out["seats"] = seats(S, now=now)
    if tab in ("health", "all"): out["health"] = health(S, now=now)
    if tab == "all": out["numbers"] = {k: v for k, v in numbers_light(S).items() if k != "checks"}
    return out
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
def page_html(S=None, path="/"):
    """tools/thf_eli10.html with the js inlined and the Home JSON inlined (one request = first paint)."""
    h = _file("thf_eli10.html"); js = _file("thf_eli10.js")
    try: h0 = json.dumps(home(S or {}), default=str)
    except Exception as x: h0 = json.dumps(dict(error=type(x).__name__))
    h0 = h0.replace("</", "<\\/")
    seat = ""
    m = re.match(r"^/desk/([a-z_]+)", str(path or ""))
    if m and m.group(1) not in ("phone", "m", "chat"): seat = m.group(1)
    return h.replace("<!--HOME0-->", h0, 1).replace("<!--SEAT0-->", seat, 1).replace("<!--ELI10_JS-->", "<script>" + js + "</script>", 1)
if __name__ == "__main__":
    import sys, urllib.request
    S = json.loads(urllib.request.urlopen("http://127.0.0.1:28870/api/state", timeout=30).read())
    a = sys.argv[1] if len(sys.argv) > 1 else "home"
    out = eli10(S, a); print(json.dumps(out, indent=1, default=str)[:8000]); print("bytes", {k: len(json.dumps(v, default=str)) for k, v in out.items()})
