#!/usr/bin/env python3
"""TFO 48-72h PAPER hunt scanner. PAPER ONLY. Usage: python3 run_scan.py [--out DIR] [--sleeve 1000000] [--risk 5000]"""
import argparse,io,json,os,time,datetime as dt,math,warnings
import numpy as np,pandas as pd,requests,yfinance as yf
warnings.filterwarnings("ignore")
try:  # 2026-10-30 WITA feedfix2: ISP DNS hijack of api.kraken.com/futures.kraken.com -> same guarded process-local DoH hook (sweep had 0 Kraken rows)
    import sys as _kd_sys
    _kd_sys.path.append(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..","..")))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e: print("kraken_dns unavailable: %r" % _kd_e)
# 2026-10-28 Brett 20:31: resolve from desk base (DESK_BASE or this file's location), never $HOME (orch overrides HOME to yf_home)
DESK_BASE=os.environ.get("DESK_BASE") or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..",".."))
H={"User-Agent": json.load(open(os.path.join(DESK_BASE,"research_controls","sec_contact.json")))["user_agent"]}
FEEDS={}; KSKIP={}; TSKIP={}
def feed(n,ok,note=""): FEEDS[n]={"ok":bool(ok),"note":str(note)[:160]}
def empty_scan_guard(R):
    """tforul 2026-10-30: an empty scan frame (no equity/crypto rows, e.g. a data outage) must not raise AttributeError on R.venue.
    Logs it and exits rc=2 before any output file is written: the caller (hunt_orchestrator.us_scan) keeps the prior ranked.csv; no arms."""
    if R is None or len(R) == 0 or "venue" not in getattr(R, "columns", []):
        import sys
        print("run_scan: EMPTY scan frame (0 rows) - no ranking written, prior ranked.csv kept, no arms", file=sys.stderr, flush=True)
        feed("scan.rows", False, "0 rows: empty scan frame")
        raise SystemExit(2)
    return R
EXCL_SUFFIX=(".JK",); EXCL_WORDS=("INDONESIA","JAKARTA","IDR")
# ---------- weights (transparent) ----------
CFG=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"config.json")))
W=CFG["weights"]

def universe_us():
    rows=[]
    for u,sc,ex in [("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt","Symbol","Q"),("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt","ACT Symbol",None)]:
        try:
            for k_ in range(4):
                df=pd.read_csv(io.StringIO(requests.get(u,headers=H,timeout=30).text),sep="|")[:-1]
                if "Test Issue" in df: break
                time.sleep(5)
            df=df[df["Test Issue"]=="N"]
            if "Financial Status" in df: df=df[df["Financial Status"].isin(["N",np.nan])|df["Financial Status"].isna()]
            for _,r in df.iterrows():
                s=str(r[sc]); nm=str(r["Security Name"]).upper()
                if any(c in s for c in "$.^") or any(w in nm for w in EXCL_WORDS): continue
                if any(w in nm for w in ("2X","3X","-1X","-2X","-3X","ULTRA","BEAR ","BULL ","INVERSE","LEVERAGED","DAILY TARGET")): continue
                if any(w in nm for w in ("WARRANT","RIGHT","UNIT","PREFERRED","DEPOSITARY SH","NOTES DUE")): continue
                rows.append((s.replace(".","-"),nm,r.get("ETF","N")=="Y"))
            feed("nasdaqtrader:"+u.split("/")[-1],True,f"{len(df)} rows")
        except Exception as e: feed("nasdaqtrader:"+u.split("/")[-1],False,e)
    return pd.DataFrame(rows,columns=["sym","name","etf"]).drop_duplicates("sym")

def dl(tickers,period="1y",chunk=400):
    out=[]
    for i in range(0,len(tickers),chunk):
        try:
            d=yf.download(tickers[i:i+chunk],period=period,progress=False,auto_adjust=True,threads=True,group_by="column")
            if len(d): out.append(d)
            time.sleep(1)
        except Exception as e: print("dl err",e)
    if not out: return None
    d=pd.concat(out,axis=1); return d.loc[:,~d.columns.duplicated()]

