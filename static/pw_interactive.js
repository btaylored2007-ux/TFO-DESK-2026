/* pw_interactive.js: interactive, READ-ONLY layer for the TFO Private Wealth dashboard (PAPER).
   2026-10-03 dashboard_interactive. No order buttons, no POSTs: it only GETs /api/dashboard and /static/glossary.json.
   Hooks used by private_wealth_dashboard.html: TFOX.owns(tab), TFOX.render(tab,pw), TFOX.after(tab,pw), TFOX.tick(), TFOX.stamp(tab). */
(function(){
'use strict';
const POLL_MS=60000, OWN=['Performance','Holdings','Data health'];
const S={dash:null,dashAt:0,dashErr:null,gl:null,glIdx:null,pw:null,
  chart:{series:'firm',range:'1M',afterFee:true,bench:true},
  f:{sleeve:'',venue:'',q:''},psort:{k:'value_usd',d:-1},fsort:{k:'utc',d:-1},hsort:{k:'status',d:1},
  openPos:new Set(),openFill:new Set(),openCard:'',alertsOpen:false,lastKey:{},sortState:{},fillLimit:60};
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>typeof v==='number'&&Number.isFinite(v);
const usd=(v,d)=>num(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:d??0,minimumFractionDigits:d??0}).format(v):'—';
const usd2=v=>usd(v,2);
const usdK=v=>!num(v)?'—':Math.abs(v)>=1e6?'$'+(v/1e6).toFixed(2)+'M':Math.abs(v)>=1e3?'$'+(v/1e3).toFixed(1)+'k':'$'+v.toFixed(0);
const pct=(v,d)=>num(v)?(v>=0?'+':'')+v.toFixed(d??2)+'%':'—';
const px=v=>!num(v)?'—':Math.abs(v)>=1000?v.toLocaleString('en-US',{maximumFractionDigits:2}):Math.abs(v)>=1?v.toFixed(2):v.toPrecision(4);
const qty=v=>!num(v)?'—':Math.abs(v)>=100?Math.round(v).toLocaleString('en-US'):v.toLocaleString('en-US',{maximumFractionDigits:4});
const cls=v=>num(v)?(v>=0?'good':'bad'):'';
// 2026-10-28 (Brett t1674u): show times in WITA (Asia/Makassar, UTC+8), Brett's local zone.
const SYD=new Intl.DateTimeFormat('en-AU',{timeZone:'Asia/Makassar',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false});
const SYDD=new Intl.DateTimeFormat('en-AU',{timeZone:'Asia/Makassar',day:'2-digit',month:'short'});
const SYDT=new Intl.DateTimeFormat('en-AU',{timeZone:'Asia/Makassar',hour:'2-digit',minute:'2-digit',hour12:false});
const tms=v=>{const t=Date.parse(v);return Number.isNaN(t)?null:t};
const syd=v=>{const t=tms(v);return t==null?'—':SYD.format(new Date(t)).replace(',','')+' WITA'};
const ago=v=>{const t=tms(v);if(t==null)return '';let s=Math.max(0,(Date.now()-t)/1000);return s<90?Math.round(s)+' s ago':s<5400?Math.round(s/60)+' min ago':s<172800?(s/3600).toFixed(1)+' h ago':(s/86400).toFixed(1)+' d ago'};
const $=s=>document.querySelector(s);
const statusCls=s=>s==='FRESH'||s==='OK'?'good':s==='LAG'||s==='PENDING'||s==='DORMANT'?'warn':'bad';

/* ------------------------------------------------------------------ data */
async function loadDash(){
  try{const r=await fetch('/api/dashboard',{cache:'no-store',signal:AbortSignal.timeout(30000)});if(!r.ok)throw Error('HTTP '+r.status);
    S.dash=await r.json();S.dashAt=Date.now();S.dashErr=null}catch(e){S.dashErr=String(e.message||e)}
  if(typeof window.render==='function')window.render();else refreshOwn();
}
async function loadGlossary(){
  try{const r=await fetch('/static/glossary.json',{cache:'no-store'});S.gl=await r.json();indexGlossary()}catch(e){S.gl=null}
}
function indexGlossary(){
  const exact=new Map(),abbr=[],contains=[];
  (S.gl.terms||[]).forEach(t=>{[t.term,...(t.aliases||[])].forEach(a=>exact.set(norm(a),t));
    (t.abbr||[]).forEach(a=>abbr.push([new RegExp('(^|[^A-Za-z0-9])'+a.replace(/[.*+?^${}()|[\]\\&]/g,'\\$&')+'($|[^A-Za-z0-9])'),t]));
    (t.contains||[]).forEach(c=>contains.push([c.toLowerCase(),t]))});
  contains.sort((a,b)=>b[0].length-a[0].length);S.glIdx={exact,abbr,contains};
}
const norm=s=>String(s||'').replace(/&amp;/g,'&').replace(/\s+/g,' ').trim().toLowerCase();
function lookup(text){
  if(!S.glIdx)return null;const raw=String(text||'').replace(/\s+/g,' ').trim();if(!raw)return null;
  const n=norm(raw);const ix=S.glIdx;
  if(ix.exact.has(n))return ix.exact.get(n);
  const head=norm(raw.split(' · ')[0].replace(/\(.*?\)/g,''));if(ix.exact.has(head))return ix.exact.get(head);
  for(const [re,t] of ix.abbr)if(re.test(raw))return t;
  for(const [c,t] of ix.contains)if(n.includes(c))return t;
  return null;
}

/* ------------------------------------------------------------------ info icons + popover + glossary modal */
let pop=null,popPinned=false,popAnchor=null;
function ensurePop(){if(pop)return pop;pop=document.createElement('div');pop.id='gpop';pop.setAttribute('role','tooltip');document.body.append(pop);return pop}
function showPop(btn,pin){
  const t=(S.gl&&S.gl.terms||[]).find(x=>x.id===btn.dataset.gid);if(!t)return;ensurePop();
  pop.innerHTML=`<span class="gcat">${esc(t.category)}</span><b>${esc(t.term)}</b>${esc(t.short)}${t.long?`<p>${esc(t.long)}</p>`:''}<p><a href="#glossary" data-gopen="${esc(t.id)}">Open glossary</a></p>`;
  pop.style.display='block';const r=btn.getBoundingClientRect(),w=pop.offsetWidth,h=pop.offsetHeight;
  const vw=window.visualViewport?window.visualViewport.width:window.innerWidth,vh=window.visualViewport?window.visualViewport.height:window.innerHeight;let x=Math.min(Math.max(8,r.left+r.width/2-w/2),vw-w-8),y=r.bottom+8;if(y+h>vh-8)y=Math.max(8,r.top-h-8);
  pop.style.left=x+'px';pop.style.top=y+'px';popPinned=!!pin;popAnchor=btn;btn.setAttribute('aria-expanded','true');
}
function hidePop(){if(pop)pop.style.display='none';if(popAnchor)popAnchor.setAttribute('aria-expanded','false');popPinned=false;popAnchor=null}
const hoverable=()=>window.matchMedia&&window.matchMedia('(hover: hover)').matches;
document.addEventListener('click',e=>{
  const g=e.target.closest&&e.target.closest('.gi');
  if(g){e.preventDefault();e.stopPropagation();if(popAnchor===g&&popPinned)hidePop();else showPop(g,true);return}
  const o=e.target.closest&&e.target.closest('[data-gopen]');if(o){e.preventDefault();hidePop();openGlossary(o.dataset.gopen);return}
  if(pop&&!pop.contains(e.target))hidePop();
},true);
document.addEventListener('mouseover',e=>{const g=e.target.closest&&e.target.closest('.gi');if(g&&hoverable()&&!popPinned)showPop(g,false)});
document.addEventListener('mouseout',e=>{const g=e.target.closest&&e.target.closest('.gi');if(g&&!popPinned&&!(e.relatedTarget&&pop&&pop.contains(e.relatedTarget)))hidePop()});
document.addEventListener('focusin',e=>{if(e.target.classList&&e.target.classList.contains('gi')&&!popPinned)showPop(e.target,false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hidePop()});
window.addEventListener('scroll',()=>{if(!popPinned)hidePop()},{passive:true});
function iconFor(text){const t=lookup(text);return t?`<button type="button" class="gi" data-gid="${esc(t.id)}" aria-label="What does ${esc(t.term)} mean?" aria-expanded="false">ⓘ</button>`:''}
function decorate(root){
  if(!S.glIdx||!root)return;
  root.querySelectorAll('h2,h3,th,.metric>.muted:first-child,[data-gterm]').forEach(el=>{
    if(el.querySelector(':scope > .gi'))return;
    const text=el.dataset.gterm||Array.from(el.childNodes).filter(n=>!(n.classList&&(n.classList.contains('upd')||n.classList.contains('gi')||n.classList.contains('sarr')||n.classList.contains('status')))).map(n=>n.textContent).join(' ');
    const ic=iconFor(text);if(!ic)return;
    const tmp=document.createElement('span');tmp.innerHTML=ic;const b=tmp.firstChild;
    const anchor=Array.from(el.childNodes).find(n=>n.classList&&(n.classList.contains('upd')||n.classList.contains('sarr')));
    anchor?el.insertBefore(b,anchor):el.append(b);
  });
}
function openGlossary(focusId){
  let d=$('#gdlg');if(!d){d=document.createElement('dialog');d.id='gdlg';d.setAttribute('aria-label','Glossary');
    d.innerHTML=`<div class="ghead"><b>Glossary</b><input type="search" id="gq" placeholder="Search terms (e.g. NAV, kill, trail)" aria-label="Search glossary"><button type="button" class="hbtn" id="gclose">Close</button></div><div class="gbody" id="gbody"></div>`;
    document.body.append(d);d.querySelector('#gclose').onclick=()=>d.close();d.addEventListener('click',e=>{if(e.target===d)d.close()});
    d.querySelector('#gq').oninput=e=>fillGlossary(e.target.value)}
  fillGlossary('');if(typeof d.showModal==='function')d.showModal();else d.setAttribute('open','');
  if(focusId){const el=document.getElementById('g-'+focusId);if(el){el.scrollIntoView({block:'center'});el.style.background='#fff8e6'}}
}
function fillGlossary(q){
  const terms=(S.gl&&S.gl.terms)||[],n=norm(q);const by={};
  terms.filter(t=>!n||norm(t.term+' '+t.short+' '+(t.aliases||[]).join(' ')).includes(n)).forEach(t=>(by[t.category]=by[t.category]||[]).push(t));
  $('#gbody').innerHTML=Object.keys(by).sort().map(c=>`<h3>${esc(c)}</h3><dl>${by[c].sort((a,b)=>a.term.localeCompare(b.term)).map(t=>`<dt id="g-${esc(t.id)}">${esc(t.term)}${t.aliases&&t.aliases.length?` <small class="muted">(${esc(t.aliases.slice(0,4).join(', '))})</small>`:''}</dt><dd>${esc(t.short)}${t.long?' '+esc(t.long):''}</dd>`).join('')}</dl>`).join('')||'<p class="muted">No matching terms.</p>';
}
window.addEventListener('hashchange',()=>{if(location.hash==='#glossary')openGlossary()});

/* ------------------------------------------------------------------ "last updated" stamps on every panel */
function stampHTML(iso,status){if(!iso)return `<span class="upd">Reference text · no live data</span>`;const a=(Date.now()-tms(iso))/1000;
  return `<span class="upd ${status==='STALE'?'stale':status==='LAG'?'lag':''}" data-asof="${esc(iso)}">Updated ${esc(syd(iso))} · <span class="ago">${esc(ago(iso))}</span></span>`}
function fr(id){return ((S.dash&&S.dash.freshness)||[]).find(r=>r.id===id)||{}}
function asofFor(h,tab,pw){
  const d=pw||{},t=h.toLowerCase(),B=k=>(((d.boards||{})[k]||{}).freshness||{}).utc,fw=(d.deep18_fwd&&d.deep18_fwd.tests||[]).map(x=>x.updated_utc).filter(Boolean).sort().pop();
  if(/funder fee/.test(t))return [(d.funder||{}).equity_asof_utc||(d.funder||{}).asof_utc,fr('funder').status];
  if(/deep18 trend|trend sleeve/.test(t))return [(d.trend18||{}).updated,fr('trend18').status];
  if(/round-3|forward test|whole-book|overlays on/.test(t))return [fw,fr('deep18_fwd').status];
  if(/unproven shadows|near-miss (shadow|\/ unproven)/.test(t))return [(d.deep19_fwd&&d.deep19_fwd.tests||[]).map(x=>x.updated_utc).filter(Boolean).sort().pop()]; // 2026-10-03 DEEP19 near-miss shadows
  if(/shadow/.test(t))return [(d.shadow||{}).last_mark_utc||(d.shadow||{}).updated_utc,fr('shadow').status];
  if(/daily paper target/.test(t))return [(d.target_24h||{}).asof];
  if(/capital and p&l|all local books|powder|known booked/.test(t))return [(d.account_summary||{}).asof];
  if(/observed tape/.test(t))return [(d.watchdog||{}).utc];
  if(/hunt|opportunit/.test(t))return [(d.hunt_latest||{}).asof||B('Hunt')];
  if(/position/.test(t)&&tab==='Positions')return [B('Positions')];
  if(/authority|mandate|amendment|constraints/.test(t))return [null];
  if(/research/.test(t))return [B('Research')];
  if(/book/.test(t)&&tab==='Books')return [B('Books')];
  return [(d.cycle||{}).utc||B(tab)||d.utc];
}
function stampSections(root,tab,pw){
  if(!root)return;
  root.querySelectorAll('section, #content > h2:first-child').forEach(sec=>{
    const h=sec.matches('h2')?sec:sec.querySelector('h2,h3');if(!h||h.querySelector('.upd'))return;
    const [iso,st]=asofFor(h.textContent,tab,pw);h.insertAdjacentHTML('beforeend',stampHTML(iso,st));
  });
}

/* ------------------------------------------------------------------ generic sortable tables (existing tabs) + <details> memory */
const cellVal=s=>{s=String(s||'').trim();const m=s.replace(/[$,%+\s]|A\$|US\$/g,'').replace(/^−/,'-');const n=parseFloat(m);
  if(/^[-+]?\$?[\d,.]+(\.\d+)?%?$/.test(s.replace(/\s/g,''))||(!Number.isNaN(n)&&/^-?[\d.]+$/.test(m)))return n;const t=Date.parse(s.replace(' Syd',''));return Number.isNaN(t)?s.toLowerCase():t};
function sortable(root){
  if(!root)return;root.querySelectorAll('table:not(.xt)').forEach(tb=>{
    const ths=Array.from(tb.querySelectorAll('thead th'));if(!ths.length)return;const key=ths.map(th=>th.textContent.replace('ⓘ','').trim()).join('|');
    ths.forEach((th,i)=>{th.classList.add('sortable');th.setAttribute('aria-sort','none');th.tabIndex=0;th.title='Sort';
      const go=()=>{const cur=S.sortState[key];S.sortState[key]={i,d:cur&&cur.i===i?-cur.d:1};apply(tb,key)};
      th.onclick=e=>{if(e.target.closest('.gi'))return;go()};th.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}}});
    if(S.sortState[key])apply(tb,key);
  });
  function apply(tb,key){const st=S.sortState[key],body=tb.tBodies[0];if(!st||!body)return;
    const rows=Array.from(body.rows);rows.sort((a,b)=>{const x=cellVal(a.cells[st.i]&&a.cells[st.i].textContent),y=cellVal(b.cells[st.i]&&b.cells[st.i].textContent);
      return (typeof x===typeof y?(x<y?-1:x>y?1:0):(typeof x==='number'?-1:1))*st.d});rows.forEach(r=>body.append(r));
    tb.querySelectorAll('thead th').forEach((th,i)=>{th.setAttribute('aria-sort',i===st.i?(st.d>0?'ascending':'descending'):'none');let a=th.querySelector('.sarr');if(!a){a=document.createElement('span');a.className='sarr';th.append(a)}a.textContent=i===st.i?(st.d>0?'▲':'▼'):''})}
}
let openDetails=new Set();
function rememberDetails(){openDetails=new Set(Array.from(document.querySelectorAll('#content details[open] > summary')).map(s=>s.textContent.trim()))}
function restoreDetails(){document.querySelectorAll('#content details > summary').forEach(s=>{if(openDetails.has(s.textContent.trim()))s.parentElement.open=true})}

