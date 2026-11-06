"""THF-owned, read-only peer evidence and a persistent cash-flow-adjusted paper competition."""
import datetime as dt,hashlib,json,math,os,pathlib,time,re
ROOT=pathlib.Path("/opt/tfo/live/thf");MIRROR=pathlib.Path("/opt/tfo/data")
DIR=ROOT/"state/thf/competition";LATEST=DIR/"latest.json";RACE=DIR/"race.json";LIVE_DIR=DIR
MAX_AGE=1800
NAMES={"thf":"Taylor Hedge Fund","veteran":"Veteran Desk","oanda_aggro":"OANDA Aggro","fundamental":"Fundamental"}
def read(path,default=None):
    try:
        with open(path) as stream:
            value=json.load(stream);return value if isinstance(value,dict) else ({} if default is None else default)
    except (OSError,ValueError):return {} if default is None else default
def lines(path):
    try:
        with open(path) as stream:return [json.loads(line) for line in stream if line.strip()]
    except (OSError,ValueError):return []
def number(value):
    try:
        value=float(value);return value if math.isfinite(value) else None
    except (TypeError,ValueError):return None
def epoch(value):
    try:return dt.datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()
    except (TypeError,ValueError):return None
def atomic(path,data):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+".%d.%d.tmp"%(os.getpid(),time.time_ns()))
    with temp.open("w") as stream:json.dump(data,stream,indent=1);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
