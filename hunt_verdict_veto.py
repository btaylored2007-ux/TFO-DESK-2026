#!/usr/bin/env python3
"""HUNT VERDICT VETO HOOK (PAPER ONLY) - Brett approval 2026-10-27 20:56 WITA.
GPT/Claude verdict veto for the ASX (asx_hunt48.py) and Asia/LSE (asia_hunt48.py) hunt books.

Reuses the SAME verdict files the US hunt48 books already use (written by hunt_clearance.py, no new format):
  state/clearance/<YYYYMMDD WITA>/<book>/external_reviews.json   {book: {SYM: {GPT, CLAUDE, GPT_reason, ...}}}
  state/clearance/<YYYYMMDD WITA>/<book>/summary.json            {books: {book: {SYM: {internal, final: CLEARED|BLOCKED, ...}}}}
(US books: finalize() turns these into cleared_plan.json; a BLOCKED / GPT FAIL name is cut and never armed.)

Rule (fail closed, can only veto, never rescue):
  * finalize final == BLOCKED                  -> veto
  * GPT == FAIL or CLAUDE == FAIL              -> veto (even if summary.json was not re-finalized after the verdict landed)
  * name missing from the verdict files        -> veto ("no GPT/Claude verdict")
  * either file missing / unreadable / not today's WITA date / older than MAX_AGE_H -> veto ALL ("stay out")
A veto only blocks ARMING (BENCH->ARMED) and demotes a not-yet-live ARMED; it never touches FILLED positions. No orders, no keys.
"""
import datetime as dt, json, os
WITA = dt.timezone(dt.timedelta(hours=8))
MAX_AGE_H = 24.0
FAIL_WORDS = ("FAIL", "FAILED", "VETO", "BLOCK", "BLOCKED", "REJECT")

def wday(now): return now.astimezone(WITA).strftime("%Y%m%d")

def load(book, now, base, day=None):
    """-> dict(ok, why, rows={SYM: {final, GPT, CLAUDE, GPT_reason, CLAUDE_reason}}, src, date). ok False => every name vetoed.
    day (YYYYMMDD WITA) defaults to TODAY (WITA); files must also be <= MAX_AGE_H old by mtime."""
    day = day or wday(now); d = os.path.join(base, "state", "clearance", day, book)
    ext_p = os.path.join(d, "external_reviews.json"); sum_p = os.path.join(d, "summary.json")
    out = dict(ok=False, why=None, rows={}, src=d, date=day)
    for p in (ext_p, sum_p):
        if not os.path.exists(p): out["why"] = f"verdict file missing ({os.path.relpath(p, base)}) -> stay out"; return out
        age_h = (now.timestamp() - os.path.getmtime(p)) / 3600.0
        if age_h > MAX_AGE_H: out["why"] = f"verdict file stale ({os.path.basename(p)} {age_h:.1f}h old > {MAX_AGE_H:g}h) -> stay out"; return out
    try:
        ext = json.load(open(ext_p)).get(book) or {}      # hunt_clearance only lists desk-PASS names here; none -> {} (names then vetoed one by one)
        summ = json.load(open(sum_p))["books"].get(book)
    except Exception as e:
        out["why"] = f"verdict file unreadable ({type(e).__name__}) -> stay out"; return out
    if not isinstance(ext, dict) or not isinstance(summ, dict):
        out["why"] = f"verdict files have no '{book}' book -> stay out"; return out
    rows = {}
    for s in set(ext) | set(summ):
        e, m = ext.get(s) or {}, summ.get(s) or {}
        rows[s] = dict(final=m.get("final"), internal=m.get("internal"), GPT=e.get("GPT"), CLAUDE=e.get("CLAUDE"),
                       GPT_reason=e.get("GPT_reason"), CLAUDE_reason=e.get("CLAUDE_reason"), in_summary=s in summ, in_external=s in ext)
    out.update(ok=True, rows=rows); return out

