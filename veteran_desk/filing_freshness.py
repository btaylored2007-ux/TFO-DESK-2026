"""Point-in-time filing labels. Acceptance/publication timestamps never become ingestion timestamps.
Amendments and routine forms are retained as research, not fresh standalone event evidence.
"""
import datetime as dt,os,re,sqlite3,json,time,threading,fcntl
UTC=dt.timezone.utc
SEC_CLAIM=re.compile(r"\b(?:sec\s+(?:filing|form)|edgar|8[ -]?k(?:/a)?|10[ -]?[qk](?:/a)?)\b",re.I)
def parse(value):
    value=str(value or "").strip()
    if value.endswith(" UTC"):value=value[:-4]+"+00:00"
    if value.endswith(" WITA"):value=value[:-5]+"+08:00"
    try:
        t=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
    except (ValueError,TypeError):return None
def describe(row,asof=None,max_age_days=3):
    now=asof or dt.datetime.now(UTC)
    if isinstance(row,(tuple,list)):
        form,accepted,accession=(list(row)+[None]*3)[:3];event=None
    else:
        row=row or {};form=row.get("form");accepted=row.get("accepted_utc") or row.get("filed_utc") or row.get("published_utc") or row.get("source_utc");accession=row.get("accession");event=row.get("report_date") or row.get("event_date")
    form=str(form or "").strip().upper();t=parse(accepted);age=(now-t).total_seconds()/86400 if t else None
    amended=form.endswith("/A")
    from veteran_desk import sec_forms
    routine=sec_forms.is_routine_filing(form)
    event_t=parse(event)
    reference=t or parse((row or {}).get("filed") if isinstance(row,dict) else None)
    old_event=event_t is not None and reference is not None and (reference-event_t).total_seconds()>7*86400
    fresh=t is not None and 0<=age<=max_age_days and not amended and not routine and not old_event
    why="amendment; new event unverified" if amended else "routine filing" if routine else "event older than 7 days" if old_event else "accepted timestamp missing" if t is None else "accepted after asof" if age<0 else "historical filing" if age>max_age_days else "recent public filing; event date "+("supplied" if event_t else "unverified")
    return dict(form=form or "unknown",accepted_utc=t.isoformat() if t else None,accession=accession,age_days=round(age,2) if age is not None else None,amended=amended,stale_event=bool(old_event),event_date=event_t.date().isoformat() if event_t else None,fresh_filing=bool(fresh),freshness_reason=why)
def wire_fresh(row,asof=None,max_age_days=3):
    if not isinstance(row,dict):return False
    if row.get("catalyst") is False or row.get("routine"):return False
    t=parse(row.get("accepted_utc") or row.get("published_utc") or row.get("source_utc") or row.get("filed_utc"))
    if t is None:return False
    now=asof or dt.datetime.now(UTC);age=(now-t).total_seconds()/86400
    form=str(row.get("form") or "").upper()
    if form and SEC_CLAIM.search(form):return describe(dict(row,accepted_utc=t.isoformat()),now,max_age_days)["fresh_filing"]
    return 0<=age<=max_age_days and not form.endswith("/A")
BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META=os.path.join(BASE,"state","thf","sec_submissions")
def _sym(value):
    value=str(value or "").upper().strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,24}",value):raise ValueError("Invalid filing identity")
    return value
def remember_submissions(sym,data):
    """Called by the existing public SEC intake, not by a trader's gate."""
    sym=_sym(sym);recent=(data.get("filings") or {}).get("recent") or {};records=[]
    for i,form in enumerate(recent.get("form") or []):
        def value(key):
            values=recent.get(key) or []
            return values[i] if i<len(values) else None
        records.append(dict(form=form,accession=value("accessionNumber"),accepted_utc=value("acceptanceDateTime"),report_date=value("reportDate"),primary_document=value("primaryDocument"),filed=value("filingDate")))
    os.makedirs(META,exist_ok=True);path=os.path.join(META,sym+".json");temporary=path+".%d.%d.tmp"%(os.getpid(),time.time_ns())
    with open(temporary,"w") as stream:json.dump(dict(sym=sym,fetched_utc=dt.datetime.now(UTC).isoformat(),source="SEC submissions",records=records),stream)
    os.replace(temporary,path)
    return records
