import sys
p = sys.argv[1]; s = open(p).read()
def rep(old, new):
    global s
    assert s.count(old) == 1, (old[:70], s.count(old)); s = s.replace(old, new)
rep('                if why: t.update(status="BENCH", bench_note="IBKR cost gate: " + why)',
    '                if why: t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note="IBKR cost gate: " + why)   # sticky demotion (2026-10-27 fix)')
rep('''            t.update(status="BENCH", bench_note="desk clearance FAIL: " + "; ".join(c.get("why") or [f"R={c.get('R')} B={c.get('B')} S={c.get('S')}"])[:200])''',
    '''            t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note="desk clearance FAIL: " + "; ".join(c.get("why") or [f"R={c.get('R')} B={c.get('B')} S={c.get('S')}"])[:200])''')
rep('''        if len(live()) >= MAX_OPEN: break
        c = CL.get(t["sym"])''',
    '''        if len(live()) >= MAX_OPEN: break
        # 2026-10-27 FIX (Brett): a BENCH that was ever demoted (cost gate / clearance FAIL / audit) or already promoted once is NEVER re-armed by the engine
        if t.get("demoted_utc") or t.get("audit_bench_reason") or t.get("promoted_utc"):
            t.setdefault("bench_note", "demoted - never re-armed (needs a fresh record)"); continue
        c = CL.get(t["sym"])''')
open(p, "w").write(s); print("asx patched OK")
