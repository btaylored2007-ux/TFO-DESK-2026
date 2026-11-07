"""THF Singapore authoritative dashboard: direct routes, no pinned snapshot or runtime patch chain."""
"""TAYLOR HEDGE FUND dashboard (vd-engine), read-only, PAPER. http://100.120.8.11:18870/ (Tailscale) + http://127.0.0.1:18870/. Never writes trading data.
Cache: state/vd_engine/dash_cache.json (TTL 20s); the Dash Overlord may delete it to force a rebuild.
2026-10-30: serialize cache rebuilds (lock + unique tmp) so concurrent /api/state cannot 500 on replace race; keep a short in-memory copy to skip re-read/re-dump; slim top10 rows to fields the HTML uses."""
import datetime as dt, glob, json, math, os, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
# Public brand files only. The map is the allow-list: a query string is not a credential,
# and names outside this dict are not served.
BRAND_TYPES = {
    'favicon.svg': 'image/svg+xml',
    'mark.svg': 'image/svg+xml',
    'wordmark.svg': 'image/svg+xml',
    'tokens.css': 'text/css; charset=utf-8',
    'apple-touch-icon.png': 'image/png',
}
STATIC_EXACT = ('/chat.js', '/ticker.js')
from veteran_desk.thf_http import wire_payload, etag_matches, mutation_error, jsonl_tail, SnapshotView
VDM = os.environ.get('DASH_MODE') == 'vd'
ST = os.path.join(BASE, 'state', 'vd_engine')
PORT = int(os.environ.get('THF_DASH_PORT', '18860' if VDM else '18870'))

def is_static_asset(url_path):
    path = str(url_path or '').split('?', 1)[0]
    return path in STATIC_EXACT or path in {'/assets/brand/' + name for name in BRAND_TYPES}

def brand_asset(url_path):
    """Return (bytes, content_type) for one allow-listed /assets/brand file, else None.

    The query string is ignored. It does not grant diagnostics or any other route.
    """
    path = str(url_path or '').split('?', 1)[0]
    prefix = '/assets/brand/'
    if not path.startswith(prefix):
        return None
    name = path[len(prefix):]
    media = BRAND_TYPES.get(name)
    if media is None:
        return None
    root = os.path.realpath(os.path.join(BASE, 'assets', 'brand'))
    target = os.path.realpath(os.path.join(root, name))
    try:
        if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
            return None
        with open(target, 'rb') as handle:
            return handle.read(), media
    except (OSError, ValueError):
        return None

def listen_host():
    """Loopback unless an operator sets THF_DASH_BIND. Nginx already proxies 18870 to 127.0.0.1."""
    host = str(os.environ.get('THF_DASH_BIND', '127.0.0.1')).strip()
    return host or '127.0.0.1'
CACHE = os.path.join(ST, 'dash_cache_vd.json' if VDM else 'dash_cache.json')
BOOK_REL = 'reports/veteran_desk_book.json' if VDM else 'state/thf/thf_book.json'
VD_DASH = 'http://100.120.8.11:18860/'
WAR_ROOM = 'http://100.120.8.11:18812/warroom'
_LOCK = threading.Lock()
_MEM = None
_MEM_T = 0.0

def rj(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d

def tail(p, n):
    return jsonl_tail(p, n)

def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if k != '_rows'}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o
_TOP_KEEP = ('rank', 'sym', 'side', 'grade', 'score', 'market_open', 'wake_ok', 'why_no_wake')

def stale_min(age_min, limit):
    """True when a timestamp is missing or older than limit minutes. 0.0 is a fresh heartbeat — `age or 99` treats 0.0 as missing and flags a live engine as broken."""
    return age_min is None or age_min > limit

def split_info(items):
    """2026-10-31 brokencard: only red/amber count as broken; sev 'info' = expected/known limits, shown separately."""
    return ([b for b in items if b.get('sev') != 'info'], [b for b in items if b.get('sev') == 'info'])

def slim_row(x):
    if not isinstance(x, dict):
        return x
    o = {k: x.get(k) for k in _TOP_KEEP if k in x}
    h = x.get('history') or {}
    if isinstance(h, dict):
        o['history'] = {k: h.get(k) for k in ('exp_R', 'natural_pct') if k in h}
    return o
SEAT_ORDER = ['crypto', 'trend', 'price_action', 'essentials_utilities', 'macro', 'energy', 'banks', 'healthcare', 'news_filings', 'tech_ai_infra', 'fx_commodities']

def _pl(bk):
    try:
        from veteran_desk import pnl_light as PL
        L = PL.seat_light(bk) if bk else dict(pnl_light='grey', pnl_light_why='no own ledger', pnl_since_aud=None)
        return dict(pnl_light=L['pnl_light'], pnl_light_why=L['pnl_light_why'], pnl_since_aud=L.get('pnl_since_aud'))
    except Exception as x:
        return dict(pnl_light='grey', pnl_light_why='light error ' + type(x).__name__, pnl_since_aud=None)

