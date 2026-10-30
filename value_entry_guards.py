#!/usr/bin/env python3
"""value_entry_guards.py — HT rulings 30 Oct 2026 22:45 WITA for value_deploy NEW entries only. PAPER ONLY.
(c) REENTRY: no re-entry within REENTRY_BLOCK_TRADING_DAYS (20) trading days of a stop-out in this book
    (exit_rule/action STOP*, stop_out, HT rail stop). Sources: ledger closed[*].exit_fills + HT facts log
    (reports/tfo_head_trader_facts.jsonl lines 'STOP <SYM> fundamental_book ...'). Override only by a
    non-empty logged new_thesis (value_rank row field or reports/value_reentry_override.json).
(d) EARNINGS: no new entry within EARNINGS_BLOCK_TRADING_DAYS (5) trading days before a known EODHD
    report date. Cached in reports/earnings_cache.json (24 h). Unknown -> allowed, tagged earnings_unknown.
Trading days = weekdays (holidays not modelled; stated on every reason)."""
import datetime as dt, json, os, re
BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
REENTRY_BLOCK_TRADING_DAYS = 20
EARNINGS_BLOCK_TRADING_DAYS = 5
CACHE = os.path.join(REPORTS, "earnings_cache.json")
OVERRIDE = os.path.join(REPORTS, "value_reentry_override.json")
FACTS = os.path.join(REPORTS, "tfo_head_trader_facts.jsonl")
CACHE_TTL_S = 86400
# stop-out exits only (HT: stop_out / STOP_ORIG / HT rail stop). Not close_runner, not IDLE_STOP plan exits.
_STOP = re.compile(r"^(stop_out|STOP_ORIG|STOP|STOP_RAIL|RAIL_STOP|HT_RAIL_STOP)$", re.I)

def weekdays_between(d0, d1):
    """Weekdays strictly after d0 up to and including d1 (0 if d1 <= d0)."""
    n, d = 0, d0
    while d < d1:
        d += dt.timedelta(days=1)
        if d.weekday() < 5: n += 1
    return n

def stop_outs(led, facts_path=FACTS):
    """{sym: (date_iso, evidence)} — latest stop-out per symbol."""
    out = {}
    def put(s, d, ev):
        if s and d and (s not in out or d > out[s][0]): out[s] = (d, ev)
    closed = led.get("closed") or {}
    items = closed.items() if isinstance(closed, dict) else [((c or {}).get("symbol"), c) for c in closed if isinstance(c, dict)]
    for s, c in items:
        for f in (c or {}).get("exit_fills") or []:
            if any(_STOP.match(str(f.get(k) or "")) for k in ("exit_rule", "action")):
                put(f.get("sym") or s, str(f.get("utc") or "")[:10], "ledger closed.%s exit_fills %s %s %s" % (s, f.get("ticket_id"), f.get("exit_rule") or f.get("action"), f.get("utc")))
    try:
        for ln in open(facts_path):
            try: j = json.loads(ln)
            except Exception: continue
            m = re.match(r"\s*STOP\s+(\S+)\s+fundamental_book", str(j.get("fact") or ""))
            if m:   # 'syd' is Sydney local wall time -> UTC date
                try:
                    from zoneinfo import ZoneInfo
                    d = dt.datetime.strptime(str(j.get("syd"))[:16], "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Australia/Sydney")).astimezone(dt.timezone.utc).date().isoformat()
                except Exception:
                    continue
                put(m.group(1), d, "HT facts %s (%s Sydney)" % (j.get("ticket"), j.get("syd")))
    except FileNotFoundError:
        pass
    return out

def _overrides():
    try: return json.load(open(OVERRIDE))
    except Exception: return {}

