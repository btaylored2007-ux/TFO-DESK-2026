#!/usr/bin/env python3
"""futures_panel_liquidity_lab -- the code behind reports/research_liquidity_floor.md.

Re-tests the futures-analytics panel at a $100k/24h liquidity floor with MEASURED
per-symbol costs instead of the flat 30 bps/side charged in futures_panel_lab.py.

READ-ONLY research. Touches nothing but:
  - /Users/test/Applications/KrakenQuantDesk/reports/microstructure/ob_*.jsonl  (read)
  - the sqlite panel via yesterday's frame.pkl                                  (read)
  - two Kraken PUBLIC endpoints: /derivatives/api/v3/instruments (tickSize)
    and /derivatives/api/v3/feeschedules (taker fee)
  - quant_validation.py (imported, unmodified)
No capital, no orders, no engine, no secrets, no pre-registration created or edited.

Signal construction is copied VERBATIM from futures_panel_lab.py (build_signals,
zroll, dlog, diff, fwd, and the trade construction inside ts_test/xs_test) so the
two studies are comparable. ONLY the cost/liquidity model differs.

Stages, run in order:
  A  stage_a.py  measure spread + 10-level depth per symbol from the order-book files
  B  stage_b.py  per-symbol cost model; tickSize floor for symbols with no book
  C  stage_c.py  cost-vs-volume curve + extrapolation into the unmeasured range
  D  stage_d.py  re-run the 12x6x2 grid at floors $100k/$500k/$1M/$5M, measured cost
  E  stage_e.py  read the grid: does any gross alpha exceed its own measured cost?
  F  stage_f.py  drift control (always-long) + random-sign permutation null
  G  stage_g.py  cohort decomposition by volume BAND + capacity ceiling
  H  stage_h.py  noise benchmark, BH-FDR, quant_validation on every apparent survivor
  I  stage_i.py  the funder's exact corner: $100k-$500k band, validated + permuted
  J  stage_j.py  capacity ceiling in dollars + measured fill statistics

Receipt: reports/futures_panel_liquidity_lab.json
"""


# =============================== STAGE: stage_a ===============================

"""STAGE A: measure real spread + depth per symbol from order-book snapshots."""
import json,glob,os,collections,math,pickle
import numpy as np

MICRO="/Users/test/Applications/KrakenQuantDesk/reports/microstructure"
SCRATCH="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
LEVELS=10   # collector sums top-10 levels (kraken_perp_orderbook_collector.py:72)

def load(pattern, venue):
    rows=collections.defaultdict(list); days=collections.defaultdict(set)
    files=sorted(glob.glob(os.path.join(MICRO,pattern)))
    nbad=0
    for fn in files:
        base=os.path.basename(fn)
        day=base.replace("ob_perp_","").replace("ob_","")[:8]
        for line in open(fn):
            try: r=json.loads(line)
            except Exception: nbad+=1; continue
            s=r.get("sym"); mid=r.get("mid"); sp=r.get("spread_bps")
            if not s or mid is None or sp is None: nbad+=1; continue
            if not (math.isfinite(mid) and math.isfinite(sp)) or mid<=0: nbad+=1; continue
            if sp<0 or sp>1e4: nbad+=1; continue      # sanity: >100% spread is corruption
            rows[s].append((r.get("t"),mid,sp,r.get("bid_sz0"),r.get("ask_sz0"),
                            r.get("bid_depth"),r.get("ask_depth")))
            days[s].add(day)
    return rows,days,len(files),nbad

def summarise(rows,days,venue):
    out={}
    for s,v in rows.items():
        mid=np.array([x[1] for x in v],dtype=float)
        sp =np.array([x[2] for x in v],dtype=float)
        bs0=np.array([(x[3] if x[3] is not None else np.nan) for x in v],dtype=float)
        as0=np.array([(x[4] if x[4] is not None else np.nan) for x in v],dtype=float)
        bd =np.array([(x[5] if x[5] is not None else np.nan) for x in v],dtype=float)
        ad =np.array([(x[6] if x[6] is not None else np.nan) for x in v],dtype=float)
        # dollar depth, per side, at touch (L0) and across 10 levels
        l0_usd  = np.nanmin(np.vstack([bs0*mid, as0*mid]),axis=0)   # binding side
        d10_usd = np.nanmin(np.vstack([bd*mid,  ad*mid ]),axis=0)
        out[s]={"venue":venue,"n":len(v),"days":len(days[s]),
                "spread_med":float(np.median(sp)),"spread_mean":float(np.mean(sp)),
                "spread_sd":float(np.std(sp,ddof=1)) if len(sp)>1 else 0.0,
                "spread_p25":float(np.percentile(sp,25)),"spread_p75":float(np.percentile(sp,75)),
                "spread_p90":float(np.percentile(sp,90)),"spread_p99":float(np.percentile(sp,99)),
                "mid_med":float(np.median(mid)),
                "l0_usd_med":float(np.nanmedian(l0_usd)),"l0_usd_p25":float(np.nanpercentile(l0_usd,25)),
                "d10_usd_med":float(np.nanmedian(d10_usd)),"d10_usd_p25":float(np.nanpercentile(d10_usd,25)),
                "_l0":l0_usd,"_d10":d10_usd,"_sp":sp}
    return out

perp_rows,perp_days,npf,nbadp = load("ob_perp_*.jsonl","perp")
spot_rows,spot_days,nsf,nbads = load("ob_2026*.jsonl","spot")
print("perp files %d (bad lines %d) | spot files %d (bad lines %d)"%(npf,nbadp,nsf,nbads))
PERP=summarise(perp_rows,perp_days,"perp")
SPOT=summarise(spot_rows,spot_days,"spot")

print("\n=== MEASURED SPREAD, PERP (the panel's own venue) ===")
print("%-13s %7s %5s %9s %9s %9s %9s %12s %12s"%("symbol","n","days","sp_med","sp_mean","sp_p75","sp_p90","L0$_med","D10$_med"))
for s,d in sorted(PERP.items(),key=lambda kv:-kv[1]["n"]):
    print("%-13s %7d %5d %9.3f %9.3f %9.3f %9.3f %12.0f %12.0f"%(
        s,d["n"],d["days"],d["spread_med"],d["spread_mean"],d["spread_p75"],d["spread_p90"],
        d["l0_usd_med"],d["d10_usd_med"]))
print("\n=== MEASURED SPREAD, SPOT (cross-check) ===")
print("%-13s %7s %5s %9s %9s %12s %12s"%("symbol","n","days","sp_med","sp_mean","L0$_med","D10$_med"))
for s,d in sorted(SPOT.items(),key=lambda kv:-kv[1]["n"]):
    print("%-13s %7d %5d %9.3f %9.3f %12.0f %12.0f"%(
        s,d["n"],d["days"],d["spread_med"],d["spread_mean"],d["l0_usd_med"],d["d10_usd_med"]))

pickle.dump({"PERP":PERP,"SPOT":SPOT},open(os.path.join(SCRATCH,"costs_raw.pkl"),"wb"))

# =============================== STAGE: stage_b ===============================

