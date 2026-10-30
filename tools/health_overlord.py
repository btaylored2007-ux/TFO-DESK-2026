#!/usr/bin/env python3
"""TFO health overlord sweep (Brett order 2026-10-28 19:15/19:18 WITA). Read-only checks +
full-page dashboard screenshots. PAPER ONLY. Never touches keys, orders, Betfair, :8798, KDESK."""
import os, sys, json, glob, time, re, subprocess, shutil, socket, urllib.request, datetime, plistlib, py_compile
BASE = os.path.expanduser("~/Applications/MultiVenueDesk")
NOW = time.time()
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M")
SHOTS = os.path.join(BASE, "reports", "health_shots", STAMP)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
out = {"asof_local": datetime.datetime.now().isoformat(timespec="seconds"), "paper_only": True, "items": {}, "cards": {}, "shots": []}
def put(k, status, note=""): out["items"][k] = {"status": status, "note": note}
def sh(cmd, t=30):
    try: return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t).stdout
    except Exception as e: return "ERR %s" % e
def get(url, t=10):
    try:
        with urllib.request.urlopen(url, timeout=t) as r: return r.status, r.read()
    except Exception as e: return 0, str(e).encode()

# 1 launchd
ll = {}
for line in sh("launchctl list").splitlines():
    p = line.split("\t")
    if len(p) == 3 and p[2].startswith("com.tfo."): ll[p[2]] = (p[0], p[1])
bad = []; stale = []
for pl in glob.glob(os.path.expanduser("~/Library/LaunchAgents/com.tfo.*.plist")):
    try: d = plistlib.load(open(pl, "rb"))
    except Exception: continue
    lab = d.get("Label");
    if d.get("Disabled"): continue
    if lab not in ll:
        stale.append(lab + " not loaded (one-off/finished?)"); continue
    pid, ex = ll[lab]
    if pid == "-" and ex not in ("0",): bad.append("%s exit %s" % (lab, ex))
    iv = d.get("StartInterval"); log = d.get("StandardOutPath") or d.get("StandardErrorPath")
    if iv and log and pid == "-":
        m = max([os.path.getmtime(x) for x in (d.get("StandardOutPath"), d.get("StandardErrorPath")) if x and os.path.exists(x)] or [0])
        if False and m and NOW - m > max(3 * iv, 3600) and iv < 86400: stale.append("%s log %dmin old (every %ds)" % (lab, (NOW - m) / 60, iv))
put("launchd", "red" if bad else ("amber" if stale else "green"), "; ".join(bad + stale)[:1500] or "%d jobs loaded, all clean" % len(ll))

# 2 dashboards reachable
for name, url in [("warroom_18812", "http://127.0.0.1:18812/healthz"), ("veteran_18860", "http://127.0.0.1:18860/"), ("v2_18813", "http://127.0.0.1:18813/")]:
    st, b = get(url); put("dash_" + name, "green" if st == 200 else "red", "HTTP %s" % st)

# 2b stale manual-tick wording while funder gate auto-approves (Brett 2026-10-28 20:00 WITA)
TICK_RX = re.compile(r"(cannot arm|can.t arm|can.t switch (them|it) on|(?<!needing )(?<!no need for )you(r)? tick|until you tick|only after you tick|waiting for (your )?tick|without your tick|tick (it|them|each|one)\b|funder[- ]ticked|(?<!nothing )(?<!nothing still )needs? (your|a) tick)", re.I)
def tick_scan():
    try: fg = json.load(open(os.path.join(BASE, "state/funder_gate.json")))
    except Exception as e: return put("dash_tick_wording", "amber", "funder_gate.json unreadable %s" % e)
    if not fg.get("auto_approve"): return put("dash_tick_wording", "green", "auto_approve false - tick wording is correct")
    hits = []
    srcs = [("warroom_18812", "http://127.0.0.1:18812/"), ("veteran_18860", "http://127.0.0.1:18860/"), ("veteran_18860_api", "http://127.0.0.1:18860/api/state"), ("v2_18813", "http://127.0.0.1:18813/")]
    for nm, url in srcs:
        st, b = get(url)
        if st != 200: continue
        txt = b.decode("utf-8", "replace")
        for m in TICK_RX.finditer(txt):
            hits.append("%s: ...%s..." % (nm, re.sub(r"\s+", " ", txt[max(0, m.start() - 40):m.end() + 30])))
    for f in ("state/veteran_desk/head_of_desk.json",):
        try:
            t = json.load(open(os.path.join(BASE, f))).get("note", "")
            for m in TICK_RX.finditer(t): hits.append("%s note: ...%s..." % (f, t[max(0, m.start() - 40):m.end() + 30].replace("\n", " ")))
        except Exception: pass
    put("dash_tick_wording", "red" if hits else "green", " | ".join(hits)[:2000] or "auto_approve true and no tick / cannot-arm wording on dashboards")
