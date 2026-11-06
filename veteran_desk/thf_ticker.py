"""THF desk ticker + market clocks (read-only, PAPER). tfo_ticker_20261031 (Brett 20:01/20:04 WITA).
GET /api/ticker -> {clocks, summary (rebuilt every 30 min), urgent (every request)}. Sources: dashboard state()
(book cards, leaderboard, broken), reports/daily_profit_target_status.json (firm day P&L, USD->AUD via firm_risk_record
audusd), state/veteran_desk/news.json + news_alerts.jsonl, state/catalyst/{asx,us}.json, state/catalyst_stream/heartbeat.json,
state/firm_risk_record.json, kill files. Paths are relative to the desk root so the same file runs on the Mac or tfo-sgp1.
Any missing file -> that item is omitted (never invented). Never writes anything."""
import datetime as dt, json, os, time
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar")
SUMMARY_TTL_S = 1800
# Exchange holidays (full-day closures), hand table through Mar-2027. Half days not modelled.
HOL = {
 "ASX": "2026-12-25 2026-12-28 2027-01-01 2027-01-26 2027-03-26 2027-03-29",
 "TYO": "2026-11-03 2026-11-23 2026-12-31 2027-01-01 2027-01-11 2027-02-11 2027-02-23 2027-03-22",
 "HKG": "2026-12-25 2027-01-01 2027-02-08 2027-02-09 2027-02-10 2027-03-26 2027-03-29",
 "LON": "2026-12-25 2026-12-28 2027-01-01 2027-03-26 2027-03-29",
 "EUR": "2026-12-24 2026-12-25 2026-12-31 2027-01-01 2027-03-26 2027-03-29",
 "US":  "2026-11-26 2026-12-25 2027-01-01 2027-01-18 2027-02-15 2027-03-26",
}
HOL = {k: set(v.split()) for k, v in HOL.items()}
# (key, label, tz, sessions [(open,close) local HH:MM])
MKTS = [("ASX", "ASX", "Australia/Sydney", [("10:00", "16:00")]),
        ("TYO", "Tokyo", "Asia/Tokyo", [("09:00", "11:30"), ("12:30", "15:30")]),
        ("HKG", "Hong Kong", "Asia/Hong_Kong", [("09:30", "12:00"), ("13:00", "16:00")]),
        ("LON", "London", "Europe/London", [("08:00", "16:30")]),
        ("EUR", "Europe", "Europe/Berlin", [("09:00", "17:30")]),
        ("US", "US", "America/New_York", [("09:30", "16:00")])]
EU_SFX = (".PA", ".DE", ".SW", ".AS", ".MI", ".MC", ".BR", ".ST", ".CO", ".HE", ".OL", ".VI", ".LS", ".IR", ".F")

def _rj(rel, d=None):
    try: return json.load(open(os.path.join(BASE, rel)))
    except Exception: return d
def _tail(rel, n):
    try: return [json.loads(l) for l in open(os.path.join(BASE, rel)).read().splitlines()[-n:] if l.strip()]
    except Exception: return []
def _t(s):
    try:
        x = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")); return x if x.tzinfo else x.replace(tzinfo=UTC)
    except Exception: return None

def market_of(sym):
    s = str(sym or "").upper()
    if s.startswith("KRAKEN:") or s.startswith("HL:") or s.endswith("-USD") or s.endswith("USDT"): return "CRYPTO"
    if s.startswith("OANDA:") or s.endswith("=X") or s.endswith("=F") or ("_" in s and len(s) == 7): return "FX"
    if s.endswith(".AX"): return "ASX"
    if s.endswith(".T"): return "TYO"
    if s.endswith(".HK"): return "HKG"
    if s.endswith(".L"): return "LON"
    if s.endswith(EU_SFX): return "EUR"
    return "US"

def _session_edges(tzname, sessions, hol, day):
    tz = ZoneInfo(tzname)
    if day.weekday() >= 5 or day.isoformat() in hol: return []
    out = []
    for o, c in sessions:
        oh, om = map(int, o.split(":")); ch, cm = map(int, c.split(":"))
        out.append((dt.datetime(day.year, day.month, day.day, oh, om, tzinfo=tz).astimezone(UTC),
                    dt.datetime(day.year, day.month, day.day, ch, cm, tzinfo=tz).astimezone(UTC)))
    return out

def clock(key, label, tzname, sessions, now):
    tz = ZoneInfo(tzname); today = now.astimezone(tz).date(); hol = HOL.get(key, set())
    edges = []
    for i in range(-1, 12): edges += _session_edges(tzname, sessions, hol, today + dt.timedelta(days=i))
    for o, c in edges:
        if o <= now < c:
            return dict(key=key, label=label, tz=tzname, open=True, next_change_utc=c.isoformat(), next_kind="close")
    nxt = min((o for o, c in edges if o > now), default=None)
    ld = now.astimezone(tz).date()
    why = "holiday" if ld.isoformat() in hol else ("weekend" if ld.weekday() >= 5 else ("lunch" if any(o.astimezone(tz).date() == ld for o, c in edges if o > now) and any(c <= now and c.astimezone(tz).date() == ld for o, c in edges) else "closed"))
    return dict(key=key, label=label, tz=tzname, open=False, why=why, next_change_utc=nxt.isoformat() if nxt else None, next_kind="open")

