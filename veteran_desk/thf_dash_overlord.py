"""Taylor Hedge Fund DASH OVERLORD (launchd com.tfo.vd-dash-overlord, every 300s). Verifies every dashboard figure against source files.
Mismatch/stale -> state/vd_engine/dash_overlord.json (shown red on the dash) + logs/vd_dash_overlord.log. Repairs: delete dash cache, kickstart dash. NEVER edits trading data."""
import datetime as dt, glob, json, os, subprocess, time, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); ST = os.path.join(BASE, "state", "vd_engine"); OUT = os.path.join(ST, "dash_overlord.json")
def rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def api():
    return json.loads(urllib.request.urlopen("http://127.0.0.1:18870/api/state", timeout=30).read())
def check(A):
    mm = []; now = dt.datetime.now(dt.timezone.utc)
    def age(u):
        try: return (now - dt.datetime.fromisoformat(u)).total_seconds() / 60
        except Exception: return 999
    if age(A.get("built_utc")) > 2: mm.append("dash data is %.0f min old (cache stuck)" % age(A.get("built_utc")))
    book = rj(os.path.join(BASE, "state", "thf", "thf_book.json"), {}) or {}
    for k in ("today_pnl_aud", "equity_aud", "pnl_since_inception_aud"):
        a, b = (A.get("today") or {}).get(k), book.get(k)
        if a is None or b is None or abs(float(a) - float(b)) > 1: mm.append("today.%s dash=%s book=%s" % (k, a, b))
    if age(book.get("updated")) > 30: mm.append("book itself not updated for %.0f min (source stale)" % age(book.get("updated")))
    lb = A.get("leaderboard") or {}
    rows = {r["seat"]: r for r in lb.get("rows", [])} if isinstance(lb, dict) else {}
    for s, B in (book.get("seats") or {}).items():
        if s in rows and abs(float(rows[s]["equity_aud"]) - float(B.get("equity_aud") or 0)) > 50: mm.append("leaderboard %s equity %s vs book %s" % (s, rows[s]["equity_aud"], B.get("equity_aud")))
    cards = {c["seat"]: c for c in A.get("cards", [])}
    for f in glob.glob(os.path.join(ST, "top10", "*.json")):
        s = os.path.basename(f)[:-5]; src = (rj(f, {}) or {}).get("top10", []); c = cards.get(s)
        if not c: mm.append("seat %s missing from dash" % s); continue
        if [x["sym"] for x in src] != [x["sym"] for x in c.get("top10", [])]: mm.append("top10 %s differs from source" % s)
        if age((rj(f, {}) or {}).get("utc")) > 10: mm.append("top10 %s stale %.0f min" % (s, age((rj(f, {}) or {}).get("utc"))))
    cov = rj(os.path.join(ST, "coverage.json"), {}) or {}
    if (A.get("coverage") or {}).get("totals") != cov.get("totals"): mm.append("coverage totals differ from source")
    hb = rj(os.path.join(ST, "hb", "engine.json"), {}) or {}
    if age(hb.get("utc")) > 5: mm.append("engine heartbeat %.0f min old" % age(hb.get("utc")))
    dm = rj(os.path.join(ST, "deadman_health.json"), {}) or {}
    if age(dm.get("utc")) > 6: mm.append("deadman health %.0f min old" % age(dm.get("utc")))
    return mm
def main():
    t = dt.datetime.now(dt.timezone.utc); rep = []; mm = []
    try: mm = check(api())
    except Exception as e: mm = ["dash API unreachable: %s" % type(e).__name__]
    if mm and any(("cache" in m or "differs" in m or "unreachable" in m or "dash=" in m or "missing" in m) for m in mm):
        try: os.remove(os.path.join(ST, "dash_cache.json")); rep.append("deleted dash cache")
        except FileNotFoundError: pass
        try: mm2 = check(api())
        except Exception as e: mm2 = ["dash API unreachable: %s" % type(e).__name__]
        if any("unreachable" in m for m in mm2):
            r = subprocess.run(["launchctl", "kickstart", "-k", "gui/%d/com.tfo.vd-engine-dash" % os.getuid()], capture_output=True, text=True); rep.append("kickstarted dash rc=%d" % r.returncode)
            time.sleep(4)
            try: mm2 = check(api())
            except Exception as e: mm2 = ["dash API unreachable after restart: %s" % type(e).__name__]
        mm = mm2
    o = dict(utc=t.isoformat(), wita=(t + dt.timedelta(hours=8)).strftime("%F %T WITA"), state="OK" if not mm else "MISMATCH", mismatches=mm, repairs=rep, rule="never edits trading data")
    tmp = OUT + ".tmp"; json.dump(o, open(tmp, "w"), indent=1); os.replace(tmp, OUT)
    with open(os.path.join(BASE, "logs", "vd_dash_overlord.log"), "a") as f: f.write(json.dumps(o) + "\n")
if __name__ == "__main__": main()
