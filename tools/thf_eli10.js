(function(){
"use strict";
var H = (typeof HOME0 === "object" && HOME0) || {};
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]})}
function tab(){var h=(location.hash||"#home").replace("#","");return ["home","trades","chat","seats","health"].indexOf(h)>=0?h:"home"}
function paintNav(name){
 document.body.dataset.tab=name;
 document.querySelectorAll("nav button").forEach(function(b){b.setAttribute("aria-current", b.dataset.tab===name?"page":"false")});
}
function figure(v){return (!v || v==="—" || v==="None") ? "No figure" : v}
function homeHtml(h){
 var health=h.health||{};
 var fresh=document.getElementById("fresh");
 if(fresh) fresh.textContent = h.ts ? ("Snapshot "+h.ts+". These figures are the desk's own counters, not a separate audit.") : "No desk snapshot in this page.";
 return '<section class="hero">'
  +'<p class="health '+(health.color||"grey")+'"><i></i><span>'+esc(health.reason||"Health has not loaded.")+'</span></p>'
  +'<p class="kicker">Since 06:00 WITA · leaderboard today. Trader cards use this clock.</p>'
  +'<p class="num">'+esc(figure(h.today_txt))+'</p>'
  +'<p class="kicker">Since midnight WITA · book day. Risk rails and the CFO report use this clock.</p>'
  +'<p class="num small">'+esc(figure(h.book_today_txt))+'</p>'
  +'<p class="meta">'+esc(figure(h.since_txt))+' since the book opened · worth '+esc(figure(h.equity_txt))+'</p>'
  +'<p class="counts">'+esc(h.open==null?"—":h.open)+' trades open · '+esc(h.armed==null?"—":h.armed)+' orders waiting</p>'
  +'<details><summary>Why there are two today figures</summary><p>'+esc(h.note_today||"")+'</p></details>'
  +'<details><summary>Full status sentence</summary><p>'+esc(h.status||"")+'</p></details>'
  +'</section><details class="guide"><summary>How this paper desk works</summary><p>Transparent Trading is a paper trading engine for a retail user. It is not a live broker and it carries no certification. Money figures are whatever the desk snapshot supplies. A picture of the screen is not an accounting check.</p></details>';
}
function list(rows, line){
 if(!rows||!rows.length) return '<p class="meta">Nothing in this snapshot.</p>';
 return rows.map(line).join("");
}
function showTrades(d){
 return '<section class="panel"><p class="meta">'+esc((d&&d.summary)||"")+'</p>'+list((d&&d.rows)||[], function(r){
  return '<div class="row"><b>'+esc(r.head||r.sym||"Trade")+'</b><span>'+esc(r.sub||"")+' '+esc(r.pnl||"")+'</span></div>';
 })+'</section>';
}
function showSeats(d){
 return '<section class="panel"><p class="meta">Reports go up: seat, then Marcus, then the Head Trader, then Brett.</p>'+list((d&&d.rows)||[], function(r){
  return '<div class="row"><b>'+esc(r.name||r.seat)+'</b><span>'+esc(r.desk||"")+' · '+esc(r.money||"")+' · '+esc(r.brain||r.state||"")+'</span></div>';
 })+'</section>';
}
function showHealth(d){
 return '<section class="panel">'+list((d&&d.rows)||[], function(r){
  return '<div class="row"><b>'+esc(r.head||"Check")+'</b><span>'+esc(r.sub||"")+'</span></div>';
 })+'</section>';
}
function mountChat(){
 var main=document.getElementById("main");
 if(document.getElementById("dchat")) return;
 main.innerHTML='<div id="dchat" data-mode="hub" data-theme="light"></div>';
 var s=document.createElement("script"); s.src="/chat.js"; document.body.appendChild(s);
}
function show(name){
 paintNav(name);
 var main=document.getElementById("main");
 if(name==="home"){main.innerHTML=homeHtml(H);return}
 if(name==="chat"){mountChat();return}
 main.innerHTML='<p class="meta" style="padding:12px">Loading.</p>';
 fetch("/api/eli10?tab="+encodeURIComponent(name),{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){
  if(tab()!==name) return;
  if(name==="trades") main.innerHTML=showTrades(j.trades);
  else if(name==="seats") main.innerHTML=showSeats(j.seats);
  else main.innerHTML=showHealth(j.health);
 }).catch(function(){ if(tab()===name) main.innerHTML='<p class="meta" style="padding:12px">This tab did not load. The snapshot was not replaced with a guess.</p>'; });
}
document.querySelectorAll("nav button").forEach(function(b){b.addEventListener("click",function(){location.hash=b.dataset.tab})});
document.getElementById("reload").addEventListener("click",function(){location.reload()});
window.addEventListener("hashchange",function(){show(tab())});
show(tab());
})();
