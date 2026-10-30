"""Calibrate gate v2 vs v1 on historical setup-like samples from PIT history (TRAINING ONLY, never promotion evidence).
Samples: desk universe, every 3rd bar, trend-direction entry at close, stop k ATR, target rr*k, 10-day time stop, costs, gaps through stop at open.
Base rate for sample i uses only outcomes completed before i (no lookahead).  python3 -m veteran_desk.gate_calibrate"""
import json, os, sys, datetime as dt
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P, gate_v2 as G
KS = [0.4, 0.75, 1.0, 1.5, 2.0, 3.0, 4.5, 5.5]; RRS = [1.2, 1.5, 2.0, 3.0]; H = 10
OUT = os.path.join(P.BASE, "state", "veteran_desk", "gate_calibration_TRAINING_ONLY.json")
def samples(asof):
    syms = sorted({s for L in P.UNIVERSE.values() for s in L}); S = []
    for sym in syms:
        rows, src = G.history(sym, asof)
        if len(rows) < 400: continue
        A = G.arrays(rows); n = len(A["c"]); v = P.venue_of(sym); cost = G.COST_RT.get(v, .002); cache = {}
        for i in range(260, n - H, 3):
            st = int(A["trend"][i])
            if st == 0 or not np.isfinite(A["atr"][i]) or A["atr"][i] <= 0: continue
            side = "LONG" if st == 1 else "SHORT"; k = KS[(i // 3) % len(KS)]; rr = RRS[(i // 24) % len(RRS)]
            key = (side, k, rr)
            if key not in cache:
                R = G.outcomes(A, side, k, rr, H, cost); ok = np.isfinite(R) & (A["trend"] == st)
                cache[key] = (R, np.cumsum(np.where(ok, R, 0)), np.cumsum(ok))
            R, cs, cn = cache[key]; j = i - H - 1   # outcomes of entries <= i-H-1 are complete by bar i
            nb = int(cn[j]) if j >= 0 else 0; exp = float(cs[j] / nb) if nb else None
            sg = st; er = A["er"][i]; a = A["atr"][i]
            chase = sg * A["ret5"][i] > .12 or sg * A["ret20"][i] > .25 or sg * (A["c"][i] - A["s20"][i]) > 3 * a
            S.append(dict(sym=sym, i=i, date=A["dates"][i], side=side, k=k, rr=rr, R=float(R[i]), er=float(er), chase=bool(chase), base_n=nb, base_exp=exp))
    return S
def v1_block(s): return s["k"] < .5 or s["k"] > 4 or s["rr"] < 1.5 or s["er"] < .25 or s["chase"]
def v2_block(s, th):
    if s["k"] < th["stop_min_atr"] or s["k"] > th["stop_max_atr_hard"] or s["rr"] < th["rr_min"]: return True
    return s["base_n"] >= th["base_n_min"] and s["base_exp"] is not None and s["base_exp"] <= th["base_block_exp"]
def v2_mult(s, th):
    m = 1.0
    if s["k"] > th["stop_max_atr_soft"]: m *= .75
    if s["k"] < th["stop_tight_soft"]: m *= .5
    if s["er"] < th["choppy_er_soft"]: m *= th["chop_mult"]
    if s["chase"]: m *= th["chase_mult"]
    e = s["base_exp"]
    if s["base_n"] >= th["base_n_min"] and e is not None:
        if th["base_block_exp"] < e <= th["base_half_exp"]: m *= .5
        elif th["base_half_exp"] < e < th["base_soft_exp"]: m *= .75
    if s["base_n"] < th["base_n_min"]: m *= .75
    return max(m, th["min_mult"])
def stats(S, blockf):
    B = [s for s in S if blockf(s)]; Pp = [s for s in S if not blockf(s)]; W = [s for s in S if s["R"] > 0]; L = [s for s in S if s["R"] <= 0]
    bw = sum(1 for s in B if s["R"] > 0); bl = len(B) - bw
    return dict(samples=len(S), blocked=len(B), block_rate=round(len(B) / len(S), 3), winners_blocked_pct=round(bw / max(len(W), 1), 3), losers_blocked_pct=round(bl / max(len(L), 1), 3),
                blocked_that_were_winners_pct=round(bw / max(len(B), 1), 3), blocked_avg_R=round(float(np.mean([s["R"] for s in B])), 3) if B else None,
                passed_avg_R=round(float(np.mean([s["R"] for s in Pp])), 3) if Pp else None, passed_win_rate=round(float(np.mean([s["R"] > 0 for s in Pp])), 3) if Pp else None)
def bucket(S, key, edges):
    out = {}
    for lo, hi in zip(edges[:-1], edges[1:]):
        X = [s["R"] for s in S if lo <= s[key] < hi]
        if X: out["%s-%s" % (lo, hi)] = dict(n=len(X), avg_R=round(float(np.mean(X)), 3), win=round(float(np.mean(np.array(X) > 0)), 3))
    return out
def run(asof=None, th=None):
    asof = asof or dt.datetime.now(dt.timezone.utc); T = dict(G.TH, **(th or {})); S = samples(asof)
    rep = dict(label="TRAINING_ONLY calibration (historical, universe-biased, never promotion evidence)", generated_utc=dt.datetime.now(dt.timezone.utc).isoformat(), symbols=len({s["sym"] for s in S}),
               v1_mechanical=stats(S, v1_block), v2_hard=stats(S, lambda s: v2_block(s, T)), thresholds=T,
               by_stop_atr={str(k): bucket([s for s in S if s["k"] == k], "rr", [0, 1.5, 2.5, 9]) for k in KS},
               by_efficiency=bucket(S, "er", [0, .1, .15, .25, .35, .5, 1.01]),
               chase=dict(yes=bucket([s for s in S if s["chase"]], "k", [0, 99]), no=bucket([s for s in S if not s["chase"]], "k", [0, 99])),
               by_base_exp=bucket([s for s in S if s["base_n"] >= 40], "base_exp", [-9, -.5, -.3, -.15, 0, .15, 9]))
    wR = [(s["R"], v2_mult(s, T)) for s in S if not v2_block(s, T)]
    rep["v2_size_weighted_avg_R"] = round(sum(r * m for r, m in wR) / max(sum(m for r, m in wR), 1e-9), 3)
    json.dump(rep, open(OUT, "w"), indent=1); return rep
if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