def ident(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()[:24]
def flow_rows(events,ccy):
    out=[]
    ignore={"authorization","idle_cash_deploy_in_desk"}
    for event in events:
        if not isinstance(event,dict):continue
        kind=str(event.get("type") or event.get("kind") or "")
        if kind in ignore:continue
        amount=number(event.get("amount_aud") if ccy=="AUD" else event.get("amount_usd"))
        if amount is None:amount=number(event.get("usd") if ccy=="USD" else event.get("amount"))
        out.append(dict(id=ident(event),utc=event.get("utc"),epoch=epoch(event.get("utc")),amount=amount,kind=kind))
    return out
def position(row,key,desk,seat=None,closed=False):
    if not isinstance(row,dict):return None
    ins=str(row.get("ticker") or row.get("symbol") or row.get("sym") or row.get("ins") or row.get("instrument") or key)
    units=number(row.get("currentUnits",row.get("shares",row.get("units"))))
    side=str(row.get("side") or row.get("dir") or ("LONG" if units is not None and units>0 else "SHORT" if units else "")).upper()
    stop=row.get("stop")
    if isinstance(stop,dict):stop=stop.get("px_usd") if desk=="fundamental" else stop.get("price",stop.get("px"))
    if desk=="fundamental":
        entry=number(row.get("entry_px_usd"));mark=number(row.get("mark_usd"))
        stop=number(stop if stop is not None else row.get("stop_px_usd"));price_ccy="USD"
        exit_px=number(row.get("exit_px_usd"));net=number(row.get("realised_usd",row.get("unrealised_usd")));pnl_ccy="USD"
    else:
        entry=number(row.get("fill",row.get("entry",row.get("open_price") if closed else row.get("price"))))
        mark=number(row.get("mark",row.get("current_price")));stop=number(stop if stop is not None else (row.get("stopLossOrder") or {}).get("price"))
        exit_px=number(row.get("exit",row.get("exit_price",row.get("price") if closed else None)))
        net=number(row.get("net_aud",row.get("realized_pl_aud",row.get("upl_aud",row.get("unrealizedPL")))));pnl_ccy="AUD"
        venue=str(row.get("venue") or "")
        price_ccy=(ins.split("_")[-1] if desk=="oanda_aggro" and "_" in ins else row.get("ccy") or row.get("quote_ccy") or {"US":"USD","ASX":"AUD"}.get(venue) or "source native"+(" ("+venue+")" if venue else ""))
    raw_id=row.get("id") or row.get("trade_id")
    if closed and raw_id and row.get("transaction_id"):raw_id=str(raw_id)+":"+str(row["transaction_id"])
    if raw_id:pid=str(raw_id)
    elif closed:pid=str(key)+":"+ident({k:row.get(k) for k in ("symbol","sym","utc","closed_utc","entry_utc","units","exit_px_usd","realised_usd")})
    else:pid=str(key)
    why=row.get("thesis") or row.get("exit_reason") or row.get("reason") or row.get("source") or ""
    return dict(ref="peer:%s:%s"%(desk,pid),id=pid,desk=desk,seat=seat,symbol=ins[:45],side=side,
                status=row.get("status") or ("closed" if closed else "open"),grade=row.get("grade"),entry=entry,mark=mark,exit=exit_px,stop=stop,
                target=number(row.get("target")),price_currency=price_ccy,pnl_currency=pnl_ccy,units=units,net=net,
                sector=row.get("sector"),why=str(why)[:180],asof=row.get("closed_utc") or row.get("mark_utc") or row.get("utc") or row.get("entry_utc") or row.get("open_utc"))

def book_snapshot(desk,data,path,now):
    ccy="USD" if desk=="fundamental" else "AUD"
    equity=number(data.get("equity") if ccy=="USD" else data.get("equity_aud"))
    asof=data.get("updated") or data.get("asof_utc");stamp=epoch(asof)
    opened=[];closed=[]
    if desk=="fundamental":
        for key,p in (data.get("positions") or {}).items():
            row=position(p,key,desk)
            if row:opened.append(row)
        for key,p in (data.get("closed") or {}).items():
            row=position(p,key,desk,closed=True)
            if row:closed.append(row)
        for i,p in enumerate((data.get("recovery_closes") or [])+(data.get("thesis_exits") or [])):
            if not isinstance(p,dict):continue
            row=position(p,p.get("symbol") or str(i),desk,closed=True)
            if row:closed.append(row)
    else:
        for sid,sb in (data.get("seats") or {}).items():
            for key,p in (sb.get("positions") or {}).items():
                row=position(p,key,desk,sid)
                if row:opened.append(row)
            for i,p in enumerate(sb.get("closed") or []):
                row=position(p,p.get("id") or str(i),desk,sid,True)
                if row:closed.append(row)
    history_count=len(closed);missed=[];history_unknown=[]
    if desk=="thf":
        opened=[row for row in opened if row.get("status") in ("open","armed","queued")]
        missed=[row for row in closed if row.get("status") in ("cancelled","withdrawn","expired","rejected")]
        history_unknown=[row for row in closed if row.get("status") not in ("closed","cancelled","withdrawn","expired","rejected")]
        closed=[row for row in closed if row.get("status")=="closed"]
        missed.sort(key=lambda row:str(row.get("asof") or ""),reverse=True)
    opened_n=sum(row.get("status")=="open" for row in opened) if desk=="thf" else len(opened)
    armed_n=sum(row.get("status")=="armed" for row in opened) if desk=="thf" else 0
    queued_n=sum(row.get("status")=="queued" for row in opened) if desk=="thf" else 0
    closed.sort(key=lambda row:str(row.get("asof") or ""),reverse=True)
    warnings=[]
    if not data.get("paper_only"):warnings.append("source is not labelled paper")
    if number(data.get("n_unmarked")):warnings.append("source has unmarked positions")
    if desk=="fundamental" and abs(number(data.get("equity_identity_gap_usd")) or 0)>1:
        warnings.append("source reports an equity reconciliation gap; standings provisional")
    return dict(id=desk,name=NAMES[desk],currency=ccy,equity=equity,asof=asof,age_s=round(now-stamp) if stamp else None,
                fresh=bool(stamp and -120<=now-stamp<=MAX_AGE and equity is not None and equity>0),
                source=str(path),warnings=warnings,flows=flow_rows(data.get("capital_events") or [],ccy),
                open=opened,closed=closed,missed=missed,history_unknown=history_unknown,history_count=history_count,missed_count=len(missed),history_unknown_count=len(history_unknown),closed_count=len(closed),open_count=opened_n,armed_count=armed_n,queued_count=queued_n,active_count=len(opened),
                lifetime_net=number(data.get("pnl_since_inception_aud")),net_of_costs=True)
def oanda_snapshot(now):
    folder=MIRROR/"state/oanda_aggro";stats=read(folder/"stats.json");flows=lines(folder/"funder_topups.jsonl")
    broker_path=ROOT/"state/oanda/snapshot.json";broker=read(broker_path)
    stamp=epoch(broker.get("asof_utc"));valid=bool(broker.get("verified") and broker.get("currency")=="AUD" and broker.get("host")=="practice" and stamp and -120<=now-stamp<=MAX_AGE)
    credit=sum(number(f.get("amount_aud")) or 0 for f in flows)
    equity=(number(broker.get("nav"))+credit) if valid and number(broker.get("nav")) is not None else number(stats.get("nav"))
    asof=broker.get("asof_utc") if valid else stats.get("asof_utc");stamp=epoch(asof)
    declared=lines(folder/"trades.jsonl")
    ids={str(p.get("id")) for p in stats.get("open") or []}
    ids.update(str(p.get("trade_id") or p.get("id")) for p in declared if p.get("trade_id") or p.get("id"))
    current=broker.get("trades") or []
    if isinstance(current,dict):current=list(current.values())
    opened=[]
    for p in current:
        if str(p.get("id")) in ids or "aggro" in str(p.get("owner") or "").lower():
            row=position(p,str(p.get("id")),"oanda_aggro")
            if row:opened.append(row)
    closed=[]
    history=declared+[p for p in lines(ROOT/"state/oanda/trades.jsonl") if str(p.get("trade_id") or p.get("id")) in ids or "aggro" in str(p.get("owner") or p.get("sleeve") or "").lower()]
    for i,p in enumerate(history):
        if p.get("event") in ("closed","close","trade_closed","stop_filled","take_profit_filled","close_filled") or p.get("status")=="closed" or p.get("realized_pl_aud") is not None:
            row=position(p,str(p.get("id") or p.get("trade_id") or i),"oanda_aggro",closed=True)
            if row:closed.append(row)
    closed=list({ident(row):row for row in closed}.values());closed.sort(key=lambda row:str(row.get("asof") or ""),reverse=True)
    warnings=[]
    if not valid:warnings.append("Aggro report is stale; awaiting a verified practice-account snapshot")
    if stats.get("open") and not opened:warnings.append("current Aggro trade IDs could not be matched to the broker snapshot")
    return dict(id="oanda_aggro",name=NAMES["oanda_aggro"],currency="AUD",equity=equity,asof=asof,
                age_s=round(now-stamp) if stamp else None,fresh=bool(stamp and -120<=now-stamp<=MAX_AGE and equity and equity>0),
                source=str(broker_path if valid else folder/"stats.json"),warnings=warnings,
                scope_note="Existing Aggro model: whole practice-account NAV plus declared paper funding credit, not an isolated subaccount.",
                agg_report_asof=stats.get("asof_utc"),flows=flow_rows(flows,"AUD"),open=opened,closed=closed,closed_count=len(closed),
                open_count=len(opened),lifetime_net=None,net_of_costs=True)
def sources(now=None):
    now=time.time() if now is None else float(now)
    paths={"thf":ROOT/"state/thf/thf_book.json","veteran":MIRROR/"reports/veteran_desk_book.json","fundamental":MIRROR/"reports/fundamental_book.json"}
    result={desk:book_snapshot(desk,read(path),path,now) for desk,path in paths.items()}
    result["oanda_aggro"]=oanda_snapshot(now);return result
def score(base,current,start,now):
    seen=set(base.get("flow_ids") or []);new=[f for f in current.get("flows") or [] if f["id"] not in seen]
    reasons=[]
    if not current.get("fresh"):reasons.append("source snapshot is stale or unavailable")
    total=weighted=0.0;span=max(1,now-start)
    for flow in new:
        if flow.get("amount") is None or flow.get("epoch") is None:
            reasons.append("new funding event needs reconciliation");continue
        if flow["epoch"]<start-120 or flow["epoch"]>now+120:
            reasons.append("new funding event falls outside the competition clock");continue
        total+=flow["amount"];weighted+=flow["amount"]*max(0,min(1,(now-flow["epoch"])/span))
    initial=number(base.get("equity"));equity=number(current.get("equity"))
    denominator=(initial or 0)+weighted
    pnl=equity-initial-total if equity is not None and initial is not None else None
    ret=100*pnl/denominator if pnl is not None and denominator>0 and not reasons else None
    return dict(net_change=pnl,funding_change=total,return_pct=round(ret,5) if ret is not None else None,
                eligible=ret is not None,reasons=reasons,method="Modified Dietz, native reported currency, after recorded costs; funding excluded")
def tick(now=None,force=False):
    now=time.time() if now is None else float(now)
    if os.environ.get("PYTEST_CURRENT_TEST") and DIR==LIVE_DIR:return read(LATEST)
    previous=read(LATEST)
    if not force and now-float(previous.get("built_epoch") or 0)<30:return previous
    desks=sources(now);race=read(RACE)
    if not race and RACE.exists():raise RuntimeError("Competition baseline needs repair; it has not been reset")
    if not race:
        race=dict(id="THF-"+dt.datetime.fromtimestamp(now,dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),started_epoch=now,
                  started_utc=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
                  baselines={key:dict(equity=d["equity"],currency=d["currency"],flow_ids=[f["id"] for f in d["flows"]],fresh_at_start=d["fresh"]) for key,d in desks.items()},
                  peaks={key:1.0 for key in desks},goal="Improve THF after-cost performance with controlled drawdown; no forced trades or changed risk rails.")
    rows=[]
    for key,current in desks.items():
        base=race["baselines"].get(key) or {}
        perf=score(base,current,race["started_epoch"],now)
        if not base.get("fresh_at_start"):perf.update(eligible=False,return_pct=None,reasons=perf["reasons"]+["source was not fresh at the common start"])
        drawdown=None
        if perf["return_pct"] is not None:
            index=1+perf["return_pct"]/100;peak=max(float(race["peaks"].get(key) or 1),index);race["peaks"][key]=peak
            drawdown=round(100*(index/peak-1),4)
        rows.append(dict(**{k:v for k,v in current.items() if k not in ("open","closed","missed","history_unknown","flows")},**perf,
                         drawdown_pct=drawdown,provisional=bool(current["warnings"] or not current["fresh"])))
    rows.sort(key=lambda row:(row["return_pct"] is not None,row["return_pct"] or 0),reverse=True)
    last=None;rank=0
    for i,row in enumerate([x for x in rows if x["eligible"]],1):
        if last is None or abs(row["return_pct"]-last)>0.00001:rank=i
        row["rank"]=rank;last=row["return_pct"]
    result=dict(paper_only=True,read_only_peers=True,race_id=race["id"],started_utc=race["started_utc"],built_epoch=now,
                built_utc=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),rows=rows,
                trades={key:dict(open=d["open"],closed=d["closed"][:6],closed_count=d.get("closed_count",len(d["closed"])),missed=d.get("missed",[])[:6],missed_count=d.get("missed_count",0),history_unknown_count=d.get("history_unknown_count",0),asof=d["asof"],warnings=d["warnings"]) for key,d in desks.items()},
                goal=race["goal"],provisional=any(d["warnings"] or not d["fresh"] for d in desks.values()),
                rules="Same start. After recorded costs. Funding is not profit. Percent returns use each source's reported currency. Stale or unreconciled data is shown. No guaranteed winner.")
    atomic(RACE,race);atomic(LATEST,result);return result
