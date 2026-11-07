/* heartreply_20261103: heart (double-tap / Like), tap a message -> Like / Reply / Copy; reply = re:<root> + quote after '||'.
   Transparent Trading display chrome for /desk/chat (data-mode=page) and the embedded desk chat (data-mode=embed).
   Internal route and service names stay THF. No libraries. Reads GET /api/chat_home + /api/chat_thread?seat=X&after=K,
   writes through POST /api/funder_msg, /api/advisor_note, /api/chat_img. Unread marks = localStorage. */
(function(){
"use strict";
var root=document.getElementById("dchat");if(!root||root.dataset.up)return;root.dataset.up="1";
var PAGE=root.dataset.mode==="page";
var HUB=root.dataset.mode==="hub";
var CSS='#dchat{--bg:#f4f7f6;--card:#ffffff;--me:#e7eef4;--tx:#142033;--mu:#3e5160;--ac:#1f4e5f;--bd:#d5dee6;--hd:#10243e;--hdtx:#f4f7f6;background:var(--bg);color:var(--tx);font:16px Inter,"Noto Sans","Segoe UI",sans-serif;display:flex;flex-direction:column;position:relative;overflow:hidden;-webkit-font-smoothing:antialiased;text-align:left}'
+'@media (prefers-color-scheme:dark){#dchat:not([data-theme=light]){--bg:#0c1622;--card:#152433;--me:#1d3348;--tx:#e7eef2;--mu:#c5d0d8;--ac:#8fb8c4;--bd:#2a3e50;--hd:#071422;--hdtx:#e7eef2}}'
+'#dchat[data-mode=page]{height:100vh;height:100dvh}'
+'#dchat[data-mode=hub]{height:100%;max-width:none;margin:0;border:0;border-radius:0}'
+'#dchat[data-mode=hub] .dc-hd{min-height:40px;padding:4px 10px;background:var(--bg);color:var(--tx);border-bottom:1px solid var(--bd)}'
+'#dchat[data-mode=hub] .dc-hd h2,#dchat[data-mode=hub] .dc-hd a{color:var(--tx)}'
+'#dchat[data-mode=embed]{height:min(78vh,calc(100dvh - 24px));max-width:820px;margin:8px auto;border:1px solid var(--bd);border-radius:14px}'
+'#dchat *{box-sizing:border-box}#dchat a{color:var(--ac);text-decoration:none}#dchat button{font-family:inherit}'
+'.dc-hd{display:flex;align-items:center;gap:10px;padding:8px 12px;padding-top:max(8px,env(safe-area-inset-top));border-bottom:1px solid var(--hd);flex:none;min-height:56px;background:var(--hd);color:var(--hdtx)}'
+'.dc-hd h2{font-size:16px;margin:0;flex:1;font-weight:600;line-height:1.15;min-width:0;letter-spacing:-.02em}.dc-hd .sub{font-size:12px;color:#c5d0d8;font-weight:400;white-space:nowrap}'
+'.tt-brand{display:flex;align-items:center;gap:8px;min-width:0}.tt-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tt-mark{width:22px;height:22px;flex:none;color:var(--hdtx)}'
+'.dc-hd .dc-ic,.dc-hd a{color:var(--hdtx)}.dc-hd .dc-paper{color:#d5dee6;border-color:#3e5160}'
+'.dc-ic{background:none;border:0;color:var(--tx);font-size:24px;width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none;cursor:pointer;padding:0;line-height:1}.dc-ic:active{background:var(--card)}'
+'.dc-av{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;flex:none;font-size:17px;position:relative}'
+'.dc-sec{padding:8px 14px 2px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--mu)}'
+'.dc-star{font-size:11px;color:var(--ac);font-weight:700;letter-spacing:.04em}'
+'.dc-dotav{position:absolute;top:1px;right:1px;width:13px;height:13px;border-radius:50%;background:var(--ac);border:2px solid #000}'
+'.dc-list{flex:1;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}'
+'.dc-row{display:flex;gap:12px;align-items:center;padding:10px 14px;border:0;border-bottom:1px solid var(--bd);cursor:pointer;width:100%;background:var(--card);color:var(--tx);text-align:left;font-size:16px;min-height:64px}.dc-row:active{background:#e7eef2}'
+'.dc-row .dc-av{width:48px;height:48px;font-size:20px}.dc-row .mid{flex:1;min-width:0}.dc-row .l1{display:flex;align-items:center;gap:6px}'
+'.dc-row .l1 b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:0 0 auto;max-width:62%}.dc-row .dk{color:var(--mu);font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 auto;min-width:0}'
+'.dc-row .tm{margin-left:auto;color:var(--mu);font-size:12px;white-space:nowrap;padding-left:6px}.dc-row .l2{color:#243646;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}.dc-row.un .l2{color:var(--tx);font-weight:600}'
+'.dc-dot{width:9px;height:9px;border-radius:50%;background:var(--ac);flex:none}'
+'.dc-pill{background:var(--ac);color:#fff;border-radius:11px;min-width:22px;height:22px;padding:0 7px;font-size:13px;font-weight:600;display:flex;align-items:center;justify-content:center;flex:none}'
+'.dc-sb{display:flex;gap:8px;padding:8px 12px;padding-bottom:max(8px,env(safe-area-inset-bottom));border-top:1px solid var(--bd);flex:none;background:var(--bg)}'
+'.dc-sb input{flex:1;min-width:0;background:var(--card);border:1px solid var(--bd);border-radius:12px;color:var(--tx);font-size:16px;padding:10px 12px;outline:none}'
+'.dc-log{flex:1;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:10px 10px 4px;display:flex;flex-direction:column}'
+'.dc-day{align-self:center;color:var(--mu);font-size:12px;margin:8px 0 4px}'
+'.dc-m{display:flex;flex-direction:column;align-items:flex-start;margin:4px 0;max-width:88%}.dc-m.me{align-self:flex-end;align-items:flex-end}'
+'.dc-b{background:#fff;border:1px solid #8aa0b0;border-radius:18px;padding:10px 14px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font-size:16px;min-width:0;max-width:100%;color:#142033}.dc-m.me .dc-b{background:#e7eef4;border-color:#1f4e5f}'
+'.dc-b img{max-width:100%;border-radius:12px;display:block;margin-top:6px}.dc-b.clip{max-height:14em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 75%,transparent)}'
+'.dc-more{background:none;border:0;color:var(--ac);font-size:14px;padding:2px 6px;cursor:pointer}'
+'.dc-q{border-left:3px solid var(--ac);color:var(--mu);font-size:13px;padding:1px 8px;margin:0 0 6px;white-space:normal;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}'
+'.dc-who{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--mu);margin:0 6px 3px}.dc-who .dc-av{width:20px;height:20px;font-size:10px}'
+'.dc-mt{font-size:11px;color:var(--mu);margin:3px 8px 0;display:flex;gap:6px;align-items:center;flex-wrap:wrap}'
+'.dc-chip{display:inline-block;padding:0 7px;border-radius:9px;font-size:11px;line-height:17px;background:#2c2c2e;color:#cfcfd4}.dc-chip.sp{background:#5a3a00;color:#ffd27a}.dc-chip.cd{background:#3a2a2a;color:#f0b0a8}'
+'details.dc-rl{align-self:stretch;margin:3px 0}details.dc-rl summary{list-style:none;cursor:pointer;color:var(--mu);font-size:13px;padding:5px 10px;border:1px dashed #333;border-radius:14px;display:inline-block}'
+'details.dc-rl summary::-webkit-details-marker{display:none}details.dc-rl .dc-b{margin-top:6px;font-size:14px;color:#c8c8cc;background:#121214}'
+'details.dc-grp{align-self:stretch;display:flex;flex-direction:column}details.dc-grp>summary{list-style:none;cursor:pointer;color:var(--ac);font-size:13px;padding:4px 10px;align-self:flex-start}details.dc-grp>summary::-webkit-details-marker{display:none}'
+'.dc-nz{color:var(--mu);font-size:12px;margin:2px 6px;white-space:pre-wrap;overflow-wrap:anywhere}'
+'.dc-typ{display:flex;align-items:center;gap:8px;color:var(--mu);font-size:14px;padding:3px 14px;min-height:22px;flex:none}.dc-typ .dc-av{width:26px;height:26px;font-size:12px}'
+'.dc-typ .dots{display:inline-block;animation:dcb 1.2s infinite}@keyframes dcb{0%,100%{opacity:.3}50%{opacity:1}}'
+'.dc-prev{display:flex;gap:6px;flex-wrap:wrap;padding:0 12px 4px}.dc-prev .th{position:relative;width:60px;height:60px;border-radius:10px;overflow:hidden;border:1px solid var(--bd)}'
+'.dc-prev .th img{width:100%;height:100%;object-fit:cover}.dc-prev .th button{position:absolute;top:2px;right:2px;background:#a33;color:#fff;border:0;border-radius:50%;width:20px;height:20px;font-size:12px;line-height:20px;padding:0}'
+'.dc-comp{flex:none;border-top:1px solid var(--bd);background:var(--bg);padding:6px 10px;padding-bottom:max(8px,env(safe-area-inset-bottom))}'
+'.dc-pills{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:2px 0 8px;align-items:center}.dc-pills::-webkit-scrollbar{display:none}'
+'.dc-pl{flex:none;background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:16px;height:32px;padding:0 12px;font-size:14px;display:flex;align-items:center;gap:4px;cursor:pointer;-webkit-appearance:none;appearance:none;max-width:36vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:0;padding:0 10px}.dc-pl .lbl{display:none}@media(min-width:430px){.dc-pl .lbl{display:inline}}'
+'select.dc-pl{padding-right:22px;background-image:linear-gradient(45deg,transparent 50%,var(--mu) 50%),linear-gradient(135deg,var(--mu) 50%,transparent 50%);background-position:calc(100% - 13px) 13px,calc(100% - 9px) 13px;background-size:4px 4px;background-repeat:no-repeat}'
+'.dc-pl.on{background:var(--ac);border-color:var(--ac);color:#fff}.dc-pl.sp{background:#5a3a00;border-color:#8a5a00;color:#ffd27a}.dc-pl.sp.on{background:#c77a00;border-color:#c77a00;color:#fff}'
+'.dc-in{display:flex;align-items:flex-end;gap:4px;background:var(--card);border-radius:24px;padding:4px 5px 4px 4px}'
+'.dc-in textarea{flex:1;min-width:0;background:none;border:0;color:var(--tx);font:16px -apple-system,system-ui,sans-serif;resize:none;padding:9px 4px;max-height:30vh;line-height:1.3;outline:none;margin:0}'
+'.dc-in .dc-ic{width:38px;height:38px;font-size:24px;color:var(--mu)}.dc-in .dc-ic.on{color:#f55}'
+'.dc-send{background:var(--ac);color:#fff;border:0;border-radius:50%;width:36px;height:36px;font-size:19px;font-weight:700;flex:none;margin:1px;cursor:pointer;line-height:1}.dc-send.sp{background:#c77a00}.dc-send:disabled{opacity:.35}'
+'.dc-note{color:#8c2f2f;font-size:13px;font-weight:600;padding:0 14px 4px}'
+'.dc-typ:empty,.dc-note:empty,.dc-prev:empty{display:none}'
+'.dc-sheet{position:absolute;inset:0;background:rgba(16,36,62,.45);display:flex;align-items:flex-end;z-index:5}.dc-sheet>div{background:#f4f7f6;color:#142033;width:100%;max-height:72%;overflow-y:auto;border-radius:16px 16px 0 0;padding:8px 0 max(8px,env(safe-area-inset-bottom))}'
+'.dc-sheet h3{margin:8px 14px 4px;font-size:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em}.dc-sheet .dc-row{padding:8px 14px}.dc-sheet .dc-row .dc-av{width:36px;height:36px;font-size:15px}'
+'.dc-amber{background:#5a4300;color:#fff;font-size:13px;padding:6px 12px}.dc-amber a{color:#fff;text-decoration:underline}'
+'.dc-empty{color:var(--mu);text-align:center;padding:30px 20px;font-size:15px}.dc-paper{font-size:11px;color:var(--mu);border:1px solid var(--bd);border-radius:8px;padding:1px 6px;vertical-align:middle;font-weight:400}'
+'.dc-chain{margin:0;padding:6px 12px;font-size:12px;line-height:1.35;color:var(--mu);background:var(--card);border-bottom:1px solid var(--bd)}'
+'.dc-ais{display:flex;gap:6px;flex-wrap:wrap;padding:6px 12px;border-bottom:1px solid var(--bd);flex:none;align-items:center;font-size:12px;color:var(--mu);cursor:pointer}'
+'.dc-ai{flex:none;display:inline-flex;align-items:center;gap:6px;background:#fff;color:#142033;border:1px solid #5c7384;border-radius:12px;padding:4px 8px;white-space:nowrap;max-width:100%;font-weight:600}.dc-ai i{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;box-shadow:0 0 0 1px rgba(16,36,62,.25)}.dc-ai.st-down{border-color:#8c2f2f}.dc-ai.st-unknown,.dc-ai.st-cooling,.dc-ai.st-busy{border-color:#8a5a12}.dc-ai.st-up{border-color:#1d5c38}'
+'.dc-ais.x{flex-wrap:wrap}.dc-ais.x .dc-ai{white-space:normal;line-height:1.3}'
+'.dc-b{cursor:pointer}.dc-m.sel .dc-b{outline:2px solid var(--ac);outline-offset:1px}.dc-acts{display:flex;gap:6px;margin:5px 4px 0;flex-wrap:wrap}.dc-acts button{background:#2c2c2e;border:0;color:var(--tx);border-radius:14px;padding:5px 11px;font-size:13px;cursor:pointer}.dc-acts button:active{background:#3a3a3c}'
+'.dc-lk{font-size:12px;margin:-7px 10px 0;background:#2c2c2e;border:2px solid #000;border-radius:10px;padding:1px 6px;line-height:16px;position:relative}'
+'.dc-rb{display:flex;align-items:center;gap:8px;padding:5px 6px 5px 10px;margin:0 0 6px;border-left:3px solid var(--ac);background:#fff;border:1px solid var(--bd);border-radius:8px;font-size:13px;color:#142033}.dc-rb span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dc-rb button{background:none;border:0;color:#142033;font-size:18px;cursor:pointer;padding:0 6px}'
+'#dc-to{max-width:100%;min-width:9rem}'
+'@media(min-width:700px){#dchat[data-mode=page]{max-width:1100px;margin:0 auto;border-left:1px solid var(--bd);border-right:1px solid var(--bd)}}';
CSS+=".dc-commandbar{display:flex;gap:6px;flex-wrap:wrap;padding:4px 0 8px}.dc-commandbar #dc-prompt{flex:1;min-width:140px;max-width:100%}.dc-commandbox{padding:10px;background:var(--card);border:1px solid var(--bd);border-radius:10px;max-height:40vh;overflow:auto;font-size:13px;margin-bottom:7px;color:var(--tx)}.dc-cmdgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}.dc-cmdgrid label{display:flex;flex-direction:column;gap:4px;min-width:0}.dc-cmdgrid select,.dc-cmdgrid input{width:100%;min-width:0;padding:8px;border:1px solid var(--bd);border-radius:7px;background:#fff;color:#142033;font-size:14px}.dc-wide{grid-column:1/-1}.dc-small{overflow-wrap:anywhere;display:block;color:var(--mu);font-size:12px;line-height:1.4}.dc-commandbox details{border-top:1px solid var(--bd);padding:8px 0}.dc-commandbox summary{cursor:pointer;font-weight:600;color:var(--tx)}.dc-task{padding:8px 0;border-bottom:1px solid var(--bd)}.dc-task button,.dc-linkbtn{padding:5px 9px;margin:4px 6px 0 0;border:1px solid var(--bd);border-radius:7px;background:#fff;color:#142033;cursor:pointer}.dc-brain{padding:8px 0}.dc-race{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px}.dc-race th,.dc-race td{text-align:left;padding:6px 3px;border-bottom:1px solid var(--bd);color:var(--tx)}.dc-race small{display:block;color:#3e5160}";
// Keep the conversation readable when the phone is held sideways; all existing controls remain available.
CSS+="@media(max-height:480px) and (min-width:600px){#dchat .dc-hd{min-height:44px;padding:4px 10px;padding-top:max(4px,env(safe-area-inset-top))}#dchat .dc-hd h2{font-size:17px}#dchat .dc-hd>.dc-ic,#dchat .dc-hd>.dc-av{width:32px;height:32px}#dchat .dc-ais{padding:4px 10px}#dchat .dc-comp{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:6px;padding:4px 8px;padding-bottom:max(8px,env(safe-area-inset-bottom))}#dchat .dc-commandbar{grid-column:1;grid-row:1;flex-wrap:nowrap;overflow-x:auto;min-width:0;margin:0;padding:0;scrollbar-width:none}#dchat .dc-pills{grid-column:2;grid-row:1;min-width:0;margin:0;padding:0}#dchat #dc-command-panel,#dchat #dc-rbw,#dchat .dc-in{grid-column:1/-1}#dchat #dc-command-panel:empty,#dchat #dc-rbw:empty{display:none}#dchat #dc-to{max-width:160px}#dchat .dc-pl .lbl{display:none}}";
if(!document.getElementById("dc-css")){var st=document.createElement("style");st.id="dc-css";st.textContent=CSS;document.head.appendChild(st)}
var AIL={auto:"Auto",claude:"Claude",chatgpt:"ChatGPT",grok:"Grok",deepseek:"DeepSeek API",deepseek_api:"DeepSeek API",super:"All four"};
var MARK='<svg class=tt-mark viewBox="0 0 32 32" aria-hidden="true"><rect x="2.5" y="2.5" width="27" height="27" rx="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 11.5h14M16 11.5V22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square"/></svg>';
var MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function LS(k){try{return localStorage.getItem(k)}catch(e){return null}}function LSs(k,v){try{localStorage.setItem(k,v)}catch(e){}}
var READ={};try{READ=JSON.parse(LS("dc-read")||"{}")||{}}catch(e){}
var S={view:"home",chat:null,home:null,meta:null,rows:[],after:0,more:false,pending:null,imgs:[],noise:LS("dc-noise")==="1",q:"",busy:false,sending:false,localPend:0,rec:null,sheet:false,sign:{},ais:null,aisAt:0,aisX:LS("dc-ais-x")==="1",sel:0,reply:null,lastTap:null};
S.command=commandState();S.commandOpen=false;S.tools=null;S.toolsAt=0;S.competition=null;
var LIKES={};try{LIKES=JSON.parse(LS("dc-likes")||"{}")||{}}catch(e){}   /* heartreply_20261103: hearts (local first, server sync via /api/chat_react) */
function $(s){return root.querySelector(s)}
function esc(s){return String(s==null?"":s).replace(/[&<>"\']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","\'":"&#39;"}[c]})}
function enc(s){return encodeURIComponent(String(s||""))}
function escId(id){return String(id||"").replace(/[^a-zA-Z0-9._-]/g,"")}
function linkify(h){return h.replace(/(https?:\/\/[^\s<]+)/g,function(u){return '<a href="'+u+'" target="_blank" rel="noopener">'+u+'</a>'})}
function wita(){return new Date(Date.now()+8*3600e3)}
function today(){return wita().toISOString().slice(0,10)}
function yday(){return new Date(wita().getTime()-864e5).toISOString().slice(0,10)}
function fmtT(ts){ts=String(ts||"");var d=ts.slice(0,10),t=ts.slice(11,16);if(!d)return"";if(d===today())return t;if(d===yday())return"Yest "+t;return(+d.slice(8,10))+" "+MON[(+d.slice(5,7)||1)-1]+" "+t}
function dayLabel(d){return d===today()?"Today":d===yday()?"Yesterday":(+d.slice(8,10))+" "+MON[(+d.slice(5,7)||1)-1]}
function first(c){return c.id==="head_trader"?"Head Trader":String(c.name||"").split(" ")[0]}
function initial(n){return(String(n||"?").trim().charAt(0)||"?").toUpperCase()}
function chatById(id){if(!S.home)return null;for(var i=0;i<S.home.chats.length;i++)if(S.home.chats[i].id===id)return S.home.chats[i];return null}
function colorOf(f){var c=chatById(f);if(c)return c.color;var h=0;for(var i=0;i<String(f).length;i++)h=(h*31+String(f).charCodeAt(i))>>>0;return"hsl("+(h%360)+",45%,40%)"}
function unread(c){var r=READ[c.id]||0,n=0;(c.imp||[]).forEach(function(k){if(k>r)n++});return n}
function saveRead(){LSs("dc-read",JSON.stringify(READ))}
function nearBottom(){var l=$("#dc-log");return!l||l.scrollHeight-l.scrollTop-l.clientHeight<90}
function scrollBottom(){var l=$("#dc-log");if(l)l.scrollTop=l.scrollHeight}
function note(t){var n=$("#dc-note");if(n)n.textContent=t||""}
/* ---------- AI status chips (B20a): Claude / ChatGPT / Grok, green up - amber cooling - red off ---------- */
function aisWord(a){return a.state==="busy"?"busy":a.state==="up"?"up":a.state==="cooling"?"cooling":a.state==="down"?"off":"?"}
function dotColor(a){var known={up:"#1d5c38",down:"#8c2f2f",cooling:"#8a5a12",busy:"#8a5a12",unknown:"#8a5a12"};return known[a.state]||"#3e5160"}
function aisHtml(){if(!S.ais)return"";return'<div class="dc-ais'+(S.aisX?" x":"")+'" id=dc-ais title="Model status. A dot is not a promise the model will answer.">'+S.ais.map(function(a){var word=aisWord(a);return'<span class="dc-ai st-'+(a.state||"unknown")+'" title="'+esc(a.note||word)+'"><i style="background:'+dotColor(a)+'"></i><b>'+esc(a.name)+'</b> '+esc(word)+(a.model?" "+esc(a.model):"")+'</span>'}).join("")+'</div>'}
function drawAis(){var w=$("#dc-aisw");if(w)w.innerHTML=aisHtml()}
async function loadAis(){S.aisAt=Date.now();try{var r=await fetch("/api/ai_status",{cache:"no-store"});if(!r.ok)return;S.ais=(await r.json()).ais||null;drawAis()}catch(e){}}
/* ---------- home ---------- */
function renderHome(){S.view="home";
var head=HUB
?'<div class=dc-hd><h2>Chats <span class=dc-paper>Paper</span></h2><a class=sub href="/desk/chat">Full screen</a><button class=dc-ic id=dc-new title="New chat">+</button></div><p class=dc-chain>Reports go up: seat, then Marcus, then the Head Trader, then Brett. In a thread, To names who receives the note.</p>'
:'<div class=dc-hd>'+(PAGE?'<a class=dc-ic href="/" title="Desk home">←</a>':'')+'<h2><span class=tt-brand>'+MARK+'<span class=tt-name>Transparent Trading</span></span> <span class=dc-paper>Paper</span></h2>'+(PAGE?'':'<a class=sub href="/desk/chat">Full screen</a>')+'<button class=dc-ic id=dc-new title="New chat">+</button></div>';
root.innerHTML=head
+'<div id=dc-aisw>'+aisHtml()+'</div><div class=dc-list id=dc-list></div><div class=dc-sb><input id=dc-q type=search placeholder="Search chats" autocomplete=off value="'+esc(S.q)+'"></div>';
fillHome();var q=$("#dc-q");q.oninput=function(){S.q=q.value;fillHome()}}
function fillHome(){var el=$("#dc-list");if(!el)return;var h=S.home;if(!h){el.innerHTML='<div class=dc-empty role=status>Loading conversations</div>';return}
var q=S.q.trim().toLowerCase();
function rowButton(c, pinned){var un=unread(c);
return'<button class="dc-row'+(un?' un':'')+'" data-id="'+esc(c.id)+'"><span class=dc-av style="background:'+esc(c.color)+'">'+esc(c.av)+'</span><span class=mid><span class=l1>'+(un?'<span class=dc-dot></span>':'')+'<b>'+esc(c.name)+'</b><span class=dk>'+esc(c.desk)+(pinned?' <span class=dc-star>Pinned</span>':'')+'</span><span class=tm>'+esc(c.last?fmtT(c.last.ts):"")+'</span></span><span class=l2 style="display:block">'+esc(c.pending?(c.id==="ALL"?"Desk replying… "+c.pending.got+"/"+c.pending.want:first(c)+" is thinking…"):(c.preview||"No messages yet"))+'</span></span>'+(un?'<span class=dc-pill>'+un+'</span>':'')+'</button>'}
var pinIds={};(h.pinned||[]).forEach(function(p){pinIds[p.id]=1});
var pinRows=(h.pinned||[]).map(function(p){var c=chatById(p.id)||p;return rowButton(c,true)}).join("");
var rows=(h.chats||[]).filter(function(c){return !pinIds[c.id]&&(c.id.indexOf("adv:")!==0||c.last)&&(!q||((c.label||"")+" "+(c.preview||"")).toLowerCase().indexOf(q)>=0)}).map(function(c){return rowButton(c,false)}).join("");
el.innerHTML=(q||!pinRows?'':('<div class=dc-sec>Pinned</div>'+pinRows))+(rows?('<div class=dc-sec>Conversations</div>'+rows):'')+(pinRows||rows?'':'<div class=dc-empty role=status>No conversations match</div>')}
function sheet(){if(!S.home)return;var g={Desk:[],Admins:[],Traders:[]};S.home.chats.forEach(function(c){if(c.id.indexOf("adv:")===0)return;(c.id==="ALL"?g.Desk:c.admin?g.Admins:g.Traders).push(c)});
var h='<div class=dc-sheet id=dc-sheet><div>';for(var k in g)if(g[k].length)h+='<h3>'+k+'</h3>'+g[k].map(function(c){return'<button class=dc-row data-id="'+esc(c.id)+'"><span class=dc-av style="background:'+esc(c.color)+'">'+esc(c.av)+'</span><span class=mid><span class=l1><b>'+esc(c.name)+'</b><span class=dk>('+esc(c.desk)+')</span></span></span></button>'}).join("");
h+='</div></div>';root.insertAdjacentHTML("beforeend",h);S.sheet=true}
function closeSheet(){var s=$("#dc-sheet");if(s)s.remove();S.sheet=false}
/* ---------- thread ---------- */
function sub(m){if(!m)return"";var s=m.desk||"";if(m.status)s+=" · "+(m.status.mode==="brain"?(m.status.model||"brain"):m.status.mode==="brain_down"?"brain down":(m.status.model||m.status.mode||""));return s}
function ph(m){if(!m)return"Message…";if(!m.can_send)return"No inbox for this seat yet";if(m.id==="ALL")return"Message the desk";if(m.id.indexOf("adv:")===0)return"Ask "+m.name;return"Reply "+(m.id==="head_trader"?"HEAD TRADER":m.name)}
function toSel(){var cs=S.home?S.home.chats.filter(function(c){return c.id.indexOf("adv:")!==0||c.id===S.chat}):[],g={Desk:[],Admins:[],Traders:[],Advisors:[]};cs.forEach(function(c){(c.id==="ALL"?g.Desk:c.id.indexOf("adv:")===0?g.Advisors:c.admin?g.Admins:g.Traders).push(c)});
var h='<select class=dc-pl id=dc-to title="To">';if(!cs.length)h+='<option value="'+esc(S.chat)+'">To: '+esc(S.meta?S.meta.name:S.chat)+'</option>';
for(var k in g)if(g[k].length)h+='<optgroup label="'+k+'">'+g[k].map(function(c){return'<option value="'+esc(c.id)+'"'+(c.id===S.chat?" selected":"")+'>To: '+esc(c.id==="ALL"?"All desk":c.admin||c.id.indexOf("adv:")===0?c.name:c.name+" ("+c.desk+")")+'</option>'}).join("")+'</optgroup>';return h+'</select>'}
/* Messaging routes automatically; provider names on old replies remain historical labels. */
/* Permanent chat commands: prompts, explicit push mode, durable tasks and separate brains. */
function peerControls(){var f=S.peerFilter||{desk:"all",status:"all",q:""};return'<div class=dc-cmdgrid><label>Trade history<select id=dc-peerdesk>'+[["all","All desks"],["thf","THF"],["veteran","Veteran Desk"],["oanda_aggro","OANDA Aggro"],["fundamental","Fundamental"]].map(function(x){return'<option value="'+x[0]+'"'+(f.desk===x[0]?" selected":"")+'>'+x[1]+'</option>'}).join("")+'</select></label><label>Records<select id=dc-peerstatus>'+["all","open","closed"].map(function(x){return'<option value="'+x+'"'+(f.status===x?" selected":"")+'>'+x+'</option>'}).join("")+'</select></label><label class=dc-wide>Ticker or text<input id=dc-peerq value="'+esc(f.q)+'" placeholder="For example: BTC or TSCO.L"></label></div><button class=dc-linkbtn id=dc-peergo>Search trades</button><div id=dc-peerresults>'+peerResults()+'</div>'}
function peerResults(){var d=S.peerData;if(!d)return'<div class=dc-small>Read-only access to current positions and available closed-trade records.</div>';return'<div class=dc-small>'+esc(d.total+" matching records · read only")+'</div>'+d.records.map(function(r){return'<div class=dc-task><b>'+esc(r.desk+" · "+r.symbol+" "+r.side)+'</b><span class=dc-small>'+esc(r.status+" · "+(r.asof||r.source_asof||"date unavailable"))+'</span><span class=dc-small>'+esc("Entry "+(r.entry==null?"—":r.entry)+" · Mark "+(r.mark==null?"—":r.mark)+" · Stop "+(r.stop==null?"—":r.stop)+" ["+(r.price_currency||"source native")+"]")+'</span><span class=dc-small>'+esc("P/L "+(r.net==null?"not provided":r.net)+" "+(r.pnl_currency||""))+'</span><span class=dc-small>'+esc(r.why||"")+'</span></div>'}).join("")+(d.offset>0?'<button class=dc-linkbtn id=dc-peerprev>Previous page</button>':"")+(d.next_offset!=null?'<button class=dc-linkbtn id=dc-peernext>Next page</button>':"")}
async function loadPeerTrades(offset){var f=S.peerFilter||(S.peerFilter={desk:"all",status:"all",q:""});try{var r=await fetch("/api/peer_trades?desk="+enc(f.desk)+"&status="+enc(f.status)+"&q="+enc(f.q)+"&offset="+(offset||0)+"&limit=20",{cache:"no-store"});var j=await r.json();if(!r.ok)throw new Error(j.error||r.status);S.peerData=j;var box=$("#dc-peerresults");if(box)box.innerHTML=peerResults()}catch(e){note("Peer data: "+e.message)}}

function commandState(){return{mode:"ask",scope:"current",when:"now",at:"",every:1440,preset:"",label:"",requestId:null}}
function requestId(){return"req-"+Date.now().toString(36)+"-"+Math.random().toString(36).slice(2,14)}
function stampInput(ms){return new Date(ms+8*3600000).toISOString().slice(0,16)}
function commandBar(m){if(String(m.id).indexOf("adv:")===0)return"";
return'<div class=dc-commandbar>'+promptSel()+'<select id=dc-action class=dc-pl aria-label="Ask or push"><option value=ask>Ask</option><option value=push>Push</option><option value=learn>Learn</option></select><button class=dc-pl id=dc-tasks aria-expanded=false title="Scheduled tasks, brain and competition">Tasks & brain</button></div><div id=dc-command-panel></div>'}
function promptSel(){var groups={},items=(S.tools&&S.tools.catalog)||[];items.forEach(function(p){(groups[p.category]||(groups[p.category]=[])).push(p)});
return'<select class=dc-pl id=dc-prompt aria-label="Choose a prompt"><option value="">Choose a prompt…</option>'+Object.keys(groups).map(function(g){return'<optgroup label="'+esc(g)+'">'+groups[g].map(function(p){return'<option value="'+esc(p.id)+'"'+(S.command&&S.command.preset===p.id?" selected":"")+'>'+esc(p.label)+'</option>'}).join("")+'</optgroup>'}).join("")+'</select>'}
function applyPreset(id){var p=((S.tools&&S.tools.catalog)||[]).find(function(x){return x.id===id});if(!p)return;
var ta=$("#dc-t");if(ta){ta.value=p.text;grow();ta.focus()}S.command.mode=p.mode;S.command.preset=p.id;S.command.label=p.label;S.command.requestId=null;
if(S.chat==="ALL"&&p.audience!=="all")S.command.scope=p.audience==="trader"?"traders":"staff";
if(p.suggested_every_minutes){S.command.when="repeat";S.command.every=p.suggested_every_minutes;var d=new Date(Date.now()+8*3600000);d.setUTCHours(p.id==="morning"?7:21,p.id==="morning"?0:30,0,0);if(d.getTime()<=Date.now()+8*3600000)d.setUTCDate(d.getUTCDate()+1);S.command.at=d.toISOString().slice(0,16);S.commandOpen=true}
var a=$("#dc-action");if(a)a.value=S.command.mode;renderCommands();updateCommandButton()}
function updateCommandButton(){var b=$("#dc-send");if(b){b.title=S.command&&S.command.when!=="now"?"Save scheduled task":S.command&&S.command.mode==="push"?"Push work now":"Send";b.setAttribute("aria-label",b.title)}var a=$("#dc-action");if(a&&S.command)a.value=S.command.mode}
function deliveryWords(d){return Object.keys(d||{}).map(function(k){return k.replace(/_/g," ")+": "+d[k]}).join(" · ")}
function renderCommands(){var box=$("#dc-command-panel"),button=$("#dc-tasks");if(!box)return;if(button)button.setAttribute("aria-expanded",S.commandOpen?"true":"false");if(!S.commandOpen){box.innerHTML="";return}
var openDetails=[].slice.call(box.querySelectorAll("details")).map(function(d){return d.open}),oldBox=box.querySelector(".dc-commandbox"),oldScroll=oldBox?oldBox.scrollTop:0;
var c=S.command||commandState(),data=S.tools||{},brain=data.brain||{},tasks=data.tasks||[];
var brainText=brain.seat?'<b>'+esc(brain.name||brain.seat)+'’s own brain</b><br>'+esc((brain.facts||0)+" facts · "+(brain.journal_lines||0)+" journal entries · "+(brain.lessons||0)+" lessons")+'<div class=dc-small>'+esc((brain.last_lessons||[]).slice(-2).join(" | "))+'</div>':'<b>Separate brains</b><br>'+esc((brain.initialised||0)+" of "+(brain.seats||"")+" trader/staff brains initialised. Select a person to see their own notes.");
var schedule='<div class=dc-cmdgrid><label>Recipients<select id=dc-scope><option value=current>This chat</option><option value=traders>All traders</option><option value=staff>Admin staff</option><option value=all>All desk</option></select></label><label>When<select id=dc-when><option value=now>Now</option><option value=once>Once</option><option value=repeat>Repeat</option></select></label>';
if(c.when!=="now"){schedule+='<label class=dc-wide>'+(c.when==="once"?"Run at":"Start at (blank = now)")+'<input id=dc-at type=datetime-local value="'+esc(c.at||"")+'"><span class=dc-small>Bali / Singapore time (UTC+8)</span></label>';
if(c.when==="repeat")schedule+='<label class=dc-wide>Repeat<select id=dc-every>'+[[15,"Every 15 minutes"],[30,"Every 30 minutes"],[60,"Every hour"],[240,"Every 4 hours"],[1440,"Every day"],[10080,"Every week"]].map(function(x){return'<option value="'+x[0]+'"'+(+c.every===x[0]?" selected":"")+'>'+x[1]+'</option>'}).join("")+'</select></label>'}schedule+='</div>';
var rows=tasks.map(function(t){var active=t.status==="active",button=active?"pause":t.status==="paused"?"resume":null;return'<div class=dc-task><b>'+esc(t.label)+'</b><span class=dc-small>'+esc(t.status+" · "+t.mode+(t.next_wita?" · "+t.next_wita:""))+'</span>'+(t.last_error?'<span class=dc-small>'+esc(t.last_error)+'</span>':"")+'<span class=dc-small>'+esc(deliveryWords(t.delivery))+'</span><div>'+(button?'<button data-taskop="'+button+'" data-taskid="'+esc(t.id)+'">'+(button==="pause"?"Pause":"Resume")+'</button>':"")+(t.status!=="cancelled"&&t.status!=="complete"&&t.status!=="sent"?'<button data-taskop=cancel data-taskid="'+esc(t.id)+'">Cancel</button>':"")+'</div></div>'}).join("");
var race=S.competition,standings=(race&&race.rows)||[];var table=standings.length?'<table class=dc-race><thead><tr><th>Desk</th><th>Net return</th><th>Drawdown</th></tr></thead><tbody>'+standings.map(function(r){return'<tr><td>'+esc((r.rank?r.rank+". ":"")+r.name)+'<small>'+esc(r.age_s==null?"Data unavailable":"Data "+Math.max(0,Math.round(r.age_s/60))+" min old")+" · "+esc(r.currency||"")+'</small>'+(r.provisional?'<small>Provisional</small>':"")+((r.warnings&&r.warnings[0])?'<small>'+esc(r.warnings[0])+'</small>':"")+((r.reasons&&r.reasons[0])?'<small>'+esc(r.reasons[0])+'</small>':"")+(r.scope_note?'<small>'+esc(r.scope_note)+'</small>':"")+'</td><td>'+esc(r.return_pct==null?"Waiting":((r.return_pct>0?"+":"")+r.return_pct.toFixed(3)+"%"))+'</td><td>'+esc(r.drawdown_pct==null?"—":r.drawdown_pct.toFixed(2)+"%")+'</td></tr>'}).join("")+'</tbody></table><div class=dc-small>'+esc(race.rules||"")+'</div>':'<div class=dc-small>Competition data is loading.</div>';
box.innerHTML='<div class=dc-commandbox><div class=dc-small>'+(data.ready?'Ask for a reply. Push for action. Learn in this person’s own brain. You can schedule any of these.':'Chat command controls are starting. Your text is kept.')+'</div>'+schedule+'<details open><summary>Scheduled tasks ('+tasks.filter(function(t){return t.status==="active"}).length+' active)</summary>'+(rows||'<div class=dc-small>No tasks for this chat yet.</div>')+'</details><details><summary>My brain</summary><div class=dc-brain>'+brainText+'</div><button class=dc-linkbtn id=dc-reviewbrain>Prepare a learning review</button></details><details><summary>THF competition</summary>'+table+peerControls()+'</details></div>';
[].slice.call(box.querySelectorAll("details")).forEach(function(d,i){if(i<openDetails.length)d.open=openDetails[i]});var nextBox=box.querySelector(".dc-commandbox");if(nextBox)nextBox.scrollTop=oldScroll;
var sc=$("#dc-scope"),wh=$("#dc-when");if(sc)sc.value=c.scope;if(wh)wh.value=c.when;updateCommandButton()}
async function loadCommands(){if(!S.chat||String(S.chat).indexOf("adv:")===0)return;var who=S.chat;try{var r=await fetch("/api/chat_tools?seat="+enc(who),{cache:"no-store"});if(!r.ok)return;var j=await r.json();if(S.chat!==who)return;S.tools=j;S.toolsAt=Date.now();var select=$("#dc-prompt");if(select)select.outerHTML=promptSel();if(S.commandOpen){try{var cr=await fetch("/api/competition",{cache:"no-store"});if(cr.ok)S.competition=await cr.json()}catch(e){}var focused=document.activeElement;if(!focused||!$("#dc-command-panel").contains(focused)||!["INPUT","SELECT"].includes(focused.tagName))renderCommands()}}catch(e){}}
async function taskAction(op,id){try{var r=await fetch("/api/funder_msg",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:"task",task:{op:op,task_id:id,request_id:requestId()}})});var j=await r.json().catch(function(){return{}});if(!r.ok)throw new Error(j.error||r.status);note(j.note||"Saved");setTimeout(loadCommands,2300)}catch(e){note("Task: "+e.message)}}
root.addEventListener("change",function(e){var id=e.target.id;if(id==="dc-prompt"){applyPreset(e.target.value);return}if(!S.command)return;
var peerKeys={"dc-peerdesk":"desk","dc-peerstatus":"status","dc-peerq":"q"};if(peerKeys[id]){S.peerFilter=S.peerFilter||{desk:"all",status:"all",q:""};S.peerFilter[peerKeys[id]]=e.target.value;return}
var keys={"dc-action":"mode","dc-scope":"scope","dc-when":"when","dc-at":"at","dc-every":"every"};if(!keys[id])return;S.command[keys[id]]=e.target.value;S.command.requestId=null;
if(id==="dc-when"){if(S.command.when==="once"&&!S.command.at)S.command.at=stampInput(Date.now()+15*60000);renderCommands()}updateCommandButton()});

