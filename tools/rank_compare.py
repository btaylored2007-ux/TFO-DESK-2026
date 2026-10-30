#!/usr/bin/env python3
"""Top-N Yahoo-vs-FMP rank diff (markdown to stdout). Usage: rank_compare.py YAHOO.json FMP.json [N]"""
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3]); n = int(sys.argv[3]) if len(sys.argv) > 3 else 30
ra = [r["symbol"] for r in a["ranked"]]; rb = [r["symbol"] for r in b["ranked"]]
ta, tb = ra[:n], rb[:n]; sa = {r["symbol"]: r for r in a["ranked"]}; sb = {r["symbol"]: r for r in b["ranked"]}
print("Top-%d Yahoo (%s, gen %s) vs FMP (%s, gen %s): overlap %d/%d" % (n, sys.argv[1], a.get("generated_utc"), sys.argv[2], b.get("generated_utc"), len(set(ta) & set(tb)), n))
print("| # | Yahoo | FMP |\n|---|---|---|")
for i in range(n): print("| %d | %s | %s |" % (i+1, ta[i] if i < len(ta) else "", tb[i] if i < len(tb) else ""))
print("Only Yahoo top-%d: %s" % (n, ", ".join("%s(FMP#%s)" % (s, rb.index(s)+1 if s in rb else "-") for s in ta if s not in tb) or "-"))
print("Only FMP top-%d: %s" % (n, ", ".join("%s(Yahoo#%s)" % (s, ra.index(s)+1 if s in ra else "-") for s in tb if s not in ta) or "-"))
sm = [s for s in set(ta) | set(tb) if s in sa and s in sb and sa[s].get("sector") != sb[s].get("sector")]
print("Sector mismatches: %s" % (", ".join("%s %s->%s" % (s, sa[s].get("sector"), sb[s].get("sector")) for s in sorted(sm)) or "-"))
