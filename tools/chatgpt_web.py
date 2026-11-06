"""gptweb_20261102c (PAPER, advisory): one persistent Chromium (Xvfb :88) logged into chatgpt.com by Brett himself.
Drives the page UI only (type in composer, wait, read last assistant message; optional image attach). Never reads/exports cookies or tokens,
never calls ChatGPT backend APIs. Local API 127.0.0.1:18891: GET /health, POST /ask {prompt, timeout, images?}. 1 call at a time."""
import json, os, threading, time, queue, math, re
try:
    from tools.chatgpt_admission import Admission
except ModuleNotFoundError:
    from chatgpt_admission import Admission
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PROFILE = "/opt/tfo/live/thf/state/chatgpt_profile"; ST = "/opt/tfo/live/thf/state/thf/chatgpt_status.json"
IMG_ROOT = "/opt/tfo/live/thf/state/thf/chat_imgs"
Q = queue.Queue(); STATUS = {"state": "starting", "delivery": "ack_poll_v1"}; BUSY = [False]   # fablefirst_20261103: /health reports queue depth + busy so callers skip instead of piling up
# Finished answers stay here after the HTTP client goes away. This does not drop the browser queue.
RESULT_TTL_S = 1800; RESULT_MAX = 200; RESULTS = {}; RESULTS_LOCK = threading.Lock()
# One active browser plus one pending trader. Admission is shared by all clients.
ASK_LOCK = threading.Lock(); ASK_ACTIVE = [0]
ADMISSION = Admission(capacity=2)
RECEIPTS = os.path.join(os.path.dirname(os.path.dirname(ST)), "..", "logs", "chatgpt_bridge_requests.jsonl")
BRAIN_ROOT="/opt/tfo/live/thf/state/thf/seat_brain"
def _prompt_readback(prompt):
    """Bounded local evidence only. Never changes admission, provider use or a reply."""
    import hashlib,re
    evidence=[];body=str(prompt or "")
    for block in re.finditer(r"MY BRAIN \[THF seat=([a-z0-9_]{1,40});[^\n]*\n((?:LESSONS:[^\n]*\n?)+)",body):
        seat=block.group(1)
        path=os.path.join(BRAIN_ROOT,seat,"lessons.jsonl")
        saved={}
        try:
            if os.path.getsize(path)>2_000_000:continue
            with open(path) as stream:lines=stream.read().splitlines()[-1000:]
            for line in lines:
                try:
                    row=json.loads(line)
                    if row.get("ref"):saved[str(row["ref"])]=row
                except (ValueError,AttributeError):pass
        except OSError:pass
        for line in block.group(2).splitlines():
            match=re.search(r"\bref=([^\]\r\n]+)\]\s*(.*)",line)
            if not match:continue
            ref,received=match.groups();row=saved.get(ref);lesson=str((row or {}).get("lesson") or "")
            evidence.append(dict(brain_seat=seat,ref=ref,source="own THF brain",
              saved_kind=(row or {}).get("kind"),saved_lesson_sha256=hashlib.sha256(lesson.encode()).hexdigest() if lesson else None,
              full_saved_lesson_in_request=bool(lesson and lesson in received),
              request_lesson_sha256=hashlib.sha256(received.encode()).hexdigest()))
            if len(evidence)>=8:return evidence
    return evidence

def _receipt(event, job, **extra):
    import hashlib
    prompt=str(job.get("prompt") or "")
    rec = dict(event=event, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               request_id=job.get("request_id"), seat=job.get("seat"), workload=job.get("workload"),
               accepted_at=job.get("accepted_at"), started_at=job.get("started_at"),
               deadline=job.get("deadline"),prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
               prompt_chars=len(prompt),**extra)
    try:
        rec["brain_readback"]=_prompt_readback(prompt)
    except Exception:
        rec["brain_readback_status"]="diagnostic_unavailable"
    try:
        with ASK_LOCK:
            with open(RECEIPTS, "a") as stream:
                stream.write(json.dumps(rec)+"\n");stream.flush();os.fsync(stream.fileno())
    except OSError: pass

def _admit(job, skip_if_busy=False, wait_for_slot=False):
    ok, reason = ADMISSION.reserve(job, skip_if_busy, external_busy=bool(BUSY[0] or Q.qsize()), wait_for_slot=wait_for_slot)
    if not ok:
        job["admission_error"] = reason
        _receipt("rejected", job, reason=reason)
        return False
    try: Q.put(job)
    except BaseException:
        ADMISSION.finish(job, dict(ok=False, error="queue unavailable"))
        raise
    ASK_ACTIVE[0] = ADMISSION.snapshot()["inflight"]
    _receipt("accepted", job)
    return True

