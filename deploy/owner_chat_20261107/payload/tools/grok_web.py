"""supergrok_20261102 (Brett 20:37 WITA, PAPER, advisory): one persistent Chromium (Xvfb :89) on grok.com, logged in by Brett himself
via the tailnet-only noVNC view (tfo-grok-signin). Mirrors tools/chatgpt_web.py: drives the page UI only (type in composer, wait,
read last assistant message; optional image attach). Never logs in, never touches passwords, never reads/exports cookies or tokens,
never calls grok.com backend APIs. Picks the strongest Grok mode offered (Heavy > Expert > Think/Reasoning), never an upsell.
Local API 127.0.0.1:18892: GET /health, POST /ask {prompt, timeout, images?}. 1 call at a time.
Chat notices (DESK advisor thread, role supergrok): sign-in URL when logged out (max once per 6 h), proof reply on first login."""
import json, os, re, threading, time, queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
L = "/opt/tfo/live/thf"
PROFILE = L + "/state/grok_profile"; ST = L + "/state/thf/grok_web_status.json"
IMG_ROOT = L + "/state/thf/chat_imgs"; DUMP = "/tmp/grok_dom.html"
HOME = "https://grok.com/"; PORT = 18892
SIGNIN_URL = ""
Q = queue.Queue(); STATUS = {"state": "starting", "route": "grok_com_page_ui"}
def _old():
    try: return json.load(open(ST))
    except Exception: return {}
def _save(**k):
    STATUS.update(k, ts=time.strftime("%Y-%m-%d %H:%M:%S WITA"))
    try: tmp = ST + ".tmp"; json.dump(STATUS, open(tmp, "w")); os.replace(tmp, ST)
    except Exception: pass
COMPOSER = "textarea[aria-label*='Ask' i], textarea[placeholder*='help' i], div.ProseMirror[contenteditable='true'], div.tiptap[contenteditable='true'], form textarea, div[contenteditable='true']"
LOGIN = "a[href*='sign-in'], a[href*='accounts.x.ai'], a[href*='/login'], button[data-testid*='sign-in' i]"
STREAM = "button[aria-label*='Stop' i], button[data-testid*='stop' i]"
def _probe(pg):
    try:
        u = pg.url or ""
        if "challenge" in u or re.search(r"just a moment|attention required", pg.title() or "", re.I) or pg.locator("iframe[src*='challenges.cloudflare'], #challenge-form, :text('Performing security verification')").count(): return "captcha"
        if "accounts.x.ai" in u or "sign-in" in u: return "needs_signin"
        has_box = pg.locator(COMPOSER).count()
        has_login = pg.locator(LOGIN).count() or pg.get_by_role("link", name=re.compile(r"^\s*(sign in|log in|sign up)\s*$", re.I)).count() \
            or pg.get_by_role("button", name=re.compile(r"^\s*(sign in|log in|sign up)\s*$", re.I)).count()
        if has_box and not has_login: return "logged_in"
        return "needs_signin"
    except Exception as x: return "error:" + type(x).__name__
def _safe_imgs(paths):
    out = []; root = os.path.realpath(IMG_ROOT)
    for p in (paths or []):
        if not isinstance(p, str) or not p.strip(): continue
        rp = os.path.realpath(p.strip())
        if not (rp == root or rp.startswith(root + os.sep)): raise RuntimeError("image path not under chat_imgs")
        if not os.path.isfile(rp): raise RuntimeError("image missing")
        out.append(rp)
    return out
def _attach_images(pg, images):
    if not images: return
    fin = pg.locator("input[type='file']")
    if fin.count() == 0:
        att = pg.locator("button[aria-label*='Attach' i], button[aria-label*='Upload' i]")
        if att.count(): att.first.click(); pg.wait_for_timeout(600)
        fin = pg.locator("input[type='file']")
    if fin.count() == 0: raise RuntimeError("no file input for images")
    fin.first.set_input_files(images); t0 = time.time()
    while time.time() - t0 < 45:
        pg.wait_for_timeout(700)
        if pg.locator("form img, button[aria-label*='Remove' i], [class*='attachment' i] img, img[alt*='Upload' i]").count(): pg.wait_for_timeout(1200); return
    open(DUMP, "w").write(pg.content()); raise RuntimeError("image preview timeout")
# strongest first; an option mentioning upgrade/subscribe/locked is never clicked (no upsell, no purchase)
MODEL_PREF = [r"\bheavy\b", r"\bexpert\b", r"\bthink|reasoning\b"]
UPSELL = re.compile(r"upgrade|subscribe|get supergrok|unlock|try heavy|buy", re.I)
def _pick_model(pg):
    """Open Grok's model/mode switcher and choose Heavy > Expert > Think. Never raises (fallback = default/Auto)."""
    try:
        btn = pg.locator("#model-select-trigger, button[aria-label*='Model' i], button[aria-label*='mode' i][aria-haspopup], button[aria-haspopup='menu']:has-text('Auto'), button[aria-haspopup='menu']:has-text('Expert'), button[aria-haspopup='menu']:has-text('Fast'), button[aria-haspopup='menu']:has-text('Heavy')").first
        if not btn.count(): STATUS["model_pick"] = "switcher missing -> default"; return
        btn.click(); pg.wait_for_timeout(900)
        items = pg.locator("[role='menuitem'], [role='menuitemradio'], [role='option']")
        opts = []
        for i in range(min(items.count(), 40)):
            try: opts.append((i, (items.nth(i).inner_text(timeout=1500) or "").strip()))
            except Exception: pass
        STATUS["model_options"] = [o[1].replace("\n", " | ")[:60] for o in opts]
        for rx in MODEL_PREF:
            for i, t in opts:
                head = t.split("\n")[0]
                if t and re.search(rx, head, re.I) and not UPSELL.search(t):
                    try:
                        if items.nth(i).get_attribute("aria-disabled") == "true" or items.nth(i).get_attribute("data-disabled") is not None: continue
                    except Exception: pass
                    items.nth(i).click(); pg.wait_for_timeout(700)
                    if pg.locator("[role='dialog']:has-text('Upgrade'), [role='dialog']:has-text('Subscribe')").count():   # tier not on this sub -> close, try next
                        pg.keyboard.press("Escape"); pg.wait_for_timeout(400); btn.click(); pg.wait_for_timeout(700); continue
                    pg.keyboard.press("Escape"); STATUS["model_pick"] = head[:60]; return
        pg.keyboard.press("Escape"); STATUS["model_pick"] = "no match -> default"
    except Exception as x:
        STATUS["model_pick"] = "pick err %s -> default" % type(x).__name__
        try: pg.keyboard.press("Escape")
        except Exception: pass
