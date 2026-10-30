/* WAR ROOM CLEAN v1 2026-10-27 — PAPER-only read-only view. Reads the same status JSON as the old banners. */
(function(){
const BOOKS=[['Ours','/static/hunt48_short_status.json'],['Claude','/static/hunt48_short_claude_status.json'],['GPT','/static/hunt48_short_gpt_status.json']];
let INFO={};
const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const N=(x,d=2)=>x==null||isNaN(x)?'–':Number(x).toLocaleString('en-AU',{minimumFractionDigits:d,maximumFractionDigits:d});
const px=x=>x==null?'–':N(x,Math.abs(x)<5?4:2);
const A=(x,d=0)=>x==null||isNaN(x)?'–':(x<0?'−':'')+'A$'+N(Math.abs(x),d);
const SA=x=>x==null?'–':(x>0?'+':'')+A(x);
const C=x=>x>0?'pos':x<0?'neg':'';
const J=u=>fetch(u+(u.includes('?')?'&':'?')+'t='+Date.now()).then(r=>r.ok?r.json():null).catch(()=>null);
const bub=t=>`<button class="i" data-tk="${E(t)}" aria-label="About ${E(t)}">ⓘ</button>`;
function pop(tk,ev){const p=document.getElementById('wcpop'),i=INFO[tk];
 p.innerHTML=`<button class="x" aria-label="close">×</button><b>${E(tk)} · ${E(i?i.name:tk)}</b><div class="sec">${E(i&&i.sector||'')}</div><div>${E(i&&i.desc||'No description yet')}</div>`;
 p.style.display='block';ev&&ev.stopPropagation();}
document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('#wc .i');if(b)return pop(b.dataset.tk,e);
 const p=document.getElementById('wcpop');if(p&&!e.target.closest('#wcpop .sec, #wcpop div:not(.x)'))p.style.display='none';});
document.addEventListener('mouseover',e=>{if(matchMedia('(hover:hover)').matches){const b=e.target.closest&&e.target.closest('#wc .i');if(b)pop(b.dataset.tk)}});
function card(t,fx){const sh=t.direction==='SHORT',q=t.open_qty||0,v=q*t.price/fx,sg=sh?-1:1;
 const risk=Math.max(0,q*(t.entry-t.stop)*sg/fx);
 return `<div class="card"><div class="hd"><span class="tk">${E(t.ticker)}</span>${bub(t.ticker)}<span class="bd ${sh?'SHORT':'LONG'}">${sh?'SHORT':'LONG'}</span><span class="pl ${C(t.pnl_aud)}">${SA(t.pnl_aud)}</span></div>
 <div class="g"><div><span>Entry</span><b>${px(t.entry)}</b></div><div><span>Now</span><b>${px(t.price)}</b></div>
 <div><span>Stop</span><b>${px(t.stop)}</b></div><div><span>Targets</span><b>${px(t.t1)} / ${px(t.t2)}</b></div>
 <div><span>Shares</span><b>${N(q,0)}${q!==t.shares?' of '+N(t.shares,0):''}</b></div><div><span>Value</span><b>${A(v)}</b></div>
 <div><span>A$ at risk</span><b>${A(risk)}</b></div><div><span>Planned risk</span><b>${A(t.risk_aud)}</b></div></div>
 <div class="muted" style="margin-top:6px">P&amp;L after costs · price ${E((t.price_utc||'').slice(11,16))} UTC${t.time_exit_wita?' · time exit '+E(t.time_exit_wita):''}</div></div>`;}
