/* WAR ROOM ALL MARKETS v1 2026-10-27 (+crypto display baseline 2026-10-27) - PAPER-only, read-only. Reads /api/markets (warroom_markets.py).
   Firm summary includes US + Asia + London + Crypto breakouts (London card added 2026-10-27 pt1522u, kind 'europe'). Every market (US x3, ASX, JP, HK, SG, KR, TW,
   Crypto, Trend18, OANDA read-only) shown with big figures. OANDA strategy lab card added 2026-10-27 (shadow only). Missing data -> dash. A$, WITA. */
(function(){
const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const N=(x,d=2)=>x==null||isNaN(x)?'–':Number(x).toLocaleString('en-AU',{minimumFractionDigits:d,maximumFractionDigits:d});
const px=x=>x==null?'–':N(x,Math.abs(x)<5?4:2);
const A=(x,d=0)=>x==null||isNaN(x)?'–':(x<0?'−':'')+'A$'+N(Math.abs(x),d);
const SA=x=>x==null||isNaN(x)?'–':(x>0?'+':'')+A(x);
const C=x=>x>0?'pos':x<0?'neg':'';
const short=s=>s?String(s).replace(/^\w{3} /,'').replace(' WITA',''):'–';
function el(id,tag,where,ref){let e=document.getElementById(id);if(!e){e=document.createElement(tag||'div');e.id=id;if(where)ref?where.insertBefore(e,ref):where.appendChild(e);}return e;}
function sess(m){const s=m.session||{};if(s.always)return '<span class="wm-ch on">OPEN 24/7</span>';
 if(s.open===true)return `<span class="wm-ch on">OPEN</span><span class="wm-sm">closes ${E(short(s.closes_wita))}</span>`;
 if(s.open===false)return `<span class="wm-ch off">CLOSED</span><span class="wm-sm">opens ${E(short(s.next_open_wita))}</span>`;return '<span class="wm-ch off">–</span>';}
function dot(m){const f=m.freshness||{};const age=f.age_min==null?'no data':(f.age_min<60?f.age_min+'m old':Math.round(f.age_min/60*10)/10+'h old');
 return `<span class="wm-dot ${E(f.level||'red')}" title="data ${E(age)}"></span><span class="wm-sm">${E(age)}${f.basis==='price'?' (price)':''}${f.data_errors?' · '+E(f.data_errors)+' data error'+(f.data_errors>1?'s':''):''}</span>`;}
function openUpl(m){const p=m.positions||[];if(!p.length)return 0;let s=0,ok=false;p.forEach(x=>{if(x.upl_aud!=null){s+=x.upl_aud;ok=true}});return ok?s:null;}
function cnt(m){if(m.kind==='crypto')return `${m.fired_today??'–'} / ${m.max_per_day??'–'}`;if(m.max_open==null)return `${m.open_count??'–'}`;return `${m.open_count??'–'} / ${m.max_open}`;}
/* Display baseline (state/warroom_baselines.json): card shows realized since reset; prior total kept in history */
function rtot(m){return m.baseline?m.realized_since_reset_aud:m.realized_total_aud;}
function rlab(m){return m.baseline?'Realized since reset':'Realized total';}
function rprior(m){if(!m.baseline)return '';const b=m.baseline;return `<div class="wm-sm2 muted wm-prior">Prior to reset: ${E(A(b.prior_aud))} (kept in history)${b.reset_at?' · reset '+E(String(b.reset_at).slice(0,16).replace('T',' '))+' WITA':''}</div>`;}
function pcard(m,p){const sh=(p.side||'').toUpperCase()==='SHORT';
 return `<div class="card wm-p"><div class="hd"><span class="tk">${E(p.sym)}</span><span class="bd ${sh?'SHORT':'LONG'}">${sh?'SHORT':'LONG'}</span><span class="pl ${C(p.upl_aud)}">${SA(p.upl_aud)}</span></div>
 <div class="wm-mk">${E(m.label)}</div>
 <div class="g"><div><span>Entry</span><b>${px(p.entry)}</b></div><div><span>Mark</span><b>${px(p.mark)}</b></div>
 <div><span>Stop</span><b>${px(p.stop)}</b></div><div><span>Target</span><b>${p.t1!=null?px(p.t1)+' / ':''}${px(p.target)}</b></div>
 <div><span>Qty</span><b>${N(p.qty,Math.abs(p.qty||0)<10?2:0)}</b></div><div><span>A$ at risk</span><b>${A(p.risk_aud)}</b></div></div>
 <div class="muted" style="margin-top:6px">${E(p.note||'')}</div></div>`;}
function fqueue(q){if(!q)return '';const p=q.pending||[];const N=v=>v==null?'–':(+v).toLocaleString(undefined,{maximumFractionDigits:4});
 return `<div class="wm-gc"><div class="wm-gn">Awaiting funder approval · ${p.length}</div>${p.map(e=>`<div class="wm-sm2"><b>${E(e.id)}</b> ${E(e.side)} ${E(e.ticker)} · entry ${N(e.entry)} · stop ${N(e.stop)} · target ${N(e.target)} · ${E(e.shares)} sh · risk ${A(e.risk_aud)} · win ${A(e.win_aud)} · score ${N(e.score)}${e.tags&&e.tags.quality?' · quality '+E(e.tags.quality):''}${e.tags&&(e.tags.news_risk||[]).length?' · news '+E(e.tags.news_risk.join('; ')):''}${e.tags&&e.tags.strategy?' · '+E(e.tags.strategy):''}<br>${E(e.eli10)}${e.note?'<br><i>'+E(e.note)+'</i>':''}</div>`).join('')||'<div class="wm-sm2">Nothing waiting.</div>'}${(q.recent||[]).length?`<div class="wm-sm2">Recent: ${q.recent.map(e=>E(e.id)+' '+E(e.state)).join(' · ')}</div>`:''}<div class="wm-sm2">${E(q.how||'')}</div></div>`;}
function mcard(m){const up=openUpl(m),ar=m.armed||[];
 let extra='';
 if(m.kind==='crypto')extra=`<div class="wm-line">Entry engine <b class="${m.engine_status==='ok'?'pos':'neg'}">${E(m.engine_status||'–')}</b> · Auto-fire <b>${E(m.auto_fire||'–')}</b></div><div class="wm-sm2">${E(m.scan_line||'no scan yet')}${m.last_action?'<br>Last action: '+E(m.last_action):''}</div>`;
 if(m.kind==='trend18')extra=`<div class="wm-line">Equity <b>${A(m.equity_aud)}</b> · since start <b class="${C(m.pnl_total_aud)}">${SA(m.pnl_total_aud)}</b> · DD ${m.drawdown_pct==null?'–':N(m.drawdown_pct,1)+'%'}${m.spare_aud!=null?` · spare ${A(m.spare_aud)} · deployed ${A(m.deployed_aud)}`:''}${m.in_firm_equity===false?' · not firm equity':''}</div>`;
 if(m.kind==='fx')extra=`<div class="wm-line">Capital base <b>${A(m.capital_base_aud!=null?m.capital_base_aud:m.nav_aud)}</b> (broker NAV ${A(m.nav_aud)} + funder top-up ${A(m.funder_topup_aud||0)}) · open P&amp;L <b class="${C(m.upl_aud)}">${SA(m.upl_aud)}</b> <span class="wm-ro">READ-ONLY</span></div>`;
 if(m.error)extra+=`<div class="wm-sm2 neg">unavailable: ${E(m.error)}</div>`;
 if(m.session_note)extra+=`<div class="wm-sm2">${E(m.session_note)}</div>`;
 if(m.rs_long&&Object.keys(m.rs_long).length)extra+=`<div class="wm-sm2">RS longs (short regime): ${Object.entries(m.rs_long).map(([k,v])=>E(k)+' '+E(v)).join(' · ')}</div>`;
 {const tg=[...(m.positions||[]),...(m.armed||[])].filter(r=>r.tags);if(tg.length)extra+=`<div class="wm-sm2">Info tags (never block): ${tg.map(r=>`<b>${E(r.sym)}</b> news ${E((r.tags.news_risk||[]).length?r.tags.news_risk.join('; '):'none')} · quality ${E(r.tags.quality||'n/a')}${r.tags.quality_detail&&(r.tags.quality_detail.fails||[]).length?' ('+E(r.tags.quality_detail.fails.join(', '))+')':''}${r.tags.catalyst?' · catalyst '+E(r.tags.catalyst.label||'yes'):''}`).join('<br>')}</div>`;}
 if(m.rescan)extra+=`<div class="wm-sm2">Rescan every ${E(m.rescan.every_min)}m · last ${E(short(m.rescan.last_wita)||'–')} · next ${E(short(m.rescan.next_wita)||'–')}${(m.rescan.newly_armed||[]).length?' · armed '+E(m.rescan.newly_armed.join(', ')):''}</div>`;
 const arm=ar.length?ar.map(a=>`<div class="wm-arm"><b>${E(a.sym)}</b><span class="bd ${(a.side||'').toUpperCase()==='SHORT'?'SHORT':'LONG'}">${E((a.side||'').toUpperCase()||'–')}</span><span>trig <b>${px(a.trigger)}</b></span><span>stop ${px(a.stop)}</span></div>`).join(''):'';
 const pos=(m.positions||[]).length?(m.positions||[]).map(p=>`<div class="wm-arm"><b>${E(p.sym)}</b><span class="bd ${(p.side||'').toUpperCase()==='SHORT'?'SHORT':'LONG'}">${E(p.side)}</span><span>mark ${px(p.mark)}</span><span class="${C(p.upl_aud)}"><b>${SA(p.upl_aud)}</b></span></div>`).join(''):'';
 const asiaExtra=(m.kind==='asia'||m.kind==='europe')?`<div class="wm-k asia"><div><span>Bench</span><b>${m.bench??'–'}</b></div><div><span>Armed</span><b>${(m.armed||[]).length}</b></div></div><div class="wm-hrsline">Hours WITA <b>${E((m.hours_wita||'–').replace(/ \(.*\)/,''))}</b></div>`:'';
 return `<div class="card wm-m" id="wm-${E(m.key)}"><div class="wm-h"><span class="wm-n">${E(m.label)}</span>${sess(m)}<span class="wm-f">${dot(m)}</span></div>
 <div class="wm-k"><div><span>${E(m.count_label||'Open / max')}</span><b>${cnt(m)}</b></div><div><span>Open P&amp;L</span><b class="${C(up)}">${SA(up)}</b></div>
 <div><span>Realized today</span><b class="${C(m.realized_today_aud)}">${SA(m.realized_today_aud)}</b></div><div><span>${rlab(m)}</span><b class="${C(rtot(m))}">${SA(rtot(m))}</b></div></div>
 ${rprior(m)}${asiaExtra}${extra}${pos?'<div class="wm-sub">Open</div>'+pos:''}${arm?'<div class="wm-sub">Armed (waiting for trigger)</div>'+arm:(m.kind==='us'||m.kind==='asia'||m.kind==='europe'?'<div class="wm-sm2">No armed setups'+(m.bench?' · '+m.bench+' on bench':'')+'</div>':'')}</div>`;}
function gcard(title,g){g=g||{};
 return `<div class="wm-gc"><div class="wm-gn">${E(title)}</div><div class="wm-gk"><div><span>Risk</span><b>${A(g.open_risk_aud)}</b></div><div><span>Day P&amp;L</span><b class="${C(g.day_pnl_aud)}">${SA(g.day_pnl_aud)}</b></div><div><span>Open</span><b>${g.open_positions??'–'}</b></div><div><span>Armed</span><b>${g.armed??'–'}</b></div></div>${g.bench!=null?`<div class="wm-sm2">${g.markets_open??0}/${g.count??0} markets open · ${g.bench} on bench</div>`:`<div class="wm-sm2">${g.markets_open??0}/${g.count??0} markets open</div>`}</div>`;}
function firm(d){const f=d.firm||{},w=f.watchdog||{},j=f.jobs||{};const hc=f.health==='OK'?'pos':'neg';
 return `<div class="wm-firm"><div><span>Open risk</span><b>${A(f.open_risk_aud)}</b></div><div><span>Day P&amp;L</span><b class="${C(f.day_pnl_aud)}">${SA(f.day_pnl_aud)}</b></div>
 <div><span>Open positions</span><b>${f.open_positions??'–'}</b></div><div><span>Engines</span><b class="${hc}">${E(f.health||'–')}</b></div></div>
 <div class="wm-groups">${gcard('US books',f.us)}${gcard('Asia books',f.asia)}${f.europe?gcard('London',f.europe):''}${gcard('Crypto',f.crypto)}</div>
 <div class="wm-sm2">Watchdog ${E(w.status||'–')} (${w.alerts??'–'} alerts) · jobs failing: ${j.failing?(j.failing.length?E(j.failing.map(x=>x.replace('com.tfo.','')).join(', ')):'none'):'–'} · ${E(d.generated_wita||'')} · ${E(f.basis||'')}</div>`;}
function strip(ms){return '<div class="wm-strip">'+ms.map(m=>`<a href="#wm-${E(m.key)}" data-k="${E(m.key)}"><span class="wm-dot ${E((m.freshness||{}).level||'red')}"></span>${E(m.label.replace('US - ','US·').replace(' (read-only)','').replace(' (Kraken)','').replace(' sleeve',''))}<i class="${(m.session||{}).open||(m.session||{}).always?'on':''}"></i></a>`).join('')+'</div>';}
/* OANDA STRATEGY LAB card (added 2026-10-27): research + SHADOW forward tests + daily FX hunt top 3. Reads d.oanda_lab. No orders. */
const P=x=>x==null||isNaN(x)?'–':Math.round(x*100)+'%';
function labcard(L){
 if(!L||!L.available)return '<div class="card wm-m wm-lab" id="wm-oandalab"><div class="wm-h"><span class="wm-n">OANDA strategy lab</span></div><div class="wm-sm2">Lab data unavailable (state/oanda_lab/lab.json)</div></div>';
 const h=L.hunt||{},sc=h.scorecard||{},sv=L.survivors||[];
 const srow=x=>`<div class="wm-lr"><div class="wm-ln"><b>${E(x.name)}</b><span>${E(x.instruments)} · ${E(x.tf)}</span></div>
  <div class="wm-k3"><div><span>Holdout net (1%)</span><b class="${C(x.holdout_aud)}">${SA(x.holdout_aud)}</b></div><div><span>Win · trades</span><b>${P(x.win_rate)} · ${x.trades??'–'}</b></div><div><span>Max DD</span><b class="neg">${A(x.maxdd_aud)}</b></div></div>
  <div class="wm-sm2">${E(x.status||'shadow')} · shadow ${x.fwd_trades??0} closed / ${x.fwd_open??0} open · <b class="${C(x.fwd_aud)}">${SA(x.fwd_aud)}</b> so far</div></div>`;
 const prow=p=>{const sh=(p.side||'')==='SHORT',t=p.targets||[];return `<div class="wm-lr"><div class="wm-ln"><b>${E(p.ins)}</b><span class="bd ${sh?'SHORT':'LONG'}">${E(p.side)}</span><span>${E(p.tf)} · score ${N(p.score,2)}</span></div>
  <div class="wm-k3"><div><span>Entry</span><b>${px(p.entry)}</b></div><div><span>Stop</span><b>${px(p.stop)}</b></div><div><span>A$ risk</span><b>${A(p.risk_aud)}</b></div></div>
  <div class="wm-sm2">Targets ${t.map(px).join(' / ')||'–'} · ${E((p.fams||[]).join(', '))} · ${E(p.status||'')}${p.mtm_aud!=null?' · <b class="'+C(p.mtm_aud)+'">'+SA(p.mtm_aud)+'</b>':''}${p.exit_why?' ('+E(p.exit_why)+')':''}</div></div>`;};
 return `<div class="card wm-m wm-lab" id="wm-oandalab"><div class="wm-h"><span class="wm-n">OANDA strategy lab</span><span class="wm-ro">SHADOW · NO ORDERS</span><span class="wm-f">${dot(L)}</span></div>
 <div class="wm-k"><div><span>Configs tested</span><b>${N(L.configs_tested,0)}</b></div><div><span>Survivors</span><b class="${(L.survivors_count||0)>0?'pos':'neg'}">${L.survivors_count??'–'}</b></div>
 <div><span>Instruments</span><b>${L.universe??'–'}</b></div><div><span>Hunt score (30+)</span><b class="${C(sc.net_aud)}">${SA(sc.net_aud)}</b></div></div>
 <div class="wm-verdict">${E(L.verdict||'–')}</div>
 ${sv.length?'<div class="wm-sub">Survivors (holdout 2021-07 → now, net of costs)</div>'+sv.map(srow).join(''):'<div class="wm-sub">Survivors</div><div class="wm-sm2">None passed every test.</div>'}
 <div class="wm-sub">Today's FX hunt: top 3 ${h.date_wita?'· '+E(h.date_wita):''}${h.regime?' · '+E(h.regime):''}</div>
 ${(h.picks||[]).length?h.picks.map(prow).join(''):`<div class="wm-sm2">No setup cleared the rules today${h.n_setups!=null?' ('+h.n_setups+' fresh signals)':''}: zero picks, no shadow trade staged.${(h.watch||[]).length?' Running signals (not fresh, not traded): '+h.watch.map(w=>E(w.ins)+' '+E(w.side)+' ('+E(w.fam)+')').join(', '):''}</div>`}
 ${h.kill?'<div class="wm-sm2">Kill rule: '+(h.kill.halted?'<b class="neg">HALTED – '+E(h.kill.reason)+'</b>':'<b class="pos">active, not triggered</b> (drawdown '+N(h.kill.mtm_dd_R,1)+'R)')+'</div>':''}
 ${L.checks?'<div class="wm-sm2">Survivor checks: '+E(L.checks)+'</div>':''}
 <div class="wm-sub">Hunt scorecard (shadow, after spread + financing)</div>
 <div class="wm-k"><div><span>Closed / target</span><b>${sc.n_closed??0} / ${sc.target_n??30}</b></div><div><span>Win rate</span><b>${P(sc.win_rate)}</b></div>
 <div><span>Net A$ closed</span><b class="${C(sc.net_aud)}">${SA(sc.net_aud)}</b></div><div><span>Open MTM</span><b class="${C(sc.open_mtm_aud)}">${SA(sc.open_mtm_aud)}</b></div></div>
 <div class="wm-sm2">Avg ${sc.avg_R==null?'–':N(sc.avg_R,2)+'R'} · open ${sc.n_open??0}. ${E(h.rule||'')} Updated ${E(L.updated_wita||'–')}. Report: ${E(L.report||'–')}</div></div>`;}
async function go(){
 let d=null;try{const r=await fetch('/api/markets?t='+Date.now());if(r.ok)d=await r.json();}catch(e){}
 const wc=document.getElementById('wc');if(!wc)return;
 const fr=el('wmfirm','div',wc,document.getElementById('wctot'));
 if(!d||!d.markets){fr.innerHTML='<div class="wm-sm2 neg">All-markets data unavailable (/api/markets)</div>';return;}
 const ms=d.markets;fr.innerHTML=firm(d)+fqueue(d.funder_queue)+strip(ms);
 const plan=document.getElementById('wcp-plan');
 /* Asia + crypto cards FIRST so JP/HK/SG/KR/TW are not buried under US trade cards */
 const asia=ms.filter(m=>m.kind==='asia'), eu=ms.filter(m=>m.kind==='europe'), crypto=ms.filter(m=>m.kind==='crypto'), other=ms.filter(m=>m.kind!=='asia'&&m.kind!=='europe'&&m.kind!=='crypto'&&m.kind!=='us');
 el('wmasia','div',plan,document.getElementById('wcplan')).innerHTML='<h2>Asia markets</h2>'+asia.map(mcard).join('')+
  (eu.length?'<h2>London</h2>'+eu.map(mcard).join(''):'')+
  '<h2>Crypto (Kraken)</h2>'+crypto.map(mcard).join('')+
  (other.length?'<h2>Other sleeves</h2>'+other.map(mcard).join(''):'');
 el('wmlab','div',plan,document.getElementById('wcplan')).innerHTML='<h2>OANDA strategy lab</h2>'+labcard(d.oanda_lab);
 el('wmplan','div',plan).innerHTML='<h2>All markets</h2>'+ms.map(mcard).join('')+`<div class="wm-sm2">${E((d.notes||[]).join(' '))}</div>`;
 const pos=document.getElementById('wcp-pos');
 el('wmpos','div',pos).innerHTML=ms.filter(m=>m.kind!=='us'&&(m.positions||[]).length).map(m=>`<h2>${E(m.label)} · ${(m.positions||[]).length}</h2>`+m.positions.map(p=>pcard(m,p)).join('')).join('')||'';
 const pl=document.getElementById('wcp-pl');
 el('wmpl','div',pl,document.getElementById('wcpl')).innerHTML='<div class="wm-plt">'+ms.map(m=>{const up=openUpl(m);return `<div class="wm-plr"><div class="wm-n">${E(m.label)}</div><div class="wm-k3"><div><span>Open</span><b class="${C(up)}">${SA(up)}</b></div><div><span>Today</span><b class="${C(m.realized_today_aud)}">${SA(m.realized_today_aud)}</b></div><div><span>${m.baseline?'Since reset':'Total'}</span><b class="${C(rtot(m))}">${SA(rtot(m))}</b></div></div>${rprior(m)}</div>`}).join('')+'</div><div class="wm-sm2">Today / Total = realized A$ after costs from each ledger. Crypto = since display reset (prior total kept in history). Trend18 shows since-start P&amp;L on its card. Firm Asia row = ASX+JP+HK+SG+KR+TW.</div>';
 const rk=document.getElementById('wcp-risk'),f=d.firm||{},j=f.jobs||{};
 el('wmrisk','div',rk,document.getElementById('wcrisk')).innerHTML=`<div class="wm-groups">${gcard('US books',f.us)}${gcard('Asia books',f.asia)}${f.europe?gcard('London',f.europe):''}${gcard('Crypto',f.crypto)}</div>
 <div class="wm-plt">${ms.map(m=>`<div class="wm-plr"><div class="wm-h"><span class="wm-n">${E(m.label)}</span>${sess(m)}<span class="wm-f">${dot(m)}</span></div><div class="wm-k3"><div><span>A$ at risk</span><b>${A(m.risk_aud)}</b></div><div><span>${E(m.count_label||'Open / max')}</span><b>${cnt(m)}</b></div><div><span>Job</span><b class="${(j.failing||[]).includes(m.job)?'neg':'pos'}">${m.job?((j.failing||[]).includes(m.job)?'FAIL':((j.jobs||{})[m.job]?'OK':'–')):'–'}</b></div></div></div>`).join('')}</div>
 <div class="wm-sm2">Rail watchdog ${E((f.watchdog||{}).status||'–')} at ${E((f.watchdog||{}).asof_wita||'–')}. Failing jobs: ${E((j.failing||[]).join(', ')||'none')}${(j.missing||[]).length?' · not loaded: '+E(j.missing.join(', ')):''}. Trend18/OANDA have no stop-based risk figure (dash).</div>`;
}
function top(){const h=document.getElementById('h48'),wc=document.getElementById('wc');if(h&&wc&&h.nextSibling!==wc){document.body.insertBefore(h,wc);h.classList.add('wm-top');}}
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('#wc .wm-strip a');if(!a)return;e.preventDefault();
 const b=document.querySelector('#wctabs button[data-p="plan"]');if(b)b.click();const t=document.getElementById('wm-'+a.dataset.k);if(t)setTimeout(()=>t.scrollIntoView({behavior:'smooth',block:'start'}),30);});
document.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{top();go();setInterval(go,60000)},50));
})();