def _request_finished(job=None):
    # The worker owns release. A timed-out HTTP handler must not free its slot
    # while the browser may still be generating a response.
    ASK_ACTIVE[0] = ADMISSION.snapshot()["inflight"]

def _save(**k):
    STATUS.update(k, ts=time.strftime("%Y-%m-%d %H:%M:%S WITA")); tmp = ST + ".tmp"; json.dump(STATUS, open(tmp, "w")); os.replace(tmp, ST)
# gptrecycle_20261103 (master fixer 22:35 WITA, F1 "AI bridges ... fix it permanently"): the one chatgpt.com tab's renderer grew to
# ~2 GB RSS by 22:31 (1.57 GB at 22:00) on the 8 GB box with 326 MB swap left - the next OOM kill would hit this browser or a seat
# round. Between answers (queue empty) the tab is swapped for a fresh one once its renderer passes RECYCLE_MB; the persistent
# profile (Brett's login) is untouched. /health shows renderer_mb + recycles.
RECYCLE_MB = int(os.environ.get("CHATGPT_RECYCLE_MB", "1100"))
def _cg_of(pid):
    try: return open("/proc/%s/cgroup" % pid).read().strip()
    except OSError: return None
def renderer_mb(proc="/proc", me=None):
    """Largest Chromium renderer RSS (MB) in THIS service's cgroup (other Playwright browsers on the box are ignored)."""
    mine = _cg_of(me or os.getpid()); best = 0
    for d in os.listdir(proc):
        if not d.isdigit(): continue
        try:
            if "--type=renderer" not in open("%s/%s/cmdline" % (proc, d), "rb").read().decode("utf-8", "replace"): continue
            if mine and _cg_of(d) != mine: continue
            for ln in open("%s/%s/status" % (proc, d)):
                if ln.startswith("VmRSS:"): best = max(best, int(ln.split()[1]) // 1024); break
        except (OSError, ValueError): continue
    return best
def should_recycle(mb, qsize, limit=None): return qsize == 0 and mb > (RECYCLE_MB if limit is None else limit)
def _recycle(ctx, pg):
    """Open a fresh tab on chatgpt.com, then close the old one (its renderer exits). Returns the page to use."""
    try:
        new = ctx.new_page(); new.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
    except Exception: return pg
    try: pg.close()
    except Exception: pass
    STATUS["recycles"] = int(STATUS.get("recycles") or 0) + 1; STATUS["last_recycle"] = time.strftime("%Y-%m-%d %H:%M:%S WITA")
    return new
def _probe(pg):
    try:
        u = pg.url
        if "challenge" in u or pg.locator("iframe[src*='challenges.cloudflare']").count(): return "captcha"
        has_box = pg.locator("#prompt-textarea, div[contenteditable='true'], textarea").count()
        has_login = pg.locator("button[data-testid='login-button'], a[href*='/auth/login']").count() or pg.get_by_role("button", name="Log in").count()
        if has_box and not has_login: return "logged_in"
        return "needs_signin"
    except Exception as x: return "error:" + type(x).__name__
def _safe_imgs(paths):
    out = []
    root = os.path.realpath(IMG_ROOT)
    for p in (paths or []):
        if not isinstance(p, str) or not p.strip():
            continue
        rp = os.path.realpath(p.strip())
        if not (rp == root or rp.startswith(root + os.sep)):
            raise RuntimeError("image path not under chat_imgs")
        if not os.path.isfile(rp):
            raise RuntimeError("image missing")
        out.append(rp)
    return out
def _attach_images(pg, images):
    if not images:
        return
    fin = pg.locator('input[type="file"][accept*="image"], input[aria-label="Attach photos or videos"], input[aria-label="Attach photos"], input[aria-label="Attach files"]')
    if fin.count() == 0:
        plus = pg.locator('button[aria-label="Add files and more"], button[data-composer-navigation-target="add-context"]')
        if plus.count():
            plus.first.click(); pg.wait_for_timeout(600)
        fin = pg.locator('input[type="file"]')
    if fin.count() == 0:
        raise RuntimeError("no file input for images")
    fin.first.set_input_files(images)
    t0 = time.time()
    while time.time() - t0 < 45:
        pg.wait_for_timeout(700)
        # preview thumb / remove-attachment control / un-hidden attachments tray
        prev = pg.locator(
            '[data-composer-attachments]:not([hidden]) img, '
            '[data-composer-attachments] img, '
            'button[aria-label*="Remove file"], button[aria-label*="Remove"], '
            'div[data-composer-attachments] [class*="thumbnail"], '
            'img[alt*="Attachment"], img[alt*="Uploaded"]'
        )
        tray = pg.locator('[data-composer-attachments]:not([hidden])')
        if prev.count() or tray.count():
            return
    open("/tmp/gpt_dom.html", "w").write(pg.content())
    raise RuntimeError("image preview timeout")
import re as _re
from urllib.parse import urlparse, parse_qs
MODEL_PREF = [r"gpt[- ]?6", r"\bpro\b", r"heavy|extended|thinking", r"\bo3[- ]?pro\b", r"\bo3\b"]   # fablefirst_20261103: strongest reasoning first (GPT-6 > Pro > Thinking), never an upsell
def _model_now(pg):
    try:
        t = pg.locator("[class*='ModelPickerTrigger']").first
        if t.count(): return (t.inner_text(timeout=1200) or "").replace("\n", " ").strip()[:40]
    except Exception: pass
    return "default"
EFFORT_PREF = os.environ.get("THF_CHATGPT_EFFORT", "max")   # gptpick_20261103: thinking-effort slider: max | high (one notch below max)
def _open_picker(pg):
    """gptpick_20261103: the composer chip is 'Thinking effort' (not the model name); it opens a popover with '<model> >' (ViewToggle) + an effort slider."""
    btn = pg.locator("button:has([class*='ModelPickerTrigger']), button:has-text('Thinking effort'), button[aria-haspopup='menu']:has-text('5.'), button:has-text('Medium')").first
    if not btn.count(): return False
    try:
        if btn.get_attribute("aria-expanded") == "true": return True
    except Exception: pass
    btn.click(timeout=4000, force=True); pg.wait_for_timeout(800); return True
def _pick_model(pg):
    """gptpick_20261103 (B20a, Brett: strongest ChatGPT mode must stay selected): popover -> '<model> >' view toggle -> model radio list
    (GPT-6 > Pro > ...; items with cursor-default are locked/upsell and skipped) -> effort slider to the top (End) -> Escape.
    Never raises; always records what the chip shows."""
    try:
        if not _open_picker(pg): STATUS["model_pick"] = _model_now(pg); return
        vt = pg.locator("[role='menuitem'][class*='ViewToggle']").first
        if vt.count():
            try: vt.click(timeout=3000, force=True); pg.wait_for_timeout(800)
            except Exception: pass
        radios = pg.locator("[role='menuitemradio']"); opts = []
        for i in range(min(radios.count(), 20)):
            try: opts.append((i, (radios.nth(i).inner_text(timeout=800) or "").split("\n")[0].strip(), radios.nth(i).get_attribute("class") or "", radios.nth(i).get_attribute("aria-checked")))
            except Exception: pass
        STATUS["model_options"] = [o[1] + (" (on)" if o[3] == "true" else "") for o in opts if o[1]]
        picked = None
        for rx in MODEL_PREF:
            for i, head, cls, chk in opts:
                if head and _re.search(rx, head, _re.I) and "cursor-default" not in cls:
                    if chk != "true":
                        try: radios.nth(i).click(timeout=4000, force=True); pg.wait_for_timeout(900)
                        except Exception: continue
                    picked = head; break
            if picked: break
        STATUS["model_wanted"] = picked
        eff_label = None
        try:   # effort slider (role=slider, aria-valuenow 0..aria-valuemax; verified 3 Nov: Medium=1, High=2, Extra High=3): ArrowRight steps it up (End does nothing)
            sl = pg.locator("[role='slider']").first
            if sl.count():
                vmax = int(sl.get_attribute("aria-valuemax") or 3); before = int(sl.get_attribute("aria-valuenow") or 0)
                target = vmax if EFFORT_PREF != "high" else max(0, vmax - 1)
                sl.focus(timeout=1500); pg.wait_for_timeout(200); now = before
                for _ in range(8):
                    if now == target: break
                    pg.keyboard.press("ArrowRight" if now < target else "ArrowLeft"); pg.wait_for_timeout(350); now = int(sl.get_attribute("aria-valuenow") or now)
                try: eff_label = (pg.locator("[role='menuitem'][class*='ViewToggle']").first.inner_text(timeout=800) or "").split("\n")[0].strip()
                except Exception: pass
                STATUS["effort"] = dict(before=before, now=now, max=vmax, pref=EFFORT_PREF, label=eff_label)
        except Exception as x: STATUS["effort"] = dict(err=str(x)[:80])
        try:   # the model that is really on (radio aria-checked) + the effort label = the honest pick
            on = pg.locator("[role='menuitemradio'][aria-checked='true']").first
            model_on = (on.inner_text(timeout=800) or "").split("\n")[0].strip() if on.count() else None
        except Exception: model_on = None
        try: pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        except Exception: pass
        STATUS["model_pick"] = ("%s %s" % (model_on or "?", eff_label or "")).strip() if model_on else _model_now(pg)
    except Exception:
        STATUS["model_pick"] = _model_now(pg)
        try: pg.keyboard.press("Escape")
        except Exception: pass
def _pick_debug(pg):
    """gptpick_20261103: open the composer model switcher, record every item (text, role, aria, has-submenu), hover the first strongest match,
    record the submenu, screenshot each step into /tmp/gpt_pick_*.png, then Escape. Never changes the selection. Returns the report."""
    rep = dict(ok=True, chip_before=_model_now(pg), items=[], sub=[], shots=[])
    try:
        pg.screenshot(path="/tmp/gpt_pick_0.png"); rep["shots"].append("/tmp/gpt_pick_0.png")
        btn = pg.locator("button:has([class*='ModelPickerTrigger']), button[aria-haspopup='menu']:has-text('5.'), button:has-text('Medium')").first
        rep["btn_found"] = bool(btn.count())
        if not btn.count(): return rep
        btn.click(timeout=4000); pg.wait_for_timeout(900)
        pg.screenshot(path="/tmp/gpt_pick_1_menu.png"); rep["shots"].append("/tmp/gpt_pick_1_menu.png")
        items = pg.locator("[role='menuitem'], [role='menuitemradio'], [role='option'], [role='menuitemcheckbox']")
        for i in range(min(items.count(), 40)):
            it = items.nth(i)
            try: rep["items"].append(dict(i=i, text=(it.inner_text(timeout=1000) or "").replace("\n", " | ")[:80], role=it.get_attribute("role"), haspopup=it.get_attribute("aria-haspopup"), checked=it.get_attribute("aria-checked"), expanded=it.get_attribute("aria-expanded"), cls=(it.get_attribute("class") or "")[:60]))
            except Exception as x: rep["items"].append(dict(i=i, err=str(x)[:60]))
        tgt = None
        for rx in MODEL_PREF:
            for it in rep["items"]:
                if it.get("text") and _re.search(rx, it["text"].split(" | ")[0], _re.I): tgt = it; break
            if tgt: break
        rep["target"] = tgt
        if tgt is not None:
            items.nth(tgt["i"]).hover(timeout=3000); pg.wait_for_timeout(900)
            pg.screenshot(path="/tmp/gpt_pick_2_hover.png"); rep["shots"].append("/tmp/gpt_pick_2_hover.png")
            sub = pg.locator("[role='menuitem'], [role='menuitemradio'], [role='option'], [role='menuitemcheckbox']")
            for j in range(min(sub.count(), 40)):
                try: rep["sub"].append(dict(j=j, text=(sub.nth(j).inner_text(timeout=800) or "").replace("\n", " | ")[:80], role=sub.nth(j).get_attribute("role"), checked=sub.nth(j).get_attribute("aria-checked")))
                except Exception: pass
        try: pg.keyboard.press("Escape"); pg.wait_for_timeout(300); pg.keyboard.press("Escape")
        except Exception: pass
        rep["chip_after"] = _model_now(pg); return rep
    except Exception as x:
        rep.update(ok=False, error=str(x)[:160])
        try: pg.keyboard.press("Escape")
        except Exception: pass
        return rep
def _pick_set(pg, want=None):
    """gptpick_20261103 debug: run _pick_model, then reopen the popover and report what it shows (+ screenshot /tmp/gpt_pick_4_menu2.png)."""
    rep = dict(ok=False, chip_before=_model_now(pg))
    try:
        _pick_model(pg); rep.update(status={k: STATUS.get(k) for k in ("model_pick", "model_options", "model_wanted", "effort")})
        pg.wait_for_timeout(500); _open_picker(pg); pg.wait_for_timeout(700); pg.screenshot(path="/tmp/gpt_pick_4_menu2.png")
        items = pg.locator("[role='menuitem'], [role='menuitemradio'], [role='slider']"); rep["menu2"] = []
        for i in range(min(items.count(), 30)):
            try: rep["menu2"].append(dict(i=i, text=(items.nth(i).inner_text(timeout=800) or "").replace("\n", " | ")[:80], role=items.nth(i).get_attribute("role"), checked=items.nth(i).get_attribute("aria-checked"), valuenow=items.nth(i).get_attribute("aria-valuenow"), valuetext=items.nth(i).get_attribute("aria-valuetext")))
            except Exception: pass
        try: pg.keyboard.press("Escape")
        except Exception: pass
        rep["ok"] = True; rep["chip_final"] = _model_now(pg); return rep
    except Exception as x:
        rep["error"] = str(x)[:160]
        try: pg.keyboard.press("Escape")
        except Exception: pass
        return rep
def _menu_state(pg):
    out = []
    items = pg.locator("[role='menuitem'], [role='menuitemradio'], [role='slider']")
    for i in range(min(items.count(), 12)):
        try: out.append(((items.nth(i).inner_text(timeout=500) or "").replace("\n", "|")[:24], items.nth(i).get_attribute("role")[:6], items.nth(i).get_attribute("aria-checked"), items.nth(i).get_attribute("aria-valuenow")))
        except Exception: pass
    return out
def _pick_trace(pg):
    """gptpick_20261103 debug: step-by-step model/effort selection with a screenshot + menu state after every step; no track clicks."""
    T = []; shot = lambda n: (pg.screenshot(path="/tmp/gpt_tr_%s.png" % n), "/tmp/gpt_tr_%s.png" % n)[1]
    def rec(step):
        try: T.append(dict(step=step, chip=_model_now(pg), menu=_menu_state(pg), shot=shot(len(T))))
        except Exception as x: T.append(dict(step=step, err=str(x)[:80]))
    try:
        _open_picker(pg); pg.wait_for_timeout(600); rec("1 popover open")
        vt = pg.locator("[role='menuitem'][class*='ViewToggle']").first
        if vt.count(): vt.click(timeout=3000, force=True); pg.wait_for_timeout(700)
        rec("2 after view toggle")
        r6 = pg.locator("[role='menuitemradio']").filter(has_text=_re.compile(r"gpt[- ]?6", _re.I)).first
        if r6.count(): r6.click(timeout=4000, force=True); pg.wait_for_timeout(900)
        rec("3 after click GPT-6")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500); rec("4 after escape")
        _open_picker(pg); pg.wait_for_timeout(700); rec("5 reopened")
        sl = pg.locator("[role='slider']").first
        if sl.count():
            sl.focus(timeout=1500); pg.wait_for_timeout(200); pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(500)
        rec("6 after ArrowRight on slider")
        if sl.count(): pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(500)
        rec("7 after 2nd ArrowRight")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500); _open_picker(pg); pg.wait_for_timeout(700); rec("8 reopened again")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        return dict(ok=True, trace=T, chip_final=_model_now(pg))
    except Exception as x:
        try: pg.keyboard.press("Escape")
        except Exception: pass
        return dict(ok=False, error=str(x)[:160], trace=T)
