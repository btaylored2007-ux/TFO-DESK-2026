"""2026-10-29 PAPER: targeted high/low clip of confirmed-bad Yahoo bars in data/pit/pit.sqlite (NOT a blanket clip).
Clip only (a) non-crypto bars in the liquid universe flagged by hl_sanity_scan_20261029.json AND confirmed bad by IBKR (read-only
reqHistoricalData via ibkr_bar_check.py), or (b) Brett's hand list (clipped even when IBKR cannot serve the bar).
Keep a bar wherever IBKR shows the extreme really traded (real small-cap spikes such as AEHL / DKI stay).
Usage: python hl_clip_20261029.py PLAN_OUT.json IBKR1.json [IBKR2.json ...] [--apply]   (dry-run by default; --apply needs a pit.sqlite backup taken first)
py3.9-compatible."""
import json, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "pit", "pit.sqlite")
SCAN = os.path.join(ROOT, "data", "pit", "hl_sanity_scan_20261029.json")
OUT = os.path.join(ROOT, "data", "pit", "hl_clip_applied_20261029.json")
TAG = "hlclip_20261029"
HAND = [("BARC.L", "2026-06-17"), ("HSBA.L", "2026-07-06"), ("HSBA.L", "2026-07-17"), ("AZN.L", "2026-07-15"),
        ("DGE.L", "2026-07-09"), ("DGE.L", "2026-07-10"), ("DGE.L", "2026-07-13"),
        ("GLEN.L", "2026-10-15"), ("GSK.L", "2026-10-15"), ("HLN.L", "2026-10-15"),
        ("BA.L", "2026-10-26"), ("EXPN.L", "2026-10-26"), ("EZJ.L", "2026-10-26"), ("III.L", "2026-10-26"), ("ICG.L", "2026-10-26")]
BATS_PCT = 0.08   # BATS.L Jun-Jul prints: any extreme > 8% beyond the open-close body
REL = 0.05        # hand-list side with no IBKR: excess beyond body > 5% of the body edge
CLOSE_TOL = 0.03  # IBKR bar is "the same bar" when its close is within 3% of Yahoo's (scale 1, 100 or 1/100 for GBp/GBP)
EXT_TOL = 0.005   # Yahoo extreme must exceed IBKR's by > 0.5% to count as not traded
def is_crypto(sym): return ":" in sym
def ibkr_scale(ycl, icl):
    for s in (1.0, 100.0, 0.01):
        if icl and abs(icl * s / ycl - 1) <= CLOSE_TOL: return s
    return None
def decide(row, sides, ib, hand):
    """row = (o,h,l,c) Yahoo/DB; sides = set of 'high'/'low' to judge; ib = [o,h,l,c,v] IBKR or None; hand = bool.
    Returns dict(action='clip'|'keep', new_h, new_l, why={side: reason})."""
    o, h, l, c = row; bt, bb = max(o, c), min(o, c); nh, nl = h, l; why = {}
    sc = ibkr_scale(c, ib[3]) if ib else None
    for sd in sorted(sides):
        y = h if sd == "high" else l; edge = bt if sd == "high" else bb
        if (sd == "high" and y <= bt) or (sd == "low" and y >= bb): why[sd] = "inside body - nothing to clip"; continue
        if ib and sc:
            iv = (ib[1] if sd == "high" else ib[2]) * sc
            beyond = (y > iv * (1 + EXT_TOL)) if sd == "high" else (y < iv * (1 - EXT_TOL))
            removed = (y - max(edge, iv)) / (y - edge) if sd == "high" else (min(edge, iv) - y) / (edge - y)
            if beyond and (removed > 0.5 or hand):   # hand list: any print IBKR says never traded (>0.5% beyond) goes to the IBKR extreme
                v = max(edge, iv) if sd == "high" else min(edge, iv)
                if sd == "high": nh = round(v, 6)
                else: nl = round(v, 6)
                why[sd] = "ibkr_confirms_bad: yahoo %g vs ibkr %g -> %g" % (y, iv, v); continue
            why[sd] = "ibkr_shows_traded: yahoo %g vs ibkr %g - kept" % (y, iv)
            if hand: why[sd] += " (hand list, IBKR overrides)"
            continue
        reason = "no_ibkr" if not ib else "ibkr_mismatch (ibkr close %g vs yahoo %g)" % (ib[3], c)
        if hand:
            if sd == "high": nh = bt
            else: nl = bb
            why[sd] = "hand_list_%s: clamped to body edge %g" % (reason, edge)
        else: why[sd] = "%s - kept (not confirmed bad)" % reason
    return dict(action="clip" if (nh, nl) != (h, l) else "keep", new_h=nh, new_l=nl, why=why, ibkr_scale=sc)
