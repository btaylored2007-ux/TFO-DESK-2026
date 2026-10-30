"""Learning loop v2 (tfo_review_20261030; Brett 19:34 WITA 30 Oct 2026 "let the traders learn from the mistakes, wins etc."). PAPER, VD + THF.
On every full close book.tick calls on_close(): one auto-lesson row -> <state dir>/lessons/<seat>.jsonl (what worked/failed, R peak vs R banked,
slippage, fees, grade, override) and refreshes <state dir>/seat_stats.json (win rate, avg R, giveback ratio, slippage, fee drag) shown on both dashes.
prompt_block(seat) feeds each wake: the seat's last 5 lessons + desk-wide lessons from OTHER seats' biggest wins/losses. Advisory; never loosens a rail."""
import datetime as dt, json, os
WITA = dt.timezone(dt.timedelta(hours=8))
def _f(x, d=0.0):
    try: return float(x) if x is not None else d
    except Exception: return d
def _root(i): return str(i or "").split("-T1")[0].split("-T2")[0].split("-DXH")[0]
def trade_rows(B, pid):
    r = _root(pid); return [c for c in (B.get("closed") or []) if _root(c.get("id")) == r and c.get("fill") is not None]
def lesson_for(seat, p, B):
    rows = trade_rows(B, p.get("id")) or [p]
    net = sum(_f(c.get("net_aud")) for c in rows); gross = sum(_f(c.get("gross_aud")) for c in rows)
    fees = sum(_f(c.get("cost_aud")) for c in rows) + max(0.0, gross - net - sum(_f(c.get("cost_aud")) for c in rows))
    r0 = _f(p.get("risk_aud")) or 1.0
    banked = net / r0 if r0 else 0.0; peak = max(_f(p.get("peak_fav_R")), _f(p.get("dex_peak_net_R")), _f(p.get("revguard_peak_net_R")), _f(p.get("ts_peak_net_R")), banked)
    gb = (peak - banked) / peak if peak > 0 else 0.0
    slip = _f(p.get("slippage_bps")); win = net > 0
    why = p.get("exit_reason")
    if win and gb <= 0.35: what = "WORKED: plan held - banked %.2fR of a %.2fR peak via %s" % (banked, peak, why)
    elif win: what = "PARTLY WORKED: won %.2fR but gave back %.0f%% of a %.2fR peak (%s) - bank earlier / tighter trail" % (banked, gb * 100, peak, why)
    elif peak >= 1.0: what = "FAILED AFTER WORKING: reached %.2fR then closed %.2fR (%s) - Option C half-bank at 1R should have locked it" % (peak, banked, why)
    else: what = "FAILED: never worked (peak %.2fR), closed %.2fR via %s - entry/thesis wrong; check setup quality and timing" % (peak, banked, why)
    if slip > 10: what += "; slippage %.1f bps hurt" % slip
    if gross and fees / max(abs(gross), 1e-9) > 0.25: what += "; fees ate %.0f%% of gross" % (fees / abs(gross) * 100)
    if p.get("override"): what += "; this was an OVERRIDE (gate graded below the ask)"
    return dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), wita=dt.datetime.now(WITA).strftime("%F %H:%M WITA"), seat=seat, id=_root(p.get("id")), ticker=p.get("ticker"), side=p.get("side"),
                grade=p.get("grade"), override=bool(p.get("override")), exit_reason=why, net_aud=round(net, 2), gross_aud=round(gross, 2), fees_aud=round(fees, 2), risk_aud=round(r0, 2),
                R_banked=round(banked, 3), R_peak=round(peak, 3), giveback_ratio=round(gb, 3), slippage_bps=round(slip, 1), win=win, lesson=what)
def on_close(seat, p, B, sd, t=None):
    L = lesson_for(seat, p, B); d = os.path.join(sd, "lessons"); os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, seat + ".jsonl")
    try: seen = {json.loads(x).get("id") for x in open(fp).read().splitlines() if x.strip()}
    except Exception: seen = set()
    if L["id"] in seen: return None
    open(fp, "a").write(json.dumps(L) + "\n"); write_stats(sd); return L
