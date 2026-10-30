"""Pre-registered hunt SHAPE trial (Brett 2026-10-28 12:17 WITA, STEP 2). PAPER / SHADOW ONLY.
Frozen parallel variant of the 48-72h hunt: +5% target / -4% stop vs the control +3% / -8%, replayed on IDENTICAL candidates and
entries from every hunt48 ledger (read-only). Never alters the live hunt shape, never writes a hunt ledger, never places an order.
Cohorts: 'filled' (the desk filled it: same entry px/time/shares/time-exit) and 'cleared_unfilled' (cleared every gate, never filled:
shadow-filled at its trigger by bar replay, tagged separately, NOT counted to the stopping rule). Forward only: items armed or
entered at/after the registered start_utc. Shadow fills carry the bar time they occurred plus recorded_utc (when this job saw it).
Also logs the per-book funnel candidates -> cleared -> triggered -> filled. Writes state/hunt_shape_trial/."""
import datetime as dt, glob, hashlib, json, os, sys
# 2026-10-28 Brett B3: hunt scripts honour the desk base path (env DESK_BASE, default = this folder)
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__))); D = os.path.join(BASE, "state", "hunt_shape_trial")
PREREG = os.path.join(D, "prereg.json"); PREREG_MD = os.path.join(D, "PREREGISTRATION.md"); BOOK = os.path.join(D, "trial_book.json")
SUMMARY = os.path.join(D, "summary.json"); FUNNEL = os.path.join(D, "funnel.json"); FUNNEL_LOG = os.path.join(D, "funnel_log.jsonl")
FROZEN_DIR = os.path.join(BASE, "state", "frozen_tests"); UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8)); FX = 0.7022
PARAMS = {"name": "hunt_shape_5_4_vs_3_8", "arms": {"control": {"target_pct": 0.03, "stop_pct": 0.08}, "variant": {"target_pct": 0.05, "stop_pct": 0.04}},
          "hold_max_h": 72, "trigger_window_h_unfilled": 24, "bar_interval": "1h", "same_bar_both_hit": "stop_first",
          "unfilled_gap_rule": "no fill if bar opens beyond max_fill (same as live)", "cost_rule": "same A$ cost both arms: trade est_cost_aud, else actual fees+borrow, else 25bp of notional",
          "sizing": "identical shares to the desk trade (identical entries)", "stopping_rule_n_closed": 50, "stopping_cohort": "filled",
          "metric": "A$ P&L after costs (sum) and win rate vs break-even win rate (incl. costs), per arm",
          "decision": "at n=50 closed filled-cohort trades: variant preferred only if its A$ after costs > control's AND its win rate > its own break-even; report only - any live shape change is Brett's decision",
          "ledgers": "state/hunt48_*/ledger.json"}
HYPOTHESIS = ("H1: on the same cleared candidates and entries, a +5%/-4% shape (BE ~44% + costs) earns more A$ after costs than the current "
              "+3%/-8% shape (BE ~72.7% + costs), because the current shape needs an unrealistically high win rate. H0: no improvement.")
def checksum(p): return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def _j(p, fb=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return fb
def atomic(p, d):
    tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, indent=1, default=str)
    os.replace(tmp, p)
def ts(x):
    if not x: return None
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")); return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception: return None
def register(now=None):
    """Write the pre-registration once. Never overwrites an existing one (frozen)."""
    os.makedirs(D, exist_ok=True)
    pr = _j(PREREG)
    if pr: return pr
    now = now or dt.datetime.now(UTC)
    pr = dict(registered_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), registered_wita=now.astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA"),
              start_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), authority="Brett (funder) 2026-10-28 12:17 WITA", paper_only=True, shadow_only=True,
              hypothesis=HYPOTHESIS, params=PARAMS, params_sha256=checksum(PARAMS))
    atomic(PREREG, pr)
    with open(PREREG_MD, "w") as f:
        f.write(f"# Hunt shape trial pre-registration ({pr['registered_wita']})\n\nPAPER / SHADOW ONLY. Live hunt shape (+3%/-8%) unchanged.\n\n"
                f"**Hypothesis.** {HYPOTHESIS}\n\n**Arms.** control +3%/-8%; variant +5%/-4%. Identical candidates, entries, shares, time exits (<=72h).\n\n"
                f"**Stopping rule.** Evaluate once {PARAMS['stopping_rule_n_closed']} filled-cohort trades have closed. Cleared-but-unfilled shadow fills are reported separately and do not count.\n\n"
                f"**Metric.** {PARAMS['metric']}.\n\n**Decision.** {PARAMS['decision']}.\n\n**Start.** {pr['start_utc']} (forward only; nothing before this counts).\n\n"
                f"**Frozen params** (sha256 `{pr['params_sha256']}`):\n\n```json\n{json.dumps(PARAMS, indent=1, sort_keys=True)}\n```\n")
    return pr