def _ask(pg, prompt, timeout, images=None):
    deadline = time.time() + timeout  # includes navigation, model selection and image setup
    images = _safe_imgs(images)
    pg.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000); pg.wait_for_timeout(2500)
    st = _probe(pg)
    if st != "logged_in": raise RuntimeError(st)
    SEL = "[data-markdown-text-style='assistant-message'], [data-message-author-role='assistant']"
    JS = "()=>{const q=[...document.querySelectorAll('[data-markdown-text-style=assistant-message],[data-message-author-role=assistant]')];let e=q[q.length-1];if(!e){const m=[...document.querySelectorAll('.markdown')];e=m[m.length-1]};return e?e.innerText:''}"
    # Prefer New chat so prior history does not confuse baseline/reply matching
    try:
        nc = pg.get_by_role("button", name="New chat")
        if nc.count() == 0:
            nc = pg.locator("a[data-testid='create-new-chat-button'], button[data-testid='create-new-chat-button'], button[aria-label='New chat']")
        if nc.count():
            nc.first.click(); pg.wait_for_timeout(1500)
    except Exception:
        pass
    _pick_model(pg)
    baseline = (pg.evaluate(JS) or "").strip()
    if images:
        _attach_images(pg, images)
    box = pg.locator("#prompt-textarea") if pg.locator("#prompt-textarea").count() else pg.locator("div[contenteditable='true']").first
    box.click()
    if box.evaluate("e=>e.tagName") == "TEXTAREA":
        box.fill("")          # fablefirst_20261103: clear any stale text first
        box.fill(prompt)
    else:   # contenteditable (ProseMirror): select-all + delete, else insert_text APPENDS to leftover text and corrupts the prompt
        try:
            pg.keyboard.press("Control+A"); pg.keyboard.press("Delete")
        except Exception: pass
        pg.keyboard.insert_text(prompt)
    if time.time() >= deadline: raise RuntimeError("deadline elapsed before submission")
    pg.keyboard.press("Enter"); t0 = time.time(); last = None; same = 0
    prompt_s = (prompt or "").strip()
    # Do NOT match bare aria-label="Stop" — composer keeps that after reply completes (square stop/send).
    STREAM = "button[data-testid='stop-button'], button[aria-label='Stop generating'], button[aria-label='Stop streaming'], button[aria-label*='Stop generating']"
    while time.time() < deadline:
        pg.wait_for_timeout(1500)
        txt = (pg.evaluate(JS) or "").strip()
        if not txt or txt == baseline or txt == prompt_s:
            continue
        streaming = pg.locator(STREAM).count()
        # Also treat incomplete turn + composer Stop as streaming
        if not streaming:
            if pg.locator("button[aria-label='Stop']").count() and not pg.locator("[data-talvt-turn-state='complete']").count():
                streaming = 1
        if txt == last and not streaming:
            same += 1
        else:
            same = 0
        last = txt
        # Stable text wins; if turn marked complete, one matching poll is enough after first sight
        if same >= 2:
            return txt
        if same >= 1 and pg.locator("[data-talvt-turn-state='complete']").count() and not streaming:
            return txt
    open("/tmp/gpt_dom.html", "w").write(pg.content())
    raise RuntimeError("timeout")
