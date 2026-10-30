"""Forward-book pass mark (Brett 2026-10-28 04:50 WITA, pre-registered in research_controls/forward_pass_mark.json).
PAPER ONLY, read-only: reads the US main ledger, writes static/forward_pass_mark.json for the War Room card.
The FLAG only reports; nothing here disables, pauses or resizes any book."""
import datetime as dt, json, os, random
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "research_controls", "forward_pass_mark.json")
def trade_R(t, fx):
    risk_usd = float(t.get("risk_cap_aud") or 2500.0) * fx
    return (float(t.get("realised_usd") or 0) - float(t.get("fees_usd") or 0) - float(t.get("borrow_usd") or 0)) / risk_usd
def perm_p(xs, n_perm, seed=20261028):
    rng = random.Random(seed); obs = sum(xs) / len(xs); ge = 0
    for _ in range(n_perm):
        if sum(x if rng.random() < 0.5 else -x for x in xs) / len(xs) >= obs: ge += 1
    return (1 + ge) / (1 + n_perm)
def median(xs):
    s = sorted(xs); n = len(s); return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
def evaluate(trades, cfg):
    closed = [t for t in trades if t.get("status") == "CLOSED" and t.get("entry") is not None]
    closed.sort(key=lambda t: max([e.get("utc", "") for e in t.get("exits") or []] or [""]))
    R = [trade_R(t, cfg["audusd_fixed"]) for t in closed]
    out = dict(n_closed=len(R), mean_R=(sum(R) / len(R)) if R else None, median_R=median(R) if R else None, checkpoints=[], flag=None)
    for n in cfg["checkpoints"]:
        if len(R) < n: out["checkpoints"].append(dict(n=n, status="PENDING", need=n - len(R))); continue
        x = R[:n]; m, md, p = sum(x) / n, median(x), perm_p(x, cfg["n_perm"])
        ok = m > 0 and md > 0 and p < cfg["p_max"]
        out["checkpoints"].append(dict(n=n, status="PASS" if ok else "FAIL", mean_R=round(m, 4), median_R=round(md, 4), p=round(p, 4)))
        if n == max(cfg["checkpoints"]) and not ok: out["flag"] = "FLAG: hunt failed the 100-trade pass mark - review for change or cut (report only; nothing disabled)"
    return out
def run(base=BASE):
    cfg = json.load(open(CFG)); L = json.load(open(os.path.join(base, "state", "hunt48_short", "ledger.json")))
    res = dict(paper_only=True, rule=cfg["pass_rule"], fail_action=cfg["fail_action_at_100"], registered=cfg["registered_wita"],
               updated_wita=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%a %d %b %H:%M WITA"), **evaluate(L["trades"], cfg))
    p = os.path.join(base, "static", "forward_pass_mark.json"); json.dump(res, open(p + ".tmp", "w"), indent=1); os.replace(p + ".tmp", p); return res
if __name__ == "__main__": print(json.dumps(run(), indent=1))