/* ------------------------------------------------------------------ alerts strip */
function renderAlerts(pw){
  const box=$('#alertstrip');if(!box)return;
  const al=((S.dash&&S.dash.alerts)||[]).slice();
  ((pw&&pw.alerts)||[]).forEach(a=>al.push({severity:'warn',source:'Desk health',text:a.message,utc:null}));
  const k=S.dash&&S.dash.kill||{},t18=k.trend18||{},firm=k.firm||{},frozen=(k.books||[]).filter(b=>b.frozen).length;
  const killChip=`<span class="al ${firm.new_entries_halted||frozen||t18.active?'bad':'good'}" data-gterm="Kill rule">Kill: firm ${esc(firm.level||'?')}${firm.new_entries_halted?' · entries HALTED':''} · ${frozen} book${frozen===1?'':'s'} frozen · Trend18 ${t18.active?'KILLED':'armed'}</span>`;
  const refusals=al.filter(a=>/refus/i.test(a.source+' '+a.text)).length;
  const sevN=s=>al.filter(a=>a.severity===s).length;
  const key=JSON.stringify([al,killChip,S.alertsOpen]);if(S.lastKey.alerts===key)return;S.lastKey.alerts=key;
  const top=al.filter(a=>a.severity!=='info').slice(0,3);
  box.innerHTML=`<div class="astrip" role="region" aria-label="Alerts"><b data-gterm="Alerts">Alerts</b>${killChip}<span class="al ${refusals?'warn':'good'}" data-gterm="Mark refusal">Refusals/mark issues: ${refusals}</span>
   ${sevN('bad')?`<span class="al bad">${sevN('bad')} critical</span>`:''}${sevN('warn')?`<span class="al warn">${sevN('warn')} warning${sevN('warn')>1?'s':''}</span>`:''}${sevN('info')?`<span class="al info">${sevN('info')} info</span>`:''}
   ${!al.length?'<span class="al good">No open alerts</span>':''}
   ${top.map(a=>`<span class="al ${esc(a.severity)}" title="${esc(a.source)}">${esc(a.text.length>110?a.text.slice(0,108)+'…':a.text)}</span>`).join('')}
   ${al.length?`<button type="button" class="linkbtn" id="alerttoggle" aria-expanded="${S.alertsOpen}">${S.alertsOpen?'Hide':'Show all '+al.length}</button>`:''}</div>
   ${S.alertsOpen?`<div class="alist">${al.map(a=>`<div><span class="pill ${a.severity==='bad'?'bad':a.severity==='warn'?'warn':'muted'}">${esc(a.severity.toUpperCase())}</span> <b>${esc(a.source)}</b> · ${esc(a.text)}${a.utc?` <small class="muted">${esc(syd(a.utc))}</small>`:''}</div>`).join('')}</div>`:''}`;
  const tg=$('#alerttoggle');if(tg)tg.onclick=()=>{S.alertsOpen=!S.alertsOpen;renderAlerts(S.pw)};decorate(box);
}

