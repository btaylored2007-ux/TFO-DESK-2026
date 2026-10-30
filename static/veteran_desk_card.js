// Veteran Desk link card (Brett 2026-10-28 16:49 WITA). Read-only: reads static/veteran_desk.json, links to the desk dashboard.
(function(){var u='http://100.127.19.45:18860/';var d=document.createElement('a');d.href=u;d.target='_blank';d.id='vetdesk-card';
d.style.cssText='display:block;margin:10px;padding:12px 14px;border:1px solid #3a4a5a;border-radius:10px;background:#111a24;color:#e8eef5;text-decoration:none;font:600 15px system-ui';
d.textContent='VETERAN DESK (paper) · 10 trader books · open dashboard →';
document.body.insertBefore(d,document.body.firstChild);
fetch('static/veteran_desk.json',{cache:'no-store'}).then(r=>r.json()).then(j=>{var s=j.summary||j;if(s&&s.passed!=null)d.textContent+=' · ideas '+(s.proposals||0)+', passed '+s.passed}).catch(()=>{});})();
