"""tfo_paidpush_20261030 (Brett 30 Oct 2026 21:17 WITA): paid research feeds for seat DATA packs. PAPER research only.
Keys are loaded ONLY from ~/.tfo_secrets/<name>.env (or the env var); never printed, logged or returned. Each item is source-tagged.
Authentication failures use a 30-minute breaker; rate limits respect Retry-After. Shared refreshes never queue desks. Disabled under pytest."""
import fcntl, json, math, os, threading, time, urllib.error, urllib.parse, urllib.request
from email.utils import parsedate_to_datetime
_BRK = {}; BREAKER_MIN = 30
def _test_mode(): return bool(os.environ.get("PYTEST_CURRENT_TEST"))
def _key(fname, var):
    k = os.environ.get(var)
    if k: return k.strip()
    try:
        for ln in open(os.path.expanduser("~/.tfo_secrets/%s.env" % fname)):
            if ln.strip().startswith(var + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None
def _retry_after(headers, default=60):
    value = (headers or {}).get("Retry-After")
    try: delay = float(value)
    except (ValueError, TypeError):
        try: delay = parsedate_to_datetime(str(value)).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError): delay = default
    return max(1.0, min(86400.0, delay)) if math.isfinite(delay) else float(default)
def _get(name, url, headers=None, t=10):
    if _test_mode() or os.environ.get("PAID_FEEDS_DISABLE") == "1": return None
    bucket = "coinalyze" if urllib.parse.urlparse(url).hostname == "api.coinalyze.net" else name
    if _BRK.get(bucket, 0) > time.time(): return None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk", "Accept": "application/json"}, **(headers or {})))
            with urllib.request.urlopen(request, timeout=t) as response:
                result = json.loads(response.read())
            _BRK.pop(bucket, None)
            return result
        except urllib.error.HTTPError as error:
            if error.code in (401, 403): _BRK[bucket] = time.time() + BREAKER_MIN * 60
            elif error.code == 429:
                delay = _retry_after(error.headers)
                if attempt == 0 and delay <= 3:
                    time.sleep(delay + .05)
                    continue
                _BRK[bucket] = time.time() + delay
            return None
        except Exception: return None
    return None
def _rows(js):
    if isinstance(js, list): return js
    if isinstance(js, dict):
        for k in ("data", "results", "response"):
            v = js.get(k)
            if isinstance(v, list): return v
            if isinstance(v, dict) and isinstance(v.get("data"), list): return v["data"]
    return []
def _slim(r, keep):
    return {k: r.get(k) for k in keep if isinstance(r, dict) and r.get(k) is not None}
def uw(path, keep, n=15):
    k = _key("unusual_whales", "UNUSUAL_WHALES_API_KEY")
    if not k: return None
    js = _get("uw" + path, "https://api.unusualwhales.com" + path, {"Authorization": "Bearer " + k})
    rows = [_slim(r, keep) for r in _rows(js)[:n]]
    return dict(src="unusual_whales" + path, rows=rows) if rows else None