/* ------------------------------------------------------------------ chart */
const RANGES={'1D':864e5,'1W':6048e5,'1M':2592e6,'3M':7776e6,'ALL':Infinity};
function feeFactor(t){ // pro-forma fee factor: product of (1 - daily rate x stub) for every fee boundary at or before t
  const h=(S.dash&&S.dash.fee&&S.dash.fee.history)||[];let f=1;h.forEach(x=>{const b=tms(x.boundary_utc);if(b!=null&&b<=t&&num(x.rate))f*=1-x.rate*(num(x.stub_fraction)?x.stub_fraction:1)});return f}
function feesPaidBy(t){const h=(S.dash&&S.dash.fee&&S.dash.fee.history)||[];return h.reduce((s,x)=>{const b=tms(x.boundary_utc);return b!=null&&b<=t&&num(x.fee_usd)?s+x.fee_usd:s},0)}
function chartData(){
  const ser=(S.dash&&S.dash.series||{})[S.chart.series];if(!ser||!ser.points||!ser.points.length)return null;
  const all=ser.points.map(p=>({t:tms(p[0]),v:p[1],ev:p[2]===1,af:p[3]})).filter(p=>p.t!=null);
  const span=RANGES[S.chart.range],end=all[all.length-1].t,start=span===Infinity?all[0].t:end-span;
  let pts=all.filter(p=>p.t>=start);const before=all.filter(p=>p.t<start).pop();if(before&&pts.length<2)pts.unshift(before);if(!pts.length)pts=[all[all.length-1]];
  const r=(S.dash.benchmark||{}).pre_fee_cagr_pct||19,t0=pts[0].t,v0=pts[0].v;
  // benchmark: starts at the range-start equity, compounds at r%/yr, and receives the same dollar flows at each capital step
  let bench=v0;pts.forEach((p,i)=>{p.after=ser.after_fee==='actual'?p.v-feesPaidBy(p.t):num(p.af)?p.af:p.v*feeFactor(p.t);
    if(i){bench*=Math.pow(1+r/100,(p.t-pts[i-1].t)/31557600000);if(p.ev)bench+=p.v-pts[i-1].v}p.bench=bench});
  // 2026-10-28 (Brett t1674u): a deposit/transfer step must not read as return or flatten the scale. When the range has
  // capital steps, plot performance ex-deposits: range-start equity chained by print-to-print returns, skipping steps.
  const hasEv=pts.slice(1).some(p=>p.ev);let ix=1,ixa=1,bx=v0;
  pts.forEach((p,i)=>{if(i&&!p.ev&&num(p.v)&&num(pts[i-1].v)&&pts[i-1].v>0){ix*=p.v/pts[i-1].v;if(num(p.after)&&num(pts[i-1].after)&&pts[i-1].after>0)ixa*=p.after/pts[i-1].after}
    if(i)bx*=Math.pow(1+r/100,(p.t-pts[i-1].t)/31557600000);p.px=v0*ix;p.pxa=v0*ixa;p.pxb=bx});
  return {ser,pts,r,hasEv};
}
function drawChart(host,compact){
  const cd=chartData();if(!cd){host.innerHTML='<p class="empty">No history for this series yet.</p>';return null}
  const {pts}=cd,W=960,H=compact?240:340,L=70,Rm=16,T=14,Bm=30;
  const rok=cd.ser.returns_ok!==false,xf=cd.hasEv&&rok,lines=[[xf?'px':'v','var(--green)','',true],[xf?'pxa':'after','#7a4fb5','6,4',S.chart.afterFee&&rok],[xf?'pxb':'bench','#6b7780','2,4',S.chart.bench&&rok]].filter(l=>l[3]);
  let mn=Infinity,mx=-Infinity;pts.forEach(p=>lines.forEach(l=>{const y=p[l[0]];if(num(y)){mn=Math.min(mn,y);mx=Math.max(mx,y)}}));
  if(mx-mn<1){mx+=1;mn-=1}const pad=(mx-mn)*.08;mn-=pad;mx+=pad;
  const t0=pts[0].t,t1=pts[pts.length-1].t,dt=Math.max(1,t1-t0);
  const X=t=>L+(W-L-Rm)*(pts.length<2?.5:(t-t0)/dt),Y=v=>T+(H-T-Bm)*(1-(v-mn)/(mx-mn));
  const path=k=>pts.filter(p=>num(p[k])).map((p,i)=>(i?'L':'M')+X(p.t).toFixed(1)+','+Y(p[k]).toFixed(1)).join('');
  let g='';for(let i=0;i<=4;i++){const v=mn+(mx-mn)*i/4,y=Y(v);g+=`<line x1="${L}" x2="${W-Rm}" y1="${y}" y2="${y}" stroke="#e6eaec"/><text x="${L-8}" y="${y+4}" text-anchor="end" font-size="11" fill="#6b7780">${esc(usdK(v))}</text>`}
  const nx=compact?4:6;for(let i=0;i<=nx;i++){const t=t0+dt*i/nx,x=X(t);const lab=dt<2*864e5?SYDT.format(new Date(t)):SYDD.format(new Date(t))+(dt<15*864e5?' '+SYDT.format(new Date(t)):'');g+=`<text x="${x}" y="${H-10}" text-anchor="${i===0?'start':i===nx?'end':'middle'}" font-size="11" fill="#6b7780">${esc(lab)}</text>`}
  const ev=pts.filter(p=>p.ev).map(p=>`<path d="M${X(p.t)},${H-Bm} l-4,7 h8z" fill="#c08a00"><title>Capital step (deposit/transfer) ${esc(syd(new Date(p.t).toISOString()))}</title></path>`).join('');
  host.innerHTML=`<div class="chartwrap"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cd.ser.label)} equity chart">${g}${ev}${lines.map(l=>`<path d="${path(l[0])}" fill="none" stroke="${l[1]}" stroke-width="${l[0]==='v'||l[0]==='px'?2:1.6}" ${l[2]?`stroke-dasharray="${l[2]}"`:''}/>`).join('')}
   <line class="hx" x1="0" x2="0" y1="${T}" y2="${H-Bm}" stroke="#9aa6ad" stroke-dasharray="3,3" visibility="hidden"/>${lines.map(l=>`<circle class="hd" data-k="${l[0]}" r="4" fill="${l[1]}" visibility="hidden"/>`).join('')}
   <rect x="${L}" y="${T}" width="${W-L-Rm}" height="${H-T-Bm}" fill="transparent" class="hov"/></svg><div class="tip"></div></div>
   <div class="legend"><span><i style="border-color:var(--green)"></i>${xf?'Performance ex-deposits (range-start equity × time-weighted return; deposits/transfers removed)':'Equity after costs (before fee)'}</span>${S.chart.afterFee&&rok?'<span><i style="border-color:#7a4fb5;border-top-style:dashed"></i>After funder fee'+(cd.ser.after_fee==='actual'?' (fees actually paid)':' (pro-forma, fee from 2 Oct)')+'</span>':''}${S.chart.bench&&rok?`<span><i style="border-color:#6b7780;border-top-style:dotted"></i>Benchmark ${cd.r}%/yr before fee (starts at range-start equity; ${xf?'no flows':'adds the same capital flows'})</span>`:''}${pts.some(p=>p.ev)?'<span style="color:#c08a00">▲ capital event (deposit/transfer)</span>':''}</div>`;
  const svg=host.querySelector('svg'),tip=host.querySelector('.tip'),hx=svg.querySelector('.hx'),wrap=host.querySelector('.chartwrap');
  const move=e=>{const r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)*W/r.width;let t=t0+(sx-L)/(W-L-Rm)*dt;let lo=0,hi=pts.length-1;while(hi-lo>1){const m=(lo+hi)>>1;pts[m].t<t?lo=m:hi=m}
    const p=Math.abs(pts[lo].t-t)<Math.abs(pts[hi].t-t)?pts[lo]:pts[hi];const x=X(p.t);hx.setAttribute('x1',x);hx.setAttribute('x2',x);hx.setAttribute('visibility','visible');
    svg.querySelectorAll('.hd').forEach(c=>{const v=p[c.dataset.k];if(num(v)){c.setAttribute('cx',x);c.setAttribute('cy',Y(v));c.setAttribute('visibility','visible')}});
    tip.innerHTML=`<b>${esc(syd(new Date(p.t).toISOString()))}</b><br>Equity ${esc(usd2(p.v))}${xf?'<br>Ex-deposits '+esc(usd2(p.px)):''}${S.chart.afterFee&&rok?'<br>After fee '+esc(usd2(p.after)):''}${S.chart.bench&&rok?'<br>Benchmark '+esc(usd2(p.bench))+' (equity '+esc(pct((p.v/p.bench-1)*100))+' vs it)':''}<br>vs range start ${esc(pct((p.v/pts[0].v-1)*100))} (incl. flows)${p.ev?'<br>▲ capital event':''}`;
    tip.style.display='block';const wr=wrap.getBoundingClientRect(),px_=(x/W)*r.width+(r.left-wr.left);tip.style.left=Math.min(Math.max(4,px_+12),wr.width-tip.offsetWidth-4)+'px';tip.style.top='10px'};
  const out=()=>{tip.style.display='none';hx.setAttribute('visibility','hidden');svg.querySelectorAll('.hd').forEach(c=>c.setAttribute('visibility','hidden'))};
  const hov=svg.querySelector('.hov');hov.addEventListener('pointermove',move);hov.addEventListener('pointerdown',move);hov.addEventListener('pointerleave',out);
  return cd;
}
function twr(p,k){ // time-weighted index: chains print-to-print returns, skipping capital steps (deposits/transfers)
  let idx=1;const out=[1];for(let i=1;i<p.length;i++){if(!p[i].ev&&num(p[i][k])&&num(p[i-1][k])&&p[i-1][k]>0)idx*=p[i][k]/p[i-1][k];out.push(idx)}return out}