def fx_clock(now):
    ny = ZoneInfo("America/New_York"); l = now.astimezone(ny); wd = l.weekday()
    sun_open = lambda d: dt.datetime(d.year, d.month, d.day, 17, 0, tzinfo=ny).astimezone(UTC)
    d0 = l.date() - dt.timedelta(days=(wd + 1) % 7)           # most recent Sunday (NY)
    o = sun_open(d0); c = sun_open(d0 + dt.timedelta(days=5))  # Fri 17:00 NY
    if o <= now < c: return dict(key="FX", label="FX", tz="America/New_York", open=True, next_change_utc=c.isoformat(), next_kind="close")
    nxt = o if now < o else sun_open(d0 + dt.timedelta(days=7))
    return dict(key="FX", label="FX", tz="America/New_York", open=False, why="weekend", next_change_utc=nxt.isoformat(), next_kind="open")

def clocks(now=None, armed_by_mkt=None):
    now = now or dt.datetime.now(UTC)
    out = [clock(*m, now) for m in MKTS] + [fx_clock(now), dict(key="CRYPTO", label="Crypto", tz="UTC", open=True, next_change_utc=None, next_kind="24/7")]
    for c in out: c["armed"] = (armed_by_mkt or {}).get(c["key"], 0)
    return out

def _cards(S): return [c for c in (S.get("cards") or []) if isinstance(c, dict)]
def _book(c): return c.get("book_view") or {}

TICKER_BOOKS = [("VD", "reports/veteran_desk_book.json"), ("THF", "state/thf/thf_book.json")]

def summary(S, now):
    items = []
    # tfo-ticker-bookpnl-1031: NEVER show PROXY/USD-converted firm figures. Booked day P&L per book,
    # read from the same book files the 'How are we doing today?' card uses (today_pnl_aud), labelled by book.
    for lbl, rel in TICKER_BOOKS:
        b = _rj(rel, {}) or {}
        v = b.get("today_pnl_aud")
        if v is None or str(b.get("status") or "").upper() == "PROXY": continue
        v = float(v); day = b.get("day") or ""
        stale = "" if day in (now.astimezone(WITA).date().isoformat(), now.date().isoformat()) else " (book day %s)" % day
        items.append(dict(k="book", text="%s book day %sA$%s · equity A$%s%s" % (lbl, "+" if v >= 0 else "−", f"{abs(v):,.0f}", f"{float(b.get('equity_aud') or 0):,.0f}", stale)))
    for c in _cards(S):
        b = _book(c); a = len(b.get("armed") or []); p = len(b.get("positions") or [])
        if a or p:
            names = [x.get("ticker") for x in (b.get("positions") or []) + (b.get("armed") or [])][:4]
            items.append(dict(k="seat", text="%s: %d filled/held, %d armed (%s)" % (c.get("trader") or c.get("seat"), p, a, ", ".join(map(str, names)))))
    lb = (S.get("leaderboard") or {}).get("rows") or []
    if lb:
        top = sorted(lb, key=lambda r: r.get("rank") or 99)[0]
        nm = next((c.get("trader") for c in _cards(S) if c.get("seat") == top.get("seat")), top.get("seat"))
        items.append(dict(k="lb", text="Leaderboard leader: %s day %sA$%s" % (nm, "+" if (top.get("day_pnl_aud") or 0) >= 0 else "−", f"{abs(top.get('day_pnl_aud') or 0):,.0f}")))
    nxt = sorted([c for c in clocks(now) if not c["open"] and c.get("next_change_utc")], key=lambda c: c["next_change_utc"])[:3]
    for c in nxt:
        t = _t(c["next_change_utc"]); items.append(dict(k="open", text="Next open: %s %s WITA" % (c["label"], t.astimezone(WITA).strftime("%a %H:%M"))))
    names = set()
    for c in _cards(S):
        for x in (_book(c).get("positions") or []) + (_book(c).get("armed") or []): names.add(str(x.get("ticker")))
    news = []
    nj = _rj("state/veteran_desk/news.json", {}) or {}
    for sym, hs in (nj.get("asset_tags") or {}).items():
        if sym in names:
            for h in hs or []: news.append((h.get("published_utc") or "", sym, h.get("source") or "", h.get("title") or ""))
    for mk in ("asx", "us"):
        cj = _rj("state/catalyst/%s.json" % mk, {}) or {}
        for sym, rec in (cj.get("names") or {}).items():
            if sym in names:
                for it in (rec or {}).get("items") or []: news.append((it.get("when") or "", sym, "ASX ann" if mk == "asx" else "US news", it.get("title") or ""))
    seen = set()
    for when, sym, src, title in sorted(news, reverse=True):
        if (sym, title) in seen: continue
        seen.add((sym, title))
        if len(seen) > 6: break
        items.append(dict(k="news", text="%s · %s: %s" % (sym, src, title[:140])))
    if not any(i["k"] == "news" for i in items): items.append(dict(k="news", text="No fresh news on held/armed names (%d names)" % len(names)))
    return dict(built_utc=now.isoformat(), next_refresh_utc=(now + dt.timedelta(seconds=SUMMARY_TTL_S)).isoformat(), items=items)

