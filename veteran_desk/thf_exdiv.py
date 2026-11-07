"""THF cash distributions: dated background cache; decision readers never do network I/O."""
import concurrent.futures as cf,datetime as dt,fcntl,json,os,sqlite3,threading,time,urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
UTC=dt.timezone.utc
BASE=Path(__file__).resolve().parents[1];STATE=BASE/"state/thf"
CACHE=STATE/"exdiv_calendar.json";ATTEMPT=STATE/"exdiv_attempt.json";LOG=STATE/"exdiv_refresh.jsonl"
LOCK=STATE/"exdiv_refresh.lock"
MAX_CALLS=256;MAX_SECONDS=600;_THREAD=None;_MEM=None;_MEM_MTIME=None;_MEM_LOCK=threading.Lock()
TZ={".AX":"Australia/Sydney",".L":"Europe/London",".AS":"Europe/Amsterdam",".PA":"Europe/Paris",".DE":"Europe/Berlin",".TO":"America/Toronto",".T":"Asia/Tokyo",".HK":"Asia/Hong_Kong",".SI":"Asia/Singapore",".KS":"Asia/Seoul",".NS":"Asia/Kolkata",".BO":"Asia/Kolkata",".NZ":"Pacific/Auckland"}
TZ.update({".SS":"Asia/Shanghai",".SZ":"Asia/Shanghai",".TW":"Asia/Taipei",".TWO":"Asia/Taipei",".ST":"Europe/Stockholm",".OL":"Europe/Oslo",".CO":"Europe/Copenhagen",".HE":"Europe/Helsinki",".MC":"Europe/Madrid",".MI":"Europe/Rome",".SW":"Europe/Zurich",".JO":"Africa/Johannesburg",".TA":"Asia/Jerusalem",".SA":"America/Sao_Paulo",".V":"America/Toronto",".BR":"Europe/Brussels",".IR":"Europe/Dublin",".AT":"Europe/Athens",".LS":"Europe/Lisbon",".WA":"Europe/Warsaw",".IS":"Europe/Istanbul"})

def now():return dt.datetime.now(UTC)
def _read(p,default=None):
    try:return json.loads(Path(p).read_text())
    except (OSError,ValueError):return default if default is not None else {}
def _write(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+".%d.%d.tmp"%(os.getpid(),time.time_ns()))
    tmp.write_text(json.dumps(value,separators=(",",":")));os.replace(tmp,p)
def _nth(y,m,w,n):
    d=dt.date(y,m,1);return d+dt.timedelta(days=(w-d.weekday())%7+7*(n-1))
def _observed(d,newyear=False):
    return d-dt.timedelta(days=1) if d.weekday()==5 and not newyear else d+dt.timedelta(days=1) if d.weekday()==6 else d