function chartStats(cd){
  if(cd&&cd.ser.returns_ok===false){const p=cd.pts,a=p[0],b=p[p.length-1];return `<div class="stats"><div><span>Start (${esc(syd(new Date(a.t).toISOString()))})</span><b>${esc(usd(a.v))}</b></div><div><span>End</span><b>${esc(usd(b.v))}</b></div><div><span>Change ($)</span><b>${esc(usd(b.v-a.v))}</b></div></div><p class="warn">${esc(cd.ser.note||'Returns are not meaningful for this series.')}</p>`}
  if(!cd)return '';const p=cd.pts,a=p[0],b=p[p.length-1],days=(b.t-a.t)/864e5,ch=b.v-a.v;
  let dep=0;for(let i=1;i<p.length;i++)if(p[i].ev&&num(p[i].v)&&num(p[i-1].v))dep+=p[i].v-p[i-1].v;const mkt=ch-dep;
  const tv=twr(p,'v'),ta=twr(p,'after'),r=tv[tv.length-1],ra=ta[ta.length-1],chp=(r-1)*100;
  let peak=-Infinity,mdd=0;tv.forEach(x=>{peak=Math.max(peak,x);mdd=Math.min(mdd,(x/peak-1)*100)});
  const bret=Math.pow(1+cd.r/100,days/365.25)-1,evn=p.filter(x=>x.ev).length,ann=days>=7?(Math.pow(r,365/days)-1)*100:null;
  return `<div class="stats"><div><span>Start (${esc(syd(new Date(a.t).toISOString()))})</span><b>${esc(usd(a.v))}</b></div><div><span>End</span><b>${esc(usd(b.v))}</b></div>
   <div><span>Market P/L (US$, ex-deposits)</span><b class="${cls(mkt)}">${esc(usd(mkt))}</b></div>${dep?`<div><span>Deposits / transfers in (US$, not profit)</span><b>${esc(usd(dep))}</b></div>`:''}<div><span>Return before fee${evn?' (time-weighted)':''}</span><b class="${cls(chp)}">${esc(pct(chp))}</b></div><div><span>Return after fee${evn?' (time-weighted)':''}</span><b class="${cls(ra-1)}">${esc(pct((ra-1)*100))}</b></div>
   <div><span>Benchmark ${cd.r}%/yr over range</span><b>${esc(pct(bret*100))}</b></div><div><span>vs benchmark (before fee)</span><b class="${cls(r-1-bret)}">${esc(pct((r-1-bret)*100))} pts</b></div>
   <div><span>Max drawdown in range</span><b class="${mdd<0?'bad':''}">${esc(pct(mdd))}</b></div><div><span>Annualised${days<7?' (needs ≥7 days)':''}</span><b>${ann==null?'—':esc(pct(ann,1))}</b></div></div>
   ${evn?`<p class="warn">${evn} capital step${evn>1?'s':''} (▲ deposit or transfer) in this range. Returns, drawdown, the benchmark and the chart line are time-weighted: those steps are excluded so money moved in or out is not counted as profit or loss. Deposits are shown separately above; the market move inside a step's print interval is counted with the deposit.</p>`:''}${cd.ser.hidden_spikes?`<p class="muted">${cd.ser.hidden_spikes} one-print spike${cd.ser.hidden_spikes>1?'s':''} (transfer double-counts later corrected) hidden from this chart; the source file is unchanged.</p>`:''}`;
}
function chartControls(){
  const ser=(S.dash&&S.dash.series)||{};
  return `<div class="toolbar"><label>Series <select id="xser">${Object.keys(ser).map(k=>`<option value="${esc(k)}" ${k===S.chart.series?'selected':''}>${esc(ser[k].label)}</option>`).join('')}</select></label>
   <span class="seg" role="group" aria-label="Time range">${Object.keys(RANGES).map(r=>`<button type="button" data-range="${r}" aria-pressed="${r===S.chart.range}">${r==='ALL'?'All':r}</button>`).join('')}</span>
   <label class="tog"><input type="checkbox" id="xaf" ${S.chart.afterFee?'checked':''}> After fee</label><label class="tog"><input type="checkbox" id="xbm" ${S.chart.bench?'checked':''}> 19% benchmark</label></div>`;
}
function wireChart(root,compact){
  const host=root.querySelector('.chost');const cd=drawChart(host,compact);const st=root.querySelector('.cstats');if(st)st.innerHTML=chartStats(cd);
  const sel=root.querySelector('#xser');if(sel)sel.onchange=e=>{S.chart.series=e.target.value;rerender()};
  root.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{S.chart.range=b.dataset.range;rerender()});
  const af=root.querySelector('#xaf');if(af)af.onchange=e=>{S.chart.afterFee=e.target.checked;rerender()};
  const bm=root.querySelector('#xbm');if(bm)bm.onchange=e=>{S.chart.bench=e.target.checked;rerender()};
}