def main(argv):
    apply_ = "--apply" in argv; args = [a for a in argv if not a.startswith("--")]
    plan_out, ibf = args[0], args[1:]
    IB = {}
    for f in ibf:
        for s, v in json.load(open(f)).items():
            if "bars" in v or s not in IB: IB[s] = v
    scan = json.load(open(SCAN))
    con = sqlite3.connect(DB, timeout=120) if apply_ else sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=120)
    cand = {}
    for r in scan:
        if not r.get("liquid") or is_crypto(r["sym"]): continue
        cand.setdefault((r["sym"], r["date"]), dict(scan=r, hand=False))
    hand = list(HAND)
    for d, o, h, l, c in con.execute("SELECT date,o,h,l,c FROM bars WHERE sym='BATS.L' AND date BETWEEN '2026-06-01' AND '2026-07-31' ORDER BY date"):
        bt, bb = max(o, c), min(o, c)
        if (h - bt) / bt > BATS_PCT or (bb - l) / bb > BATS_PCT: hand.append(("BATS.L", d))
    for k in hand: cand.setdefault(k, dict(scan=None, hand=True))["hand"] = True
    clipped, kept, skipped, restored = [], [], [], []
    for (s, d), m in sorted(cand.items()):
        row = con.execute("SELECT o,h,l,c,src FROM bars WHERE sym=? AND date=?", (s, d)).fetchone()
        if not row: skipped.append(dict(sym=s, date=d, why="no row in pit")); continue
        o, h, l, c, src = row; r = m["scan"]; sides = set(); stale = []
        if r:
            for fl in r["flags"]:
                sd = fl.split()[0]
                if (sd == "high" and abs(h - r["h"]) > 1e-5 * max(1.0, abs(h))) or (sd == "low" and abs(l - r["l"]) > 1e-5 * max(1.0, abs(l))): stale.append(sd)   # scan JSON is rounded; DB holds float32-ish yahoo values
                else: sides.add(sd)
        ibv = IB.get(s) or {}; ib = (ibv.get("bars") or {}).get(d)
        if m["hand"]:
            bt, bb = max(o, c), min(o, c)
            if (h - bt) / bt > REL: sides.add("high")
            if (bb - l) / bb > REL: sides.add("low")
            if ib:
                sc = ibkr_scale(c, ib[3])
                if sc and h > bt and h > ib[1] * sc * (1 + EXT_TOL): sides.add("high")
                if sc and l < bb and l < ib[2] * sc * (1 - EXT_TOL): sides.add("low")
        base = dict(sym=s, date=d, hand=m["hand"], liquid=bool(r), old=dict(o=o, h=h, l=l, c=c, src=src), ibkr_bar=ib, ibkr_err=ibv.get("err"), ibkr_exch=ibv.get("exch"))
        if stale: base["already_changed_sides"] = stale
        rs = {}
        for sd in stale:   # bar re-written since the 13:46 scan (pit.put_bars ingest filter clamps to the body on every re-fetch): put back an extreme IBKR shows really traded
            sc = ibkr_scale(c, ib[3]) if ib else None; bt, bb = max(o, c), min(o, c); cur = h if sd == "high" else l; edge = bt if sd == "high" else bb; orig = r[sd[0]]
            if not sc or abs(cur - edge) > 1e-5 * max(1.0, abs(edge)): continue   # only rows clamped exactly to the body edge
            iv = (ib[1] if sd == "high" else ib[2]) * sc
            if (sd == "high" and orig <= iv * (1 + EXT_TOL)) or (sd == "low" and orig >= iv * (1 - EXT_TOL)): rs[sd] = orig
        if rs:
            restored.append(dict(base, new=dict(h=rs.get("high", h), l=rs.get("low", l)), why={sd: "ibkr_shows_traded: scan extreme %g vs ibkr %g - ingest clamp undone" % (v, ((ib[1] if sd == "high" else ib[2]) * ibkr_scale(c, ib[3]))) for sd, v in rs.items()}))
            continue
        if not sides:
            kept.append(dict(base, why={"-": "already changed since scan" if stale else ("hand list: no side beyond body >5%% and IBKR %s" % ("agrees" if ib else "unavailable"))})); continue
        dcs = decide((o, h, l, c), sides, ib, m["hand"])
        rec = dict(base, new=dict(h=dcs["new_h"], l=dcs["new_l"]), why=dcs["why"], ibkr_scale=dcs["ibkr_scale"])
        (clipped if dcs["action"] == "clip" else kept).append(rec)
    n_app = 0
    if apply_:
        with con:
            for x in clipped:
                o = x["old"]; cur = con.execute("UPDATE bars SET h=?, l=?, src=COALESCE(src,'')||? WHERE sym=? AND date=? AND h=? AND l=?",
                                                (x["new"]["h"], x["new"]["l"], "|%s(h %g->%g,l %g->%g)" % (TAG, o["h"], x["new"]["h"], o["l"], x["new"]["l"]), x["sym"], x["date"], o["h"], o["l"]))
                x["applied"] = cur.rowcount == 1; n_app += cur.rowcount
            for x in restored:
                o = x["old"]; cur = con.execute("UPDATE bars SET h=?, l=?, src=COALESCE(src,'')||? WHERE sym=? AND date=? AND h=? AND l=?",
                                                (x["new"]["h"], x["new"]["l"], "|hlrestore_20261029(h %g->%g,l %g->%g ibkr traded)" % (o["h"], x["new"]["h"], o["l"], x["new"]["l"]), x["sym"], x["date"], o["h"], o["l"]))
                x["applied"] = cur.rowcount == 1; n_app += cur.rowcount
    res = dict(job=TAG, paper_only=True, applied=apply_, db=DB, ibkr_files=ibf,
               rule="clip only non-crypto liquid bars confirmed bad by IBKR read-only, plus Brett's hand list (clamped to body when IBKR cannot serve); keep where IBKR shows the extreme traded",
               counts=dict(candidates=len(cand), clipped=len(clipped), kept=len(kept), restored=len(restored), skipped=len(skipped), rows_updated=n_app,
                           clipped_hand=sum(x["hand"] for x in clipped), clipped_liquid_ibkr=sum((not x["hand"]) for x in clipped),
                           kept_ibkr_traded=sum(any("ibkr_shows_traded" in w for w in x["why"].values()) for x in kept),
                           kept_no_ibkr=sum(any(("no_ibkr" in w or "mismatch" in w) for w in x["why"].values()) for x in kept)),
               clipped=clipped, kept=kept, restored=restored, skipped=skipped)
    json.dump(res, open(plan_out, "w"), indent=1, default=str)
    print(json.dumps(res["counts"]))
if __name__ == "__main__":
    main(sys.argv[1:])