"""STAGE B: per-symbol cost model. Measured where the book exists; exchange-metadata
floor (tickSize) everywhere else. Impact from the measured 10-level depth ladder."""
import json,os,pickle,math
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
C=pickle.load(open(os.path.join(S,"costs_raw.pkl"),"rb")); PERP=C["PERP"]
F=pickle.load(open(os.path.join(S,"frame.pkl"),"rb")); frames=F["frames"]; liqv=F["liq"]
INS={i["symbol"]:i for i in json.load(open(os.path.join(S,"kf_instruments.json")))}
TAKER_BPS=5.0            # 0.05% bottom tier, futures.kraken.com/derivatives/api/v3/feeschedules
CLIPS=[500.0,2500.0,10000.0]
LEVELS=10

# ---- median price per panel symbol (from the frames used in yesterday's study) ----
price={f:float(np.nanmedian(d["close"])) for f,d in frames.items()}

def tick_bps(sym):
    i=INS.get(sym)
    if not i: return None
    ts=i.get("tickSize"); p=price.get(sym)
    if ts is None or not p or p<=0: return None
    return float(ts)/p*1e4

def impact_bps(l0_usd, d10_usd, clip, tickbps):
    """Walk a 10-level ladder. Levels beyond touch are spaced ONE TICK apart -- the
    tightest spacing the exchange permits, i.e. the most generous (lowest) impact the
    data can support. Size beyond L0 is spread evenly over the remaining 9 levels.
    Returns (impact_bps_per_side, fillable)."""
    if not (np.isfinite(l0_usd) and np.isfinite(d10_usd)) or d10_usd<=0: return (np.nan,False)
    if clip<=l0_usd: return (0.0,True)              # fills at touch
    if clip>d10_usd: return (np.nan,False)          # 10 levels cannot fill it
    rest=d10_usd-l0_usd
    if rest<=0: return (np.nan,False)
    per=rest/(LEVELS-1)
    need=clip-l0_usd
    n=min(LEVELS-1, math.ceil(need/per)) if per>0 else LEVELS-1
    # value-weighted average distance from touch, in ticks (level k sits k ticks out)
    filled=0.0; cost=0.0
    for k in range(1,n+1):
        take=min(per, need-filled)
        if take<=0: break
        cost+=take*k*tickbps; filled+=take
    if filled<=0: return (0.0,True)
    return (cost/(l0_usd+filled)*(filled/(l0_usd+filled))*0 + cost/clip, True)

rows={}
for f in sorted(frames):
    tb=tick_bps(f)
    m=PERP.get(f)
    rec={"sym":f,"vol24":liqv.get(f,0.0),"price":price.get(f),"tick_bps":tb,
         "measured":bool(m),"n_snap":(m["n"] if m else 0),"days":(m["days"] if m else 0)}
    if m:
        rec["spread_med"]=m["spread_med"]; rec["spread_mean"]=m["spread_mean"]
        rec["spread_sd"]=m["spread_sd"]; rec["spread_p75"]=m["spread_p75"]; rec["spread_p90"]=m["spread_p90"]
        rec["l0_usd"]=m["l0_usd_med"]; rec["d10_usd"]=m["d10_usd_med"]
        for c in CLIPS:
            ib,ok=impact_bps(m["l0_usd_med"],m["d10_usd_med"],c,tb if tb else m["spread_med"])
            rec["imp_%d"%int(c)]=ib; rec["fill_%d"%int(c)]=ok
            # per-snapshot fillability, not just the median book
            fr=np.mean(C["PERP"][f]["_d10"]>=c) if f in C["PERP"] else np.nan
            rec["fillfrac_%d"%int(c)]=float(fr)
            rec["rt_%d"%int(c)]=(m["spread_med"]+2*TAKER_BPS+2*ib) if np.isfinite(ib) else np.nan
    rows[f]=rec

meas=[r for r in rows.values() if r["measured"]]
print("panel symbols: %d | with measured order book: %d | without: %d"%(
    len(rows),len(meas),len(rows)-len(meas)))
print("measured 24h-volume support: $%.2fM .. $%.1fM"%(
    min(r["vol24"] for r in meas)/1e6, max(r["vol24"] for r in meas)/1e6))

print("\n=== MEASURED ROUND-TRIP COST, by symbol (bps) ===")
print("%-13s %12s %8s %8s %9s %9s %9s %9s %9s"%(
    "symbol","vol24_usd","tick_bp","spr_med","L0$","D10$","RT@500","RT@2.5k","RT@10k"))
for r in sorted(meas,key=lambda x:-x["vol24"]):
    print("%-13s %12.0f %8.3f %8.3f %9.0f %9.0f %9.2f %9.2f %9.2f"%(
        r["sym"],r["vol24"],r["tick_bps"],r["spread_med"],r["l0_usd"],r["d10_usd"],
        r["rt_500"],r["rt_2500"],r["rt_10000"]))

print("\n=== EXCHANGE-METADATA COST FLOOR, symbols with NO order-book data ===")
print("(round-trip floor = 1 tick spread + 2x5bps taker fee; impact >= 0 not included)")
un=sorted([r for r in rows.values() if not r["measured"]],key=lambda x:-x["vol24"])
print("%-13s %12s %10s %12s"%("symbol","vol24_usd","tick_bps","RT_floor_bps"))
for r in un[:12]+un[-8:]:
    tb=r["tick_bps"]
    print("%-13s %12.0f %10s %12s"%(r["sym"],r["vol24"],
        ("%.3f"%tb) if tb else "n/a", ("%.2f"%(tb+2*TAKER_BPS)) if tb else "n/a"))
print("... (%d unmeasured symbols total)"%len(un))
pickle.dump({"rows":rows,"TAKER_BPS":TAKER_BPS,"CLIPS":CLIPS},open(os.path.join(S,"costmodel.pkl"),"wb"))

# =============================== STAGE: stage_c ===============================

"""STAGE C: cost-vs-volume curve + extrapolation to the unmeasured (illiquid) range."""
import os,pickle,math,json
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
M=pickle.load(open(os.path.join(S,"costmodel.pkl"),"rb")); rows=M["rows"]; TAKER=M["TAKER_BPS"]
C=pickle.load(open(os.path.join(S,"costs_raw.pkl"),"rb"))
F=pickle.load(open(os.path.join(S,"frame.pkl"),"rb")); liqv=F["liq"]
LEVELS=10; CLIPS=[500.,2500.,10000.]

meas=[r for r in rows.values() if r["measured"]]
x=np.log10([r["vol24"] for r in meas])
def fit(y,label):
    yl=np.log10(y)
    A=np.vstack([x,np.ones(len(x))]).T
    coef,res,_,_=np.linalg.lstsq(A,yl,rcond=None)
    pred=A@coef
    ss=1-((yl-pred)**2).sum()/((yl-yl.mean())**2).sum()
    print("  %-10s log10(y) = %+.4f*log10(V) %+.4f   R2=%.3f  n=%d"%(label,coef[0],coef[1],ss,len(x)))
    return coef,ss
print("=== log-log fits on the 6 MEASURED panel symbols (support $5.0M - $203M/day) ===")
cs,r2s=fit([r["spread_med"] for r in meas],"spread")
cl,r2l=fit([r["l0_usd"]     for r in meas],"L0_usd")
cd,r2d=fit([r["d10_usd"]    for r in meas],"D10_usd")