/* ------------------------------------------------------------------ positions / fills */
function filt(rows){const q=norm(S.f.q);return rows.filter(r=>(!S.f.sleeve||r.sleeve===S.f.sleeve)&&(!S.f.venue||r.venue===S.f.venue)&&(!q||norm([r.symbol,r.book,r.sleeve,r.venue,r.action,r.theme].join(' ')).includes(q)))}
function sortRows(rows,s){return rows.slice().sort((a,b)=>{const x=a[s.k],y=b[s.k];if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;return (x<y?-1:x>y?1:0)*s.d})}
function th(label,k,s,numc,which){const on=s.k===k;return `<th class="sortable${numc?' num':''}" data-sort="${k}" data-which="${which}" tabindex="0" aria-sort="${on?(s.d>0?'ascending':'descending'):'none'}">${esc(label)}<span class="sarr">${on?(s.d>0?'▲':'▼'):''}</span></th>`}
function filterBar(){
  const d=S.dash||{},rows=(d.positions||[]).concat(d.fills||[]);const sl=[...new Set(rows.map(r=>r.sleeve).filter(Boolean))].sort(),vn=[...new Set(rows.map(r=>r.venue).filter(Boolean))].sort();
  return `<div class="toolbar"><label data-gterm="Sleeve">Sleeve <select id="xsl"><option value="">All sleeves</option>${sl.map(x=>`<option ${x===S.f.sleeve?'selected':''}>${esc(x)}</option>`).join('')}</select></label>
   <label data-gterm="Venue">Venue <select id="xvn"><option value="">All venues</option>${vn.map(x=>`<option ${x===S.f.venue?'selected':''}>${esc(x)}</option>`).join('')}</select></label>
   <label>Search <input type="search" id="xq" value="${esc(S.f.q)}" placeholder="Symbol, book, theme"></label>${S.f.sleeve||S.f.venue||S.f.q?'<button type="button" class="linkbtn" id="xclr">Clear filters</button>':''}</div>`;
}
function railSummary(r){const x=r.rail;if(!x)return '—';if(x.variant==='trend')return 'Trend rebalance';
  const lv=num(x.level_usd)&&num(r.mark_usd)?' · '+pct((r.mark_usd/x.level_usd-1)*100,1)+' above rail':'';
  return `${x.variant} · ${x.half_banked?'half banked, trail '+(num(x.trail_pct)?(x.trail_pct*100).toFixed(1)+'%':'')+' @ '+px(x.level_usd):'stop '+px(x.stop_usd)+' / half '+px(x.half_usd)}${lv}`}
