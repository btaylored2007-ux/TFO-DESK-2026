/* MOBILE TOP CARDS v2 2026-10-28 (PAPER, read-only). Brett: Fundamental card not visible on iPhone.
   Cause: warroom_clean.js rehome() moves every legacy panel (incl. #fundamental-desk, #oanda-panel) into hidden #wcmore (More tab).
   Always-visible desk tiles under War Room totals: Fundamental, OANDA, TREND-CORE, Crypto Sleeve.
   Dual-currency (A$ · US$) reuses Fundamental AUDUSD (true_return.fx); if FX missing, keep A$ only and omit US$.
   Feeds: /api/all (fundamental_desk.true_return + fx), /api/m (oanda), /static/trend_core_status.json, /static/btc_pounce_status.json. */
(function(){
const U=v=>v==null?'—':'US$'+Math.round(v).toLocaleString('en-US');
const Au=v=>v==null?'—':'A$'+Math.round(v).toLocaleString('en-US');
const S=(v,p)=>v==null?'—':(v>=0?'+':'−')+Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:p||0});
const C=v=>v==null?'':(v>=0?'pos':'neg');
const css='#wctop,#wctop *{box-sizing:border-box;max-width:100%}#wctop .ln{display:block!important}#wctop .ln span{display:block!important;text-align:left!important;white-space:normal!important;overflow-wrap:anywhere}#wctop .ts{overflow-wrap:anywhere}#wctop .tc{background:#161b22;border:2px solid #3a7;border-radius:14px;padding:14px 16px;margin:10px 0;color:#f4f6f8}#wctop .tc h3{margin:0 0 8px;font-size:19px}#wctop .ln{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 10px;padding:7px 0;border-bottom:1px solid #2a313a;font-size:16px}#wctop .ln b{font-size:14px;letter-spacing:.03em}#wctop .ln span{font-weight:700;font-variant-numeric:tabular-nums;text-align:right}#wctop .ts{font-size:13px;color:#aab;margin-top:6px}#wctop .pos{color:#3ddc84}#wctop .neg{color:#ff5c5c}';
let FX=null; // shared AUDUSD from Fundamental when available
function mount(){let t=document.getElementById('wctop');if(t)return t;const wc=document.getElementById('wc');if(!wc)return null;
 const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
 t=document.createElement('div');t.id='wctop';t.innerHTML=
  '<div class="tc" id="wcfund"><h3>Fundamental Desk · PAPER</h3><div class="ts">loading…</div></div>'+
  '<div class="tc" id="wcoanda" style="border-color:#c90"><h3>OANDA practice · PAPER</h3><div class="ts">loading…</div></div>'+
  '<div class="tc" id="wctrend" style="border-color:#6af"><h3>TREND-CORE · PAPER</h3><div class="ts">loading…</div></div>'+
  '<div class="tc" id="wccrypto" style="border-color:#a6f"><h3>Crypto Sleeve · PAPER</h3><div class="ts">loading…</div></div>';
 t.style.cssText='margin:8px;max-width:calc(100vw - 16px);box-sizing:border-box';document.body.prepend(t);return t;}
const L=(k,v,c)=>'<div class="ln"><b>'+k+'</b><span class="'+(c||'')+'">'+v+'</span></div>';
/* Dual line: AUD-native amount -> A$ · US$ when FX known; else A$ only (never invent FX). */
function dualAud(aud){
  if(aud==null||isNaN(aud))return '—';
  if(FX==null||!(FX>0))return Au(aud);
  return Au(aud)+' · '+U(aud*FX);
}
function dualPnlAud(aud){
  if(aud==null||isNaN(aud))return '—';
  if(FX==null||!(FX>0))return 'A$'+S(aud);
  return 'A$'+S(aud)+' · US$'+S(aud*FX);
}
function fund(){fetch('/api/all',{cache:'no-store'}).then(r=>r.json()).then(d=>{const el=document.getElementById('wcfund');if(!el)return;const fd=d.fundamental_desk||{},tr=fd.true_return||{};
 if(fd.error||tr.error||tr.equity_usd==null){el.innerHTML='<h3>Fundamental Desk · PAPER</h3><div class="ts neg">'+(fd.error||tr.error||'true return unavailable')+'</div>';return;}
 const fx=(tr.fx||{}).rate; if(fx!=null&&fx>0)FX=fx;
 const A=u=>u==null||!fx?'—':'A$'+Math.round(u/fx).toLocaleString('en-US');
 el.innerHTML='<h3>Fundamental Desk · PAPER</h3>'+L('TOTAL BOOK VALUE',A(tr.equity_usd)+' · '+U(tr.equity_usd))+L('AVAILABLE POWDER (cash)',A(tr.cash_usd)+' · '+U(tr.cash_usd))+L('INVESTED',A(tr.invested_mv_usd)+' · '+U(tr.invested_mv_usd))
 +L('TRUE P&amp;L since inception','A$'+S(tr.true_pnl_aud)+' · US$'+S(tr.true_pnl_usd)+' ('+S(tr.true_pnl_pct_of_opening_capital,2)+'%)',C(tr.true_pnl_usd))
 +'<div class="ts">As of '+(tr.book_updated||'').slice(0,16).replace('T',' ')+' UTC · AUDUSD '+(fx||'—')+' · '+(fd.n_long??'–')+' long / '+(fd.n_short??'–')+' short</div>';}).catch(e=>{const el=document.getElementById('wcfund');if(el)el.querySelector('.ts').textContent='feed unavailable';});}
