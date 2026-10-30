#!/usr/bin/env python3
"""Health-overlord full-page capture via the Mac's own headless Chrome (CDP, local only).
Usage: ho_capture.py OUTDIR. Captures each page/tab at 1440 and 430 widths, full page."""
import sys, os, json, time, base64, subprocess, tempfile, shutil, urllib.request, websocket
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9339
# reap orphaned capture Chromes from earlier runs (only our own ho_chrome_ profiles) so the port is free
subprocess.run(["pkill", "-f", "--", "remote-debugging-port=%d .*ho_chrome_" % PORT], capture_output=True); time.sleep(1)
ud = tempfile.mkdtemp(prefix="ho_chrome_")
proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=%d" % PORT,
                         "--remote-allow-origins=*", "--user-data-dir=" + ud, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try: tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2)); break
        except Exception: time.sleep(0.3)
    page = [t for t in tabs if t.get("type") == "page"][0]
    WS = [websocket.create_connection(page["webSocketDebuggerUrl"], timeout=120)]
    n = [0]
    def cmd(m, **p):
        n[0] += 1; i = n[0]; WS[0].send(json.dumps({"id": i, "method": m, "params": p}))
        while True:
            r = json.loads(WS[0].recv())
            if r.get("id") == i: return r.get("result", {})
    def reconnect():
        # renderer crashed (very tall page): open a fresh tab and carry on (health fix 2026-10-28 20:3x)
        try: WS[0].close()
        except Exception: pass
        t = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/json/new?about:blank" % PORT, method="PUT"), timeout=10))
        WS[0] = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=120)
        cmd("Page.enable"); cmd("Runtime.enable")
    def ev(js): return cmd("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True).get("result", {}).get("value")
    cmd("Page.enable"); cmd("Runtime.enable")
    errors = []
    def shot(name, w):
        mobile = w < 800
        cmd("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=mobile)
        time.sleep(1.5)
        h = ev("Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)") or 900
        # pages with inner scroll panes: also measure tallest scrollable element
        inner = ev("Math.max.apply(null,[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);return /(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+50}).map(e=>e.scrollHeight).concat([0]))") or 0
        H = int(min(max(h, inner, 900), 40000))
        cmd("Emulation.setDeviceMetricsOverride", width=w, height=H, deviceScaleFactor=1, mobile=mobile)
        time.sleep(1.0)
        p = os.path.join(OUT, "%s_%d.png" % (name, w)); parts = []
        SEG = 8000
        for k, off in enumerate(range(0, H, SEG)):
            r = cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                    clip={"x": 0, "y": off, "width": w, "height": min(SEG, H - off), "scale": 1})
            pp = p if k == 0 else p.replace(".png", "_p%d.png" % (k + 1))
            open(pp, "wb").write(base64.b64decode(r["data"])); parts.append(pp)
        txt = ev("document.body ? document.body.innerText : ''") or ""
        open(p.replace(".png", ".txt"), "w").write(txt)
        return {"name": name, "w": w, "h": H, "file": p, "parts": parts}
    def safe_shot(name, w):
        try: return shot(name, w)
        except Exception as e:
            errors.append("%s_%d: %s" % (name, w, e))
            try: reconnect()
            except Exception as e2: errors.append("reconnect: %s" % e2)
            return None
    res = []
    def load(url, wait=12):
        cmd("Page.navigate", url=url); time.sleep(wait)
        # wait (up to 60 s more) until the page has left its Connecting/Loading state
        for _ in range(30):
            try:
                busy = ev("(()=>{const t=document.body?document.body.innerText:'';return /Connecting\\.\\.\\.|Loading interactive data|Reconnecting\\.\\.\\./.test(t)})()")
            except Exception: busy = False
            if not busy: break
            time.sleep(2)
    pages = [("warroom_home", "http://127.0.0.1:18812/", None), ("warroom_m", "http://127.0.0.1:18812/m", None),
             ("veteran", "http://127.0.0.1:18860/", None), ("v2", "http://127.0.0.1:18813/", None)]
    for name, url, _ in pages:
        for w in (1440, 430):
            try: load(url)
            except Exception as e:
                errors.append("load %s: %s" % (url, e))
                try: reconnect(); load(url)
                except Exception: continue
            r_ = safe_shot(name, w)
            if r_: res.append(r_)
    try: load("http://127.0.0.1:18812/warroom", 15)
    except Exception as e:
        errors.append("load warroom: %s" % e); reconnect(); load("http://127.0.0.1:18812/warroom", 15)
    tabsl = ev("[...document.querySelectorAll('#wctabs button')].map(b=>b.dataset.p)") or ["plan"]
    for t in tabsl:
        ev("(()=>{const b=document.querySelector('#wctabs button[data-p=\"%s\"]'); if(b) b.click(); window.scrollTo(0,0); return 1})()" % t)
        time.sleep(2)
        for w in (1440, 430):
            r_ = safe_shot("warroom_" + t, w)
            if r_: res.append(r_)
    json.dump(errors, open(os.path.join(OUT, "capture_errors.json"), "w"), indent=1)
    json.dump(res, open(os.path.join(OUT, "capture.json"), "w"), indent=1)
    print(json.dumps(res))
finally:
    proc.terminate()
    try: proc.wait(10)
    except Exception: proc.kill()
    # 2026-10-28 DASH 5: headless Chrome children can outlive the parent and keep port 9339, which broke the next
    # overlord capture (capture.json missing). Reap any process still using this run's private profile dir.
    subprocess.run(["pkill", "-f", "--", "--user-data-dir=" + ud], capture_output=True)
    shutil.rmtree(ud, ignore_errors=True)