def ind(o,h,l,c,v):
    """all indicators on one series; returns dict"""
    n=len(c); p=c.iloc[-1]
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean()
    m20,m50=c.rolling(20).mean(),c.rolling(50).mean(); m200=c.rolling(200).mean() if n>=200 else m50*np.nan
    up=h.diff();dn=-l.diff();pdm=np.where((up>dn)&(up>0),up,0.);ndm=np.where((dn>up)&(dn>0),dn,0.)
    pdi=100*pd.Series(pdm,c.index).rolling(14).mean()/atr; ndi=100*pd.Series(ndm,c.index).rolling(14).mean()/atr
    adx=(100*(pdi-ndi).abs()/(pdi+ndi)).rolling(14).mean()
    e12,e26=c.ewm(span=12).mean(),c.ewm(span=26).mean(); macd=e12-e26; sig=macd.ewm(span=9).mean()
    d=c.diff(); rsi=100-100/(1+d.clip(lower=0).rolling(14).mean()/(-d.clip(upper=0)).rolling(14).mean())
    sd=c.rolling(20).std(); bbw=4*sd/m20; kel_up=c.ewm(span=20).mean()+1.5*atr; kel_dn=c.ewm(span=20).mean()-1.5*atr
    squeeze=(m20+2*sd<kel_up)&(m20-2*sd>kel_dn)
    rng=h-l; nr7=rng.iloc[-1]<=rng.iloc[-7:].min()
    rv=v.iloc[-1]/v.iloc[-21:-1].mean() if v.iloc[-21:-1].mean()>0 else np.nan
    obv=(np.sign(d).fillna(0)*v).cumsum(); obv_tr=(obv.iloc[-1]-obv.iloc[-11])/(v.iloc[-20:].mean()*10+1e-9)
    mfm=((c-l)-(h-c))/(h-l).replace(0,np.nan); ad=(mfm.fillna(0)*v).cumsum(); ad_tr=(ad.iloc[-1]-ad.iloc[-11])/(v.iloc[-20:].mean()*10+1e-9)
    vwap5=(c*v).iloc[-5:].sum()/max(v.iloc[-5:].sum(),1); vwap_rec=(c.iloc[-2]<vwap5)&(p>vwap5)
    hi20=h.iloc[-21:-1].max(); lo10=l.iloc[-10:].min(); pdh=h.iloc[-1]; pdl=l.iloc[-1]
    roc=lambda k:(p/c.iloc[-k-1]-1)*100 if n>k else np.nan
    stack=int(p>m20.iloc[-1])+int(m20.iloc[-1]>m50.iloc[-1])+int(m50.iloc[-1]>m200.iloc[-1] if not math.isnan(m200.iloc[-1]) else 0)
    pull=(stack>=2)&(abs(p/m20.iloc[-1]-1)<0.02)&(roc(5)<0)&(rsi.iloc[-1]>40)
    return dict(px=p,atr=atr.iloc[-1],atr_pct=100*atr.iloc[-1]/p,m20=m20.iloc[-1],m50=m50.iloc[-1],m200=m200.iloc[-1],stack=stack,
        adx=adx.iloc[-1],macd_up=bool(macd.iloc[-1]>sig.iloc[-1]),rsi=rsi.iloc[-1],roc5=roc(5),roc20=roc(20),roc60=roc(60),
        bbw=bbw.iloc[-1],squeeze=bool(squeeze.iloc[-1]),squeeze_fire=bool(squeeze.iloc[-2] and not squeeze.iloc[-1]),nr7=bool(nr7),
        relvol=rv,obv_tr=obv_tr,ad_tr=ad_tr,vwap_reclaim=bool(vwap_rec),hi20=hi20,lo10=lo10,pdh=pdh,pdl=pdl,breakout=bool(p>=hi20),near_bo=bool(p>=0.98*hi20),pullback=bool(pull),
        adv_usd=(c*v).iloc[-20:].mean())

def score(r,cost_pct):
    s={}
    s["trend"]=r.stack/3; s["adx"]=min(max((r.adx-15)/20,0),1) if r.adx==r.adx else 0; s["macd"]=float(r.macd_up)
    s["rsi"]=1-abs(r.rsi-62)/30 if r.rsi==r.rsi else 0
    s["roc"]=np.tanh((np.nan_to_num(r.roc20)/10+np.nan_to_num(r.roc60)/20)/2); s["rs"]=np.tanh(np.nan_to_num(r.rs20)/8)
    s["squeeze"]=float(bool(r["squeeze"]) or bool(r["squeeze_fire"])); s["nr7"]=float(r.nr7)
    s["relvol"]=min(np.nan_to_num(r.relvol),4)/4; s["obv"]=np.tanh(r.obv_tr); s["ad"]=np.tanh(r.ad_tr); s["vwap"]=float(r.vwap_reclaim)
    s["breakout"]=1.0 if r.breakout else (0.5 if r.near_bo else 0); s["pullback"]=float(r.pullback)
    s["catalyst"]=r.get("cat_score",0); s["cost"]=cost_pct/0.5  # cost as fraction of half the +2% target... per 0.5%
    s["liq"]=1.0 if r.adv_usd<2e7 else 0; s["earn"]=1.0 if (r.get("earn_days",99)<=3 and not r.get("event_play",False)) else 0
    # extension penalty: >3 ATR above 20dMA = chase
    ext=(r.px-r.m20)/r.atr if r.atr>0 else 0
    tot=sum(W[k]*s[k] for k in W)-max(ext-3,0)*0.75
    return tot,s