def reentry_block(sym, row, stops, today=None):
    """(reason or None, info dict for the ticket)."""
    if sym not in stops: return None, {}
    today = today or dt.date.today()
    d, ev = stops[sym]
    n = weekdays_between(dt.date.fromisoformat(d), today)
    if n >= REENTRY_BLOCK_TRADING_DAYS: return None, {"prior_stop_out": d, "prior_stop_evidence": ev}
    nt = (row or {}).get("new_thesis") or (_overrides().get(sym) or {}).get("new_thesis")
    if isinstance(nt, str) and nt.strip():
        return None, {"reentry_override": {"new_thesis": nt.strip(), "prior_stop_out": d, "evidence": ev}}
    return ("re-entry blocked: stopped out %s (%d of %d trading days, weekdays; %s); override needs a logged new_thesis"
            % (d, n, REENTRY_BLOCK_TRADING_DAYS, ev)), {}

def _load_cache():
    try: return json.load(open(CACHE))
    except Exception: return {}

def _save_cache(c):
    os.makedirs(REPORTS, exist_ok=True); tmp = CACHE + ".tmp"
    json.dump(c, open(tmp, "w"), indent=1, sort_keys=True); os.replace(tmp, CACHE)

def earnings_map(symbols, led=None, today=None):
    """{sym: next_earnings dict or None}. Ledger next_earnings, then cache (<24 h), then one EODHD batch. Never guessed."""
    today = today or dt.date.today(); t_iso = today.isoformat()
    cache, now = _load_cache(), dt.datetime.now(dt.timezone.utc)
    out, need = {}, []
    held = (led or {}).get("positions") or {}
    for s in symbols:
        c = cache.get(s)
        if c:
            try: fresh = (now - dt.datetime.fromisoformat(c["checked_utc"].replace("Z", "+00:00"))).total_seconds() < CACHE_TTL_S
            except Exception: fresh = False
            if fresh and (c.get("ev") is None or c["ev"].get("date", "") >= t_iso):
                out[s] = c.get("ev"); continue
        ne = (held.get(s) or {}).get("next_earnings")
        if ne and ne.get("date", "") >= t_iso: out[s] = ne; continue
        need.append(s)
    if need:
        import paid_feeds
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ"); errs = []
        def _fetch(chunk):
            try:
                return paid_feeds.eodhd_next_earnings(chunk, today=t_iso), None
            except Exception as e:
                return None, "%s: %s" % (type(e).__name__, str(e)[:80])
        for i in range(0, len(need), 20):          # chunked: long batch URLs 404
            chunk = need[i:i+20]
            got, err = _fetch(chunk)
            if got is None:                         # split to singles so one bad code can't hide the rest
                got = {}
                for s1 in chunk:
                    g1, e1 = _fetch([s1])
                    if g1 is None: errs.append("%s %s" % (s1, e1)); continue
                    got.update(g1); cache[s1] = {"ev": g1.get(s1), "checked_utc": stamp}
                for s1 in chunk:
                    out[s1] = got.get(s1)
                continue
            for s1 in chunk:
                out[s1] = got.get(s1); cache[s1] = {"ev": got.get(s1), "checked_utc": stamp}
        _save_cache(cache)                          # failed names are NOT cached (retried next run)
        if errs: out["_error"] = "; ".join(errs)[:300]
    return out

def earnings_block(sym, ev, today=None):
    """(reason or None, info for the ticket)."""
    today = today or dt.date.today()
    if not ev or not ev.get("date"):
        return None, {"earnings_unknown": True, "earnings_check": "no EODHD date for %s (not guessed)" % sym}
    d = dt.date.fromisoformat(ev["date"])
    n = weekdays_between(today, d)
    info = {"next_earnings": ev, "trading_days_to_earnings": n}
    if d >= today and n <= EARNINGS_BLOCK_TRADING_DAYS:
        return ("earnings-proximity: %s report %s (%s) is %d trading day(s) away (block <= %d; %s)"
                % (sym, ev["date"], ev.get("timing"), n, EARNINGS_BLOCK_TRADING_DAYS, ev.get("source"))), info
    return None, info