def amend(note, now=None):
    """Transparent pre-reg amendment log (frozen PARAMS untouched; appended once per note)."""
    pr = _j(PREREG)
    if not pr: return None
    am = pr.setdefault("amendments", [])
    if any(a.get("note") == note for a in am): return pr
    now = now or dt.datetime.now(UTC); a = dict(utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), wita=now.astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA"), note=note, params_sha256_after=checksum(pr["params"]))
    am.append(a); atomic(PREREG, pr)
    with open(PREREG_MD, "a") as f: f.write(f"\n**Amendment {a['wita']}.** {note} (frozen params unchanged, sha256 `{a['params_sha256_after']}`)\n")
    return pr
def verify(pr): return bool(pr) and checksum(pr["params"]) == pr["params_sha256"] and pr["params"] == PARAMS

def book_of(path): return os.path.basename(os.path.dirname(path)).replace("hunt48_", "")
def sym_of(t): return t.get("yahoo") or t.get("ticker") or t.get("sym")
def is_cleared(t):
    """Brett 28 Oct 13:15 WITA measurement fix: cleared ONLY when clearance.final == 'CLEARED' (rows with no final are NOT cleared)."""
    c = t.get("clearance")
    return isinstance(c, dict) and str(c.get("final") or "").upper() == "CLEARED"
def uniq(rows):
    """Measurement fix: one row per name (repeated re-records of e.g. 0288.HK / 1177.HK count once)."""
    return {sym_of(t) for t in rows if sym_of(t)}
def to_aud(t, local):
    if t.get("fx_aud"): return local * float(t["fx_aud"])
    if t.get("q2usd"): return local * float(t["q2usd"]) / FX
    return local / FX
def cost_aud(t, notional_local):
    if t.get("est_cost_aud"): return float(t["est_cost_aud"])
    if t.get("fees_usd") is not None: return (float(t.get("fees_usd") or 0) + float(t.get("borrow_usd") or 0)) / FX
    return 0.0025 * abs(to_aud(t, notional_local))

def simulate(entry, entry_t, side, bars, tp, sl, exit_by, now):
    """Walk bars after entry. Returns dict(status, exit_px, exit_utc, why). Same-bar both-hit -> stop first."""
    s = 1 if side == "long" else -1
    tgt = entry * (1 + s * tp); stp = entry * (1 - s * sl); last = None
    for b in bars:
        if b["t"] <= entry_t: continue
        if b["t"] > exit_by: break
        last = b
        hit_stop = b["l"] <= stp if s > 0 else b["h"] >= stp
        hit_tgt = b["h"] >= tgt if s > 0 else b["l"] <= tgt
        if hit_stop: return dict(status="CLOSED", exit_px=(min(b["o"], stp) if s > 0 else max(b["o"], stp)), exit_utc=b["t"], why="stop")
        if hit_tgt: return dict(status="CLOSED", exit_px=(max(b["o"], tgt) if s > 0 else min(b["o"], tgt)), exit_utc=b["t"], why="target")
    if now >= exit_by and last: return dict(status="CLOSED", exit_px=last["c"], exit_utc=last["t"], why="time")
    return dict(status="OPEN", exit_px=(last or {}).get("c"), exit_utc=None, why="open")