def catalysts_us(sym):
    c={"cat_notes":[],"cat_score":0.0}
    t=yf.Ticker(sym)
    try:
        info=t.info; c.update(short_pct_float=info.get("shortPercentOfFloat"),days_to_cover=info.get("shortRatio"),float_sh=info.get("floatShares"),
            rec=info.get("recommendationKey"),tgt=info.get("targetMeanPrice"),sector=info.get("sector"),country=info.get("country"))
        if (info.get("country") or "").lower()=="indonesia": c["excluded"]=True
        if (c["short_pct_float"] or 0)>0.15 and (c["days_to_cover"] or 0)>3: c["cat_notes"].append(f"squeeze SI {c['short_pct_float']:.0%} DTC {c['days_to_cover']}"); c["cat_score"]+=0.3
        FEEDS.setdefault("yf.info",{"ok":True,"note":""})
    except Exception as e:
        if "Too Many" in str(e) or "Rate" in str(e): c["_rl"]=True
        else: feed("yf.info",False,e)
    try:
        ed=t.get_earnings_dates(limit=6)
        if ed is not None and len(ed):
            now=pd.Timestamp.now(tz=ed.index.tz)
            fut=ed[ed.index>now]; past=ed[ed.index<=now].dropna(subset=["Reported EPS"]) if "Reported EPS" in ed else ed.iloc[0:0]
            if len(fut): c["next_earn"]=str(fut.index.min().date()); c["earn_days"]=(fut.index.min()-now).days
            if len(past):
                lp=past.iloc[0]; sp=lp.get("Surprise(%)"); c["last_surprise_pct"]=sp; c["last_earn"]=str(past.index[0].date())
                if sp==sp and sp is not None and (now-past.index[0]).days<=10:
                    c["cat_notes"].append(f"recent earnings surprise {sp:+.1f}%"); c["cat_score"]+=0.5*np.sign(sp)
        FEEDS.setdefault("yf.earnings_dates",{"ok":True,"note":""})
    except Exception as e:
        if "Too Many" in str(e) or "Rate" in str(e): c["_rl"]=True
        else: feed("yf.earnings_dates",False,e)
    try:
        ud=t.upgrades_downgrades
        if ud is not None and len(ud):
            ud=ud[ud.index>=pd.Timestamp.now()-pd.Timedelta(days=7)]
            ups=(ud["Action"].isin(["up"])).sum(); dns=(ud["Action"].isin(["down"])).sum()
            pt=0
            if "priceTargetAction" in ud: pt=(ud["priceTargetAction"]=="Raises").sum()-(ud["priceTargetAction"]=="Lowers").sum()
            if ups or dns or pt: c["cat_notes"].append(f"7d analyst up{ups}/down{dns}, PT net {pt:+d}"); c["cat_score"]+=0.3*np.sign(ups-dns+0.5*pt)
        FEEDS.setdefault("yf.upgrades_downgrades",{"ok":True,"note":""})
    except Exception as e:
        if "Too Many" in str(e) or "Rate" in str(e): c["_rl"]=True
        else: feed("yf.upgrades_downgrades",False,e)
    try:
        nw=t.news or []; titles=[(n.get("content",{}) or {}).get("title") or n.get("title","") for n in nw[:8]]
        c["headlines"]=[x for x in titles if x][:4]
        kw=" ".join(c["headlines"]).lower()
        for k,w,lab in [("acqui",0.4,"M&A"),("merger",0.4,"M&A"),("buyout",0.4,"M&A"),("fda",0.3,"FDA"),("approval",0.3,"FDA/approval"),("raises guidance",0.4,"guide up"),("upgrade",0.2,"upgrade"),("downgrade",-0.3,"downgrade"),("cuts guidance",-0.5,"guide cut")]:
            if k in kw: c["cat_notes"].append("news:"+lab); c["cat_score"]+=w
        FEEDS.setdefault("yf.news",{"ok":True,"note":""})
    except Exception as e:
        if "Too Many" in str(e) or "Rate" in str(e): c["_rl"]=True
        else: feed("yf.news",False,e)
    return c

# Brett 2026-10-27 21:18 WITA: each US row sets its OWN direction from its own trend, relative strength and catalyst.
# The SPY-vs-50dMA regime is a RANK input only (counter-regime rows lose CFG["direction"]["counter_regime_rank_penalty"]),
# never a block. Kraken rows keep the BTC-vs-50dMA venue regime (unchanged).
def _f(x):
    try: x=float(x); return x if x==x else None
    except Exception: return None
def us_direction(r,cat_score=None):
    """(dir, basis). Votes: trend +1 if up-stack>=trend_min_stack and px>50dMA, -1 if down-stack (mirror) >=trend_min_stack and
    px<50dMA; RS +1/-1 if rs20 beyond +/-rs_min_pct; catalyst +1/-1 if cat_score beyond +/-cat_min (researched names only).
    Sum>0 long, <0 short; tie -> the side whose own score is higher (long score vs mirrored short score)."""
    D=CFG["direction"]; px,m50,rs=_f(r["px"]),_f(r["m50"]),_f(r["rs20"]); st=_f(r["stack"]) or 0
    mi=r["_mirror"] if isinstance(r["_mirror"],dict) else {}; mst=_f(mi.get("stack")) or 0
    tv=1 if (st>=D["trend_min_stack"] and px is not None and m50 is not None and px>m50) else (-1 if (mst>=D["trend_min_stack"] and px is not None and m50 is not None and px<m50) else 0)
    rv=0 if rs is None or abs(rs)<D["rs_min_pct"] else (1 if rs>0 else -1)
    cs=_f(cat_score); cv=0 if cs is None or abs(cs)<D["cat_min"] else (1 if cs>0 else -1)
    v=tv+rv+cv
    if v==0:
        ls,ss=_f(r.get("score")),_f(r.get("short_score")); d="long" if (ls if ls is not None else -1e9)>=(ss if ss is not None else -1e9) else "short"
    else: d="long" if v>0 else "short"
    return d,f"trend{tv:+d} rs{rv:+d} cat{cv:+d}"+(" tie->score" if v==0 else "")
def set_directions(R,REG):
    """dir per row (US: own signals; Kraken: venue regime), regime_dir, counter_regime, dir_basis, rank_score (penalised if counter-regime)."""
    pen=float(CFG["direction"]["counter_regime_rank_penalty"]); dirs,basis=[],[]
    for _,r in R.iterrows():
        if r["venue"]=="ibkr": d,b=us_direction(r)
        else: d,b=REG.get(r["venue"],"short"),"venue regime"
        dirs.append(d); basis.append(b)
    R["dir"]=dirs; R["dir_basis"]=basis; R["regime_dir"]=R.venue.map(REG)
    R["counter_regime"]=(R.venue=="ibkr")&(R.dir!=R.regime_dir)
    R["rank_score"]=np.where(R.dir=="long",R.score,R.short_score)-np.where(R.counter_regime,pen,0.0)
    return R