def _easter(y):
    a=y%19;b=y//100;c=y%100;d=b//4;e=b%4;f=(b+8)//25;g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30;i=c//4;k=c%4;l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451
    return dt.date(y,(h+l-7*m+114)//31,(h+l-7*m+114)%31+1)
def us_holidays(y):
    return {_observed(dt.date(y,1,1),True),_nth(y,1,0,3),_nth(y,2,0,3),_easter(y)-dt.timedelta(days=2),max(dt.date(y,5,d) for d in range(25,32) if dt.date(y,5,d).weekday()==0),_observed(dt.date(y,6,19)),_observed(dt.date(y,7,4)),_nth(y,9,0,1),_nth(y,11,3,4),_observed(dt.date(y,12,25))}
def is_us(sym):return "." not in sym or sym.rsplit(".",1)[-1] in ("A","B")
def session_day(sym,asof=None):
    suffix="."+sym.rsplit(".",1)[-1] if "." in sym else ""
    return (asof or now()).astimezone(ZoneInfo(TZ.get(suffix,"America/New_York"))).date()
def session(d,sym):return d.weekday()<5 and (not is_us(sym) or d not in us_holidays(d.year))
def shift(d,n,sym):
    step=1 if n>0 else -1
    for _ in range(abs(n)):
        d+=dt.timedelta(days=step)
        while not session(d,sym):d+=dt.timedelta(days=step)
    return d
def snapshot():
    global _MEM,_MEM_MTIME
    try:
        stat=CACHE.stat();mtime=(stat.st_dev,stat.st_ino,stat.st_mtime_ns,stat.st_size)
    except OSError:return {}
    with _MEM_LOCK:
        if _MEM is None or mtime!=_MEM_MTIME:_MEM=_read(CACHE);_MEM_MTIME=mtime
        result=_MEM
    return result
def stale(data,asof=None):
    try:return bool(data.get("stale")) or ((asof or now())-dt.datetime.fromisoformat(data["fetched_utc"])).total_seconds()>36*3600
    except (KeyError,ValueError,TypeError):return True
def universe():
    from veteran_desk import vd_engine as V
    U,_=V.build_universe()
    return {s:u for s,u in U.items() if u.get("cls")=="EQ" and not s.startswith(("^","KRAKEN:")) and not s.endswith(("=X","=F"))}
def focus_symbols(U=None):
    priority=[]
    book=_read(STATE/"thf_book.json")
    for seat in (book.get("seats") or {}).values():
        for p in (seat.get("positions") or {}).values():
            if str(p.get("status") or "").startswith(("open","armed")):priority.append(p.get("ticker"))
    for p in (BASE/"state/vd_engine/top10").glob("*.json"):
        priority.extend(r.get("sym") for r in _read(p).get("top10") or [] if isinstance(r,dict))
    return list(dict.fromkeys(s for s in priority if s and (U is None or s in U)))
def massive_page(query):
    import paid_feeds
    return paid_feeds.massive("/stocks/v1/dividends",**query)
def yahoo_events(sym,start,end):
    from veteran_desk.data_layer import yahoo_chart_response
    params=dict(period1=int(dt.datetime.combine(start,dt.time(),UTC).timestamp()),period2=int(dt.datetime.combine(end+dt.timedelta(days=1),dt.time(),UTC).timestamp()),interval="1d",events="div")
    response=yahoo_chart_response(sym,params=params,timeout=8)
    response.raise_for_status();data=response.json()
    result=(data.get("chart") or {}).get("result") or []
    if not result:raise RuntimeError("Yahoo chart unavailable")
    r=result[0];zone=ZoneInfo((r.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    out=[]
    for x in ((r.get("events") or {}).get("dividends") or {}).values():
        out.append(dict(date=dt.datetime.fromtimestamp(x["date"],zone).date().isoformat(),amount=float(x["amount"]),source="Yahoo chart cash event",fetched_utc=now().isoformat(),currency=(r.get("meta") or {}).get("currency"),basis="chart quote currency",declaration_date=None))
    return out
def _massive_event(row):
    amount=row.get("split_adjusted_cash_amount")
    if amount is None:amount=row.get("cash_amount")
    if not row.get("ex_dividend_date") or amount is None:return None
    return dict(date=row["ex_dividend_date"],amount=float(amount),cash_amount=row.get("cash_amount"),currency=row.get("currency"),source="Massive dividends",fetched_utc=now().isoformat(),declaration_date=row.get("declaration_date"),type=row.get("dividend_type"),basis="split-adjusted cash" if row.get("split_adjusted_cash_amount") is not None else "cash")
def refresh(force=False,max_calls=MAX_CALLS,max_seconds=MAX_SECONDS):
    """CLI/one background collector only. Keeps last good events and explicit unknown coverage on error."""
    STATE.mkdir(parents=True,exist_ok=True);started=time.monotonic();day=now().date().isoformat()
    with LOCK.open("a+") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return dict(status="already_refreshing")
        previous=snapshot();attempt=_read(ATTEMPT)
        if not force and attempt.get("day")==day:return dict(status="already_attempted_today")
        _write(ATTEMPT,dict(day=day,started_utc=now().isoformat(),force=bool(force)))
        U=universe();calls=0;errors={};checked={};events=dict(previous.get("events") or {})
        start=now().date()-dt.timedelta(days=45);end=now().date()+dt.timedelta(days=25)
        aliases={s.replace("-","."):s for s in U if is_us(s)}
        collected={};complete=False;pages=0;query={"ex_dividend_date.gte":start.isoformat(),"ex_dividend_date.lte":end.isoformat(),"limit":5000,"sort":"ex_dividend_date.desc"}
        while calls<min(32,max_calls) and time.monotonic()-started<max_seconds:
            try:
                calls+=1;pages+=1;data=massive_page(query)
                if str(data.get("status") or "").upper() in ("ERROR","NOT_AUTHORIZED") or not isinstance(data.get("results"),list):raise RuntimeError("Dividend response unavailable")
                for row in data.get("results") or []:
                    sym=aliases.get(str(row.get("ticker") or ""));event=_massive_event(row)
                    if sym and event:collected.setdefault(sym,[]).append(event)
                nxt=data.get("next_url")
                if not nxt:complete=True;break
                url=urllib.parse.urlsplit(nxt)
                if url.hostname not in ("api.massive.com","api.polygon.io") or url.path!="/stocks/v1/dividends":raise RuntimeError("Unsupported dividend pagination")
                query={k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items() if k.lower()!="apikey"}
            except Exception as error:errors["massive"]=type(error).__name__;break
        stamp=now().isoformat()
        for sym in aliases.values():
            if complete:
                events[sym]=collected.get(sym,[])
                checked[sym]=dict(source="Massive dividends",fetched_utc=stamp,status="checked",window=[start.isoformat(),end.isoformat()])
            elif sym in collected:
                # Partial pull is additional evidence, never proof of an empty calendar.
                known={(e["date"],e.get("amount")):e for e in events.get(sym,[])}
                known.update({(e["date"],e.get("amount")):e for e in collected[sym]})
                events[sym]=list(known.values());checked[sym]=dict(source="Massive dividends",fetched_utc=stamp,status="partial")
        oldcoverage=previous.get("coverage") or {}
        focus=focus_symbols(U)
        fallback=[s for s in focus if not is_us(s) or not events.get(s)]
        fallback+=sorted((s for s in U if not is_us(s) and s not in fallback),key=lambda s:(oldcoverage.get(s) or {}).get("fetched_utc") or "")
        fallback=list(dict.fromkeys(fallback))[:max(0,max_calls-calls)]
        # Bounded workers only; network is entirely outside scans, seat packs and execution.
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            pending={};iterator=iter(fallback)
            def fill():
                nonlocal calls
                while len(pending)<4 and calls<max_calls and time.monotonic()-started<max_seconds:
                    sym=next(iterator,None)
                    if sym is None:return
                    calls+=1;pending[pool.submit(yahoo_events,sym,start,end)]=sym
            fill()
            while pending:
                done,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
                for task in done:
                    sym=pending.pop(task)
                    try:
                        extra=task.result()
                        # Yahoo supplies past cash events, not a complete forward declaration calendar.
                        merged={(e["date"],e.get("amount")):e for e in events.get(sym,[]) if str(e.get("date") or "")>now().date().isoformat()}
                        merged.update({(e["date"],e.get("amount")):e for e in extra})
                        if extra or not is_us(sym):events[sym]=list(merged.values())
                        if not (complete and is_us(sym)):checked[sym]=dict(source="Yahoo chart cash events",fetched_utc=now().isoformat(),status="historical_only",forward="unavailable",window=[start.isoformat(),end.isoformat()])
                    except Exception as error:
                        errors[sym]=type(error).__name__
                        if not (complete and is_us(sym)):checked[sym]=dict(oldcoverage.get(sym) or {},status="stale" if sym in events else "unavailable",error=type(error).__name__)
                fill()
        coverage={}
        for sym in U:coverage[sym]=checked.get(sym) or dict(oldcoverage.get(sym) or {},status="stale" if sym in events else "not_yet_checked")
        events={s:sorted([e for e in value if str(e.get("date") or "")>=start.isoformat()],key=lambda e:e["date"]) for s,value in events.items() if s in U}
        result=dict(version=1,fetched_utc=now().isoformat(),window=[start.isoformat(),end.isoformat()],events=events,coverage=coverage,universe=len(U),massive_complete=complete,massive_pages=pages,calls=calls,call_budget=max_calls,elapsed_s=round(time.monotonic()-started,2),errors=errors,calendar_note="US regular exchange holidays; other listing windows use local weekdays and are approximate. Missing forward events are unknown.",adjustment_note="Known recent cash distributions only; not a complete total-return history.")
        if not checked:result=dict(previous,last_attempt_utc=now().isoformat(),last_attempt_errors=errors,stale=True)
        _write(CACHE,result)
        record={k:result.get(k) for k in ("fetched_utc","universe","massive_complete","massive_pages","calls","call_budget","elapsed_s","errors")}
        with LOG.open("a") as stream:stream.write(json.dumps(record)+"\n")
        return record
def context(symbols,asof=None,data=None,c=None):
    data=snapshot() if data is None else data;asof=asof or now();out={};lines=[];missing=[]
    for sym in dict.fromkeys(symbols):
        if sym.startswith(("KRAKEN:","^")) or sym.endswith(("=X","=F")):continue
        day=session_day(sym,asof);lo=shift(day,-5,sym);hi=shift(day,10,sym);recent=shift(day,-2,sym)
        coverage=(data.get("coverage") or {}).get(sym) or {};status=coverage.get("status")
        if stale(data,asof) or status not in ("checked","historical_only"):missing.append(sym)
        rows=[e for e in (data.get("events") or {}).get(sym,[]) if lo.isoformat()<=str(e.get("date") or "")<=hi.isoformat()]
        last=[e for e in rows if e["date"]<=day.isoformat()];future=[e for e in rows if e["date"]>day.isoformat()]
        record={}
        if last:
            event=dict(last[-1]);event["amount"]=sum(e["amount"] for e in last if e["date"]==event["date"]);pct=None
            if c is not None:
                try:
                    prior=c.execute("SELECT c FROM bars WHERE sym=? AND date<? AND knowable_utc<=? ORDER BY date DESC LIMIT 1",(sym,event["date"],asof.isoformat())).fetchone()
                    if prior and prior[0]:pct=100*event["amount"]/prior[0]
                except sqlite3.Error:pass
            event["pct_of_prior_close"]=round(pct,4) if pct is not None else None
            record["exdiv_last"]=event
            if event["date"]>=recent.isoformat():lines.append("EX-DIV: %s ex %s %s%s"%(sym,event["date"],event["amount"]," (%.3f%%)"%pct if pct is not None else " (prior-close percentage unavailable)"))
        if future:record["exdiv_next"]=future[0]
        if record:out[sym]=record
    return dict(events=out,lines=lines,status="ex-div data not available for %d of %d symbols"%(len(missing),len(set(symbols))) if missing else "dated cash-distribution cache",missing_symbols=missing[:20],missing_count=len(missing),fetched_utc=data.get("fetched_utc"),calendar_note=data.get("calendar_note"),forward_note="No listed future event means unannounced or unavailable, not a guaranteed zero distribution.")
def adjust_rows(c,sym,rows,asof=None,data=None):
    """Apply known cash gaps once to raw OHLC. Leave newest execution close and volumes unchanged."""
    if not rows:return rows,dict(status="no bars")
    data=snapshot() if data is None else data;events=(data.get("events") or {}).get(sym) or [];asof=asof or now()
    dates=[str(r[0]) for r in rows];selected=[e for e in events if dates[0]<e.get("date","")<=dates[-1]]
    if not selected:return rows,dict(status="no known cash event in bars",cache_stale=stale(data,asof))
    # Query only when an actual event needs a basis check.
    try:sources={r[0] for r in c.execute("SELECT DISTINCT src FROM bars WHERE sym=? AND date>=? AND date<=?",(sym,dates[0],dates[-1]))}
    except (sqlite3.Error,AttributeError):sources=set()
    raw={"yahoo","yahoo_chart_v8","yahoo_chart_v8(split-adj,not div-adj)","yahoo_batch","eodhd_bulk_sgp","massive_raw"}
    if not sources or any(source not in raw for source in sources):return rows,dict(status="adjustment skipped; raw cash basis unverified",sources=sorted(str(x) for x in sources))
    out=[list(r) for r in rows];applied=[];groups={}
    for e in selected:
        # Historical replay cannot consume a not-yet-public declaration.
        public=str(e.get("declaration_date") or str(e.get("fetched_utc") or "")[:10])
        if public and public>asof.astimezone(UTC).date().isoformat():continue
        groups[e["date"]]=groups.get(e["date"],0)+float(e.get("amount") or 0)
    for exdate,amount in sorted(groups.items()):
        if exdate not in dates or amount<=0:continue
        i=dates.index(exdate)
        if i<1:continue
        prior=float(out[i-1][4] or 0)
        if prior<=amount:continue
        factor=(prior-amount)/prior
        for j in range(i):
            for k in (1,2,3,4):
                if out[j][k] is not None:out[j][k]*=factor
        applied.append(dict(date=exdate,amount=amount,prior_close=prior,adjusted_prior_close=prior-amount,factor=factor))
    return [tuple(r) for r in out],dict(status="known recent cash events adjusted" if applied else "no applicable public event",applied=applied,cache_stale=stale(data,asof),basis="recent cash-distribution adjustment; latest raw execution close unchanged")
def brief(seat=None):
    symbols=focus_symbols() if not seat else []
    if seat:
        from veteran_desk import desk
        symbols=desk._pins(seat,{}).get("pinned") or []
    info=context(symbols)
    lines=info["lines"][:10]
    return "\nTHF CASH DISTRIBUTIONS: "+json.dumps(dict(status=info["status"],fetched_utc=info["fetched_utc"],lines=lines,forward_note=info["forward_note"]))+"\n"
def start_refresh_loop():
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():return
    def worker():
        while True:
            try:refresh()
            except Exception:pass
            time.sleep(300)
    _THREAD=threading.Thread(target=worker,name="thf-exdiv-cache",daemon=True);_THREAD.start()
if __name__=="__main__":
    import sys
    if len(sys.argv)>1 and sys.argv[1]=="refresh":print(json.dumps(refresh(force="--force" in sys.argv)))
    else:print(json.dumps(context(focus_symbols(universe()))))