function brow(t,dir,trig,risk,book){return `<div class="br"><div><b>${E(t)}</b> ${bub(t)}</div><span class="bd ${dir}">${dir}</span><div class="r">trig ${px(trig)}</div><div class="r">${A(risk)}${book?'<div class="muted">'+E(book)+'</div>':''}</div></div>`}
async function go(){
 const [info,cal,...bs]=await Promise.all([J('/static/asset_info.json'),J('/static/cal_paper_bench.json'),...BOOKS.map(b=>J(b[1]))]);
 if(info)INFO=info;
 let tot='',plan='',posAll=[],armed=[],risk=[];const OPN={s:0,n:0,d:0,dn:0};
 BOOKS.forEach(([nm],k)=>{const j=bs[k];if(!j){tot+=`<div><div class="n">${nm}</div><div class="v">–</div><div class="s">unavailable</div></div>`;return;}
  const fx=j.audusd||0.70,tr=j.trades||[],op=tr.filter(t=>t.status==='FILLED'&&(t.open_qty||0)>0),ar=tr.filter(t=>t.status==='ARMED');
  const rk=op.reduce((s,t)=>s+Math.max(0,t.open_qty*(t.entry-t.stop)*(t.direction==='SHORT'?-1:1)/fx),0),val=op.reduce((s,t)=>s+t.open_qty*t.price/fx,0);
  /* OPEN-PNL FIX 2026-10-27 (pt1534u): audit H4 made pnl_today_aud a day CHANGE (~0 while NY is shut), so the tile headline is now
     P&L since entry after costs (pnl_since_entry_aud) with today's change underneath. */
  const opn=j.pnl_since_entry_aud!=null?j.pnl_since_entry_aud:null;if(opn!=null){OPN.s+=opn;OPN.n++;}if(j.pnl_today_aud!=null){OPN.d+=j.pnl_today_aud;OPN.dn++;}
  tot+=`<div><div class="n">${nm}</div><div class="v ${C(opn)}">${SA(opn)}</div><div class="s">open P&amp;L after costs · today <b class="${C(j.pnl_today_aud)}">${SA(j.pnl_today_aud)}</b></div><div class="s">${op.length} open · ${ar.length} armed</div></div>`;
  plan+=`<div class="book"><span>${nm} book · ${op.length} open</span><span class="muted">${A(val)} held</span></div>`+(op.map(t=>card(t,fx)).join('')||'<div class="empty">No open trades</div>');
  op.forEach(t=>posAll.push([nm,t,fx]));
  armed.push([nm,ar]);risk.push([nm,rk,val,j]);});
 const upd=(bs.find(x=>x)||{}).updated_wita||'';
 if(OPN.n)tot+=`<div><div class="n">Total (3 books)</div><div class="v ${C(OPN.s)}">${SA(OPN.s)}</div><div class="s">open P&amp;L after costs · today <b class="${C(OPN.d)}">${OPN.dn?SA(OPN.d):'–'}</b></div><div class="s">${OPN.n}/3 books reporting</div></div>`;
 document.getElementById('wctot').innerHTML=tot;
 document.getElementById('wcupd').textContent='PAPER money only · P&L today after costs · prices '+upd+' · '+((bs.find(x=>x)||{}).regime||'');
 let ah=armed.map(([nm,ar])=>`<details class="bench"><summary>${nm} · ${ar.length} armed / bench</summary>${ar.map(t=>brow(t.ticker,t.direction,t.trigger,t.risk_aud,t.bench?'bench':'armed')).join('')||'<div class="empty">None</div>'}</details>`).join('');
 if(cal){const ct=(cal.trades||[]).filter(t=>t.status==='ARMED');ah+=`<details class="bench"><summary>FX / commodity calendar bench · ${ct.length} armed</summary>${ct.map(t=>brow(t.sym,t.direction.toUpperCase(),t.trigger,t.risk_aud,'sim fill')).join('')||'<div class="empty">None</div>'}</details>`;}
 document.getElementById('wcplan').innerHTML=plan+'<h2>Armed &amp; bench (waiting for trigger)</h2>'+ah;
 document.getElementById('wcpos').innerHTML=posAll.length?posAll.map(([nm,t,fx])=>`<div class="muted" style="margin-top:8px">${nm} book</div>`+card(t,fx)).join(''):'<div class="empty">No open positions</div>';
 document.getElementById('wcrisk').innerHTML=risk.map(([nm,rk,val,j])=>`<div class="card"><div class="hd"><span class="tk" style="font-size:20px">${nm}</span><span class="pl" style="font-size:20px">${A(rk)} at risk</span></div><div class="g"><div><span>Held value</span><b>${A(val)}</b></div><div><span>Open / max</span><b>${j.open??'–'} / ${j.max_open??'–'}</b></div><div><span>Bench armed</span><b>${j.bench_armed??'–'}</b></div><div><span>Causal violations</span><b>${j.causal_violations_count??0}</b></div></div><div class="muted" style="margin-top:6px">Costs: ${E(j.costs||'')}${j.borrow_note?' · '+E(j.borrow_note):''}</div></div>`).join('');
}
function tabs(){const want=localStorage.getItem('wctab')||'plan';
 document.querySelectorAll('#wctabs button').forEach(b=>{b.onclick=()=>{localStorage.setItem('wctab',b.dataset.p);show(b.dataset.p)}});show(want);}
function show(p){document.querySelectorAll('#wctabs button').forEach(b=>b.classList.toggle('on',b.dataset.p===p));
 document.querySelectorAll('.wcpane').forEach(x=>x.classList.toggle('on',x.id==='wcp-'+p));
 document.getElementById('wcmore').style.display=p==='more'?'block':'none';window.scrollTo(0,0);}
function rehome(){const wc=document.getElementById('wc'),more=document.getElementById('wcmore');
 document.body.prepend(wc);const trk=document.getElementById('trackercard');if(trk)document.body.prepend(trk);document.body.appendChild(more);document.body.appendChild(document.getElementById('wcpop'));document.body.appendChild(document.getElementById('wctabs'));
 [...document.body.children].forEach(c=>{if(c!==wc&&c.id!=='trackercard'&&c!==more&&c.id!=='wctabs'&&c.id!=='wcpop'&&c.tagName!=='SCRIPT'&&c.tagName!=='STYLE'&&c.tagName!=='LINK')more.appendChild(c)});
 /* P&L tab: reuse existing firm-value + 'who picks best' + desks blocks (moved, not copied, so their live scripts keep working) */
 const st=document.getElementById('simpletop'),pl=document.getElementById('wcpl'),rk=document.getElementById('wcriskx');
 if(st){const hero=st.querySelector('.hero');if(hero)pl.appendChild(hero);const cmp=st.querySelector('.cmp');if(cmp){const h=document.createElement('h2');h.textContent='Who picks best';pl.appendChild(h);pl.appendChild(cmp);}
  const d=document.getElementById('st_desks');if(d){const h=document.createElement('h2');h.textContent='All desks';pl.appendChild(h);pl.appendChild(d);}
  const cal=document.getElementById('stcal');if(cal)rk.appendChild(cal);
  const mood=document.getElementById('st_mood');if(mood)pl.prepend(mood);
  ['st_cards','st_cash','st_benchh','st_benchnote','st_bench','st_cutbox'].forEach(id=>{const e=document.getElementById(id);if(e)e.setAttribute('data-wc-dup','1')});
  pl.classList.add('wcsimple');pl.id='wcpl';}
 const fd=document.getElementById('fulldetail');if(fd)fd.open=true;}
document.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{rehome();tabs();go();setInterval(go,60000)},0));
})();