def snapshot():return read(LATEST,dict(rows=[],trades={},paper_only=True,read_only_peers=True,status="initialising"))
def brief():
    data=snapshot();trades=data.get("trades") or {}
    view=dict(started_utc=data.get("started_utc"),asof=data.get("built_utc"),rules=data.get("rules"),
              standings=[{k:r.get(k) for k in ("id","return_pct","rank","age_s","provisional","reasons")} for r in data.get("rows") or []],
              peer_evidence={key:dict(asof=value.get("asof"),warnings=value.get("warnings"),open=value.get("open",[])[:30],closed=value.get("closed",[])[:6],closed_available=value.get("closed_count")) for key,value in trades.items() if key!="thf"})
    return "\nTHF COMPETITION / READ-ONLY PEER RESEARCH. Recheck ideas with THF paid quotes, own stops, sizing and liquidity. Do not copy orders or change rails to win. Optional peer-data misses never veto a trade.\n"+json.dumps(view,separators=(",",":"),default=str)+"\n"

def context(seat=None):
    parts=[]
    try:
        from veteran_desk import thf_paid_pack as PAID
        parts.append(PAID.brief(seat=seat) if seat else PAID.brief())
    except Exception:pass
    try:
        from veteran_desk import thf_exdiv as EXDIV
        parts.append(EXDIV.brief(seat))
    except Exception:pass
    try:parts.append(brief())
    except Exception:pass
    return "".join(parts)

