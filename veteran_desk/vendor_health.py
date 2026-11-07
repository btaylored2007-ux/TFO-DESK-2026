"""healthdesk_20261103 (PAPER, monitoring only - never trades, never touches marks, FX conversion or rails): one honest health row
per PAID DATA VENDOR. Brett 20:10 WITA 3 Nov "Oanda and Unusual Whales is up the health feed needs to be updated", 20:16 "PLEASE CHECK
THE HEALTH DESK - full review and full debug and fix"; Head Trader P0 ch-1793706250-713e; Marcus 20:54 (also Massive + EODHD).

The feed keeper (thf_feedkeeper.py, every ~60 s) calls rows(ks) and stores the result as feeds['vendor:<id>'] in
state/thf/feed_status.json. Readers: dash Health tab (thf_home 'Paid data vendors' row + Needs Brett box), the seat HEALTH line
(dash_context) and the Singapore watcher (sgp_health).

Status: ok | stale | down | closed | not_configured
  down           = the last request failed (auth / HTTP / timeout / DNS / vendor quota)
  stale          = no new good data for longer than max(10 min, 3 x poll) for OANDA while FX is open (Sun 17:00 - Fri 17:00 New York),
                   max(30 min, 3 x poll) for Unusual Whales / Massive during the US regular session (09:30-16:00 New York, holidays
                   not modelled)
  closed         = outside those hours and older than that (the last good time is still shown)
  not_configured = no credentials on this server (code still reads the feed, so it counts as broken)
Evidence comes from files the feeds already write (OANDA check snapshot + reconcile health + its unit result, the news stream heartbeat,
the minute-bar store health). The ONLY network call is EODHD GET /api/user (EODHD's own call counter, no market data) at most once per
10 min. Secrets are never printed or stored; every error text goes through redact()."""
import datetime as dt, json, os, re, subprocess, time, urllib.error, urllib.request
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:   # pragma: no cover
    NY = None
TAG = "healthdesk_20261103"
L = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
SEC = "/home/tfo/.tfo_secrets"   # fixed path: the feed keeper runs as root, so ~ would be /root
P = dict(oanda_snap=os.path.join(L, "state", "oanda", "snapshot.json"), oanda_recon=os.path.join(L, "state", "oanda", "reconcile_health.json"),
         oanda_retries=os.path.join(L, "state", "oanda", "api_retries.jsonl"), oanda_err=os.path.join(L, "logs", "oanda-check.err.log"),
         hb=os.path.join(L, "state", "catalyst_stream", "heartbeat.json"), queues=os.path.join(L, "state", "catalyst_stream", "queues"),
         intraday=os.path.join(L, "data", "intraday", "health.json"))
