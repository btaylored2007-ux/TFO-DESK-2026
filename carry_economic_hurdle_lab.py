#!/usr/bin/env python3
"""Bounded PAPER research; no live ledger writes, network, ticks or deployment.

The 2026-09-10 manifest fixes two economic-hurdle hypotheses plus two controls.
Cached history is a diagnostic, NOT fresh validation. Missing full trade economics,
genuine holdout or independent-mechanism status always refuses deployment.
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import quant_validation as qv

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "reports/carry_economic_hurdle_preregistration_2026-09-10.json"
SYMBOLS = ("PF_XBTUSD", "PF_ETHUSD")


def atomic_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as f:
        json.dump(safe(value),f,indent=2,allow_nan=False)
        f.write('\n')
        pending=Path(f.name)
    os.replace(pending,path)


def merge_settlements(old, new):
    """Append-only settlement history. A source revision is a refusal, not overwrite."""
    if new.get('result')!='success' or not new.get('rates'):
        raise ValueError('new settled funding response unsuccessful/empty')
    rows={r['timestamp']:r for r in old.get('rates',[])}
    if len(rows)!=len(old.get('rates',[])):
        raise ValueError('existing duplicate funding timestamps')
    seen=set()
    added=0
    for r in new['rates']:
        key=r['timestamp']
        if key in seen: raise ValueError('new duplicate funding timestamps')
        seen.add(key)
        rate=float(r['relativeFundingRate'])
        if not math.isfinite(rate): raise ValueError('non-finite collected funding')
        dt=datetime.fromisoformat(key.replace('Z','+00:00'))
        if dt.tzinfo is None or dt.minute or dt.second or dt.microsecond:
            raise ValueError('settlement not exact UTC hour')
        if key in rows:
            if not math.isclose(float(rows[key]['relativeFundingRate']),rate,rel_tol=1e-12,abs_tol=1e-16):
                raise ValueError('historical funding revision at '+key)
        else:
            rows[key]=r;added+=1
    return {'result':'success','serverTime':new.get('serverTime'),'rates':[rows[k] for k in sorted(rows)]},added


def collect_settlements(cache, force=False):
    """Two unauthenticated public GETs at most once20h. Never touch original cache."""
    cache=Path(cache).resolve()
    if cache==(ROOT/'reports/carry_funding_cache').resolve():
        raise ValueError('collector refuses to write the original research cache')
    cache.mkdir(parents=True,exist_ok=True)
    state_path=cache/'collection_state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    now=time.time()
    if not force and now-state.get('attempt_epoch',0)<20*3600:
        return {'status':'SKIP','reason':'settlement collection attempted within20h','prior':state}
    staged=[]
    result={'attempt_epoch':now,'checked_utc':datetime.now(timezone.utc).isoformat(),'paper_only':True,'sources':[]}
    try:
        for symbol in SYMBOLS:
            target=cache/(symbol+'.json')
            old_path=target if target.exists() else ROOT/'reports/carry_funding_cache'/(symbol+'.json')
            old=json.loads(old_path.read_text())
            url='https://futures.kraken.com/derivatives/api/v4/historicalfundingrates?symbol='+symbol
            req=urllib.request.Request(url,headers={'User-Agent':'TFO-PAPER-research-settlement-collector/1.0'})
            with urllib.request.urlopen(req,timeout=15) as response:
                incoming=json.load(response)
            merged,added=merge_settlements(old,incoming)
            staged.append((target,merged))
            result['sources'].append({'url':url,'added_settlements':added,'total_settlements':len(merged['rates'])})
        # Validate both responses before publishing either. The analysis itself aligns hours.
        for target,merged in staged: atomic_json(target,merged)
        result['status']='OK'
        atomic_json(state_path,result)
        return result
    except Exception as exc:
        result.update({'status':'REFUSED','reason':type(exc).__name__+': '+str(exc)[:300]})
        atomic_json(state_path,result)
        raise


def input_signature(cache):
    spec=json.loads(SPEC.read_text())
    return {'manifest_sha256':sha(SPEC),'script_sha256':sha(__file__),
        'settlements_sha256':{s:hashlib.sha256(json.dumps(json.loads((Path(cache)/(s+'.json')).read_text())['rates'],sort_keys=True,separators=(',',':')).encode()).hexdigest() for s in SYMBOLS},
        'registered_configurations':spec['finite_configurations']}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_hourly(path):
    obj = json.loads(Path(path).read_text())
    if obj.get("result") != "success" or not obj.get("rates"):
        raise ValueError("funding response absent/unsuccessful")
    values = {}
    for row in obj["rates"]:
        dt = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.minute or dt.second or dt.microsecond:
            raise ValueError("funding timestamp is not an exact UTC hour")
        stamp = int(dt.timestamp())
        rate = float(row["relativeFundingRate"])
        if not math.isfinite(rate):
            raise ValueError("non-finite settled funding")
        if stamp in values:
            raise ValueError("duplicate settlement timestamp")
        values[stamp] = rate
    return values


def joint_complete_block(series):
    common = sorted(set.intersection(*(set(s) for s in series)))
    by_day = {}
    for stamp in common:
        by_day.setdefault(stamp // 86400, []).append(stamp)
    full = sorted(d for d, rows in by_day.items()
                  if rows == list(range(d * 86400, (d + 1) * 86400, 3600)))
    blocks = []
    for day in full:
        if not blocks or day != blocks[-1][-1] + 1:
            blocks.append([])
        blocks[-1].append(day)
    if not blocks:
        raise ValueError("no complete joint UTC days")
    chosen = max(blocks, key=len)
    hours = list(range(chosen[0] * 86400, (chosen[-1] + 1) * 86400, 3600))
    data = np.array([[s[t] for s in series] for t in hours])
    return hours, data, {"joint_hours": len(common), "complete_days": len(full),
                         "contiguous_blocks_days": [len(b) for b in blocks],
                         "chosen_days": len(chosen), "excluded_complete_days": len(full)-len(chosen)}


def simulate(funding, policy, delay_hours, rt_bps, spec):
    """At hour i act on settled data no later than i-delay; earn funding i.

    Constant capital/notional model; fee at each entry/exit plus terminal unwind.
    Funding-neutral long spot/short perp is assumed; basis and liquidation are
    missing and explicitly prevent deployment. Cash benchmark charged on tied
    capital, with conservative33% perp collateral reserve additional to spot.
    """
    n = len(funding)
    delay = int(delay_hours)
    if delay < 1:
        raise ValueError("settlement at signal time cannot be captured")
    p = spec["policy"]
    warm = p["funding_mean_days"] * 24 + max(spec["execution_delay_hours"])
    if n <= warm + 48:
        raise ValueError("insufficient complete history after signal warmup")
    cumul = np.vstack([np.zeros(2), np.cumsum(funding, axis=0)])
    held = np.zeros(2)
    gross = np.zeros(n)
    net = np.zeros(n)
    deployed = np.zeros(n)
    weights = np.zeros((n, 2))
    cycle_start = None
    cycles = []
    entries = exits = 0
    side_cost = rt_bps / 20000
    cash_hourly = p["cash_annual_pct"] / 100 / 8760
    hurdle = p["cash_annual_pct"] + rt_bps / 100 * 365 / p["economic_cost_amortisation_days"]
    for i in range(warm, n):
        j = i - delay
        instantaneous = funding[j] * 8760 * 100
        mean30 = (cumul[j+1] - cumul[j+1-p["funding_mean_days"]*24]) / (p["funding_mean_days"]*24) * 8760 * 100
        mean7 = float((cumul[j+1] - cumul[j+1-p["exit_mean_days"]*24]).mean() / (p["exit_mean_days"]*24) * 8760 * 100)
        cost = 0.0
        if policy != "buy_hold_control" and held.sum() > 0 and mean7 < p["exit_basket_annual_pct"]:
            cost += held.sum()*side_cost
            exits += int(np.count_nonzero(held))
            cycles.append({"start": cycle_start, "end": i, "complete": True})
            cycle_start = None
            held[:] = 0
        if policy == "buy_hold_control":
            if i == warm:
                held[:] = .5
                entries += 2
                cost += side_cost
                cycle_start = i
        elif float(instantaneous.mean()) >= p["entry_basket_annual_pct"]:
            eligible = (instantaneous >= p["per_asset_min_annual_pct"]) & (held == 0)
            if policy.startswith("economic_"):
                eligible &= mean30 > hurdle
                if policy == "economic_btc":
                    eligible[1] = False
                allocation = max(0.0, 1.0-float(held.sum()))
            else:
                # Literal published missing-name sizing; intentionally report bloat.
                allocation = 1.0
            count = int(eligible.sum())
            if count and allocation > 1e-12:
                if not np.any(held):
                    cycle_start = i
                held[eligible] = allocation/count
                entries += count
                cost += allocation*side_cost
        weights[i] = held
        deployed[i] = held.sum()
        funding_income = float(np.dot(held, funding[i]))
        # 1 capital unit = 0.75 spot / 0.25 perp cash collateral; no double-counted cash yield.
        tied = min(1.0, .75*held.sum()*4/3)
        gross[i] = .75*funding_income + (1.0-tied)*cash_hourly
        net[i] = gross[i]-.75*cost
    if np.any(held):
        net[-1] -= .75*held.sum()*side_cost
        exits += int(np.count_nonzero(held))
        cycles.append({"start": cycle_start, "end": n-1, "complete": False})
    sl = slice(warm, None)
    return {"net": net[sl], "gross": gross[sl], "deployed": deployed[sl],
            "weights": weights[sl], "cash": np.full(n-warm, cash_hourly),
            "entries": entries, "exits_including_terminal": exits, "cycles": cycles,
            "economic_hurdle_pct": hurdle, "warmup_hours": warm,
            "max_spot_notional_per_capital": float(.75*deployed.max()),
            "max_funding_notional_vs_budget": float(deployed.max())}


def daily(hours, returns):
    result = {}
    for t, value in zip(hours, returns):
        result.setdefault(t//86400, []).append(float(value))
    return {d: sum(v) for d,v in result.items() if len(v)==24}


def neff_and_cycles(returns, deployed, cycles):
    r = np.asarray(returns)
    rho = float(np.corrcoef(r[:-1], r[1:])[0,1]) if np.std(r)>1e-16 else 0.0
    rho = max(0.0, rho) if math.isfinite(rho) else 0.0
    ar_n = max(2.0, len(r)*(1-rho)/(1+rho))
    # A funding episode is the repeated opportunity; days held do not invent cycles.
    complete = sum(c["complete"] for c in cycles)
    independent = qv.effective_bets(complete, rho)
    effective = max(2.0, min(ar_n, independent["independent_bets"]))
    return effective, {"daily_ar1": rho, "ar1_effective_days": ar_n,
                       "completed_cycles": complete, "effective_cycles": independent,
                       "harness_floor_warning": "Harness requires >=2; zero/one completed cycle NEVER passes independent-cycle gate."}


def spy_returns(path):
    values = {}
    with Path(path).open() as f:
        for row in csv.DictReader(f):
            if row.get("SPY"):
                stamp = int(datetime.fromisoformat(row["Date"]).replace(tzinfo=timezone.utc).timestamp())//86400
                values[stamp] = float(row["SPY"])
    dates = sorted(values)
    return {dates[i]:values[dates[i]]/values[dates[i-1]]-1 for i in range(1,len(dates))}


def safe(obj):
    if isinstance(obj, dict):
        return {k:safe(v) for k,v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def run(cache, out):
    started = datetime.now(timezone.utc)
    spec = json.loads(SPEC.read_text())
    sources = [cache/(s+".json") for s in SYMBOLS]
    hours, funding, coverage = joint_complete_block([load_hourly(f) for f in sources])
    sr = spy_returns(ROOT/"reports/rebalance_premium_prices.csv")
    configured = []
    for policy in spec["policies"]:
        for cost in spec["roundtrip_bps"]:
            for delay in spec["execution_delay_hours"]:
                sim = simulate(funding, policy, delay, cost, spec)
                net = daily(hours[sim["warmup_hours"]:], sim["net"])
                gross = daily(hours[sim["warmup_hours"]:], sim["gross"])
                active = daily(hours[sim["warmup_hours"]:], sim["deployed"])
                days = sorted(net)
                r = np.array([net[d] for d in days])
                g = np.array([gross[d] for d in days])
                cash = np.full(len(r), spec["policy"]["cash_annual_pct"]/36500)
                excess = r-cash
                effective, effective_detail = neff_and_cycles(excess, sim["deployed"], sim["cycles"])
                ev = qv.evaluate(f"{policy}_c{cost:g}_delay{delay}h", excess,
                    n_trials=spec["search_lineage"]["total_charged"], periods_per_year=365,
                    trades_per_period=max(1,sim["entries"])/len(r), gross_returns=g-cash,
                    n_effective=effective)
                unadjusted = qv.evaluate("no_search_penalty_sensitivity",excess,n_trials=1,
                    periods_per_year=365,n_effective=effective)
                mid = int(len(r)*.7)
                folds = []
                for tr,te in qv.purged_kfold_splits(len(r),5,.05,30):
                    folds.append({"n_train":len(tr),"n_test":len(te),
                        "test_cash_excess_ann_pct":float(excess[te].mean()*36500),
                        "train_max_index":int(max(tr)),"test_start":int(min(te)),"test_end":int(max(te)),
                        "fitted_parameters":0})
                aligned = [d for d in days if d in sr]
                active_days=[d for d in days if active[d]>0]
                active_aligned=[d for d in active_days if d in sr]
                spy = np.array([sr[d] for d in aligned])
                aligned_r = np.array([net[d] for d in aligned])
                beta = float(np.cov(aligned_r,spy,ddof=1)[0,1]/np.var(spy,ddof=1)) if len(active_aligned)>=20 and len(active_aligned)>=.8*len(active_days) and np.var(spy)>0 else None
                configured.append({"name":ev["name"],"policy":policy,"roundtrip_bps":cost,"delay_hours":delay,
                    "evaluation":ev,"no_search_penalty_sensitivity":unadjusted,"effective_evidence":effective_detail,
                    "beta_to_SPY":beta,"beta_observations":len(aligned),"mean_net_ann_pct":float(r.mean()*36500),
                    "beta_active_days":len(active_days),"beta_active_days_with_spy":len(active_aligned),
                    "mean_excess_cash_ann_pct":float(excess.mean()*36500),
                    "historical_first70_excess_ann_pct":float(excess[:mid].mean()*36500),
                    "historical_last30_excess_ann_pct":float(excess[mid:].mean()*36500),
                    "historical_split_not_pristine_holdout":True,"purged_embargoed_folds":folds,
                    "max_funding_notional_vs_budget":sim["max_funding_notional_vs_budget"],
                    "entries":sim["entries"],"complete_cycles":effective_detail["completed_cycles"],
                    "cash_hurdle_pct":sim["economic_hurdle_pct"],"_series":excess.tolist(),"_days":days})
    wrc = qv.whites_reality_check([c["_series"] for c in configured],n_boot=500,rng=np.random.default_rng(9102026))
    liquidity = json.loads((ROOT/"reports/carry_monitor.json").read_text())
    vols = {a["base"]:a.get("vol24h_usd",0) for a in liquidity.get("assets",[])}
    liquidity_diagnostic=qv.liquidity_screen(
        (.75*funding.mean(axis=0)*24-spec['policy']['cash_annual_pct']/36500)*10000,
        [vols.get('BTC',0),vols.get('ETH',0)],floors=[0,1e6,5e6,1e8])
    candidate_rows = []
    for policy in ("economic_btc","economic_basket"):
        cases = [c for c in configured if c["policy"]==policy]
        primary = next(c for c in cases if c["roundtrip_bps"]==90 and c["delay_hours"]==1)
        profile = qv.decay_profile({(c["delay_hours"]-1)*3600:c['evaluation']['mean_return_per_period']*10000 for c in cases if c["roundtrip_bps"]==90})
        if ev_base:=primary['evaluation']['mean_return_per_period']:
            profile['interpretation']='First delay is5h beyond next-hour baseline, not a1-second measurement. Negative baseline is a refusal, not capturable alpha.'
            if ev_base<0:
                profile['verdict']='REFUSED: baseline and delayed cash-relative effects are negative; no positive edge to retain'
        else:
            profile.update({'measurable':False,'fragile':None,'verdict':'UNMEASURABLE: zero cash-relative baseline; no capturable signal to decay'})
        prospective = sum(d*86400 >= datetime.fromisoformat(spec["registered_utc"].replace("Z","+00:00")).timestamp() for d in primary["_days"])
        reasons = ["same carry mechanism already registered: not independently deployable",
                   "full basis/FX/margin/paired-fill return model unavailable",
                   "historical time-aligned liquidity and current executable fee evidence unavailable"]
        if prospective < spec["validation"]["prospective_holdout_min_days"]:
            reasons.append(f"only {prospective} prospective days; requires60 after registration")
        if primary["complete_cycles"] <20:
            reasons.append(f"only {primary['complete_cycles']} completed funding cycles; requires20")
        ev = primary["evaluation"]
        if not ev["survives_deflation"]: reasons.append("does not survive deflation")
        if primary['beta_to_SPY'] is None: reasons.append('cannot estimate SPY beta over sufficient ACTIVE days; missing prices/cash-only interval is not zero beta')
        if ev["t_stat"] <=2: reasons.append("positive cash-excess t does not exceed2")
        if primary["historical_first70_excess_ann_pct"]<=0 or primary["historical_last30_excess_ann_pct"]<=0: reasons.append("decays/fails positive cash excess in historical halves")
        if sum(f["test_cash_excess_ann_pct"]>0 for f in primary["purged_embargoed_folds"])<4: reasons.append("fewer than4/5 purged folds positive")
        if not wrc["survives"]: reasons.append("family White reality check fails")
        candidate_rows.append({"mechanism":policy,"candidate_status":"REFUSED","eligible_for_autonomous_deployment":False,
             "primary_evaluation":ev,"beta_to_SPY":primary["beta_to_SPY"],"prospective_days":prospective,
             "execution_delay_profile":profile,"current_liquidity_usd":vols,"refusal_reasons":reasons})
    for c in configured:
        c.pop("_series"); c.pop("_days")
    receipt = {"study":"carry_economic_hurdle","paper_only":True,"research_only":True,
        "started_utc":started.isoformat(),"completed_utc":datetime.now(timezone.utc).isoformat(),
        "registration_sha256":sha(SPEC),"script_sha256":sha(__file__),"manifest":spec,
        "input_sha256":{str(p):sha(p) for p in sources},"coverage":coverage,
        "live_source_hashes_at_run":{name:sha(ROOT/name) for name in ['carry_paper_desk.py','carry_monitor.py']},
        "control_amendment_note":"Current-rule control is the pre-amendment observed32%/2% missing-name sizing specified before results. Root may now have applied16% perasset/remainder-cap amendment. This historical control is NOT relabelled as amended live implementation.",
        "history_start_utc":datetime.fromtimestamp(hours[0],timezone.utc).isoformat(),
        "history_end_utc":datetime.fromtimestamp(hours[-1],timezone.utc).isoformat(),
        "cash_and_collateral_convention":"75% spot/funding notional +25% perp collateral; all deployed capital forgoes4% cash, idle cash earns4%; additional venue risks omitted and are hard refusals",
        "entry_hurdle_limitation":"Predefined filter tests funding rate against4% plus fees; with25% idle margin collateral, full-capital cash hurdle is higher. Net evaluation debits ALL foregone cash; this is not hidden by the filter and is no reason to change parameters after results.",
        "costs_are_modelled_not_current_quotes":True,"current_control_limit":"settlement-driven historical reconstruction, not exact monitor snapshot path; source missing-name sizing intentionally reproduced",
        "white_reality_check":wrc,"liquidity_diagnostic":liquidity_diagnostic,
        "liquidity_limit":"Current snapshot filter only, NOT historical capacity; cannot validate past execution. BTC-only cannot infer cross-asset t from one name.",
        "configurations":configured,"candidate_results":candidate_rows,
        "autonomous_deploy_candidates":[],"verdict":"NO SURVIVOR; NO CAPITAL DEPLOYED",
        "next_data_lane":spec["followup"]}
    out.mkdir(parents=True,exist_ok=True)
    atomic_json(out/"carry_economic_hurdle_lab.json",receipt)
    lines=["# Carry economic-hurdle validation — 2026-09-10", "", "NO SURVIVOR. PAPER research only; no capital deployed.", "",
           "Two new cash-relative entry-filter hypotheses and two controls;24 configurations fixed before execution. This is the same carry mechanism, not a newly independent strategy.", "",
           "`current_rule_control` names the observed PRE-AMENDMENT basket32% / per-asset2% / missing-name allocation rule. It does NOT describe the subsequently amended per-asset16% / remainder-cap implementation. The manifest remains frozen and the live source hashes at run are recorded separately.", "",
           f"History: {receipt['history_start_utc']} through {receipt['history_end_utc']}; {coverage['chosen_days']} contiguous complete days. History predates registration and has been searched before: it is diagnostic, not pristine holdout.", "",
           "No completed funding cycle in the current evaluation window means the annualised figures below are short-sample accounting diagnostics, not validated profit or loss forecasts. Zero activity is untested, not success. There is no instruction to keep mining variants until one turns positive.", "",
           "| Policy | RT bps | Delay hours | Net %/yr | Excess4% cash %/yr | t | DSR | Complete cycles |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for c in configured:
        e=c['evaluation'];lines.append(f"|{c['policy']}|{c['roundtrip_bps']}|{c['delay_hours']}|{c['mean_net_ann_pct']:.3f}|{c['mean_excess_cash_ann_pct']:.3f}|{e['t_stat']}|{e['deflated_sharpe']}|{c['complete_cycles']}|")
    lines += ["", "Settlement funding is summed hourly; no8-hour annualisation, no funding before delayed entry. Each entry/exit costs its full modelled leg costs; terminal inventory is unwound.75% spot/25% margin allocation prevents cost-free collateral. These are arithmetic funding-only models; missing basis,FX,margin and executable fills prohibit deployment.", "",
              "The current-rule control reproduces missing-name sizing. Maximum notional/budget: "+str(max(c['max_funding_notional_vs_budget'] for c in configured if c['policy']=='current_rule_control'))+". It is not a simulation of current live marks or a promise of current expectancy.", "",
              "Trial lineage:4415 conservatively documented prior configurations +24 here=4439; original ledgers overlap/omit branches, so this is declared not canonical. No-search-penalty sensitivities are inJSON. Purged30-day/5% embargo folds fit no parameters; historical70/30 split is descriptive only.", ""]
    for c in candidate_rows:
        lines += ["## "+c['mechanism']+" — REFUSED", ""]+["- "+r for r in c['refusal_reasons']]+[""]
    lines += ["Next collection lane: "+spec['followup'], "", "No frozen registration, live ledger, tick, executor or gate was edited."]
    (out/"research_carry_economic_hurdle_2026-09-10.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(safe({"verdict":receipt['verdict'],"coverage":coverage,"candidates":candidate_rows,"receipt":str(out/"carry_economic_hurdle_lab.json")}),indent=2,allow_nan=False))
    return receipt


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache",type=Path,default=ROOT/"reports/carry_funding_cache")
    parser.add_argument("--output-dir",type=Path,default=ROOT/"reports")
    parser.add_argument("--collect",action='store_true',help='Fetch two public settlement series into separate owned cache, once20h')
    parser.add_argument("--incremental",action='store_true',help='Skip validation if settled-rate inputs, manifest and runner are unchanged')
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    lock_handle=(args.output_dir/'.carry_economic_hurdle.lock').open('a')
    try:
        fcntl.flock(lock_handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('SKIP: carry economic-hurdle research already running')
        raise SystemExit(0)
    if args.collect:
        if args.cache==(ROOT/'reports/carry_funding_cache'):
            args.cache=ROOT/'reports/carry_economic_hurdle_cache'
        print(json.dumps({'collection':collect_settlements(args.cache)},indent=2))
    state=args.output_dir/'carry_economic_hurdle_run_state.json'
    signature=input_signature(args.cache)
    prior=json.loads(state.read_text()) if state.exists() else {}
    if args.incremental and prior.get('input_signature')==signature:
        print('SKIP: identical settled funding; finite hypotheses remain armed for new data, no repeated search.')
    else:
        run(args.cache,args.output_dir)
        atomic_json(state,{'completed_utc':datetime.now(timezone.utc).isoformat(),'input_signature':signature})
