#!/usr/bin/env python3
"""HUNT48 CROSS-MARKET STAGING (Brett 28 Oct 2026 13:40 WITA approve-all #3). PAPER ONLY. STAGE ONLY.

Each hunt book (asx jp hk sg kr tw lse) may STAGE setups from OTHER markets: BENCH/ARMED-pending setups that already passed
their home market's review + levels (+3%/-8%, B1, room/cost gates) are copied into state/hunt48_<host>/xstaged.json as
XSTAGED_WATCH rows. They are written to a SEPARATE file (never into any ledger's trades list), so no engine loop arms or
fills them. Unchanged and enforced here: per-market caps (a source market already at MAX_OPEN live trades stages nothing
elsewhere; a host carries at most MAX_OPEN cross-stages per source market), MAX_OPEN 3, A$2,500 risk per trade (rows with
higher risk are skipped), funder tick required (needs_funder_tick=True, auto_arm=False). Execution of a ticked cross-stage
still goes through the HOME market book's session/fill path; this file never orders anything."""
import datetime as dt, json, os
# 2026-10-28 Brett B3: hunt scripts honour the desk base path (env DESK_BASE, default = this folder)
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
BOOKS = ("asx", "jp", "hk", "sg", "kr", "tw", "lse")
MAX_OPEN = 3; RISK_AUD = 2500.0; MAX_PER_HOST = 12
LIVE = ("ARMED", "FILLED", "awaiting_funder")
CFG = os.path.join(BASE, "state", "hunt_cross_market.json")
KEEP = ("sym", "direction", "trigger", "max_fill", "stop", "t1", "t2", "shares", "risk_aud", "risk_aud_actual", "notional_aud",
        "ccy", "sector", "score", "armed_utc", "armed_wita", "cost_R", "room_R")

def enabled(base=BASE):
    try: return bool(json.load(open(os.path.join(base, "state", "hunt_cross_market.json"))).get("enabled"))
    except Exception: return False

def _ledger(m, base):
    try: return json.load(open(os.path.join(base, "state", "hunt48_%s" % m, "ledger.json")))
    except Exception: return {"trades": []}

def build(base=BASE, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    led = {m: _ledger(m, base) for m in BOOKS}
    live = {m: sum(t.get("status") in LIVE for t in led[m]["trades"]) for m in BOOKS}
    out = {}
    for host in BOOKS:
        own = {t["sym"] for t in led[host]["trades"] if t.get("status") in LIVE + ("BENCH",)}
        rows, per_src = [], {}
        for src in BOOKS:
            if src == host: continue
            if live[src] >= MAX_OPEN: continue            # per-market cap: source market full -> stages nothing elsewhere
            cands = [t for t in led[src]["trades"] if t.get("status") in ("BENCH", "ARMED") and t.get("armed_utc")
                     and dt.datetime.fromisoformat(t["armed_utc"]) > now - dt.timedelta(hours=24)
                     and not t.get("demoted_utc") and t["sym"] not in own]
            for t in sorted(cands, key=lambda t: -(t.get("score") or 0)):
                if per_src.get(src, 0) >= MAX_OPEN or len(rows) >= MAX_PER_HOST: break
                if float(t.get("risk_aud") or 0) > RISK_AUD + 1e-6 or float(t.get("risk_aud_actual") or 0) > RISK_AUD + 1e-6: continue
                rows.append(dict({k: t.get(k) for k in KEEP}, host_book=host, src_market=src, src_id=t.get("id"), src_status=t.get("status"),
                                 status="XSTAGED_WATCH", needs_funder_tick=True, auto_arm=False, paper_only=True,
                                 note="cross-market stage only; arms only via funder tick through the %s book's session/fill path" % src.upper()))
                per_src[src] = per_src.get(src, 0) + 1
        out[host] = dict(book="HUNT48_%s_XSTAGED" % host.upper(), paper_only=True, stage_only=True, max_open=MAX_OPEN,
                         risk_per_trade_aud=RISK_AUD, host_live=live[host], updated_utc=now.isoformat(timespec="seconds"),
                         authority="Brett 28 Oct 13:40 approve-all #3", rows=rows)
    return out

def run(base=BASE, now=None):
    if not enabled(base): return {}
    out = build(base, now)
    for host, o in out.items():
        p = os.path.join(base, "state", "hunt48_%s" % host, "xstaged.json"); tmp = p + ".tmp"
        json.dump(o, open(tmp, "w"), indent=1); os.replace(tmp, p)
    return out

if __name__ == "__main__":
    o = run()
    print("cross-market staging %s: " % ("ON" if o else "OFF") + ", ".join("%s %d" % (h, len(v["rows"])) for h, v in o.items()))