function posDetail(r){
  const x=r.rail||{},fills=((S.dash||{}).fills||[]).filter(f=>f.symbol===r.symbol);
  return `<div class="kv"><div><span>Exit rule</span>${esc(x.rule||'—')}</div><div><span>Variant</span>${esc(x.variant||'—')}</div><div><span>Stop (-5% from fill)</span>${esc(px(x.stop_usd))}</div>
   <div><span>Half-take target</span>${esc(px(x.half_usd))} ${x.half_banked?'<b class="good">banked</b>':'not yet'}</div><div><span>Current rail level</span>${esc(px(x.level_usd))}</div>
   <div><span>Trail</span>${num(x.trail_pct)?esc((x.trail_pct*100).toFixed(2)+'% off high '+px(x.trail_wm_usd)):'not active until the half'}</div><div><span>ATR14</span>${num(x.atr14_pct)?esc((x.atr14_pct*100).toFixed(2)+'%'):'—'}</div>
   <div><span>Max hold due</span>${esc(syd(x.max_hold_due_utc))}</div><div><span>Held</span>${num(x.held_days)?esc(x.held_days.toFixed(1)+' days'):'—'}</div>
   <div><span>Rail runner</span>${esc(x.status||'—')} · ${esc(x.why||'')} ${x.checked_utc?'('+esc(syd(x.checked_utc))+')':''}</div>
   <div><span>Entry</span>${esc(syd(r.entry_utc))} · ${esc(px(r.entry_usd))} · ticket ${esc(r.ticket_id||'—')}</div><div><span>Entry costs</span>${esc(usd2(r.entry_fees_usd))}</div>
   <div><span>Mark</span>${esc(px(r.mark_usd))} · ${esc(syd(r.mark_utc))} · ${esc(r.mark_src||'')}</div><div><span>Theme / label</span>${esc(r.theme||'—')} / ${esc(r.label||'—')}</div></div>
   ${r.why?`<p class="muted" style="margin:8px 0 0">Why: ${esc(r.why)}</p>`:''}
   ${fills.length?`<h3 style="margin:12px 0 4px;font-size:14px">Fills with costs (${fills.length})</h3>${fillsMini(fills)}`:''}`;
}
function fillsMini(fs){return `<div class="scroll"><table class="xt"><thead><tr><th>Time</th><th>Action</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Principal</th><th class="num">Fees</th><th class="num">Cost bp</th><th class="num">P&L</th><th>Exit rule</th></tr></thead><tbody>${fs.map(f=>`<tr><td>${esc(syd(f.utc))}</td><td>${esc(f.action)}</td><td class="num">${esc(qty(f.qty))}</td><td class="num">${esc(px(f.px_usd))}</td><td class="num">${esc(usd(f.principal_usd))}</td><td class="num">${esc(usd2(f.fees_usd))}</td><td class="num">${esc(num(f.cost_bp)?f.cost_bp.toFixed(1):'—')}</td><td class="num ${cls(f.pnl_usd)}">${esc(usd(f.pnl_usd))}</td><td>${esc(f.exit_rule||'')}</td></tr>`).join('')}</tbody></table></div>`}
function positionsTable(rows,opts){
  opts=opts||{};const s=S.psort,cols=[['Symbol','symbol'],['Sleeve','sleeve'],['Venue','venue'],['Side','side'],['Qty','units',1],['Entry','entry_usd',1],['Mark','mark_usd',1],['Value','value_usd',1],['Weight %','weight_pct',1],['Unrealised','upnl_usd',1],['Unrealised %','upnl_pct',1],['Rails','rail_s'],['Mark time','mark_utc']];
  rows=sortRows(rows.map(r=>Object.assign({rail_s:railSummary(r)},r)),s);
  const tv=rows.reduce((a,r)=>a+(r.value_usd||0),0),tu=rows.reduce((a,r)=>a+(r.upnl_usd||0),0);
  if(!rows.length)return '<p class="empty">No open positions match these filters.</p>';
  return `<div class="scroll"><table class="xt" data-kind="pos"><thead><tr>${cols.map(c=>th(c[0],c[1],s,c[2],'p')).join('')}</tr></thead><tbody>${rows.map(r=>{const id=r.book+':'+r.symbol,open=S.openPos.has(id);
   return `<tr class="row" tabindex="0" data-pid="${esc(id)}" aria-expanded="${open}"><td><span class="chev">${open?'▾':'▸'}</span>${esc(r.symbol)}</td><td>${esc(r.sleeve)}</td><td>${esc(r.venue)}</td><td>${esc(r.side)}</td><td class="num">${esc(qty(r.units))}</td><td class="num">${esc(px(r.entry_usd))}</td><td class="num">${esc(px(r.mark_usd))}</td><td class="num">${esc(usd(r.value_usd))}</td><td class="num">${esc(num(r.weight_pct)?r.weight_pct.toFixed(1):'—')}</td><td class="num ${cls(r.upnl_usd)}">${esc(usd(r.upnl_usd))}</td><td class="num ${cls(r.upnl_pct)}">${esc(pct(r.upnl_pct))}</td><td>${esc(r.rail_s)}</td><td>${esc(syd(r.mark_utc))}</td></tr>${open?`<tr class="detail"><td colspan="${cols.length}">${posDetail(r)}</td></tr>`:''}`}).join('')}
   <tr class="sum"><td colspan="7">Total (${rows.length})</td><td class="num">${esc(usd(tv))}</td><td></td><td class="num ${cls(tu)}">${esc(usd(tu))}</td><td colspan="3"></td></tr></tbody></table></div>`;
}
function fillsTable(rows){
  const s=S.fsort,cols=[['Time','utc'],['Sleeve','sleeve'],['Venue','venue'],['Symbol','symbol'],['Action','action'],['Qty','qty',1],['Price','px_usd',1],['Principal','principal_usd',1],['Proceeds','proceeds_usd',1],['Fees','fees_usd',1],['Cost bp','cost_bp',1],['P&L','pnl_usd',1],['Exit rule','exit_rule']];
  rows=sortRows(rows,s);const all=rows.length;const shown=rows.slice(0,S.fillLimit);
  if(!all)return '<p class="empty">No fills match these filters.</p>';
  const tf=rows.reduce((a,r)=>a+(r.fees_usd||0),0),tp=rows.reduce((a,r)=>a+(r.pnl_usd||0),0);
  return `<div class="scroll"><table class="xt" data-kind="fill"><thead><tr>${cols.map(c=>th(c[0],c[1],s,c[2],'f')).join('')}</tr></thead><tbody>${shown.map(f=>{const id=(f.ticket_id||'')+':'+f.action+':'+f.utc,open=S.openFill.has(id),fd=f.fee_detail||{};
   return `<tr class="row" tabindex="0" data-fid="${esc(id)}" aria-expanded="${open}"><td><span class="chev">${open?'▾':'▸'}</span>${esc(syd(f.utc))}</td><td>${esc(f.sleeve)}</td><td>${esc(f.venue)}</td><td>${esc(f.symbol)}</td><td>${esc(f.action)}</td><td class="num">${esc(qty(f.qty))}</td><td class="num">${esc(px(f.px_usd))}</td><td class="num">${esc(usd(f.principal_usd))}</td><td class="num">${esc(usd(f.proceeds_usd))}</td><td class="num">${esc(usd2(f.fees_usd))}</td><td class="num">${esc(num(f.cost_bp)?f.cost_bp.toFixed(1):'—')}</td><td class="num ${cls(f.pnl_usd)}">${esc(usd(f.pnl_usd))}</td><td>${esc(f.exit_rule||'')}</td></tr>${open?`<tr class="detail"><td colspan="${cols.length}"><div class="kv"><div><span>Ticket</span>${esc(f.ticket_id)}</div><div><span>Fee breakdown</span>${esc(Object.entries(fd).map(([k,v])=>k.replace(/_/g,' ')+' '+usd2(v)).join(' · ')||'—')}</div><div><span>Fee schedule</span>${esc(f.fee_schedule||'—')}</div><div><span>Net proceeds</span>${esc(usd2(f.net_proceeds_usd))}</div><div><span>Return from original fill</span>${esc(pct(f.ret_orig_pct))}</div><div><span>Rail level at exit</span>${esc(px(f.rail_level_usd))}</div><div><span>Price class</span>${esc(f.tape||'—')}</div><div><span>Exit variant</span>${esc(f.exit_variant||'V0 / n.a.')}</div></div>${f.why?`<p class="muted" style="margin:8px 0 0">Why: ${esc(f.why)}</p>`:''}</td></tr>`:''}`}).join('')}
   <tr class="sum"><td colspan="9">Filtered total (${all} fills)</td><td class="num">${esc(usd2(tf))}</td><td></td><td class="num ${cls(tp)}">${esc(usd(tp))}</td><td></td></tr></tbody></table></div>
   ${all>S.fillLimit?`<button type="button" class="linkbtn" id="xmore">Show ${Math.min(100,all-S.fillLimit)} more (${all-S.fillLimit} hidden)</button>`:''}`;
}
function wireTables(root){
  root.querySelectorAll('th[data-sort]').forEach(h=>{const go=()=>{const st=h.dataset.which==='p'?S.psort:h.dataset.which==='f'?S.fsort:S.hsort;const k=h.dataset.sort;if(st.k===k)st.d=-st.d;else{st.k=k;st.d=['utc','value_usd','fees_usd','pnl_usd','upnl_usd','principal_usd'].includes(k)?-1:1}rerender()};
    h.onclick=e=>{if(e.target.closest('.gi'))return;go()};h.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}}});
  root.querySelectorAll('tr.row[data-pid]').forEach(r=>{const go=()=>{const id=r.dataset.pid;S.openPos.has(id)?S.openPos.delete(id):S.openPos.add(id);rerender()};r.onclick=e=>{if(e.target.closest('.gi,a,button'))return;go()};r.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}}});
  root.querySelectorAll('tr.row[data-fid]').forEach(r=>{const go=()=>{const id=r.dataset.fid;S.openFill.has(id)?S.openFill.delete(id):S.openFill.add(id);rerender()};r.onclick=e=>{if(e.target.closest('.gi,a,button'))return;go()};r.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}}});
  const sl=root.querySelector('#xsl');if(sl)sl.onchange=e=>{S.f.sleeve=e.target.value;rerender()};
  const vn=root.querySelector('#xvn');if(vn)vn.onchange=e=>{S.f.venue=e.target.value;rerender()};
  const q=root.querySelector('#xq');if(q){q.oninput=e=>{S.f.q=e.target.value;clearTimeout(q._t);q._t=setTimeout(()=>{rerender();const n=document.querySelector('#xq');if(n){n.focus();n.setSelectionRange(n.value.length,n.value.length)}},250)}}
  const c=root.querySelector('#xclr');if(c)c.onclick=()=>{S.f={sleeve:'',venue:'',q:''};rerender()};
  const m=root.querySelector('#xmore');if(m)m.onclick=()=>{S.fillLimit+=100;rerender()};
}

