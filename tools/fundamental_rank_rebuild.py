#!/usr/bin/env python3
"""Daily atomic rebuild: value_hunt.json -> value_rank.json. PAPER ONLY. No orders.
HT-approved 2026-10-29. Writes temp files; renames only if both stages succeed and output is sane.
On any failure the existing reports are left untouched. Rename is done under desk_lock ledger_lock('fundamental')."""
import json, os, sys, time, traceback
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE); os.chdir(BASE)
REP = os.path.join(BASE, "reports")
UNIV = os.environ.get("RANK_UNIVERSE", os.path.join(BASE, "state", "fundamental_rank_universe.json"))
HUNT, RANK = os.path.join(REP, "value_hunt.json"), os.path.join(REP, "value_rank.json")
MIN_RANKED, MIN_KEPT_FRAC = 100, 0.5
def log(m): print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S %Z"), m), flush=True)
def main():
    tag = time.strftime("%Y%m%d_%H%M%S"); th, tr = HUNT + ".tmp_" + tag, RANK + ".tmp_" + tag
    try:
        U = json.load(open(UNIV)); log("universe %s: %d names" % (UNIV, len(U)))
        prev_kept = (json.load(open(HUNT)).get("n_kept") or 0) if os.path.exists(HUNT) else 0
        import value_hunt as H, value_rank as R
        H.OUT = th; H.main(U)
        h = json.load(open(th)); h["universe"] = os.path.relpath(UNIV, BASE); json.dump(h, open(th, "w"), indent=1)
        if h["n_kept"] < max(MIN_RANKED, MIN_KEPT_FRAC * prev_kept):
            raise RuntimeError("hunt insane: kept %d (prev %d) — likely rate-limited/fetch failure" % (h["n_kept"], prev_kept))
        R.SRC, R.OUT = th, tr; R.main()
        n = len(json.load(open(tr))["ranked"])
        if n < MIN_RANKED: raise RuntimeError("rank insane: %d ranked < %d" % (n, MIN_RANKED))
        import desk_lock
        with desk_lock.ledger_lock("fundamental", timeout=600):
            os.replace(th, HUNT); os.replace(tr, RANK)
        log("OK: value_hunt kept %d/%d, value_rank %d ranked — renamed into place" % (h["n_kept"], h["n_screened"], n)); return 0
    except Exception as e:
        log("FAILED (old files kept): %s" % e); traceback.print_exc(); return 1
    finally:
        for f in (th, tr):
            if os.path.exists(f): os.remove(f)
if __name__ == "__main__": sys.exit(main())