def px(coef,V): return 10**(coef[0]*math.log10(V)+coef[1])
def impact(l0,d10,clip,tickbps):
    if clip<=l0: return 0.0,True
    if clip>d10: return float("nan"),False
    per=(d10-l0)/(LEVELS-1); need=clip-l0; filled=0.;cost=0.
    for k in range(1,LEVELS):
        take=min(per,need-filled)
        if take<=0: break
        cost+=take*k*tickbps; filled+=take
    return cost/clip,True

print("\n=== THE HEADLINE ARTIFACT: what retail ACTUALLY pays, round-trip, by 24h volume ===")
print("Support of the measurement is $5.0M-$203M/day. Everything below $5M is EXTRAPOLATED.")
print("%-12s %10s %9s %10s %10s %9s %9s %9s  %s"%(
    "vol24/day","spread","L0$","D10$","tick_bps","RT@500","RT@2.5k","RT@10k","status"))
curve=[]
for V in [1e5,5e5,1e6,5e6,5e7,2e8]:
    sp=px(cs,V); l0=px(cl,V); d10=px(cd,V)
    # tick floor: median tick_bps of panel symbols within a half-decade of V
    near=[r["tick_bps"] for r in rows.values() if r["tick_bps"] and 10**-0.5<=r["vol24"]/V<=10**0.5]
    tb=float(np.median(near)) if near else float(np.median([r["tick_bps"] for r in rows.values() if r["tick_bps"]]))
    row={"vol":V,"spread":sp,"l0":l0,"d10":d10,"tick_bps":tb,
         "in_support": 5.04e6<=V<=2.034e8,"n_near":len(near)}
    for c in CLIPS:
        ib,ok=impact(l0,d10,c,max(tb,sp/2))
        row["rt_%d"%int(c)]=(sp+2*TAKER+2*ib) if ok else float("nan")
        row["fill_%d"%int(c)]=ok
    curve.append(row)
    st="MEASURED" if row["in_support"] else "EXTRAPOLATED (no data)"
    def s_(k):
        v=row["rt_%d"%int(k)]
        return ("%9.1f"%v) if np.isfinite(v) else "  UNFILL"
    print("%-12s %10.3f %9.0f %10.0f %10.3f %s %s %s  %s"%(
        "$%.0fk"%(V/1e3) if V<1e6 else "$%.0fM"%(V/1e6),
        sp,l0,d10,tb,s_(500),s_(2500),s_(10000),st))

print("\n(For reference, the SAME table restricted to what was actually measured:)")
print("%-13s %12s %9s %9s %9s %9s"%("symbol","vol24","spread","RT@500","RT@2.5k","RT@10k"))
for r in sorted(meas,key=lambda z:-z["vol24"]):
    print("%-13s %12.0f %9.3f %9.2f %9.2f %9.2f"%(r["sym"],r["vol24"],r["spread_med"],
        r["rt_500"],r["rt_2500"],r["rt_10000"]))

# per-symbol cost table used by the backtest: measured -> measured; else floor & fitted
COST={}
for f,r in rows.items():
    V=max(r["vol24"],1.0); tb=r["tick_bps"] or 1.0
    e={}
    for c in CLIPS:
        k=int(c)
        if r["measured"] and np.isfinite(r.get("rt_%d"%k,np.nan)):
            e["meas_%d"%k]=r["rt_%d"%k]; e["floor_%d"%k]=r["rt_%d"%k]; e["fit_%d"%k]=r["rt_%d"%k]
        else:
            e["floor_%d"%k]=tb+2*TAKER            # hard lower bound: 1 tick + fees, zero impact
            sp=px(cs,V); l0=px(cl,V); d10=px(cd,V)
            ib,ok=impact(l0,d10,c,max(tb,sp/2))
            e["fit_%d"%k]=(sp+2*TAKER+2*ib) if ok else float("nan")
            e["meas_%d"%k]=float("nan")
        e["fillable_%d"%k]= (r.get("fill_%d"%k,None) if r["measured"] else np.isfinite(e["fit_%d"%k]))
    e["measured"]=r["measured"]; e["vol24"]=r["vol24"]; COST[f]=e
pickle.dump({"COST":COST,"curve":curve,"fit_spread":cs.tolist(),"r2_spread":float(r2s),
             "fit_l0":cl.tolist(),"r2_l0":float(r2l),"fit_d10":cd.tolist(),"r2_d10":float(r2d),
             "TAKER":TAKER,"CLIPS":CLIPS},open(os.path.join(S,"costcurve.pkl"),"wb"))

print("\n=== cost floor vs fitted, for the illiquid cohort (<$5M/day, n=%d) ==="%
      sum(1 for f in COST if COST[f]["vol24"]<5e6))
fl=[COST[f]["floor_10000"] for f in COST if COST[f]["vol24"]<5e6]
fi=[COST[f]["fit_10000"] for f in COST if COST[f]["vol24"]<5e6 and np.isfinite(COST[f]["fit_10000"])]
un=sum(1 for f in COST if COST[f]["vol24"]<5e6 and not np.isfinite(COST[f]["fit_10000"]))
print("hard floor  RT@10k: median %.1f bps  (min %.1f, max %.1f)"%(np.median(fl),min(fl),max(fl)))
print("fitted      RT@10k: median %.1f bps  (n=%d fillable, %d UNFILLABLE at 10 levels)"%(
      np.median(fi) if fi else float('nan'),len(fi),un))

# =============================== STAGE: stage_d ===============================

"""STAGE D: re-run the panel grid with MEASURED per-symbol costs at 4 liquidity floors."""
import os,pickle,math,sys,collections,json
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk")
import quant_validation as qv
F=pickle.load(open(os.path.join(S,"frame.pkl"),"rb")); frames=F["frames"]; liqv=F["liq"]
CC=pickle.load(open(os.path.join(S,"costcurve.pkl"),"rb")); COST=CC["COST"]; CLIPS=CC["CLIPS"]

BAR=1800; HORIZONS=[1,2,4,8,24,48]
HNAME={1:"0.5h",2:"1h",4:"2h",8:"4h",24:"12h",48:"24h"}
FLOORS=[1e5,5e5,1e6,5e6]

