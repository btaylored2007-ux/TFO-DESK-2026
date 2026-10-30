"""All TFO desks EXCEPT Veteran Desk -> one phone view (Brett 28 Oct 21:11 WITA). Read-only, PAPER.
Built on reports/strategy_scoreboard.json (tools/strategy_scoreboard.py). Never invents numbers: None stays None."""
import json, os, subprocess, sys, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOT_IN_TOTAL = ("TEST", "shadow", "Shadow", "benchmark", "└", "outside firm equity")   # replays/benchmarks/sub-sleeves: shown, not summed
def _armed(src):
    p = os.path.join(BASE, (src or "").split(" ")[0])
    if not p.endswith("ledger.json") or not os.path.exists(p): return None
    try: return sum(t.get("status") == "ARMED" for t in json.load(open(p)).get("trades", []))
    except Exception: return None
def _light(r):
    st = str(r.get("status") or ""); s, d = r.get("pnl_since_start_aud"), r.get("pnl_today_aud")
    if "STOPPED" in st or "HALT" in st.upper(): return "red", "stopped/halted: " + st
    if s is None: return "amber", "no P&L figure (" + (st or "status unknown") + ")"
    if s < 0 and (d or 0) < 0: return "red", "below start and down today"
    if s < 0: return "amber", "below start"
    if "review" in st.lower() or "CLOSED" in st: return "amber", st
    return "green", "at/above start" + ("" if d is None else (", up today" if d > 0 else ", flat/down today"))
SNAP_P = os.path.join(BASE, "state", "all_desks_since_snap.json"); SNAP = {}
def _wday(): return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))   # WITA date
def _snap_load():
    global SNAP
    try: J = json.load(open(SNAP_P))
    except Exception: J = {}
    d = _wday(); prev = sorted(k for k in J if k < d)
    SNAP = dict(prev=J[prev[-1]] if prev else {}, prev_date=prev[-1] if prev else None)
def _snap_save(m):
    """Keep the LAST since-start figure seen each WITA day; tomorrow's today = since - that."""
    try:
        try: J = json.load(open(SNAP_P))
        except Exception: J = {}
        J[_wday()] = m; J = {k: J[k] for k in sorted(J)[-10:]}
        tmp = SNAP_P + ".tmp"; json.dump(J, open(tmp, "w")); os.replace(tmp, SNAP_P)
    except Exception: pass
def build():
    _snap_load()
    sb = os.path.join(BASE, "reports", "strategy_scoreboard.json")
    if not os.path.exists(sb) or time.time() - os.path.getmtime(sb) > 300:   # refresh (read-only tool, ~0.2s); keep last good copy on failure
        try: subprocess.run([sys.executable, os.path.join(BASE, "tools", "strategy_scoreboard.py")], cwd=BASE, timeout=60, capture_output=True)
        except Exception: pass
    S = json.load(open(sb))
    cards, tot = [], dict(pnl_today_aud=0.0, pnl_week_aud=0.0, pnl_since_start_aud=0.0); miss = {k: 0 for k in tot}; n = 0
    for r in S.get("rows", []):
        if r.get("family") == "veteran_desk" or str(r.get("strategy", "")).startswith("Veteran Desk"): continue
        name = str(r.get("strategy", "")).replace("└", "").strip(); inc = not any(x in str(r.get("strategy", "")) for x in NOT_IN_TOTAL) and r.get("in_firm_equity") is not False
        r = dict(r); tb = None
        if r.get("pnl_today_aud") is None:   # 2026-10-29 fix: fill today only where data exists (never invented)
            s0 = r.get("pnl_since_start_aud"); op = r.get("open_positions")
            if s0 is not None and float(s0) == 0 and not op: r["pnl_today_aud"] = 0.0; tb = "0: never traded, nothing open"
            elif s0 is not None and name in SNAP.get("prev", {}) and not op: r["pnl_today_aud"] = round(float(s0) - SNAP["prev"][name], 2); tb = "since-start change vs " + SNAP.get("prev_date", "?") + " close snapshot (flat book)"
            elif s0 is not None and name in SNAP.get("prev", {}): r["pnl_today_aud"] = round(float(s0) - SNAP["prev"][name], 2); tb = "since-start change vs " + SNAP.get("prev_date", "?") + " snapshot (derived)"
        lt, why = _light(r)
        cards.append(dict(name=name, today_basis=tb, today=r.get("pnl_today_aud"), week=r.get("pnl_week_aud"), since=r.get("pnl_since_start_aud"), open=r.get("open_positions"),
                          armed=_armed(r.get("source")), light=lt, why=why, status=("shadow" if (str(r.get("status") or "").lower() == "running" and any(x in name.lower() for x in ("shadow", "benchmark"))) else r.get("status")), in_total=inc, family=r.get("family")))
        if inc:
            n += 1
            for k in tot:
                if r.get(k) is None: miss[k] += 1
                else: tot[k] += r[k]
    _snap_save({c["name"]: c["since"] for c in cards if c["since"] is not None})
    cards.sort(key=lambda c: 1e18 if c["since"] is None else -c["since"])
    return dict(paper_only=True, excludes="Veteran Desk", generated_wita=S.get("generated_wita"), audusd=S.get("audusd"),
                total={k: round(v, 2) for k, v in tot.items()}, total_missing=miss, total_books=n, cards=cards)
if __name__ == "__main__": print(json.dumps(build(), indent=1)[:3000])