_CIK=None
def insider_buys(sym):
    global _CIK
    try:
        if _CIK is None:
            j=requests.get("https://www.sec.gov/files/company_tickers.json",headers=H,timeout=20).json(); _CIK={v["ticker"]:v["cik_str"] for v in j.values()}
        cik=_CIK.get(sym.replace("-",".")) or _CIK.get(sym)
        if not cik: return None
        j=requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",headers=H,timeout=20).json()["filings"]["recent"]
        cut=(dt.date.today()-dt.timedelta(days=14)).isoformat()
        f4=[(d,a,p) for f,d,a,p in zip(j["form"],j["filingDate"],j["accessionNumber"],j["primaryDocument"]) if f=="4" and d>=cut]
        buys=0
        for d,a,p in f4[:6]:
            x=requests.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{a.replace('-','')}/{p.split('/')[-1]}",headers=H,timeout=20).text
            if "<transactionCode>P</transactionCode>" in x: buys+=1
            time.sleep(0.12)
        FEEDS.setdefault("sec.edgar.form4",{"ok":True,"note":"open-market P codes, 14d"})
        return dict(form4_14d=len(f4),insider_open_mkt_buys=buys)
    except Exception as e: feed("sec.edgar.form4",False,e); return None

def kraken():
    try:
        ap=requests.get("https://api.kraken.com/0/public/AssetPairs",timeout=20).json()["result"]
        tk=requests.get("https://api.kraken.com/0/public/Ticker",timeout=20).json()["result"]
        rows=[]
        for k,v in ap.items():
            q=v.get("quote"); ws=v.get("wsname","")
            if q not in ("ZUSD","USD","USDT") or ".d" in k or k not in tk: continue
            last=float(tk[k]["c"][0]); vol=float(tk[k]["v"][1])*float(tk[k]["p"][1])
            base=ws.split("/")[0]
            if base in ("EUR","GBP","AUD","CAD","CHF","JPY","USDT","USDC","DAI","PYUSD","USDG","RLUSD","EURT","TUSD","USDS","FDUSD","UST") : continue
            if vol>=1e5: rows.append((k,ws,last,vol))
        feed("kraken.public",True,f"{len(rows)} pairs >$1m 24h")
        return pd.DataFrame(rows,columns=["pair","ws","px","vol24_usd"]).sort_values("vol24_usd",ascending=False).drop_duplicates("ws")
    except Exception as e: feed("kraken.public",False,e); return pd.DataFrame()

def kraken_ohlc(pair):
    for k_ in range(6):
        jj=requests.get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440",timeout=20).json()
        if jj.get("error") and any("Too many" in e or "Throttled" in e or "EAPI:Rate" in e for e in jj["error"]): time.sleep(3*(k_+1)); continue
        break
    if jj.get("error"): raise RuntimeError(jj["error"])
    j=jj["result"]
    k=[x for x in j if x!="last"][0]; d=pd.DataFrame(j[k],columns="t o h l c vw v n".split()).astype(float)
    d.index=pd.to_datetime(d.t,unit="s"); return d

def crypto_catalysts():
    out={}
    try:
        tr=requests.get("https://api.coingecko.com/api/v3/search/trending",timeout=20).json()
        out["cg_trending"]=[c["item"]["symbol"].upper() for c in tr.get("coins",[])]; feed("coingecko.trending",True)
    except Exception as e: feed("coingecko.trending",False,e)
    try:
        fr=requests.get("https://futures.kraken.com/derivatives/api/v3/tickers",timeout=20).json()["tickers"]
        # Kraken docs: fundingRate = ABSOLUTE quote-ccy payout per contract-unit per hour (linear PF_: = relativeFundingRate*index px);
        # relativeFundingRate = FRACTION PER HOUR (UI shows it x100 as %/1h). Funding settles hourly. 8h equiv = rel*8 (constant-rate).
        def _fr(t):
            rel=t.get("relativeFundingRate"); px=t.get("indexPrice")
            if rel is None and t.get("fundingRate") is not None and px: rel=t["fundingRate"]/px   # linear fallback (PF_ only)
            return dict(funding=rel, funding_rel_1h=rel, funding_pct_1h=(rel*100 if rel is not None else None), funding_pct_8h=(rel*800 if rel is not None else None),
                        funding_abs_quote_1h=t.get("fundingRate"), funding_pred_rel_1h=t.get("relativeFundingRatePrediction"), funding_kind="current-hour predicted (ticker), not settled", oi=t.get("openInterest"))
        out["funding"]={t["symbol"]:_fr(t) for t in fr if t["symbol"].startswith("PF_")}
        feed("kraken.futures.funding_oi",True,f"{len(out['funding'])} perps")
    except Exception as e: feed("kraken.futures.funding_oi",False,e)
    UA={"User-Agent":"Mozilla/5.0 (Macintosh) Chrome/128 Safari/537.36"}
    try:  # token unlocks: DefiLlama public datasets (free) - next 7d unlock as % of circ proxy (unlocked total)
        lst=requests.get("https://defillama-datasets.llama.fi/emissionsProtocolsList",timeout=20).json(); out["unlocks"]={}
        now=time.time(); n=0
        for pid in lst:
            try:
                j=requests.get(f"https://defillama-datasets.llama.fi/emissions/{pid}",timeout=15).json(); n+=1
                sym=((j.get("metadata") or {}).get("token") or "").split(":")[-1]; tk=(j.get("name") or pid)
                sy=(j.get("documentedData") or j).get("tokenSymbol") or j.get("tokenSymbol") or ""
                fut=past=0.0
                for cat in (j.get("documentedData") or {}).get("data",[]):
                    d=cat.get("data",[]); 
                    a=[x for x in d if x["timestamp"]<=now]; b=[x for x in d if x["timestamp"]<=now+7*86400]
                    if a and b: fut+=b[-1]["unlocked"]-a[-1]["unlocked"]; past+=a[-1]["unlocked"]
                if past>0 and fut/past>0.005: out["unlocks"][(sy or pid).upper()]=round(100*fut/past,2)
            except Exception: pass
        feed("token_unlocks",True,f"defillama datasets: {n} protocols, {len(out['unlocks'])} with >0.5% unlock next 7d")
    except Exception as e: feed("token_unlocks",False,e)
    try:  # crypto ETF flows: farside.co.uk HTML (browser UA works)
        fl={}
        for a_,u in (("BTC","https://farside.co.uk/btc/"),("ETH","https://farside.co.uk/eth/")):
            t=pd.read_html(io.StringIO(requests.get(u,headers=UA,timeout=20).text)); x=max(t,key=len); x.columns=range(x.shape[1])
            x=x[x[0].astype(str).str.match(r"\d{1,2} \w{3} \d{4}")]
            v=pd.to_numeric(x[x.columns[-1]].astype(str).str.replace(",","").str.replace("(","-").str.replace(")",""),errors="coerce").dropna()
            fl[a_]=dict(last=float(v.iloc[-1]),last_date=str(x[0].iloc[-1]),sum5d=round(float(v.iloc[-5:].sum()),1))
        out["etf_flows"]=fl; feed("crypto_etf_flows",True,f"farside US$m: "+json.dumps(fl))
    except Exception as e: feed("crypto_etf_flows",False,e)
    return out