def build():
    t = time.time()
    S = {}
    book = rj(os.path.join(BASE, BOOK_REL), {}) or {}
    S['today'] = dict(today_pnl_aud=book.get('today_pnl_aud'), equity_aud=book.get('equity_aud'), pnl_since_inception_aud=book.get('pnl_since_inception_aud'), cash_aud=book.get('cash_aud'), book_updated=book.get('updated'), day=book.get('day'), book_id=book.get('book'), book_label=book.get('label'), book_path=BOOK_REL, seed_aud=book.get('seed_aud'), created_utc=book.get('created_utc'))
    try:
        from veteran_desk import leaderboard as LB
        O = None
        if VDM:
            raise StopIteration
        if book:
            import datetime as _dt
            _t = _dt.datetime.now(_dt.timezone.utc)
            OP = os.path.join(BASE, 'state', 'thf', 'leaderboard_open.json')
            prev = rj(OP, None)
            O = prev if prev and prev.get('window_start_utc') == LB.window_start(_t).isoformat() and (set(prev.get('seats') or {}) >= set(book.get('seats') or {})) else LB.open_marks(book, _t, prev)
            if O is not prev:
                _tmp = OP + '.tmp.%d' % os.getpid()
                json.dump(O, open(_tmp, 'w'), default=str)
                os.replace(_tmp, OP)
        lb = LB.standings(D=book, O=O) if book else None
    except StopIteration:
        lb = None
        try:
            lb = LB.standings(D=book) if book else None
        except Exception as x:
            lb = dict(error=str(x)[:160])
    except Exception as x:
        lb = dict(error=str(x)[:160])
    S['leaderboard'] = lb
    try:
        from veteran_desk import small_coin as SC
        S['small_coin'] = SC.scoreboard(book)
    except Exception as x:
        S['small_coin'] = dict(error=str(x)[:160])
    try:
        if VDM:
            raise RuntimeError('THF-only panel')
        from veteran_desk import thf_review as TR
        S['accountability'] = TR.dash()
    except Exception as x:
        S['accountability'] = dict(error=str(x)[:160])
    try:
        from veteran_desk import lesson_loop as LL, book as BK_
        S['seat_stats'] = LL.read_stats(BK_._state_dir())
    except Exception as x:
        S['seat_stats'] = dict(error=str(x)[:160], seats={})
    rank = {r['seat']: (i + 1, r) for i, r in enumerate((lb or {}).get('rows', []) or [])}
    top = {os.path.basename(f)[:-5]: rj(f, {}) for f in glob.glob(os.path.join(ST, 'top10', '*.json'))}
    Q = tail(os.path.join(ST, 'wake_queue.jsonl'), 400)
    wakes = {}
    if VDM:
        Q = [q for q in Q if not str(q.get('wake_id', '')).startswith('THF-')]
    for q in Q:
        wakes.setdefault(q['wake_id'], {}).update({k: v for k, v in q.items() if v is not None})
    WL = sorted(wakes.values(), key=lambda w: w.get('wake_id'), reverse=True)
    gpt = {os.path.basename(f)[:-5]: rj(f) for f in sorted(glob.glob(os.path.join(ST, 'second_look', 'gpt', '*.json')))}
    grok = {os.path.basename(f)[:-5]: rj(f) for f in sorted(glob.glob(os.path.join(ST, 'second_look', 'grok', '*.json')))}
    _today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    _desk = 'vd' if VDM else 'thf'

    def _wita_day(u):
        try:
            return dt.datetime.fromisoformat(u).astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        except Exception:
            return None
    calls = [c for c in tail(os.path.join(BASE, 'state', 'llm_queue', 'calls.jsonl'), 3000) if c.get('desk') == _desk and _wita_day(c.get('utc')) == _today]
    fails_by_seat, calls_by_seat = ({}, {})
    for c in calls:
        calls_by_seat[c.get('seat')] = calls_by_seat.get(c.get('seat'), 0) + 1
        if not c.get('ok'):
            fails_by_seat.setdefault(c.get('seat'), []).append(c)
    failed_wakes, resolved_wakes = ({}, {})

    def _wok(w):
        return str(w.get('result')) == 'done rc=0' and isinstance(w.get('seats'), dict) and any((w.get('seats') or {}).values())
    for w in WL:
        if w.get('status') == 'finished' and str(w.get('wake_id', ''))[4:12] == _today.replace('-', '') and (not _wok(w)):
            failed_wakes[w['wake_id']] = w
    _refunded = {str(e.get('msg', '')).split()[1] for e in tail(os.path.join(ST, 'engine_log.jsonl'), 3000) if str(e.get('msg', '')).startswith('REFUND ')}
    for wid, w in list(failed_wakes.items()):
        ss = set(w.get('seats') or []) or {str(wid).split('-', 3)[-1]}
        later_ok = [x for x in WL if x.get('status') == 'finished' and str(x.get('wake_id', ''))[:17] > str(wid)[:17] and _wok(x)]
        fixed = {s: next((x['wake_id'] for x in sorted(later_ok, key=lambda x: x['wake_id']) if s in (x.get('seats') or {})), None) for s in ss}
        if fixed and all(fixed.values()):
            resolved_wakes[wid] = sorted(set(fixed.values()))[0]
            failed_wakes.pop(wid)
        elif not w.get('proposals') and wid in _refunded:
            resolved_wakes[wid] = 'refunded (model round failed, budget returned, no trade impact)'
            failed_wakes.pop(wid)
    resolved_calls = {}
    for seat, fl in list(fails_by_seat.items()):
        last_ok = max((c.get('utc') or '' for c in calls if c.get('seat') == seat and c.get('ok')), default='')
        keep = [c for c in fl if (c.get('utc') or '') > last_ok]
        if len(keep) < len(fl):
            resolved_calls[seat] = len(fl) - len(keep)
        if keep:
            fails_by_seat[seat] = keep
        else:
            fails_by_seat.pop(seat)
    cards = []
    for seat in SEAT_ORDER + ['head_of_desk']:
        v = top.get(seat) or {}
        rk = rank.get(seat)
        sw = [w for w in WL if seat in (w.get('seats') or [])]
        lastg = next((gpt[w['wake_id']] for w in sw if w['wake_id'] in gpt), None)
        lastk = next((grok[w['wake_id']] for w in sw if w['wake_id'] in grok), None)
        raw_top = v.get('top10', []) or []
        slim_top = [slim_row(x) for x in raw_top]
        best = next((x for x in slim_top if x.get('grade') in ('A+', 'A')), (slim_top or [None])[0])
        st = 'green' if any((x.get('wake_ok') and (not x.get('why_no_wake')) for x in raw_top)) else 'amber' if any((x.get('grade') in ('A', 'A+') for x in raw_top)) else 'grey'
        bk = (book.get('seats') or {}).get(seat) or {}
        sf = fails_by_seat.get(seat, [])
        swf = [w for w in failed_wakes.values() if seat in str(w.get('wake_id', ''))]
        if sf or next((w.get('wake_id') in failed_wakes for w in sw if w.get('status') == 'finished'), False):
            st = 'red'
        cards.append(dict(**_pl(bk), seat=seat, trader=v.get('trader') or seat, rank=rk[0] if rk else None, day_pnl_aud=rk[1].get('day_pnl_aud') if rk else bk.get('today_pnl_aud'), equity_aud=bk.get('equity_aud'), best=best, status=st, top10=slim_top, scanned=v.get('scanned'), n_A=v.get('n_A'), n_Aplus=v.get('n_Aplus'), updated=v.get('utc'), overlaps=v.get('overlaps'), wakes_today=calls_by_seat.get(seat, 0) if VDM else ((rj(os.path.join(ST, 'wake_counts.json'), {}) or {}).get('seat') or {}).get(seat, 0), model_fails_today=len(sf), failed_wakes_today=len(swf), last_fail=sf[-1] if sf else None, last_wake=sw[0] if sw else None, gpt=lastg, grok=lastk, open_positions=[dict(ticker=p.get('ticker'), side=p.get('side'), status=p.get('status')) for p in (bk.get('positions') or {}).values() if p.get('status') in ('open', 'armed')], book_view=_seat_view(bk)))
    hb = rj(os.path.join(ST, 'hb', 'engine.json'), {}) or {}
    dm = rj(os.path.join(ST, 'deadman_health.json'), {}) or {}
    ov = rj(os.path.join(ST, 'dash_overlord.json'), {}) or {}
    cov = rj(os.path.join(ST, 'coverage.json'), {}) or {}
    go = rj(os.path.join(ST, 'gate_overlord.json'), {}) or {}
    now = dt.datetime.now(dt.timezone.utc)

    def age(u):
        try:
            return round((now - dt.datetime.fromisoformat(u)).total_seconds() / 60, 1)
        except Exception:
            return None
    broken = []
    hb_age, dm_age = (age(hb.get('utc')), age(dm.get('utc')))
    if stale_min(hb_age, 5):
        broken.append(dict(sev='red', what='Engine heartbeat is %s min old' % hb_age))
    if hb.get('fails'):
        broken.append(dict(sev='amber', what='Engine had %s failed cycle(s) in a row' % hb.get('fails')))
    if stale_min(dm_age, 6):
        broken.append(dict(sev='red', what='Dead-man watchdog has not checked in for %s min' % dm_age))
    if dm.get('state') == 'DEGRADED':
        broken.append(dict(sev='red', what='Watchdog says DEGRADED: ' + str(dm.get('why'))))
    for g in cov.get('gaps', []):
        broken.append(g)
    for m in ov.get('mismatches', []):
        broken.append(dict(sev='red', what='Dash check: ' + m))
    wc = list(go.get('waiting_closed') or [])
    for s in go.get('stuck', []):
        if s.get('cls') == 'armed_unfilled' and s.get('wall_age_h') is None and (s.get('market_open') is False):
            wc.append(s)
            continue
        from veteran_desk.thf_order_diagnostics import age_review
        review = age_review(s, book, now)
        if review:
            s['display_age_review'] = review  # This is the response copy, never persisted to engine state.
            continue
        broken.append(dict(sev='amber', what='Stuck: %s %s %s armed %sh unfilled (market-open hours)' % (s.get('cls'), s.get('seat'), s.get('ticker'), s.get('age_h'))))
    if wc:
        broken.append(dict(sev='info', what='%d armed order(s) waiting for their market to open (not stuck; only open-market hours count): %s' % (len(wc), ', '.join((str(x.get('ticker')) for x in wc[:15])))))
    nf = sum((len(v) for v in fails_by_seat.values()))
    if nf:
        broken.append(dict(sev='red', what='Model calls failed today: %d of %d (%s)' % (nf, len(calls), ', '.join(('%s %d' % (k, len(v)) for k, v in sorted(fails_by_seat.items()))))))
    if failed_wakes:
        broken.append(dict(sev='red', what='Wakes failed today (no model answer / rc!=0): ' + ', '.join(sorted(failed_wakes)[-5:])))
    if resolved_wakes:
        broken.append(dict(sev='info', what='Resolved: wake(s) failed earlier today but a later wake for the same seat succeeded: ' + ', '.join(('%s -> %s' % (a, b) for a, b in sorted(resolved_wakes.items())[-5:]))))
    if resolved_calls:
        broken.append(dict(sev='info', what='Resolved: earlier failed model calls followed by a successful call: ' + ', '.join(('%s %d' % kv for kv in sorted(resolved_calls.items())))))
    fc = feed_cards()
    bw, pf = (fc['borrow'], fc['paid_feeds'])
    eu, iv = (pf.get('eu_ws') or {}, pf.get('intraday') or {})
    if not bw.get('fresh_24h'):
        broken.append(dict(sev='amber', what='IBKR borrow %s (age %sh). Shorts fail closed once the file is older than 24h.' % (bw.get('status'), bw.get('age_h'))))
    if not eu.get('healthy'):
        broken.append(dict(sev='info' if eu.get('markets_open') is False else 'amber', what=('EU/LSE markets closed - websocket idle (%s); resumes at the next EU open' if eu.get('markets_open') is False else 'EU/LSE websocket not healthy (%s); Yahoo is the EU mark fallback') % (eu.get('why') or 'down')))
    if not (iv.get('n_1m') or iv.get('n_5m') or iv.get('n_15m')):
        broken.append(dict(sev='amber', what='No 1m/5m/15m bars in data/intraday/bars.sqlite yet'))
    broken, info = split_info(broken)
    C = rj(os.path.join(ST, 'wake_counts.json'), {}) or {}
    if VDM:
        C = dict(C, total=len(calls), seat=calls_by_seat, note='VD: model rounds today (engine wakes book to THF)')
    S.update(borrow=bw, paid_feeds=pf, model_calls=dict(today=len(calls), failed=nf, by_seat_failed={k: len(v) for k, v in fails_by_seat.items()}), cards=cards, wakes=WL[:40], wake_counts=C, caps=dict(per_seat=6, total=40), health=dict(engine=hb, engine_age_min=age(hb.get('utc')), deadman=dm, deadman_age_min=age(dm.get('utc')), overlord=ov, overlord_age_min=age(ov.get('utc'))), coverage=cov, gate_overlord=go, broken=broken, info=info, grok_queue=tail(os.path.join(ST, 'grok_second_look_queue.jsonl'), 20), links=dict(vd_dash=VD_DASH, classic='/classic' if VDM else None, war_room=WAR_ROOM, leaderboard='#leaderboard'), built_utc=now.isoformat(), build_secs=round(time.time() - t, 2), first_top10=rj(os.path.join(ST, 'first_top10.json')))
    S = clean(S)
    tmp = '%s.tmp.%s.%s' % (CACHE, os.getpid(), threading.get_ident())
    json.dump(S, open(tmp, 'w'), default=str)
    os.replace(tmp, CACHE)
    return S

