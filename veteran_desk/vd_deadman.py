"""Taylor Hedge Fund dead-man watchdog (launchd com.tfo.vd-deadman, StartInterval 120). Independent: stdlib only, no veteran_desk imports.
Heartbeat >5 min old -> launchctl kickstart -k gui/<uid>/com.tfo.vd-engine (max 3 per 30 min). >15 min -> DEGRADED. Writes state/vd_engine/deadman_health.json."""
import datetime as dt, json, os, subprocess, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); ST = os.path.join(BASE, "state", "vd_engine")
HB = os.path.join(ST, "hb", "engine.json"); OUT = os.path.join(ST, "deadman_health.json"); INC = os.path.join(ST, "deadman_incidents.json")
LABELS = {"engine": ("com.tfo.vd-engine", HB, 5, 15)}
def rj(p, d):
    try: return json.load(open(p))
    except Exception: return d
def wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"; json.dump(o, open(t, "w"), indent=1); os.replace(t, p)
def main():
    now = dt.datetime.now(dt.timezone.utc); I = rj(INC, {"restarts": []}); I["restarts"] = [r for r in I["restarts"] if time.time() - r["ts"] < 1800]
    hb = rj(HB, {}); 
    try: age = (now - dt.datetime.fromisoformat(hb["utc"])).total_seconds() / 60
    except Exception: age = 999
    st, why, act = "OK", "heartbeat %.1f min" % age, None
    if age > 5:
        if I["restarts"] and time.time() - I["restarts"][-1]["ts"] < 300: st = "WAITING"; why += "; restarted <5 min ago, giving it time to boot"
        elif len(I["restarts"]) < 3:
            try:
                r = subprocess.run(["launchctl", "kickstart", "-k", "gui/%d/com.tfo.vd-engine" % os.getuid()], capture_output=True, text=True, timeout=60); rc, err = r.returncode, r.stderr[-200:]
            except subprocess.TimeoutExpired: rc, err = "timeout", "kickstart still waiting after 60s"
            act = dict(ts=time.time(), utc=now.isoformat(), rc=rc, err=err, hb_age_min=round(age, 1)); I["restarts"].append(act); I.setdefault("history", []).append(act); I["history"] = I["history"][-50:]
            st = "RESTARTED"
        else: st = "DEGRADED"; why += "; restart budget (3/30min) exhausted"
        if age > 15: st = "DEGRADED"
    # dashboard liveness (report only; KeepAlive handles restarts)
    try:
        import urllib.request; dash = urllib.request.urlopen("http://127.0.0.1:18870/healthz", timeout=5).read().decode() == "ok"
    except Exception: dash = False
    wj(INC, I)
    wj(OUT, dict(utc=now.isoformat(), wita=(now + dt.timedelta(hours=8)).strftime("%F %T WITA"), state=st, why=why, hb_age_min=round(age, 1), engine_pid=hb.get("pid"), restarts_30m=len(I["restarts"]), last_action=act, dash_up=dash))
    if act or st != "OK":
        with open(os.path.join(BASE, "logs", "vd_deadman.log"), "a") as f: f.write("%s %s %s %s\n" % (now.isoformat(), st, why, act))
if __name__ == "__main__": main()