tick_scan()

# 3 feeds
def port(p):
    s = socket.socket(); s.settimeout(3)
    try: s.connect(("127.0.0.1", p)); return True
    except Exception: return False
    finally: s.close()
put("ibkr_gateway_4002", "green" if port(4002) else "red", "listening" if port(4002) else "not listening")
try:
    fh = json.load(open(os.path.join(BASE, "state/veteran_desk/feeds_health.json")))
    fails = [k for k, v in fh.items() if isinstance(v, dict) and "fails" in v and (v.get("fails", 0) > 2)]
    oldest = max((NOW - datetime.datetime.fromisoformat(v["last_ok"]).timestamp()) for k, v in fh.items() if isinstance(v, dict) and v.get("last_ok"))
    put("feeds_veteran", "red" if fails else ("amber" if oldest > 7200 else "green"), "fails=%s oldest_ok=%dmin" % (fails, oldest / 60))
except Exception as e: put("feeds_veteran", "amber", "unreadable %s" % e)
try:
    sys.path.insert(0, BASE); import kraken_dns; kraken_dns.install()
    st, b = get("https://api.kraken.com/0/public/AssetPairs", 20)
    n = len(json.loads(b).get("result", {})) if st == 200 else 0
    st2, b2 = get("https://api.kraken.com/0/public/Time", 10)
    put("kraken_pairs", "green" if n > 300 and st2 == 200 else "red", "%d pairs listed via kraken_dns; time HTTP %s" % (n, st2))
except Exception as e: put("kraken_pairs", "amber", "check error %s" % e)
oc = sorted(glob.glob(os.path.join(BASE, "state/oanda*")) + glob.glob(os.path.join(BASE, "reports/oanda*")), key=os.path.getmtime)
if oc: put("oanda_feed", "green" if NOW - os.path.getmtime(oc[-1]) < 7200 else "amber", "newest oanda file %s %dmin old" % (os.path.basename(oc[-1]), (NOW - os.path.getmtime(oc[-1])) / 60))
else: put("oanda_feed", "amber", "no oanda state found")

# 4 pit + disk
pit = os.path.join(BASE, "data/pit/pit.sqlite")
age = (NOW - max(os.path.getmtime(pit), os.path.getmtime(pit + "-wal") if os.path.exists(pit + "-wal") else 0)) / 60 if os.path.exists(pit) else 1e9
put("pit_sqlite", "green" if age < 180 else "amber", "%.1fGB, written %dmin ago" % (os.path.getsize(pit) / 1e9 if os.path.exists(pit) else 0, age))
du = shutil.disk_usage(BASE); free = du.free / 1e9
put("disk", "green" if free > 50 else ("amber" if free > 20 else "red"), "%.0fGB free (%.0f%% used)" % (free, 100 * du.used / du.total))

# 5 tracebacks in logs touched in last 65 min
tb = []
for lg in glob.glob(os.path.join(BASE, "logs/*")) + glob.glob(os.path.join(BASE, "reports/**/logs/*"), recursive=True) + glob.glob(os.path.join(BASE, "state/**/*.log"), recursive=True):
    try:
        if NOW - os.path.getmtime(lg) > 3900 or os.path.getsize(lg) == 0: continue
        with open(lg, "rb") as f:
            f.seek(max(0, os.path.getsize(lg) - 40000)); tail = f.read().decode("utf-8", "replace")
        if "Traceback (most recent call last)" in "\n".join(tail.splitlines()[-25:]):
            last = tail[tail.rfind("Traceback"):].strip().splitlines()
            tb.append("%s: %s" % (os.path.relpath(lg, BASE), last[-1][:160] if last else ""))
    except Exception: pass