def feed_cards():
    """Borrow freshness + paid-mark health for the dashboard. Read-only; does not pull FTP or open a websocket."""
    try:
        from veteran_desk.borrow_status import dash as bd
        borrow = bd()
    except Exception as e:
        borrow = dict(status='error', fresh_24h=False, error=type(e).__name__)
    paid = {}
    try:
        from veteran_desk import eodhd_eu_ws as EU
        paid['eu_ws'] = EU.health()
    except Exception as e:
        paid['eu_ws'] = dict(healthy=False, why=type(e).__name__)
    try:
        from veteran_desk import intraday_store as IB
        paid['intraday'] = IB.health()
    except Exception as e:
        paid['intraday'] = dict(present=False, error=type(e).__name__)
    return dict(borrow=borrow, paid_feeds=paid)

def state():
    global _MEM, _MEM_T
    now = time.monotonic()
    if _MEM is not None and now - _MEM_T < 20:
        return _MEM
    with _LOCK:
        now = time.monotonic()
        if _MEM is not None and now - _MEM_T < 20:
            return _MEM
        try:
            if time.time() - os.path.getmtime(CACHE) < 20:
                S = json.load(open(CACHE))
                _MEM, _MEM_T = (S, time.monotonic())
                return S
        except Exception:
            pass
        S = build()
        _MEM, _MEM_T = (S, time.monotonic())
        return S
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thf_dash.html')).read() if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thf_dash.html')) else '<h1>Taylor Hedge Fund</h1>'
if VDM:
    HTML = HTML.replace('<title>Transparent Trading · Paper</title>', '<title>Veteran Desk</title>').replace('>Transparent Trading<', '>Veteran Desk<').replace('not a live broker', 'own VD book').replace('THF own fresh paper book (', 'Veteran Desk own paper book (').replace(', separate from the old Veteran Desk book)', ')').replace('>Veteran Desk dash / leaderboard</a>', '>Transparent Trading dash</a> · <a href="/classic">classic VD page</a>')
    VD_DASH = 'http://100.120.8.11:18870/'
    try:
        from veteran_desk import dash_server as _DS
    except Exception:
        _DS = None