def uw_flow(): return uw("/api/option-trades/flow-alerts?limit=30", ("ticker", "type", "total_premium", "strike", "expiry", "alert_rule", "underlying_price", "created_at"))
def uw_darkpool(): return uw("/api/darkpool/recent?limit=30", ("ticker", "size", "price", "premium", "executed_at"))
def uw_congress(): return uw("/api/congress/recent-trades?limit=20", ("ticker", "txn_type", "amounts", "name", "transaction_date", "filed_at_date"))
# 2026-10-30 22:10 WITA (Brett via TFO): CoinGlass retired -> Coinalyze is the crypto derivatives source (funding, OI, liquidations)
# for Kai and the THF crypto seat. Key: ~/.tfo_secrets/coinalyze.env COINALYZE_API_KEY (never printed). No key -> listed as inactive, not a failure.
CZ = "https://api.coinalyze.net/v1"
CZ_SYMS = {"BTC": "BTCUSDT_PERP.A", "ETH": "ETHUSDT_PERP.A", "SOL": "SOLUSDT_PERP.A"}   # Binance USDT perps
def coinalyze_key(): return _key("coinalyze", "COINALYZE_API_KEY")
def coinalyze_derivs():
    k = coinalyze_key()
    if not k: return None
    h = {"api_key": k}; syms = ",".join(CZ_SYMS.values()); now = int(time.time())
    oi = _get("cz_oi", CZ + "/open-interest?convert_to_usd=true&symbols=" + syms, h)
    fr = _get("cz_fr", CZ + "/funding-rate?symbols=" + syms, h)
    lq = _get("cz_liq", CZ + "/liquidation-history?interval=1hour&convert_to_usd=true&from=%d&to=%d&symbols=%s" % (now - 86400, now, syms), h)
    ls = _get("cz_ls", CZ + "/long-short-ratio-history?interval=1hour&from=%d&to=%d&symbols=%s" % (now - 7200, now, syms), h)
    out = {}
    for c, sym in CZ_SYMS.items():
        r = {}
        for x in (oi or []):
            if x.get("symbol") == sym: r["oi_usd"] = x.get("value")
        for x in (fr or []):
            if x.get("symbol") == sym: r["funding_rate"] = x.get("value")
        for x in (lq or []):
            if x.get("symbol") == sym:
                hist = x.get("history") or []
                r["liq_24h_long_usd"] = round(sum(float(z.get("l") or 0) for z in hist)); r["liq_24h_short_usd"] = round(sum(float(z.get("s") or 0) for z in hist))
        for x in (ls or []):
            if x.get("symbol") == sym and x.get("history"):
                z = x["history"][-1]; r["long_short_ratio"] = z.get("r"); r["long_pct"] = z.get("l"); r["short_pct"] = z.get("s")
        if r: out[c] = r
    if not out: return None
    required=("oi_usd","funding_rate","liq_24h_long_usd","liq_24h_short_usd","long_short_ratio","long_pct","short_pct")
    missing={coin:[name for name in required if (out.get(coin) or {}).get(name) is None] for coin in CZ_SYMS}
    missing={coin:names for coin,names in missing.items() if names}
    res = dict(src="coinalyze_v1_oi_funding_liq_ls",rows=out,partial=bool(missing),missing_measures=missing)
    try:   # snapshot for the dashboards (VD dash Crypto derivatives card); atomic write, no key inside
        import datetime as _dt
        if _test_mode() or res.get("partial"): return res
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "data_hub"); os.makedirs(d, exist_ok=True)
        t = os.path.join(d, "coinalyze_latest.json.tmp"); json.dump(dict(res, utc=_dt.datetime.now(_dt.timezone.utc).isoformat(), feed_source="coinalyze"), open(t, "w")); os.replace(t, os.path.join(d, "coinalyze_latest.json"))
    except Exception: pass
    return res
def eia_spot():
    k = _key("eia", "EIA_API_KEY")
    if not k: return None
    q = urllib.parse.urlencode([("api_key", k), ("frequency", "daily"), ("data[0]", "value"), ("sort[0][column]", "period"), ("sort[0][direction]", "desc"), ("length", "12")])
    r = _rows(_get("eia", "https://api.eia.gov/v2/petroleum/pri/spt/data/?" + q))
    rows = [_slim(x, ("period", "series-description", "value", "units")) for x in r]
    return dict(src="eia_v2_petroleum_spot", rows=rows) if rows else None
def fmp_movers():
    k = _key("fmp", "FMP_API_KEY")
    if not k: return None
    out = {}
    for p in ("biggest-gainers", "biggest-losers", "most-actives"):
        r = _rows(_get("fmp_" + p, "https://financialmodelingprep.com/stable/%s?apikey=%s" % (p, urllib.parse.quote(k))))
        if r: out[p] = [_slim(x, ("symbol", "price", "changesPercentage", "exchange")) for x in r[:10]]
    return dict(src="fmp_stable_movers", rows=out) if out else None
def benzinga():
    from veteran_desk import massive as M
    r = M.news(limit=20)
    return dict(src=(r[0]["source"] if r else "massive"), rows=r) if r else None
