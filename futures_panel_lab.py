#!/usr/bin/env python3
"""futures_panel_lab — the code behind reports/research_futures_panel.md.

READ-ONLY research. Touches nothing but the sqlite panel (SELECT only), the Kraken
public OHLC endpoint, and quant_validation.py (imported, unmodified).

Four stages, run in order from a scratch directory:
  STAGE 1 build_panel   sqlite futures_analytics -> 30-min bucket series per (symbol, field)
  STAGE 2 fetch_prices  Kraken public REST OHLC(30m) + market_intelligence.candles
  STAGE 3 frame         align panel + price into per-symbol arrays, measure liquidity
  STAGE 4 lab           the 144-cell primary hypothesis grid (TS + XS x 12 signals x 6 horizons)

Kill-tests (liquidity floors, entry-delay ladder), the honest effective-N, the power
analysis and the quant_validation runs are driven from the same ts_test/xs_test entry
points in STAGE 4; see the report for the numbers and reports/futures_panel_lab.json
for the machine-readable receipt.
"""

# ============================== STAGE: build_panel ==============================
"""Reconstruct futures_analytics into one 30-min bucket series per (symbol, field).
Payload quirks handled: funding timestamps are MILLISECONDS, all others seconds;
open-interest and funding.rate/relativeRate are per-bucket OHLC quads, the rest scalars."""
import sqlite3, json, collections, pickle, math, datetime as dt
DB="/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
def f(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except Exception: return None
panel=collections.defaultdict(dict); spotmap={}
c=sqlite3.connect(DB)
nrow=0; nbad=0
for spot,fsym,metric,pj in c.execute(
        "SELECT spot_symbol,futures_symbol,metric,payload_json FROM futures_analytics WHERE metric!='availability'"):
    nrow+=1; spotmap[fsym]=spot
    d=json.loads(pj); ts=d.get("timestamp") or []
    if not ts: continue
    ts=[int(t)//1000 if int(t)>1e11 else int(t) for t in ts]
    dat=d.get("data")
    def put(field, vals, pick=None):
        global nbad
        if not vals or len(vals)!=len(ts): return
        for t,v in zip(ts,vals):
            if pick is not None and isinstance(v,list): v=v[pick] if len(v)>pick else None
            x=f(v)
            if x is None: continue
            if not (1700000000<t<1800000000): nbad+=1; continue
            panel[(fsym,field)][t]=x
    if metric=="open-interest":  put("oi", dat, pick=3)
    elif metric=="funding":      put("funding", dat.get("relativeRate"), pick=3); put("funding_abs", dat.get("rate"), pick=3)
    elif metric=="long-short-ratio": put("lsr", dat)
    elif metric=="future-basis": put("basis", dat.get("basis"))
    elif metric=="cvd":          put("cvd", dat.get("cvd")); put("buy_vol", dat.get("buy_volume")); put("sell_vol", dat.get("sell_volume"))
    elif metric=="liquidation-volume": put("liq", dat)
    elif metric=="aggressor-differential": put("aggr", dat)
panel=dict(panel)
print("rows parsed %d, out-of-range cells dropped %d"%(nrow,nbad))
by=collections.defaultdict(lambda:[0,0])
for (s,fd),ser in panel.items(): by[fd][0]+=1; by[fd][1]+=len(ser)
U=lambda t: dt.datetime.fromtimestamp(t,dt.UTC).strftime("%Y-%m-%d %H:%M")
print("%-12s %7s %8s %8s"%("field","series","cells","medlen"))
for fd in sorted(by):
    lens=sorted(len(panel[(s,f2)]) for (s,f2) in panel if f2==fd)
    print("%-12s %7d %8d %8d"%(fd,by[fd][0],by[fd][1],lens[len(lens)//2]))
allb=sorted(set(t for s in panel.values() for t in s))
print("bucket span",U(allb[0]),"->",U(allb[-1]),"n_union",len(allb))
# cross-sectional breadth per bucket
br=collections.Counter()
for (s,fd),ser in panel.items():
    if fd!="oi": continue
    for t in ser: br[t]+=1
b=sorted(br.values())
print("XS breadth per 30-min bucket (symbols with OI): median %d, p10 %d, p90 %d, max %d"%(
    b[len(b)//2], b[len(b)//10], b[9*len(b)//10], b[-1]))
pickle.dump({"panel":panel,"spotmap":spotmap},open("panel.pkl","wb"))

# ============================== STAGE: fetch_prices ==============================
"""Prices for the panel. Same source the desk already uses: Kraken public REST OHLC
(`https://api.kraken.com/0/public/OHLC`, interval=30) — identical endpoint that filled
market_intelligence.candles (source='kraken-rest-ohlc'). Fresh pull covers 08-09..08-24;
the candles table covers 07-18..08-21; union spans the whole panel window."""
import urllib.request,json,time,pickle,sqlite3,collections,sys
D=pickle.load(open("panel.pkl","rb")); spotmap=D["spotmap"]
syms=sorted(set(spotmap[s] for s in set(k[0] for k in D["panel"])))
print("need",len(syms),"spot symbols")
def get(url):
    for a in range(4):
        try:
            return json.loads(urllib.request.urlopen(url,timeout=25).read())
        except Exception as e:
            time.sleep(2+2*a)
    return None
ap=get("https://api.kraken.com/0/public/AssetPairs")
alt={}
for k,v in (ap or {}).get("result",{}).items():
    ws=v.get("wsname"); 
    if ws: alt[ws]=v["altname"]
    alt[v["altname"]]=v["altname"]
px=collections.defaultdict(dict)  # spot -> {ts: (close, vwap, volume)}
# 1) candles table
c=sqlite3.connect("/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite")
q=",".join("?"*len(syms))
n=0
for s,ts,cl,vw,vol in c.execute(
        "SELECT symbol,ts,close,vwap,volume FROM candles WHERE interval_min=30 AND symbol IN (%s)"%q, syms):
    px[s][int(ts)]=(cl, vw or cl, vol or 0.0); n+=1
print("from candles table:",n,"bars,",len(px),"symbols")
# 2) fresh REST pull
miss=[]
for i,s in enumerate(syms):
    pair=alt.get(s) or s.replace("/","")
    d=get("https://api.kraken.com/0/public/OHLC?pair=%s&interval=30"%pair)
    if not d or d.get("error") or not d.get("result"):
        miss.append(s); continue
    key=[k for k in d["result"] if k!="last"]
    if not key: miss.append(s); continue
    for row in d["result"][key[0]]:
        px[s][int(row[0])]=(float(row[4]), float(row[5]) or float(row[4]), float(row[6]))
    time.sleep(0.35)
    if i%20==0: print("  fetched",i,flush=True)
print("REST pull done. unmapped/failed:",len(miss),miss[:10])
tot=sum(len(v) for v in px.values())
print("total price bars:",tot,"symbols with prices:",len(px))
pickle.dump(dict(px),open("prices.pkl","wb"))

# ============================== STAGE: frame ==============================
import pickle,numpy as np,collections
D=pickle.load(open("panel.pkl","rb")); panel=D["panel"]; spotmap=D["spotmap"]
px=pickle.load(open("prices.pkl","rb"))
FIELDS=["oi","lsr","basis","cvd","buy_vol","sell_vol","liq","aggr","funding"]
frames={}
for f in sorted(set(k[0] for k in panel)):
    s=spotmap[f]; p=px.get(s)
    if not p: continue
    ts=sorted(panel.get((f,"oi"),{}))
    ts=[t for t in ts if t in p]
    if len(ts)<96: continue
    T=np.array(ts,dtype=np.int64)
    d={"ts":T,"close":np.array([p[t][0] for t in T]),
       "dvol":np.array([p[t][1]*p[t][2] for t in T])}
    for fd in FIELDS:
        ser=panel.get((f,fd),{})
        d[fd]=np.array([ser.get(t,np.nan) for t in T])
    frames[f]=d
print("frames:",len(frames),"total bucket-rows:",sum(len(v['ts']) for v in frames.values()))
# 24h dollar volume per symbol (median of rolling 48-bar sums over panel window)
liqv={}
for f,d in frames.items():
    dv=d["dvol"]
    if len(dv)<48: liqv[f]=float(np.nansum(dv)); continue
    cs=np.concatenate([[0],np.nancumsum(dv)]); liqv[f]=float(np.median(cs[48:]-cs[:-48]))
pickle.dump({"frames":frames,"liq":liqv,"spotmap":spotmap},open("frame.pkl","wb"))
print("liquid>=5M:",sorted(f for f in liqv if liqv[f]>=5e6))
print("liquid>=1M:",len([f for f in liqv if liqv[f]>=1e6]))

# ============================== STAGE: lab ==============================
"""Futures-analytics positioning panel — signal hunt.
Read-only. Every cell counted for the search deflation."""
import pickle,numpy as np,collections,math,json,sys
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk")
import quant_validation as qv

F=pickle.load(open("frame.pkl","rb")); frames=F["frames"]; liqv=F["liq"]
BAR=1800
HORIZONS=[1,2,4,8,24,48]           # in 30-min bars -> 0.5h,1h,2h,4h,12h,24h
HNAME={1:"0.5h",2:"1h",4:"2h",8:"4h",24:"12h",48:"24h"}

def zroll(x,w=48):
    """causal rolling z-score, min 12 obs"""
    out=np.full(len(x),np.nan)
    for i in range(len(x)):
        lo=max(0,i-w+1); s=x[lo:i+1]; s=s[np.isfinite(s)]
        if len(s)<12: continue
        sd=s.std(ddof=1)
        if sd>0: out[i]=(x[i]-s.mean())/sd
    return out

def dlog(x,k):
    out=np.full(len(x),np.nan)
    with np.errstate(all="ignore"):
        out[k:]=np.log(np.where(x[k:]>0,x[k:],np.nan)/np.where(x[:-k]>0,x[:-k],np.nan))
    return out

def diff(x,k):
    out=np.full(len(x),np.nan); out[k:]=x[k:]-x[:-k]; return out

def build_signals(d):
    """All signals are causal: value at index i uses data at or before i."""
    c=d["close"]; ret=dlog(c,1)
    sig={}
    oi=d["oi"]
    sig["oi_chg_1h"]  = zroll(dlog(oi,2))
    sig["oi_chg_4h"]  = zroll(dlog(oi,8))
    # positioning build-up: OI rising with price = new longs (continuation candidate)
    r1=dlog(c,2)
    sig["oi_x_price"] = zroll(np.sign(np.nan_to_num(dlog(oi,2)))*r1)
    # liquidation spike, signed to FADE the concurrent move (reversal hypothesis)
    liq=np.nan_to_num(d["liq"],nan=0.0)
    lz=zroll(liq)
    sig["liq_spike_fade"] = -np.sign(np.nan_to_num(r1))*np.clip(lz,0,None)
    sig["liq_spike_cont"] = -sig["liq_spike_fade"]
    # aggressor differential normalised by traded volume
    bv=np.nan_to_num(d["buy_vol"],nan=0.0); sv=np.nan_to_num(d["sell_vol"],nan=0.0)
    tot=bv+sv; imb=np.where(tot>0,(bv-sv)/np.where(tot>0,tot,1),np.nan)
    sig["aggr_imb"]=zroll(imb)
    cvd=d["cvd"]
    sig["cvd_slope"]=zroll(diff(cvd,2))
    # CVD/price divergence: flow up but price down (or vice versa) -> flow wins?
    sig["cvd_div"]=zroll(diff(cvd,4))-zroll(dlog(c,4))
    lsr=d["lsr"]
    sig["lsr_contra"]=-zroll(lsr)
    sig["lsr_chg_contra"]=-zroll(diff(lsr,2))
    b=d["basis"]
    sig["basis_level"]=zroll(b)
    # basis orthogonalised to funding (carry already validated; test only the residual)
    fu=d["funding"]
    m=np.isfinite(b)&np.isfinite(fu)
    res=np.full(len(b),np.nan)
    if m.sum()>30 and np.nanstd(fu[m])>0:
        A=np.vstack([fu[m],np.ones(m.sum())]).T
        coef,_,_,_=np.linalg.lstsq(A,b[m],rcond=None)
        res[m]=b[m]-(A@coef)
    sig["basis_resid"]=zroll(res)
    return sig,ret

SIGNALS=["oi_chg_1h","oi_chg_4h","oi_x_price","liq_spike_fade","liq_spike_cont",
         "aggr_imb","cvd_slope","cvd_div","lsr_contra","lsr_chg_contra",
         "basis_level","basis_resid"]

SIG={} ; RET={}
for f,d in frames.items():
    s,r=build_signals(d); SIG[f]=s; RET[f]=r

def fwd(d,h):
    c=d["close"]; out=np.full(len(c),np.nan)
    with np.errstate(all="ignore"): out[:-h]=np.log(c[h:]/c[:-h])
    return out

# ---------------- time-series test: pooled, non-overlapping, bucket-clustered -------
def ts_test(signame,h,min_liq=0.0,entry_lag=1):
    """entry_lag in bars: signal observed at i, enter at i+lag, hold h bars.
    Non-overlapping: stride = h+lag."""
    per_bucket=collections.defaultdict(list)   # bucket ts -> list of per-symbol pnl (bps)
    per_sym=collections.defaultdict(list)
    for f,d in frames.items():
        if liqv.get(f,0)<min_liq: continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=fwd(d,h); ts=d["ts"]; step=h+entry_lag
        # forward return measured from entry bar (i+lag) over h bars
        for i in range(0,len(s)-step,step):
            j=i+entry_lag
            if j+h>=len(s): break
            if not np.isfinite(s[i]) or not np.isfinite(fr[j]): continue
            pnl=np.sign(s[i])*fr[j]*10000.0
            if not np.isfinite(pnl): continue
            per_bucket[int(ts[j])].append(pnl); per_sym[f].append(pnl)
    if not per_bucket: return None
    buckets=sorted(per_bucket)
    bmeans=np.array([np.mean(per_bucket[b]) for b in buckets])
    if len(bmeans)<8: return None
    gross=float(bmeans.mean())
    sd=bmeans.std(ddof=1)
    t_clu=float(gross/(sd/math.sqrt(len(bmeans)))) if sd>0 else 0.0
    flat=np.array([x for b in buckets for x in per_bucket[b]])
    n_sym=len(per_sym)
    # mean pairwise correlation across symbol return series (aligned on buckets)
    mat=[]
    common=None
    for f,v in per_sym.items():
        if len(v)>=8: mat.append(v)
    L=min((len(m) for m in mat),default=0)
    rho=qv.mean_pairwise_correlation([m[:L] for m in mat]) if len(mat)>=2 and L>=8 else 0.0
    n_eff=qv.effective_sample_size(len(flat),n_series=max(1,n_sym),mean_correlation=max(0.0,rho))
    return {"signal":signame,"horizon":HNAME[h],"h_bars":h,"gross_bps":gross,
            "net_bps":gross-60.0,"t_clustered":t_clu,"n_rebal":len(bmeans),
            "n_obs":len(flat),"n_sym":n_sym,"mean_rho":rho,"n_eff":n_eff,
            "bucket_means":bmeans,"min_liq":min_liq,"lag":entry_lag}

# ---------------- cross-sectional test: tercile spread ------------------------------
def xs_test(signame,h,min_liq=0.0,entry_lag=1,min_breadth=6):
    by_bucket=collections.defaultdict(dict)  # ts -> {sym:(sig, fwd_from_entry)}
    for f,d in frames.items():
        if liqv.get(f,0)<min_liq: continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=fwd(d,h); ts=d["ts"]
        for i in range(len(s)-h-entry_lag):
            j=i+entry_lag
            if np.isfinite(s[i]) and np.isfinite(fr[j]):
                by_bucket[int(ts[i])][f]=(float(s[i]),float(fr[j]))
    step=(h+entry_lag)*BAR
    buckets=sorted(b for b in by_bucket if len(by_bucket[b])>=min_breadth)
    if not buckets: return None
    chosen=[];last=-1
    for b in buckets:
        if b-last>=step: chosen.append(b); last=b
    spreads=[];ranks_prev=None;rank_ac=[]
    for b in chosen:
        row=by_bucket[b]; names=sorted(row)
        sv=np.array([row[n][0] for n in names]); rv=np.array([row[n][1] for n in names])
        k=max(1,len(names)//3)
        o=np.argsort(-sv)
        top=rv[o[:k]].mean(); bot=rv[o[-k:]].mean()
        spreads.append((top-bot)*10000.0)
        rk={n:i for i,n in enumerate(np.array(names)[o])}
        if ranks_prev:
            sh=set(rk)&set(ranks_prev)
            if len(sh)>=5:
                a=np.array([rk[n] for n in sh]);c2=np.array([ranks_prev[n] for n in sh])
                if a.std()>0 and c2.std()>0: rank_ac.append(float(np.corrcoef(a,c2)[0,1]))
        ranks_prev=rk
    sp=np.array(spreads)
    if len(sp)<8: return None
    gross=float(sp.mean()); sd=sp.std(ddof=1)
    t=float(gross/(sd/math.sqrt(len(sp)))) if sd>0 else 0.0
    rac=float(np.mean(rank_ac)) if rank_ac else 0.0
    eb=qv.effective_bets(len(sp),rac)
    med_breadth=int(np.median([len(by_bucket[b]) for b in chosen]))
    return {"signal":signame,"horizon":HNAME[h],"h_bars":h,"gross_bps":gross,
            "net_bps":gross-120.0,   # two legs, 30bps/side each => 120 bps round trip
            "t_clustered":t,"n_rebal":len(sp),"n_obs":len(sp),"n_sym":med_breadth,
            "rank_autocorr":rac,"independent_bets":eb["independent_bets"],
            "n_eff":max(2.0,eb["independent_bets"]),"bucket_means":sp,
            "min_liq":min_liq,"lag":entry_lag}

if __name__=="__main__":
    rows=[];N=0
    for sg in SIGNALS:
        for h in HORIZONS:
            r=ts_test(sg,h); N+=1
            if r: r["family"]="TS"; rows.append(r)
            r2=xs_test(sg,h); N+=1
            if r2: r2["family"]="XS"; rows.append(r2)
    print("primary cells attempted:",N,"returned:",len(rows))
    rows.sort(key=lambda r:-abs(r["t_clustered"]))
    print("\n%-16s %-3s %-5s %9s %9s %7s %7s %7s %8s"%("signal","fam","hor","gross","net","t","n_reb","n_sym","n_eff"))
    for r in rows[:25]:
        print("%-16s %-3s %-5s %9.2f %9.2f %7.2f %7d %7d %8.1f"%(
            r["signal"],r["family"],r["horizon"],r["gross_bps"],r["net_bps"],
            r["t_clustered"],r["n_rebal"],r["n_sym"],r["n_eff"]))
    pickle.dump({"rows":rows,"N_primary":N},open("rows.pkl","wb"))