def _seat_view(bk):
    try:
        from veteran_desk import pos_live as _PL
        return _PL.seat_view(bk)
    except Exception as e:
        return dict(positions=[], armed=[], closed_today=[], error=type(e).__name__)

class H(BaseHTTPRequestHandler):

    def log_message(self, *a):
        pass

    server_version = 'THF'
    sys_version = ''

    def version_string(self):
        return 'THF'

    def _send(self, code, body, ct):
        static = code == 200 and is_static_asset(self.path)
        b, headers = wire_payload(body, ct, self.headers.get('Accept-Encoding', ''), static=static)
        if static and etag_matches(self.headers.get('If-None-Match'), headers['ETag']):
            code, b = 304, b''
        try:
            self.send_response(code)
            self.send_header('Content-Type', ct)
            for name, value in headers.items():
                self.send_header(name, value)
            if not static:
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
            if code != 304:
                self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            if getattr(self, 'command', 'GET') != 'HEAD' and code != 304:
                self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        try:
            found = brand_asset(self.path)
            if found is not None:
                body, ctype = found
                self._send(200, body, ctype)
                return
            if self.path.split('?')[0] == '/api/autonomy':
                from veteran_desk import thf_home as EH
                report = EH.autonomy_status()
                self._send(200 if report.get('available') and report.get('fresh') else 503,
                           json.dumps(report, default=str), 'application/json')
            elif self.path.split('?')[0] in ('/api/chat_tools','/api/chat_tasks','/api/competition','/api/peer_trades'):
                from urllib.parse import urlparse,parse_qs
                from veteran_desk import thf_chat_tasks as TASKS,thf_competition as COMP
                path=self.path.split('?')[0];seat=(parse_qs(urlparse(self.path).query).get('seat') or ['ALL'])[0]
                if path=='/api/peer_trades':
                    query=parse_qs(urlparse(self.path).query)
                    try:data=COMP.query(desk=(query.get('desk') or ['all'])[0],status=(query.get('status') or ['all'])[0],search=(query.get('q') or [''])[0],offset=(query.get('offset') or [0])[0],limit=(query.get('limit') or [50])[0])
                    except ValueError as error:return self._send(400,json.dumps(dict(error=str(error))),'application/json')
                else:data=COMP.snapshot() if path=='/api/competition' else TASKS.tools(seat) if path=='/api/chat_tools' else TASKS.listing(seat)
                self._send(200,json.dumps(data,default=str),'application/json')
            elif self.path.startswith('/api/state'):
                self._send(200, json.dumps(_fm_state(state()), default=str), 'application/json')
            elif VDM and _DS is not None and (self.path.split('?')[0] == '/classic'):
                self._send(200, _DS.HTML.replace('/api/state', '/api/classic'), 'text/html; charset=utf-8')
            elif VDM and _DS is not None and (self.path.split('?')[0] == '/api/classic'):
                self._send(200, json.dumps(_DS._clean(_DS.state()), default=str), 'application/json')
            elif self.path.startswith('/api/opps'):
                from veteran_desk import thf_charts as TCH
                self._send(200, json.dumps(clean(TCH.opps(os.path.join(BASE, BOOK_REL))), default=str), 'application/json')
            elif self.path.startswith('/api/desk'):
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_charts as TCH
                self._send(200, json.dumps(clean(TCH.desk((parse_qs(urlparse(self.path).query).get('seat') or [''])[0], os.path.join(BASE, BOOK_REL))), default=str), 'application/json')
            elif self.path.startswith('/api/chart'):
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_charts as TCH
                self._send(200, json.dumps(clean(TCH.chart((parse_qs(urlparse(self.path).query).get('sym') or [''])[0])), default=str), 'application/json')
            elif self.path.startswith('/api/ticker'):
                from veteran_desk import thf_ticker as TK
                _S = state()
                _T = TK.ticker(_S)
                try:
                    from veteran_desk import thf_home as EH
                    _T = EH.ticker_fresh(_T, _S)
                except Exception:
                    pass
                self._send(200, json.dumps(clean(_T), default=str), 'application/json')
            elif self.path.split('?')[0] == '/ticker.js':
                self._send(200, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thf_ticker.js')).read(), 'application/javascript; charset=utf-8')
            elif self.path.split('?')[0] in ('/hub', '/hub/'):
                self._send(200, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thf_hub.html')).read(), 'text/html; charset=utf-8')
            elif self.path.startswith('/api/hub'):
                from veteran_desk import thf_hub as HUB
                self._send(200, json.dumps(clean(HUB.hub(state())), default=str), 'application/json')
            elif self.path.startswith('/api/funder_msgs'):
                from veteran_desk import thf_funder as FM
                self._send(200, json.dumps(FM.summary(), default=str), 'application/json')
            elif self.path.startswith('/api/advisor_thread'):
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_funder as FM
                self._send(200, json.dumps(FM.adv_thread((parse_qs(urlparse(self.path).query).get('trade') or [''])[0])), 'application/json')
            elif self.path.startswith('/api/chatgpt_status'):
                try:
                    _cg = json.load(open('/opt/tfo/live/thf/state/thf/chatgpt_status.json'))
                except Exception:
                    _cg = dict(state='down')
                self._send(200, json.dumps(dict(_cg, signin_url='http://100.120.8.11:18890/vnc.html?autoconnect=1&resize=scale')), 'application/json')
            elif self.path.split('?')[0] == '/api/chat':
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_funder as FM
                a = int((parse_qs(urlparse(self.path).query).get('after') or ['0'])[0] or 0)
                rows = []
                try:
                    rows = [json.loads(l) for l in open('/opt/tfo/live/thf/state/thf/funder_thread.jsonl') if l.strip()]
                except Exception:
                    pass
                rows = [dict(r, k=i + 1) for i, r in enumerate(rows)]
                _fm = {}
                try:
                    for _l in open('/opt/tfo/live/thf/state/funder_messages.jsonl'):
                        if _l.strip():
                            _x = json.loads(_l)
                            _fm[_x.get('id')] = _x
                except Exception:
                    pass
                for _r in rows:
                    if not _r.get('images') and _fm.get(_r.get('id'), {}).get('images'):
                        _r['images'] = _fm[_r['id']]['images']
                adv = [dict(ts_wita=r.get('ts_wita'), text=r.get('text'), mode='advisor', images=r.get('images') or [], **{'from': 'brett' if r.get('role') == 'brett' else r.get('role'), 'to': r.get('advisor')}) for r in FM._aread() if r.get('trade_id') == 'DESK'][-40:]
                first = a == 0
                out = rows[-80:] + adv if first else [r for r in rows if r['k'] > a]
                out.sort(key=lambda r: str(r.get('ts_wita')))
                self._send(200, json.dumps(dict(rows=out, next=len(rows), first=first), default=str), 'application/json')
            elif self.path.startswith('/api/chat_img/'):
                from veteran_desk import thf_funder as FM
                iid = self.path.split('/api/chat_img/', 1)[1].split('?', 1)[0].strip('/')
                try:
                    raw, mime = FM.img_read(iid)
                    self._send(200, raw, mime)
                except Exception as x:
                    self._send(404, json.dumps(dict(error=str(x)[:80])), 'application/json')
            elif not VDM and (self.path.split('?')[0] in ('/', '/index.html', '/desk', '/desk/', '/desk/phone', '/desk/phone/', '/desk/m', '/desk/m/') or (self.path.startswith('/desk/') and self.path.split('?')[0].split('/')[2] not in ('chat',))):
                try:
                    from veteran_desk import thf_home as EH
                    from veteran_desk import thf_display_epoch as DE
                    _S = state()
                    _b = DE.stamp_page(EH.page_html(_S, self.path), _S.get('today') or {}, os.path.join(BASE, 'state', 'thf', 'display_epoch.json'))
                except Exception as _x:
                    _b = ''
                if len(_b) > 2000:
                    self._send(200, _b, 'text/html; charset=utf-8')
                else:
                    self._send(200, HTML.replace('<body>', '<body><div id="tfo-tk"></div><script src="/ticker.js" defer></script>', 1).replace('</body>', _fm_panel() + '</body>', 1).replace('<!--tfo_funmsg_20261102_html-->', ''), 'text/html; charset=utf-8')
            elif self.path.split('?')[0] in ('/classic', '/classic/'):
                self._send(200, HTML.replace('<body>', '<body><div id="tfo-tk"></div><script src="/ticker.js" defer></script>', 1).replace('</body>', _fm_panel() + '</body>', 1).replace('<!--tfo_funmsg_20261102_html-->', ''), 'text/html; charset=utf-8')
            elif self.path.split('?')[0] == '/api/home':
                from veteran_desk import thf_home as EH
                from veteran_desk import thf_display_epoch as DE
                _S = state()
                _payload = EH.home(_S)
                DE.attach(_payload, _S.get('today') or {}, os.path.join(BASE, 'state', 'thf', 'display_epoch.json'))
                self._send(200, json.dumps(_payload, default=str), 'application/json')
            elif self.path.split('?')[0] == '/api/eli10':
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_home as EH
                self._send(200, json.dumps(EH.eli10(state(), (parse_qs(urlparse(self.path).query).get('tab') or ['all'])[0]), default=str), 'application/json')
            elif self.path.split('?')[0] == '/chat.js':
                from veteran_desk import thf_chat_home as CH
                self._send(200, CH.js(), 'application/javascript; charset=utf-8')
            elif self.path.split('?')[0] == '/api/numbers':
                from veteran_desk import thf_home as EH
                _S = state()
                _O = _T = None
                try:
                    from veteran_desk import thf_charts as TCH
                    _O = clean(TCH.opps(os.path.join(BASE, BOOK_REL)))
                except Exception:
                    pass
                try:
                    from veteran_desk import thf_ticker as TK
                    _T = EH.ticker_fresh(TK.ticker(_S), _S)
                except Exception:
                    pass
                self._send(200, json.dumps(EH.numbers_check(_S, opps=_O, ticker=_T), default=str), 'application/json')
            elif self.path.split('?')[0] == '/api/chat_home':
                from veteran_desk import thf_chat_home as CH
                self._send(200, json.dumps(CH.home(), default=str), 'application/json')
            elif self.path.split('?')[0] == '/api/chat_thread':
                from urllib.parse import urlparse, parse_qs
                from veteran_desk import thf_chat_home as CH
                _q = parse_qs(urlparse(self.path).query)
                _i = lambda k: int((_q.get(k) or ['0'])[0] or 0)
                self._send(200, json.dumps(CH.thread((_q.get('seat') or ['ALL'])[0], after=_i('after'), before=_i('before'), limit=max(10, min(_i('limit') or 60, 200))), default=str), 'application/json')
            elif self.path.split('?')[0] == '/api/ai_status':
                from veteran_desk import thf_chat_home as CH
                self._send(200, json.dumps(dict(ais=CH.ai_status(), paper=True), default=str), 'application/json')
            elif self.path.split('?')[0] in ('/desk/chat', '/desk/chat/'):
                from veteran_desk import thf_chat_home as CH
                self._send(200, CH.page_html(), 'text/html; charset=utf-8')
            elif self.path.startswith('/healthz'):
                self._send(200, 'ok', 'text/plain')
            elif self.path in ('/', '/index.html') or self.path.startswith('/?') or self.path.startswith('/desk/'):
                self._send(200, HTML.replace('<body>', '<body><div id="tfo-tk"></div><script src="/ticker.js" defer></script>', 1).replace('</body>', _fm_panel() + '</body>', 1).replace('<!--tfo_funmsg_20261102_html-->', ''), 'text/html; charset=utf-8')
            else:
                self._send(404, 'not found', 'text/plain')
        except Exception as e:
            self._send(500, json.dumps(dict(error=str(e)[:200])), 'application/json')

def _eli10_warm():
    global _MEM, _MEM_T
    while True:
        try:
            if _MEM is None or time.monotonic() - _MEM_T > 14:
                with _LOCK:
                    if _MEM is None or time.monotonic() - _MEM_T > 14:
                        _S = build()
                        _MEM, _MEM_T = (_S, time.monotonic())
        except Exception:
            pass
        time.sleep(2)
if not VDM and (not os.environ.get('PYTEST_CURRENT_TEST')):
    threading.Thread(target=_eli10_warm, daemon=True, name='eli10_warm').start()

_FM_VIEW = SnapshotView(ttl=1.0)

def _fm_state(S):
    return _FM_VIEW.get(S, lambda: _build_fm_state(S))

def _build_fm_state(S):
    out = dict(S)
    try:
        from veteran_desk import thf_funder as FM
        out['funder_msgs'] = FM.summary()
    except Exception as x:
        out['funder_msgs'] = dict(error=type(x).__name__)
    try:
        from veteran_desk import strategy_lab as SL
        out['strategy_lab'] = SL.card()
    except Exception as x:
        out['strategy_lab'] = dict(error=type(x).__name__)
    return out

def _sl_panel():
    try:
        from veteran_desk import strategy_lab as SL
        return SL.panel()
    except Exception:
        return ''

def _fm_panel():
    from veteran_desk import thf_funder as FM, thf_chat_home as CH
    try:
        body = open(os.path.join(BASE, 'tools', 'thf_chat.html')).read() + FM.panel()
    except Exception:
        body = ''
    return CH.inline(body)

def _fm_post(self):
    from veteran_desk import thf_funder as FM
    p = self.path.split('?')[0]
    try:
        n = int(self.headers.get('Content-Length') or 0)
        limit = 12 * 1024 * 1024 if p == '/api/chat_img' else 8192
        if n <= 0 or n > limit:
            return self._send(413, json.dumps(dict(error='bad size')), 'application/json')
        raw = self.rfile.read(n)
        if p == '/api/chat_img' and str(self.headers.get('Content-Type') or '').lower().startswith('multipart/form-data'):
            result = FM.img_save_multipart(self.headers.get('Content-Type'), raw)
        else:
            b = json.loads(raw or b'{}')
            if not isinstance(b, dict):
                raise ValueError('expected an object')
            if p == '/api/funder_msg':
                if b.get('kind') == 'task':
                    from veteran_desk import thf_chat_tasks as TASKS
                    try: result = TASKS.submit(b.get('task'))
                    except TASKS.Unavailable as error:
                        return self._send(503,json.dumps(dict(error=str(error))),'application/json')
                else:
                    if isinstance(b.get('command'),dict) and b['command'].get('mode') in ('ask','push','learn'):
                        from veteran_desk import thf_chat_tasks as TASKS
                        if not TASKS.ready():return self._send(503,json.dumps(dict(error='Chat command controls are starting; your text is kept. Try again shortly.')),'application/json')
                    extra={key:value for key,value in dict(command=b.get('command'),dedupe_key=b.get('request_id')).items() if value is not None}
                    result = FM.post(b.get('kind'), b.get('text'), b.get('seat'), images=b.get('images'), ai=b.get('ai'), **extra)
            elif p == '/api/advisor_note':
                result = FM.adv_add(b.get('trade'), b.get('advisor'), b.get('text'), ctx=b.get('ctx'), route=b.get('route'), images=b.get('images'), command=b.get('command'))
            elif p == '/api/funder_ack':
                result = FM.ack(b.get('id'), b.get('by') or 'funder', remove=bool(b.get('remove')))
            elif p == '/api/chat_img':
                result = FM.img_save(data_url=b.get('data_url'), b64=b.get('b64'), mime=b.get('mime'))
            elif p == '/api/seat_schedule':
                from veteran_desk import thf_home as EH
                result = EH.sched_request(b)
            elif p == '/api/chat_react':
                from veteran_desk import thf_chat_home as CH
                result = CH.react(b)
            else:
                return self._send(404, json.dumps(dict(error='not found')), 'application/json')
        return self._send(200, json.dumps(result), 'application/json')
    except ValueError as error:
        return self._send(400, json.dumps(dict(error=str(error)[:200])), 'application/json')
    except Exception as error:
        return self._send(500, json.dumps(dict(error=type(error).__name__)), 'application/json')
H.do_POST = _fm_post
try:
    from thf_guardian.dash_mount import install as _gd_install
    _gd_install(H, sys.modules.get(__name__))
except Exception as _gde:
    import sys as _gsys
    _gsys.stderr.write('thf_guardian mount failed: %s\n' % _gde)
# Validate every mutation, including the installed guardian proxy, before reading its body.
_POST_HANDLER = H.do_POST
def _checked_post(self):
    error = mutation_error(self.headers)
    if error:
        return self._send(error[0], json.dumps(dict(error=error[1])), 'application/json')
    return _POST_HANDLER(self)
H.do_POST = _checked_post

if __name__ == '__main__':
    ThreadingHTTPServer((listen_host(), PORT), H).serve_forever()
