#!/opt/tfo/venv/bin/python
"""tfo_feedkeeper_20261102 (PAPER): code-only THF feed freshness keeper. No AI. Checks every THF feed ~60s,
self-heals (restart unit -> retry fetch -> free fallback), backs off, quarantines bad data, shadow-tests free helpers.
Never writes primary feed files, ledgers or book files; fallbacks go to state/thf/feed_fallback/."""
import json, os, time, subprocess, datetime as dt, shutil, sqlite3
import requests
L = "/opt/tfo/live/thf"; S = "/opt/tfo/state"; T = L + "/state/thf"
FB = T + "/feed_fallback"; QD = T + "/feed_quarantine"
STATUS = T + "/feed_status.json"; HEAL = T + "/feed_heal.jsonl"; HELP = T + "/feed_helpers.json"; KS = T + "/feed_keeper_state.json"
UA = {"User-Agent": "Mozilla/5.0 tfo-feedkeeper"}
def utcnow(): return dt.datetime.now(dt.timezone.utc)
def weekend_us():  # US equity closed Sat 00:00 ET .. Sun 18:00 ET approx (UTC-5)
    n = utcnow() - dt.timedelta(hours=5); return n.weekday() == 5 or (n.weekday() == 6 and n.hour < 18) or (n.weekday() == 4 and n.hour >= 17)
def asia_in_session():  # paidbars_idle_20261102: paid_bars log is only written by Asia/ASX hunts
    try: return bool(json.load(open(T + "/asia_intraday_status.json")).get("markets_in_session"))
    except Exception: return True