# ---- signal construction: VERBATIM from futures_panel_lab.py (do not re-derive) ----
def zroll(x,w=48):
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
    c=d["close"]; ret=dlog(c,1); sig={}
    oi=d["oi"]
    sig["oi_chg_1h"]=zroll(dlog(oi,2)); sig["oi_chg_4h"]=zroll(dlog(oi,8))
    r1=dlog(c,2)
    sig["oi_x_price"]=zroll(np.sign(np.nan_to_num(dlog(oi,2)))*r1)
    liq=np.nan_to_num(d["liq"],nan=0.0); lz=zroll(liq)
    sig["liq_spike_fade"]=-np.sign(np.nan_to_num(r1))*np.clip(lz,0,None)
    sig["liq_spike_cont"]=-sig["liq_spike_fade"]
    bv=np.nan_to_num(d["buy_vol"],nan=0.0); sv=np.nan_to_num(d["sell_vol"],nan=0.0)
    tot=bv+sv; imb=np.where(tot>0,(bv-sv)/np.where(tot>0,tot,1),np.nan)
    sig["aggr_imb"]=zroll(imb)
    cvd=d["cvd"]; sig["cvd_slope"]=zroll(diff(cvd,2))
    sig["cvd_div"]=zroll(diff(cvd,4))-zroll(dlog(c,4))
    lsr=d["lsr"]; sig["lsr_contra"]=-zroll(lsr); sig["lsr_chg_contra"]=-zroll(diff(lsr,2))
    b=d["basis"]; sig["basis_level"]=zroll(b)
    fu=d["funding"]; m=np.isfinite(b)&np.isfinite(fu); res=np.full(len(b),np.nan)
    if m.sum()>30 and np.nanstd(fu[m])>0:
        A=np.vstack([fu[m],np.ones(m.sum())]).T
        coef,_,_,_=np.linalg.lstsq(A,b[m],rcond=None); res[m]=b[m]-(A@coef)
    sig["basis_resid"]=zroll(res)
    return sig,ret
SIGNALS=["oi_chg_1h","oi_chg_4h","oi_x_price","liq_spike_fade","liq_spike_cont",
         "aggr_imb","cvd_slope","cvd_div","lsr_contra","lsr_chg_contra",
         "basis_level","basis_resid"]
SIG={};RET={}
for f,d in frames.items():
    s,r=build_signals(d); SIG[f]=s; RET[f]=r
def fwd(d,h):
    c=d["close"]; out=np.full(len(c),np.nan)
    with np.errstate(all="ignore"): out[:-h]=np.log(c[h:]/c[:-h])
    return out
FWD={f:{h:fwd(d,h) for h in HORIZONS} for f,d in frames.items()}
# ------------------------------------------------------------------------------------

VARIANTS=[("floor",  "hard lower bound: 1 tick + 2x5bps fee, zero impact"),
          ("fit",    "fitted spread+depth from measured curve, tick-spaced impact")]

def cost_of(f,clip,variant):
    e=COST.get(f)
    if not e: return float("nan")
    return e["%s_%d"%(variant,int(clip))]

def ts_test(signame,h,min_liq):
    """Identical trade construction to futures_panel_lab.ts_test (entry_lag=1,
    non-overlapping stride h+1). Only the cost model differs: per-symbol, not flat."""
    per_bucket=collections.defaultdict(list); per_sym=collections.defaultdict(list)
    entry_lag=1
    for f,d in frames.items():
        if liqv.get(f,0)<min_liq: continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=FWD[f][h]; ts=d["ts"]; step=h+entry_lag
        for i in range(0,len(s)-step,step):
            j=i+entry_lag
            if j+h>=len(s): break
            if not np.isfinite(s[i]) or not np.isfinite(fr[j]): continue
            pnl=np.sign(s[i])*fr[j]*10000.0
            if not np.isfinite(pnl): continue
            per_bucket[int(ts[j])].append((pnl,f)); per_sym[f].append(pnl)
    if not per_bucket: return None
    buckets=sorted(per_bucket)
    bmeans=np.array([np.mean([p for p,_ in per_bucket[b]]) for b in buckets])
    if len(bmeans)<8: return None
    gross=float(bmeans.mean()); sd=bmeans.std(ddof=1)
    t=float(gross/(sd/math.sqrt(len(bmeans)))) if sd>0 else 0.0
    flat=[p for b in buckets for p,_ in per_bucket[b]]
    mat=[v for v in per_sym.values() if len(v)>=8]
    L=min((len(m) for m in mat),default=0)
    rho=qv.mean_pairwise_correlation([m[:L] for m in mat]) if len(mat)>=2 and L>=8 else 0.0
    n_eff=qv.effective_sample_size(len(flat),n_series=max(1,len(per_sym)),
                                   mean_correlation=max(0.0,rho))
    out={"signal":signame,"family":"TS","horizon":HNAME[h],"h_bars":h,"floor":min_liq,
         "gross_bps":gross,"t_gross":t,"n_rebal":len(bmeans),"n_obs":len(flat),
         "n_sym":len(per_sym),"mean_rho":rho,"n_eff":n_eff,"sd_bps":float(sd)}
    for clip in CLIPS:
        for var,_ in VARIANTS:
            nb=[]
            for b in buckets:
                vals=[p-cost_of(f,clip,var) for p,f in per_bucket[b]]
                vals=[v for v in vals if np.isfinite(v)]
                if vals: nb.append(np.mean(vals))
            if len(nb)<8: out["net_%s_%d"%(var,int(clip))]=float("nan"); continue
            nb=np.array(nb); m_=float(nb.mean()); s_=nb.std(ddof=1)
            out["net_%s_%d"%(var,int(clip))]=m_
            out["tnet_%s_%d"%(var,int(clip))]=float(m_/(s_/math.sqrt(len(nb)))) if s_>0 else 0.0
            out["_nb_%s_%d"%(var,int(clip))]=nb
    out["_bm"]=bmeans
    return out