def shadow_fill(t, bars, window_end):
    """First bar after armed_utc that fills under the LIVE hunt48 touch rule (cal_paper_bench.touch_fill, fix 3 28 Oct): range crosses the
    trigger -> trigger capped at max_fill; gap-through open within band -> open; open beyond max_fill -> no fill (MISSED). Same entry for both arms."""
    import cal_paper_bench as CB
    side = t.get("direction") or "long"; trig = float(t["trigger"]); mf = float(t.get("max_fill") or trig); a = ts(t.get("armed_utc"))
    sg = 1 if side == "long" else -1; tt = dict(trigger=trig, max_fill=(max(mf, trig) if sg > 0 else min(mf, trig)))
    for b in bars:
        if b["t"] < a or b["t"] > window_end: continue
        px, miss = CB.touch_fill(tt, b, sg)
        if miss: return None
        if px is not None: return (px, b["t"])
    return None

def fetch_bars(sym, cache):
    if sym in cache: return cache[sym]
    try:
        import warnings; warnings.filterwarnings("ignore")
        import yfinance as yf
        d = yf.Ticker(sym).history(period="60d", interval="1h", prepost=False)
        cache[sym] = [dict(t=i.to_pydatetime().astimezone(UTC), o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close)) for i, r in d.iterrows()]
    except Exception:
        cache[sym] = []
    return cache[sym]

def eligible(start, ledgers):
    out = []; seen = set()
    for path in ledgers:
        for t in (_j(path) or {}).get("trades") or []:
            if not t.get("trigger") and not t.get("entry"): continue
            et, at = ts(t.get("entry_utc")), ts(t.get("armed_utc"))
            if t.get("entry") and et and et >= start: out.append(("filled", book_of(path), t))
            elif not t.get("entry") and is_cleared(t) and at and at >= start and t.get("status") not in ("ARMED",):
                k = (book_of(path), sym_of(t), str(t.get("armed_utc"))[:10])
                if k in seen: continue                     # measurement fix: re-recorded duplicate name, same session -> once
                seen.add(k); out.append(("cleared_unfilled", book_of(path), t))
            elif not t.get("entry") and is_cleared(t) and at and at >= start: out.append(("cleared_pending", book_of(path), t))
    return out