def metadata(sym):
    try:
        with open(os.path.join(META,_sym(sym)+".json")) as stream:return json.load(stream).get("records") or []
    except (OSError,ValueError):return []
def accession(value):
    match=re.search(r"(\d{10}-\d{2}-\d{6})|/(\d{18})/",str(value or ""))
    if not match:return None
    if match.group(1):return match.group(1)
    value=match.group(2);return value[:10]+"-"+value[10:12]+"-"+value[12:]
def enrich_reference(sym,row):
    if isinstance(row,(tuple,list)):
        form,accepted,acc=(list(row)+[None]*3)[:3];base=dict(form=form,accepted_utc=accepted,accession=acc)
    else:base=dict(row or {})
    acc=base.get("accession") or accession(base.get("url") or base.get("link"))
    if acc:base["accession"]=acc
    exact=next((item for item in metadata(sym) if item.get("accession")==acc),None)
    if exact:base.update({k:v for k,v in exact.items() if v not in (None,"")})
    return base
def annotate(sym,row,asof=None):
    base=enrich_reference(sym,row);observed=dict(base)
    if not observed.get("accepted_utc") and observed.get("filed"):
        observed["filed_utc"]=observed["filed"];observed["time_precision"]="date"
    result=describe(observed,asof)
    base.update(result)
    if observed.get("time_precision")=="date":
        base["accepted_utc"]=None
        base["public_date"]=str(observed.get("filed"))[:10]
        base["acceptance_precision"]="unknown; SEC index supplies a date only"
    return base
def sec_claim_fresh(proposal,asof=None,rows=None):
    p=proposal or {};primary=" ".join(str(p.get(k) or "") for k in ("catalyst","catalyst_ref"))
    text=primary if SEC_CLAIM.search(primary) else " ".join(str(p.get(k) or "") for k in ("news","news_hit")) if not p.get("catalyst") else primary
    if not SEC_CLAIM.search(text):return None
    sym=p.get("ticker") or p.get("sym")
    try:sym=_sym(sym)
    except ValueError:return None
    now=asof or dt.datetime.now(UTC);provided=rows is not None
    records=list(rows or [])
    if rows is None:
        records.extend(metadata(sym))
        from veteran_desk import pit
        if not (os.environ.get("PYTEST_CURRENT_TEST") and os.path.abspath(pit.DB)==os.path.join(pit.BASE,"data/pit/pit.sqlite")):
            try:
                c=sqlite3.connect("file:"+os.path.abspath(pit.DB)+"?mode=ro",uri=True,timeout=.2)
                try:records.extend(c.execute("SELECT form,filed_utc,accession FROM filings WHERE sym=? ORDER BY filed_utc DESC LIMIT 20",(sym,)).fetchall())
                finally:c.close()
            except (OSError,sqlite3.Error):pass
        try:
            data=json.load(open(os.path.join(pit.BASE,"state/veteran_desk/edgar_8k_cache.json")))
            if int(data.get("version") or 0)>=3:
                records.extend(row for row in data.get("rows") or [] if row.get("sym")==sym)
        except (OSError,ValueError):pass
    records=[enrich_reference(sym,row) for row in records]
    if re.search(r"\b(?:8[ -]?K|10[ -]?[QK])/A\b",text,re.I):return False
    requested=re.search(r"\b(8[ -]?K|10[ -]?[QK])\b",text,re.I)
    if requested:
        wanted=requested.group(1).upper().replace(" ","").replace("-","")
        records=[row for row in records if str(row.get("form") or "").upper().replace("-","").replace("/A","")==wanted]
    references=set(re.findall(r"\d{10}-\d{2}-\d{6}",text))
    path_ref=accession(text)
    if path_ref:references.add(path_ref)
    if references:records=[row for row in records if row.get("accession") in references]
    documents=[row for row in records if row.get("primary_document") and str(row["primary_document"]).lower() in text.lower()]
    if documents:records=documents
    described=[annotate(sym,row,now) for row in records]
    if any(row.get("fresh_filing") for row in described):return True
    # An incomplete or lagging filing table is not proof that no new disclosure exists.
    if described and (references or documents or provided):return False
    if any(row.get("amended") and parse(row.get("accepted_utc") or row.get("filed")) and 0<=(now-parse(row.get("accepted_utc") or row.get("filed"))).total_seconds()<=3*86400 for row in described):return False
    return None