/* ------------------------------------------------------------------ overview top: chart, sleeve cards, IBKR */
function ibkrPanel(){const i=(S.dash||{}).ibkr||{};
  return `<section aria-label="IBKR paper mirror"><div class="split"><h2>IBKR paper mirror</h2><span class="status warn">${esc((i.label||'Mirror pending').toUpperCase())}</span></div>
   <div class="kv"><div><span>Account</span>${esc(i.account||'—')}</div><div><span>Base currency</span>${esc(i.base_ccy||'—')}</div><div><span>Gateway</span>${esc(i.gateway_host||'—')}</div><div><span>First mirror</span>${esc(i.first_mirror||'—')}</div></div>
   <p class="muted">${esc(i.balance_note||'')} ${esc(i.role||'')} This dashboard does not connect to IBKR.</p></section>`}
function sleeveCards(){
  const sl=(S.dash||{}).sleeves||[];const fresh=id=>fr(id==='deep18_fwd'?'deep18_fwd':id);
  return `<div class="cards">${sl.map(s=>{const f=fresh(s.id);return `<button type="button" class="card" data-card="${esc(s.id)}" aria-expanded="${S.openCard===s.id}">
   <div class="ct"><span data-gterm="${esc(s.id==='shark'?'Shark':s.id==='crypto'?'Crypto clips':s.id==='shadow'?'Shadow 2x Nasdaq':s.id==='deep18_fwd'?'FT1-FT5':'Trend18')}">${esc(s.name)}</span><span class="pill ${statusCls(f.status)}">${esc(f.status||'—')}</span></div>
   <div class="cv">${s.equity_usd==null?esc(usd(s.pnl_usd))+' <small class="muted">P&L</small>':esc(usd(s.equity_usd))}</div>
   <div class="cn">${s.equity_usd==null?'':'P&L <span class="'+cls(s.pnl_usd)+'">'+esc(usd(s.pnl_usd))+'</span> · '}costs ${esc(usd(s.costs_usd))} · ${esc(s.status||'')}</div>
   <div class="cn">${s.in_firm_equity?'In firm equity':'Shadow: not in firm equity'} · kill ${esc(s.kill||'—')}</div><div class="cn">${f.asof_utc?'Updated '+esc(syd(f.asof_utc))+' · '+esc(ago(f.asof_utc)):''}</div></button>`}).join('')}</div>
   ${S.openCard?cardDetail(sl.find(s=>s.id===S.openCard)):''}`;
}
function cardDetail(s){if(!s)return '';const d=S.dash||{};
  const map={trend18:'Trend18',shark:'Shark (equity clips)',crypto:'Crypto clips'},sk=map[s.id];
  let h=`<div class="xpanel"><div class="split"><h3 style="margin:0">${esc(s.name)} · details</h3><button type="button" class="linkbtn" data-card="${esc(s.id)}">Close</button></div>`;
  if(s.next)h+=`<p class="muted">${esc(s.next)}</p>`;
  if(s.rails)h+=`<p><b>Rails:</b> ${esc(s.rails)}</p>`;
  if(s.exit_test)h+=`<p class="muted">Exit-rule forward test (${esc(s.exit_test.rule_doc||'')}): ${esc(s.exit_test.trades)} tracked trade(s) · updated ${esc(syd(s.exit_test.updated_utc))}</p>`;
  if(sk){const pos=(d.positions||[]).filter(r=>r.sleeve===sk);h+=`<h3 style="font-size:15px">Positions (${pos.length})</h3>`+positionsTable(pos);
    const fl=(d.fills||[]).filter(r=>r.sleeve===sk).slice(0,15);if(fl.length)h+=`<h3 style="font-size:15px">Latest fills with costs</h3>`+fillsMini(fl)}
  if(s.grades&&s.grades.length)h+=`<h3 style="font-size:15px" data-gterm="A+/A/B grades">Latest shark grades</h3><div class="scroll"><table class="xt"><thead><tr><th>Time</th><th>Symbol</th><th>Grade</th><th>Decision</th><th>Reasons</th></tr></thead><tbody>${s.grades.map(g=>`<tr><td>${esc(syd(g.utc))}</td><td>${esc(g.symbol)}</td><td>${esc(g.grade)}</td><td>${esc(g.action)}</td><td>${esc(g.reasons)}</td></tr>`).join('')}</tbody></table></div>`;
  if(s.id==='shadow'||s.id==='deep18_fwd')h+=`<p class="muted">Full table further down this page; chart: Performance tab.</p>`;
  const sid={trend18:'trend18',shark:'shark',shadow:'shadow',deep18_fwd:'fwd_ft2_glide'}[s.id];if(sid&&(d.series||{})[sid])h+=`<button type="button" class="linkbtn" data-goseries="${sid}">Open chart in Performance</button>`;
  return h+'</div>';
}
function overviewTop(){
  const d=S.dash;if(!d)return S.dashErr?`<p class="warn">Interactive data unavailable (${esc(S.dashErr)}). Retrying every 60 s.</p>`:'<p class="muted">Loading interactive data…</p>';
  const b=d.benchmark||{};
  return `<section aria-label="Equity curve"><div class="split"><h2>Firm equity after costs vs 19% benchmark</h2>${stampHTML(fr('firm').asof_utc,fr('firm').status)}</div>
   <p class="muted">${esc(b.rule||'')}</p>${chartControls()}<div class="chost"></div><div class="cstats"></div></section>
   <section aria-label="Sleeves"><div class="split"><h2>Sleeves</h2><span class="muted">Click a sleeve for positions, fills with costs and rails</span></div>${sleeveCards()}</section>${ibkrPanel()}`;
}
function wireOverview(root){wireChart(root,false);wireTables(root);
  root.querySelectorAll('[data-card]').forEach(c=>c.onclick=e=>{if(e.target.closest('.gi'))return;S.openCard=S.openCard===c.dataset.card?'':c.dataset.card;rerender()});
  root.querySelectorAll('[data-goseries]').forEach(c=>c.onclick=()=>{S.chart.series=c.dataset.goseries;window.select&&window.select('Performance')});
}

/* ------------------------------------------------------------------ own tabs */
function perfTab(){const d=S.dash;if(!d)return overviewTop();
  const ser=d.series||{};return `<section aria-label="Equity and sleeves"><div class="split"><h2>Equity curve</h2>${stampHTML(fr(S.chart.series==='shark'?'eq_fund':S.chart.series==='shadow'?'shadow':S.chart.series.startsWith('fwd_')?'deep18_fwd':'firm').asof_utc)}</div>
   <p class="muted">Pick the firm or any sleeve, a time range, and whether to show the after-fee line and the 19%/yr (before fee) benchmark. Hover or tap the chart for values. Books are booked net of modelled trading costs.</p>
   ${chartControls()}<div class="chost"></div><div class="cstats"></div><p class="muted">Sources: ${Object.values(ser).map(s=>esc(s.label)+' ← '+esc(s.source)).join(' · ')}. Trend18 has no history until its first entry (Mon 5 Oct US close).</p></section>
   <section aria-label="Fee and benchmark"><h2>Funder fee and benchmark</h2><div class="kv"><div><span>Fee now</span>${esc(Number((d.fee||{}).rate_pct_day||0).toFixed(4))}%/day (~${esc((d.fee||{}).annual_drag_simple_pct)}%/yr simple)</div><div><span>Fees paid so far</span>${esc(usd2((d.fee||{}).cum_fee_paid_usd))}</div><div><span>Benchmark</span>${esc((d.benchmark||{}).pre_fee_cagr_pct)}%/yr before fee ≈ ${esc((d.benchmark||{}).after_fee_cagr_pct)}%/yr after fee</div><div><span>Pre-fee break-even</span>${esc((d.fee||{}).break_even_pre_fee_cagr_pct)}%/yr</div></div>
   <p class="muted">Rate history: ${esc(((d.fee||{}).rate_history||[]).map(x=>x.from_sydney_fee_date+' '+x.rate_pct_day+'%/day').join(' → '))}.</p></section>`}