def query(desk="all",status="all",search="",offset=0,limit=50):
    if desk not in set(NAMES)|{"all"}:raise ValueError("Unknown desk")
    if status not in ("all","open","closed"):raise ValueError("Choose all, open or closed records")
    try:offset=int(offset);limit=int(limit)
    except (ValueError,TypeError):raise ValueError("Invalid page")
    if offset<0 or not 1<=limit<=100:raise ValueError("Use pages of 1-100 records")
    search=str(search or "").strip().lower()[:120];data=sources();records=[]
    for key,snapshot in data.items():
        if desk!="all" and key!=desk:continue
        selected=(snapshot["open"] if status=="open" else snapshot["closed"] if status=="closed" else snapshot["open"]+snapshot["closed"])
        for record in selected:
            if not search or search in " ".join(str(record.get(k) or "") for k in ("symbol","why","id","seat","status")).lower():
                records.append(dict(record,source_asof=snapshot["asof"],source_fresh=snapshot["fresh"]))
    records.sort(key=lambda row:str(row.get("asof") or ""),reverse=True)
    return dict(read_only=True,desk=desk,status=status,total=len(records),offset=offset,limit=limit,
                search=search,next_offset=offset+limit if offset+limit<len(records) else None,records=records[offset:offset+limit],
                sources={key:{k:value.get(k) for k in ("asof","age_s","warnings","scope_note")} for key,value in data.items() if desk=="all" or key==desk})