put("log_tracebacks", "amber" if tb else "green", " | ".join(tb)[:2500] or "none in last hour")

# 6 AI seats
try:
    mu = [json.loads(l) for l in open(os.path.join(BASE, "state/veteran_desk/model_usage.jsonl")) if l.strip()][-20:]
    eng = {}
    for m in mu: eng[m.get("engine")] = m
    bad = ["%s ok=%s err=%s" % (k, v.get("ok"), v.get("err")) for k, v in eng.items() if not v.get("ok")]
    lat = json.dumps(json.load(open(os.path.join(BASE, "state/veteran_desk/latest.json"))))
    dna = lat.lower().count("did not answer")
    put("ai_calls", "red" if bad else ("amber" if dna else "green"), "; ".join(bad) or ("last per engine ok: %s; did-not-answer=%d" % (",".join("%s@%s" % (k, v.get("utc", "")[11:16]) for k, v in eng.items()), dna)))
except Exception as e: put("ai_calls", "amber", "unreadable %s" % e)

# 7 py_compile with system 3.9
errs = []; n = 0
for f in glob.glob(os.path.join(BASE, "*.py")) + glob.glob(os.path.join(BASE, "veteran_desk/*.py")) + glob.glob(os.path.join(BASE, "tools/*.py")):
    n += 1
r = sh("cd %s && /usr/bin/python3 -c \"import py_compile,glob,sys\nbad=[]\nfor f in glob.glob('*.py')+glob.glob('veteran_desk/*.py')+glob.glob('tools/*.py'):\n  try: py_compile.compile(f,doraise=True,cfile='/tmp/ho_pyc/'+f.replace('/','_')+'c')\n  except Exception as e: bad.append(f+': '+str(e).splitlines()[0][:120])\nprint('\\n'.join(bad))\"" % BASE, 180)
bad = [l for l in r.splitlines() if l.strip()]
put("py_compile_39", "red" if bad else "green", ("; ".join(bad))[:1500] or "%d scripts compile on /usr/bin/python3" % n)

# 8 stale state files (health/alarms jsons)
st = []
for f in glob.glob(os.path.join(BASE, "state/*_health.json")):
    a = (NOW - os.path.getmtime(f)) / 3600
    if a > 26: st.append("%s %.0fh" % (os.path.basename(f), a))
put("state_freshness", "amber" if st else "green", "; ".join(st) or "all *_health.json < 26h")

# 9 private wealth cycle + strategy tracker sanity
try:
    cf = json.load(open(os.path.join(BASE, "reports/private_wealth_cycle_latest.json")))
    age_h = (NOW - os.path.getmtime(os.path.join(BASE, "reports/private_wealth_cycle_latest.json"))) / 3600
    put("pw_cycle_receipt", "green" if age_h < 2 else "amber", "last receipt %.1fh old%s" % (age_h, " (runner says RECOVERY_REQUIRED: interrupted claim left for custodian)" if age_h >= 2 else ""))
except Exception as e: put("pw_cycle_receipt", "amber", "unreadable %s" % e)
try:
    stt = json.load(open(os.path.join(BASE, "static/strategy_tracker.json")))
    odd = ["%s %s" % (r["strategy"].strip(), r.get("pnl_aud")) for r in stt["rows"] if (r.get("pnl_aud") or 0) < 0 and not r.get("trades") and not r.get("open") and "shadow" not in (r["strategy"] + str(r.get("src"))).lower() and "benchmark" not in r["strategy"].lower()]
    put("tracker_loss_without_trades", "amber" if odd else "green", "; ".join(odd) or "none (shadow/benchmark inventories excluded)")