def urgent(S, now):
    out = []; win = now - dt.timedelta(hours=2)
    for c in _cards(S):
        b = _book(c)
        for x in b.get("closed_today") or []:
            t = _t(x.get("closed_utc"))
            if x.get("why") == "stop" and t and t >= win:
                out.append(dict(k="stop", text="STOP HIT %s %s (%s) %sA$%s" % (x.get("ticker"), x.get("side"), c.get("trader"), "+" if (x.get("pnl_aud") or 0) >= 0 else "−", f"{abs(x.get('pnl_aud') or 0):,.0f}"), utc=t.isoformat()))
        for x in b.get("positions") or []:
            if (x.get("hours_in") or 99) < 2 and (x.get("notional_aud") or 0) >= 150000:
                out.append(dict(k="fill", text="BIG FILL %s %s A$%s (%s)" % (x.get("ticker"), x.get("side"), f"{x.get('notional_aud'):,.0f}", c.get("trader"))))
    fr = ((_rj("state/firm_risk_record.json", {}) or {}).get("firm") or {})
    if fr.get("new_entries_halted") or fr.get("daily_freeze") or fr.get("firm_level") not in (None, "OK"):
        # tfo_layout_20261102: plain-English firm risk line + record time (no raw python values)
        try: _ft = dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE, "state/firm_risk_record.json")), WITA).strftime("%H:%M WITA")
        except Exception: _ft = "?"
        _bits = []
        if fr.get("firm_level") == "UNMEASURED" or fr.get("firm_drawdown_pct") is None: _bits.append("firm risk not measured yet")
        elif fr.get("firm_level") not in (None, "OK"): _bits.append("firm risk level %s, drawdown %.2f%%" % (fr.get("firm_level"), float(fr.get("firm_drawdown_pct"))))
        if fr.get("new_entries_halted"): _bits.append("new entries paused")
        if fr.get("daily_freeze"): _bits.append("daily risk freeze on")
        out.append(dict(k="kill", text="FIRM RISK: %s (as of %s)" % ("; ".join(_bits) or "check firm risk", _ft)))
    for rel in ("state/thf/ENGINE_KILL", "state/vd_engine/ENGINE_KILL", "ENGINE_KILL", "state/veteran_desk/KILL_ALERT.json"):
        if os.path.exists(os.path.join(BASE, rel)): out.append(dict(k="kill", text="KILL SWITCH ON: " + rel))
    for b in S.get("broken") or []:
        if (b.get("sev") or "") in ("red", "crit", "critical"): out.append(dict(k="feed", text="FEED/HEALTH: " + str(b.get("what"))[:140]))
    hb = _rj("state/catalyst_stream/heartbeat.json", {}) or {}
    t = _t(hb.get("utc"))
    if hb and (not t or (now - t).total_seconds() > 300): out.append(dict(k="feed", text="NEWS STREAM DOWN (heartbeat %s)" % (t.astimezone(WITA).strftime("%H:%M WITA") if t else "missing")))
    for v in ("asx_announcements", "fmp_news", "massive_news"):
        st = ((hb.get("vendors") or {}).get(v) or {}).get("status")
        # tfo_newsfallback_20261101 (ported): one headline vendor failing is not an outage while another headline vendor is ok
        if st == "fail" and not any((((hb.get("vendors") or {}).get(x) or {}).get("status") == "ok") for x in ("fmp_news", "uw_news", "massive_news", "eodhd_news", "fmp_press") if x != v): out.append(dict(k="feed", text="NEWS FEED %s failing (%s)" % (v, ((hb.get("vendors") or {}).get(v) or {}).get("err"))))
    eng = (S.get("health") or {}).get("engine") or {}
    te = _t(eng.get("utc"))
    if eng and te and (now - te).total_seconds() > 900: out.append(dict(k="feed", text="ENGINE heartbeat stale since %s" % te.astimezone(WITA).strftime("%H:%M WITA")))
    return out

_SUM = {"t": 0, "v": None}
def ticker(S):
    now = dt.datetime.now(UTC)
    if _SUM["v"] is None or time.time() - _SUM["t"] > SUMMARY_TTL_S:
        _SUM["v"] = summary(S, now); _SUM["t"] = time.time()
    armed = {}
    for c in _cards(S):
        for x in _book(c).get("armed") or []:
            m = market_of(x.get("ticker")); armed[m] = armed.get(m, 0) + 1
    return dict(paper_only=True, utc=now.isoformat(), clocks=clocks(now, armed), summary=_SUM["v"], urgent=urgent(S, now))
