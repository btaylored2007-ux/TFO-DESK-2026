"""Separate evidence that a lesson was placed in a prompt, cited, or validated.

Inclusion is not citation. Citation is not a validated change. A sentence such as
"I learned" is not adoption. Reviews stay hypotheses. This does not edit risk rules.
"""
import datetime as dt
import json
import os
import re

TRADERS = ("banks", "energy", "essentials_utilities", "healthcare", "news_filings", "price_action",
           "tech_ai_infra", "trend", "macro", "fx_commodities", "crypto")
ADMINS = ("head_of_desk", "head_trader", "cfo", "software_engineer", "fixer", "blind_challenger", "devils_advocate")
SEATS = TRADERS + ADMINS
CLAIM = re.compile(r"\bi\s+learned\b", re.I)
REF = re.compile(r"ref=([A-Za-z0-9_-]{3,80})")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _identity(row):
    level = row.get("level")
    evidence = row.get("evidence") or {}
    if level == "included":
        return (row.get("seat"), level, row.get("task_id"), tuple(row.get("lesson_ids") or ()))
    if level in ("cited", "uncited"):
        return (row.get("seat"), level, row.get("task_id"), tuple(row.get("cited_ids") or ()))
    if level == "lessons_available":
        return (row.get("seat"), level, tuple(row.get("lesson_ids") or ()))
    if level == "validated":
        return (row.get("seat"), level, row.get("lesson_id"), evidence.get("check"))
    if level in ("outcome_link", "decision_link", "insufficient_data"):
        return (row.get("seat"), level, row.get("lesson_id"), row.get("reason"),
                row.get("task_id"), evidence.get("later_task_id"), evidence.get("trade_id"))
    return None


def append(path, row):
    """Append one trace row. A restart that repeats the same identity does not add a second copy."""
    for forbidden in ("reply", "prompt", "lesson", "text"):
        row.pop(forbidden, None)
    prior = load(path)
    key = _identity(row)
    if key is not None:
        for old in prior:
            if _identity(old) == key:
                return old
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def record_included(seat, lesson_ids, source, path, task_id=None):
    if seat not in SEATS:
        raise ValueError("unknown seat")
    if not str(task_id or "").strip():
        raise ValueError("included lesson needs a task_id")
    ids = [str(item) for item in lesson_ids if item and str(item) != "unreferenced"]
    if not ids:
        raise ValueError("included lesson needs a ref")
    row = dict(level="included", seat=seat, lesson_ids=ids, source=source, task_id=str(task_id),
               utc=_now(), validated=False, model_adoption=False)
    return append(path, row)


def record_admin_available(seat, lesson_ids, path):
    """An admin lesson file exists. This is not a trade, a citation, or an outcome."""
    if seat not in ADMINS:
        raise ValueError("admin availability is only for admin seats")
    ids = [str(item) for item in lesson_ids if item and str(item) != "unreferenced"]
    if not ids:
        raise ValueError("admin availability needs a lesson ref")
    row = dict(level="lessons_available", seat=seat, lesson_ids=ids, source="seat_brain.lessons",
               utc=_now(), validated=False, model_adoption=False, causal_edge=False)
    return append(path, row)


def interpret_reply(text, included_ids):
    """Cited ids must have been in the prompt. 'I learned' alone validates nothing."""
    included = set(included_ids or [])
    found = REF.findall(str(text or ""))
    cited = [item for item in found if item in included]
    claim = bool(CLAIM.search(str(text or "")))
    return dict(level="cited" if cited else "uncited", cited_ids=cited,
                claim_not_adoption=bool(claim and not cited), validated=False,
                model_adoption=False)


def record_validated(seat, lesson_id, path, evidence):
    """Only a caller with a separate check may write this. Never call it from model text."""
    if not evidence or not evidence.get("check"):
        raise ValueError("validated impact needs an explicit check name")
    row = dict(level="validated", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=True, model_adoption=False, evidence=dict(evidence))
    return append(path, row)


