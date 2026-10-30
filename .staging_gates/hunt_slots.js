/* HUNT SLOTS v1.1 2026-10-27 (PAPER; + open-gate status). hunt_orchestrator.py -> /static/hunt_orchestrator_status.json.
   Adds "Armed / Open slots / Backups" to each War Room market card [id="wm-<key>"] (cards re-render; block is re-injected). Mobile-first. */
(function(){
 const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const P=v=>v==null?'–':(Math.abs(v)>=1000?Number(v).toFixed(1):Number(v).toPrecision(5));
 const st=document.createElement('style');st.textContent=`.hs{margin-top:8px;border-top:1px dashed #333;padding-top:6px;font-size:13px}
 .hs-k{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:4px}.hs-k div{background:#151515;border-radius:6px;padding:4px 6px}
 .hs-k span{display:block;color:#999;font-size:11px}.hs-k b{font-size:15px}.hs-k .free{color:#4caf50}.hs-k .thin{color:#ff9800}
 .hs details{margin-top:2px}.hs summary{cursor:pointer;color:#bbb;padding:4px 0;min-height:28px}
 .hs-r{display:flex;flex-wrap:wrap;gap:4px 10px;padding:3px 0;border-bottom:1px solid #222}.hs-r b{min-width:64px}
 .hs-r .S{color:#ef5350}.hs-r .L{color:#66bb6a}.hs-r .w{color:#999;flex-basis:100%;font-size:11px}.hs-n{color:#888;font-size:11px}`;
 document.head.appendChild(st);
 let D=null;
 function block(b){const ready=b.backups_ready??0;
  const rows=(b.backups||[]).slice(0,8).map(x=>`<div class="hs-r"><b>${x.rank??''}. ${E(x.sym)}</b><span class="${E(x.dir)}">${E(x.dir)}</span><span>trig ${P(x.trigger)}</span><span>stop ${P(x.stop)}</span>${x.room_R!=null?`<span>room ${Number(x.room_R).toFixed(2)}R</span>`:''}<span>${E(x.state)}${x.gate?' · '+E(x.gate):''}</span>${x.why?`<span class="w">${E(String(x.why).slice(0,120))}</span>`:''}</div>`).join('')||'<div class="hs-n">no backups</div>';
  const arm=(b.armed||[]).map(x=>`${E(x.sym)} ${E(x.dir||'')}`).join(', ')||'none';
  return `<div class="hs" data-hs="1"><div class="hs-k"><div><span>Armed</span><b>${(b.armed||[]).length}</b></div><div><span>Open slots</span><b class="${b.open_slots?'free':''}">${b.open_slots??'–'} / ${b.max_open??3}</b></div><div><span>Backups ready</span><b class="${b.thin_bench?'thin':''}">${ready}${b.thin_bench?' thin':''}</b></div></div>
  ${gates(b)}<div class="hs-n">Armed: ${arm} · next hunt ${E(b.next_hunt_wita||'–')}${b.last_hunt?' · last '+E(b.last_hunt):''}</div>
  <details><summary>Backups (ranked, auto slot-in)</summary>${rows}${b.note?`<div class="hs-n">${E(b.note)}</div>`:''}</details></div>`;}
 function gates(b){const g=b.open_gates;const sg=(b.staged||[]).map(x=>`${E(x.sym)} ${E(x.dir||'')}`).join(', ');
  if(!g&&!sg)return '';
  const res=((g&&g.results)||[]).slice(-6).map(r=>`<div class="hs-r"><b>${E(r.ticker)}</b><span>${E(r.phase)}</span><span class="${r.ok?'L':'S'}">${r.ok?'PASS':'CANCEL'}</span>${(r.why||[]).length?`<span class="w">${E((r.why||[]).join('; ').slice(0,140))}</span>`:''}</div>`).join('');
  return `<div class="hs-n">Open gates (gap · RS vs index · OR break+vol · spread): pre ${E(g&&g.pre_wita||'pending')} · OR ${E(g&&g.or_wita||'pending')}${sg?' · staged: '+sg:''}</div>${res?`<details><summary>Gate results</summary>${res}</details>`:''}`;}
 function inject(){if(!D||!D.books)return;
  for(const [k,b] of Object.entries(D.books)){document.querySelectorAll(`[id="wm-${k}"]`).forEach(c=>{
   const old=c.querySelector('[data-hs]');const h=block(b);if(old){if(old.dataset.v!==b.generated_wita+(old.querySelector('details')?.open?'o':'')){const o=old.querySelector('details')?.open;old.outerHTML=h;if(o)c.querySelector('[data-hs] details').open=true;c.querySelector('[data-hs]').dataset.v=b.generated_wita;}}
   else{c.insertAdjacentHTML('beforeend',h);c.querySelector('[data-hs]').dataset.v=b.generated_wita;}});}}
 async function load(){try{const r=await fetch('/static/hunt_orchestrator_status.json?t='+Date.now());if(r.ok)D=await r.json();}catch(e){}inject();}
 load();setInterval(load,60000);setInterval(inject,3000);
})();
