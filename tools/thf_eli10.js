/* dasheli10_20261103 (PAPER): phone-first ELI10 dashboard - tabs Home / Trades / Chat / Seats / Health.
   pushhealth_20261103: Seats = own schedule (editable: POST /api/seat_schedule) + brain / lessons / AI in use / last runs + scheduled pushes card; Health = Singapore server group, AI use per seat, pushes row; open rows survive the 30 s refresh.
   Data: Home JSON inlined in the page (first paint = one request), then GET /api/home (every 30s), GET /api/eli10?tab=trades|seats|health
   (loaded the first time a tab opens, refreshed every 30s while it is open), chat = the existing tools/thf_chat.js via /chat.js (loaded on
   first open). Trade charts: GET /api/chart?sym= on tap. Seat deep-dive: GET /api/desk?seat= on tap. No libraries. */
(function(){
"use strict";
var $=function(s,r){return (r||document).querySelector(s)};
var TABS=["home","trades","chat","seats","health"],ICON={home:"",trades:"",chat:"",seats:"",health:""},LBL={home:"Home",trades:"Trades",chat:"Chat",seats:"Seats",health:"Health"};
var S={tab:null,data:{},at:{},busy:{},seat:(document.body.dataset.seat||"").replace(/[^a-z_]/g,""),all:/[?&]all=1/.test(location.search),desktop:window.innerWidth>=900,charts:{}};
var R={};
S.errors={};S.failures={};S.retryAt={};S.sourceAt={};
function readJSON(url,ms){
 var controller=typeof AbortController!=="undefined"?new AbortController():null,timer;
 var options={cache:"no-store"};if(controller)options.signal=controller.signal;
 var request=Promise.resolve().then(function(){return fetch(url,options)}).then(function(r){if(!r.ok)throw new Error("HTTP "+r.status);return r.json()});
 var timeout=new Promise(function(resolve,reject){timer=setTimeout(function(){if(controller)controller.abort();reject(new Error("The server took too long to answer"))},ms||12000)});
 return Promise.race([request,timeout]).then(function(j){clearTimeout(timer);return j},function(e){clearTimeout(timer);throw e});
}
function ageWords(ms){var s=Math.max(0,Math.floor(ms/1000));return s<60?s+"s":Math.floor(s/60)+"m"}
function updateStatus(){
 var t=S.tab||"home",el=$("#refresh-status"),bar=$("#refreshbar"),button=$("#refresh-now");if(!el||!bar)return;
 var now=Date.now(),last=S.at[t],source=S.sourceAt[t],text,kind="";
 if(t==="chat"){text="Chat checks for new messages while this tab is open."}
 else if(t==="seats"&&S.editing){text="Your schedule edit is kept. Finish editing before this page refreshes."}
 else if(S.errors[t]){kind="offline";text=(last?"Update failed — showing the last successful page from "+ageWords(now-last)+" ago. ":"This page could not load. ")+"Use Refresh to try again."}
 else if(source&&now-source>90000){kind="warn";text="Figures are "+ageWords(now-source)+" old. Check Health before relying on them."}
 else if(S.busy[t]){text=last?"Updating — the previous figures stay visible.":"Loading the latest figures…"}
 else if(last){text="Page checked "+ageWords(now-last)+" ago"+(source?" · figures built "+ageWords(now-source)+" ago":"")+". Auto-refreshes every 30 seconds."}
 else{text="Choose a tab to load its figures."}
 if(el.textContent!==text)el.textContent=text;bar.className="refreshbar"+(kind?" "+kind:"");bar.hidden=t==="chat";document.body.classList.toggle("tab-chat",t==="chat");
 if(button){button.disabled=!!S.busy[t]||(t==="seats"&&!!S.editing);button.hidden=t==="chat"}
}
function detailById(p,id){return [].slice.call(p.querySelectorAll("details[data-id]")).filter(function(d){return d.dataset.id===id})[0]}
function tradeKey(r){var d=r.d||{};return [r.kind,r.seat||r.sub,r.sym,d.id||d.trade_id||d.opened_utc||d.entry_utc||"",d.entry==null?d.limit:d.entry,d.stop,d.target].join(":")}

function esc(s){return String(s==null?"":s).replace(/[&<>"\']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","\'":"&#39;"}[c]})}
function cls(v){return v>0.5?"pos":v<-0.5?"neg":""}
function dot(c){return '<span class="dot '+esc(c||"grey")+'"></span>'}
function f(v){return v==null||isNaN(v)?"—":Number(v).toLocaleString("en-AU",{maximumSignificantDigits:6})}
function row(color,head,sub,pnl,dd,open,id){return '<details class="row"'+(open?" open":"")+(id?' data-id="'+esc(id)+'"':"")+'><summary>'+dot(color)+'<div class="mid"><div class="h">'+head+'</div>'+(sub?'<div class="s">'+sub+'</div>':"")+(pnl?'<div class="pnl">'+pnl+'</div>':"")+'</div><span class="chev">›</span></summary><div class="dd">'+dd+'</div></details>'}
function glossary(g){if(!g)return"";var ks=Object.keys(g);return '<details class="row glo"><summary>'+dot("grey")+'<div class="mid"><div class="h">What do these words mean?</div><div class="s">plain-English meaning of every code word on this page</div></div><span class="chev">›</span></summary><div class="dd"><dl>'+ks.map(function(k){return "<dt>"+esc(k)+"</dt><dd>"+esc(g[k])+"</dd>"}).join("")+"</dl></div></details>"}
function raw(d){try{return '<details class="row"><summary>'+dot("grey")+'<div class="mid"><div class="h">Raw numbers</div></div><span class="chev">›</span></summary><div class="dd"><pre style="white-space:pre-wrap;font-size:12px;margin:0">'+esc(JSON.stringify(d,null,1).slice(0,4000))+"</pre></div></details>"}catch(e){return""}}
/* pushhealth_20261103: generic escaped table + lines for Health rows (AI cost per seat, Singapore server, scheduled pushes) */
function tbl(d){if(!d||!d.cols)return"";return '<div class="tw"><table><tr>'+d.cols.map(function(c){return "<th>"+esc(c)+"</th>"}).join("")+"</tr>"+(d.rows||[]).map(function(r){return "<tr>"+r.map(function(c,i){return "<td"+(i&&String(c).length<=12?' style="white-space:nowrap"':"")+">"+esc(c)+"</td>"}).join("")+"</tr>"}).join("")+"</table></div>"}
function needsBrett(d){return (d.items||[]).map(function(x){return '<div class="card '+(x.open?"amber":"green")+'" style="margin:6px 0"><h3>'+(x.open?"🙋 ":"✅ ")+esc(x.title)+"</h3>"+(x.status?"<p><b>"+esc(x.status)+"</b></p>":"")+"<p>"+esc(x.why)+"</p>"+(x.link?'<p><a class="btn" href="'+esc(x.link.url)+'" target="_blank" rel="noopener">'+esc(x.link.label)+'</a><br><span class="mu">'+esc(x.link.url)+"</span></p>":"")+(x.steps||[]).map(function(t){return "<p>"+esc(t)+"</p>"}).join("")+(x.cmd?'<pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;background:#0d1117;padding:8px;border-radius:6px;margin:4px 0">'+esc(x.cmd)+"</pre>":"")+(x.keep?'<p class="mu">'+esc(x.keep)+"</p>":"")+(x.cooldown?'<p class="mu">AI cooldown file says: '+esc(x.cooldown)+"</p>":"")+"</div>"}).join("")}   /* pushhealth_20261103 Needs Brett box */
function tblLines(d){return (d.table?tbl(d.table):"")+((d.lines||[]).length?d.lines.map(function(x){return "<p class='mu'>"+esc(x)+"</p>"}).join(""):"")}
function pageDd(r){var g=r.page;if(!g)return"";var o='<div class="sp" style="border-left:3px solid #30363d;padding-left:9px;margin:8px 0">';
 o+="<p>🗓 <b>Own schedule:</b> "+esc(g.schedule)+"</p>";
 if(g.pending)o+='<p class="mu">⏳ Your change from '+esc(g.pending.at)+' is waiting to be applied (within 5 min): '+esc(JSON.stringify(g.pending.what))+"</p>";else if(g.last_change)o+='<p class="mu">Last schedule change '+esc(g.last_change.at)+": "+esc(g.last_change.ok?(g.last_change.why||"applied"):"refused - "+(g.last_change.why||""))+"</p>";
 o+=g.editable?'<button type="button" class="btn" data-sched="'+esc(r.seat)+'">✏️ Change schedule</button><div class="schedbox"></div>':'<p class="mu">This seat works inside the trading rounds / on messages; to change that, ask Sam (engineer) in the chat.</p>';
 o+="<p>🧠 <b>Brain:</b> "+esc(g.brain_txt)+"</p>";if((g.lessons||[]).length)o+="<p>📚 Latest lessons: "+g.lessons.map(esc).join("<br>")+"</p>";if((g.journal||[]).length)o+='<p class="mu">📓 Journal: '+g.journal.map(esc).join("<br>")+"</p>";
 o+="<p>🤖 <b>AI in use:</b> "+esc(g.ai_txt)+"</p>";if(g.last_round)o+="<p>⏱ Last trading round: "+esc(g.last_round)+"</p>";
 if((g.pushes||[]).length)o+="<p>⏰ Scheduled pushes: "+g.pushes.map(function(x){return esc(String(x.label||"").split(" (")[0]+": "+(x.today?("note "+x.note+", "+(x.woke?"woken":x.skipped?"no wake - "+x.skipped:"wake-up from "+x.open)):"not today"))}).join(" · ")+"</p>";
 return o+"</div>"}
function schedForm(btn){var seat=btn.dataset.sched,box=btn.nextElementSibling;if(!box)return;if(box.firstChild){box.innerHTML="";S.editing=0;return}
 var r=((S.data.seats||{}).rows||[]).filter(function(x){return x.seat===seat})[0]||{};var g=r.page||{};var raw=g.schedule_raw||{};var ses=(raw.sessions||[]).map(function(x){return String(x).toUpperCase()});var cur=+(raw.every_min||30);
 var opts=[10,15,20,30,45,60,90,120,180,240];if(opts.indexOf(cur)<0)opts.push(cur);S.editing=1;
 box.innerHTML='<div class="card" style="margin:6px 0"><label>Look for trades every <select name="every">'+opts.map(function(m){return "<option"+(m===cur?" selected":"")+">"+m+"</option>"}).join("")+"</select> minutes</label>"
  +'<div style="margin:8px 0">while these markets are open:<br>'+(g.sessions_all||[]).map(function(x){return '<label style="margin-right:12px;white-space:nowrap"><input type="checkbox" name="ses" value="'+esc(x)+'"'+(ses.indexOf(x)>=0?" checked":"")+"> "+esc(x)+"</label>"}).join("")+"</div>"
  +'<label>Quiet times (no wake-ups), like 12:00-13:00:<br><input name="quiet" value="'+esc((raw.quiet||[]).join(", "))+'" style="width:100%;font-size:16px"></label>'
  +'<div style="margin-top:8px"><button type="button" class="btn" data-schedsave="'+esc(seat)+'">Save</button><span class="sub schedmsg"></span></div><p class="mu">Applied within 5 minutes (checked again, the old schedule is backed up). A push from Brett or a colleague still wakes the seat any time. Times are WITA.</p></div>'}
function schedSave(btn){var seat=btn.dataset.schedsave,box=btn.closest(".schedbox");if(!box)return;var msg=box.querySelector(".schedmsg");var ses=[].slice.call(box.querySelectorAll("input[name=ses]:checked")).map(function(x){return x.value});
 if(!ses.length){msg.textContent=" pick at least one market";return}btn.disabled=true;msg.textContent=" saving…";
 fetch("/api/seat_schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({seat:seat,every_min:+box.querySelector("select[name=every]").value,sessions:ses,quiet:box.querySelector("input[name=quiet]").value})}).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||("HTTP "+r.status));return j})}).then(function(j){msg.textContent=" ✅ "+(j.note||"saved");S.editing=0;setTimeout(function(){load("seats",true)},2500)}).catch(function(e){msg.textContent=" ❌ "+(e.message||e);btn.disabled=false})}
function tabFromUrl(){var h=(location.hash||"").replace("#","");if(TABS.indexOf(h)>=0)return h;return S.seat?"seats":"home"}
/* ---- tab bar ---- */
function drawBar(){var bar=$("#tabbar");bar.innerHTML=TABS.map(function(t){return '<button type="button" role="tab" id="tab-'+t+'" aria-controls="p-'+t+'" aria-selected="'+(t===S.tab?'true':'false')+'" tabindex="'+(t===S.tab?'0':'-1')+'" data-tab="'+t+'" class="'+(t===S.tab?"on":"")+'" aria-label="'+LBL[t]+'"><span class="i">'+ICON[t]+'</span><span>'+LBL[t]+'</span><span class="b" id="b-'+t+'" style="display:none"></span></button>'}).join("");
 bar.querySelectorAll("button").forEach(function(b){b.addEventListener("click",function(){show(b.dataset.tab)})});
 bar.addEventListener("keydown",function(e){var b=e.target.closest("[data-tab]");if(!b)return;var i=TABS.indexOf(b.dataset.tab),n;if(e.key==="ArrowRight")n=(i+1)%TABS.length;else if(e.key==="ArrowLeft")n=(i+TABS.length-1)%TABS.length;else if(e.key==="Home")n=0;else if(e.key==="End")n=TABS.length-1;else return;e.preventDefault();show(TABS[n]);var next=$("#tab-"+TABS[n]);if(next)next.focus()});badges()}
function badges(){var h=S.data.home||{};var hb=$("#b-health");if(hb){hb.style.display=h.health?"":"none";hb.className="b "+esc((h.health||{}).color||"grey");hb.textContent=(h.health||{}).color==="green"?"✓":(h.health||{}).color==="red"?"!":"?"}
 var tb=$("#b-trades");if(tb){tb.style.display=h.open!=null?"":"none";tb.textContent=h.open}}
function show(t){if(TABS.indexOf(t)<0)t="home";S.tab=t;TABS.forEach(function(x){var p=$("#p-"+x);if(p){p.classList.toggle("on",x===t||S.all);p.setAttribute("aria-hidden",x===t||S.all?"false":"true")}});
 $("#tabbar").querySelectorAll("button").forEach(function(b){var selected=b.dataset.tab===t;b.classList.toggle("on",selected);b.setAttribute("aria-selected",selected?"true":"false");b.tabIndex=selected?0:-1});
 if(!S.all){try{history.replaceState(null,"",location.pathname+location.search+(t==="home"&&!S.seat?"":"#"+t))}catch(e){}}
 if(t==="chat")mountChat();else load(t);updateStatus();if(!S.all)window.scrollTo(0,0)}
/* ---- data ---- */
function load(t,force){
 if(t==="chat"||S.busy[t]||(t==="seats"&&S.editing))return;
 if(!force&&Date.now()<(S.retryAt[t]||0))return;
 if(!force&&S.data[t]&&Date.now()-(S.at[t]||0)<30000){render(t);updateStatus();return}
 S.busy[t]=1;var p=$("#p-"+t);if(p)p.setAttribute("aria-busy","true");updateStatus();
 var u=t==="home"?"/api/home":"/api/eli10?tab="+t;
 return readJSON(u).then(function(j){
  var data=t==="home"?j:(j[t]||j);if(!data||typeof data!=="object"||data.error)throw new Error(data&&data.error||"The server returned no figures");
  S.data[t]=data;S.at[t]=Date.now();S.sourceAt[t]=Date.parse(j.built_utc||data.built_utc||"")||0;
  S.errors[t]=null;S.failures[t]=0;S.retryAt[t]=0;S.busy[t]=0;render(t);if(t==="home")badges();
 }).catch(function(e){
  S.errors[t]=String(e.message||e);S.failures[t]=(S.failures[t]||0)+1;S.retryAt[t]=Date.now()+Math.min(60000,5000*Math.pow(2,S.failures[t]-1));
  if(p&&!S.data[t])p.innerHTML='<div class="card red"><h3>This page could not load</h3><p>Your connection or the server may be busy. The Refresh button above tries again.</p><p class="sub">'+esc(S.errors[t])+'</p></div>';
 }).then(function(){S.busy[t]=0;if(p)p.setAttribute("aria-busy","false");updateStatus()});
}
function render(t){
 var p=$("#p-"+t);if(!p||!R[t])return;if(t==="seats"&&S.editing)return;
 var keep=[].slice.call(p.querySelectorAll("details[open][data-id]")).map(function(d){
  return {id:d.dataset.id,extras:["chartbox","deskbox"].map(function(name){var box=d.querySelector("."+name),b=d.querySelector(name==="chartbox"?"[data-chart]":"[data-desk]");return box&&box.firstChild?{name:name,nodes:[].slice.call(box.childNodes),text:b&&b.textContent}:null}).filter(Boolean)};
 });
 try{p.innerHTML=R[t](S.data[t])}catch(e){p.innerHTML='<div class="card red">This page could not be displayed: '+esc(e.message||e)+'</div>'}
 keep.forEach(function(k){var d=detailById(p,k.id);if(!d)return;d.open=true;k.extras.forEach(function(x){var box=d.querySelector("."+x.name),b=d.querySelector(x.name==="chartbox"?"[data-chart]":"[data-desk]");if(box)x.nodes.forEach(function(n){box.appendChild(n)});if(b&&x.text)b.textContent=x.text})});
 p.querySelectorAll("[data-go]").forEach(function(el){el.setAttribute("role","button");el.tabIndex=0});
 if(t==="seats"&&S.seat){var d=detailById(p,S.seat);if(d){d.open=true;d.scrollIntoView({block:"start"});S.seat=""}}
}
function autonomyCard(value){
 var a=value&&typeof value==="object"?value:{},m=a.metrics||{},s=a.summary||{};
 var color=a.fresh===true&&["green","amber","red"].indexOf(a.status)>=0?a.status:"amber";
 var headline=a.fresh===true?a.headline:"Waiting for a current paper-desk check";
 var count=function(v){return typeof v==="number"&&isFinite(v)&&v>=0&&Math.floor(v)===v?v.toLocaleString("en-AU"):"not reported"};
 var labels={learning:"Learning",teamwork:"AI teamwork",recovery:"Recovery",trading:"Practice trading"};
 var out='<section id="autonomy-card" class="card '+color+'" aria-label="Automatic practice desk status"><h3>'+esc(headline||"Paper-desk check")+'</h3><p class="sub">Practice money only · '+esc(a.checked||"No current check is available yet.")+'</p>';
 out+='<p><b>'+(a.fresh===true?"Learning sample":"Last reported sample")+':</b> '+esc(count(m.completed_trades))+' finished trades · '+esc(count(m.winning_trades))+' won · '+esc(count(m.losing_trades))+' lost.</p>';
 Object.keys(labels).forEach(function(key){out+='<p><b>'+labels[key]+':</b> '+esc(a.fresh===true?s[key]||"Waiting for a current report.":"Waiting for a current report.")+'</p>'});
 out+='<p class="sub">Finished trades can also break even. These checks track what happened; they do not promise future profits.</p>';
 var extra={updated_utc:a.updated_utc||null,fresh:a.fresh===true,metrics:m,details:a.details||{}};
 out+='<details class="row" data-id="autonomy-details"><summary>'+dot("grey")+'<div class="mid"><div class="h">Extra check details</div><div class="s">Optional: counts and the check report</div></div><span class="chev">›</span></summary><div class="dd"><pre style="white-space:pre-wrap;font-size:12px">'+esc(JSON.stringify(extra,null,1).slice(0,6000))+'</pre></div></details></section>';
 return out;
}
/* ---- Home ---- */
R.home=function(h){if(!h)return '<div class="card">Loading…</div>';if(h.error)return '<div class="card red"><h3>Home data error</h3><p>'+esc(h.error)+'</p></div>';var hc=h.health||{};var br=h.brains||{};
 var o='<div class="card '+esc(hc.color)+' hero tap" data-go="health"><span class="bigdot '+esc(hc.color)+'"></span><div><div class="h1">'+esc(h.status)+'</div><div class="sub">'+esc(hc.reason)+' <b>Tap for Health ›</b></div></div></div>';
 o+='<div class="card '+((h.today_shown_aud!=null?h.today_shown_aud:h.today_aud||0)>=0?"green":"red")+'"><div class="sub">Money made today, '+esc(h.today_clock||"since 06:00 WITA")+' (pretend money)</div><div class="big '+cls(h.today_shown_aud!=null?h.today_shown_aud:h.today_aud)+'">'+esc(h.today_txt)+'</div><p>The whole paper book is <b>'+esc(h.today_words)+'</b> = the leaderboard\'s "today" column added up. Since the start on 30 Oct: <b class="'+cls(h.since_aud)+'">'+esc(h.since_txt)+'</b>. The book is worth <b>'+esc(h.equity_txt)+'</b> (it started with '+esc(h.seed_txt)+').</p>'
 +row("grey","Two clocks: "+esc(h.today_txt)+" since 06:00 WITA · "+esc(h.book_today_txt||"—")+" since midnight WITA","tap: why there are two 'today' numbers","","<p><b>Since 06:00 WITA</b> (just after the US close): the big number, the leaderboard and every trader card. <b>Since midnight WITA</b> (the book's own day "+esc(h.book_day)+"): "+esc(h.book_today_txt||"—")+" - the risk rails and the CFO report use this one.</p><p>"+esc(h.note_today)+"</p><p class='mu'>Cash "+esc(h.cash_txt)+" · book updated "+esc(h.book_updated)+" · page built "+esc(h.ts)+"</p>")+'</div>';
 o+='<div class="grid2"><div class="card tap" data-go="trades"><div class="sub">Trades open</div><div class="big2">'+esc(h.open)+'</div><div class="sub">bets we are in right now</div></div><div class="card tap" data-go="trades"><div class="sub">Orders waiting</div><div class="big2">'+esc(h.armed)+'</div><div class="sub">placed, not filled yet ("armed")'+(h.armed_wait_open?' · '+esc(h.armed_wait_open)+(h.armed_wait_open===1?' waits':' wait')+' for its market to open':'')+'</div></div></div>';
 var nb=h.numbers||{};if(nb.txt)o+='<div class="card '+(nb.ok?"green":"red")+' tap" data-go="health"><h3>'+(nb.ok?"✅ The numbers agree":"⚠️ The numbers do not agree")+'</h3><p class="sub">'+esc(nb.txt)+' Checked on every refresh. <b>Tap for Health ›</b></p></div>';
 o+=autonomyCard(h.autonomy);
 o+='<div class="card"><h3>What the desk is doing now</h3><p>'+esc(h.doing)+'</p><p class="sub">'+esc(h.markets)+'</p></div>';
 if((h.best||[]).length||(h.worst||[]).length)o+='<div class="card tap" data-go="seats"><h3>Who is winning today <span class="sub">(since 06:00 WITA, the leaderboard)</span></h3>'+(h.best||[]).map(function(b){return '<div>🏆 '+esc(b.txt)+'</div>'}).join("")+(h.worst||[]).map(function(b){return '<div class="mu">📉 '+esc(b.txt)+'</div>'}).join("")+'<div class="sub">Tap for every seat ›</div></div>';
 o+='<div class="card '+esc(br.color||"grey")+'"><h3>The AI brains</h3><p>'+esc(br.txt||"")+'</p><div>'+(h.ais||[]).map(function(a){return '<span class="chip"><i class="'+esc(a.state==="up"?"green":a.state==="cooling"?"amber":a.state==="down"?"red":"grey")+'"></i>'+esc(a.name)+': '+esc(a.word)+'</span>'}).join("")+'</div></div>';
 if((h.urgent||[]).length)o+='<div class="card red"><h3>Urgent</h3>'+h.urgent.map(function(u){return "<p>"+esc(u)+"</p>"}).join("")+"</div>";
 o+='<div class="card"><a class="btn" href="/desk/chat">💬 Chat full screen</a><a class="btn" href="/classic">🗂 Old page (desktop charts)</a>'+(h.links&&h.links.vd?'<a class="btn" href="'+esc(h.links.vd)+'">Veteran Desk</a>':"")+(h.links&&h.links.war_room?'<a class="btn" href="'+esc(h.links.war_room)+'">War Room</a>':"")+'<a class="btn" href="/hub">Hub</a><div class="sub">PAPER = pretend money, no real trades. Updated '+esc(h.ts)+'.</div></div>';
 return o};
/* ---- Trades ---- */
var KIND={open:["Open now","bets we are in right now"],armed:["Orders waiting to fill","placed, not filled yet - one order each; if its market is closed it waits for the open"],queued:["Ideas lined up for the market open","NOT orders yet - near the open the engine may wake the trader to decide"],closed:["Closed today","finished since midnight WITA (the book's day)"],idea:["Ideas the engine is watching","not placed - a trader may be woken for them"],blocked:["Blocked by a safety check","the engine will not trade these for now"]};
function tradeDd(r){var d=r.d||{};var o="";if(r.size)o+="<p>Size: "+esc(r.size)+(r.fees&&r.fees!=="—"?" · fees so far "+esc(r.fees):"")+"</p>";if(r.since)o+="<p>"+esc(r.since)+(r.mark?" · price checked "+esc(r.mark):"")+"</p>";
 if(r.why)o+="<p>Why: "+esc(r.why)+"</p>";if(r.lift)o+="<p>Tag: "+esc(r.lift)+"</p>";if(r.rail)o+="<p>"+esc(r.rail)+"</p>";if(r.note)o+="<p>Note: "+esc(r.note)+"</p>";if(r.live_src)o+='<p class="mu">live price source: '+esc(r.live_src)+"</p>";
 var l=d.live_data||{};var x=[];if(l.day_pct!=null)x.push("today "+f(l.day_pct)+"%");if(l.vol_vs_avg!=null)x.push("volume "+f(l.vol_vs_avg)+"x the 20-day average");if(l.spread_bps!=null)x.push("spread "+f(l.spread_bps)+" bp");if(l.news&&l.news.n24h!=null)x.push(l.news.n24h+" news items in 24h");if(l.funding_pct!=null)x.push("funding "+f(l.funding_pct)+"%");if(x.length)o+="<p>Live: "+esc(x.join(" · "))+"</p>";
 if(r.grade)o+='<p class="mu">'+esc(r.grade)+" grade · long = betting it goes up, short = betting it goes down · stop = where we get out · target = where we take the win</p>";
 if(r.sym&&r.kind!=="closed")o+='<button type="button" class="btn" data-chart="'+esc(r.sym)+'">📉 Show price chart</button><div class="chartbox"></div>';
 return o+raw(d)}
R.trades=function(t){if(!t)return '<div class="card">Loading…</div>';var rows=t.rows||[];var o='<div class="card"><h3>Every trade in plain words</h3><p class="sub">'+esc(t.summary)+' · updated '+esc(t.ts)+'</p></div>';
 Object.keys(KIND).forEach(function(k){var rs=rows.filter(function(r){return r.kind===k});if(!rs.length)return;o+='<div class="sec">'+esc(KIND[k][0])+' ('+rs.length+') <span style="text-transform:none;font-weight:400">· '+esc(KIND[k][1])+'</span></div><div class="card grid" style="padding:0 10px">'+rs.map(function(r){return row(r.color,esc(r.head),esc(r.sub),r.pnl?'<b class="'+(r.color==="green"?"pos":r.color==="red"?"neg":"")+'">'+esc(r.pnl)+"</b>":"",tradeDd(r),false,tradeKey(r))}).join("")+"</div>"});
 if(!rows.length)o+='<div class="card">No open, armed or queued trades right now.</div>';
 var sc=t.small_coin||{};o+='<div class="card grey">'+row("grey","Small-coin side book (Kai, PAPER)",esc(sc.txt||""),"","<p>"+esc(sc.rule||"")+"</p>"+((sc.rows||[]).length?'<div class="tw"><table><tr><th>seat</th><th>coin</th><th>side</th><th>status</th><th>risk A$</th><th>P&L A$</th></tr>'+sc.rows.map(function(r){return "<tr><td>"+esc(r.seat)+"</td><td>"+esc(r.ticker)+"</td><td>"+esc(r.side)+"</td><td>"+esc(r.status)+"</td><td>"+esc(r.risk_aud)+"</td><td>"+esc(r.pnl_aud)+"</td></tr>"}).join("")+"</table></div>":"<p class='mu'>no small-coin trades yet</p>"))+glossary(t.glossary)+'</div>';return o};
/* ---- Seats ---- */
function seatDd(r){var o="";if(r.state)o+="<p>"+dot(r.state_color)+" "+esc(r.state)+"</p>";o+="<p>"+dot(r.brain_color)+" Brain: "+esc(r.brain)+(r.brain_detail?' <span class="mu">('+esc(r.brain_detail)+")</span>":"")+"</p>";if(r.last_action)o+="<p>Last action: "+esc(r.last_action)+"</p>";o+=pageDd(r);
 if(!r.admin){o+="<p>"+esc(r.money)+(r.week_txt&&r.week_txt!=="—"?" · "+esc(r.week_txt)+" this week":"")+(r.equity_txt&&r.equity_txt!=="—"?" · book worth "+esc(r.equity_txt):"")+"</p>";o+="<p>"+dot(r.light)+" Profit light: "+esc(r.light_why)+"</p>";
  if((r.positions||[]).length)o+="<p>In the book: "+r.positions.map(function(p){return esc(p.sym+" "+p.side+(p.status==="armed"?" (armed, waiting to fill)":""))}).join(", ")+"</p>";
  o+='<p class="mu">Wake-ups today '+esc(r.wakes_today)+' (budget + free check-ins) · failed AI calls '+esc(r.fails)+' · scanned '+esc(r.scanned==null?"—":r.scanned)+' instruments, '+esc(r.n_A)+' A ideas, '+esc(r.n_Aplus)+' A+ ideas · list updated '+esc(r.updated)+"</p>";
  if(r.half_risk||r.red_streak)o+='<p class="err">'+(r.half_risk?"HALF RISK (3 losing rounds in a row, trades half size until a winning round). ":"")+(r.red_streak?"Losing rounds in a row: "+esc(r.red_streak):"")+"</p>";
  if(r.plan)o+='<p>Recovery plan ('+esc(r.plan.when)+", "+esc(r.plan.status)+"): "+esc(r.plan.what)+(r.plan.fix?" → "+esc(r.plan.fix):"")+"</p>";
  if(r.stats)o+="<p>Closed trades: "+esc(r.stats.trades)+" · win rate "+esc(Math.round((r.stats.win_rate||0)*100))+"% · average "+esc(r.stats.avg_R)+"R · net "+esc(r.stats.net_aud)+"</p>";
  if((r.mark_missing||[]).length)o+='<p class="err">No live price for: '+esc(r.mark_missing.join(", "))+"</p>";
  if((r.top10||[]).length)o+='<details class="row"><summary>'+dot("grey")+'<div class="mid"><div class="h">Top 10 ideas on the list</div><div class="s">score = how good the setup looks (100 best) · exp R = expected win in R · size = natural size % of the book</div></div><span class="chev">›</span></summary><div class="dd"><div class="tw"><table><tr><th>#</th><th>what</th><th>side</th><th>grade</th><th>score</th><th>exp R</th><th>size</th><th>market open</th><th>wake-up</th></tr>'+r.top10.map(function(x){return "<tr><td>"+esc(x.rank)+"</td><td>"+esc(x.sym)+"</td><td>"+esc(x.side)+"</td><td>"+esc(x.grade)+"</td><td>"+esc(x.score)+"</td><td>"+esc(x.exp_R==null?"—":x.exp_R)+"</td><td>"+esc(x.size_pct==null?"—":x.size_pct+"%")+"</td><td>"+esc(x.open)+"</td><td>"+esc(x.wake)+"</td></tr>"}).join("")+"</table></div></div></details>";
  if(r.last_wake)o+="<p class='mu'>Last wake-up: "+esc(r.last_wake.id)+" "+esc(r.last_wake.status)+" "+esc(r.last_wake.when)+"</p>";
  (r.looks||[]).forEach(function(l){o+="<p class='mu'>"+esc(l.label)+": "+esc(l.status||"")+" "+esc(l.when||"")+" — "+esc(l.text||"")+"</p>"});
  o+='<button type="button" class="btn" data-desk="'+esc(r.seat)+'">📊 More about this seat (hit rate, lessons)</button><a class="btn" href="/classic?desk='+esc(r.seat)+'">Old desk page with charts</a><div class="deskbox"></div>'}
 return o}
R.seats=function(t){if(!t)return '<div class="card">Loading…</div>';var rows=t.rows||[];var br=t.brains||{};
 var tt=t.total||{};var o='<div class="card '+esc(br.color||"grey")+'"><h3>Every seat in plain words</h3><p>All traders together: <b class="'+(String(tt.today_txt||"").charAt(0)==="-"?"neg":"pos")+'">'+esc(tt.today_txt||"—")+'</b> today since 06:00 WITA (= the big number on Home) · '+esc(tt.since_txt||"—")+' since start · '+esc(tt.open)+' open, '+esc(tt.armed)+' waiting to fill.</p><p>'+esc(br.txt||"")+'</p><p class="sub">'+esc(t.window||"")+'. Prizes: the top 3 seats by today\'s profit win '+(t.prizes||[]).map(function(p){return "A$"+Number(p).toLocaleString("en-AU")}).join(" / ")+' of extra paper money. Tap a seat for details. Updated '+esc(t.ts)+'</p></div>';
 var pw=t.push;if(pw)o+='<div class="card '+(pw.live?"green":"grey")+'">'+row(pw.live?"green":"grey","⏰ "+esc(pw.head),"scheduled pushes at each market open: hunt for A/A+ ideas, report blocks to Marcus · tap for the list and how to change it","",tbl({cols:["push","when (WITA)","who","today"],rows:pw.rows})+(pw.error?'<p class="err">'+esc(pw.error)+'</p>':"")+'<p class="mu">'+esc(pw.how)+' Last check '+esc(pw.last_run||"-")+'.</p>')+'</div>';   /* pushhealth_20261103 */
 var tr=rows.filter(function(r){return !r.admin}),ad=rows.filter(function(r){return r.admin});
 o+='<div class="sec">Traders ('+tr.length+')</div><div class="card grid" style="padding:0 10px">'+tr.map(function(r){return row(r.light,esc((r.rank?"#"+r.rank+" ":"")+r.name)+' <span class="mu">'+esc(r.desk)+'</span>','<span class="'+cls(r.today_aud)+'"><b>'+esc(r.today_txt)+'</b> today (06:00)</span> · '+esc(r.since_txt)+' since start · '+esc(r.open)+' open, '+esc(r.armed)+' waiting',dot(r.brain_color)+" "+esc(r.brain)+" · "+esc(r.state),seatDd(r),false,r.seat)}).join("")+"</div>";
 if(ad.length)o+='<div class="sec">Desk admins ('+ad.length+')</div><div class="card grid" style="padding:0 10px">'+ad.map(function(r){return row(r.brain_color,esc(r.name)+' <span class="mu">'+esc(r.desk)+'</span>',esc(r.money),esc(r.brain)+(r.last_action?" · "+esc(r.last_action):""),seatDd(r),false,r.seat)}).join("")+"</div>";
 o+='<div class="card grey">'+row("grey","House rules for seats","prizes, losing streaks, recovery plans","","<p>"+esc(t.accountability_rule||"")+"</p>"+(t.rules||[]).map(function(x){return "<p class='mu'>"+esc(x)+"</p>"}).join(""))+glossary(t.glossary)+"</div>";return o};
/* ---- Health ---- */
var GRP={checks:["Engine and checks","is the machine running and are the numbers right?"],ai:["AI brains","the minds that make the trading decisions"],feeds:["Data feeds","prices, news and the borrow list"],sgp:["Singapore server","the machine the whole desk runs on - checked every 5 min; safe things are fixed automatically, the rest goes to Sam (engineer)"],broken:["Broken right now","needs fixing"],info:["Known limits (not broken)","things we know and accept"]};
R.health=function(t){if(!t)return '<div class="card">Loading…</div>';var rows=t.rows||[];
 var o='<div class="card '+esc(t.color)+' hero"><span class="bigdot '+esc(t.color)+'"></span><div><div class="h1">'+esc(t.color==="green"?"Everything is healthy.":t.color==="amber"?"Mostly healthy - "+t.n_amber+(t.n_amber===1?" thing needs":" things need")+" a look.":"Something is broken - "+t.n_red+(t.n_red===1?" red item.":" red items."))+'</div><div class="sub">'+esc(t.reason)+'</div></div></div>';
 Object.keys(GRP).forEach(function(g){var rs=rows.filter(function(r){return (r.group||"checks")===g});if(!rs.length)return;var open=g!=="info";o+='<details class="card"'+(open?" open":"")+' style="padding:10px 14px"><summary style="cursor:pointer;font-weight:700;list-style:none">'+esc(GRP[g][0])+' ('+rs.length+') <span class="sub" style="font-weight:400">· '+esc(GRP[g][1])+'</span></summary><div class="grid">'+rs.map(function(r){return row(r.dot,esc(r.head),esc(r.sub),"",r.d?(r.d.items?needsBrett(r.d):(r.d.table||r.d.lines)?tblLines(r.d):r.id==="feeds"&&r.d.feeds?'<div class="tw"><table><tr><th>feed</th><th>state</th><th>last update</th></tr>'+r.d.feeds.map(function(x){return "<tr><td>"+dot(x.dot)+" "+esc(x.name)+"</td><td>"+esc(x.dot==="green"?"working":x.problem||x.status)+"</td><td class=nw>"+esc(x.age)+"</td></tr>"}).join("")+"</table></div>":r.id==="numbers"?((r.d.bad||[]).length?"<p class='err'>"+r.d.bad.map(esc).join("<br>")+"</p>":"<p>Every pair matched: today since 06:00 WITA (big number = leaderboard column = trader cards = Seats total), each seat's today / since start / book worth = its leaderboard row, since start and book worth = the 11 seats added up, trades open and orders waiting = the same on Home, Trades, Seats and the leaderboard.</p>")+'<p class="mu"><a href="/api/numbers">Full check list (/api/numbers)</a></p>':r.id==="coverage"&&r.d.by_market?'<div class="tw"><table><tr><th>market</th><th>available</th><th>scored</th><th>no data</th><th>stale</th><th>no volume</th></tr>'+Object.keys(r.d.by_market).map(function(k){var v=r.d.by_market[k]||{};return "<tr><td>"+esc(k)+"</td><td>"+esc(v.available)+"</td><td>"+esc(v.scored)+"</td><td>"+esc(v.no_data)+"</td><td>"+esc(v.stale)+"</td><td>"+esc(v.no_volume)+"</td></tr>"}).join("")+"</table></div>":raw(r.d)):"<p class='mu'>no more detail</p>",r.id==="needs_brett"&&r.dot!=="green")}).join("")+"</div></details>"});
 if((t.wakes||[]).length)o+='<div class="card">'+row("grey","Recent engine wake-ups ("+t.wakes.length+")","each line = the engine woke a trader to look at fresh ideas","",'<div class="tw"><table><tr><th>when</th><th>who</th><th>status</th><th>ideas</th></tr>'+t.wakes.map(function(w){return "<tr><td>"+esc(w.when)+"</td><td>"+esc(w.seats)+"</td><td>"+esc(w.status)+(w.result?" "+esc(w.result):"")+"</td><td>"+esc(w.ideas)+"</td></tr>"}).join("")+"</table></div>")+"</div>";
 var l=t.links||{};o+='<div class="card"><a class="btn" href="/classic">🗂 Old page</a><a class="btn" href="'+esc(l.raw||"/api/state")+'">Raw data (/api/state)</a><a class="btn" href="/hub">Hub</a>'+(l.vd?'<a class="btn" href="'+esc(l.vd)+'">Veteran Desk</a>':"")+(l.war_room?'<a class="btn" href="'+esc(l.war_room)+'">War Room</a>':"")+'<div class="sub">dash data built '+esc(t.built)+' in '+esc(t.build_secs)+'s · updated '+esc(t.ts)+'</div>'+glossary(t.glossary)+'</div>';return o};
/* ---- Chat (existing renderer) ---- */
function mountChat(){var p=$("#p-chat");if(p.dataset.up)return;p.dataset.up="1";p.innerHTML='<div id="dchat" data-mode="hub" data-theme="light"></div>';var s=document.createElement("script");s.src="/chat.js";s.onerror=function(){p.innerHTML='<div class="card red"><h3>Chat could not load</h3><p><a href="/desk/chat">Open the chat page</a></p></div>'};document.body.appendChild(s)}
/* ---- charts (tap to load) ---- */
function draw(cv,d,o){var r=window.devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;cv.width=w*r;cv.height=h*r;var g=cv.getContext("2d");g.scale(r,r);g.clearRect(0,0,w,h);var P=(d&&d.pts)||[];
 if(P.length<2){g.fillStyle="#8b949e";g.font="12px system-ui";g.fillText("no price data yet ("+((d&&d.src)||"loading")+")",8,20);return}
 var lv=[o.entry,o.stop,o.target].filter(function(x){return x!=null&&isFinite(x)});var ys=P.map(function(p){return p[1]});var lo=Math.min.apply(null,ys.concat(lv)),hi=Math.max.apply(null,ys.concat(lv));var pad=(hi-lo)*0.06||1;lo-=pad;hi+=pad;
 var t0=P[0][0],t1=P[P.length-1][0]||t0+1;var X=function(t){return 4+(w-58)*(t-t0)/Math.max(t1-t0,1)},Y=function(v){return 4+(h-18)*(1-(v-lo)/(hi-lo))};
 var line=function(v,c,lab){if(v==null||!isFinite(v))return;g.strokeStyle=c;g.setLineDash([5,4]);g.beginPath();g.moveTo(4,Y(v));g.lineTo(w-54,Y(v));g.stroke();g.setLineDash([]);g.fillStyle=c;g.font="10px system-ui";g.fillText(lab+" "+f(v),w-52,Y(v)+3)};
 line(o.entry,"#58a6ff","in");line(o.stop,"#f85149","stop");line(o.target,"#2ea043","target");
 g.strokeStyle="#e6edf3";g.lineWidth=1.4;g.beginPath();P.forEach(function(p,i){i?g.lineTo(X(p[0]),Y(p[1])):g.moveTo(X(p[0]),Y(p[1]))});g.stroke();
 var L=P[P.length-1];g.fillStyle="#ffd33d";g.beginPath();g.arc(X(L[0]),Y(L[1]),3,0,7);g.fill();g.fillStyle="#8b949e";g.font="10px system-ui";
 var tf=function(t){return new Date(t*1000).toLocaleString("en-AU",{timeZone:"Asia/Makassar",hour12:false,day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})};g.fillText(tf(t0)+" → "+tf(L[0])+" WITA · "+(d.src||""),6,h-3)}
function chart(btn){var sym=btn.dataset.chart,box=btn.nextElementSibling;if(!box)return;if(box.firstChild){box.innerHTML="";btn.textContent="📉 Show price chart";return}btn.textContent="Loading chart…";
 var d=btn.closest("details");var rawd={};try{rawd=JSON.parse(d.querySelector("pre").textContent)}catch(e){}var o={entry:rawd.entry!=null?rawd.entry:rawd.limit,stop:rawd.stop,target:rawd.target,side:rawd.side};
 readJSON("/api/chart?sym="+encodeURIComponent(sym)).then(function(j){var cv=document.createElement("canvas");cv.className="ch";box.appendChild(cv);draw(cv,j,o);btn.textContent="Hide chart";var lp=document.createElement("div");lp.className="sub";lp.textContent="blue = where we got in, red = stop (we get out), green = target (we take the win), yellow dot = latest point on this chart"+(j.last!=null?" · last price "+f(j.last):"")+" · chart snapshot checked "+new Date().toLocaleTimeString("en-AU",{timeZone:"Asia/Makassar",hour12:false})+" WITA. Hide and show it again to refresh.";box.appendChild(lp)}).catch(function(e){btn.textContent="Chart failed: "+(e.message||e)})}
function desk(btn){var seat=btn.dataset.desk,box=btn.parentNode.querySelector(".deskbox");if(!box)return;if(box.firstChild){box.innerHTML="";return}btn.textContent="Loading…";
 readJSON("/api/desk?seat="+encodeURIComponent(seat)).then(function(d){var s=d.stats||{};var pc=function(x){return x==null?"—":Math.round(x*100)+"%"};var a=function(x){return x==null?"—":(x<0?"-":"")+"A$"+Math.abs(x).toLocaleString("en-AU",{maximumFractionDigits:0})};
  var h="<p><b>"+esc(d.desk_name)+"</b>: book day since midnight WITA "+a(d.today_pnl_aud)+" (the 06:00 'today' is on the row above) · closed trades "+esc(s.n||0)+" · win rate "+pc(s.win_rate)+" · average "+esc(s.avg_R==null?"—":s.avg_R)+"R · net after costs "+a(s.net_aud)+" (fees "+a(d.fees_aud)+")</p>";
  h+="<p>Hit rate by grade: "+(Object.keys(d.by_grade||{}).map(function(g){var v=d.by_grade[g];return esc(g)+" "+pc(v.win_rate)+" ("+esc(v.n)+" trades, "+esc(v.avg_R)+"R, "+a(v.net_aud)+")"}).join(" · ")||"no closed trades yet")+"</p>";
  h+="<p>Lifted-trade tags (ideas pushed above the usual bar): "+(Object.keys(d.lift_tags||{}).map(function(g){var v=d.lift_tags[g];return esc(g.replace(/_/g," "))+" ×"+esc(v.n)+" "+a(v.net_aud)}).join(" · ")||"none")+"</p>";
  h+="<p class='mu'>How it learns: "+esc(d.learning||"")+"</p>";if((d.lessons||[]).length)h+="<p>Last lessons: "+d.lessons.slice(0,5).map(function(l){return esc((l.wita||"")+" "+l.ticker+" "+l.side+" "+(l.grade||"")+" "+a(l.net_aud)+" — "+l.lesson)}).join("<br>")+"</p>";
  var ar=Object.keys(d.arm_reviews||{}).map(function(k){var r=d.arm_reviews[k];return esc(r.ticker+" "+r.side)+" — Grok "+esc((r.grok||{}).verdict||(r.grok||{}).status||"—")+" · Claude "+esc((r.claude||{}).verdict||(r.claude||{}).status||"—")}).join("; ");h+="<p>Second looks before arming (advice only, never block): "+(ar||"none yet")+"</p>";
  box.innerHTML=h;btn.textContent="📊 Hide"}).catch(function(e){btn.textContent="Could not load: "+(e.message||e)})}
document.addEventListener("click",function(e){var sc=e.target.closest("[data-sched]");if(sc){schedForm(sc);return}var sv=e.target.closest("[data-schedsave]");if(sv){schedSave(sv);return}var b=e.target.closest("[data-chart]");if(b){chart(b);return}var d=e.target.closest("[data-desk]");if(d){desk(d);return}var g=e.target.closest("[data-go]");if(g&&!S.all){show(g.dataset.go)}});
/* ---- boot ---- */
try{var h0=JSON.parse(($("#home0")||{}).textContent||"null");if(h0&&!h0.error){S.data.home=h0;S.at.home=Date.now();S.sourceAt.home=Date.parse(h0.built_utc||"")||0}}catch(e){}
if(S.all)document.body.classList.add("all");
drawBar();render("home");
if(S.all){TABS.forEach(function(t){if(t==="chat")mountChat();else load(t,true)});S.tab="home"}else show(tabFromUrl());
if(S.desktop){var ts=document.createElement("script");ts.src="/ticker.js";ts.defer=true;document.head.appendChild(ts)}
document.addEventListener("click",function(e){if(e.target.closest("#refresh-now"))load(S.tab||"home",true)});
document.addEventListener("keydown",function(e){var el=e.target.closest("[data-go]");if(el&&e.target===el&&(e.key==="Enter"||e.key===" ")){e.preventDefault();show(el.dataset.go)}});
window.addEventListener("online",function(){load(S.tab||"home",true)});
setInterval(function(){updateStatus();if(document.hidden)return;if(S.all){TABS.forEach(function(t){if(t!=="chat"&&Date.now()-(S.at[t]||0)>30000)load(t,false)});return}var t=S.tab;if(t!=="chat"&&!(t==="seats"&&S.editing)&&Date.now()-(S.at[t]||0)>30000)load(t,false);if(t!=="home"&&Date.now()-(S.at.home||0)>60000)load("home",false)},5000);
document.addEventListener("visibilitychange",function(){if(!document.hidden&&S.tab&&S.tab!=="chat")load(S.tab,true)});
window.addEventListener("hashchange",function(){var t=tabFromUrl();if(t!==S.tab)show(t)});
})();