def fda_calendar():
    import re,html as _h
    try:
        s=requests.get("https://www.rttnews.com/corpinfo/fdacalendar.aspx",headers={"User-Agent":"Mozilla/5.0 Chrome/128"},timeout=20).text
        t=re.sub(r"<script.*?</script>|<style.*?</style>","",s,flags=re.S); t=_h.unescape(re.sub(r"<[^>]+>","|",t)); t=re.sub(r"\s*\|[\s|]*","|",t)
        ev={}
        for m in re.finditer(r"\(\|([A-Z.\-]{1,6})\|[^|]*\)\|([^|]{2,120})\|(\d\d/\d\d/\d{4})\|[^|]*\|(pending)",t):
            d=dt.datetime.strptime(m.group(3),"%m/%d/%Y").date()
            if 0<=(d-dt.date.today()).days<=21: ev.setdefault(m.group(1),[]).append(f"FDA {m.group(2)[:40]} {d}")
        feed("fda_calendar",True,f"rttnews page1 (recent+upcoming): {len(ev)} US tickers with pending FDA decision <=21d"); return ev
    except Exception as e: feed("fda_calendar",False,e); return {}

def macro():
    try:
        x=requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",timeout=20).json()
        ev=[e for e in x if e.get("country")=="USD" and e.get("impact")=="High"]
        feed("forexfactory.calendar",True,f"{len(ev)} high-impact USD"); return ev
    except Exception as e: feed("forexfactory.calendar",False,e); return []

