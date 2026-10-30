/* tfo_legacy.js: shared READ-ONLY layer for the War Room (/warroom) and mobile (/m) pages. PAPER only.
   2026-10-03 dashboard follow-up. It never POSTs and has no order controls. It GETs /api/funder and
   /static/glossary.json, and reads (without changing) the page's own /api/all and /api/positions responses.
   Adds: ⓘ glossary popovers + Glossary dialog, a "last updated" stamp per panel, the funder fee + 19% benchmark
   card, open-<details> memory across refreshes, and a refresh when the tab becomes visible again. */
(function(){
'use strict';
const PAGE=location.pathname.replace(/\/+$/,'')==='/m'?'m':'warroom';
const FEEDS={};                       // '/api/all' | '/api/positions' -> {at, data}
const G={gl:null,idx:null,cache:new Map()};
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>typeof v==='number'&&Number.isFinite(v);
const usd=(v,d)=>num(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:d??0,minimumFractionDigits:d??0}).format(v):'—';
const sgn=(v,d)=>num(v)?(v>=0?'+':'−')+usd(Math.abs(v),d):'—';
const SYD=new Intl.DateTimeFormat('en-AU',{timeZone:'Australia/Sydney',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'});
const SYDDATE=new Intl.DateTimeFormat('en-AU',{timeZone:'Australia/Sydney',day:'2-digit',month:'short',year:'numeric'});

/* ---------------------------------------------------------------- time parsing (naive stamps are Sydney local) */
function sydOffsetMin(ms){try{const s=new Intl.DateTimeFormat('en-US',{timeZone:'Australia/Sydney',timeZoneName:'shortOffset'}).formatToParts(new Date(ms)).find(p=>p.type==='timeZoneName').value;const m=/GMT([+-])(\d+)(?::(\d+))?/.exec(s);return m?(m[1]==='-'?-1:1)*(+m[2]*60+(+m[3]||0)):600}catch(e){return 600}}
function parseT(v){
  if(num(v))return v>1e12?v:v>1e9?v*1000:null;
  if(typeof v!=='string')return null;const s=v.trim();
  if(/^\d{4}-\d\d-\d\d$/.test(s)){const t=Date.parse(s+'T00:00:00Z');return t-sydOffsetMin(t)*60000+86399000}  // date only: end of that Sydney day
  const m=/^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d(?::\d\d(?:\.\d+)?)?)(Z|[+-]\d\d:?\d\d)?$/.exec(s);if(!m)return null;
  if(m[3]){const t=Date.parse(m[1]+'T'+m[2]+(m[3]==='Z'?'Z':m[3].replace(/^([+-]\d\d)(\d\d)$/,'$1:$2')));return Number.isNaN(t)?null:t}
  const u=Date.parse(m[1]+'T'+m[2]+'Z');if(Number.isNaN(u))return null;return u-sydOffsetMin(u)*60000;
}
const fmtT=t=>SYD.format(new Date(t)).replace(',','');
const ago=t=>{const s=Math.max(0,(Date.now()-t)/1000);return s<90?Math.round(s)+' s ago':s<5400?Math.round(s/60)+' min ago':s<172800?(s/3600).toFixed(1)+' h ago':(s/86400).toFixed(1)+' d ago'};
const KEYS=['updated_utc','updated','last_valued_at','asof_utc','asof','generated_utc','generated','checked_at','last_eval_utc','mark_utc','last_timestamp','utc','updated_at'];
function findTime(o){
  if(!o||typeof o!=='object')return null;
  for(const k of KEYS){const t=parseT(o[k]);if(t)return t}
  for(const k of ['book_risk','book','summary']){const s=o[k];if(s&&typeof s==='object')for(const kk of KEYS){const t=parseT(s[kk]);if(t)return t}}
  if(Array.isArray(o.points)&&o.points.length){const p=o.points[o.points.length-1];const t=parseT(Array.isArray(p)?p[0]:p&&(p.t||p.utc||p.date));if(t)return t}
  return null;
}

/* ---------------------------------------------------------------- observe the page's own feeds (read-only clone) */
const nativeFetch=window.fetch.bind(window);
window.fetch=function(input,init){
  const p=nativeFetch(input,init);
  try{const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(path==='/api/all'||path==='/api/positions')p.then(r=>{if(r&&r.ok)r.clone().json().then(j=>{FEEDS[path]={at:Date.now(),data:j};soon()}).catch(()=>{})}).catch(()=>{})}catch(e){}
  return p;
};

/* ---------------------------------------------------------------- glossary */
const norm=s=>String(s||'').replace(/&amp;/g,'&').replace(/\s+/g,' ').trim().toLowerCase();
const clean=s=>String(s||'').replace(/[\u{1F000}-\u{1FAFF}\u2600-\u27BF\uFE0F\u25CF\u2022]/gu,' ').replace(/\s+/g,' ').trim();
async function loadGlossary(){try{const r=await nativeFetch('/static/glossary.json',{cache:'no-store'});if(!r.ok)return;G.gl=await r.json();index();soon()}catch(e){}}
function index(){
  const exact=new Map(),abbr=[],contains=[];
  (G.gl.terms||[]).forEach(t=>{[t.term,...(t.aliases||[])].forEach(a=>exact.set(norm(a),t));
    (t.abbr||[]).forEach(a=>abbr.push([new RegExp('(^|[^A-Za-z0-9])'+a.replace(/[.*+?^${}()|[\]\\&]/g,'\\$&')+'($|[^A-Za-z0-9])'),t]));
    (t.contains||[]).forEach(c=>contains.push([c.toLowerCase(),t]))});
  contains.sort((a,b)=>b[0].length-a[0].length);G.idx={exact,abbr,contains};G.cache.clear();
}
// Context fixes: on these legacy pages "Fee" in the trade blotter is a trading fee, not the funder fee.
const OVERRIDE={'fee':'costs'};
function lookup(text,strict){
  if(!G.idx)return null;const raw=clean(text);if(!raw)return null;const ck=(strict?'1':'0')+raw;if(G.cache.has(ck))return G.cache.get(ck);
  const n=norm(raw),ix=G.idx;let t=null;
  if(OVERRIDE[n])t=(G.gl.terms||[]).find(x=>x.id===OVERRIDE[n])||null;
  if(!t&&ix.exact.has(n))t=ix.exact.get(n);
  if(!t){const head=norm(raw.split(/ · | — | - /)[0].replace(/\(.*?\)/g,''));if(ix.exact.has(head))t=ix.exact.get(head)}
  if(!t)for(const [re,x] of ix.abbr)if(re.test(raw)){t=x;break}
  if(!t&&!strict)for(const [c,x] of ix.contains)if(n.includes(c)){t=x;break}
  G.cache.set(ck,t);return t;
}
let pop=null,pinned=false,anchor=null;
function showPop(btn,pin){
  const t=(G.gl&&G.gl.terms||[]).find(x=>x.id===btn.dataset.gid);if(!t)return;
  if(!pop){pop=document.createElement('div');pop.id='tfo-gpop';pop.setAttribute('role','tooltip');document.body.append(pop)}
  pop.innerHTML=`<span class="gcat">${esc(t.category)}</span><b>${esc(t.term)}</b>${esc(t.short)}${t.long?`<p>${esc(t.long)}</p>`:''}<p><a href="#glossary" data-gopen="${esc(t.id)}">Open glossary</a></p>`;
  pop.style.display='block';const r=btn.getBoundingClientRect(),w=pop.offsetWidth,h=pop.offsetHeight;
  const vw=window.visualViewport?window.visualViewport.width:innerWidth,vh=window.visualViewport?window.visualViewport.height:innerHeight;
  let x=Math.min(Math.max(8,r.left+r.width/2-w/2),vw-w-8),y=r.bottom+8;if(y+h>vh-8)y=Math.max(8,r.top-h-8);
  pop.style.left=x+'px';pop.style.top=y+'px';pinned=!!pin;anchor=btn;btn.setAttribute('aria-expanded','true');
}
function hidePop(){if(pop)pop.style.display='none';if(anchor)anchor.setAttribute('aria-expanded','false');pinned=false;anchor=null}
const hoverable=()=>window.matchMedia&&matchMedia('(hover: hover)').matches;
document.addEventListener('click',e=>{
  const g=e.target.closest&&e.target.closest('.tfo-gi');
  if(g){e.preventDefault();e.stopPropagation();if(anchor===g&&pinned)hidePop();else showPop(g,true);return}
  const o=e.target.closest&&e.target.closest('[data-gopen]');if(o){e.preventDefault();e.stopPropagation();hidePop();openGlossary(o.dataset.gopen);return}
  if(pop&&!pop.contains(e.target))hidePop();
},true);
document.addEventListener('mouseover',e=>{const g=e.target.closest&&e.target.closest('.tfo-gi');if(g&&hoverable()&&!pinned)showPop(g,false)});
document.addEventListener('mouseout',e=>{const g=e.target.closest&&e.target.closest('.tfo-gi');if(g&&!pinned&&!(e.relatedTarget&&pop&&pop.contains(e.relatedTarget)))hidePop()});
document.addEventListener('focusin',e=>{if(e.target.classList&&e.target.classList.contains('tfo-gi')&&!pinned)showPop(e.target,false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hidePop()});
window.addEventListener('scroll',()=>{if(!pinned)hidePop()},{passive:true});
const SEL='h1,h2,h3,th,dt,summary,.lbl,.label,.cardtitle,.k,[data-gterm]';
const STRICT=el=>el.matches('.k,summary');
function ownText(el){
  if(el.dataset.gterm)return el.dataset.gterm;
  const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join(' ').trim();
  if(own)return own;
  return [...el.childNodes].filter(n=>!(n.classList&&(n.classList.contains('meta')||n.classList.contains('tfo-gi')||n.classList.contains('tfo-upd')))).map(n=>n.textContent).join(' ');
}
function decorate(root){
  if(!G.idx||!root)return;
  root.querySelectorAll(SEL).forEach(el=>{
    if(el.closest('#tfo-gdlg,#tfo-gpop')||el.querySelector(':scope > .tfo-gi'))return;
    const text=ownText(el);if(!text||text.length>90)return;
    const t=lookup(text,STRICT(el));if(!t)return;
    const b=document.createElement('button');b.type='button';b.className='tfo-gi';b.dataset.gid=t.id;b.setAttribute('aria-label','What does '+t.term+' mean?');b.setAttribute('aria-expanded','false');b.textContent='ⓘ';
    const meta=[...el.childNodes].find(n=>n.classList&&(n.classList.contains('meta')||n.classList.contains('tfo-upd')));
    const lastText=[...el.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim()).pop();
    if(meta)el.insertBefore(b,meta);else if(lastText&&lastText.nextSibling)el.insertBefore(b,lastText.nextSibling);else el.append(b);
  });
}
function openGlossary(focusId){
  let d=document.getElementById('tfo-gdlg');
  if(!d){d=document.createElement('dialog');d.id='tfo-gdlg';d.setAttribute('aria-label','Glossary');
    d.innerHTML=`<div class="ghead"><b>Glossary</b><input type="search" id="tfo-gq" placeholder="Search (e.g. NAV, kill, trail, fee)" aria-label="Search glossary"><button type="button" id="tfo-gclose">Close</button></div><div class="gbody" id="tfo-gbody"></div><div class="gfoot">Definitions live in <code>static/glossary.json</code> · full page: <a href="/glossary">/glossary</a></div>`;
    document.body.append(d);d.querySelector('#tfo-gclose').onclick=()=>d.close();d.addEventListener('click',e=>{if(e.target===d)d.close()});
    d.querySelector('#tfo-gq').oninput=e=>fill(e.target.value);d.addEventListener('close',()=>{if(location.hash==='#glossary')history.replaceState(null,'',location.pathname+location.search)})}
  fill('');if(typeof d.showModal==='function'){if(!d.open)d.showModal()}else d.setAttribute('open','');
  if(focusId){const el=document.getElementById('tfo-g-'+focusId);if(el){el.scrollIntoView({block:'center'});el.classList.add('hit')}}
}
function fill(q){
  const terms=(G.gl&&G.gl.terms)||[],n=norm(q),by={};
  terms.filter(t=>!n||norm(t.term+' '+t.short+' '+(t.aliases||[]).join(' ')).includes(n)).forEach(t=>(by[t.category]=by[t.category]||[]).push(t));
  document.getElementById('tfo-gbody').innerHTML=Object.keys(by).sort().map(c=>`<h3>${esc(c)}</h3><dl>${by[c].sort((a,b)=>a.term.localeCompare(b.term)).map(t=>`<dt id="tfo-g-${esc(t.id)}">${esc(t.term)}</dt><dd>${esc(t.short)}${t.long?' '+esc(t.long):''}</dd>`).join('')}</dl>`).join('')||'<p>No matching terms.</p>';
}
window.addEventListener('hashchange',()=>{if(location.hash==='#glossary')openGlossary()});

/* ---------------------------------------------------------------- "last updated" stamps */
// [heading regex or element id, feed, key, max age (s) before the stamp turns amber]
const H=3*3600,P5=15*60;
const MAP_WARROOM=[
  [/^Salary/,'all','salary',null],[/Rumour/,'all','crypto_rumour',H],[/Convexity/,'all','convexity',H],[/Dated-Event/,'all','dated_event',H],
  [/Fundamental Desk/,'pos','fundamental_desk',P5],[/^Consolidated/,'all','firm_risk',2*H],[/Firm equity curve/,'all','equity',2*H],
  [/^Growth book/,'all','growth_equity',3*86400],[/^Value book/,'all','value',H],[/^Carry executor/,'all','carry',H],[/^Live engine book/,'all','engine_equity',H],
  [/^Multi-desk floor/,'all','floor',2*H],[/^Replica desk/,'all','replica',2*H],[/^Engine & risk state/,'all','engine_equity',H],[/^OANDA/,'all','oanda',null],
  [/^Risk engine/,'all','risk',2*H],[/^Infrastructure health/,'all','health',H],[/^Research verdicts/,'all','cost_wall',null],[/^Cost wall/,'all','cost_wall',null],
  [/^Strategy tournament/,'all','tournament',2*H],[/Rally Rider X/,'pos','rally_rider_x',P5],[/Rally Rider HOLD/,'pos','rally_rider_hold',P5],[/Rally Rider/,'pos','rally_rider',P5],
  [/Dip Rider/,'pos','dip_rider',P5],[/Capacity Probe/,'pos','capacity_probe',P5],[/^Edge map|^Traps|^Live wire/,'all','warroom',null],[/^Trade blotter/,'all','blotter',null],[/^Engine log/,'all','engine_equity',H],
  ['#tfo-hero','all','firm_risk',2*H],['#portfolio','pos',null,P5]];
const MAP_M=[
  ['salary-card','all','salary',null],['tab-positions>.hero','all','firm_risk',2*H],['recoverycard','all','crypto_recovery',null],['shortcard','pos','sol_short_pilot',null],
  ['gclcard','all','firm_risk',2*H],['fundcard','pos','fundamental_desk',P5],['clipcard','all','firm_risk',2*H],['topcard','all','firm_risk',2*H],['cryptorumcard','all','crypto_rumour',H],
  ['convexcard','all','convexity',H],['deventcard','all','dated_event',H],['rrcard','pos','rally_rider',P5],['capcard','pos','capacity_probe',P5],['puntcard','pos','punt_24h',P5],
  ['poslist','pos',null,P5],['books','all','firm_risk',2*H],['floors','all','floor',2*H],['oanda-mobile','all','oanda',null],['carrycard','all','carry',H],['traps','all','warroom',null],['wire','all','warroom',null]];
function stampFor(feed,key,maxAge){
  const f=FEEDS[feed==='pos'?'/api/positions':'/api/all'];if(!f)return null;
  const o=key?f.data&&f.data[key]:f.data;let t=key?findTime(o):null;
  if(feed==='pos'&&!key)t=f.at;           // the positions payload is marked on every request
  if(key&&o==null)return {html:`No data in the feed · checked ${esc(fmtT(f.at))}`,cls:'none',t:null};
  if(!t)return {html:`Feed checked ${esc(fmtT(f.at))} · ${esc(ago(f.at))}`,cls:'',t:null,title:'This data carries no timestamp of its own; this is when the page last received it.'};
  const old=maxAge&&(Date.now()-t)/1000>maxAge;
  return {html:`Updated ${esc(fmtT(t))} · ${esc(ago(t))}`,cls:old?'old':'',t,title:old?'Older than this panel normally is: the book may be dormant or its writer has stopped.':''};
}
function putStamp(host,where,id,s){
  let el=document.querySelector(`.tfo-upd[data-for="${id}"]`);
  if(!el){el=document.createElement('div');el.className='tfo-upd';el.dataset.for=id;
    if(where==='after')host.after(el);else if(where==='afterh2')(host.querySelector(':scope > h2')||host.firstElementChild).after(el);else host.prepend(el)}
  if(!s){el.hidden=true;return}
  el.hidden=false;const c='tfo-upd'+(s.cls?' '+s.cls:'');if(el.className!==c)el.className=c;if(el.innerHTML!==s.html)el.innerHTML=s.html;if(s.title)el.title=s.title;else el.removeAttribute('title');
}
function stamps(){
  if(PAGE==='warroom'){
    document.querySelectorAll('main section').forEach((sec,i)=>{
      const h=sec.querySelector(':scope > h2');const txt=h?clean(ownText(h)):'';
      let m=MAP_WARROOM.find(r=>typeof r[0]!=='string'&&h&&getComputedStyle(h).display!=='none'&&r[0].test(txt));
      if(!m)m=MAP_WARROOM.find(r=>typeof r[0]==='string'&&(sec.matches(r[0])||sec.querySelector(':scope > '+r[0]+', :scope '+r[0])));
      if(!m||sec.id==='tfo-fee')return;const id='w'+i;
      const visibleH=h&&getComputedStyle(h).display!=='none';
      putStamp(sec,visibleH?'afterh2':'prepend',id,stampFor(m[1],m[2],m[3]));
    });
  }else{
    MAP_M.forEach(([sel,feed,key,max])=>{
      const host=sel.includes('>')?document.querySelector('#'+sel.replace('>',' > ')):document.getElementById(sel);if(!host)return;
      const shown=host.offsetParent!==null&&getComputedStyle(host).display!=='none';
      putStamp(host,'after','m-'+sel,shown?stampFor(feed,key,max):null);
    });
  }
}

/* ---------------------------------------------------------------- funder fee + 19% benchmark card */
let FUND=null,FUNDAT=0;
async function loadFunder(){try{const r=await nativeFetch('/api/funder',{cache:'no-store'});if(r.ok){FUND=await r.json();FUNDAT=Date.now()}}catch(e){}renderFee()}
function renderFee(){
  let host=document.getElementById('tfo-fee');
  if(!host){const sal=document.getElementById('salary-card');if(!sal)return;
    host=document.createElement('section');host.id='tfo-fee';host.className=PAGE==='m'?'tfo-fee card':'tfo-fee wide';host.setAttribute('aria-labelledby','tfo-fee-h');sal.after(host)}
  const f=FUND;
  if(!f||f.status!=='OK'){host.innerHTML=`<h2 id="tfo-fee-h" data-gterm="Funder fee">Funder fee &amp; 19% benchmark</h2><p class="tfo-muted">${f?'Funder fee record unavailable: '+esc(f.status):'Loading the funder fee record…'}</p>`;return}
  const r=f.rate_pct_day/100,bpre=19,bafter=(1.19*Math.pow(1-r,365)-1)*100;
  const t0=parseT(f.inception_utc),days=t0?(Date.now()-t0)/864e5:0,inc=f.inception_equity_usd;
  const benchGain=num(inc)&&days>0?inc*(Math.pow(1+bpre/100,days/365.25)-1):null;
  const si=f.since_inception||{},gross=si.pnl_gross_usd;
  const hist=(f.rate_history||[]).map(h=>`${esc(h.rate_pct_day)}%/day from the ${esc(h.from_sydney_fee_date)} charge`).join(' → ');
  const next=f.today&&parseT(f.today.next_accrual_utc),nextAmt=(f.day||{}).projected_fee_open_day_usd;
  const asof=parseT(f.equity_asof_utc||f.asof_utc);
  host.innerHTML=`<h2 id="tfo-fee-h" data-gterm="Funder fee">Funder fee · PAPER · flat daily 09:00 Sydney</h2>
   <div class="tfo-upd">${asof?`Equity as of ${esc(fmtT(asof))} · ${esc(ago(asof))} · fee record checked ${esc(fmtT(FUNDAT))}`:''}</div>
   <div class="tfo-grid">
    <div><span data-gterm="Rate">Rate now</span><b>${esc(f.rate_pct_day)}%/day</b><small>~${esc(f.rate_pct_yr_simple)}%/yr simple</small></div>
    <div><span data-gterm="Today's fee paid">Today's fee paid</span><b>${usd(f.today&&f.today.fee_paid_usd,2)}</b><small>${esc((f.today&&f.today.paid_at_sydney)||'')}</small></div>
    <div><span data-gterm="Total paid since 2 Oct">Total paid since 2 Oct</span><b>${usd(f.cum_fee_paid_usd,2)}</b><small>${next?'next ~'+usd(nextAmt,2)+' at '+esc(fmtT(next)):''}</small></div>
    <div><span data-gterm="Net equity vs HWM">Net equity vs HWM</span><b class="${(f.net_vs_hwm_usd||0)<0?'neg':'pos'}">${sgn(f.net_vs_hwm_usd,2)}</b><small>net ${usd(f.equity_net_usd,0)} · HWM display only</small></div>
    <div><span data-gterm="19% benchmark">19% benchmark</span><b>19%/yr before fee</b><small>≈ ${bafter.toFixed(2)}%/yr after the fee · pre-fee break-even ${esc(f.break_even_pre_fee_cagr_pct)}%</small></div>
    <div><span data-gterm="Benchmark">Since fee start vs 19%</span><b class="${num(gross)&&num(benchGain)&&gross-benchGain<0?'neg':'pos'}">${num(gross)&&num(benchGain)?sgn(gross-benchGain,0):'—'}</b><small>desk ${sgn(gross,0)} before fee vs benchmark ${sgn(benchGain,0)} over ${days.toFixed(1)} days</small></div>
   </div>
   <p class="tfo-muted">Rate history: ${hist}. Earlier charges stand at the rate in force. The benchmark is Brett's yardstick: 19% a year before the funder fee. <a href="/#tab=Performance">Equity chart with the 19% benchmark →</a></p>`;
}

/* ---------------------------------------------------------------- open <details> survive refreshes */
const OPEN=new Set(),SHUT=new Set();
const dkey=d=>{const s=d.querySelector(':scope > summary');const host=d.closest('[id]');return (host?host.id:'')+'|'+(s?clean(s.textContent).replace(/\d[\d,.]*/g,'#'):'')};
document.addEventListener('toggle',e=>{const d=e.target;if(!(d instanceof HTMLDetailsElement)||d.dataset.tfoRestoring)return;const k=dkey(d);if(d.open){OPEN.add(k);SHUT.delete(k)}else{SHUT.add(k);OPEN.delete(k)}},true);
function restoreDetails(){document.querySelectorAll('details').forEach(d=>{const k=dkey(d);if(OPEN.has(k)&&!d.open){d.dataset.tfoRestoring='1';d.open=true;setTimeout(()=>delete d.dataset.tfoRestoring,0)}})}

/* ---------------------------------------------------------------- header button, refresh on focus, scheduling */
function header(){
  const h=document.querySelector('header');if(!h||h.querySelector('.tfo-gbtn'))return;
  const b=document.createElement('button');b.type='button';b.className='tfo-gbtn';b.textContent='ⓘ Glossary';b.setAttribute('aria-label','Open the glossary');b.onclick=()=>openGlossary();
  if(PAGE==='m'){const lp=h.querySelector('.livepill');lp?lp.before(b):h.append(b)}else{const up=h.querySelector('#p-updated');up?up.before(b):h.append(b)}
}
let timer=null;function soon(){clearTimeout(timer);timer=setTimeout(pass,120)}
let busy=false;
function pass(){if(busy)return;busy=true;try{header();renderFeeIfMissing();decorate(document.body);stamps();restoreDetails()}finally{busy=false}}
function renderFeeIfMissing(){if(!document.getElementById('tfo-fee'))renderFee()}
const OURS='.tfo-upd,#tfo-gpop,#tfo-gdlg';
new MutationObserver(muts=>{if(busy)return;if(muts.some(m=>!(m.target.closest&&m.target.closest(OURS))&&![...m.addedNodes,...m.removedNodes].every(n=>n.nodeType===1&&(n.classList.contains('tfo-gi')||n.classList.contains('tfo-upd')))))soon()}).observe(document.documentElement,{childList:true,subtree:true});
document.addEventListener('click',e=>{if(e.target.closest&&e.target.closest('nav button'))setTimeout(stamps,60)});
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible')return;
  const age=Math.min(...['/api/all','/api/positions'].map(p=>FEEDS[p]?Date.now()-FEEDS[p].at:Infinity));
  if(age>30000){const fns=PAGE==='m'?['positions','overview']:['refresh','refreshPositions'];fns.forEach(n=>{try{typeof window[n]==='function'&&window[n]()}catch(e){}});loadFunder()}
});
setInterval(()=>{stamps()},15000);           // only the "x min ago" text changes; no panel is redrawn
setInterval(loadFunder,60000);setInterval(loadGlossary,600000);
function boot(){loadGlossary().then(()=>{if(location.hash==='#glossary')openGlossary()});loadFunder();soon()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
window.TFOL={openGlossary,lookup,feeds:FEEDS};
})();