def _job_budget(job, now=None):
    """Queue waiting spends the total deadline; never keep working after the caller leaves."""
    now = time.time() if now is None else now
    return min(float(job["timeout"]), max(0, float(job.get("deadline") or now) - now - 5))

def retain(job):
    rid = job.get("request_id"); result = job.get("result")
    if not rid or not isinstance(result, dict): return
    with RESULTS_LOCK:
        RESULTS[rid] = dict(result=result, at=time.time())
        if len(RESULTS) > RESULT_MAX:
            for key in sorted(RESULTS, key=lambda item: RESULTS[item]["at"])[:len(RESULTS) - RESULT_MAX]:
                RESULTS.pop(key, None)
def lookup(request_id):
    if not request_id: return None
    now = time.time()
    with RESULTS_LOCK:
        row = RESULTS.get(request_id)
        if row and now - row["at"] > RESULT_TTL_S:
            RESULTS.pop(request_id, None); row = None
    if row: return dict(state="done", result=row["result"])
    with ADMISSION.lock:
        job = ADMISSION.active.get(request_id)
        if job is None:
            job = next((waiter for waiter in ADMISSION.waiters if waiter.get("request_id") == request_id), None)
    if job is not None: return dict(state="pending", job=job)
    return None
def _mark_delivery(kind):
    STATUS["last_delivery"] = kind
    STATUS["last_delivery_at"] = time.strftime("%Y-%m-%d %H:%M:%S WITA")