def eodhd_earnings():
    import datetime as dt
    from veteran_desk import data_layer as DL
    d = dt.date.today(); r = DL.earnings(str(d), str(d + dt.timedelta(days=3)))
    r = _rows(r) if not isinstance(r, list) else r
    return dict(src="eodhd_earnings_calendar", rows=r[:40]) if r else None
PACK = {"benzinga_news": benzinga, "eodhd_earnings": eodhd_earnings, "fmp_movers": fmp_movers, "uw_flow": uw_flow, "uw_darkpool": uw_darkpool,
        "uw_congress": uw_congress, "coinalyze_derivs": coinalyze_derivs, "eia_spot": eia_spot}
SNAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "data_hub", "paid_pack_latest.json")
def snapshot():
    try: return json.load(open(SNAP))
    except Exception: return {}

BRIEF_BUDGET=3800
def symbols_for(seat):
    """Read-only relevance hints. Never use a user-supplied seat as an arbitrary path."""
    import re
    if isinstance(seat,dict):seat=seat.get("seat")
    if not re.fullmatch(r"[a-z_]{1,40}",str(seat or "")):return []
    root=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));syms=[]
    try:
        book=json.load(open(os.path.join(root,"state/thf/thf_book.json")))
        syms.extend(p["ticker"] for p in ((book.get("seats",{}).get(seat) or {}).get("positions") or {}).values() if p.get("ticker") and p.get("status") in ("open","armed"))
    except (OSError,ValueError,TypeError):pass
    try:
        top=json.load(open(os.path.join(root,"state/vd_engine/top10",seat+".json")))
        syms.extend(r["sym"] for r in top.get("top10",[]) if r.get("sym"))
    except (OSError,ValueError,TypeError):pass
    return list(dict.fromkeys(syms))
_FIELDS={
 "symbol":("symbol","ticker","code"),
 "when":("published_utc","created_at","executed_at","transaction_date","report_date","date","period"),
 "headline":("title","series-description"),
 "type":("type","txn_type","alert_rule","before_after_market"),
 "price":("price","underlying_price"),
 "value":("value",),"premium":("total_premium","premium"),"size":("size","amounts"),
 "strike":("strike",),"expiry":("expiry",),"change_pct":("changesPercentage",),"who":("name",),"units":("units",),
 "actual":("actual",),"estimate":("estimate",)}
def _compact_paid(row,text_limit=64,lean=False):
    if not isinstance(row,dict):return row
    out={}
    for label,keys in _FIELDS.items():
        if lean and label in ("strike","expiry","actual","estimate","who","change_pct"):continue
        for key in keys:
            value=row.get(key)
            if value is None or value=="":continue
            if isinstance(value,str) and len(value)>text_limit:value=value[:max(1,text_limit-1)]+"…"
            out[label]=value;break
    return out or {k:v for k,v in list(row.items())[:4] if isinstance(v,(str,int,float,bool)) or v is None}
_FEED_FIELDS={
 "benzinga_news":("symbol","when","headline"),
 "eodhd_earnings":("symbol","when","type","actual","estimate"),
 "uw_flow":("symbol","when","type","price","premium","strike","expiry"),
 "uw_darkpool":("symbol","when","price","size","premium"),
 "uw_congress":("symbol","when","type","size","who"),
 "eia_spot":("when","headline","value","units"),
 "fmp_movers":("symbol","price","change_pct")}
def _feed_row(name,row,limit,lean):
    compact=_compact_paid(row,limit,lean);fields=_FEED_FIELDS.get(name)
    return {k:v for k,v in compact.items() if k in fields} if fields and isinstance(compact,dict) else compact

def _relevant(row,symbols):
    if not isinstance(row,dict) or not symbols:return False
    raw=" ".join(str(row.get(key) or "") for key in ("symbol","ticker","code")).upper()
    return any(sym and (sym.upper() in raw or sym.upper().replace("KRAKEN:","").replace("=X","") in raw) for sym in symbols)
