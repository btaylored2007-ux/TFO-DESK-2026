import json, os
from veteran_desk import desk as D
def _mk(tmp, monkeypatch):
    for dk in ("veteran_desk", "thf"):
        d = tmp / "state" / dk / "seat_memory"; d.mkdir(parents=True)
        m = {"date_range": ["2000-01-03", "2026-10-30"], "setups": {"breakout20": {"n": 100, "win_rate": 0.45, "avg_r": 0.1}},
             "vol_regime": [{"sym": "XOM", "pctile_5y": 0.5}], "earnings_reaction": {"n": 0}}
        (d / "energy.json").write_text(json.dumps(m)); (d / "energy.md").write_text("# Core memory energy\n" + "x" * 5000)
    monkeypatch.setattr(D, "BASE", str(tmp))
def test_summary_mode_is_short(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch); s = D._seat_mem({"seat": "energy", "engine": "codex"}, desk="veteran_desk", full=False)
    assert "CORE MEMORY" in s and "breakout20" in s and len(s) <= D.SEAT_MEM_SUMMARY_MAX
def test_thf_gets_full_memory(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch); s = D._seat_mem({"seat": "energy", "engine": "codex"}, desk="thf", full=True)
    assert "legacy desk records excluded" in s and "breakout20" in s and "Core memory energy" not in s
def test_missing_memory_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "BASE", str(tmp_path)); assert D._seat_mem({"seat": "nope", "engine": "codex"}) == ""
