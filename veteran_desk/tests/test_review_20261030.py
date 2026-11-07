"""tfo_review_20261030 (Brett 19:34 WITA 30 Oct 2026, PAPER, VD + THF): flexible A 1-2%, override half size, 15 bps collar, pre-arm futures units/fee filter/margin
headroom, fractional Kraken units, beta-check fail closed, THF cap 40% no 750k min, half-risk at 1% floor, GPT HIGHs (pre-arm bars, ledger-before-queue, gap stops,
no 0.65 AUDUSD), learning loop, VD stern review, Dex rules, forward journal path."""
import datetime as dt, json, os, sys, tempfile
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, gate_v2 as G, lesson_loop as LL, thf_review as TR, desk as DK
from veteran_desk.tests.test_gate_v2 import g, P0
# 1 flexible A
def test_flexible_a_band():
    assert BK.risk_plan(500000, "A", seat_pct=.01)["pct"] == .01 and BK.risk_plan(500000, "A", seat_pct=.015)["pct"] == .015
    assert BK.risk_plan(500000, "A", seat_pct=.03)["pct"] == .02            # A capped at 2%
    assert BK.risk_plan(500000, "A", seat_pct=.008)["pct"] == .01   # tfo_review_fix_20261031: A never below 1%, never rejected for it
    assert BK.risk_plan(500000, "A+")["pct"] == .04 and BK.risk_plan(500000, "A+", mult=9)["pct"] == .05
    assert BK.risk_plan(500000, "A", floor=BK.RISK_CRYPTO_MIN, seat_pct=.02)["pct"] == .01   # Kai band unchanged
def test_gate_takes_seat_risk_pct():
    r = g(P0(risk_pct=1.5)); assert r["passed"] and abs(r["risk_pct"] - .015) < 1e-9 and abs(r["seat_risk_pct"] - .015) < 1e-9
# 2 override half size
def test_override_half_size():
    # t2808u: the override half is a real cut. It is not lifted back to 1%.
    r = g(P0(grade="A", grade_seat="A+")); assert r["override"] and any("override" in w for w in r["warnings"]) and abs(r["risk_pct"] - .005) < 1e-9
# 3 slip collar constant
def test_slip_collar_15bps(): assert BK.SLIP_COLLAR_BPS == 15.0 and BK.MAX_SLIP == .0015
# 4 pre-arm futures units + 30% margin headroom
def test_prearm_futures_multiplier_mismatch():
    e = dict(ticker="GC=F", entry=2400.0, target=2500.0, multiplier=10)
    assert "futures_units" in (BK.prearm_check(e, 100, 2400.0, None) or "")
def test_prearm_futures_unknown_spec_fails_closed():
    assert "no contract spec" in (BK.prearm_check(dict(ticker="ZZZ=F", entry=10.0, target=12.0), 1, 10.0, None) or "")
def test_margin_headroom_constant(): assert BK.MARGIN_HEADROOM == 1.3
# 5 fee filter
def test_fee_filter():
    assert BK.fee_filter("XOM", 100.0, 110.0) is None
    assert "fee_filter" in BK.fee_filter("XOM", 100.0, 100.01)
    r = g(P0(target=P0()["entry"] * 1.00001)); assert not r["passed"]
# 6 Option C on legacy grade (tags)
def test_pos_grade_from_tags(): assert BK.pos_grade(dict(tags={"strategy": "veteran:x grade A"})) == "A"
# crypto fractional units (head-trader ruling)
def test_crypto_fractional_units():
    assert BK.crypto_units(0.123456789) == 0.12345678
    p = dict(ticker="KRAKEN:SOLUSD", venue="CRYPTO", shares=0.3); assert BK._can_half(p) and BK._half(p) == 0.15
# beta-check exception rejects
def test_us_beta_error_is_hard():
    r = g(P0(ticker="XOM"), th=dict(us_beta_cur_usd="bad"))
    assert not r["passed"] and any("us_beta check error" in x for x in r["reasons"])
# THF cap: 40% of equity, no US$750k minimum
def test_thf_beta_cap_no_min(monkeypatch):
    monkeypatch.setenv("THF_DESK", "1"); monkeypatch.setattr(BK, "book_equity_aud", lambda D: 1_000_000.0)
    assert abs(BK.us_beta_cap_usd({}, .65) - BK.US_BETA_CAP_PCT * 1_000_000 * .65) < 1e-6
# THF/VD half risk at the 1% floor
def test_half_risk_a_allowed_at_1pct(monkeypatch):
    monkeypatch.setattr(TR, "size_mult", lambda s: .5); monkeypatch.setattr(BK, "seat_equity", lambda s: 500000.0)
    monkeypatch.setattr(G, "gate", lambda *a, **k: dict(passed=True, reasons=[], warnings=[], risk_pct=.015, risk_aud=7500, size_mult=1.0))
    r = DK.risk_gate(dict(_seat="energy", ticker="XOM", grade="A"), None, None, None, None, 0)
    assert r["passed"] and abs(r["risk_pct"] - .01) < 1e-9 and r["risk_aud"] == 5000
# GPT HIGH: no fills/exits from bars before the order was armed
def test_post_extremes_ignores_pre_arm_bars():
    m = dict(last=100.0, hi=105.0, lo=95.0, window_start="2026-10-30T10:00:00+00:00")
    assert BK.post_extremes(m, "2026-10-30T11:00:00+00:00")[:2] == (100.0, 100.0)
    assert BK.post_extremes(m, "2026-10-30T09:00:00+00:00")[:2] == (105.0, 95.0)