def for_armed(book, now, base, armed_day, today=None):
    """verdicts that govern an already-ARMED (unfilled) trade: today's set if present and fresh, else the set from the day it was
    cleared/armed (an evening arm for the next open keeps the verdict it was armed on, while that file is <= MAX_AGE_H old).
    Neither usable -> ok False -> veto (stay out)."""
    V = today if today is not None else load(book, now, base)
    if V.get("ok") or not armed_day or armed_day == V.get("date"): return V
    Va = load(book, now, base, armed_day)
    return Va if Va.get("ok") else V

# 2026-10-28 07:36 Brett OVERRIDE: GPT/Claude verdicts are ADVISORY ONLY (recorded, never block). Only desk/finalize CLEARED + fresh files gate.
ADVISORY = True
def ai_tags(sym, V):
    """-> {ai_gpt: {verdict, reason}, ai_claude: {verdict, reason}} for recording on a trade."""
    r = ((V or {}).get("rows") or {}).get(sym) or {}
    return dict(ai_gpt=dict(verdict=r.get("GPT") or "PENDING", reason=str(r.get("GPT_reason") or "")[:300]),
                ai_claude=dict(verdict=r.get("CLAUDE") or "PENDING", reason=str(r.get("CLAUDE_reason") or "")[:300]))
def _bad(v): return str(v or "").strip().upper() in FAIL_WORDS

def veto(sym, V):
    """None if the name may arm, else a short reason string. V = load(...).
    2026-10-29 18:00 WITA Brett "Chat gpt can be warning not a block": item 8 reverted -- GPT FAIL/PENDING is an advisory
    WARNING (see warn(); recorded on the ticket as ai_gpt), never a block. finalize != CLEARED still blocks. Claude retired/ignored."""
    if not V or not V.get("ok"): return "GPT veto hook: " + str((V or {}).get("why") or "no verdicts -> stay out")
    r = V["rows"].get(sym)
    if not r or not r.get("in_summary") or not r.get("in_external"): return f"GPT veto hook: no verdict for {sym} in {V['date']} clearance -> stay out"
    bits = []
    if str(r.get("final") or "").upper() != "CLEARED":
        bits.append(f"finalize {r.get('final') or 'NOT FINALIZED'} (GPT={r.get('GPT')})")
    # desk-PENDING is also reflected when final is missing; callers still check internal==PASS separately
    return ("GPT veto hook: " + "; ".join(bits))[:240] if bits else None

def warn(sym, V):
    """Advisory GPT warning string for the ticket (None if GPT PASS / no row). Never blocks (Brett 2026-10-29 18:00)."""
    r = ((V or {}).get("rows") or {}).get(sym) or {}
    gpt = str(r.get("GPT") or "PENDING").upper()
    if r and (gpt in ("PENDING", "PENDING_LOGIN", "") or _bad(r.get("GPT"))):
        return ("GPT advisory warning: " + str(r.get("GPT_reason") or r.get("GPT") or "PENDING"))[:200]
    return None

def summary(V):
    """compact status block for the book status JSON / dashboard."""
    if not V: return dict(ok=False, why="not loaded")
    rows = V.get("rows", {})
    return dict(ok=V.get("ok"), why=V.get("why"), date_wita=V.get("date"),
                cleared=sorted(s for s in rows if veto(s, V) is None),
                gpt_fail=sorted(s for s, r in rows.items() if _bad(r.get("GPT"))), claude_fail=sorted(s for s, r in rows.items() if _bad(r.get("CLAUDE"))),
                blocked=sorted(s for s, r in rows.items() if str(r.get("final") or "").upper() == "BLOCKED"),
                rule="arm only if finalize CLEARED; GPT FAIL = advisory warning, not a block (Brett 2026-10-29 18:00; Claude retired); missing/stale verdict files -> stay out (hunt_verdict_veto.py)")
