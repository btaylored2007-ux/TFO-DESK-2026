"""Dashboard message box. Injected beside the existing page. It does not draw charts.

Phone (/desk/phone) gets a compact strip. Desktop gets the same controls, larger.
Brain dots, feed doctor, takeover and the halt banner are all in this strip.
"""

PANEL = r"""
<section id="thf-guardian">
<style>
#thf-guardian{border:2px solid #3a7;border-radius:8px;padding:8px;margin:8px;font:13px sans-serif;background:#101a14;color:#eee}
#thf-guardian textarea,#thf-guardian input,#thf-guardian select{width:100%;box-sizing:border-box;margin:3px 0;background:#122;color:#eee;border:1px solid #355}
#thf-guardian button{margin:2px 4px 2px 0;padding:6px 10px;font-weight:bold}
#thf-guardian .gd-row{border-top:1px solid #254;padding:3px 0}
#thf-guardian .bad{color:#f96}#thf-guardian .ok{color:#6d6}
#thf-guardian.gd-phone textarea{height:34px}
#thf-guardian.gd-phone .gd-wide{display:none}
#thf-guardian.gd-phone .gd-thread{max-height:110px;overflow:auto;font-size:12px}
</style>
<b>THF guardian</b> <span id="gd-mode"></span>
<div id="gd-sum" class="gd-row">loading…</div>
<div id="gd-brains" class="gd-row"></div>
<details class="gd-wide"><summary>feeds, locks, drift</summary><div id="gd-ops"></div></details>
<div id="gd-take" class="gd-row"></div>
<textarea id="gd-text" rows="2" placeholder="Ask the desk, or push traders"></textarea>
<button type="button" id="gd-send">Send</button>
<button type="button" id="gd-takeover">Take control</button>
<button type="button" id="gd-hand">Hand back</button>
<div id="gd-confirm"></div>
<div id="gd-thread" class="gd-thread"></div>
<script>
(function(){
  const root=document.getElementById('thf-guardian');
  if(location.pathname.indexOf('/desk/phone')===0) root.classList.add('gd-phone');
  let REV=0, PENDING=null;
  function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
  async function refresh(){
    try{
      const s=await (await fetch('/api/guardian_status',{cache:'no-store'})).json();
      REV=s.screen_rev||0;
      const brains=s.brains||{};
      const ids=Object.keys(brains);
      const down=ids.filter(k=>brains[k].mode==='degraded');
      document.getElementById('gd-mode').textContent=s.firm_paused?' · NEW ARMS PAUSED':'';
      document.getElementById('gd-sum').innerHTML=(s.ok?'<span class="ok">checks green</span>':'<span class="bad">checks need a look</span>')
        +' · brains '+(ids.length-down.length)+'/'+ids.length+' up'
        +(down.length?' · <span class="bad">'+esc(down.join(', '))+' down</span>':'')
        +(s.auto_clear_halted_until?' · <span class="bad">auto-clear halted</span>':'')
        +(s.takeover&&s.takeover.on?' · <b>TAKEOVER</b>':'');
      document.getElementById('gd-brains').innerHTML=ids.map(k=>{
        const b=brains[k]; const cls=b.mode==='degraded'?'bad':(b.mode==='ai'||b.mode==='code'?'ok':'');
        return '<span class="'+cls+'">'+esc(b.label||k)+' '+esc(b.mode)+(b.mode==='degraded'?' '+((b.real_successes||0)+'/2'):'')+'</span>';
      }).join(' · ');
      const feeds=(s.feeds&&s.feeds.feeds)||[];
      document.getElementById('gd-ops').textContent=feeds.map(f=>f.title+': '+f.state).join(' | ')
        +' drift '+(s.drift&&s.drift.state);
    }catch(e){document.getElementById('gd-sum').textContent='guardian status unavailable';}
    try{
      const t=await (await fetch('/api/guardian_thread',{cache:'no-store'})).json();
      document.getElementById('gd-thread').innerHTML=(t.messages||[]).slice(-12).map(m=>'<div class="gd-row"><b>'+esc(m.role)+'</b> '+esc(m.text)+'</div>').join('')||'<i>no messages</i>';
    }catch(e){}
  }
  async function send(extra){
    const text=document.getElementById('gd-text').value;
    const body=Object.assign({text:text,screen_rev:REV}, extra||{});
    const r=await fetch('/api/guardian_msg',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    const box=document.getElementById('gd-confirm');
    box.innerHTML='';
    if(j.needs_confirm && j.token){
      PENDING=j.token;
      const b=document.createElement('button');
      b.textContent='Confirm '+(j.action&&j.action.kind);
      b.onclick=()=>send({confirm:PENDING});
      box.appendChild(b);
    }else{
      PENDING=null;
      document.getElementById('gd-text').value='';
    }
    refresh();
  }
  document.getElementById('gd-send').onclick=()=>send();
  document.getElementById('gd-takeover').onclick=()=>{document.getElementById('gd-text').value='take control'; send();};
  document.getElementById('gd-hand').onclick=()=>{document.getElementById('gd-text').value='hand back'; send();};
  refresh(); setInterval(refresh, 15000);
})();
</script>
</section>
"""


def panel_html():
    return PANEL
