"""Push traders.

The normal path is the existing desk wake (AI hunt) for seats whose brain is up.
Every wake still computes the code grade first. The brain is merged only when
the caller reports it arrived inside 25 seconds.

A degraded seat does not get an AI hunt. It gets the code-grade report and may
be offered an A+/A at half size through caps.screen_arm. B names go to the
head-trader queue. Nothing here calls a model itself.
"""
import os, subprocess, sys

from thf_guardian.caps import screen_arm
from thf_guardian.gradewake import merge_wake
from thf_guardian.seats import HUNT_SEATS, SEAT_LABEL
from thf_guardian.util import read_json

BRAIN_WINDOW_SEC = 25


def classify(text):
    raw = (text or "").lower()
    urgent = "urgent" in raw or "asap" in raw or "now" in raw or "high" in raw
    urgency = "high" if urgent else ("low" if "low" in raw else "normal")
    return urgency


def plan(seats, modes, packets, book, now, brain_window_sec=BRAIN_WINDOW_SEC):
    """packets[seat] = {rows, brain, brain_sec, universe, marks, max_notional, base_pct}.

    brain_sec is how long the brain took. Over the window, brain_in_time is false
    and the code grade is used. The function does not wait.
    """
    reports = []
    ai_dispatch = []
    det_arms = []
    head = []
    for seat in seats:
        if seat not in HUNT_SEATS and seat != "head_of_desk":
            reports.append(dict(seat=seat, state="skipped", detail="not a hunt seat"))
            continue
        if seat == "head_of_desk":
            head.append(dict(seat=seat, text="push addressed Marcus; no hunt on the head seat"))
            reports.append(dict(seat=seat, state="queued_head", detail="head trader"))
            continue
        pkt = (packets or {}).get(seat) or {}
        in_time = pkt.get("brain") is not None and pkt.get("brain_sec") is not None and pkt["brain_sec"] <= brain_window_sec
        merged = merge_wake(pkt.get("rows") or [], pkt.get("brain"), in_time, pkt.get("universe"),
                            pkt.get("marks"), pkt.get("max_notional"))
        mode = (modes or {}).get(seat, {}).get("mode")
        best = []
        for row in merged["rows"]:
            why = row.get("why_no_wake") or (", ".join(row.get("floors_failed") or []) or None)
            if row.get("grade") == "B":
                why = why or "grade B does not self-arm"
            best.append(dict(ticker=row.get("sym") or row.get("ticker"), grade=row.get("grade"),
                             code_grade=row.get("code_grade"), R=row.get("r_fit") or row.get("R"),
                             source=row.get("source"), why_not_armed=why))
        report = dict(seat=seat, label=SEAT_LABEL.get(seat, seat), state="running", mode=mode,
                      swept=len(pkt.get("rows") or []), cleared=sum(1 for r in merged["rows"] if r.get("grade") in ("A", "A+", "B")),
                      graded=best, brain=merged["brain"], rejected=merged["rejected"], best=best[:5])
        if mode == "degraded":
            report["state"] = "deterministic"
            report["detail"] = "brain down; code grade only; A+/A at half size if the caps pass; no B"
            for row in merged["rows"]:
                if row.get("grade") == "B":
                    head.append(dict(seat="head_of_desk", from_seat=seat, ticker=row.get("sym") or row.get("ticker"),
                                     grade="B", text="B from %s while degraded; not armed" % seat))
                    continue
                if row.get("grade") not in ("A", "A+"):
                    continue
                proposal = dict(seat=seat, ticker=row.get("sym") or row.get("ticker"), side=row.get("side") or "LONG",
                                grade=row["grade"], venue=row.get("market") or row.get("venue") or "US",
                                sector=row.get("sector"), theme=row.get("theme"), source="code")
                decision = screen_arm(proposal, book or {}, modes, base_pct=pkt.get("base_pct") or (0.04 if row["grade"] == "A+" else 0.01))
                if decision["ok"]:
                    det_arms.append(dict(proposal=proposal, size_mult=decision["size_mult"], risk_pct=decision["risk_pct"]))
                else:
                    head.append(dict(seat="head_of_desk", from_seat=seat, ticker=proposal["ticker"], grade=row["grade"],
                                     text="; ".join(decision["reasons"])))
                    for b in best:
                        if b["ticker"] == proposal["ticker"]:
                            b["why_not_armed"] = "; ".join(decision["reasons"])
        elif mode == "ai":
            ai_dispatch.append(seat)
            report["detail"] = "brain up; code grade computed; existing desk wake will run"
        else:
            report["state"] = "no_brain_yet"
            report["detail"] = "no real call yet; not dispatching an AI hunt"
        reports.append(report)
    return dict(reports=reports, ai_dispatch=ai_dispatch, deterministic_arms=det_arms, head=head,
                brain_window_sec=brain_window_sec, paper_only=True)


def packets_from_top10(cfg, seats):
    """Last engine top10 on disk. No model call. A missing file is an empty sweep."""
    out = {}
    for seat in seats:
        doc = read_json(cfg.p("state", "vd_engine", "top10", seat + ".json"), {}) or {}
        rows = []
        for x in doc.get("top10") or []:
            rows.append(dict(x, sym=x.get("sym"), score=x.get("score") or 0, nz=x.get("pillars_gt1") or 0,
                             r_fit=x.get("r_fit") or 0, floors_failed=x.get("floors_failed") or []))
        out[seat] = dict(rows=rows, brain=None, brain_sec=None, universe=[r.get("sym") for r in rows])
    return out


def dispatch_ai(seats, cfg, popen=None):
    """Start the existing desk wake and return immediately. Does not wait for the brain."""
    seats = [s for s in seats if s in HUNT_SEATS]
    if not seats:
        return None
    popen = popen or subprocess.Popen
    cmd = [sys.executable, "-m", "veteran_desk.desk", "run", "--seats=" + ",".join(seats)]
    env = dict(os.environ, THF_DESK="1", PYTHONUNBUFFERED="1")
    popen(cmd, cwd=cfg.live, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cmd