def brief(seat=None):
    """Whole paid evidence for every trader/admin; bounded context, three wire records, all derivative groups."""
    data=snapshot();available=data.get("feeds") or {};symbols=symbols_for(seat)
    try:age_min=max(0,(time.time()-float(data.get("built_epoch") or 0))/60) if data.get("built_epoch") else None
    except (TypeError,ValueError):age_min=None
    prefix="THF PAID FEEDS (source-tagged research; optional misses never veto a trade): "
    def build(limit,lean):
        feeds={}
        for name in PACK:
            item=available.get(name) or {};rows=item.get("rows") or []
            entry=dict(available=bool(item),source=item.get("src"),rows=len(rows),fresh=bool(item) and age_min is not None and age_min<=10 and name not in (data.get("unavailable") or []) and not item.get("stale"))
            if item.get("stale"):
                try:
                    import datetime
                    stamp=datetime.datetime.fromisoformat(str(item.get("last_success_utc")).replace("Z","+00:00"))
                    entry["last_success_utc"]=stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if stamp.tzinfo else "unknown"
                except (TypeError,ValueError):entry["last_success_utc"]="unknown"
            if isinstance(rows,list):
                order=sorted(enumerate(rows),key=lambda ir:(not _relevant(ir[1],symbols),ir[0]))[:3]
                entry["samples"]=[_feed_row(name,row,limit,lean) for _,row in order]
                entry["row_indices"]=[i for i,_ in order]
                entry["relevant"]=sum(_relevant(row,symbols) for _,row in order)
            elif isinstance(rows,dict):
                sample={}
                for group,value in rows.items():
                    if isinstance(value,list):
                        selected=sorted(enumerate(value),key=lambda ir:(not _relevant(ir[1],symbols),ir[0]))[:1]
                        sample[group]=[_feed_row(name,row,limit,lean) for _,row in selected]
                    elif isinstance(value,dict):
                        # BTC, ETH and SOL derivatives retain every named measure, never a clipped JSON string.
                        sample[group]=dict(value)
                    else:sample[group]=value
                entry["samples"]=sample
            feeds[name]=entry
        return dict(built_utc=data.get("built_utc"),age_min=round(age_min,1) if age_min is not None else None,feeds=feeds,unavailable=data.get("unavailable") or [],inactive=data.get("inactive") or [],
                    note="Up to three complete records per wire; own/engine symbols first, remaining samples general. References use source feed and original row indices.")
    result=None
    for limit,lean in ((64,False),(40,True),(24,True)):
        result=build(limit,lean)
        rendered=prefix+json.dumps(result,separators=(",",":"),default=str,ensure_ascii=False)+"\n"
        if len(rendered)<=BRIEF_BUDGET:return rendered
    # Fixed metadata and optional details yield before complete evidence records. No mid-record slicing.
    for entry in result["feeds"].values():
        entry.pop("row_indices",None);entry.pop("relevant",None)
        sample=entry.get("samples")
        if isinstance(sample,list):
            entry["samples"]=[{k:v for k,v in row.items() if k in ("symbol","when","headline","price","value","premium","type","size","units")} if isinstance(row,dict) else row for row in sample]
    result["note"]="Complete compact source records; general samples may be unrelated to this seat."
    rendered=prefix+json.dumps(result,separators=(",",":"),default=str,ensure_ascii=False)+"\n"
    if len(rendered)>BRIEF_BUDGET:
        # Feed keys already identify their vendor/class. Detailed source labels yield before evidence rows.
        for entry in result["feeds"].values():entry.pop("source",None)
        result["note"]="Sources are the feed keys. Complete compact records; dates retained."
        rendered=prefix+json.dumps(result,separators=(",",":"),default=str,ensure_ascii=False)+"\n"
    if len(rendered)>BRIEF_BUDGET:
        # Repeated field names become a declared schema; each row remains one complete structured record.
        for entry in result["feeds"].values():
            sample=entry.get("samples")
            if isinstance(sample,list) and all(isinstance(row,dict) for row in sample):
                columns=list(dict.fromkeys(k for row in sample for k in row if k!="symbol"))
                entry["columns"]=columns
                entry["samples"]=[dict(**({"symbol":row["symbol"]} if "symbol" in row else {}),values=[row.get(k) for k in columns]) for row in sample]
        result["note"]="Sources are feed keys; columns describe each complete compact record."
        rendered=prefix+json.dumps(result,separators=(",",":"),default=str,ensure_ascii=False)+"\n"
    return rendered