# GPT HIGH: gap stop fills at the gap price
def test_gap_stop_px():
    assert BK.gap_stop_px(dict(stop=100.0, side="LONG"), 97.0) == 97.0 and BK.gap_stop_px(dict(stop=100.0, side="LONG"), 101.0) == 100.0
    assert BK.gap_stop_px(dict(stop=100.0, side="SHORT"), 104.0) == 104.0
# GPT HIGH: ledger saved before the queue
def test_ledger_saved_before_queue():
    src = open(BK.__file__).read(); i = src.index("ledger committed BEFORE the queue"); j = src.index("FG.update(f, queue_path)")
    assert i < j
# GPT HIGH: no 0.65 fallback; stale -> blocked; fresh last-good used
def test_audusd_last_good_and_stale(monkeypatch):
    monkeypatch.setattr(BK, "AUDUSD_CACHE", None); BK._AU_LAST.clear()
    t0 = dt.datetime(2026, 10, 30, 10, tzinfo=dt.timezone.utc)
    assert BK.audusd(px_fn=lambda s: dict(last=.66), t=t0) == .66
    assert BK.audusd(px_fn=lambda s: None, t=t0 + dt.timedelta(minutes=30)) == .66
    with pytest.raises(BK.FxUnavailable): BK.audusd(px_fn=lambda s: None, t=t0 + dt.timedelta(minutes=90))
    BK._AU_LAST.clear()
# learning loop
def test_lesson_loop_close_stats_prompt():
    d = tempfile.mkdtemp()
    B = dict(closed=[dict(id="VET-1", fill=10, net_aud=1500, gross_aud=1600, cost_aud=100)])
    L = LL.on_close("energy", dict(id="VET-1", ticker="XOM", side="LONG", grade="A", risk_aud=1000, peak_fav_R=2.0, slippage_bps=4, exit_reason="t2", override=True), B, d)
    assert L["R_banked"] == 1.5 and L["R_peak"] == 2.0 and L["override"] and L["win"]
    assert LL.on_close("energy", dict(id="VET-1"), B, d) is None   # once per trade
    LL.on_close("banks", dict(id="VET-2", ticker="BAC", side="SHORT", grade="A", risk_aud=1000, exit_reason="stop"), dict(closed=[dict(id="VET-2", fill=1, net_aud=-1000, gross_aud=-950, cost_aud=50)]), d)
    S = LL.read_stats(d)["seats"]; assert S["energy"]["win_rate"] == 1.0 and S["banks"]["avg_R"] == -1.0
    pb = LL.prompt_block("energy", d); assert "XOM" in pb and "banks LOSS BAC" in pb and "lessons_respected" in pb
def test_prompt_schema_has_risk_and_lessons():
    src = open(DK.__file__).read(); assert '\\"risk_pct\\"' in src and '\\"lessons_respected\\"' in src and "_ll_block(seat)" in src
def test_missing_lessons_respected_halves():
    # t2808u: the missing-lessons half stays a half. It is not lifted back to 1%.
    r = g(P0(_ll_required=True)); assert any("lessons_respected missing" in w for w in r["warnings"]) and abs(r["risk_pct"] - .005) < 1e-9
# losing-seat rule on VD too (temp dir under pytest)
def test_vd_stern_review(monkeypatch):
    monkeypatch.delenv("THF_DESK", raising=False); d = tempfile.mkdtemp(); monkeypatch.setenv("SEAT_REVIEW_DIR", d)
    bp = os.path.join(d, "book.json"); json.dump(dict(seats=dict(energy=dict(seed_aud=500000, equity_aud=490000, closed=[]))), open(bp, "w")); monkeypatch.setenv("SEAT_REVIEW_BOOK", bp)
    assert TR.enabled() and "STERN REVIEW (Veteran Desk" in TR.prompt_line("energy", "r1")
    for i in (2, 3): TR.prompt_line("energy", "r%d" % i)
    assert TR.size_mult("energy") == .5 and "1% floor - allowed" in TR.prompt_line("energy", "r4").replace("%%", "%")
    assert os.path.dirname(TR._ROUNDS()) == d
# Dex
def test_dex_both_dissent_pass():
    r = g(P0(_seat="trend"), ch=dict(call="SHORT"), da=dict(factual_kill=True, fact="x")); assert not r["passed"] and any("both debate models dissent" in x for x in r["reasons"])
def test_dex_one_dissent_half():
    r = g(P0(_seat="trend"), ch=dict(call="SHORT")); assert any("one debate model dissents" in w for w in r["warnings"])
def test_dex_stop_1p5_atr():
    c = P0()["entry"]; a = G.arrays(__import__("veteran_desk.tests.test_gate_v2", fromlist=["HIST"]).HIST[0])["atr"][-1]
    r = g(P0(_seat="trend", stop=c - 1.0 * a, target=c + 4 * a)); assert any("1.5 ATR" in x for x in r["reasons"])
def test_dex_smt_cooldown_floor():
    r = G.gate(dict(P0(), ticker="SMT.L", _seat="trend"), None, None, None, dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc), 0, hist=__import__("veteran_desk.tests.test_gate_v2", fromlist=["HIST"]).HIST, env=dict(book=([], "OK"), firm="NORMAL"))
    assert any("SMT.L re-entry" in x for x in r["reasons"])
# forward journal follows the test redirect
def test_fwd_dir_follows_sd(monkeypatch):
    d = tempfile.mkdtemp(); monkeypatch.delenv("THF_DESK", raising=False); monkeypatch.setattr(DK, "SD", d); assert DK._fwd_dir() == d