def worker():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True); os.chmod(PROFILE, 0o700)
    os.makedirs(IMG_ROOT, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, args=["--no-first-run", "--disable-blink-features=AutomationControlled"], viewport={"width": 1280, "height": 860})
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try: pg.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        except Exception: pass
        last = 0
        while True:
            try: job = Q.get(timeout=5)
            except queue.Empty:
                if time.time() - last > 120:
                    last = time.time()
                    try:   # gptrecycle_20261103
                        mb = renderer_mb(); STATUS["renderer_mb"] = mb
                        if should_recycle(mb, Q.qsize()) and not BUSY[0]: pg = _recycle(ctx, pg)
                    except Exception: pass
                    _save(state=_probe(pg))
                continue
            if job.get("kind") == "picktrace":
                try: job["out"].put(_pick_trace(pg))
                except Exception as x: job["out"].put(dict(ok=False, error=str(x)[:160]))
                continue
            if job.get("kind") == "pickset":
                try: job["out"].put(_pick_set(pg, job.get("want") or r"gpt[- ]?6"))
                except Exception as x: job["out"].put(dict(ok=False, error=str(x)[:160]))
                continue
            if job.get("kind") == "pickdebug":   # gptpick_20261103 (B20a): dump the composer model menu (items, roles, submenus) + screenshots so the strongest model can be picked for real
                try: job["out"].put(_pick_debug(pg))
                except Exception as x: job["out"].put(dict(ok=False, error=str(x)[:160]))
                continue
            if job.get("kind") == "shot":   # fablefirst_20261103 debug aid: screenshot of the live page (never the prompt text)
                try: pg.screenshot(path=job.get("path") or "/tmp/gpt_shot.png"); job["out"].put(dict(ok=True, url=pg.url, state=_probe(pg), path=job.get("path") or "/tmp/gpt_shot.png"))
                except Exception as x: job["out"].put(dict(ok=False, error=str(x)[:120]))
                continue
            budget = _job_budget(job)
            if budget < 60 or not ADMISSION.start(job):
                ADMISSION.finish(job, dict(ok=False, error="expired in queue; nothing submitted"))
                _receipt("finished", job, ok=False, reason=job["result"]["error"])
                _request_finished(job)
                continue
            BUSY[0] = True
            try: r = dict(ok=True, text=_ask(pg, job["prompt"], budget, job.get("images")), model=STATUS.get("model_pick"), model_options=STATUS.get("model_options"))
            except Exception as x: r = dict(ok=False, error=str(x)[:120]); _save(state=_probe(pg))
            finally: BUSY[0] = False
            ADMISSION.finish(job, r); retain(job)
            r = job["result"]
            _request_finished(job)
            _receipt("finished", job, ok=bool(r.get("ok")), reason=r.get("error"))
            _save(state=STATUS.get("state"), last_ask_ok=bool(r.get("ok")), last_ask=time.strftime("%Y-%m-%d %H:%M:%S WITA"), last_err=None if r.get("ok") else r.get("error"))
            try:   # gptrecycle_20261103: answer delivered first, then a fresh tab if this one got too big and nobody is waiting
                mb = renderer_mb(); STATUS["renderer_mb"] = mb
                if should_recycle(mb, Q.qsize()): pg = _recycle(ctx, pg)
            except Exception: pass
