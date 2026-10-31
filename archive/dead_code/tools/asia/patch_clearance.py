import sys
p = sys.argv[1]; s = open(p).read()
def rep(old, new, n=1):
    global s
    assert s.count(old) == n, (old[:80], s.count(old)); s = s.replace(old, new)
rep('if ASX_ONLY: BOOKS = {"asx": "hunt48_asx"}; OUT = os.path.join(OUT, "asx")\n',
    'if ASX_ONLY: BOOKS = {"asx": "hunt48_asx"}; OUT = os.path.join(OUT, "asx")\n'
    '# ASIA books (Brett 2026-10-27 pt1481u): `--book jp|hk|sg|kr|tw` runs ONLY that separate A$1m book in state/clearance/D/<mkt>/ (others untouched).\n'
    '# Candidates = asia_hunt48.clearance_plan(mkt): ledger BENCH (never-demoted) + ARMED with current levels, LOCAL ccy; ADV/risk converted to AUD.\n'
    'ASIA_MKTS = ("jp", "hk", "sg", "kr", "tw")\n'
    'ASIA_MKT = arg("--book") if arg("--book") in ASIA_MKTS else None\n'
    'if ASIA_MKT: BOOKS = {ASIA_MKT: f"hunt48_{ASIA_MKT}"}; OUT = os.path.join(OUT, ASIA_MKT)\n')
rep('    for f in ("ranked_all_incl_excluded.csv", "ranked.csv", "ranked_oanda.csv", "ranked_asx.csv"):',
    '    for f in ("ranked_all_incl_excluded.csv", "ranked.csv", "ranked_oanda.csv", "ranked_asx.csv") + ((f"ranked_{ASIA_MKT}.csv",) if ASIA_MKT else ()):')
rep('    if ASX_ONLY: return {"asx": asx_plan()}\n',
    '    if ASX_ONLY: return {"asx": asx_plan()}\n    if ASIA_MKT:\n        import asia_hunt48 as AH; return {ASIA_MKT: AH.clearance_plan(ASIA_MKT)}\n')
rep('for r in ([] if ASX_ONLY else top):', 'for r in ([] if (ASX_ONLY or ASIA_MKT) else top):')
rep('        if IC.borrow(ys) is None: b["unknowns"].append("borrow fee/availability")\n',
    '        if ASIA_MKT and sw.get("venue") == ASIA_MKT: b["short_interest"].update(borrow_fee=(fnum(sw.get("borrow_pa")) if sw.get("borrow_pa") else U), borrow_ok=str(sw.get("shortable")) == "True", borrow_note=str(sw.get("short_src")))\n'
    '        elif IC.borrow(ys) is None: b["unknowns"].append("borrow fee/availability")\n')
rep('    if sw.get("venue") == "asx" and sw.get("sector"): b["sector_key"] = "ASX:" + sw["sector"]',
    '    if sw.get("venue") == "asx" and sw.get("sector"): b["sector_key"] = "ASX:" + sw["sector"]\n'
    '    if ASIA_MKT and sw.get("venue") == ASIA_MKT and sw.get("sector"): b["sector_key"] = ASIA_MKT.upper() + ":" + sw["sector"]   # same key as asia_hunt48 1/sector')
old = '''    px = e; q = max(s["shares"], 1); bsym = (tkey(s) + ".AX") if asx and not tkey(s).endswith(".AX") else tkey(s)
    comm = (2 * IC.asx_side_aud(q, px)) if asx else (IC.us_side(q, px, "buy") + IC.us_side(q, px, "sell"))
    cost = (2 * (ASX_SLIP if asx else SLIP) * px + comm / q + (0 if lg else IC.borrow_pa(bsym, "asx" if asx else "us") * px * 3 / 365))
    if not lg and not IC.has_borrow(bsym, q, "asx" if asx else "us"): ok = False; why.append("no borrow (IBKR: no/insufficient shares available)")
    elif not lg: why.append(f"IBKR borrow {IC.borrow_pa(bsym, 'asx' if asx else 'us'):.2%}/yr")
'''
new = '''    am = ASIA_MKT if (ASIA_MKT and sw.get("venue") == ASIA_MKT) else None
    px = e; q = max(s["shares"], 1); bsym = (tkey(s) + ".AX") if asx and not tkey(s).endswith(".AX") else tkey(s)
    if am:      # Asia: IBKR schedule per market (asia_markets.side_cost: HK stamp both sides, KR/TW sell tax) + 10bp/side + half tick/side + borrow
        import asia_markets as AMK
        bpa = fnum(sw.get("borrow_pa")) or 0.0
        cost = (AMK.side_cost(am, q, px, "buy") + AMK.side_cost(am, q, px, "sell")) / q + 2 * ASX_SLIP * px + AMK.tick(am, px) + (0 if lg else bpa * px * 3 / 365)
        if not lg and not (str(sw.get("shortable")) == "True" and (fnum(sw.get("borrow_avail")) or 0) >= q): ok = False; why.append("no confirmed borrow / not shortable (" + str(sw.get("short_src"))[:80] + ")")
        elif not lg: why.append(f"IBKR borrow {bpa:.2%}/yr")
    else:
        comm = (2 * IC.asx_side_aud(q, px)) if asx else (IC.us_side(q, px, "buy") + IC.us_side(q, px, "sell"))
        cost = (2 * (ASX_SLIP if asx else SLIP) * px + comm / q + (0 if lg else IC.borrow_pa(bsym, "asx" if asx else "us") * px * 3 / 365))
        if not lg and not IC.has_borrow(bsym, q, "asx" if asx else "us"): ok = False; why.append("no borrow (IBKR: no/insufficient shares available)")
        elif not lg: why.append(f"IBKR borrow {IC.borrow_pa(bsym, 'asx' if asx else 'us'):.2%}/yr")
'''
rep(old, new)
rep('    if asx and adva:      # ASX book', '    if (asx or am) and adva:      # ASX/Asia book')
rep('        part = s["shares"] * px / adva; lim = 0.02 if adva < ASX_THIN_ADV else 0.05',
    '        part = s["shares"] * px * ((fnum(sw.get("fx_aud")) or 1) if am else 1) / adva; lim = 0.02 if adva < ASX_THIN_ADV else 0.05')
rep('    if ASX_ONLY: atomic(os.path.join(OUT, "asx_plan_input.json"), plans["asx"])\n',
    '    if ASX_ONLY: atomic(os.path.join(OUT, "asx_plan_input.json"), plans["asx"])\n    if ASIA_MKT: atomic(os.path.join(OUT, f"{ASIA_MKT}_plan_input.json"), plans[ASIA_MKT])\n')
rep('        audusd = 1.0 if book == "asx" else L.get("audusd_armed", 0.70);',
    '        audusd = 1.0 if book == "asx" else ((__import__("asia_hunt48").local_per_aud(book) or 1.0) if ASIA_MKT else L.get("audusd_armed", 0.70));')
rep('        if book == "asx": P = json.load(open(os.path.join(OUT, "asx_plan_input.json")))\n',
    '        if book == "asx": P = json.load(open(os.path.join(OUT, "asx_plan_input.json")))\n        if ASIA_MKT and book == ASIA_MKT: P = json.load(open(os.path.join(OUT, f"{ASIA_MKT}_plan_input.json")))\n')
open(p, "w").write(s); print("patched OK")