function holdTab(){const d=S.dash;if(!d)return '<p class="muted">Loading…</p>';
  const pos=filt(d.positions||[]),fl=filt(d.fills||[]);
  return `<section aria-label="Open positions"><div class="split"><h2>Open positions</h2>${stampHTML(fr('positions').asof_utc,fr('positions').status)}</div>${filterBar()}
   <p class="muted">Click a row for its rails (stop, half-take, trail, max hold), entry costs and fills. Click a column to sort. Read-only: no orders can be placed here.</p>${positionsTable(pos)}</section>
   <section aria-label="Fills with costs"><div class="split"><h2>Fills with costs</h2>${stampHTML(fr('shark_book').asof_utc,fr('shark_book').status)}</div><p class="muted">Head Trader entry and exit tickets for shark equity clips and crypto clips, newest first, with fees and cost in basis points. Other books' fills are on the Trade tab.</p>${fillsTable(fl)}</section>`}
function healthTab(){const d=S.dash;if(!d)return '<p class="muted">Loading…</p>';
  const rows=sortRows((d.freshness||[]).map(r=>Object.assign({st:{STALE:0,UNKNOWN:0,CLOCK_ERROR:0,LAG:1,PENDING:2,DORMANT:3,FRESH:4}[r.status]??5},r)),S.hsort.k==='status'?{k:'st',d:S.hsort.d}:S.hsort);
  const k=d.kill||{},firm=k.firm||{};
  return `<section aria-label="Data freshness"><div class="split"><h2>Data freshness</h2>${stampHTML(d.generated_utc)}</div><p class="muted">Every panel, its data source, when that data was last produced (Sydney time) and whether that is within its schedule. Page data refreshes automatically every 60 s.</p>
   <div class="scroll"><table class="xt"><thead><tr>${th('Panel','panel',S.hsort,0,'h')}${th('Source','source',S.hsort,0,'h')}${th('Last updated','asof_utc',S.hsort,0,'h')}${th('Age','age_s',S.hsort,1,'h')}${th('Cadence','cadence',S.hsort,0,'h')}${th('Status','status',S.hsort,0,'h')}${th('Note','note',S.hsort,0,'h')}</tr></thead><tbody>
   ${rows.map(r=>`<tr><td>${esc(r.panel)}</td><td class="muted">${esc(r.source)}</td><td>${esc(syd(r.asof_utc))}</td><td class="num">${esc(r.asof_utc?ago(r.asof_utc):'—')}</td><td>${esc(r.cadence)}</td><td><span class="pill ${statusCls(r.status)}">${esc(r.status)}</span></td><td>${esc(r.note)}</td></tr>`).join('')}</tbody></table></div></section>
   <section aria-label="Kill status"><div class="split"><h2>Kill rule status</h2>${stampHTML(firm.asof_utc)}</div><div class="kv"><div><span>Firm level</span>${esc(firm.level)} · DD ${esc(pct(firm.drawdown_pct))} (warn ${esc(firm.warn_dd_pct)}%, halt ${esc(firm.halt_dd_pct)}%)</div><div><span>New entries</span>${firm.new_entries_halted?'<b class="bad">HALTED</b>':'allowed'}</div><div><span>Trend18</span>${(k.trend18||{}).active?'<b class="bad">KILL ACTIVE</b>':'armed (-30% NAV/unit)'}</div><div><span>Forward tests killed</span>${esc((k.deep18_fwd||[]).filter(x=>x.active).length)} of ${esc((k.deep18_fwd||[]).length)}</div></div>
   <div class="scroll"><table><thead><tr><th>Book</th><th>Drawdown</th><th>Kill at</th><th>Status</th><th>Checked</th></tr></thead><tbody>${(k.books||[]).map(b=>`<tr><td>${esc(b.book)}</td><td>${esc(pct(b.drawdown_pct))}</td><td>${esc(k.book_kill_dd_pct)}%</td><td>${b.frozen?'<b class="bad">FROZEN</b>':'armed'}</td><td>${esc(syd(b.checked_utc))}</td></tr>`).join('')}</tbody></table></div></section>${ibkrPanel()}`}

/* ------------------------------------------------------------------ public hooks */
let curTab='Overview';
function rerender(){S.lastKey={};if(OWN.includes(curTab))renderOwn(curTab,true);else if(curTab==='Overview')renderTop(true)}
function renderOwn(tab,force){
  const c=$('#content');if(!c)return;const key=JSON.stringify([tab,S.dashAt,S.chart,S.f,S.psort,S.fsort,S.hsort,[...S.openPos],[...S.openFill],S.fillLimit,!!S.glIdx]);
  if(!force&&S.lastKey.own===key&&c.dataset.own===tab)return;S.lastKey.own=key;c.dataset.own=tab;
  const sy=window.scrollY;c.innerHTML=tab==='Performance'?perfTab():tab==='Holdings'?holdTab():healthTab();
  if(tab==='Performance')wireChart(c,false);wireTables(c);decorate(c);window.scrollTo(0,sy);
}
function renderTop(force){
  const x=$('#xtop');if(!x)return;if(curTab!=='Overview'){x.innerHTML='';x.dataset.k='';return}
  const key=JSON.stringify([S.dashAt,S.chart,S.openCard,[...S.openPos],S.psort,!!S.glIdx]);if(!force&&x.dataset.k===key)return;x.dataset.k=key;
  x.innerHTML=overviewTop();if(S.dash)wireOverview(x);decorate(x);
}
function refreshOwn(){if(OWN.includes(curTab))renderOwn(curTab);renderTop();renderAlerts(S.pw)}
window.TFOX={
  owns:tab=>OWN.includes(tab),
  stamp:tab=>`<span class="muted">${esc(tab)} · interactive data ${S.dash?'built '+esc(syd(S.dash.generated_utc)):'loading'} · auto-refresh every 60 s</span>`,
  render:(tab,pw)=>{curTab=tab;S.pw=pw;const x=$('#xtop');if(x){x.innerHTML='';x.dataset.k=''}renderOwn(tab);renderAlerts(pw)},
  before:()=>rememberDetails(),
  after:(tab,pw)=>{curTab=tab;S.pw=pw;const c=$('#content');if(c)c.dataset.own='';restoreDetails();stampSections(c,tab,pw);sortable(c);decorate(c);decorate(document.querySelector('header'));renderTop();renderAlerts(pw)},
  tick:()=>{document.querySelectorAll('.upd[data-asof] .ago').forEach(a=>{a.textContent=ago(a.parentElement.dataset.asof)});const n=$('#nextref');if(n&&S.dashAt){const s=Math.max(0,Math.round((S.dashAt+POLL_MS-Date.now())/1000));n.textContent='Data '+(S.dash?syd(S.dash.generated_utc):'—')+' · next refresh '+s+' s'}},
  openGlossary:openGlossary, state:S, lookup:lookup
};
function hashTab(){const m=/^#tab=(.+)$/.exec(location.hash);if(m&&typeof window.select==='function'){const t=decodeURIComponent(m[1]);if(document.getElementById('tab-'+t))window.select(t)}}
window.addEventListener('hashchange',hashTab);
loadGlossary().then(()=>{refreshOwn();if(typeof window.render==='function')window.render();if(location.hash==='#glossary')openGlossary();hashTab()});
loadDash();setInterval(loadDash,POLL_MS);setInterval(loadGlossary,600000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&Date.now()-S.dashAt>POLL_MS)loadDash()});
})();
