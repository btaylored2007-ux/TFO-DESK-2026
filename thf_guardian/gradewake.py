"""Code grade first. The brain is merged only if it answers inside the window.

The grade function is the same rule as veteran_desk.vd_engine.grade. The brain
cannot lift a code grade. Output that fails the schema, the universe, a price
sanity check, or the size cap is dropped and the code grade stands. This module
does not sleep and does not call a model; the caller says whether the brain
answered in time.
"""
GRADE_RANK = {"A+": 3, "A": 2, "B": 1, "C": 0, "reject": -1}
BRAIN_KEYS = ("ticker", "side", "grade", "entry", "stop", "target")
ENTRY_FAR = 0.03  # same 3% as gate_v2.TH entry_far_pct


def code_grade(score, nz, R, floors_failed):
    """Mirror of vd_engine.grade. floors_failed is a non-empty list or a true flag."""
    if floors_failed:
        return "B" if score >= 70 else "C"
    if score >= 92 and nz >= 3 and R >= 0.92:
        return "A+"
    if score >= 85 and nz >= 2 and R >= 0.85:
        return "A"
    if score >= 70:
        return "B"
    return "C"


def _lower(a, b):
    return a if GRADE_RANK.get(a, -1) <= GRADE_RANK.get(b, -1) else b


def validate_brain(idea, universe, mark, max_notional):
    """Return a list of problems. Empty means the idea may be merged."""
    problems = []
    if not isinstance(idea, dict):
        return ["brain output is not an object"]
    for key in BRAIN_KEYS:
        if idea.get(key) in (None, ""):
            problems.append("missing " + key)
    if problems:
        return problems
    if idea["grade"] not in GRADE_RANK:
        problems.append("grade %s not in schema" % idea["grade"])
    if str(idea["side"]).upper() not in ("LONG", "SHORT"):
        problems.append("side not LONG or SHORT")
    ticker = str(idea["ticker"]).upper()
    if universe is not None and ticker not in {str(s).upper() for s in universe}:
        problems.append("%s not in the seat universe" % ticker)
    try:
        entry, stop, target = float(idea["entry"]), float(idea["stop"]), float(idea["target"])
    except (TypeError, ValueError):
        return problems + ["entry/stop/target not numbers"]
    long_ = str(idea["side"]).upper() == "LONG"
    if long_ and not (stop < entry < target):
        problems.append("stop/target not on the long side of entry")
    if (not long_) and not (target < entry < stop):
        problems.append("stop/target not on the short side of entry")
    if mark is not None:
        try:
            last = float(mark)
        except (TypeError, ValueError):
            last = None
        if last and abs(entry / last - 1) > ENTRY_FAR:
            problems.append("entry %.4g is more than 3%% from last %.4g" % (entry, last))
    shares = idea.get("shares")
    px = entry
    if shares not in (None, ""):
        try:
            notion = abs(float(shares) * px)
        except (TypeError, ValueError):
            problems.append("shares not a number")
            notion = None
        if notion is not None and max_notional is not None and notion > float(max_notional) + 1e-6:
            problems.append("notional %.0f above cap %.0f" % (notion, float(max_notional)))
    if idea.get("risk_aud") is not None and idea.get("equity_aud"):
        try:
            pct = float(idea["risk_aud"]) / float(idea["equity_aud"])
        except (TypeError, ValueError, ZeroDivisionError):
            pct = None
        if pct is not None and pct > 0.05 + 1e-9:
            problems.append("risk %.2f%% above the 5%% cap" % (pct * 100))
    return problems


def grade_rows(rows):
    out = []
    for row in rows or []:
        g = code_grade(float(row.get("score") or 0), int(row.get("nz") or row.get("pillars_gt1") or 0),
                       float(row.get("r_fit") or row.get("R") or 0), row.get("floors_failed"))
        item = dict(row, code_grade=g, grade=g, source="code")
        out.append(item)
    return out


def merge_wake(rows, brain, brain_in_time, universe=None, marks=None, max_notional=None):
    """rows are engine features. brain is a list of ideas or None.

    brain_in_time False means the answer missed the 20–30s window. The code
    grade is returned either way. A late or invalid brain is not waited on and
    is not merged.
    """
    code = grade_rows(rows)
    by_ticker = {str(r.get("sym") or r.get("ticker")).upper(): r for r in code}
    if not brain_in_time or not brain:
        return dict(source="code", brain="late" if not brain_in_time else "none", rows=code, rejected=[])
    rejected = []
    for idea in brain:
        ticker = str((idea or {}).get("ticker") or "").upper()
        mark = (marks or {}).get(ticker)
        if isinstance(mark, dict):
            mark = mark.get("last")
        problems = validate_brain(idea, universe, mark, max_notional)
        base = by_ticker.get(ticker)
        if base is None:
            problems = problems + ["no code grade for %s" % ticker]
        if problems:
            rejected.append(dict(ticker=ticker, problems=problems))
            continue
        merged_grade = _lower(base["code_grade"], idea["grade"])
        base.update(side=str(idea["side"]).upper(), entry=float(idea["entry"]), stop=float(idea["stop"]),
                    target=float(idea["target"]), grade=merged_grade, brain_grade=idea["grade"],
                    source="merged", thesis=idea.get("thesis"))
        if idea.get("shares") is not None:
            base["shares"] = idea["shares"]
    return dict(source="merged" if any(r.get("source") == "merged" for r in code) else "code",
                brain="accepted" if not rejected else "partial", rows=code, rejected=rejected)
