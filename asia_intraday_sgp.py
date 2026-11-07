#!/opt/tfo/venv/bin/python
"""feed100_20261102 (PAPER, data only): Asia/ASX intraday 5m collector on the droplet -> data/intraday/bars.sqlite
(table bars(sym,tf,ts ms,o,h,l,c,v,src)). Yahoo chart v8 (free; ASX/HK/JP/KR/TW, delayed by source). Own store
state/thf/asia_intraday.sqlite is re-merged every pass so a Mac copy of bars.sqlite can never erase droplet bars.
Priority each pass: held/armed Asia names + top-ADV names; the rest rotate. Status -> state/thf/asia_intraday_status.json."""
import json, os, time, sqlite3, datetime as dt, concurrent.futures as cf
from zoneinfo import ZoneInfo
import requests
L = "/opt/tfo/live/thf"; BARS = L + "/data/intraday/bars.sqlite"; OWN = L + "/state/thf/asia_intraday.sqlite"
ST = L + "/state/thf/asia_intraday_status.json"; ROT = L + "/state/thf/asia_intraday_rot.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
# market -> (suffixes, tz, open, close)
MK = {"ASX": ((".AX",), "Australia/Sydney", "10:00", "16:12"), "JP": ((".T",), "Asia/Tokyo", "09:00", "15:32"),
      "KR": ((".KS", ".KQ"), "Asia/Seoul", "09:00", "15:32"), "HK": ((".HK",), "Asia/Hong_Kong", "09:30", "16:10"),
      "TW": ((".TW",), "Asia/Taipei", "09:00", "13:32"), "SG": ((".SI",), "Asia/Singapore", "09:00", "17:06")}
PER_PASS = int(os.environ.get("ASIA_INTRADAY_PER_PASS", "100")); GRACE = dt.timedelta(minutes=20)
def in_session(m, now):
    _, tz, o, c = MK[m]
    from veteran_desk.feed_health import asia_session_open
    return asia_session_open(m, now, (tz, o, c))
def mkt(sym):
    for m, (sx, *_r) in MK.items():
        if any(sym.endswith(s) for s in sx): return m
def held():
    out = set()
    for rel in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
        try: b = json.load(open(os.path.join(L, rel)))
        except Exception: continue
        for B in (b.get("seats") or {}).values() if isinstance(b.get("seats"), dict) else []:
            for p in (B.get("positions") or {}).values():
                if isinstance(p, dict) and p.get("ticker"): out.add(str(p["ticker"]).upper())
    return out
EOD_EX = {".AX": "AU", ".KS": "KO", ".KQ": "KQ", ".TW": "TW", ".SI": "SG"}   # EODHD intraday works; HK empty, TSE absent -> Yahoo
_K = None
def _key():
    global _K
    if _K is None:
        import sys; sys.path.insert(0, L)
        try:
            from veteran_desk.eodhd_eu_ws import _key as k; _K = k() or ""
        except Exception: _K = ""
    return _K
def fetch_eodhd(sym):
    suf = "." + sym.rsplit(".", 1)[1]; ex = EOD_EX.get(suf)
    if not ex or not _key(): return None
    try:
        r = requests.get("https://eodhd.com/api/intraday/%s.%s" % (sym.rsplit(".", 1)[0], ex), params={"api_token": _key(), "fmt": "json", "interval": "5m", "from": int(time.time()) - 86400}, timeout=20)
        if r.status_code != 200: return None
        out = [(sym, "5m", int(x["timestamp"]) * 1000, x.get("open"), x.get("high"), x.get("low"), x["close"], x.get("volume") or 0, "eodhd_asia_sgp") for x in r.json() if x.get("close") is not None]
        return out or None
    except Exception: return None
YB = {"until": 0}
def fetch_yahoo(sym):
    if time.time() < YB["until"]: return None
    for host in ("query1", "query2"):
        try:
            from veteran_desk.data_layer import yahoo_chart_response
            r = yahoo_chart_response(sym, {"range": "1d", "interval": "5m"}, host=host, headers=UA, timeout=15)
            if r.status_code == 429: continue
            res = r.json()["chart"]["result"][0]; q = res["indicators"]["quote"][0]
            return [(sym, "5m", int(t) * 1000, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0, "yahoo_asia_sgp")
                    for i, t in enumerate(res.get("timestamp") or []) if q["close"][i] is not None]
        except Exception: pass
    YB["until"] = time.time() + 60   # both hosts 429 -> skip Yahoo for the rest of this pass
    return None