def record_insufficient(seat, lesson_id, path, reason, observed=None):
    """No causal claim. The reason says which observation was missing."""
    observed = dict(observed or {})
    for forbidden in ("reply", "prompt", "lesson", "text"):
        observed.pop(forbidden, None)
    row = dict(level="insufficient_data", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=False, model_adoption=False, reason=reason, observed=observed)
    return append(path, row)


def record_citation(seat, reply, path, task_id):
    """Cite only refs that this reply actually names and that this task included. The reply is not stored."""
    if not str(task_id or "").strip():
        return record_insufficient(seat, "missing", path, "missing_task_id")
    task_id = str(task_id)
    included = []
    for row in load(path):
        if row.get("seat") == seat and row.get("level") == "included" and row.get("task_id") == task_id:
            included = row.get("lesson_ids") or []
    if not included:
        return record_insufficient(seat, task_id, path, "no_inclusion_for_task", dict(task_id=task_id))
    parsed = interpret_reply(reply, included)
    row = dict(parsed, seat=seat, source="seat_brain.after_chat", task_id=task_id, utc=_now())
    return append(path, row)


def link_later_decision(seat, lesson_id, path, later_task_id, injection_task_id):
    """A later task used a lesson only when that earlier task included it and the reply cited it."""
    lesson_id, later_task_id, injection_task_id = str(lesson_id or ""), str(later_task_id or ""), str(injection_task_id or "")
    if not lesson_id or not later_task_id or not injection_task_id:
        return record_insufficient(seat, lesson_id or "missing", path, "missing_decision_ids")
    if later_task_id == injection_task_id:
        return record_insufficient(seat, lesson_id, path, "later_task_is_the_injection",
                                   dict(task_id=injection_task_id))
    rows = load(path)
    included = any(row.get("seat") == seat and row.get("level") == "included" and row.get("task_id") == injection_task_id
                   and lesson_id in [str(item) for item in (row.get("lesson_ids") or [])] for row in rows)
    cited = any(row.get("seat") == seat and row.get("task_id") == injection_task_id
                and lesson_id in [str(item) for item in (row.get("cited_ids") or [])] for row in rows)
    if not included or not cited:
        return record_insufficient(seat, lesson_id, path, "no_citation",
                                   dict(task_id=injection_task_id, later_task_id=later_task_id))
    row = dict(level="decision_link", seat=seat, lesson_id=lesson_id, task_id=injection_task_id,
               utc=_now(), validated=False, model_adoption=False, causal_edge=False,
               evidence=dict(link="decision", later_task_id=later_task_id,
                             injection_task_id=injection_task_id, causal_edge=False))
    return append(path, row)


def _seen(rows, seat, lesson_id, level):
    return any(row.get("seat") == seat and row.get("lesson_id") == lesson_id and row.get("level") == level for row in rows)