OANDA_POLL_S = 900          # tfo-thf-oanda-check.timer (every 15 min); seat FX quotes are fetched on demand (paid_feeds.fetch_quote)
INTRADAY_POLL_S = 900       # tfo-thf-intraday-bars.timer (*:7/15)
EODHD_USER_EVERY_S = 600    # at most one GET /api/user per 10 min
VENDORS = ("oanda", "unusual_whales", "eodhd", "massive")
NAMES = dict(oanda="OANDA practice", unusual_whales="Unusual Whales", eodhd="EODHD (REST data calls)", massive="Massive")
READERS = dict(   # who reads each vendor on this server and what happens when it fails (A1/A4 of ch-1793706250-713e, read-only audit 22:27 WITA)
    oanda=["OANDA paper sleeve check every 15 min (tfo-thf-oanda-check, oanda_sleeve + oanda_aggro: stops, exits, reconcile, NAV) + daily runs (tfo-thf-oanda-daily); a failed GET is retried 3x, then that run fails and the next one retries; broker stops stay in place",
           "live FX quotes in the macro + fx_commodities seat prompts (paid_feeds: OANDA first, Yahoo 15-20 min late if OANDA fails, error kept in memory only)",
           "FX daily bars (tfo-thf-pit-fx 06:10 + 08:40 WITA: OANDA candles first, Yahoo fallback) - incl. the AUDUSD daily close that seat sizing falls back to after the live OANDA mid",
           "READ by the THF book since fxmark_20261103: OANDA-book FX (=X) marks, trigger checks and fills use the OANDA practice mid (Yahoo 5m only when OANDA is stale/down; the row says which); A$ conversion = OANDA crosses -> Yahoo -> pit daily close -> last good (age shown); futures stay on the Yahoo future (OANDA CFD shown beside it in prompts only)"],   # fxmark_20261103
    unusual_whales=["news stream (tfo-thf-catalyst-stream, every ~20 s): UW news + options-flow alerts go to the trader seats' news queues (last 12 rows per seat reach each prompt); if UW fails it backs off (30 s to 15 min) and FMP, Massive/Benzinga and EDGAR news still arrive",
                    "seat paid pack (thf_paid_pack: flow, dark pool, congress trades) is fetched each round but dropped from the prompt by the pack trimmer (known gap, reported)",
                    "NOT read by the book, its marks, rails or gates"],
    eodhd=["EU/Asia minute bars (tfo-thf-intraday-bars, tfo-thf-asia-intraday) - stop while refused",
           "paid live data for ASX and other non-US names on the dash and in seat packs (Yahoo, 15-20 min late, is used instead; the book's own marks for these use Yahoo anyway)",
           "EODHD news + earnings in the news stream (FMP, Massive, Unusual Whales still answer)",
           "daily closes loader (tfo-thf-pit-eod: Yahoo fallback) and sector tags",
           "NOT affected: the live European price feed (EODHD websocket, a separate service) and the book's P/L marks"],
    massive=["US prices and minute bars (paid real-time; Yahoo fallback)", "Massive/Benzinga news in the news stream"])
_SECRET_VALUES = []
_UNIT_CACHE = {}