def fetch(sym):
    return fetch_eodhd(sym) or fetch_yahoo(sym)
def main():
    now = dt.datetime.now(dt.timezone.utc); live = [m for m in MK if in_session(m, now)]
    if os.environ.get("ASIA_FORCE"): live = os.environ["ASIA_FORCE"].split(",")  # test hook
    st = {"paper_only": True, "tag": "feed100_20261102", "run_utc": now.isoformat(timespec="seconds"), "markets_in_session": live, "markets": {}}
    own = sqlite3.connect(OWN, timeout=60)
    own.execute("create table if not exists bars(sym TEXT, tf TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, src TEXT, PRIMARY KEY(sym, tf, ts))")
    if live:
        try: names = json.load(open(L + "/data/pit/scan_universe.json"))["names"]
        except Exception: names = []
        uni = sorted([n for n in names if mkt(n["sym"]) in live], key=lambda n: -(n.get("adv20_aud") or 0))
        hs = [s for s in held() if mkt(s) in live]
        try: rot = json.load(open(ROT)).get("i", 0)
        except Exception: rot = 0
        core = hs + [n["sym"] for n in uni[:60] if n["sym"] not in hs]
        rest = [n["sym"] for n in uni if n["sym"] not in core]
        k = max(PER_PASS - len(core), 0); pick = core + ([rest[(rot + j) % len(rest)] for j in range(min(k, len(rest)))] if rest else [])
        json.dump({"i": (rot + k) % max(len(rest), 1)}, open(ROT, "w"))
        ok = fail = 0; rows = []
        with cf.ThreadPoolExecutor(6) as ex:
            for r in ex.map(fetch, pick):
                if r is None: fail += 1
                else: ok += 1; rows += r
        own.executemany("insert or replace into bars values(?,?,?,?,?,?,?,?,?)", rows); own.commit()
        for m in live:
            mu = [n["sym"] for n in uni if mkt(n["sym"]) == m]
            st["markets"][m] = {"universe": len(mu), "fetched_this_pass": sum(1 for s in pick if mkt(s) == m)}
        st.update(fetched_ok=ok, fetched_fail=fail, rows=len(rows), held_asia=hs, rotation_full_cycle_passes=(len(rest) // max(k, 1) + 1) if rest else 1)
    # merge last 2 days of own store into bars.sqlite (idempotent; survives a Mac copy-over)
    cut = int((now - dt.timedelta(days=2)).timestamp() * 1000)
    rows = own.execute("select * from bars where ts>=?", (cut,)).fetchall()
    b = sqlite3.connect(BARS, timeout=60); b.execute("pragma busy_timeout=60000")
    cur = b.executemany("insert or ignore into bars(sym,tf,ts,o,h,l,c,v,src) values(?,?,?,?,?,?,?,?,?)", rows); b.commit()
    st["merged_new_rows"] = cur.rowcount
    cov = own.execute("select count(distinct sym), max(ts) from bars where ts>=?", (int((now - dt.timedelta(hours=8)).timestamp() * 1000),)).fetchone()
    st["syms_with_bars_8h"] = cov[0]; st["last_bar_utc"] = dt.datetime.fromtimestamp(cov[1] / 1000, dt.timezone.utc).isoformat() if cov[1] else None
    from veteran_desk.feed_health import asia_data_status
    st["data_status"] = asia_data_status(st, now)
    st["healthy"] = st["data_status"] == "fresh"
    st["freshness"] = "underlying bars, not this file's mtime"
    own.execute("delete from bars where ts<?", (int((now - dt.timedelta(days=10)).timestamp() * 1000),)); own.commit()
    t = ST + ".tmp"; json.dump(st, open(t, "w"), indent=1); os.replace(t, ST); print(json.dumps(st)[:600])
if __name__ == "__main__": main()