def _rows(sd, seat):
    try: return [json.loads(x) for x in open(os.path.join(sd, "lessons", seat + ".jsonl")).read().splitlines() if x.strip()]
    except Exception: return []
def stats_for(rows):
    n = len(rows)
    if not n: return dict(trades=0)
    g = sum(r["gross_aud"] for r in rows)
    return dict(trades=n, win_rate=round(sum(r["win"] for r in rows) / n, 3), avg_R=round(sum(r["R_banked"] for r in rows) / n, 3),
                giveback_ratio=round(sum(r["giveback_ratio"] for r in rows if r["R_peak"] > 0) / max(1, sum(1 for r in rows if r["R_peak"] > 0)), 3),
                avg_slippage_bps=round(sum(r["slippage_bps"] for r in rows) / n, 1), fees_aud=round(sum(r["fees_aud"] for r in rows), 2),
                fee_drag_pct_of_gross=round(100 * sum(r["fees_aud"] for r in rows) / abs(g), 1) if g else None, net_aud=round(sum(r["net_aud"] for r in rows), 2),
                overrides=sum(1 for r in rows if r.get("override")))
def write_stats(sd):
    d = os.path.join(sd, "lessons"); seats = sorted(f[:-6] for f in os.listdir(d) if f.endswith(".jsonl")) if os.path.isdir(d) else []
    S = dict(updated_wita=dt.datetime.now(WITA).strftime("%F %H:%M WITA"), rule="tfo_review_20261030 learning loop", seats={s: stats_for(_rows(sd, s)) for s in seats})
    tmp = os.path.join(sd, "seat_stats.json.tmp.%d" % os.getpid()); json.dump(S, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(sd, "seat_stats.json")); return S
def read_stats(sd):
    try: return json.load(open(os.path.join(sd, "seat_stats.json")))
    except Exception: return dict(seats={})
def prompt_block(seat, sd, n=5, k=2):
    mine = _rows(sd, seat)[-n:]; others = []
    d = os.path.join(sd, "lessons")
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        if f.endswith(".jsonl") and f[:-6] != seat: others += _rows(sd, f[:-6])
    wins = sorted([r for r in others if r["win"]], key=lambda r: -r["net_aud"])[:k]; losses = sorted([r for r in others if not r["win"]], key=lambda r: r["net_aud"])[:k]
    st = stats_for(_rows(sd, seat))
    s = "\nYOUR LAST %d TRADE LESSONS (auto-written at each close):\n" % len(mine) + ("".join("- %s %s %s grade %s%s: %s (net A$%.0f, %.2fR banked / %.2fR peak, slip %.1f bps, fees A$%.0f)\n" % (r["wita"], r["ticker"], r["side"], r["grade"], " OVERRIDE" if r.get("override") else "", r["lesson"], r["net_aud"], r["R_banked"], r["R_peak"], r["slippage_bps"], r["fees_aud"]) for r in mine) or "- none yet\n")
    if st.get("trades"): s += "YOUR STATS: %d trades, win %.0f%%, avg %.2fR, giveback %.0f%%, slip %.1f bps, fee drag %s%%\n" % (st["trades"], st["win_rate"] * 100, st["avg_R"], st["giveback_ratio"] * 100, st["avg_slippage_bps"], st.get("fee_drag_pct_of_gross"))
    if wins or losses: s += "DESK LESSONS (other seats' biggest wins/losses):\n" + "".join("- %s %s %s %s: %s\n" % (r["seat"], "WIN" if r["win"] else "LOSS", r["ticker"], "A$%.0f" % r["net_aud"], r["lesson"]) for r in wins + losses)
    s += "In your JSON add \"lessons_respected\": one or two sentences on how this proposal respects the lessons above (required).\n"
    return s