def _now(): return dt.datetime.now(UTC)
def _rj(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
def _parse(u):
    if u in (None, ""): return None
    try:
        if isinstance(u, (int, float)): return dt.datetime.fromtimestamp(float(u) / (1000.0 if float(u) > 1e11 else 1.0), UTC)
        t = dt.datetime.fromisoformat(str(u).strip().replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception: return None
def _iso(t): return t.astimezone(UTC).isoformat(timespec="seconds") if t else None
def wita(t, now=None):
    """'22:14 WITA' today, '2 Nov 08:09 WITA' on another day, '—' when unknown."""
    t = _parse(t) if not isinstance(t, dt.datetime) else t
    if not t: return "—"
    w = t.astimezone(WITA); n = (now or _now()).astimezone(WITA)
    return w.strftime("%H:%M WITA") if w.date() == n.date() else "%d %s WITA" % (w.day, w.strftime("%b %H:%M"))
def _env_val(fn, var):
    v = os.environ.get(var)
    if v: return v.strip()
    try:
        for ln in open(os.path.join(SEC, fn)):
            ln = ln.strip()
            if ln.startswith("export "): ln = ln[7:].strip()
            if ln.startswith(var + "="):
                v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                if v and v not in _SECRET_VALUES: _SECRET_VALUES.append(v)
                return v or None
    except Exception: return None
    return None
def _has_var(fn, var):
    """True if the credential exists (value never returned to the caller)."""
    if os.environ.get(var): return True
    try: return any(re.match(r"\s*(?:export\s+)?%s\s*=\s*\S" % re.escape(var), ln) for ln in open(os.path.join(SEC, fn)))
    except Exception: return False
def redact(s, n=200):
    """No tokens, api keys in URLs, Bearer headers or OANDA account ids in any text we store or show."""
    s = str(s or "")
    for v in _SECRET_VALUES:
        if v and len(v) >= 6: s = s.replace(v, "***")
    s = re.sub(r"(?i)(api_token|apikey|api_key|token|key)=([^&\s\"']+)", r"\1=***", s)
    s = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", s)
    s = re.sub(r"\b\d{3}-\d{3}-\d{6,10}-\d{3}\b", "<account>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]
def fx_open(now):
    """FX week: Sunday 17:00 to Friday 17:00 New York."""
    n = now.astimezone(NY) if NY else now - dt.timedelta(hours=5); wd = n.weekday(); m = n.hour * 60 + n.minute
    if wd == 5: return False
    if wd == 6: return m >= 17 * 60
    if wd == 4: return m < 17 * 60
    return True
def us_session(now):
    """US regular session 09:30-16:00 New York, Mon-Fri (holidays not modelled)."""
    n = now.astimezone(NY) if NY else now - dt.timedelta(hours=5)
    return n.weekday() < 5 and 9 * 60 + 30 <= n.hour * 60 + n.minute < 16 * 60
def next_utc_midnight(now):
    d = (now.astimezone(UTC) + dt.timedelta(days=1)).date()
    return dt.datetime(d.year, d.month, d.day, tzinfo=UTC)
def _unit(name, X):
    """systemctl show (Result, exit time, Environment) - cached 60 s; X['units'][name] in tests."""
    if "units" in X: return (X["units"] or {}).get(name) or {}
    c = _UNIT_CACHE.get(name)
    if c and time.time() - c[0] < 60: return c[1]
    out = {}
    try:
        r = subprocess.run(["systemctl", "show", name, "-p", "Result", "-p", "ExecMainExitTimestamp", "-p", "ExecMainStatus", "-p", "ActiveState", "-p", "Environment"],
                           capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            k, _, v = ln.partition("=")
            if k == "Environment": out["env"] = dict(x.split("=", 1) for x in v.split() if "=" in x and not re.search(r"(?i)key|token|secret|pass", x.split("=", 1)[0]))
            elif k: out[k] = v
    except Exception: pass
    _UNIT_CACHE[name] = (time.time(), out); return out
def _decide(configured, err, last_good, now, stale_s, session_open):
    """The 713e status rules -> (status, age_s)."""
    age = (now - last_good).total_seconds() if last_good else None
    if not configured: return "not_configured", age
    if err: return "down", age
    if age is None or age > stale_s: return ("stale" if session_open else "closed"), age
    return "ok", age
def _vendor_side(err):
    e = str(err or "").lower()
    return bool(re.search(r"http (402|429|5\d\d)|timed? ?out|timeout|urlerror|temporary failure|name or service|connection|limit|quota|503|504|502", e))
def _row(vid, status, last_good, age, now, err=None, detail="", source="", path="", unit=None, stale_s=None, session=None, needs_brett=None,
         vendor_side=None, extra=None):
    r = dict(kind="vendor", vendor=NAMES[vid], source=source, path=path, unit=unit, status=status, age_s=(int(age) if age is not None else None),
             age_min=(int(age // 60) if age is not None else None), last_good_utc=_iso(last_good), last_good_wita=wita(last_good, now),
             last_error=(redact(err) if err else None), detail=redact(detail, 400), readers=READERS.get(vid, []), stale_after_min=(int(stale_s // 60) if stale_s else None),
             session=session, needs_brett=needs_brett, vendor_side=(_vendor_side(err) if vendor_side is None and err else bool(vendor_side)), tag=TAG)
    if extra: r.update(extra)
    return r


# ---- OANDA practice ---------------------------------------------------------------------------------------------------------------
def oanda(now, ks, X):
    snap = X["oanda_snap"] if "oanda_snap" in X else (_rj(P["oanda_snap"], {}) or {})
    rh = X["oanda_recon"] if "oanda_recon" in X else (_rj(P["oanda_recon"], {}) or {})
    conf = X["conf"].get("oanda", True) if "conf" in X else (_has_var("oanda.env", "OANDA_PRACTICE_TOKEN") or os.path.exists("/home/tfo/.tfo/oanda_practice_token"))
    u = _unit("tfo-thf-oanda-check.service", X)
    goods = [t for t in (_parse(snap.get("asof_utc")), _parse(rh.get("last_clean_utc"))) if t]
    lg = max(goods) if goods else None
    err = None
    if u.get("Result") not in (None, "", "success"):
        tail = X.get("oanda_err_tail") if "oanda_err_tail" in X else _tail(P["oanda_err"], 600)
        err = "the 15-min OANDA check failed (%s, exit %s)%s" % (u.get("Result"), u.get("ExecMainStatus"), (": " + tail) if tail else "")
    elif rh.get("last_pass_ok") is False and rh.get("last_error") and re.search(r"(?i)http|timeout|timed out|urlerror|connection|refused|name or service", str(rh.get("last_error"))):
        err = "OANDA request failed: %s" % rh.get("last_error")
    stale_s = max(600, 3 * OANDA_POLL_S); op = fx_open(now)
    st, age = _decide(conf, err, lg, now, stale_s, op)
    note = ""
    rt = X["oanda_retry"] if "oanda_retry" in X else _last_jsonl(P["oanda_retries"])
    if isinstance(rt, dict) and _parse(rt.get("utc")) and (not lg or _parse(rt.get("utc")) > lg - dt.timedelta(hours=6)):
        note = " Last retried error %s: %s (it retried and recovered)." % (wita(rt.get("utc"), now), redact(rt.get("error"), 120))
    det = {"ok": "answering: the 15-min check read the account at %s (reconcile %s, %s open trades)" % (wita(lg, now), rh.get("last_pass_ok") is not False and "clean" or "NOT clean", snap.get("open_trade_count", "?")),
           "down": "the last OANDA request failed", "stale": "no new OANDA data for %s min while FX is open (limit %d min)" % (int((age or 0) // 60), stale_s // 60),
           "closed": "FX market closed (weekend) - last data %s" % wita(lg, now), "not_configured": "no OANDA practice token on this server"}[st] + note
    nb = "OANDA refused our practice token - Brett may need to issue a new practice token" if (st == "down" and re.search(r"HTTP 40[13]", str(err))) else None
    return _row("oanda", st, lg, age, now, err, det, source="OANDA practice API (api-fxpractice, paper account): account + pricing + candles", path=P["oanda_snap"],
                unit="tfo-thf-oanda-check", stale_s=stale_s, session="fx open" if op else "fx closed", needs_brett=nb)


# ---- Unusual Whales -----------------------------------------------------------------------------------------------------------------
def unusual_whales(now, ks, X):
    hb = X["hb"] if "hb" in X else (_rj(P["hb"], {}) or {})
    mem = ks.setdefault("_vendor_health", {}); v = hb.get("vendors") or {}
    U = {k: v[k] for k in ("uw_news", "uw_flow") if isinstance(v.get(k), dict)}
    hbt = _parse(hb.get("utc"))
    if hbt and U and any(x.get("status") == "ok" and not x.get("paced") for x in U.values()):
        if not _parse(mem.get("uw_last_ok")) or _parse(mem.get("uw_last_ok")) < hbt: mem["uw_last_ok"] = _iso(hbt)
    if not mem.get("uw_last_ok"):
        q = X["uw_queue_last"] if "uw_queue_last" in X else _queue_last("uw_")
        if q: mem["uw_last_ok"] = _iso(q)
    lg = _parse(mem.get("uw_last_ok"))
    env = (_unit("tfo-thf-catalyst-stream.service", X).get("env") or {})
    gap = max([int(env.get(k) or d) for k, d in (("CATALYST_UW_NEWS_GAP_S", 180), ("CATALYST_UW_FLOW_GAP_S", 90))] + [20])
    stale_s = max(1800, 3 * gap); op = us_session(now)
    inactive = [k for k, x in U.items() if x.get("status") == "inactive" or "key missing" in str(x.get("err") or "")]
    conf = (X["conf"].get("unusual_whales", True) if "conf" in X else _has_var("unusual_whales.env", "UNUSUAL_WHALES_API_KEY")) and not (U and len(inactive) == len(U))
    bad = {k: x for k, x in U.items() if x.get("status") in ("fail", "backoff")}
    err = "; ".join("%s: %s" % (k.replace("uw_", ""), x.get("err") or x.get("status")) for k, x in sorted(bad.items())) or None
    if not U and hb: err = err or "the news stream does not report Unusual Whales any more"
    hb_age = (now - hbt).total_seconds() if hbt else None
    if hb_age is not None and hb_age > 300 and not err: lg = min(lg, hbt) if lg else hbt   # stream stopped: no new UW data since its last heartbeat
    st, age = _decide(conf, err, lg, now, stale_s, op)
    parts = ", ".join("%s %s%s" % (k.replace("uw_", ""), x.get("status"), (" (%s items)" % x.get("n")) if x.get("n") else "") for k, x in sorted(U.items())) or "not in the news stream"
    det = {"ok": "answering: %s; polled every %ds" % (parts, gap), "down": "%s - the news stream keeps retrying (Unusual Whales was upgraded to unlimited on 3 Nov; a 429 now would mean their side)" % parts,
           "stale": "no new Unusual Whales data for %s min in US market hours (limit %d min)%s" % (int((age or 0) // 60), stale_s // 60, (" - the news stream heartbeat is %d min old" % (hb_age // 60)) if hb_age and hb_age > 300 else ""),
           "closed": "US market closed - last good %s (%s)" % (wita(lg, now), parts), "not_configured": "no Unusual Whales key on this server"}[st]
    return _row("unusual_whales", st, lg, age, now, err, det, source="Unusual Whales API: /api/news/headlines + /api/option-trades/flow-alerts (via the news stream)",
                path=P["hb"], unit="tfo-thf-catalyst-stream", stale_s=stale_s, session="us open" if op else "us closed",
                extra=dict(parts={k.replace("uw_", ""): dict(status=x.get("status"), n=x.get("n"), err=redact(x.get("err"), 120) if x.get("err") else None) for k, x in U.items()}))


# ---- EODHD REST (daily call quota) -------------------------------------------------------------------------------------------------
def eodhd_user(ks, X, now):
    """EODHD GET /api/user -> dict(requests, limit, extra, date, http, utc); cached EODHD_USER_EVERY_S in ks. Never raises."""
    if "user" in X: return X["user"]
    mem = ks.setdefault("_vendor_health", {}); c = mem.get("eodhd_user") or {}
    if c and time.time() - float(c.get("t") or 0) < EODHD_USER_EVERY_S: return c
    out = dict(t=time.time(), utc=_iso(now))
    key = _env_val("eodhd.env", "EODHD_API_KEY")
    if not key: out.update(http=None, err="no EODHD key"); mem["eodhd_user"] = out; return out
    try:
        req = urllib.request.Request("https://eodhd.com/api/user?fmt=json&api_token=" + key, headers={"User-Agent": "TFO paper desk health"})
        with urllib.request.urlopen(req, timeout=10) as f:
            j = json.loads(f.read()); out.update(http=f.status, requests=j.get("apiRequests"), limit=j.get("dailyRateLimit"), extra=j.get("extraLimit"), date=j.get("apiRequestsDate"))
    except urllib.error.HTTPError as e: out.update(http=e.code, err="HTTP %s" % e.code)
    except Exception as e: out.update(http=None, err=redact("%s %s" % (type(e).__name__, e), 120))
    mem["eodhd_user"] = out; return out
def eodhd(now, ks, X):
    hb = X["hb"] if "hb" in X else (_rj(P["hb"], {}) or {})
    mem = ks.setdefault("_vendor_health", {}); v = hb.get("vendors") or {}; hbt = _parse(hb.get("utc"))
    E = {k: v[k] for k in ("eodhd_news", "eodhd_earnings") if isinstance(v.get(k), dict)}
    conf = X["conf"].get("eodhd", True) if "conf" in X else _has_var("eodhd.env", "EODHD_API_KEY")
    u = eodhd_user(ks, X, now) if conf else {}
    try: used, lim, ext = int(u.get("requests") or 0), int(u.get("limit") or 0), int(u.get("extra") or 0)
    except Exception: used, lim, ext = 0, 0, 0
    exhausted = bool(lim) and used >= lim + ext
    r402 = any("402" in str(x.get("err") or "") for x in E.values())
    if hbt and any(x.get("status") == "ok" and not x.get("paced") for x in E.values()):
        if not _parse(mem.get("eodhd_last_ok")) or _parse(mem.get("eodhd_last_ok")) < hbt: mem["eodhd_last_ok"] = _iso(hbt)
    quota = exhausted or r402
    if quota and not mem.get("eodhd_refused_since"): mem["eodhd_refused_since"] = _iso(now)
    if not quota: mem.pop("eodhd_refused_since", None)
    bad = {k: x for k, x in E.items() if x.get("status") in ("fail", "backoff")}
    err = None
    if quota: err = "HTTP 402 - daily call limit used up (%s of %s calls used%s)" % ("{:,}".format(used), "{:,}".format(lim + ext) if lim else "?", ", no extra calls bought" if lim and not ext else "")
    elif bad: err = "; ".join("%s: %s" % (k.replace("eodhd_", ""), x.get("err") or x.get("status")) for k, x in sorted(bad.items()))
    lg = _parse(mem.get("eodhd_last_ok"))
    if not lg and lim and not exhausted and u.get("http") == 200 and not bad: lg = _parse(u.get("utc"))   # first pass: counter readable and below the limit
    st, age = _decide(conf, err, lg, now, 10 ** 9, True)
    if st in ("stale", "closed"): st = "ok"   # no session rule: many jobs call EODHD; 'down' needs a failed request
    rs = next_utc_midnight(now)
    pct = (100.0 * used / (lim + ext)) if lim else None
    det = ("EODHD's daily call limit is used up (%s of %s calls on %s), so every EODHD data call is refused with HTTP 402 until the counter resets at "
           "00:00 UTC = %s. The key is shared with Brett's Mac jobs. This check first saw the refusal at %s." % ("{:,}".format(used), "{:,}".format(lim + ext) if lim else "?", u.get("date") or "?", wita(rs, now), wita(mem.get("eodhd_refused_since"), now))) if quota else \
          ("answering; %s of today's %s calls used (%.0f%%)" % ("{:,}".format(used), "{:,}".format(lim + ext), pct) if (st == "ok" and pct is not None) else
           ("the last EODHD request failed" if st == "down" else ("no EODHD key on this server" if st == "not_configured" else "answering")))
    nb = ("Buy extra EODHD calls for today (EODHD account page), or switch off the Mac jobs that use the same EODHD key (Health tab, 'Your Mac'). "
          "Otherwise it comes back by itself at %s." % wita(rs, now)) if quota else None
    return _row("eodhd", st, lg, age, now, err, det, source="EODHD REST API (eodhd.com/api: intraday, real-time, news, earnings, EOD) - shared key, 100k calls/day",
                path=P["hb"], unit=None, session=None, needs_brett=nb, vendor_side=bool(quota) or None,
                extra=dict(quota=dict(used=used, limit=lim, extra=ext, date=u.get("date"), pct=(round(pct, 1) if pct is not None else None), exhausted=exhausted,
                                      resets_utc=_iso(rs), resets_wita=wita(rs, now), checked_wita=wita(u.get("utc"), now), http=u.get("http")),
                           parts={k.replace("eodhd_", ""): dict(status=x.get("status"), err=redact(x.get("err"), 120) if x.get("err") else None) for k, x in E.items()}))


# ---- Massive (US prices + news) ---------------------------------------------------------------------------------------------------
def massive(now, ks, X):
    hb = X["hb"] if "hb" in X else (_rj(P["hb"], {}) or {})
    ih = X["intraday"] if "intraday" in X else (_rj(P["intraday"], {}) or {})
    mem = ks.setdefault("_vendor_health", {}); v = hb.get("vendors") or {}; hbt = _parse(hb.get("utc"))
    m = v.get("massive_news") if isinstance(v.get("massive_news"), dict) else {}
    if hbt and m.get("status") == "ok" and not m.get("paced"):
        if not _parse(mem.get("massive_last_ok")) or _parse(mem.get("massive_last_ok")) < hbt: mem["massive_last_ok"] = _iso(hbt)
    goods = [t for t in (_parse(mem.get("massive_last_ok")), _parse(ih.get("newest_utc"))) if t]
    lg = max(goods) if goods else None
    conf = X["conf"].get("massive", True) if "conf" in X else _has_var("massive.env", "MASSIVE_API_KEY")
    err = ("news: %s" % (m.get("err") or m.get("status"))) if m.get("status") in ("fail", "backoff") else None
    stale_s = max(1800, 3 * INTRADAY_POLL_S); op = us_session(now)
    st, age = _decide(conf, err, lg, now, stale_s, op)
    det = {"ok": "answering: news %s, newest US minute bar %s" % (m.get("status") or "?", wita(ih.get("newest_utc"), now)), "down": "the last Massive news request failed (US prices may still come in)",
           "stale": "no new Massive data for %s min in US market hours (limit %d min)" % (int((age or 0) // 60), stale_s // 60), "closed": "US market closed - newest US minute bar %s" % wita(ih.get("newest_utc"), now),
           "not_configured": "no Massive key on this server"}[st]
    return _row("massive", st, lg, age, now, err, det, source="Massive API (US real-time aggregates + Benzinga news)", path=P["intraday"], unit="tfo-thf-intraday-bars",
                stale_s=stale_s, session="us open" if op else "us closed")


# ---- helpers ------------------------------------------------------------------------------------------------------------------
def _tail(p, n=600):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - n)); return redact(f.read().decode(errors="replace").strip().splitlines()[-1] if sz else "", 160)
    except Exception: return ""
def _last_jsonl(p):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - 4000)); ln = [x for x in f.read().decode(errors="replace").splitlines() if x.strip()]
        return json.loads(ln[-1]) if ln else None
    except Exception: return None
def _queue_last(prefix):
    """Newest fetched_utc of a news-stream queue item whose source starts with prefix (first pass only)."""
    best = None
    try:
        for f in os.listdir(P["queues"]):
            try:
                with open(os.path.join(P["queues"], f), "rb") as fh:
                    fh.seek(0, 2); sz = fh.tell(); fh.seek(max(0, sz - 200000)); lines = fh.read().decode(errors="replace").splitlines()
            except Exception: continue
            for ln in reversed(lines):
                if ('"source": "%s' % prefix) not in ln: continue
                try: t = _parse(json.loads(ln).get("fetched_utc"))
                except Exception: t = None
                if t and (not best or t > best): best = t
                break
    except Exception: return None
    return best


def rows(ks=None, now=None, X=None):
    """{'vendor:oanda': row, 'vendor:unusual_whales': row, 'vendor:eodhd': row, 'vendor:massive': row}. Never raises."""
    ks = ks if isinstance(ks, dict) else {}; X = X or {}; now = now or X.get("now") or _now(); out = {}
    for vid, fn in (("oanda", oanda), ("unusual_whales", unusual_whales), ("eodhd", eodhd), ("massive", massive)):
        try: out["vendor:" + vid] = fn(now, ks, X)
        except Exception as e: out["vendor:" + vid] = dict(kind="vendor", vendor=NAMES[vid], status="unknown", detail="health check error %s" % type(e).__name__, age_s=None, readers=READERS.get(vid, []), tag=TAG)
    return out
def broken_words(r):
    """One short plain line for a vendor row that counts as broken (down / stale / not_configured), else None."""
    if not isinstance(r, dict) or r.get("status") not in ("down", "stale", "not_configured"): return None
    nm = r.get("vendor") or "?"
    if r.get("status") == "down": return "%s DOWN: %s%s" % (nm, r.get("last_error") or "request failed", " (needs Brett)" if r.get("needs_brett") else "")
    if r.get("status") == "stale": return "%s STALE: no new data for %s min (last good %s)" % (nm, r.get("age_min"), r.get("last_good_wita"))
    return "%s not configured (no key on this server)" % nm


if __name__ == "__main__":   # debug: python -m veteran_desk.vendor_health  (redacted rows; one EODHD /user call)
    print(json.dumps(rows({}), indent=1, default=str))