def arm_result(t, entry, entry_t, side, bars, arm, exit_by, now, shares):
    r = simulate(entry, entry_t, side, bars, arm["target_pct"], arm["stop_pct"], exit_by, now)
    s = 1 if side == "long" else -1; c = cost_aud(t, entry * shares)
    gross = to_aud(t, s * ((r["exit_px"] or entry) - entry) * shares)
    r.update(gross_aud=round(gross, 2), cost_aud=round(c, 2), net_aud=round(gross - c, 2), cost_pct=c / abs(to_aud(t, entry * shares)) if entry * shares else 0)
    if r["exit_utc"]: r["exit_utc"] = r["exit_utc"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return r

def stats(items, arm):
    cl = [i for i in items if i[arm]["status"] == "CLOSED"]
    nets = [i[arm]["net_aud"] for i in cl]; a = PARAMS["arms"][arm]
    cp = sum(i[arm]["cost_pct"] for i in cl) / len(cl) if cl else 0.0
    be = (a["stop_pct"] + cp) / (a["target_pct"] + a["stop_pct"])
    return dict(n_closed=len(cl), n_open=len(items) - len(cl), pnl_aud=round(sum(nets), 0), win_rate=(sum(n > 0 for n in nets) / len(nets)) if nets else None,
                be_win_rate=round(be, 3), expectancy_aud=round(sum(nets) / len(nets), 0) if nets else None)

def funnel(ledgers, now):
    out = {}; day = now - dt.timedelta(hours=24)
    trial = (_j(BOOK) or {}).get("items") or {}
    for path in ledgers:
        L = _j(path) or {}; T = L.get("trades") or []; b = book_of(path)
        def f(rows):
            clr = [t for t in rows if is_cleared(t)]
            trig = [t for t in rows if t.get("entry") or (is_cleared(t) and (trial.get(f"{b}:{t.get('id')}") or {}).get("shadow_fill_px"))]
            return dict(candidates=len(uniq(rows)), cleared=len(uniq(clr)), triggered=len(uniq(trig)), filled=len(uniq([t for t in rows if t.get("entry")])),
                        rows=len(rows), unit="unique names; cleared = clearance.final CLEARED")
        last = [t for t in T if (ts(t.get("recorded_utc") or t.get("armed_utc")) or now) >= day]
        out[b] = dict(all_time=f(T), last_24h=f(last), dropped=len(L.get("dropped") or []))
    v2 = _j(os.path.join(BASE, "reports", "v2", "tfo_v2_staged.json")) or {}; vb = _j(os.path.join(BASE, "reports", "v2", "tfo_v2_book.json")) or {}
    o = v2.get("orders") or []
    out["tfo_v2"] = dict(all_time=dict(candidates=len(o), cleared=len(o), triggered=None, filled=len(vb.get("positions") or {}) + len(vb.get("closed") or [])), note="V2: staged orders (current file only) vs open+closed positions")
    return out

def run(now=None, bars_fn=None, ledgers=None):
    now = now or dt.datetime.now(UTC); pr = register(now)
    if not verify(pr):
        atomic(SUMMARY, dict(error="FROZEN PARAMS CHECKSUM MISMATCH - trial halted, nothing evaluated", updated_utc=now.isoformat())); return None
    start = ts(pr["start_utc"]); ledgers = ledgers if ledgers is not None else sorted(glob.glob(os.path.join(BASE, "state", "hunt48_*", "ledger.json")))
    prev = (_j(BOOK) or {}).get("items") or {}; cache = {}; bars_fn = bars_fn or (lambda s: fetch_bars(s, cache)); items = {}
    for cohort, b, t in eligible(start, ledgers):
        key = f"{b}:{t.get('id')}"; side = t.get("direction") or "long"; sym = sym_of(t); old = prev.get(key) or {}
        if old.get("control", {}).get("status") == "CLOSED" and old.get("variant", {}).get("status") == "CLOSED" and old.get("cohort") == cohort:
            items[key] = old; continue
        bars = bars_fn(sym) or []
        if cohort == "filled":
            entry, entry_t, shares = float(t["entry"]), ts(t["entry_utc"]), float(t.get("shares") or t.get("open_qty") or 0)
            exit_by = min(ts(t.get("time_exit_orig_utc") or t.get("time_exit_utc")) or entry_t + dt.timedelta(hours=PARAMS["hold_max_h"]), entry_t + dt.timedelta(hours=PARAMS["hold_max_h"]))
            rec = dict(shadow_fill_px=None)
        else:
            if cohort == "cleared_pending": continue
            a = ts(t["armed_utc"]); sf = old.get("shadow_fill_px") and (old["shadow_fill_px"], ts(old["shadow_fill_utc"])) or shadow_fill(t, bars, a + dt.timedelta(hours=PARAMS["trigger_window_h_unfilled"]))
            if not sf:
                if now > a + dt.timedelta(hours=PARAMS["trigger_window_h_unfilled"]): items[key] = dict(cohort="cleared_never_triggered", book=b, sym=sym, side=side, armed_utc=t.get("armed_utc"))
                continue
            entry, entry_t = sf; shares = float(t.get("shares") or 0); exit_by = entry_t + dt.timedelta(hours=PARAMS["hold_max_h"])
            rec = dict(shadow_fill_px=entry, shadow_fill_utc=entry_t.strftime("%Y-%m-%dT%H:%M:%SZ"), shadow_tag="SHADOW FILL (bar replay) - cleared but not filled by desk")
        items[key] = dict(cohort=cohort, book=b, id=t.get("id"), sym=sym, side=side, entry=entry, entry_utc=entry_t.strftime("%Y-%m-%dT%H:%M:%SZ"), shares=shares,
                          exit_by_utc=exit_by.strftime("%Y-%m-%dT%H:%M:%SZ"), recorded_utc=old.get("recorded_utc") or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          desk_status=t.get("status"), desk_realised_aud=(round(float(t["realised_usd"]) / FX, 2) if t.get("realised_usd") is not None else t.get("realised_aud")) if cohort == "filled" else None,
                          control=arm_result(t, entry, entry_t, side, bars, PARAMS["arms"]["control"], exit_by, now, shares),
                          variant=arm_result(t, entry, entry_t, side, bars, PARAMS["arms"]["variant"], exit_by, now, shares), **rec)
    for k, v in prev.items(): items.setdefault(k, v)           # never drop a recorded trial item
    atomic(BOOK, dict(paper_only=True, shadow_only=True, updated_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), items=items))
    S = {}
    for cohort in ("filled", "cleared_unfilled"):
        it = [i for i in items.values() if i.get("cohort") == cohort]
        S[cohort] = {arm: stats(it, arm) for arm in ("control", "variant")}
    n = S["filled"]["variant"]["n_closed"]; N = PARAMS["stopping_rule_n_closed"]
    verdict = "RUNNING"
    if n >= N:
        c, v = S["filled"]["control"], S["filled"]["variant"]
        verdict = "VARIANT PREFERRED (report only)" if v["pnl_aud"] > c["pnl_aud"] and (v["win_rate"] or 0) > v["be_win_rate"] else "CONTROL KEPT (variant not better)"
    summ = dict(paper_only=True, shadow_only=True, updated_wita=now.astimezone(WITA).strftime("%a %d %b %H:%M WITA"), start_utc=pr["start_utc"], params_sha256=pr["params_sha256"],
                params_ok=True, progress=f"{n}/{N} filled-cohort closed", verdict=verdict, cohorts=S,
                cleared_never_triggered=sum(1 for i in items.values() if i.get("cohort") == "cleared_never_triggered"))
    atomic(SUMMARY, summ); fn = funnel(ledgers, now); atomic(FUNNEL, dict(updated_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), books=fn))
    try:
        if os.path.abspath(D) == os.path.join(BASE, "state", "hunt_shape_trial"):     # War Room card feed (not in tests)
            atomic(os.path.join(BASE, "static", "hunt_funnel.json"), dict(updated_wita=now.astimezone(WITA).strftime("%a %d %b %H:%M WITA"), books=fn, trial=dict(progress=summ["progress"], verdict=verdict),
                   note="unique names; cleared = clearance.final == CLEARED (measurement fix Brett 28 Oct 13:15 WITA)"))
    except Exception: pass
    with open(FUNNEL_LOG, "a") as f: f.write(json.dumps(dict(utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), books={b: x["all_time"] for b, x in fn.items()})) + "\n")
    os.makedirs(FROZEN_DIR, exist_ok=True)
    for arm, lab in (("control", "TEST hunt shape CONTROL +3%/-8% (shadow replay)"), ("variant", "TEST hunt shape VARIANT +5%/-4% (shadow replay)")):
        s = S["filled"][arm]
        atomic(os.path.join(FROZEN_DIR, f"hunt_shape_{arm}.json"), dict(label=lab, rule=f"same entries as live hunt; target +{PARAMS['arms'][arm]['target_pct']:.0%} / stop -{PARAMS['arms'][arm]['stop_pct']:.0%}; <=72h; shadow only",
               pass_mark=f"{s['n_closed']}/{N} closed; {PARAMS['decision']}", kill="report only", be_win_rate=s["be_win_rate"], paper_only=True, frozen=True,
               registered=pr["registered_wita"], status="Test", n_closed=s["n_closed"], open=s["n_open"], pnl_aud=s["pnl_aud"], win_rate=s["win_rate"], expectancy_aud=s["expectancy_aud"],
               missed=S["cleared_unfilled"][arm]["n_closed"], params_sha256=pr["params_sha256"], src="state/hunt_shape_trial/summary.json"))
    return summ
if __name__ == "__main__":
    s = run(); print(json.dumps(s, indent=1, default=str)[:3000])
