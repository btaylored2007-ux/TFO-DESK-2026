#!/usr/bin/env python3
"""tfo_v2_dashboard_watch.py — health watcher for the V2 dashboard (launchd every 300 s). PAPER ONLY.

Hits /healthz and /api/all on 127.0.0.1:18813. If the server does not answer, restarts it with
`launchctl kickstart -k gui/<uid>/com.tfo.v2-dashboard`. Writes reports/v2/tfo_v2_dashboard_health.json
(status OK | DEGRADED (data alerts) | DOWN, alerts, last_ok). Touches nothing else.
data_degraded lists setups with partial price downloads after retries: INFORMATIONAL, never changes status.
  --no-restart   report only (used for tests on non-Mac hosts)
"""
import datetime as dt, json, os, subprocess, sys, tempfile, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("DESK") or os.environ.get("BASE") or HERE
V2 = os.path.join(BASE, "reports", "v2"); OUT = os.path.join(V2, "tfo_v2_dashboard_health.json")
PORT = int(os.environ.get("TFO_V2_DASH_PORT", "18813")); URL = "http://127.0.0.1:%d" % PORT
LABEL = "com.tfo.v2-dashboard"


def get(path):
    try:
        with urllib.request.urlopen(URL + path, timeout=15) as r: return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, None
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, e)}


def main():
    t = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev = {}
    try: prev = json.load(open(OUT))
    except Exception: pass
    code, hz = get("/healthz"); acode, api = get("/api/all")
    rec = {"utc": t, "url": URL, "healthz_code": code, "api_code": acode, "restarts": prev.get("restarts", 0), "last_ok": prev.get("last_ok")}
    if code is None or acode is None:
        rec["status"] = "DOWN"; rec["alerts"] = [str((hz or {}).get("error") or (api or {}).get("error"))]
        if "--no-restart" not in sys.argv:
            cmd = ["launchctl", "kickstart", "-k", "gui/%d/%s" % (os.getuid(), LABEL)]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                rec["restart"] = {"cmd": " ".join(cmd), "rc": p.returncode, "stderr": p.stderr[-300:]}; rec["restarts"] += 1
            except Exception as e:
                rec["restart"] = {"cmd": " ".join(cmd), "error": str(e)}
    else:
        rec["alerts"] = [a["code"] + ": " + a["msg"] for a in (api or {}).get("alerts", [])]
        rec["status"] = "OK" if code == 200 else "DEGRADED"
        rec["freshness"] = (api or {}).get("freshness", {}).get("badge")
        rec["data_degraded"] = ["%s %s/%s failed (%s%%)" % (c.get("setup"), c.get("failed"), c.get("universe"), c.get("failed_share_pct"))
                                for c in (api or {}).get("data_coverage") or [] if c.get("status") == "degraded"]   # neutral, informational
        if code == 200: rec["last_ok"] = t
    os.makedirs(V2, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=V2, prefix=".dash_health", suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(rec, f, indent=1)
    os.replace(tmp, OUT)
    print(json.dumps({k: rec[k] for k in ("utc", "status", "healthz_code", "api_code")}))


if __name__ == "__main__":
    main()