function renderThread(){S.view="thread";var m=S.meta||{id:S.chat,name:S.chat,desk:"",av:"?",color:"#333",can_send:true};
root.innerHTML='<div class=dc-hd><button class=dc-ic id=dc-back title="Back">←</button>'+MARK+'<h2 id=dc-ttl>'+esc(m.name)+(m.star?'':'')+'<br><span class=sub id=dc-sub>'+esc(sub(m))+'</span></h2>'+(PAGE?'<span class=dc-paper>Paper</span>':'<a class=sub href="/desk/chat#c='+esc(m.id)+'" title="Full screen">Full</a>')+'</div>'
+'<div id=dc-aisw>'+aisHtml()+'</div><div id=dc-amber></div><div class=dc-log id=dc-log><div class=dc-empty>Loading…</div></div><div class=dc-typ id=dc-typ></div><div class=dc-note id=dc-note></div><div class=dc-prev id=dc-prev></div>'
+'<div class=dc-comp><div id=dc-rbw></div><div class=dc-pills>'+toSel()+'<button class="dc-pl'+(S.noise?" on":"")+'" id=dc-nz title="Show relay / queue lines">⋯<span class=lbl> relay</span></button></div>'
+'<div class=dc-in><button class=dc-ic id=dc-clip title="Attach image">＋</button><input type=file id=dc-file accept="image/*" multiple hidden><textarea id=dc-t rows=1 placeholder="'+esc(ph(m))+'"'+(m.can_send?"":" disabled")+'></textarea><button class=dc-ic id=dc-mic title="Dictate">🎤</button><button class=dc-send id=dc-send title="Send"'+(m.can_send?"":" disabled")+'>↑</button></div></div>';
var comp=$("#dc-rbw");if(comp)comp.insertAdjacentHTML("beforebegin",commandBar(m));
var ta=$("#dc-t");ta.addEventListener("input",function(){grow();if(S.command)S.command.requestId=null});ta.addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey&&window.matchMedia&&window.matchMedia("(hover:hover)").matches){e.preventDefault();send()}});
ta.addEventListener("paste",function(e){var it=[].slice.call(e.clipboardData&&e.clipboardData.items||[]).filter(function(i){return i.type.indexOf("image/")===0});if(!it.length)return;e.preventDefault();addFiles(it.map(function(i){return i.getAsFile()}).filter(Boolean))});
$("#dc-to").onchange=function(){openChat(this.value)};$("#dc-file").onchange=function(){addFiles(this.files||[]);this.value=""};
var SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR)$("#dc-mic").style.display="none";
if(m.id==="adv:chatgpt")signin(m.id.slice(4));renderPrev();fillRows();renderTyp();loadCommands()}
function updateHeader(){var m=S.meta;if(!m)return;var t=$("#dc-ttl");if(t)t.innerHTML=esc(m.name)+(m.star?' ⭐':'')+'<br><span class=sub id=dc-sub>'+esc(sub(m))+'</span>';var ta=$("#dc-t");if(ta&&document.activeElement!==ta)ta.placeholder=ph(m)}
function grow(){var ta=$("#dc-t");if(!ta)return;ta.style.height="auto";ta.style.height=Math.min(ta.scrollHeight,window.innerHeight*0.3)+"px"}
function imgs(r){return(r.images||[]).map(function(id){return'<img loading=lazy src="/api/chat_img/'+escId(id)+'" alt="">'}).join("")}
function lkey(r){return(String(S.chat||"").indexOf("adv:")===0?"a":"t")+r.k}
function liked(r){var v=LIKES[lkey(r)];return v===1||(v!==0&&!!r.lk)}
function rowByK(k){for(var i=S.rows.length-1;i>=0;i--)if(S.rows[i].k===k)return S.rows[i];return null}
function redraw(){var l=$("#dc-log"),st=l?l.scrollTop:0;fillRows();if(l)l.scrollTop=st}
function toggleLike(k){var r=rowByK(k);if(!r)return;var on=!liked(r);LIKES[lkey(r)]=on?1:0;LSs("dc-likes",JSON.stringify(LIKES));r.lk=on;redraw();try{fetch("/api/chat_react",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({k:r.k,src:lkey(r).charAt(0),chat:S.chat,on:on,f:r.f,id:r.id||null,root:r.root||null,ts:r.ts||null})}).catch(function(){})}catch(e){}}
function snip(t,n){return String(t||"").replace(/\s+/g," ").trim().slice(0,n||120)}
function renderReply(){var w=$("#dc-rbw");if(!w)return;var r=S.reply;w.innerHTML=r?'<div class=dc-rb><span>\u21a9 Replying to <b>'+esc(r.kd==="me"?"your message":r.nm)+'</b>: '+esc(snip(r.text,120))+'</span><button data-act=unreply title="Cancel reply">\u2715</button></div>':"";var ta=$("#dc-t");if(ta&&S.meta&&S.meta.can_send!==false)ta.placeholder=r?"Reply to "+(r.kd==="me"?"your message":r.nm)+"\u2026":ph(S.meta)}
function act(a,k){if(a==="unreply"){S.reply=null;renderReply();return}var r=rowByK(k);if(!r)return;if(a==="like"){S.sel=0;toggleLike(k);return}if(a==="reply"){S.reply=r;S.sel=0;redraw();renderReply();var ta=$("#dc-t");if(ta)ta.focus();return}if(a==="copy"){try{navigator.clipboard.writeText(r.text||"");note("copied")}catch(e){note("copy not available")}S.sel=0;redraw();setTimeout(function(){note("")},1500)}}
function rowHtml(r){if(r.kd==="noise")return S.noise?'<div class=dc-nz>'+esc(r.nm+": "+r.text)+'</div>':"";
if(r.kd==="relay")return'<details class=dc-rl'+(S.noise?" open":"")+'><summary>✉ From '+esc(r.nm)+(r.to&&r.to!=="ALL"&&r.to!=="brett"?' → To '+esc((chatById(r.to)||{}).name||r.to):'')+' · '+esc(fmtT(r.ts))+'</summary><div class=dc-b>'+linkify(esc(r.text))+imgs(r)+'</div></details>';
var me=r.kd==="me",t=r.text||"",rq="",qm=/\n?\|\| \u21a9 replying to ([^\n]*)$/.exec(t);if(qm){rq=qm[1];t=t.slice(0,qm.index)}var lng=t.length>900,meta=[],lk=liked(r),sl=S.sel===r.k;
var who=me?"":((S.chat==="ALL"||r.f!==S.chat)?'<div class=dc-who><span class=dc-av style="background:'+esc(colorOf(r.f))+'">'+esc(initial(r.nm))+'</span>'+esc(r.nm)+'</div>':"");
if(me){if(S.chat==="ALL"||(r.to&&r.to!==S.chat))meta.push("to "+esc(r.to==="ALL"?"All desk":(chatById(r.to)||{}).name||r.to));if(r.ai)meta.push('<span class="dc-chip sp">'+esc(AIL[r.ai]||r.ai)+'</span>')}
else{if(r.to&&r.to!=="brett"&&r.to!=="ALL"&&r.to!==r.f)meta.push('From '+esc(r.nm)+' → To '+esc(r.to==="all"?"All desk":(chatById(r.to)||{}).name||r.to));if(r.code)meta.push('<span class="dc-chip cd">code reply · AI was down</span>');else if(r.mode&&r.mode!=="funder"&&r.mode!=="advisor")meta.push('<span class=dc-chip>'+esc(r.mode)+'</span>')}
meta.push(esc(fmtT(r.ts)));
return'<div class="dc-m'+(me?" me":"")+(sl?" sel":"")+'" data-k="'+r.k+'">'+who+'<div class="dc-b'+(lng?" clip":"")+'">'+(rq?'<div class=dc-q>\u21a9 '+esc(rq)+'</div>':r.q?'<div class=dc-q>'+esc(r.q)+'</div>':"")+linkify(esc(t))+imgs(r)+'</div>'+(lk?'<div class=dc-lk title="You liked this">\u2764\ufe0f</div>':"")+(lng?'<button class=dc-more>show more ▾</button>':"")+(sl?'<div class=dc-acts><button data-act=like data-k="'+r.k+'">'+(lk?"\ud83d\udc94 Unlike":"\u2764\ufe0f Like")+'</button><button data-act=reply data-k="'+r.k+'">\u21a9 Reply</button><button data-act=copy data-k="'+r.k+'">\u29c9 Copy</button></div>':"")+'<div class=dc-mt>'+meta.join(" · ")+'</div></div>'}
function fillRows(){var log=$("#dc-log");if(!log)return;var rows=S.rows,out=[],day="",i=0,lastGrp=-1;
while(i<rows.length){var r=rows[i],d=String(r.ts||"").slice(0,10);if(d&&d!==day){day=d;out.push('<div class=dc-day>'+esc(dayLabel(d))+'</div>')}
if(S.chat==="ALL"&&r.kd!=="me"&&r.root){var j=i,grp=[];while(j<rows.length&&rows[j].kd!=="me"&&rows[j].root===r.root){grp.push(rows[j]);j++}
var vis=grp.filter(function(x){return x.kd!=="noise"||S.noise});if(vis.length>2){out.push('<details class=dc-grp><summary>'+vis.length+' replies ▾</summary>'+vis.map(rowHtml).join("")+'</details>');lastGrp=out.length-1}else out.push(vis.map(rowHtml).join(""));i=j;continue}
out.push(rowHtml(r));i++}
if(lastGrp>=0)out[lastGrp]=out[lastGrp].replace("<details class=dc-grp>","<details class=dc-grp open>");
log.innerHTML=(S.more?'<button class=dc-more id=dc-older>Earlier messages</button>':"")+(out.join("")||'<div class=dc-empty role=status>No messages in this conversation yet.</div>')}
function renderTyp(){var el=$("#dc-typ");if(!el)return;var p=S.pending,m=S.meta||{},local=S.localPend&&Date.now()-S.localPend<20*60e3;
if(!p&&!local){el.innerHTML="";return}
var txt=S.chat==="ALL"?"Desk replying… "+(p?p.got+"/"+p.want:""):first(m)+" is thinking";
el.innerHTML='<span class=dc-av style="background:'+esc(m.color||"#333")+'">'+esc(m.av||"?")+'</span>'+esc(txt)+'<span class=dots>…</span>'}
function renderPrev(){var p=$("#dc-prev");if(!p)return;p.innerHTML=S.imgs.map(function(im,i){return'<div class=th><img src="'+im.url+'"><button type=button data-i="'+i+'" title="remove">×</button></div>'}).join("")}
function addFiles(files){var list=[].slice.call(files).filter(function(f){return/^image\//.test(f.type)||/\.(png|jpe?g|webp|gif|heic)$/i.test(f.name||"")});
list.forEach(function(f){if(S.imgs.length>=4){note("max 4 images");return}if(f.size>8*1024*1024){note("image too large (>8MB)");return}S.imgs.push({file:f,url:URL.createObjectURL(f)})});renderPrev()}
function signin(a){fetch("/api/"+a+"_status",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){var el=$("#dc-amber");if(!el||j.state==="logged_in")return;el.innerHTML='<div class=dc-amber>⚠ '+esc("ChatGPT")+' needs a sign-in: <a href="'+esc(j.signin_url||"#")+'">open the sign-in view</a> (Tailscale only)</div>'}).catch(function(){})}
async function upload(f){var fd=new FormData();fd.append("file",f.file,f.file.name||"image.png");var r=await fetch("/api/chat_img",{method:"POST",body:fd});var j=await r.json().catch(function(){return{}});if(!r.ok||!j.id)throw new Error(j.error||("upload "+r.status));return j.id}
async function send(){var ta=$("#dc-t");if(!ta||S.sending)return;var t=ta.value.trim();if(!t&&!S.imgs.length)return;var m=S.meta||{};if(m.can_send===false){note("this seat has no inbox yet");return}
var fileSig=S.imgs.map(function(f){return[f.file.name,f.file.size,f.file.lastModified].join(":")}).join("|");if(S.command&&S.command.payload&&S.command.fileSig!==fileSig){S.command.requestId=null;S.command.payload=null}
S.sending=true;$("#dc-send").disabled=true;note(S.imgs.length?"uploading image…":"");var ids=[];
try{for(var i=0;i<S.imgs.length;i++)ids.push(await upload(S.imgs[i]))}catch(e){note("upload failed: "+e.message);S.sending=false;$("#dc-send").disabled=false;return}
var chat=S.chat,text=t,u="/api/funder_msg",b,rp=S.reply,seatTo=null;
if(rp){var _rid=function(x){return /^[A-Za-z][A-Za-z0-9]*-[\w.:@-]{3,80}$/.test(x||"")};var rid=_rid(rp.root)?rp.root:(_rid(rp.id)?rp.id:"");/* replythread_20261103: any thread id (seat mail sm-, push-, htd-...), not only fm- - a reply to a seat-mail/roll-call row used to start a new thread */if(chat==="ALL"&&rp.kd!=="me"){var rc=chatById(rp.f);if(rc&&rc.can_send)seatTo=rp.f}var tail="\n|| \u21a9 replying to "+(rp.kd==="me"?"my message":rp.nm)+" ("+fmtT(rp.ts)+"): \u00ab"+snip(rp.text,240).replace(/\|\|/g,"|")+"\u00bb";if(chat.indexOf("adv:")===0)t=t+tail;else text=(rid?"re:"+rid+" ":"")+text+tail}
if(chat.indexOf("adv:")===0){u="/api/advisor_note";b={trade:"DESK",advisor:chat.slice(4),text:t,route:"",images:ids}}else if(chat==="ALL"&&seatTo)b={kind:"seat",seat:seatTo,text:text,images:ids,ai:"auto"};else if(chat==="ALL")b={kind:"all",text:text,images:ids,ai:"auto"};else b={kind:"seat",seat:chat,text:text,images:ids,ai:"auto"};
var command=S.command||commandState(),isTask=false;
if(chat.indexOf("adv:")!==0){
 if(!command.requestId)command.requestId=requestId();
 if(command.when!=="now"||command.scope!=="current"||command.mode==="learn"){
  isTask=true;u="/api/funder_msg";var schedule={kind:command.when};if(command.at&&command.when!=="now")schedule.at=command.at+"+08:00";if(command.when==="repeat")schedule.every_minutes=+command.every;
  b={op:"create",request_id:command.requestId,text:text,label:command.label||text.slice(0,80),mode:command.mode,scope:command.scope,seat:seatTo||chat,ai:"auto",preset:command.preset,schedule:schedule,images:ids}
 }else{b.command={mode:command.mode,preset:command.preset};b.request_id=command.requestId}
}
if(isTask){if(command.payload&&command.payload.request_id===command.requestId&&command.fileSig===fileSig)b=command.payload;else{command.payload=b;command.fileSig=fileSig}}
try{var r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(isTask?{kind:"task",task:b}:b)});var j=await r.json().catch(function(){return{}});
if(!r.ok){note("error "+(j.error||r.status));return}
ta.value="";grow();S.imgs=[];renderPrev();S.localPend=Date.now();if(S.reply){S.reply=null;renderReply();if(seatTo)setTimeout(function(){note("\u21a9 sent to "+((chatById(seatTo)||{}).name||seatTo)+": the answer shows here and in their chat")},50)}
note(isTask?(j.note||"Task saved"):"");if(isTask){S.localPend=0;S.command=commandState();var ps=$("#dc-prompt");if(ps)ps.value="";updateCommandButton();renderCommands();setTimeout(loadCommands,2300)}else S.command.requestId=null;renderTyp();setTimeout(pollThread,700);setTimeout(function(){note("")},6000)}
catch(e){note("send failed: "+e.message)}finally{S.sending=false;var sb=$("#dc-send");if(sb)sb.disabled=false}}
function mic(){var SR=window.SpeechRecognition||window.webkitSpeechRecognition,b=$("#dc-mic"),ta=$("#dc-t");if(!SR||!ta)return;if(S.rec){try{S.rec.stop()}catch(e){}return}
var rec=new SR(),base=ta.value;rec.lang=navigator.language||"en-AU";rec.interimResults=true;rec.continuous=false;
rec.onresult=function(e){var s="";for(var i=0;i<e.results.length;i++)s+=e.results[i][0].transcript;ta.value=(base?base.replace(/\s+$/,"")+" ":"")+s;grow()};
rec.onend=function(){S.rec=null;if(b)b.classList.remove("on")};rec.onerror=rec.onend;try{rec.start();S.rec=rec;if(b)b.classList.add("on")}catch(e){note("mic not available")}}
/* ---------- data ---------- */
async function loadHome(){try{var r=await fetch("/api/chat_home",{cache:"no-store"});if(!r.ok)return;S.home=await r.json();
if(LS("dc-read")===null){(S.home.chats||[]).forEach(function(c){if(c.last)READ[c.id]=c.last.k});saveRead()}   /* first visit: old history is read, only new rows count */
if(S.view==="home")fillHome()}catch(e){}}
async function openChat(id){if(!id)return;closeSheet();if(S.view==="thread"&&S.chat===id)return;S.view="thread";S.chat=id;S.rows=[];S.after=0;S.more=false;S.meta=chatById(id)||null;S.pending=null;S.localPend=0;S.imgs=[];S.sel=0;S.reply=null;S.command=commandState();S.commandOpen=false;S.tools=null;
if(PAGE&&location.hash!=="#c="+id)location.hash="#c="+id;renderThread();await loadThread(0)}
async function loadThread(before){var u="/api/chat_thread?seat="+enc(S.chat)+(before?"&before="+before:"");try{var r=await fetch(u,{cache:"no-store"});var j=await r.json().catch(function(){return{}});if(!r.ok){var bad=$("#dc-log");if(bad&&!before)bad.innerHTML='<div class=dc-empty role=alert>Could not load this conversation ('+esc(j.error||r.status)+'). Retrying.</div>';return}if(S.view!=="thread"||!j.chat||j.chat.id!==S.chat)return;
S.meta=j.chat;if(before){S.rows=j.rows.concat(S.rows);S.more=j.more}else{S.rows=j.rows;S.more=j.more;S.after=j.next}S.pending=j.pending;markRead();updateHeader();fillRows();renderTyp();if(!before)scrollBottom()}catch(e){var l=$("#dc-log");if(l&&!S.rows.length)l.innerHTML='<div class=dc-empty role=alert>Could not load this conversation. Retrying.</div>'}}
async function pollThread(){if(S.busy||S.view!=="thread")return;S.busy=true;try{var r=await fetch("/api/chat_thread?seat="+enc(S.chat)+"&after="+S.after,{cache:"no-store"});var j=await r.json();if(S.view!=="thread"||!j.chat||j.chat.id!==S.chat)return;
S.after=j.next;S.pending=j.pending;S.meta=j.chat;if(j.rows.length){var atB=nearBottom();S.rows=S.rows.concat(j.rows);if(j.rows.some(function(x){return x.kd!=="me"}))S.localPend=0;markRead();fillRows();if(atB)scrollBottom()}renderTyp();updateHeader()}catch(e){}finally{S.busy=false}}
function markRead(){if(!S.rows.length)return;var k=S.rows[S.rows.length-1].k;if((READ[S.chat]||0)<k){READ[S.chat]=k;saveRead()}}
function goHome(){S.view="home";S.chat=null;if(S.rec){try{S.rec.stop()}catch(e){}}if(PAGE&&location.hash)history.replaceState(null,"",location.pathname);renderHome();loadHome()}
function tick(){var pane=root.closest(".pane");if(document.hidden||(!PAGE&&pane&&!pane.classList.contains("on")&&!document.body.classList.contains("all")))return;if(S.commandOpen&&Date.now()-(S.toolsAt||0)>15000)loadCommands();if(Date.now()-S.aisAt>30000)loadAis();if(S.view==="home")loadHome();else{pollThread();if(Date.now()-(S.homeAt||0)>15000){S.homeAt=Date.now();loadHome()}}}
/* ---------- events (one delegated listener) ---------- */
root.addEventListener("click",function(e){var t=e.target.closest("button,a,summary");if(!t)return;var id=t.id;if(t.dataset&&t.dataset.act){act(t.dataset.act,+t.dataset.k);return}
if(id==="dc-new"){sheet();return}if(t.classList.contains("dc-sheet")){closeSheet();return}
if(t.dataset&&t.dataset.id&&(t.classList.contains("dc-row")||t.classList.contains("p"))){openChat(t.dataset.id);return}
if(t.dataset&&t.dataset.taskop){taskAction(t.dataset.taskop,t.dataset.taskid);return}
if(id==="dc-peergo"){loadPeerTrades(0);return}if(id==="dc-peernext"){loadPeerTrades(S.peerData&&S.peerData.next_offset||0);return}if(id==="dc-peerprev"){loadPeerTrades(Math.max(0,(S.peerData&&S.peerData.offset||0)-20));return}
if(id==="dc-tasks"){S.commandOpen=!S.commandOpen;renderCommands();if(S.commandOpen)loadCommands();return}
if(id==="dc-reviewbrain"){applyPreset("daily_learn");S.command.when="now";renderCommands();return}
if(id==="dc-back"){goHome();return}if(id==="dc-send"){send();return}if(id==="dc-clip"){$("#dc-file").click();return}if(id==="dc-mic"){mic();return}
if(id==="dc-nz"){S.noise=!S.noise;LSs("dc-noise",S.noise?"1":"0");t.classList.toggle("on",S.noise);fillRows();return}
if(id==="dc-older"){var f=S.rows.length?S.rows[0].k:0;if(f)loadThread(f);return}
if(t.classList.contains("dc-more")&&!id){var b=t.previousElementSibling;if(b){var c=b.classList.toggle("clip");t.textContent=c?"show more ▾":"show less ▴"}return}
if(t.parentElement&&t.parentElement.classList.contains("th")&&t.dataset.i!=null){S.imgs.splice(+t.dataset.i,1);renderPrev();return}});
root.addEventListener("click",function(e){if(e.target.id==="dc-sheet")closeSheet();if(e.target.closest&&e.target.closest("#dc-ais")){S.aisX=!S.aisX;LSs("dc-ais-x",S.aisX?"1":"0");drawAis()}});
root.addEventListener("click",function(e){if(S.view!=="thread"||e.target.closest("button,a,summary,textarea,input,select"))return;var m=e.target.closest(".dc-m");if(!m||!m.dataset.k)return;try{if(String(window.getSelection&&window.getSelection()||""))return}catch(x){}var k=+m.dataset.k,n=Date.now();if(S.lastTap&&S.lastTap.k===k&&n-S.lastTap.t<380){S.lastTap=null;S.sel=0;toggleLike(k);return}S.lastTap={k:k,t:n};S.sel=S.sel===k?0:k;redraw()});   /* heartreply_20261103: tap = actions, double-tap = heart */
document.addEventListener("visibilitychange",function(){if(!document.hidden)tick()});
if(PAGE)window.addEventListener("hashchange",function(){var m=/^#c=(.+)$/.exec(location.hash||"");if(m){openChat(decodeURIComponent(m[1]))}else if(S.view!=="home")goHome()});
var m0=PAGE?/^#c=(.+)$/.exec(location.hash||""):null;
loadAis();if(m0){renderHome();loadHome().then(function(){openChat(decodeURIComponent(m0[1]))})}else{renderHome();loadHome()}
// Fit the embedded pane above the dashboard navigation and mobile keyboard.
// Full-screen chat owns its viewport and keeps its existing layout.
function fitEmbed(){
 if(PAGE||!root.isConnected||root.offsetParent===null)return;
 var vp=window.visualViewport,edge=vp?vp.offsetTop+vp.height:window.innerHeight;
 var nav=document.getElementById("tabbar"),box=nav&&nav.getBoundingClientRect();
 if(box&&getComputedStyle(nav).position==="fixed"&&box.height>0)edge=Math.min(edge,box.top);
 if(!nav)return;
 var top=root.getBoundingClientRect().top,available=Math.max(0,edge-top-8);
 var height=Math.floor(available);
 if(height>0&&root.style.height!==height+"px")root.style.height=height+"px";
}
if(!PAGE){
 var queued=false;
 function scheduleFit(){if(queued)return;queued=true;requestAnimationFrame(function(){queued=false;fitEmbed()})}
 window.addEventListener("resize",scheduleFit);window.addEventListener("scroll",scheduleFit,{passive:true});
 if(window.visualViewport){visualViewport.addEventListener("resize",scheduleFit);visualViewport.addEventListener("scroll",scheduleFit)}
 if(window.ResizeObserver){var paneObserver=new ResizeObserver(scheduleFit);paneObserver.observe(root);paneObserver.observe(document.body)}
 scheduleFit();
}
setInterval(tick,3000);
})();