def observe_book_close(seat, lesson_id, path, observed):
    """Record the book net for a lesson that was injected, cited, and used in a later decision.

    A close does not set validated and does not claim a causal benefit. The configured
    rule text still says a book close is what validates a lesson. That sentence is not
    what this function does. See RULE_GAP.
    """
    if not isinstance(observed, dict) or observed.get("source") != "book_close":
        raise ValueError("a book close is required")
    if str(observed.get("trade_id") or "") != str(lesson_id) or "net_aud" not in observed:
        raise ValueError("the close must name this trade and its book net")
    rows = load(path)
    if _seen(rows, seat, lesson_id, "validated"):
        return next(row for row in rows if row.get("seat") == seat and row.get("lesson_id") == lesson_id and row.get("level") == "validated")
    lesson_id = str(lesson_id)
    injections = {row.get("task_id") for row in rows if row.get("seat") == seat and row.get("level") == "included"
                  and row.get("task_id") and lesson_id in [str(item) for item in (row.get("lesson_ids") or [])]}
    citations = {row.get("task_id") for row in rows if row.get("seat") == seat and row.get("task_id")
                 and lesson_id in [str(item) for item in (row.get("cited_ids") or [])]}
    linked = any(row.get("seat") == seat and row.get("level") == "decision_link" and row.get("lesson_id") == lesson_id
                 and (row.get("evidence") or {}).get("later_task_id") for row in rows)
    evidence = dict(trade_id=lesson_id, net_aud=observed.get("net_aud"), source="book_close")
    if not injections:
        return record_insufficient(seat, lesson_id, path, "not_in_prompt", evidence)
    if not (injections & citations):
        return record_insufficient(seat, lesson_id, path, "included_not_cited", evidence)
    if not linked:
        return record_insufficient(seat, lesson_id, path, "no_later_decision", evidence)
    row = dict(level="outcome_link", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=False, model_adoption=False, causal_edge=False,
               evidence=dict(evidence, link="outcome", causal_edge=False))
    return append(path, row)


AUTHORITY = dict(
    decisions="state/thf/seat_brain/<seat>/lessons.jsonl",
    trace="state/thf/lesson_trace.jsonl",
    not_used_for_decisions="state/thf/learning",
    rule="A lesson is included when placed in a prompt, cited only if the reply names that ref, and validated only by a later book close. Otherwise the state is insufficient_data.",
)
# The rule sentence above is the configured text and is left unchanged. It is incompatible
# with the implemented close path: a book close is not validation and is not a causal benefit.
RULE_GAP = ("AUTHORITY.rule says validation is a later book close. observe_book_close does not set validated. "
            "A close becomes an outcome link only after a task id, a ref= citation, and a different later decision id. "
            "validated is only record_validated with an explicit check. Existing rows are not rewritten.")


def publish_authority(directory, rows):
    """Refresh the legacy folder with the live trace. It does not create lessons or validations."""
    os.makedirs(directory, exist_ok=True)
    body = dict(AUTHORITY, utc=_now(), seats=coverage(rows), validated_count=sum(
        1 for row in rows if row.get("level") == "validated" and row.get("validated") is True))
    path = os.path.join(directory, "SOURCE.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return body


def load(path):
    rows = []
    try:
        lines = open(path, encoding="utf-8")
    except OSError:
        return rows
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


# These admins are outside the trader round. A missing trace row means no turn has used them yet.
WAITING_FOR_WORK = ("blind_challenger", "cfo", "devils_advocate", "fixer", "head_of_desk", "software_engineer")


def coverage(rows, seats=SEATS):
    out = []
    for seat in seats:
        mine = [row for row in rows if row.get("seat") == seat]
        included = [row for row in mine if row.get("level") == "included"]
        cited = [row for row in mine if row.get("cited_ids")]
        validated = [row for row in mine if row.get("level") == "validated" and row.get("validated") is True]
        insufficient = [row for row in mine if row.get("level") == "insufficient_data"]
        available = [row for row in mine if row.get("level") == "lessons_available"]
        if not mine and seat in WAITING_FOR_WORK:
            state = "waiting_for_work"
        elif not mine:
            state = "no_evidence"
        elif validated:
            state = "validated_impact_recorded"
        elif included and not validated:
            state = "included_not_validated"
        elif available and not cited:
            state = "lessons_available_no_trace"
        elif insufficient:
            state = "insufficient_data"
        else:
            state = "partial"
        out.append(dict(seat=seat, role="admin" if seat in ADMINS else "trader", state=state,
                        included=len(included), cited=len(cited), validated=len(validated),
                        outcome="validated" if validated else ("insufficient_data" if insufficient else None),
                        note=("No admin turn is in the trace. The brain block runs when this seat is actually used."
                              if state == "waiting_for_work" else
                              None if mine else "no close and no non-trade task evidence; do not invent an outcome")))
    return out
