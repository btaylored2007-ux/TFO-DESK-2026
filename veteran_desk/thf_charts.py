"""tfo_thfcharts_20261031 (Brett pt2421u 18:38 WITA): live charts for every THF armed/queued opportunity + open trade, with desk/seat name. Read-only, PAPER."""
import json, os, sqlite3, threading, time, urllib.request, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = os.path.join(BASE, "state", "vd_engine")
NAMES = {'crypto': 'Kai Navarro', 'news_filings': 'Quinn Mercer', 'essentials_utilities': 'Nora Whitlock', 'trend': 'Dex Romano · Trend', 'price_action': 'Dex Romano · Price Action', 'macro': 'Theo Ashby', 'energy': 'Rex Calder', 'tech_ai_infra': 'Nico Hart', 'banks': 'Victor Crane', 'fx_commodities': 'Lena Voss', 'healthcare': 'Iris Mendel'}
# One loader per symbol prevents a cold-cache burst from repeating provider I/O.
# Waiting and provider calls happen outside the map lock, so other charts stay fast.
from concurrent.futures import Future
_C = {}; _L = threading.Lock(); _INFLIGHT = {}
_CHART_WAIT_S = 12.0

def desk_name(seat):
    try:
        from veteran_desk import vd_engine as V; return V.SEATS[seat]["name"]
    except Exception: return NAMES.get(seat, seat)
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def opps(book_path):
    """Every THF armed / queued / open trade from the THF book, plus engine A/A+/B candidates pending or queued for the next open."""
    B = _rj(book_path, {}) or {}; out = []; seen = set()
    for seat, sb in (B.get("seats") or {}).items():
        if not isinstance(sb, dict): continue
        for p in (sb.get("positions") or {}).values():
            st = p.get("status")
            if st not in ("armed", "open"): continue
            kind = "open" if st == "open" else ("queued_for_open" if p.get("queued_for_open") else "armed")
            seen.add((seat, str(p.get("ticker")).upper()))
            out.append(dict(kind=kind, seat=seat, desk_name=(p.get("tags") or {}).get("desk_name") or desk_name(seat), sym=p.get("ticker"), side=p.get("side"), grade=p.get("grade"),
                            entry=p.get("fill") or p.get("entry"), stop=p.get("stop"), target=p.get("target"), units=p.get("shares"), risk_aud=p.get("risk_aud"), risk_pct=p.get("risk_pct"),
                            lift_tag=p.get("lift_tag"), review=(_rj(os.path.join(os.path.dirname(book_path), "arm_reviews.json"), {}) or {}).get(p.get("id")), id=p.get("id"), since=p.get("filled_utc") or p.get("armed_utc"), src="book"))
    Bs = _rj(os.path.join(ST, "broker_state.json"), {}) or {}
    for seat, P in (Bs.get("pending") or {}).items():
        for sym, x in (P or {}).items():
            if (seat, sym.upper()) in seen: continue
            seen.add((seat, sym.upper())); out.append(_cand(seat, x, "pending_wake"))
    for f in sorted(os.listdir(os.path.join(ST, "top10"))) if os.path.isdir(os.path.join(ST, "top10")) else []:
        seat = f[:-5]
        if seat == "head_of_desk": continue
        for x in (_rj(os.path.join(ST, "top10", f), {}) or {}).get("top10", []):
            if not (x.get("grade") in ("A", "A+") or (x.get("grade") == "B" and seat == "crypto")) or (seat, str(x["sym"]).upper()) in seen: continue
            why = str(x.get("wake_block") or x.get("why_no_wake") or "")
            k = "engine_blocked" if "earnings" in why else ("engine_queued_for_open" if x.get("market_open") is False else "engine_candidate")
            seen.add((seat, str(x["sym"]).upper())); out.append(_cand(seat, x, k))
    rank = {"open": 0, "armed": 1, "queued_for_open": 2, "pending_wake": 3, "engine_queued_for_open": 4, "engine_candidate": 5, "engine_blocked": 6}
    out.sort(key=lambda o: (rank.get(o["kind"], 7), {"A+": 0, "A": 1, "B": 2}.get(o.get("grade"), 3)))
    return out
def _cand(seat, x, kind):
    e, s = x.get("close"), x.get("stop"); t = None
    try: t = e + 2 * (e - s)   # indicative 2R target; the book sets the final target at arm
    except Exception: pass
    return dict(kind=kind, seat=seat, desk_name=desk_name(seat), sym=x.get("sym"), side=x.get("side"), grade=x.get("grade"), entry=e, stop=s, target=t, indicative=True,
                why=x.get("wake_block") or x.get("why_no_wake"), src="engine")
def _get(url, t=8):
    try:
        try:
            import sys; sys.path.insert(0, BASE); import kraken_dns; kraken_dns.install()
        except Exception: pass
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "thf-dash"}), timeout=t).read())
    except Exception: return None