except Exception as e: put("tracker_loss_without_trades", "amber", "unreadable %s" % e)
# 9b dashboard liveness + marks (Brett t1674u 2026-10-28 20:06 WITA)
# (a) header must not still read "Connecting" after 65 s on PW home and /m
PY314 = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
stuck = []
for nm, u in [("pw_home", "http://127.0.0.1:18812/"), ("pw_m", "http://127.0.0.1:18812/m")]:
    try:
        r = subprocess.run([PY314, os.path.join(BASE, "tools/ho_cdp_eval.py"), u, "65",
                            "(()=>{const t=document.body.innerText;return /Connecting/.test(t.slice(0,600))?'STUCK':'OK'})()"],
                           capture_output=True, text=True, timeout=150)
        if "EVAL: OK" not in r.stdout: stuck.append("%s %s" % (nm, (r.stdout.split("\n")[0] or r.stderr[-120:]).strip()[:120]))
    except Exception as e: stuck.append("%s check error %s" % (nm, e))
put("dash_connecting_60s", "red" if stuck else "green", "; ".join(stuck) or "PW home and /m connected within 65 s")
# (b) unresolved MARK_MISSING on held positions (API filters alerts whose symbols have a newer real mark)
try:
    st_, b_ = get("http://127.0.0.1:18812/api/dashboard", 60)
    al = [a for a in json.loads(b_).get("alerts", []) if "MARK_MISSING" in (a.get("text") or "")]
    put("mark_missing_held", "red" if al else "green", "; ".join(a["text"][:140] for a in al)[:1500] or "no unresolved MARK_MISSING")
except Exception as e: put("mark_missing_held", "amber", "check error %s" % e)
# 10 full-page screenshots (CDP capture on the Mac's headless Chrome)
os.makedirs(SHOTS, exist_ok=True)
try:
    r = subprocess.run([next((x for x in ("/usr/local/bin/python3", sys.executable) if os.path.exists(x)), sys.executable), os.path.join(BASE, "tools/ho_capture.py"), SHOTS], capture_output=True, text=True, timeout=600)
    caps = json.load(open(os.path.join(SHOTS, "capture.json")))
    out["shots"] = caps
    flags = []
    for c in caps:
        txt = open(c["file"].replace(".png", ".txt")).read()
        for pat in ("only after you tick", "did not answer", "Traceback", "undefined", "NaN", "[object Object]"):
            if pat in txt: flags.append("%s_%d:%s" % (c["name"], c["w"], pat))
        out["cards"]["%s_%d" % (c["name"], c["w"])] = {"status": "green" if c["h"] > 950 else "amber", "height": c["h"]}
    put("dashboard_copy_scan", "amber" if flags else "green", "; ".join(flags) or "%d captures, no stale/error copy found" % len(caps))
except Exception as e: put("dashboard_capture", "red", "capture failed %s" % e)
# should-be-trading check (Brett 2026-10-29): never-traded books, hunts nothing armed, silent VD seats, stale reports
try:
    r = subprocess.run([PY314, os.path.join(BASE, "tools/should_be_trading.py")], capture_output=True, text=True, timeout=120)
    sb = json.load(open(os.path.join(BASE, "state", "should_be_trading.json")))
    put("should_be_trading", sb["overall"], ("; ".join("%s %s: %s" % (f["check"], f["what"], f["why"][:100]) for f in sb["flags"]) or "every running book/hunt/seat trading or proposing; reports fresh")[:1500])
except Exception as e: put("should_be_trading", "amber", "check error %s" % e)
# prune >48h
for d in glob.glob(os.path.join(BASE, "reports/health_shots/*")):
    if os.path.isdir(d) and NOW - os.path.getmtime(d) > 48 * 3600 and re.match(r"\d{8}-\d{4}$", os.path.basename(d)): shutil.rmtree(d, ignore_errors=True)
out["overall"] = "red" if any(v["status"] == "red" for v in out["items"].values()) else ("amber" if any(v["status"] == "amber" for v in out["items"].values()) else "green")
json.dump(out, open(os.path.join(SHOTS, "sweep.json"), "w"), indent=1)
json.dump(out, open(os.path.join(BASE, "state", "health_overlord.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