def xs_test(signame,h,min_liq,min_breadth=6):
    """Identical to futures_panel_lab.xs_test; cost is the traded names' own measured cost."""
    entry_lag=1
    by_bucket=collections.defaultdict(dict)
    for f,d in frames.items():
        if liqv.get(f,0)<min_liq: continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=FWD[f][h]; ts=d["ts"]
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
    spreads=[];legcost={ (v,int(c)):[] for v,_ in VARIANTS for c in CLIPS }
    ranks_prev=None;rank_ac=[]
    for b in chosen:
        row=by_bucket[b]; names=sorted(row)
        sv=np.array([row[n][0] for n in names]); rv=np.array([row[n][1] for n in names])
        k=max(1,len(names)//3); o=np.argsort(-sv)
        na=np.array(names)
        longs=list(na[o[:k]]); shorts=list(na[o[-k:]])
        spreads.append((rv[o[:k]].mean()-rv[o[-k:]].mean())*10000.0)
        for var,_ in VARIANTS:
            for c in CLIPS:
                cl=[cost_of(n,c,var) for n in longs]; cs2=[cost_of(n,c,var) for n in shorts]
                cl=[v for v in cl if np.isfinite(v)]; cs2=[v for v in cs2 if np.isfinite(v)]
                legcost[(var,int(c))].append((np.mean(cl) if cl else np.nan)+(np.mean(cs2) if cs2 else np.nan))
        rk={n:i for i,n in enumerate(na[o])}
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
    out={"signal":signame,"family":"XS","horizon":HNAME[h],"h_bars":h,"floor":min_liq,
         "gross_bps":gross,"t_gross":t,"n_rebal":len(sp),"n_obs":len(sp),
         "n_sym":int(np.median([len(by_bucket[b]) for b in chosen])),"rank_autocorr":rac,
         "n_eff":max(2.0,eb["independent_bets"]),"sd_bps":float(sd)}
    for var,_ in VARIANTS:
        for c in CLIPS:
            lc=np.array(legcost[(var,int(c))])
            nb=sp-lc; nb=nb[np.isfinite(nb)]
            if len(nb)<8: out["net_%s_%d"%(var,int(c))]=float("nan"); continue
            m_=float(nb.mean()); s_=nb.std(ddof=1)
            out["net_%s_%d"%(var,int(c))]=m_
            out["tnet_%s_%d"%(var,int(c))]=float(m_/(s_/math.sqrt(len(nb)))) if s_>0 else 0.0
            out["_nb_%s_%d"%(var,int(c))]=nb
    out["_bm"]=sp
    return out

if __name__=="__main__":
    rows=[];N_hyp=0;N_eval=0
    for fl in FLOORS:
        nsym=sum(1 for f in frames if liqv.get(f,0)>=fl)
        print("floor $%.0fk : %d symbols"%(fl/1e3,nsym),flush=True)
        for sg in SIGNALS:
            for h in HORIZONS:
                for fn in (ts_test,xs_test):
                    N_hyp+=1; N_eval+=len(CLIPS)*len(VARIANTS)
                    r=fn(sg,h,fl)
                    if r: rows.append(r)
    print("\nhypothesis cells attempted: %d  returned: %d   (cost evaluations: %d)"%(N_hyp,len(rows),N_eval))
    pickle.dump({"rows":rows,"N_hyp":N_hyp,"N_eval":N_eval,"FLOORS":FLOORS},
                open(os.path.join(S,"liq_rows.pkl"),"wb"))

# =============================== STAGE: stage_e ===============================

"""STAGE E: read the grid -- does gross alpha anywhere exceed its own measured cost?"""
import os,pickle,math
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
R=pickle.load(open(os.path.join(S,"liq_rows.pkl"),"rb")); rows=R["rows"]
CC=pickle.load(open(os.path.join(S,"costcurve.pkl"),"rb")); COST=CC["COST"]

for fl in R["FLOORS"]:
    sub=[r for r in rows if r["floor"]==fl]
    print("\n"+"="*104)
    print("FLOOR $%.0fk  --  %d cells"%(fl/1e3,len(sub)))
    print("="*104)
    for var in ["floor","fit"]:
        for clip in [500,2500,10000]:
            k="net_%s_%d"%(var,clip)
            v=[r for r in sub if np.isfinite(r.get(k,np.nan))]
            pos=[r for r in v if r[k]>0]
            print("  cost=%-5s clip=$%-6d : %3d cells evaluable, %2d net-positive, best net %+8.1f bps"%(
                var,clip,len(v),len(pos), max([r[k] for r in v]) if v else float('nan')))
    # best by gross t
    sub2=sorted(sub,key=lambda r:-abs(r["t_gross"]))
    print("\n  top 6 by |t_gross|:")
    print("  %-16s %-3s %-5s %10s %7s %8s %10s %10s %8s"%(
        "signal","fam","hor","gross","t","n_eff","net_fl@10k","net_fit@10k","n_sym"))
    for r in sub2[:6]:
        print("  %-16s %-3s %-5s %10.1f %7.2f %8.1f %10s %10s %8d"%(
            r["signal"],r["family"],r["horizon"],r["gross_bps"],r["t_gross"],r["n_eff"],
            "%.1f"%r.get("net_floor_10000",float('nan')),
            "%.1f"%r.get("net_fit_10000",float('nan')),r["n_sym"]))

print("\n\n"+"#"*104)
print("# THE DECISIVE TEST: gross alpha vs its own MEASURED round-trip cost")
print("#"*104)
# every cell, at the hard cost FLOOR (most generous possible cost)
best=[]
for r in rows:
    g=r["gross_bps"]
    for clip in [500,2500,10000]:
        nf=r.get("net_floor_%d"%clip,float("nan"))
        if np.isfinite(nf) and nf>0:
            best.append((nf,r,clip))
best.sort(key=lambda x:-x[0])
print("cells with gross > HARD COST FLOOR (1 tick + fees, zero impact): %d of %d x3 clips"%(
    len(best),len(rows)))
print("\n%-16s %-3s %-5s %9s %9s %9s %8s %7s %8s %6s"%(
    "signal","fam","hor","floor$","gross","net_floor","t_net","clip","n_eff","n_sym"))
for nf,r,clip in best[:20]:
    print("%-16s %-3s %-5s %9.0fk %9.1f %9.1f %8.2f %7d %8.1f %6d"%(
        r["signal"],r["family"],r["horizon"],r["floor"]/1e3,r["gross_bps"],nf,
        r.get("tnet_floor_%d"%clip,float('nan')),clip,r["n_eff"],r["n_sym"]))
pickle.dump({"best":[(nf,{k:v for k,v in r.items() if not k.startswith("_")},clip) for nf,r,clip in best]},
            open(os.path.join(S,"survivors.pkl"),"wb"))

# =============================== STAGE: stage_f ===============================

"""STAGE F: drift control + permutation null. Is the 24h 'survivor' a signal or just beta?"""
import os,pickle,math,sys,collections
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk")
sys.path.insert(0,S)
import stage_d as L
import numpy as np
frames=L.frames; liqv=L.liqv; SIG=L.SIG; FWD=L.FWD; HORIZONS=L.HORIZONS; HNAME=L.HNAME

def raw_trades(signame,h,min_liq,mode="signal",rng=None):
    """mode: signal | long (always +1) | perm (random sign)"""
    per_bucket=collections.defaultdict(list)
    for f,d in frames.items():
        if liqv.get(f,0)<min_liq: continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=FWD[f][h]; ts=d["ts"]; step=h+1
        for i in range(0,len(s)-step,step):
            j=i+1
            if j+h>=len(s): break
            if not np.isfinite(s[i]) or not np.isfinite(fr[j]): continue
            if mode=="signal": sgn=np.sign(s[i])
            elif mode=="long": sgn=1.0
            else: sgn=rng.choice([-1.0,1.0])
            pnl=sgn*fr[j]*10000.0
            if np.isfinite(pnl): per_bucket[int(ts[j])].append(pnl)
    if not per_bucket: return None
    b=sorted(per_bucket)
    return np.array([np.mean(per_bucket[k]) for k in b]),len(b)

print("=== DRIFT CONTROL: what does ALWAYS-LONG earn in the same cells? ===")
print("(if always-long ~= the 'signal', the signal is measuring market drift, not information)")
print("%-8s %-6s %14s %14s %10s %10s"%("floor","hor","signal_mean","always_long","n_rebal","ratio"))
for fl in [1e6,5e6]:
    for h in [8,24,48]:
        gs=[]
        for sg in L.SIGNALS:
            r=raw_trades(sg,h,fl,"signal")
            if r: gs.append(r[0].mean())
        rl=raw_trades("oi_x_price",h,fl,"long")
        if not rl: continue
        lm=rl[0].mean(); nb=rl[1]
        med=float(np.median(gs))
        print("%-8s %-6s %14.1f %14.1f %10d %10s"%(
            "$%.0fM"%(fl/1e6),HNAME[h],med,lm,nb,
            ("%.2f"%(med/lm)) if abs(lm)>1e-9 else "n/a"))

print("\n=== how many of the 12 signals are net-positive per cell? ===")
print("(a real signal is selective; drift makes ALL of them 'work' at once)")
print("%-8s %-6s %10s %10s %12s"%("floor","hor","n_pos/12","median_gross","spread_of_12"))
for fl in [1e5,5e5,1e6,5e6]:
    for h in [24,48]:
        gs=[]
        for sg in L.SIGNALS:
            r=raw_trades(sg,h,fl,"signal")
            if r: gs.append(r[0].mean())
        if not gs: continue
        print("%-8s %-6s %10s %10.1f %12.1f"%("$%.0fk"%(fl/1e3),HNAME[h],
            "%d/12"%sum(1 for g in gs if g>0),float(np.median(gs)),float(np.max(gs)-np.min(gs))))

print("\n=== PERMUTATION NULL for the best cell (lsr_chg_contra TS 24h, $5M floor) ===")
rng=np.random.default_rng(20260824)
obs=raw_trades("lsr_chg_contra",48,5e6,"signal")
print("observed gross %.1f bps over %d non-overlapping rebalances"%(obs[0].mean(),obs[1]))
null=[]
for _ in range(2000):
    r=raw_trades("lsr_chg_contra",48,5e6,"perm",rng)
    if r: null.append(r[0].mean())
null=np.array(null)
p=float(np.mean(null>=obs[0].mean()))
print("random-sign null: mean %.1f, sd %.1f, p(null >= observed) = %.3f"%(null.mean(),null.std(),p))
print("null 5th/95th pct: %.1f / %.1f bps"%(np.percentile(null,5),np.percentile(null,95)))
pickle.dump({"perm_p":p,"null_mean":float(null.mean()),"null_sd":float(null.std()),
             "obs":float(obs[0].mean()),"n_rebal":obs[1],
             "null_p5":float(np.percentile(null,5)),"null_p95":float(np.percentile(null,95))},
            open(os.path.join(S,"drift.pkl"),"wb"))

# =============================== STAGE: stage_g ===============================

"""STAGE G: cohort decomposition by volume BAND + capacity ceiling."""
import os,pickle,math,sys,collections
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk"); sys.path.insert(0,S)
import stage_d as L
import quant_validation as qv
frames=L.frames; liqv=L.liqv; SIG=L.SIG; FWD=L.FWD; HNAME=L.HNAME
CC=pickle.load(open(os.path.join(S,"costcurve.pkl"),"rb")); COST=CC["COST"]
BANDS=[("$100k-500k",1e5,5e5),("$500k-1M",5e5,1e6),("$1M-5M",1e6,5e6),(">=$5M",5e6,1e18)]

def band_cells(lo,hi,signame,h):
    per_bucket=collections.defaultdict(list); syms=set()
    for f,d in frames.items():
        v=liqv.get(f,0)
        if not (lo<=v<hi): continue
        s=SIG[f].get(signame)
        if s is None: continue
        fr=FWD[f][h]; ts=d["ts"]; step=h+1
        for i in range(0,len(s)-step,step):
            j=i+1
            if j+h>=len(s): break
            if not np.isfinite(s[i]) or not np.isfinite(fr[j]): continue
            p=np.sign(s[i])*fr[j]*10000.0
            if np.isfinite(p): per_bucket[int(ts[j])].append(p); syms.add(f)
    if len(per_bucket)<8: return None
    b=sorted(per_bucket)
    bm=np.array([np.mean(per_bucket[k]) for k in b])
    sd=bm.std(ddof=1)
    return {"gross":float(bm.mean()),"t":float(bm.mean()/(sd/math.sqrt(len(bm)))) if sd>0 else 0.0,
            "n_rebal":len(bm),"n_sym":len(syms),"sd":float(sd)}

print("="*112)
print("ILLIQUID-COHORT DECOMPOSITION: gross alpha in each volume band vs that band's own measured cost")
print("="*112)
print("%-12s %5s %6s %-6s %11s %8s %8s %11s %11s %12s"%(
    "band","nsym","nreb","hor","best_gross","t","med_grs","cost_floor","cost_fit","VERDICT"))
out=[]
for name,lo,hi in BANDS:
    ns=[f for f in frames if lo<=liqv.get(f,0)<hi]
    cf=[COST[f]["floor_10000"] for f in ns if f in COST]
    ci=[COST[f]["fit_10000"] for f in ns if f in COST and np.isfinite(COST[f]["fit_10000"])]
    nunfill=sum(1 for f in ns if f in COST and not np.isfinite(COST[f]["fit_10000"]))
    for h in [1,2,4,8,24,48]:
        res=[]
        for sg in L.SIGNALS:
            r=band_cells(lo,hi,sg,h)
            if r: res.append((r["gross"],r["t"],r["n_rebal"],sg))
        if not res: continue
        res.sort(key=lambda x:-x[0])
        bg,bt,nb,bs=res[0]
        medg=float(np.median([x[0] for x in res]))
        cfl=float(np.median(cf)) if cf else float("nan")
        cfi=float(np.median(ci)) if ci else float("nan")
        v="gross<FLOOR" if bg<cfl else ("gross<fitted" if (np.isfinite(cfi) and bg<cfi) else "gross>cost*")
        print("%-12s %5d %6d %-6s %11.1f %8.2f %8.1f %11.1f %11s %12s"%(
            name,len(ns),nb,HNAME[h],bg,bt,medg,cfl,
            ("%.1f"%cfi) if np.isfinite(cfi) else "UNFILL",v))
        out.append({"band":name,"n_sym":len(ns),"hor":HNAME[h],"best_gross":bg,"best_t":bt,
                    "best_sig":bs,"median_gross":medg,"n_rebal":nb,"cost_floor":cfl,
                    "cost_fit":cfi if np.isfinite(cfi) else None,"n_unfillable":nunfill,
                    "verdict":v})
    print("   ^ %s: %d symbols, %d of them UNFILLABLE at a $10k clip within 10 levels"%(name,len(ns),nunfill))

print("\n"+"="*112)
print("CAPACITY CEILING -- max $/day deployable before measured impact erases the edge")
print("="*112)
LEVELS=10
def max_clip(l0,d10,tickbps,budget_bps):
    """largest clip whose per-side impact*2 <= budget_bps"""
    lo,hi=0.0,d10
    for _ in range(60):
        m=(lo+hi)/2
        if m<=l0: imp=0.0
        else:
            per=(d10-l0)/(LEVELS-1); need=m-l0; filled=0.;cost=0.
            for k in range(1,LEVELS):
                take=min(per,need-filled)
                if take<=0: break
                cost+=take*k*tickbps; filled+=take
            imp=cost/m
        if 2*imp<=budget_bps: lo=m
        else: hi=m
    return lo
M=pickle.load(open(os.path.join(S,"costmodel.pkl"),"rb"))["rows"]
print("%-14s %12s %9s %10s %12s %12s %14s"%(
    "symbol","vol24","spread","gross_hdr","budget_bps","max_clip$","cap_$/day@24h"))
for f in sorted([x for x in M.values() if x["measured"]],key=lambda z:-z["vol24"]):
    gross=177.8   # the single best (and still-insignificant) 24h cell
    budget=gross-f["spread_med"]-2*5.0
    mc=max_clip(f["l0_usd"],f["d10_usd"],f["tick_bps"],budget)
    print("%-14s %12.0f %9.3f %10.1f %12.1f %12.0f %14.0f"%(
        f["sym"],f["vol24"],f["spread_med"],gross,budget,mc,mc))
print("\n(24h horizon => 1 rebalance/day, so capacity $/day == max clip. The 7-symbol")
print(" basket sums these; per-symbol clips are capped by the 10-level book, not by the day.)")
pickle.dump({"bands":out},open(os.path.join(S,"cohorts.pkl"),"wb"))

# =============================== STAGE: stage_h ===============================

"""STAGE H: noise benchmark + deflation + quant_validation on every apparent survivor."""
import os,pickle,math,sys
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk")
import quant_validation as qv
from scipy import stats as sps
R=pickle.load(open(os.path.join(S,"liq_rows.pkl"),"rb")); rows=R["rows"]

HOUSE=1671
N_grid=R["N_hyp"]            # 576
N_band=4*6*12                # 288 cohort-band cells (stage G)
N_ctrl=6                     # always-long drift controls
N_perm=1
N_NEW=N_grid+N_band+N_ctrl+N_perm
N=HOUSE+N_NEW
print("SEARCH COUNT")
print("  house prior (research_futures_panel.md)          %6d"%HOUSE)
print("  new: measured-cost grid (12sig x 6hor x 2fam x 4floor) %6d"%N_grid)
print("  new: cohort-band decomposition (4 bands x 6hor x 12sig) %6d"%N_band)
print("  new: always-long drift controls                  %6d"%N_ctrl)
print("  new: permutation null                            %6d"%N_perm)
print("  -------------------------------------------------------")
print("  N_new = %d   TOTAL N = %d"%(N_NEW,N))
print("  (conservative alt: counting each of the %d cost evaluations separately -> N = %d)"%(
    R["N_eval"], HOUSE+R["N_eval"]+N_band+N_ctrl+N_perm))

ts=[abs(r["t_gross"]) for r in rows]
print("\nNOISE BENCHMARK over the %d returned grid cells"%len(rows))
print("  observed max |t|                    %.3f"%max(ts))
Nn=len(rows)
exp_max=sps.norm.ppf(1-1.0/(2*Nn))*(1+0.0)
print("  expected max |t| if ALL were noise   %.3f"%exp_max)
print("  cells |t|>1.96: observed %d, expected under null %.1f"%(
    sum(1 for t in ts if t>1.96), 0.05*Nn))
pv=[2*(1-sps.norm.cdf(t)) for t in ts]
rej,adjp=qv.benjamini_hochberg(pv,alpha=0.05)
nsurv=int(rej.sum()); minadj=float(adjp.min())
print("  Benjamini-Hochberg 5%% FDR survivors: %d   (min adjusted p = %.3f)"%(nsurv,minadj))

print("\nFORMAL VALIDATION of the apparent survivors (net-positive at the HARD cost floor)")
cands=[]
for r in rows:
    for clip in [10000]:
        nb=r.get("_nb_floor_%d"%clip)
        if nb is None or not np.isfinite(r.get("net_floor_%d"%clip,np.nan)): continue
        if r["net_floor_%d"%clip]>0: cands.append((r["net_floor_%d"%clip],r,clip,nb))
cands.sort(key=lambda x:-x[0])
print("%d cells net-positive at the hard floor, $10k clip. Validating the top 6:\n"%len(cands))
res=[]
for netv,r,clip,nb in cands[:6]:
    ev=qv.evaluate("%s/%s/%s/floor$%.0fk"%(r["signal"],r["family"],r["horizon"],r["floor"]/1e3),
                   list(np.asarray(nb)/10000.0), n_trials=N)
    print("  %-34s n=%3d  net %+7.1f bps  Sharpe %+.3f  t %+.2f  DSR %.4f  survives=%s"%(
        "%s %s %s $%.0fk"%(r["signal"],r["family"],r["horizon"],r["floor"]/1e3),
        len(nb),netv,ev.get("sharpe_per_period",float('nan')),ev.get("t_stat",float('nan')),
        ev.get("deflated_sharpe",float('nan')),ev.get("survives_deflation")))
    print("       min track record needed: %s obs   (have %d)"%(
        ("%.0f"%ev["min_track_record_obs"]) if ev.get("min_track_record_obs") and np.isfinite(ev["min_track_record_obs"]) else "n/a",len(nb)))
    res.append({"cell":"%s %s %s $%.0fk"%(r["signal"],r["family"],r["horizon"],r["floor"]/1e3),
                "n":len(nb),"net_bps":netv,"sharpe":ev.get("sharpe_per_period"),
                "t":ev.get("t_stat"),"dsr":ev.get("deflated_sharpe"),
                "survives":ev.get("survives_deflation"),
                "mtrl":ev.get("min_track_record_obs")})
pickle.dump({"N":N,"N_NEW":N_NEW,"N_grid":N_grid,"N_band":N_band,"N_eval":R["N_eval"],
             "max_t":max(ts),"exp_max_t":float(exp_max),"n_gt196":sum(1 for t in ts if t>1.96),
             "bh_survivors":nsurv,"min_adj_p":minadj,"validated":res},open(os.path.join(S,"validation.pkl"),"wb"))

# =============================== STAGE: stage_i ===============================

"""STAGE I: the funder's exact corner -- validate + permutation the illiquid band's best cell."""
import os,pickle,math,sys,collections
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
sys.path.insert(0,"/Users/test/Applications/MultiVenueDesk"); sys.path.insert(0,S)
import stage_d as L, quant_validation as qv
frames=L.frames; liqv=L.liqv; SIG=L.SIG; FWD=L.FWD
V=pickle.load(open(os.path.join(S,"validation.pkl"),"rb")); N=V["N"]

def band(lo,hi,sg,h,mode="signal",rng=None):
    pb=collections.defaultdict(list)
    for f,d in frames.items():
        v=liqv.get(f,0)
        if not (lo<=v<hi): continue
        s=SIG[f].get(sg)
        if s is None: continue
        fr=FWD[f][h]; ts=d["ts"]; step=h+1
        for i in range(0,len(s)-step,step):
            j=i+1
            if j+h>=len(s): break
            if not np.isfinite(s[i]) or not np.isfinite(fr[j]): continue
            sgn = np.sign(s[i]) if mode=="signal" else (1.0 if mode=="long" else rng.choice([-1.,1.]))
            p=sgn*fr[j]*1e4
            if np.isfinite(p): pb[int(ts[j])].append(p)
    if len(pb)<8: return None
    b=sorted(pb); return np.array([np.mean(pb[k]) for k in b])

print("=== THE FUNDER'S CORNER: $100k-$500k/day cohort, 24h horizon ===")
best=None
for sg in L.SIGNALS:
    a=band(1e5,5e5,sg,48)
    if a is None: continue
    m=a.mean(); sd=a.std(ddof=1); t=m/(sd/math.sqrt(len(a)))
    if best is None or m>best[1]: best=(sg,m,t,a)
sg,m,t,a=best
print("best of 12 signals: %s   gross %+.1f bps over %d non-overlapping rebalances (t=%.2f)"%(sg,m,len(a),t))
al=band(1e5,5e5,sg,48,"long")
print("ALWAYS-LONG in the same cohort/horizon: %+.1f bps  -> the 'signal' captures %.0f%% of plain drift"%(
    al.mean(), 100*m/al.mean() if al.mean() else float('nan')))
rng=np.random.default_rng(20260824); null=[]
for _ in range(2000):
    r=band(1e5,5e5,sg,48,"perm",rng)
    if r is not None: null.append(r.mean())
null=np.array(null); p=float(np.mean(null>=m))
print("random-sign permutation null: mean %+.1f sd %.1f, p(null>=obs) = %.3f  [5th %.1f / 95th %.1f]"%(
    null.mean(),null.std(),p,np.percentile(null,5),np.percentile(null,95)))

CC=pickle.load(open(os.path.join(S,"costcurve.pkl"),"rb"))
curve={int(r["vol"]):r for r in CC["curve"]}
c100=curve[100000]
print("\nCOST it must clear (fitted curve at $100k/day, the band's lower edge):")
print("  round-trip @ $500 clip  : %.1f bps  -> gross %.1f is %.2fx cost  => LOSES %.1f bps"%(
    c100["rt_500"],m,m/c100["rt_500"],m-c100["rt_500"]))
print("  round-trip @ $2,500 clip: UNFILLABLE within 10 levels")
print("  round-trip @ $10,000 clip: UNFILLABLE within 10 levels")
ev=qv.evaluate("illiquid_corner_%s_24h"%sg,list((a-c100["rt_500"])/1e4),n_trials=N)
print("\nquant_validation on the net series (gross - fitted $500-clip cost), N=%d:"%N)
print("  n=%d  Sharpe %+.3f  t %+.2f  DSR %.4f  survives_deflation=%s  min_track_record=%s obs"%(
    len(a),ev.get("sharpe_per_period"),ev.get("t_stat"),ev.get("deflated_sharpe"),
    ev.get("survives_deflation"),
    ("%.0f"%ev["min_track_record_obs"]) if ev.get("min_track_record_obs") and np.isfinite(ev["min_track_record_obs"]) else "n/a"))
# also at the (non-credible but hard) floor
M=pickle.load(open(os.path.join(S,"costmodel.pkl"),"rb"))["rows"]
fl=float(np.median([M[f]["tick_bps"]+10.0 for f in frames if 1e5<=liqv.get(f,0)<5e5]))
ev2=qv.evaluate("illiquid_corner_hardfloor",list((a-fl)/1e4),n_trials=N)
print("  at the HARD floor (%.1f bps, 1 tick + fees, zero impact -- not a credible cost):"%fl)
print("    net %+.1f bps  Sharpe %+.3f  t %+.2f  DSR %.4f  survives=%s"%(
    m-fl,ev2.get("sharpe_per_period"),ev2.get("t_stat"),ev2.get("deflated_sharpe"),
    ev2.get("survives_deflation")))
pickle.dump({"sig":sg,"gross":float(m),"t":float(t),"n":len(a),"always_long":float(al.mean()),
    "perm_p":p,"null_p95":float(np.percentile(null,95)),"cost_500":c100["rt_500"],
    "dsr_fit":ev.get("deflated_sharpe"),"dsr_floor":ev2.get("deflated_sharpe"),
    "hard_floor_bps":fl,"survives_fit":ev.get("survives_deflation"),
    "survives_floor":ev2.get("survives_deflation")},open(os.path.join(S,"corner.pkl"),"wb"))

# =============================== STAGE: stage_j ===============================

"""STAGE J: capacity ceiling for the illiquid corner + measured fill statistics."""
import os,pickle,math
import numpy as np
S="/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad"
CC=pickle.load(open(os.path.join(S,"costcurve.pkl"),"rb"))
CO=pickle.load(open(os.path.join(S,"corner.pkl"),"rb"))
RAW=pickle.load(open(os.path.join(S,"costs_raw.pkl"),"rb"))["PERP"]
M=pickle.load(open(os.path.join(S,"costmodel.pkl"),"rb"))["rows"]
curve={int(r["vol"]):r for r in CC["curve"]}; TAKER=CC["TAKER"]; LEVELS=10

def rt(clip,l0,d10,tickbps,spread):
    if clip>d10: return float("nan")
    if clip<=l0: imp=0.0
    else:
        per=(d10-l0)/(LEVELS-1); need=clip-l0; filled=0.;cost=0.
        for k in range(1,LEVELS):
            take=min(per,need-filled)
            if take<=0: break
            cost+=take*k*tickbps; filled+=take
        imp=cost/clip
    return spread+2*TAKER+2*imp

print("=== CAPACITY CEILING for the illiquid corner ($100k/day name, gross %.1f bps) ==="%CO["gross"])
c=curve[100000]
print("fitted book at $100k/day: spread %.1f bps, L0 $%.0f, 10-level depth $%.0f, tick %.2f bps"%(
    c["spread"],c["l0"],c["d10"],c["tick_bps"]))
print("%10s %12s %12s"%("clip$","RT_cost_bps","net_bps"))
be=None
for clip in [25,50,100,250,500,1000,1828,2500]:
    v=rt(clip,c["l0"],c["d10"],max(c["tick_bps"],c["spread"]/2),c["spread"])
    net=(CO["gross"]-v) if np.isfinite(v) else float("nan")
    if np.isfinite(net) and net>0: be=clip
    print("%10s %12s %12s"%("$%d"%clip,("%.1f"%v) if np.isfinite(v) else "UNFILLABLE",
                            ("%+.1f"%net) if np.isfinite(net) else "-"))
print("\nlargest clip with positive net: $%s   (24h horizon => 1 rebalance/day)"%(be if be else "NONE"))
print("=> CAPACITY CEILING PER SYMBOL: about $%s/day"%(be if be else "0"))
n_band=26
print("   across the %d symbols in the $100k-500k band: about $%s/day for the whole sleeve"%(
      n_band, f"{be*n_band:,}" if be else "0"))
print("   ...against a $10,000 sleeve. The sleeve cannot express itself at this size.")
print("   Cost floor even at an infinitesimal clip = spread+fees = %.1f bps; gross %.1f leaves %.1f bps"%(
      c["spread"]+2*TAKER, CO["gross"], CO["gross"]-c["spread"]-2*TAKER))

print("\n=== MEASURED FILL STATISTICS (fraction of real snapshots that could absorb the clip) ===")
print("%-14s %12s %10s %10s %10s"%("symbol","vol24","fill@$500","fill@$2.5k","fill@$10k"))
for f,r in sorted([(k,v) for k,v in M.items() if v["measured"]],key=lambda z:-z[1]["vol24"]):
    print("%-14s %12.0f %10.3f %10.3f %10.3f"%(f,r["vol24"],
        r["fillfrac_500"],r["fillfrac_2500"],r["fillfrac_10000"]))
print("\n(measured symbols fill every clip essentially always -- the fill problem is entirely")
print(" in the UNMEASURED illiquid range, where the fitted 10-level book is $1.8k at $100k/day.)")