WS = {"XBTUSD": "BTC/USD", "XXBTZUSD": "BTC/USD", "BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD", "XETHZUSD": "ETH/USD"}
def _crypto(sym):
    pair = sym.split(":", 1)[1].upper(); wp = WS.get(pair); pts = []; src = None
    if wp:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % os.path.join(BASE, "reports", "ws_tape.sqlite"), uri=True, timeout=5)
            since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
            rows = c.execute("SELECT utc_ts, price FROM trades WHERE pair=? AND utc_ts>=? ORDER BY utc_ts", (wp, since)).fetchall(); c.close()
            bk = {}
            for u, p in rows:
                m = int(dt.datetime.fromisoformat(u.replace("Z", "+00:00")).timestamp() // 60 * 60); bk[m] = p
            pts = sorted(bk.items()); src = "kraken ws_tape"
        except Exception: pts = []
    if len(pts) < 5:
        js = _get("https://api.kraken.com/0/public/OHLC?interval=5&pair=" + pair) or {}
        r = [v for k, v in (js.get("result") or {}).items() if k != "last"]
        if r: pts = [(int(b[0]), float(b[4])) for b in r[0][-144:]]; src = "kraken OHLC 5m"
    if wp:   # newest tick from ws_tape for the live dot
        pass
    return pts, src
def _equity(sym):
    pts, src = [], None
    try:
        from veteran_desk import intraday_store as IS
        c = IS._ro()
        if c:
            rows = c.execute("SELECT ts, c FROM bars WHERE sym=? AND tf='5m' ORDER BY ts DESC LIMIT 160", (sym,)).fetchall(); c.close()
            pts = sorted((int(t // 1000 if t > 1e11 else t), p) for t, p in rows); src = "intraday 5m (paid)"
    except Exception: pts = []
    if len(pts) < 5:
        try:
            from veteran_desk import data_layer as DL
            pts = [(int(r[0]), float(r[4])) for r in DL.intraday(sym, interval="5m")][-160:]; src = "data_layer 5m (fallback)"
        except Exception: pts = []
    return pts, src
def chart(sym):
    sym = str(sym or "").strip(); cr = sym.upper().startswith("KRAKEN:")
    ttl = 5 if cr else 60
    with _L:
        hit = _C.get(sym)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        pending = _INFLIGHT.get(sym)
        owner = pending is None
        if owner:
            pending = Future()
            _INFLIGHT[sym] = pending
    if not owner:
        # A stalled provider cannot hold follower HTTP threads indefinitely.
        # Keep the original loader active; never duplicate its uncertain work.
        return pending.result(timeout=_CHART_WAIT_S)
    try:
        pts, src = _crypto(sym) if cr else _equity(sym)
        out = dict(sym=sym, src=src or "no data", pts=pts,
                   last=pts[-1][1] if pts else None,
                   asof=pts[-1][0] if pts else None,
                   fetched=int(time.time()), refresh_s=ttl)
    except BaseException as error:
        with _L:
            _INFLIGHT.pop(sym, None)
        pending.set_exception(error)
        raise
    with _L:
        _C[sym] = (time.monotonic(), out)
        _INFLIGHT.pop(sym, None)
    pending.set_result(out)
    return out

def desk(seat, book_path):
    """tfo_thfdesks_20261031 (Brett 18:49): per-trader desk analytics from the THF book + lesson loop (read-only). Reviews are advisory; nothing here gates a trade."""
    B = _rj(book_path, {}) or {}; sb = (B.get("seats") or {}).get(seat) or {}
    sd = os.path.dirname(book_path); rows = []
    try: rows = [json.loads(x) for x in open(os.path.join(sd, "lessons", seat + ".jsonl")).read().splitlines() if x.strip()]
    except Exception: pass
    closed = [c for c in (sb.get("closed") or []) if c.get("fill") is not None]
    def agg(rs):
        n = len(rs)
        return dict(n=n, win_rate=round(sum(1 for r in rs if r.get("win")) / n, 3) if n else None, avg_R=round(sum(float(r.get("R_banked") or 0) for r in rs) / n, 3) if n else None,
                    net_aud=round(sum(float(r.get("net_aud") or 0) for r in rs), 2))
    by_grade = {g: agg([r for r in rows if r.get("grade") == g]) for g in ("A+", "A", "B", "C") if any(r.get("grade") == g for r in rows)}
    lifts = {}
    for c in closed + [p for p in (sb.get("positions") or {}).values()]:
        t = c.get("lift_tag") or ("lifted_b" if c.get("lifted_b") else "lifted_subfloor" if c.get("lifted_subfloor") else "none")
        L = lifts.setdefault(t, dict(n=0, net_aud=0.0)); L["n"] += 1; L["net_aud"] = round(L["net_aud"] + float(c.get("net_aud") or 0), 2)
    rev = None
    try:
        from veteran_desk import llm_backend as LB
        rv = sorted(glob_reviews(seat))[-1:] ; rev = rv[0] if rv else None
    except Exception: rev = None
    return dict(seat=seat, desk_name=desk_name(seat), equity_aud=sb.get("equity_aud") or sb.get("equity"), cash_aud=sb.get("cash_aud") or sb.get("cash"),
                today_pnl_aud=sb.get("today_pnl_aud"), stats=agg(rows), fees_aud=round(sum(float(r.get("fees_aud") or 0) for r in rows), 2), by_grade=by_grade, lift_tags=lifts,
                lessons=rows[-5:], learning="lesson_loop.on_close writes state/thf/lessons/%s.jsonl; prompt_block feeds the last lessons into this seat's next proposal prompt; thf_review halves risk after 3 red rounds" % seat,
                review=rev, arm_reviews={k: v for k, v in (_rj(os.path.join(sd, "arm_reviews.json"), {}) or {}).items() if v.get("seat") == seat})
def glob_reviews(seat):
    import glob as _g
    out = []
    for f in _g.glob(os.path.join(ST, "runs", "*.json"))[-40:]:
        d = _rj(f, {}) or {}
        s = (d.get("seats") or {}).get(seat) or {}
        if s.get("gpt") or s.get("grok"): out.append((os.path.getmtime(f), dict(run=os.path.basename(f), grok=s.get("grok") or s.get("gpt"))))
    return [x[1] for x in sorted(out, key=lambda x: x[0])]