# name: (source, path, unit|None, cadence_s, max_age_s, kind, fallback)
FEEDS = {
 "yahoo_quotes":   ("Yahoo chart v8 (16 idx/FX/BTC; ASX ~21m delayed by source)", S+"/yahoo.json", "tfo-yahoo", 60, 300, "prices", "yahoo"),
 "coingecko":      ("CoinGecko simple/price", S+"/coingecko.json", "tfo-coingecko", 90, 450, "prices", "crypto"),
 "kraken_ticker":  ("Kraken WS ticker", S+"/kraken_ticker.json", "tfo-kraken", 1, 120, "prices", "crypto"),
 "kraken_tape":    ("Kraken WS trades", S+"/kraken_tape.jsonl", "tfo-kraken", 1, 180, "nonempty", "crypto"),
 "thf_wstape":     ("Kraken WS -> ws_tape.sqlite", L+"/reports/ws_tape.sqlite", "tfo-thf-wstape", 1, 180, "sqlite", "crypto"),
 "news_rss":       ("Fed/MarketWatch/CoinDesk/CNBC RSS", S+"/news.heartbeat", "tfo-news", 60, 300, "heartbeat", "news"),
 "thf_newsbot":    ("THF news_collector (crypto RSS)", L+"/reports/news/seen.json", "tfo-thf-news", 300, 1200, "json", "news"),
 "announcements":  ("SEC 8-K atom + ASX announcements", S+"/announce_scanner.heartbeat", "tfo-announce", 900, 2100, "heartbeat", "news"),
 "news_latency":   ("forward_edge news latency archive", "/opt/tfo/data/forward_edge_collectors/news_latency.sqlite.offset", "tfo-fe-news-latency", 60, 300, "nonempty", None),
 "thf_msgdesk":    ("THF msgdesk funder inbox consumer (heartbeat)", L+"/state/thf/msgdesk_heartbeat", "tfo-thf-msgdesk", 8, 60, "epoch", None),   # tfo_msgdesk_20261102
 "feed_ok":        ("Mac->droplet input sync (.feed_ok) - BACKUP ONLY since sgpfeeds_20261103", L+"/.feed_ok", "tfo-thf-feed", 120, 900, "epoch", None),
 "intraday_bars":  ("Mac data_sync intraday bars copy - BACKUP ONLY (droplet writes its own, see intraday_pull)", "/opt/tfo/data/sqlite/intraday_bars.sqlite", None, 120, 1800, "nonempty", None),
 "audusd":         ("AUDUSD last-good (FX)", T+"/audusd_last_good.json", None, 60, 900, "json", "yahoo"),
 "eodhd_eu":       ("EODHD CBOE Europe WS (health)", L+"/state/eodhd_eu/health.json", "tfo-thf-eodhd-eu", 60, 600, "json", None),  # tfo_eodhdsgp_20261102
 "us_stream_fmp":  ("US quote/trade WS (parallel_desk research ledger, Mac; no THF reader) - BACKUP ONLY", L+"/state/parallel_desk/us_stream_status.json", None, 60, 600, "json", "yahoo"),
 "paid_bars":      ("paid bars fetch log (EODHD/FMP)", L+"/state/paid_bars/fetch_%s.jsonl", None, 300, 3600, "nonempty", None),
 # feed100_20261102: droplet-native feeds + previously untracked inputs
 "pit_eod_daily":  ("EODHD bulk EOD + Yahoo fallback -> pit.sqlite (droplet)", L+"/state/thf/pit_eod_status.json", "tfo-thf-pit-eod", 21600, 108000, "json", None),
 "pit_fx_daily":   ("FX daily bars (OANDA practice candles for OANDA-book pairs, Yahoo for the rest) + futures + indices -> pit.sqlite (droplet)", L+"/state/thf/pit_fx_status.json", "tfo-thf-pit-fx", 43200, 181000, "json", None),   # fxfeed_20261103
 "oanda_practice:oanda": ("OANDA practice sleeve check every 15 min on the droplet (tfo-thf-oanda-check: stop fills, stop reconcile, exits, queued entries, NAV)", L+"/state/oanda/reconcile_health.json", "tfo-thf-oanda-check", 900, 2700, "json", None),   # fxfeed_20261103 (master GO 17:15 3 Nov)
 "firm_risk_service": ("firm risk service on the droplet (tfo-thf-firm-risk risk-serve every 15 s: firm heat/halt + OANDA entry admits, which fail closed when this is >90 s old)", L+"/reports/firm_risk_service.json", "tfo-thf-firm-risk", 15, 90, "json", None),   # fxfeed_20261103 (master GO 17:25)
 "oanda_practice:oanda_aggro": ("OANDA AGGRO sleeve (runs inside the 15-min check: exits, trails, missing-stop restore)", L+"/state/oanda_aggro/stats.json", "tfo-thf-oanda-check", 900, 2700, "json", None),   # fxfeed_20261103
 "asia_intraday":  ("ASX/KR/TW/SG EODHD 5m + HK/JP Yahoo 5m -> bars.sqlite", L+"/state/thf/asia_intraday_status.json", "tfo-thf-asia-intraday", 300, 900, "json", None),
 "bars_sqlite":    ("droplet intraday bars.sqlite (Mac copy + droplet writers)", L+"/data/intraday/bars.sqlite", None, 300, 3600, "sqlite", None),
 "coinalyze":      ("Coinalyze OI/funding/liq/LS (paid pack)", L+"/state/data_hub/coinalyze_latest.json", None, 300, 3600, "json", None),
 "catalyst_stream":("catalyst stream on droplet (FMP earnings/8-K/press, ASX, UW, EDGAR F4, TDnet) - sgpfeeds_20261103", L+"/state/catalyst_stream/heartbeat.json", "tfo-thf-catalyst-stream", 20, 300, "json", None),
 # sgpfeeds_20261103: the four feeds that used to be Mac-only, now droplet timers (Mac rsync is backup-only)
 "news_desk":      ("news_desk regime flag + headlines -> news.json (droplet timer 10 min)", L+"/state/veteran_desk/news.json", "tfo-thf-news-desk", 600, 1500, "json", "news"),
 "event_calendar": ("event calendar earnings/macro 72h (droplet timer 30 min)", L+"/state/calendar/calendar_latest.json", "tfo-thf-event-calendar", 1800, 5400, "json", None),
 "edgar_8k":       ("EDGAR 8-K cache top-up (droplet timer 15 min)", L+"/state/veteran_desk/edgar_8k_cache.json", "tfo-thf-edgar8k", 900, 3600, "json", None),
 "intraday_pull":  ("US/EU 1m-15m bars pull Massive+EODHD -> bars.sqlite (droplet timer 15 min)", L+"/data/intraday/health.json", "tfo-thf-intraday-bars", 900, 2700, "json", None),
 "kraken_sweep":   ("Kraken 24h traded-value sweep -> kraken_24h_all.json (Kai universe; droplet timer 4h)", L+"/state/veteran_desk/kraken_24h_all.json", "tfo-thf-kraken-sweep", 14400, 21600, "json", None),
 "feed_delay":     ("venue feed-delay labels Yahoo probes -> feed_delay.json (droplet timer 5 min)", L+"/state/feed_delay.json", "tfo-thf-feed-delay", 300, 1200, "json", None),
 "ibkr_borrow":    ("IBKR FTP borrow (droplet timer)", L+"/state/ibkr_borrow.json", "tfo-thf-borrow", 3600, 10800, "json", None),
 "earnings_cache": ("earnings calendar cache (Mac hunt48 value_entry_guards; THF seats use calendar_latest.json) - BACKUP ONLY", L+"/reports/earnings_cache.json", None, 86400, 129600, "json", None),
 "sector_fill":    ("sector tags fill (EODHD General)", L+"/state/thf/sector_fill_status.json", None, 86400, 4*86400, "json", None),
 "sbx_feed_ok":    ("sandbox .feed_ok (sbx_thf, not live)", "/opt/tfo/sbx_thf/.feed_ok", None, 120, 10**9, "epoch", None),
}
OANDA_DIRS = [L+"/state/oanda_lab"]   # fxfeed_20261103: state/oanda + oanda_aggro are FEEDS rows (tfo-thf-oanda-check); the lab is not run on the droplet yet
OANDA_PAUSED = {   # fxfeed_20261103 (Brett 16:13 "make sure all feeds are 100%"): say what it really is, never a fake ok
 "oanda_lab": "OANDA shadow strategy lab (research, GET only, never trades): PAUSED - its code needs numba, which the droplet venv does not have (the Mac venv is not portable); Mac job com.tfo.oanda-lab was switched off 2 Nov 09:32 WITA (SGP1-only rule). Follow-up: own venv + RAM check.",
}
def oanda_detail(name, o):
    """fxfeed_20261103: plain detail for the OANDA rows; (status_override or None, text)."""
    try:
        if name == "oanda_practice:oanda":
            import sys; sys.path.insert(0, L) if L not in sys.path else None
            import oanda_reconcile_guard as RG
            g = RG.board(L + "/state/oanda"); sn = jload(L + "/state/oanda/snapshot.json", {}) or {}
            fr = jload(L + "/reports/firm_risk_service.json", {}) or {}; ov = fr.get("oanda") or {}
            try: fage = time.time() - dt.datetime.fromisoformat(str(fr.get("utc")).replace("Z", "+00:00")).timestamp()
            except Exception: fage = None
            if fage is None or fage > 90: ent = "new entries FAIL CLOSED: the droplet firm risk service (tfo-thf-firm-risk, every 15 s) last wrote %s ago (limit 90 s)" % ("?" if fage is None else ("%ds" % fage if fage < 3600 else "%dh" % (fage // 3600)))   # healthdesk_20261103: the unit exists since fxfeed 17:25 - the old 'no droplet unit' text was out of date
            elif not fr.get("new_entries_allowed") or not ov.get("ok"): ent = "new entries blocked by the firm risk service (%s)" % (fr.get("reason") or ov.get("reason"))
            elif int(ov.get("open_n") or 0) >= int(ov.get("max_open") or 4) or float(ov.get("open_risk") or 0) >= float(ov.get("risk_cap") or 0.02): ent = "new entries blocked by HT's OANDA rails (%s/%s trades open, open risk %.2f%% vs %.0f%% cap) until a trade closes" % (ov.get("open_n"), ov.get("max_open"), 100 * float(ov.get("open_risk") or 0), 100 * float(ov.get("risk_cap") or 0.02))
            else: ent = "new entries allowed within OANDA rails (%s/%s open, open risk %.2f%% of %.0f%%)" % (ov.get("open_n"), ov.get("max_open"), 100 * float(ov.get("open_risk") or 0), 100 * float(ov.get("risk_cap") or 0.02))
            txt = "reconcile %s (last clean %s UTC), NAV %s %s, %s open trades (%s TFO); %s" % (g.get("reconcile_status"), str(g.get("reconcile_last_clean_utc"))[:16], sn.get("currency"), round(float(sn.get("nav") or 0)), sn.get("open_trade_count"), sn.get("tfo_open"), ent)
            return (None if g.get("reconcile_status") == "CLEAN" else "invalid"), txt
        if name == "oanda_practice:oanda_aggro":
            c = jload(L + "/state/oanda_aggro/config.json", {}) or {}
            orj = jload(L + "/state/oanda_aggro/open_risk.json", {}) or {}; rows = orj.get("rows") or []
            return None, "%d AGGRO trades open (account open risk %s%% across %d sleeve trades); new AGGRO entries %s" % (sum(1 for x in rows if x.get("owner") == "AGGRO"), orj.get("total_pct", "?"), len(rows), "OFF (HT 31 Oct config: no designs)" if not c.get("designs") else "on: " + ",".join(map(str, c.get("designs"))))
    except Exception as e: return None, "detail error %s" % type(e).__name__
    return None, ""
# sgpfeeds_20261103: feeds that only mirror the Mac. When the Mac is not pushing at all they are reported as "backup_idle"
# (not stale, no heal, not an alarm): THF runs on the droplet-native feeds. If the Mac IS pushing but the file is stale,
# the normal stale -> heal path still runs (e.g. a stuck rsync).
BACKUP_ONLY = {"feed_ok", "intraday_bars", "us_stream_fmp", "sbx_feed_ok", "earnings_cache"}
MAC_PUSH_MARKS = ["/opt/tfo/data/sqlite/MANIFEST.json", "/opt/tfo/data/state/catalyst_stream/heartbeat.json", "/opt/tfo/data/state/thf/thf_book.json"]
# healthdesk_20261103 (SGP watcher fault 'feeds' red since 20:11 3 Nov = earnings_cache 'stale'; Brett 20:16 "CHECK THE HEALTH DESK"):
# a BACKUP_ONLY mirror whose copy here is as new as the Mac's own file (the 2-min rsync is NOT stuck) is not a broken droplet feed:
# the Mac job that writes it simply has not rewritten it. earnings_cache = the Mac value book's earnings guard cache (value_entry_guards
# via value_deploy, Mac desk_tick.sh); no Singapore code reads it, THF seats use the event calendar. It aged past 36 h at 20:09 WITA
# (Mac last wrote it 2 Nov 08:09 WITA) and the keeper 'healed' it every 30 min with "observe-only". Stuck rsync (Mac copy newer) -> stale.
MAC_MIRROR = {"earnings_cache": "/opt/tfo/data/reports/earnings_cache.json", "us_stream_fmp": "/opt/tfo/data/state/parallel_desk/us_stream_status.json"}
MIRROR_NOTE = {"earnings_cache": "used only by the Mac's value book entry guard (value_entry_guards.py); THF seats use the event calendar (event_calendar row)",
               "us_stream_fmp": "Mac research ledger stream; no THF reader"}
def mirror_current(name, path, slack_s=300):
    """(True, mac_mtime) when the Mac source of a BACKUP_ONLY mirror exists and is not newer than our copy (+ one sync cycle)."""
    src = MAC_MIRROR.get(name)
    try:
        sm = os.path.getmtime(src); return (sm <= os.path.getmtime(path) + slack_s), sm
    except Exception: return False, None
def mac_pushing(max_age=900):
    for p in MAC_PUSH_MARKS:
        try:
            if time.time() - os.path.getmtime(p) < max_age: return True
        except Exception: pass
    return False
def jload(p, d):
    try: return json.load(open(p))
    except Exception: return d
def jsave(p, o):
    tmp = p + ".tmp%d" % os.getpid(); json.dump(o, open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
    try: shutil.chown(p, "tfo", "tfo")
    except Exception: pass
def _own(p):   # healthdesk_20261103 (master 22:3x): this keeper runs as root; files/dirs it creates under state/thf must stay tfo:tfo, or the tfo writers
    try:      # (msgdesk pack_check, thf_scan) of feed_heal.jsonl get EACCES (22:29:49 WITA 3 Nov) and feed_fallback/ + feed_quarantine/ were root-only
        if os.geteuid() == 0 and os.stat(p).st_uid != 0: return
        if os.geteuid() == 0: shutil.chown(p, "tfo", "tfo")
    except Exception: pass
def heal_log(r):
    r["ts_utc"] = utcnow().isoformat(timespec="seconds")
    with open(HEAL, "a") as f: f.write(json.dumps(r) + "\n")
    _own(HEAL)
def nums(o, out, pre=""):
    if isinstance(o, dict):
        for k, v in o.items(): nums(v, out, pre + str(k) + ".")
    elif isinstance(o, list) and o and len(o) <= 4:
        for i, v in enumerate(o[:1]): nums(v, out, pre + str(i) + ".")
    elif isinstance(o, (int, float)) and not isinstance(o, bool) and 0 < abs(o) < 1e7 and any(x in pre.lower() for x in ("price", "last", "usd", "close", "bid", "ask", "mid", "rate", ".c.", ".a.", ".b.")) and not any(x in pre.lower() for x in ("time", "ts", "updated", "change", "vol", "count", "qty", "size", "amount", "lot", "_at")):
        out[pre[:-1]] = float(o)
def check(name, cfg, prev):
    src, path, unit, cad, maxage, kind, fbk = cfg
    if "%s" in path: path = path % time.strftime("%Y%m%d")
    r = {"source": src, "path": path, "unit": unit, "cadence_s": cad, "max_age_s": maxage, "status": "ok", "detail": ""}
    if not os.path.exists(path): r.update(status="missing", age_s=None); return r, None
    age = time.time() - os.path.getmtime(path); r["age_s"] = int(age)
    snap = None
    try:
        if kind in ("json", "prices"):
            o = json.load(open(path))
            if not o: r.update(status="invalid", detail="empty json")
            if kind == "prices":
                snap = {}; nums(o, snap); old = (prev or {}).get("snap") or {}
                bad = [k for k, v in snap.items() if k in old and old[k] > 0 and abs(v / old[k] - 1) > 0.25]
                if bad: r.update(status="quarantined", detail="price jump >25%%: %s" % bad[:3])
            if name in ("pit_eod_daily",):   # feed100_20261102: per-exchange coverage in detail
                ex = o.get("exchanges") or {}; low = sorted((v.get("coverage_pct") or 0, k) for k, v in ex.items() if v.get("liquid"))
                r["coverage"] = {k: dict(last_date=v.get("last_date"), coverage_pct=v.get("coverage_pct")) for k, v in ex.items()}
                r["detail"] = "%d exchanges; lowest coverage %s" % (len(ex), low[:2])
            if name == "pit_fx_daily":   # fxfeed_20261103: per-market freshness (fx / fut / idx) in detail
                cov = {kk: dict(n=v.get("n"), ok=v.get("ok"), fresh=v.get("fresh"), newest=v.get("newest"), failed=v.get("failed"), mismatch=v.get("mismatch")) for kk, v in o.items() if isinstance(v, dict) and "fresh" in v}
                r["coverage"] = cov; r["detail"] = "; ".join("%s %s/%s fresh, newest %s, %s failed" % (kk, v["fresh"], v["n"], v["newest"], v["failed"]) for kk, v in cov.items()) or "no markets"
            if name == "firm_risk_service":   # fxfeed_20261103
                ov = o.get("oanda") or {}
                r["detail"] = "%s, firm new entries %s (heat %.2f%% vs %.0f%% cap); OANDA %s/%s open, open risk %.2f%%" % (o.get("level"), "allowed" if o.get("new_entries_allowed") else "HALTED (%s)" % o.get("reason"), 100 * float(o.get("firm_heat") or 0), 100 * float(o.get("heat_cap") or 0), ov.get("open_n"), ov.get("max_open"), 100 * float(ov.get("open_risk") or 0))
            if name in ("oanda_practice:oanda", "oanda_practice:oanda_aggro"):   # fxfeed_20261103
                so, r["detail"] = oanda_detail(name, o)
                if so: r.update(status=so)
            if name == "asia_intraday":
                r["coverage"] = dict(in_session=o.get("markets_in_session"), markets=o.get("markets"), syms_8h=o.get("syms_with_bars_8h"), ok=o.get("fetched_ok"), fail=o.get("fetched_fail"), last_bar=o.get("last_bar_utc"), data_status=o.get("data_status"))
                r["detail"] = "in session %s, syms(8h)=%s, last_bar=%s, data=%s" % (o.get("markets_in_session"), o.get("syms_with_bars_8h"), o.get("last_bar_utc"), o.get("data_status"))
                if o.get("data_status") == "stale_underlying" or (o.get("markets_in_session") and not o.get("last_bar_utc")):
                    r.update(status="degraded", detail="Asia file is recent but underlying bars are empty or old: " + r["detail"])
                elif o.get("data_status") == "session_closed":
                    r.update(status="closed", detail="Asia session closed; file rewrite is not bar freshness. " + r["detail"])
            if name == "sector_fill": r["detail"] = "coverage %s%%, still missing %s" % (o.get("coverage_pct"), o.get("still_missing"))
            if name == "catalyst_stream":
                bad = {k: v.get("err") for k, v in (o.get("vendors") or {}).items() if v.get("status") not in ("ok", "disabled")}   # sgpfeeds_20261103: switched-off vendors are not failures
                if bad: r["detail"] = "vendors not ok: %s" % bad
            if name == "us_stream_fmp" or name == "eodhd_eu":
                down = [k for k, v in (o.get("streams") or {}).items() if (v == "down" if name == "eodhd_eu" else v != "up")]   # eufeed_20261103: EU 'off' (eu-quote not enabled) / 'off: quota' are not faults
                if down: r["detail"] = "streams down: %s" % down
            if name == "eodhd_eu":
                if "authorized" in o: r["authorized"] = True if o.get("authorized") is True else False if o.get("authorized") is False else None
                if isinstance(o.get("last_status"), dict): r["last_status"] = dict(code=o["last_status"].get("code"), message=o["last_status"].get("message"), utc=o["last_status"].get("utc"))
            if name == "eodhd_eu" and "healthy" in o and age <= maxage:   # eufeed_20261103: the writer's verdict counts - 0 live symbols was 'ok' here all day 3 Nov
                if o["healthy"] and age <= 120: r["detail"] = str(o.get("why") or r["detail"])[:400]
                else: r.update(status="degraded" if o.get("markets_open", True) else "closed", detail=("heartbeat %ds old" % age) if o["healthy"] else str(o.get("why") or "not healthy")[:400])
        elif kind == "epoch":
            v = float(open(path).read().strip()); age = time.time() - v; r["age_s"] = int(age)
        elif kind == "heartbeat":
            v = float(open(path).read().strip() or 0)
            if v > 1e9: age = max(age, time.time() - v); r["age_s"] = int(age)
        elif kind == "sqlite":
            w = path + "-wal"
            if os.path.exists(w): age = min(age, time.time() - os.path.getmtime(w)); r["age_s"] = int(age)
            c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5); c.execute("select 1").fetchone(); c.close()
        if os.path.getsize(path) == 0: r.update(status="invalid", detail="empty file")
    except Exception as e:
        r.update(status="invalid", detail="parse: %s" % str(e)[:80])
    if r["status"] == "ok" and age > maxage:
        if name in ("us_stream_fmp", "eodhd_eu", "paid_bars", "pit_eod_daily", "pit_fx_daily", "bars_sqlite", "asia_intraday", "earnings_cache", "intraday_pull") and weekend_us(): r.update(status="closed", detail="market closed (weekend)")
        elif name == "paid_bars" and not asia_in_session(): r.update(status="closed", detail="asia hunts idle (no Asia market in session)")  # paidbars_idle_20261102
        else: r["status"] = "stale"
    if name in BACKUP_ONLY and r["status"] in ("stale", "missing") and (not mac_pushing() or os.path.exists(L + "/state/thf/MAC_SYNC_PAUSE")): r.update(status="backup_idle", detail=("Mac not pushing: " if not mac_pushing() else "Mac-to-Singapore sync paused on purpose (state/thf/MAC_SYNC_PAUSE): ") + "backup-only mirror feed, THF runs on droplet-native feeds")   # sgpfeeds_20261103; backuponly_20261106 (master fixer, E6): the planned pause is not a fault; a stuck rsync while the Mac pushes still shows stale (healthdesk_20261103)
    elif name in BACKUP_ONLY and r["status"] == "stale" and name in MAC_MIRROR:   # healthdesk_20261103: current mirror of a Mac-only file -> not a droplet fault
        cur, sm = mirror_current(name, path)
        if cur: r.update(status="backup_idle", detail="Mac-only file, copy here is current: the Mac job that writes it last did so %s; %s" % (
            (dt.datetime.fromtimestamp(sm, dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%d %b %H:%M WITA"), MIRROR_NOTE.get(name, "no THF reader on Singapore")))
    if unit:
        a = subprocess.run(["systemctl", "is-active", unit + ".service"], capture_output=True, text=True).stdout.strip()
        r["unit_state"] = a
    if r["status"] == "quarantined":
        os.makedirs(QD, exist_ok=True); _own(QD)
        try: qp = QD + "/%s_%s%s" % (name, time.strftime("%Y%m%d%H%M%S"), os.path.splitext(path)[1]); shutil.copy2(path, qp); _own(qp)
        except Exception: pass
        snap = (prev or {}).get("snap")  # keep last good reference, do not accept bad
    return r, snap
# ---- free fallbacks (keyless, public). Write only into FB dir, validated. ----
def fb_yahoo():
    syms = ["^AXJO", "^GSPC", "AUDUSD=X", "BTC-USD", "GC=F", "CL=F"]; out = {}
    for s in syms:
        from veteran_desk.data_layer import yahoo_chart_response
        m = yahoo_chart_response(s, {"interval": "1m", "range": "1d"}, headers=UA, timeout=10).json()["chart"]["result"][0]["meta"]
        out[s] = {"price": m.get("regularMarketPrice"), "time": m.get("regularMarketTime")}
    return out
def fb_crypto():
    out = {}
    k = requests.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD,ETHUSD,SOLUSD", timeout=10).json()["result"]
    for p, v in k.items(): out["kraken." + p] = float(v["c"][0])
    try:
        g = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd", timeout=10).json()
        for p, v in g.items(): out["coingecko." + p] = v["usd"]
    except Exception: pass
    return out
def fb_news():
    import feedparser; items = []
    for u in ["https://www.federalreserve.gov/feeds/press_all.xml", "https://feeds.bbci.co.uk/news/business/rss.xml", "https://www.sec.gov/news/pressreleases.rss", "https://cointelegraph.com/rss"]:
        try:
            raw = requests.get(u, headers={"User-Agent": "tfo-feedkeeper research admin@example.com"}, timeout=10).content
            for e in feedparser.parse(raw).entries[:15]: items.append({"src": u, "title": e.get("title"), "link": e.get("link"), "published": e.get("published")})
        except Exception: pass
    if not items: raise RuntimeError("no rss items")
    return items
FALLBACK = {"yahoo": fb_yahoo, "crypto": fb_crypto, "news": fb_news}
def run_fallback(kind):
    data = FALLBACK[kind](); os.makedirs(FB, exist_ok=True); _own(FB)   # healthdesk_20261103
    if not data: raise RuntimeError("empty")
    jsave(FB + "/%s.json" % kind, {"ts_utc": utcnow().isoformat(timespec="seconds"), "paper_only": True, "data": data}); return len(data)
# ---- helpers discovery (shadow) ----
HELPERS = {
 "binance_btc": ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda j: float(j["price"]), "btc"),
 "coinbase_btc": ("https://api.coinbase.com/v2/prices/BTC-USD/spot", lambda j: float(j["data"]["amount"]), "btc"),
 "bitstamp_btc": ("https://www.bitstamp.net/api/v2/ticker/btcusd/", lambda j: float(j["last"]), "btc"),
 "frankfurter_audusd": ("https://api.frankfurter.app/latest?from=AUD&to=USD", lambda j: float(j["rates"]["USD"]), "audusd"),
 "bbc_business_rss": ("https://feeds.bbci.co.uk/news/business/rss.xml", None, "rss"),
 "sec_press_rss": ("https://www.sec.gov/news/pressreleases.rss", None, "rss"),
 "ecb_rss": ("https://www.ecb.europa.eu/rss/press.html", None, "rss"),
 "rba_rss": ("https://www.rba.gov.au/rss/rss-cb-media-releases.xml", None, "rss"),
}
PIP_HELPERS = ["yfinance", "feedparser", "ccxt", "pycoingecko", "pandas_datareader", "investpy"]
def ref_prices():
    ref = {}
    try:
        k = json.load(open(S + "/kraken_ticker.json")); n = {}; nums(k, n)
        b = [v for kk, v in n.items() if ("XBT" in kk.upper() or "BTC" in kk.upper()) and 1000 < v < 1e6]
        if b: ref["btc"] = b[0]
    except Exception: pass
    try:
        y = json.load(open(S + "/yahoo.json")); n = {}; nums(y, n)
        a = [v for kk, v in n.items() if "AUDUSD" in kk.upper() and 0.3 < v < 1.5]
        if a: ref["audusd"] = a[0]
    except Exception: pass
    return ref
def helpers_run():
    import importlib.util
    H = jload(HELP, {"candidates": {}}); C = H.setdefault("candidates", {}); ref = ref_prices(); now = time.time()
    for name, (url, fn, kind) in HELPERS.items():
        c = C.setdefault(name, {"url": url, "kind": kind, "type": "public_endpoint", "free": True, "promoted": False, "pass_since": None, "runs": 0, "passes": 0})
        ok, det = False, ""
        try:
            rq = requests.get(url, headers={"User-Agent": "tfo-feedkeeper research admin@example.com"}, timeout=8)
            if kind == "rss":
                import feedparser; e = feedparser.parse(rq.content).entries; ok = len(e) > 0; det = "%d items" % len(e)
            else:
                v = fn(rq.json()); r0 = ref.get(kind)
                if r0: dev = abs(v / r0 - 1); ok = dev < (0.005 if kind == "btc" else 0.01); det = "v=%.5g ref=%.5g dev=%.3f%%" % (v, r0, dev * 100)
                else: det = "v=%.5g no primary ref" % v
        except Exception as e: det = "err %s" % str(e)[:60]
        c["runs"] += 1; c["last_check_utc"] = utcnow().isoformat(timespec="seconds"); c["last_detail"] = det; c["last_ok"] = ok
        if ok:
            c["passes"] += 1; c["pass_since"] = c["pass_since"] or now
            if not c["promoted"] and now - c["pass_since"] >= 86400 and c["passes"] >= 20: c["promoted"] = True; c["promoted_utc"] = c["last_check_utc"]; heal_log({"feed": "helpers", "action": "promote", "helper": name})
        else:
            c["pass_since"] = None
            if c["promoted"]: c["promoted"] = False; heal_log({"feed": "helpers", "action": "demote", "helper": name, "detail": det})
        c["shadow_hours"] = round((now - c["pass_since"]) / 3600, 1) if c["pass_since"] else 0
    for p in PIP_HELPERS:
        inst = importlib.util.find_spec(p) is not None
        C.setdefault("pip:" + p, {"type": "pip_package", "free": True, "promoted": False})["installed"] = inst
    H["updated_utc"] = utcnow().isoformat(timespec="seconds"); H["rule"] = "free/keyless only; promote after >=24h continuous shadow pass vs primary"; jsave(HELP, H)
# ---- main loop ----
GRACE_S = {"thf_newsbot": 900}
def heal(name, cfg, r, ks):
    st = ks.setdefault(name, {"fails": 0, "next_try": 0, "last_heal": None})
    if time.time() < st["next_try"]: r["heal"] = "backoff"; return
    unit, fbk = cfg[2], cfg[6]; act = []
    if r["status"] == "quarantined": act.append("quarantined copy kept; consumers pointed to fallback")
    grace = None
    if unit and r["status"] != "quarantined":   # feeds_20261102: grace >=10 min after a (re)start so a slow poll (newsbot ~6 min) is never killed mid-run
        try:
            sh = dict(l.split("=", 1) for l in subprocess.run(["systemctl", "show", unit + ".service", "-p", "ActiveState", "-p", "ActiveEnterTimestampMonotonic"], capture_output=True, text=True, timeout=20).stdout.split())
            up = time.monotonic() - int(sh.get("ActiveEnterTimestampMonotonic") or 0) / 1e6
            if sh.get("ActiveState") in ("active", "activating") and up < GRACE_S.get(name, 600): grace = int(up)
        except Exception: pass
        if grace is not None: act.append("grace: %s up %ds < %ds, poll may be in progress - no restart" % (unit, grace, GRACE_S.get(name, 600)))
    if unit and r["status"] != "quarantined" and grace is None:
        typ = "start" if subprocess.run(["systemctl", "cat", unit + ".timer"], capture_output=True).returncode == 0 else "restart"
        # sgpfeeds_20261103: --no-block so a slow oneshot (e.g. tfo-thf-pit-fx, minutes) or a slow restart never wedges this loop
        # (15:20 WITA: 'systemctl start tfo-thf-pit-fx.service' timed out after 120 s twice and no feed_status was written)
        from veteran_desk import healing_guard as HG
        target = unit + ".service"
        snapshot = HG.probe_unit(target)
        def submit():
            return subprocess.run(["systemctl", "--no-block", typ, target], capture_output=True, text=True, timeout=60).returncode == 0
        decision = HG.guarded_attempt(target, typ, submit, owner="feedkeeper", observation=snapshot,
                                      state_path=os.path.join(L, "state", "thf", "recovery_guard.json"))
        act.append("%s %s: %s" % (typ, unit, decision["reason"]))
    if fbk:
        try: act.append("fallback %s ok n=%d" % (fbk, run_fallback(fbk))); r["using_fallback"] = FB + "/%s.json" % fbk
        except Exception as e: act.append("fallback %s fail %s" % (fbk, str(e)[:60]))
    st["fails"] += 1; st["next_try"] = time.time() + min(1800, 60 * 2 ** min(st["fails"], 5)); st["last_heal"] = utcnow().isoformat(timespec="seconds")
    r["heal"] = "; ".join(act) or "observe-only (no owned unit/fallback)"; heal_log({"feed": name, "status": r["status"], "age_s": r.get("age_s"), "action": r["heal"]})
def once(ks):
    prev = jload(STATUS, {}).get("feeds", {}); out = {}
    for n, cfg in FEEDS.items():
        r, snap = check(n, cfg, prev.get(n))
        if snap is not None: r["snap"] = snap
        st = ks.get(n, {})
        if r["status"] in ("stale", "invalid", "missing", "quarantined") and n != "sbx_feed_ok":
            try: heal(n, cfg, r, ks)
            except Exception as e: r["heal"] = "heal error: %s" % str(e)[:120]   # sgpfeeds_20261103: one bad heal must not kill the whole pass
        elif r["status"] in ("ok", "closed", "backup_idle"): ks.setdefault(n, {})["fails"] = 0; ks[n]["next_try"] = 0
        r["last_heal"] = ks.get(n, {}).get("last_heal"); out[n] = r
    for d in OANDA_DIRS:
        fs = [os.path.join(d, f) for f in os.listdir(d)] if os.path.isdir(d) else []
        age = int(time.time() - max(os.path.getmtime(f) for f in fs)) if fs else None
        nm = os.path.basename(d); paused = nm in OANDA_PAUSED and (age is None or age > 3600)   # fxfeed_20261103 helper 2: honest status
        out["oanda_practice:" + nm] = {"source": "OANDA practice (paper)", "path": d, "age_s": age, "status": "paused" if paused else ("ok" if age is not None and age < 3600 else ("idle" if fs else "empty")), "detail": OANDA_PAUSED.get(nm, "observe-only")}
    try:   # healthdesk_20261103: one row per paid data vendor (OANDA, Unusual Whales, EODHD REST quota, Massive) - status/last good/age/last error; never healed here
        import sys; sys.path.insert(0, L) if L not in sys.path else None
        from veteran_desk import vendor_health as VH
        out.update(VH.rows(ks))
    except Exception as e: print("vendor rows err", type(e).__name__, flush=True)
    units = subprocess.run("systemctl list-units --all 'tfo-*.service' --no-legend --plain", shell=True, capture_output=True, text=True).stdout.split("\n")
    U = {}
    for ln in units:
        p = ln.split()
        if len(p) >= 4: U[p[0]] = p[2] + "/" + p[3]
    now = utcnow()
    try:
        import sys
        if L not in sys.path: sys.path.insert(0, L)
        from veteran_desk.feed_status_facts import annotate, summarize
        for name, row in out.items():
            if isinstance(row, dict) and row.get("unit") and U.get(row["unit"] + ".service", "").startswith("active"):
                row["process_state"] = "running"
            elif isinstance(row, dict) and row.get("unit") and U.get(row["unit"] + ".service"):
                row["process_state"] = "inactive"
        annotate(out, now)
        facts_summary = summarize(out, now)
    except Exception as error:
        facts_summary = dict(ok_is_not_usable=True, error=type(error).__name__)
    doc = {"paper_only": True, "updated_utc": now.isoformat(timespec="seconds"), "updated_wita": (now + dt.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M WITA"),
           "summary": {s: sum(1 for v in out.values() if v["status"] == s) for s in set(v["status"] for v in out.values())},
           "facts_summary": facts_summary, "feeds": out, "units": U}
    jsave(STATUS, doc); jsave(KS, ks)
def main():
    os.makedirs(T, exist_ok=True); ks = jload(KS, {}); last_help = 0
    while True:
        t0 = time.time()
        try: once(ks)
        except Exception as e: print("loop err", e, flush=True)
        if time.time() - last_help > 3600:
            try: helpers_run()
            except Exception as e: print("helpers err", e, flush=True)
            last_help = time.time()
        time.sleep(max(5, 60 - (time.time() - t0)))
if __name__ == "__main__":
    import sys
    if "--once" in sys.argv: ks = jload(KS, {}); once(ks); helpers_run() if "--helpers" in sys.argv else None
    else: main()