# last assistant bubble: Grok aligns user rows to the end (items-end) and assistant rows to the start
JS = """()=>{const b=[...document.querySelectorAll('.message-bubble,[data-testid="assistant-message"],[class*="response-content"]')]
 .filter(e=>!e.closest('.items-end,[data-testid="user-message"]'));const e=b[b.length-1];if(!e)return '';
 const m=e.querySelector('.response-content-markdown,[class*="markdown"]');return (m||e).innerText||''}"""
THINKING = re.compile(r"^(thinking|thought for|researching|agents? (are )?thinking|searching|reading)\b.{0,80}$", re.I | re.S)
def _ask(pg, prompt, timeout, images=None):
    images = _safe_imgs(images)
    pg.goto(HOME, wait_until="domcontentloaded", timeout=45000); pg.wait_for_timeout(3000)   # fresh page = new chat, no stale history
    st = _probe(pg)
    if st != "logged_in": raise RuntimeError("grok " + st)
    _pick_model(pg)
    baseline = (pg.evaluate(JS) or "").strip()
    if images: _attach_images(pg, images)
    box = pg.locator(COMPOSER).first; box.click()
    if box.evaluate("e=>e.tagName") == "TEXTAREA": box.fill(prompt)
    else: pg.keyboard.insert_text(prompt)
    pg.wait_for_timeout(300); pg.keyboard.press("Enter")
    t0 = time.time(); last = None; same = 0; saw_stop = False; prompt_s = (prompt or "").strip()
    while time.time() - t0 < timeout:
        pg.wait_for_timeout(2000)
        streaming = pg.locator(STREAM).count() > 0; saw_stop = saw_stop or streaming
        txt = (pg.evaluate(JS) or "").strip()
        if not txt or txt == baseline or txt == prompt_s or THINKING.match(txt): same = 0; continue
        same = same + 1 if (txt == last and not streaming) else 0; last = txt
        if same >= (2 if saw_stop else 5): return txt   # without a seen stop button demand ~10 s of stable text
    open(DUMP, "w").write(pg.content()); raise RuntimeError("grok timeout")
def _on_state(prev, st, pg):
    """Record the page state. Do not post into the owner chat and do not send a proof prompt."""
    if st == "captcha":
        _save(state="captcha", blocked=True)
        return
    if st != prev:
        _save(state=st)
def worker():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True); os.chmod(PROFILE, 0o700); os.makedirs(IMG_ROOT, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, args=["--no-first-run"], viewport={"width": 1280, "height": 860})
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try: pg.goto(HOME, wait_until="domcontentloaded", timeout=45000); pg.wait_for_timeout(4000)
        except Exception: pass
        last = 0; prev = None
        while True:
            try: job = Q.get(timeout=5)
            except queue.Empty:
                # logged out: re-probe every 30 s (Brett may be signing in via noVNC); never navigate away mid-login
                if time.time() - last > (120 if prev == "logged_in" else 30):
                    last = time.time(); st = _probe(pg); _save(state=st)
                    if st != prev: _on_state(prev, st, pg); prev = st
                continue
            try:
                r = dict(ok=True, text=_ask(pg, job["prompt"], job["timeout"], job.get("images")), model=STATUS.get("model_pick"), model_options=STATUS.get("model_options"))
                _save(state="logged_in", last_ok=time.strftime("%Y-%m-%d %H:%M:%S WITA")); prev = "logged_in"
            except Exception as x: r = dict(ok=False, error=str(x)[:160]); prev = _probe(pg); _save(state=prev, last_err=str(x)[:120])
            job["out"].put(r)
class H(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def _j(s, c, d): b = json.dumps(d).encode(); s.send_response(c); s.send_header("Content-Type", "application/json"); s.end_headers(); s.wfile.write(b)
    def do_GET(s): s._j(200, STATUS)
    def do_POST(s):
        if s.path.rstrip("/") != "/ask": return s._j(404, dict(ok=False, error="POST /ask"))
        b = json.loads(s.rfile.read(int(s.headers.get("Content-Length") or 0)) or b"{}"); o = queue.Queue(1)
        imgs = b.get("images") or []
        if not isinstance(imgs, list): imgs = []
        Q.put(dict(prompt=str(b.get("prompt", ""))[:12000], timeout=max(30, min(int(b.get("timeout", 180)), 600)), images=imgs[:8], out=o))
        try: s._j(200, o.get(timeout=660))
        except queue.Empty: s._j(504, dict(ok=False, error="timeout"))
if __name__ == "__main__":
    import sys; sys.path.insert(0, L)
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