def query_context(text):
    text=str(text or "")
    if not re.search(r"peer|competit|veteran|oanda|fundamental",text,re.I):return ""
    names=[key for key,pattern in (("veteran",r"veteran"),("fundamental",r"fundamental"),("oanda_aggro",r"oanda")) if re.search(pattern,text,re.I)]
    desk=names[0] if len(names)==1 else "all";status="closed" if re.search(r"closed|history|past trades|exits",text,re.I) else "open" if re.search(r"open trades|open positions",text,re.I) else "all"
    data=query(desk,status,limit=20)
    symbols={row["symbol"] for row in data["records"]}
    # Search by an explicit symbol when it is present anywhere in the available history.
    candidates=re.findall(r"\b[A-Z][A-Z0-9._=-]{1,18}\b",text)
    for candidate in candidates:
        if candidate in {"THF","OANDA","PAPER","USD","AUD","READ","ONLY","JSON"}:continue
        found=query(desk,status,search=candidate,limit=20)
        if any(candidate in row["symbol"].upper() for row in found["records"]):
            data=found;break
    page=re.search(r"\bpage\s+(\d+)",text,re.I)
    if page and int(page.group(1))>1:data=query(desk,status,search=data.get("search",""),offset=(int(page.group(1))-1)*20,limit=20)
    return "\nREQUESTED READ-ONLY PEER TRADE EVIDENCE (historical prices are not new entry quotes):\n"+json.dumps(data,separators=(",",":"),default=str)+"\n"