function oanda(){fetch('/api/m',{cache:'no-store'}).then(r=>r.json()).then(d=>{const el=document.getElementById('wcoanda');if(!el)return;const o=d.oanda||{};
 if(o.nav_aud==null){el.innerHTML='<h3>OANDA practice · PAPER</h3><div class="ts">no snapshot</div>';return;}
 if(o.audusd!=null&&o.audusd>0&&FX==null)FX=o.audusd;
 el.innerHTML='<h3>OANDA practice · PAPER</h3>'+L('NAV','A$'+Math.round(o.nav_aud).toLocaleString('en-US'))+L('P&amp;L vs start','A$'+S(o.pnl_aud)+' · US$'+S(o.pnl_usd),C(o.pnl_aud))+L('Open trades',(o.tfo_open??'–')+' · uPnL A$'+S(o.tfo_upl_aud))+L('Status',(o.badge||'')+' · '+((o.reconcile||{}).reconcile_status||''))
 +'<div class="ts">As of '+(o.asof_utc||'').slice(0,16).replace('T',' ')+' UTC · practice account, not desk equity</div>';}).catch(e=>{});}
function trend(){fetch('/static/trend_core_status.json?t='+Date.now(),{cache:'no-store'}).then(r=>r.json()).then(s=>{const el=document.getElementById('wctrend');if(!el)return;
 if(s.nav_aud==null){el.innerHTML='<h3>TREND-CORE · PAPER</h3><div class="ts">no snapshot</div>';return;}
 if(s.audusd!=null&&s.audusd>0)FX=s.audusd; // prefer live book FX when present
 const powder=s.free_cash_aud!=null?s.free_cash_aud:s.cash_aud;
 const invested=s.deployed_aud;
 const pnl=s.vs_seed_aud;
 const nOpen=(s.holdings||[]).filter(h=>(h.qty||0)||(h.value_aud||0)).length;
 el.innerHTML='<h3>TREND-CORE · A$1m paper</h3>'
  +L('TOTAL BOOK VALUE',dualAud(s.nav_aud))
  +L('AVAILABLE POWDER (cash)',dualAud(powder))
  +L('INVESTED',dualAud(invested))
  +L('TRUE P&amp;L since inception',dualPnlAud(pnl)+(s.seed_aud!=null?' (vs A$'+Math.round(s.seed_aud).toLocaleString('en-US')+' seed)':''),C(pnl))
  +'<div class="ts">As of '+(s.updated_wita||(s.mark_utc||'').slice(0,16).replace('T',' ')+' UTC')+' · AUDUSD '+(FX||'—')+' · '+nOpen+' holdings · path B, not firm equity</div>';
 }).catch(e=>{const el=document.getElementById('wctrend');if(el)el.querySelector('.ts').textContent='feed unavailable';});}
function crypto(){fetch('/static/btc_pounce_status.json?t='+Date.now(),{cache:'no-store'}).then(r=>r.json()).then(s=>{const el=document.getElementById('wccrypto');if(!el)return;
 if(s.equity_est_aud==null&&s.cash_aud==null){el.innerHTML='<h3>Crypto Sleeve · PAPER</h3><div class="ts">no snapshot</div>';return;}
 const total=s.equity_est_aud!=null?s.equity_est_aud:s.cash_aud;
 const powder=s.cash_aud;
 const invested=(total!=null&&powder!=null)?Math.max(0,total-powder):null;
 const pnl=(s.total_pnl_aud_after_fees!=null)?s.total_pnl_aud_after_fees
   :((s.equity_est_aud!=null&&s.book_start_aud!=null)?(s.equity_est_aud-s.book_start_aud):s.interest_aud);
 const open=s.position?1:0;
 el.innerHTML='<h3>Crypto Sleeve · PAPER</h3>'
  +L('TOTAL BOOK VALUE',dualAud(total))
  +L('AVAILABLE POWDER (cash)',dualAud(powder))
  +L('INVESTED',dualAud(invested))
  +L('TRUE P&amp;L since inception',dualPnlAud(pnl),C(pnl))
  +'<div class="ts">BTC pounce A$1m paper · As of '+(s.generated_utc||'').toString().slice(0,16).replace('T',' ')+' UTC · AUDUSD '+(FX||'—')+' · '+open+' open · '+(s.n_trades||0)+' trades</div>';
 }).catch(e=>{const el=document.getElementById('wccrypto');if(el)el.querySelector('.ts').textContent='feed unavailable';});}
function go(){if(mount()){fund();oanda();trend();crypto();}}
document.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{go();setInterval(()=>{fund();oanda();trend();crypto();},60000)},50));
})();