def _build_pack():
    """All paid feeds that answered, source-tagged; share one fresh snapshot across THF processes."""
    cached=snapshot() if not _test_mode() else {}
    if not _test_mode():
        if 0 <= time.time() - float(cached.get("built_epoch") or 0) < 60 and isinstance(cached.get("feeds"), dict): return cached
    out, miss, inactive = {}, [], []
    for n, f in PACK.items():
        if n == "coinalyze_derivs" and not coinalyze_key(): inactive.append("coinalyze_derivs: key missing (awaiting new key from Brett)"); continue
        try: v = f()
        except Exception: v = None
        if v and not v.get("partial"):
            item=dict(v)
            if not _test_mode():
                import datetime
                item.update(last_success_epoch=time.time(),last_success_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),stale=False)
            out[n]=item
        else:
            miss.append(n)
            previous=(cached.get("feeds") or {}).get(n)
            if v and not isinstance(previous,dict):out[n]=dict(v,stale=True,stale_reason="Provider returned incomplete measures; no complete cache exists")
            if isinstance(previous,dict):
                out[n]=dict(previous,stale=True,last_success_epoch=previous.get("last_success_epoch") or cached.get("built_epoch"),
                            last_success_utc=previous.get("last_success_utc") or cached.get("built_utc"),
                            stale_reason="Latest provider refresh failed; dated research cache only")
    result = dict(feeds=out, unavailable=miss, inactive=inactive, note="paid feeds first; Yahoo/free feeds are fallback only")
    if not _test_mode():
        import datetime
        result.update(built_epoch=time.time(), built_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        try:
            os.makedirs(os.path.dirname(SNAP), exist_ok=True); temp = SNAP + ".%d.%d.tmp" % (os.getpid(), time.time_ns())
            with open(temp, "w") as stream: json.dump(result, stream)
            os.replace(temp, SNAP)
        except OSError: pass
    return result
def _refreshing_snapshot():
    cached = snapshot()
    if isinstance(cached.get("feeds"), dict): return dict(cached, refreshing=True)
    return dict(feeds={}, unavailable=list(PACK), inactive=[], refreshing=True,
                note="Paid research refresh in progress; optional research never vetoes a trade")
def pack():
    """One process refreshes; other desks immediately use the dated shared snapshot."""
    if _test_mode(): return _build_pack()
    cached = snapshot()
    if 0 <= time.time() - float(cached.get("built_epoch") or 0) < 60 and isinstance(cached.get("feeds"), dict): return cached
    try:
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        lock = open(SNAP + ".lock", "a")
    except OSError: return _refreshing_snapshot()
    try:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return _refreshing_snapshot()
        return _build_pack()   # Recheck freshness after acquiring the interprocess lock.
    finally:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally: lock.close()
_REFRESH_THREAD=None
_REFRESH_THREAD_LOCK=threading.Lock()
def _refresh_loop(every_s):
    while True:
        try:pack()
        except Exception:pass   # Research refresh failures never stop the chat or paper execution.
        time.sleep(every_s)
def start_refresh_loop(every_s=300):
    """One supervised research thread in the long-lived THF chat service, independent of AI capacity."""
    if _test_mode() or os.environ.get("PAID_FEEDS_DISABLE")=="1":return None
    global _REFRESH_THREAD
    with _REFRESH_THREAD_LOCK:
        if _REFRESH_THREAD is None or not _REFRESH_THREAD.is_alive():
            _REFRESH_THREAD=threading.Thread(target=_refresh_loop,args=(max(60,int(every_s)),),daemon=True,name="thf-paid-research")
            _REFRESH_THREAD.start()
    return _REFRESH_THREAD

if __name__ == "__main__":
    p = pack(); print(json.dumps({k: (v["src"], len(v["rows"])) for k, v in p["feeds"].items()}), "unavailable:", p["unavailable"])
