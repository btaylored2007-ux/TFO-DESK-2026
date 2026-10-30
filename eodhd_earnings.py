"""2026-10-30 asiafix (TFO approved 21:25 WITA, PAPER ONLY): EODHD calendar/earnings -> next report dates for the
JP / ASX / LSE hunt event files. DATA ONLY: no gate logic here. A name with no EODHD date stays absent, so the
clearance earnings gate still fails closed on UNKNOWN. Scope: jp, asx, lse only (HK/KR/TW/SG paused 19:56). Py3.9-safe."""
import datetime as dt, json, os
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "state", "calendar", "eodhd_earnings")
SFX = {"jp": (".TSE", ".T"), "asx": (".AU", ".AX"), "lse": (".LSE", ".L")}   # EODHD exchange code -> Yahoo suffix used by the desk
WINDOW_D = 150
def parse(rows, mkt, today):
    """{yahoo_sym: earliest report_date ISO >= today}. Rows for other exchanges / bad dates / past dates are ignored."""
    eo, ys = SFX[mkt]; out = {}
    for x in rows or []:
        code = str((x or {}).get("code") or "")
        if not code.endswith(eo): continue
        try: d = dt.date.fromisoformat(str(x.get("report_date"))[:10])
        except Exception: continue
        if d < today: continue
        s = code[: -len(eo)].upper() + ys
        if s not in out or d.isoformat() < out[s]: out[s] = d.isoformat()
    return out
def fetch_rows(today, fetch=None):
    """All-exchange EODHD rows for [today, today+WINDOW_D], cached per day. [] on any error (-> names stay UNKNOWN)."""
    p = os.path.join(CACHE, "rows_%s.json" % today.isoformat())
    try: return json.load(open(p))
    except Exception: pass
    try:
        if fetch is None:
            import fundamental_feed as _ff
            fetch = lambda a, b: _ff._eodhd("calendar/earnings", {"from": a, "to": b})
        r = fetch(today.isoformat(), (today + dt.timedelta(days=WINDOW_D)).isoformat())
        if not r.ok: return []
        rows = (r.data or {}).get("earnings") or []
        if rows:
            os.makedirs(CACHE, exist_ok=True); tmp = p + ".tmp"; json.dump(rows, open(tmp, "w")); os.replace(tmp, p)
        return rows
    except Exception: return []
def next_dates(mkt, today=None, fetch=None):
    if mkt not in SFX: return {}
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return parse(fetch_rows(today, fetch), mkt, today)
def fill(earn, eod, syms=None, today=None):
    """Merge: keep a Yahoo date that is today-or-later unless EODHD has an EARLIER future date (earlier = more conservative
    for the hold-window block). Yahoo missing/past -> EODHD date if any. No EODHD date -> Yahoo value unchanged (possibly absent)."""
    today = (today or dt.datetime.now(dt.timezone.utc).date()).isoformat(); out = dict(earn or {})
    for s in (syms if syms is not None else list(eod)):
        e = eod.get(s)
        if not e: continue
        y = str(out.get(s) or "")[:10]
        if not y or y < today or e < y: out[s] = e
    return out
