"""Regenerate staged desk copies from LIVE files right before deploy (no clobbering of other workers' edits)."""
import ast, py_compile, sys
S = 'staged_fixes/datalayer_20261030/desks/'
F = sys.argv[1:]
BLK = '''try:   # tfo_datalayer_20261030 (Brett HARD RULE 20:59 WITA): yfinance daily bars -> EODHD first, Yahoo fallback
    import os as _os_pf, sys as _sys_pf; _sys_pf.path.insert(0, _os_pf.path.dirname(_os_pf.path.abspath(__file__)))
    from veteran_desk import paid_first as _PF; _PF.install()
except Exception: pass'''
for f in F:
    src = open(f).read(); t = ast.parse(src)
    node = next((n for n in t.body if isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.ImportFrom) and n.module == '__future__')), None)
    if node is None or 'tfo_datalayer_20261030' in src: print('SKIP', f); continue
    L = src.split('\n'); L.insert(node.end_lineno, BLK)
    open(S + f, 'w').write('\n'.join(L)); py_compile.compile(S + f, doraise=True)
    print('ok', f, 'after line', node.end_lineno)
