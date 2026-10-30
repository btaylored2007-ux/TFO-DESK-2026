"""One-off 2026-10-30 (learning overlord 22:10 WITA): recompute r_multiple on every journaled close from the book's closed record using
learning.risk0 (original initial stop, this leg's shares). Backs up each journal first. Journals are learning state, NOT ledgers; book untouched."""
import json, os, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import learning as L
D = json.load(open(L.BOOK)); au = D.get("audusd"); stamp = time.strftime("%Y%m%d-%H%M"); rep = {}
for seat, B in (D.get("seats") or {}).items():
    jp = os.path.join(L._sd(seat), "journal.jsonl")
    if not os.path.exists(jp): continue
    byid = {p.get("id"): p for p in list(B.get("closed") or []) + list((B.get("positions") or {}).values())}
    rows = [json.loads(l) for l in open(jp) if l.strip()]; ch = []
    for x in rows:
        if x.get("event") != "close" or x.get("pnl_aud_net") is None: continue
        p = byid.get(x.get("id"))
        if not p: continue
        r0 = L.risk0(p, au); r = round(float(x["pnl_aud_net"]) / r0, 3) if r0 else None
        if r != x.get("r_multiple"): ch.append((x.get("ticker"), x.get("r_multiple"), r)); x["r_multiple_old"] = x.get("r_multiple"); x["r_multiple"] = r; x["risk0_aud"] = r0 and round(r0, 2); x["r_fix"] = "learning_r_20261030"
    if ch:
        shutil.copy2(jp, jp + ".bak-" + stamp + "-learnR"); t = jp + ".tmp"
        open(t, "w").write("".join(json.dumps(x) + "\n" for x in rows)); os.replace(t, jp)
    R = [x["r_multiple"] for x in rows if x.get("event") == "close" and x.get("r_multiple") is not None]
    rep[seat] = dict(changed=ch, n=len(R), avg_r=round(sum(R) / len(R), 3) if R else None, net_aud=round(sum(x["pnl_aud_net"] for x in rows if x.get("event") == "close" and x.get("pnl_aud_net") is not None), 2))
print(json.dumps(rep, indent=1))