def deliver(handler, job, cancel_on_timeout):
    ready = False
    try: ready = job["done"].wait(timeout=max(0, float(job.get("deadline") or 0) - time.time()) + 1)
    except Exception: ready = False
    if ready:
        retain(job)
        try: wrote = handler._j(200, job.get("result") or {})
        except (BrokenPipeError, ConnectionResetError): wrote = False
        _mark_delivery("delivered" if wrote is not False else "client_disconnected")
        return
    if cancel_on_timeout:
        ADMISSION.cancel(job); _mark_delivery("timeout")
        try: handler._j(504, dict(ok=False, error="timeout; request cancelled", request_id=job.get("request_id"), state="timeout"))
        except (BrokenPipeError, ConnectionResetError): _mark_delivery("client_disconnected")
    else:
        try: handler._j(504, dict(ok=False, error="timeout; original request still running", request_id=job.get("request_id"), state="pending"))
        except (BrokenPipeError, ConnectionResetError): _mark_delivery("client_disconnected")
class H(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def _j(s, c, d):
        body = json.dumps(d).encode()
        try:
            s.send_response(c); s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", str(len(body))); s.end_headers(); s.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            _mark_delivery("client_disconnected")
            return False
        return True
    def do_GET(s):
        if s.path.startswith("/picktrace"):
            o = queue.Queue(1); Q.put(dict(kind="picktrace", out=o))
            try: return s._j(200, o.get(timeout=150))
            except queue.Empty: return s._j(504, dict(ok=False, error="picktrace timeout"))
        if s.path.startswith("/pickset"):   # gptpick_20261103: one-off selection test (local only)
            o = queue.Queue(1); Q.put(dict(kind="pickset", out=o, want=(parse_qs(urlparse(s.path).query).get("want") or [r"gpt[- ]?6"])[0]))
            try: return s._j(200, o.get(timeout=120))
            except queue.Empty: return s._j(504, dict(ok=False, error="pickset timeout"))
        if s.path.startswith("/pickdebug"):   # gptpick_20261103: dump the model menu (debug; local only)
            o = queue.Queue(1); Q.put(dict(kind="pickdebug", out=o))
            try: return s._j(200, o.get(timeout=90))
            except queue.Empty: return s._j(504, dict(ok=False, error="pickdebug timeout"))
        if s.path.startswith("/shot"):   # fablefirst_20261103: GET /shot -> /tmp/gpt_shot.png (debug; tailnet-local)
            o = queue.Queue(1); Q.put(dict(kind="shot", out=o, path="/tmp/gpt_shot.png"))
            try: return s._j(200, o.get(timeout=60))
            except queue.Empty: return s._j(504, dict(ok=False, error="shot timeout"))
        if s.path.startswith("/result"):
            from urllib.parse import urlparse, parse_qs
            rid = (parse_qs(urlparse(s.path).query).get("request_id") or [""])[0]
            found = lookup(rid)
            if not found: return s._j(404, dict(ok=False, state="unknown", request_id=rid))
            if found["state"] == "pending":
                pending = found["job"]
                return s._j(202, dict(ok=False, state="pending", request_id=rid, accepted_at=pending.get("accepted_at"), deadline=pending.get("deadline")))
            return s._j(200, dict(found["result"], state="done"))
        admission = ADMISSION.snapshot()
        s._j(200, dict(STATUS, queue=Q.qsize(), busy=bool(BUSY[0] or admission["inflight"]),
                       inflight=admission["running"], admitted=admission["inflight"], admission=admission))
    def do_POST(s):
        try:
            size = int(s.headers.get("Content-Length") or 0)
            if size < 0 or size > 1048576: return s._j(413, dict(ok=False, error="request too large"))
            b = json.loads(s.rfile.read(size) or b"{}")
            if not isinstance(b, dict): raise ValueError("expected an object")
            prompt = b.get("prompt", "")
            if not isinstance(prompt, str): raise ValueError("prompt must be text")
            if len(prompt) > 128000: return s._j(413, dict(ok=False, error="prompt too large; nothing submitted"))
            to = max(60, min(int(b.get("timeout", 120)), 600))
            if not isinstance(b.get("wait_for_slot", False), bool): raise ValueError("wait_for_slot must be boolean")
            if not isinstance(b.get("skip_if_busy", False), bool): raise ValueError("skip_if_busy must be boolean")
            if not isinstance(b.get("ack", False), bool): raise ValueError("ack must be boolean")
            seat = b.get("seat") or None
            if seat is not None and (not isinstance(seat, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", seat)):
                raise ValueError("invalid seat")
            request_id = b.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)):
                raise ValueError("invalid request id")
            workload = b.get("workload") or "other"
            if workload not in ("trader", "chat", "learning", "other"): raise ValueError("invalid workload")
            deadline = float(b.get("deadline") or time.time() + to + 45)
            if not math.isfinite(deadline): raise ValueError("invalid deadline")
            deadline = min(deadline, time.time() + to + 45)
        except (ValueError, TypeError): return s._j(400, dict(ok=False, error="invalid request"))
        imgs = b.get("images") or []
        if not isinstance(imgs, list): imgs = []
        job = dict(prompt=prompt, timeout=to, images=imgs[:8], deadline=deadline,
                   t_in=time.time(), seat=seat, workload=workload)
        if request_id: job["request_id"] = request_id
        found = lookup(request_id) if request_id else None
        if found and found["state"] == "done":
            return s._j(200, dict(found["result"], state="done"))
        if found and found["state"] == "pending":
            return deliver(s, found["job"], False)
        if not _admit(job, b.get("skip_if_busy", False), b.get("wait_for_slot", False)):
            return s._j(409, dict(ok=False, error="chatgpt busy (%s); nothing submitted" % job.get("admission_error"),
                                  request_id=job.get("request_id")))
        if b.get("ack") is True:
            return s._j(202, dict(ok=True, accepted=True, state="pending", request_id=job["request_id"]))
        try:
            deliver(s, job, True)
        finally:
            if not job["done"].is_set(): ADMISSION.cancel(job)
            _request_finished(job)

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 18891), H).serve_forever()