EXT=dict(rule="At time exit: if pnl>0 AND (close>10dMA AND ADX rising, OR new 5d high) extend 1 trading day at a time, max 10 trading days total",trail_on_extend="2xATR chandelier (highest high since entry - 2*ATR14)",at_plus2="sell half (unchanged)",at_plus5="do NOT flatten; tighten trail to 1xATR",no_ext="flat/losing trades exit at time limit",scoring="extended trades scored as separate cohort: P&L at base time exit vs final P&L",extended=False,ext_days=0,base_exit_mark=None)
def ticket(r,risk_aud,fx,venue,direction="long",shield=False):
    """GPT-adopted: trigger above prior-day high (long) / below prior-day low (short); completed 15m close + max fill; bid/ask fills"""
    sgn=1 if direction=="long" else -1
    if direction=="long": trig=max(r.pdh,r.hi20 if r.breakout or r.near_bo else r.pdh)*1.001
    else: trig=min(r.pdl,r.lo10)*0.999
    maxfill=trig*(1+sgn*0.0015)
    struct=(r.pdl if direction=="long" else r.pdh)*(1-sgn*0.002)
    stop=trig-sgn*max(1.25*r.atr,abs(trig-struct)) if abs(trig-struct)<2.5*r.atr else trig-sgn*1.25*r.atr
    if abs(trig-stop)/trig<0.015: stop=trig*(1-sgn*0.015)
    rp=abs(maxfill-stop)/maxfill
    cost=(2*CFG["costs"]["kraken_maker_pct"]+0) if venue=="kraken" else None
    if venue=="kraken": cost=CFG["costs"]["kraken_maker_pct"]+CFG["costs"]["kraken_taker_pct"]  # maker entry, worst-case taker exit (stop)
    risk_eff=risk_aud*(0.5 if shield else 1.0)
    size=risk_eff/(rp+(cost or 0.1)/100)
    if venue!="kraken": cost=round(100*(2*CFG["costs"]["ibkr_usd_per_side"]/(size*fx))+2*CFG["costs"]["ibkr_slip_bp_per_side"]/100,3)
    return dict(direction=direction,entry_trigger=round(trig,4),max_fill=round(maxfill,4),
        entry_rule=("post-only LIMIT at trigger after a completed 1h close through it (maker only; no taker entries)" if venue=="kraken" else "completed 15-min close beyond trigger (RTH, after 21:45 WITA), then one capped IOC at <= max_fill vs ask (long) / >= vs bid (short); partial fills OK, no chase"),
        fill_model="bid/ask executable (ask for buys, bid for sells), not next print",
        stop=round(stop,4),stop_pct=round(100*rp,2),t1=round(trig*(1+sgn*0.02),4),t2=round(trig*(1+sgn*0.05),4),
        size_aud=round(size),risk_aud=round(risk_eff),event_shield=bool(shield),est_roundtrip_cost_pct=round(cost,3),
        pct_of_adv=round(100*size*fx/r.adv_usd,3),gap5_stress_aud=round(0.05*size),atr=round(float(r.atr),4),extension=dict(EXT))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",default=os.path.dirname(os.path.abspath(__file__))+"/out"); ap.add_argument("--sleeve",type=float,default=1e6); ap.add_argument("--risk",type=float,default=5000); ap.add_argument("--max-open",type=int,default=3); ap.add_argument("--cat-n",type=int,default=40)
    a=ap.parse_args(); os.makedirs(a.out,exist_ok=True); t0=time.time()
    try: fx=float(requests.get("https://api.frankfurter.app/latest?from=AUD&to=USD",timeout=15).json()["rates"]["USD"]); feed("fx.frankfurter",True,fx)
    except Exception: fx=float(yf.Ticker("AUDUSD=X").history(period="5d").Close.iloc[-1])
    U=universe_us(); print("listed",len(U))
    cache=f"{a.out}/ohlcv_{dt.date.today()}.pkl"
    d=pd.read_pickle(cache) if os.path.exists(cache) else dl(U.sym.tolist(),"1y")
    if d is not None and not os.path.exists(cache): d.to_pickle(cache); feed("yfinance.bulk_ohlcv",d is not None)
    C,Hh,L,O,V=d["Close"],d["High"],d["Low"],d["Open"],d["Volume"]
    px=C.ffill().iloc[-1]; adv=(C*V).iloc[-20:].mean()
    US=set(U.sym)
    keep=[s for s in C.columns if s in US and px.get(s,0)>3 and adv.get(s,0)>5e6 and C[s].notna().sum()>=60]
    print("liquid US",len(keep),round(time.time()-t0),"s")
    spy=C["SPY"].dropna() if "SPY" in C else yf.download("SPY",period="1y",progress=False,auto_adjust=True).Close.squeeze()
    rows=[]
    for s in keep:
        try:
            m=pd.concat([O[s],Hh[s],L[s],C[s],V[s]],axis=1,keys=list("ohlcv")).dropna()
            r=ind(m.o,m.h,m.l,m.c,m.v); r["sym"]=s; r["venue"]="ibkr"; r["etf"]=bool(U.set_index("sym").etf.get(s,False))
            r["rs20"]=r["roc20"]-(spy.iloc[-1]/spy.iloc[-21]-1)*100
            r["_mirror"]=ind(1/m.o,1/m.l,1/m.h,1/m.c,m.v); r["_mirror"]["rs20"]=-r["rs20"]; rows.append(r)
        except Exception: pass
    # crypto
    K=kraken(); cc=crypto_catalysts(); btc20=None
    for _,k in K.iterrows():
        try:
            m=kraken_ohlc(k.pair); time.sleep(1.05)
            if len(m)<60: KSKIP[k.ws]=f"only {len(m)} daily bars"; continue
            r=ind(m.o,m.h,m.l,m.c,m.v*m.c/m.c); r["adv_usd"]=float((m.c*m.v).iloc[-20:].mean()); r["sym"]=k.ws; r["venue"]="kraken"; r["etf"]=False
            if k.ws.startswith(("XBT/","BTC/")) and btc20 is None: btc20=r["roc20"]
            r["vol24_usd"]=k.vol24_usd; r["_mirror"]=ind(1/m.o,1/m.l,1/m.h,1/m.c,m.v); rows.append(r)
        except Exception as e: KSKIP[k.ws]="ohlc/ind error: "+str(e)[:80]
    feed("kraken.ohlc",True,f"{sum(1 for r in rows if r['venue']=='kraken')} pairs")
    for r in rows:
        if r["venue"]=="kraken":
            r["rs20"]=r["roc20"]-(btc20 or 0); r["_mirror"]["rs20"]=-r["rs20"]
            # thin-coin rule: crypto needs >$20m 20d ADV and not +40% 5d (pump-chase)
            r["excluded"]=bool(r["adv_usd"]<2e7 or (r["roc5"] or 0)>40)
            if r["excluded"]: KSKIP[r["sym"]]="20d ADV<$20m" if r["adv_usd"]<2e7 else "roc5>40% pump"
            base=r["sym"].split("/")[0]; f=cc.get("funding",{}).get(f"PF_{'XBT' if base in ('XBT','BTC') else base}USD")
            if f: r.update({k:v for k,v in f.items() if k!="funding_kind"})   # funding = relative fraction/hour (NOT absolute)
            notes=[]
            if base in cc.get("cg_trending",[]): notes.append("coingecko trending")
            ub=cc.get("unlocks",{}).get("BTC" if base=="XBT" else base)
            if ub: notes.append(f"token unlock {ub}% next 7d"); r["unlock_7d_pct"]=ub
            if base in ("XBT","BTC","ETH") and cc.get("etf_flows"): notes.append("ETF flow 5d US$m "+str(cc["etf_flows"].get("ETH" if base=="ETH" else "BTC",{}).get("sum5d")))
            r["cat_notes"]=json.dumps(notes)
    R=pd.DataFrame(rows); R["excluded"]=(R["excluded"] if "excluded" in R else pd.Series(False,index=R.index)).fillna(False).astype(bool)   # 2026-10-27 hunt_orchestrator: no crypto rows (Kraken unreachable) must not crash the US scan
    empty_scan_guard(R)   # tforul 2026-10-30 (03:33 WITA rc=1 AttributeError)
    R["cost_pct"]=np.where(R.venue=="kraken",0.80,0.12)
    R["score"],_=zip(*[score(r,r.cost_pct) for _,r in R.iterrows()])
    def mscore(r):
        m=pd.Series(r["_mirror"]); m["adv_usd"]=r.adv_usd; return score(m,r.cost_pct)[0]
    R["short_score"]=[mscore(r) for _,r in R.iterrows()]
    spy50=spy.iloc[-50:].mean(); btcr=R[R.sym.str.startswith(("XBT/","BTC/"))]
    REG={"ibkr":"long" if spy.iloc[-1]>spy50 else "short","kraken":("long" if (len(btcr) and btcr.iloc[0].px>btcr.iloc[0].m50) else "short")}
    REG_NOTE={"SPY":round(float(spy.iloc[-1]),2),"SPY_50dMA":round(float(spy50),2),"BTC":round(float(btcr.iloc[0].px),1) if len(btcr) else None,"BTC_50dMA":round(float(btcr.iloc[0].m50),1) if len(btcr) else None,"regime":REG}
    print("REGIME",REG_NOTE)
    R=set_directions(R,REG)   # Brett 2026-10-27 21:18 WITA: per-row US direction; SPY regime = rank penalty only
    # catalysts on top N US names
    top=R[(R.venue=="ibkr")&(~R.excluded)] if a.cat_n<=0 else R[(R.venue=="ibkr")&(~R.excluded)].nlargest(a.cat_n,"rank_score")
    FDA=fda_calendar()
    from concurrent.futures import ThreadPoolExecutor
    cpath=f"{a.out}/catcache_{dt.date.today()}.json"; CC=json.load(open(cpath)) if os.path.exists(cpath) else {}
    def _one(sym):
        if sym in CC: return
        for k_ in range(5):
            c=catalysts_us(sym)
            if not c.get("_rl"): break
            time.sleep(15*(k_+1))
        if c.get("_rl"): RLFAIL.append(sym); return
        c["_ins"]=insider_buys(sym); CC[sym]=c; time.sleep(0.5)
    todo=[s_ for s_ in top.sym if s_ not in CC]; print("catalyst research", len(top), "names,", len(todo), "uncached")
    RLFAIL=[]
    with ThreadPoolExecutor(3) as ex:
        for n_,_ in enumerate(ex.map(_one,todo)):
            if n_%200==0: json.dump(CC,open(cpath,"w"),default=str); print("  researched",n_,"/",len(todo),round(time.time()-t0),"s",flush=True)
    json.dump(CC,open(cpath,"w"),default=str)
    feed("catalyst_coverage",True,f"{sum(1 for s_ in top.sym if s_ in CC)}/{len(top)} liquid US names researched (all yf endpoints answered); {len(RLFAIL)} still rate-limited")
    for i,r in top.iterrows():
        c=dict(CC.get(r.sym) or {"cat_notes":[],"cat_score":0.0}); ins=c.pop("_ins",None); c["cat_notes"]=list(c.get("cat_notes",[]))
        if r.sym in FDA: c["cat_notes"]+=FDA[r.sym]; c["fda_event"]=True
        if ins: c.update(ins); c["cat_score"]+=0.4*min(ins["insider_open_mkt_buys"],2)
        if ins and ins["insider_open_mkt_buys"]: c["cat_notes"].append(f"insider open-mkt buys {ins['insider_open_mkt_buys']} (14d)")
        c["event_play"]=False
        for k,v in c.items():
            R.at[i,k]=json.dumps(v) if isinstance(v,(list,dict)) else v
        R.at[i,"score"]=score(R.loc[i],R.at[i,"cost_pct"])[0]
        cs=float(R.at[i,"cat_score"] or 0)
        if R.at[i,"venue"]=="ibkr":   # researched: re-decide direction with the catalyst vote
            d_,b_=us_direction(R.loc[i],cs); R.at[i,"dir"]=d_; R.at[i,"dir_basis"]=b_; R.at[i,"counter_regime"]=bool(d_!=R.at[i,"regime_dir"])
        if R.at[i,"dir"]=="short": R.at[i,"rank_score"]=R.at[i,"short_score"]-W["catalyst"]*cs-(W["earn"]*-1 if (R.at[i,"earn_days"]==R.at[i,"earn_days"] and R.at[i,"earn_days"]<=3) else 0)
        else: R.at[i,"rank_score"]=R.at[i,"score"]
        if R.at[i,"venue"]=="ibkr" and R.at[i,"counter_regime"]: R.at[i,"rank_score"]-=float(CFG["direction"]["counter_regime_rank_penalty"])
    for k in ("excluded",):
        R[k]=R[k].astype(bool)
    R.drop(columns=["_mirror"]).sort_values("rank_score",ascending=False).to_csv(f"{a.out}/ranked_all_incl_excluded.csv",index=False)
    R=R[~R.excluded].sort_values("rank_score",ascending=False)
    R.drop(columns=["_mirror"]).to_csv(f"{a.out}/ranked.csv",index=False)
    # tickets with max-open + correlation check
    fomc=[pd.Timestamp(x) for x in CFG["event_shield"]["fomc"]]; now=pd.Timestamp.now(tz="Asia/Makassar")
    maxhold=now+pd.Timedelta(days=14)
    spans_fomc=any(now<f<=maxhold for f in fomc)
    cands=[]; rets={}
    for _,r in R.head(150).iterrows():
        d=r.dir; m=r["_mirror"]
        setup=(r.breakout or r.near_bo or r.pullback) if d=="long" else (m["breakout"] or m["near_bo"] or m["pullback"])
        if not setup: TSKIP[r.sym]="no setup (no breakout/near-breakout/pullback in trade direction)"; continue
        ed=r.get("earn_days",99); ed=99 if ed!=ed or ed is None else ed
        if r.venue=="ibkr" and ed<=3: TSKIP[r.sym]="own earnings <=3d"; continue  # own earnings inside base window, not event play
        shield=spans_fomc or (r.venue=="ibkr" and ed<=14)
        tk=ticket(r,a.risk,fx,r.venue,d,shield)
        if tk["pct_of_adv"]>1.0 or tk["stop_pct"]>CFG["max_stop_pct"]: TSKIP[r.sym]=f"size {tk['pct_of_adv']}% ADV / stop {tk['stop_pct']}% > cap"; continue
        if d=="short" and "squeeze" in str(r.get("cat_notes","")): TSKIP[r.sym]="short squeeze flag"; continue  # no shorting squeeze setups
        tk.update(ticker=r.sym,venue=r.venue,score=round(float(r.rank_score),2),
            why=(f"{d.upper()} (own {r.get('dir_basis','')}; {'counter-regime, rank penalised' if bool(r.get('counter_regime',False)) else 'with regime'}) "+(f"stack{r.stack}/3 ADX{r.adx:.0f} RSI{r.rsi:.0f}" if d=="long" else f"down-stack{m['stack']}/3 ADX{m['adx']:.0f} RSI{r.rsi:.0f}")+f" relvol{r.relvol:.1f} roc20 {r.roc20:+.1f}% rs20 {r.rs20:+.1f}"),
            catalyst=str(r.get("cat_notes","")) if r.get("cat_notes","")==r.get("cat_notes","") else "",next_earn=r.get("next_earn"),
            time_exit_wita=("+72h from fill, executed inside US RTH (21:30-04:00 WITA), e.g. Thu 29 Oct 22:00 WITA" if r.venue=="ibkr" else "+72h from fill"),
            event_shield_reason=("spans FOMC" if spans_fomc else "")+(" + own earnings "+str(r.get("next_earn")) if r.venue=="ibkr" and ed<=14 else ""),
            status="pending_trigger",entry_fill=None,exit_fill=None,pnl_aud=None,mode="PAPER",audusd=round(fx,4))
        try:
            ser=(C[r.sym] if r.venue=="ibkr" else kraken_ohlc(K.set_index("ws").pair[r.sym]).c).pct_change().dropna().iloc[-60:]
            ser.index=pd.to_datetime(ser.index).tz_localize(None).normalize()
        except Exception: ser=pd.Series(dtype=float)
        cands.append((tk,ser))
        if len(cands)>=40: break
    tickets=[]; shadow=[]
    for tk,ser in cands:
        corr_ok=all(abs(ser.corr(p))<=CFG["max_corr"] or ser.corr(p)!=ser.corr(p) for _,p in [(t["ticker"],t["_ser"]) for t in tickets])
        if not corr_ok: TSKIP[tk["ticker"]]="corr>0.6 with a held ticket"
        if len(tickets)<a.max_open and corr_ok: tk["_ser"]=ser; tk["id"]=len(tickets)+1; tk["book"]="LIVE_PAPER"; tickets.append(tk)
        elif len(shadow)<7: tk["id"]=100+len(shadow)+4; tk["book"]="SHADOW_ZERO_RISK"; tk["risk_aud"]=0; tk["shadow_rank"]=len(shadow)+4; shadow.append(tk)
    for t in tickets: t.pop("_ser",None)
    gap=sum(t["gap5_stress_aud"] for t in tickets)
    mac=macro()
    rep=dict(run_at=dt.datetime.now().isoformat(timespec="seconds")+" WITA",runtime_s=round(time.time()-t0),universe=dict(us_listed=len(U),us_liquid=len(keep),kraken_pairs=int((R.venue=="kraken").sum()),scored=len(R)),
        weights=W,regime=REG_NOTE,gap5_stress_aud_total=gap,shadow=shadow,feeds=FEEDS,macro_high_impact=mac,top10=R.head(10)[["sym","venue","dir","rank_score","score","short_score","px","stack","adx","rsi","roc5","roc20","relvol","breakout","pullback","atr_pct"]].round(2).to_dict("records"),tickets=tickets,skip_reasons_top40=TSKIP,kraken_skips=KSKIP,kraken_loaded=int(len(K)),
        sleeve_aud=a.sleeve,rules=f"PAPER; risk A${a.risk:.0f}; max {a.max_open} open; corr<0.6; half +2%, flat +5% or time exit")
    json.dump(rep,open(f"{a.out}/scan_report.json","w"),indent=1,default=str)
    json.dump(dict(sleeve_aud=a.sleeve,rules=rep["rules"],trades=tickets,shadow=shadow,regime=REG_NOTE,gap5_stress_aud_total=gap,generated=rep["run_at"]),open(f"{a.out}/tickets.json","w"),indent=1,default=str)
    print(json.dumps(dict(universe=rep["universe"],runtime_s=rep["runtime_s"]),indent=1)); print(R.head(10)[["sym","venue","dir","rank_score","px","relvol","roc20"]].to_string())
    for t in tickets+shadow: print(t["book"],t["ticker"],t["direction"],t["entry_trigger"],t["stop"],t["size_aud"],t["risk_aud"],t["why"],t["catalyst"])
    print(json.dumps(FEEDS,indent=1))
if __name__=="__main__": main()
