"""28 Oct 2026 Brett B3: hunt scripts honour DESK_BASE (state/static paths follow the base, never the live desk)."""
import importlib, os, sys, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def test_desk_base_env(tmp_path):
    code = "import hunt_cross_stage as a, hunt_shape_trial as b, hunt48_short_paper as c, value_hunt as d; print(a.BASE); print(b.D); print(c.LEDGER); print(d.OUT)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=dict(os.environ, DESK_BASE=str(tmp_path), PYTHONPATH=ROOT), capture_output=True, text=True, timeout=120)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    assert len(lines) >= 4, out.stderr[-500:]
    for l in lines[-4:]: assert l.startswith(str(tmp_path)), l
def test_clearance_source_honours_base():
    assert 'os.environ.get("DESK_BASE"' in open(os.path.join(ROOT, "hunt_clearance.py")).read()
