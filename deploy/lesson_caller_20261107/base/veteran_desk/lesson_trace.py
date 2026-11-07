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


def append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def record_included(seat, lesson_ids, source, path, task_id=None):
    ids = [str(item) for item in lesson_ids if item and str(item) != "unreferenced"]
    row = dict(level="included", seat=seat, lesson_ids=ids, source=source, task_id=task_id,
               utc=_now(), validated=False, model_adoption=False)
    append(path, row)
    return row


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
    append(path, row)
    return row


def record_insufficient(seat, lesson_id, path, reason, observed=None):
    """No causal claim. The reason says which observation was missing."""
    row = dict(level="insufficient_data", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=False, model_adoption=False, reason=reason,
               observed=dict(observed or {}))
    append(path, row)
    return row


def _seen(rows, seat, lesson_id, level):
    return any(row.get("seat") == seat and row.get("lesson_id") == lesson_id and row.get("level") == level for row in rows)


def observe_book_close(seat, lesson_id, path, observed):
    """Validate only when this ref was included and cited, and the book recorded the close.

    An uncited or unseen lesson stays insufficient_data. Model wording cannot validate it.
    """
    if not isinstance(observed, dict) or observed.get("source") != "book_close":
        raise ValueError("a book close is required")
    if str(observed.get("trade_id") or "") != str(lesson_id) or "net_aud" not in observed:
        raise ValueError("the close must name this trade and its book net")
    rows = load(path)
    if _seen(rows, seat, lesson_id, "validated"):
        return next(row for row in rows if row.get("seat") == seat and row.get("lesson_id") == lesson_id and row.get("level") == "validated")
    included = any(str(lesson_id) in [str(item) for item in (row.get("lesson_ids") or [])]
                   for row in rows if row.get("seat") == seat and row.get("level") == "included")
    cited = any(str(lesson_id) in [str(item) for item in (row.get("cited_ids") or [])]
                for row in rows if row.get("seat") == seat)
    evidence = dict(check="book_close", trade_id=str(lesson_id), net_aud=observed.get("net_aud"), source="book_close")
    if not included:
        return record_insufficient(seat, lesson_id, path, "not_in_prompt", evidence)
    if not cited:
        return record_insufficient(seat, lesson_id, path, "included_not_cited", evidence)
    row = dict(level="outcome_link", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=False, model_adoption=False, causal_edge=False,
               evidence=dict(evidence, link="outcome", causal_edge=False))
    append(path, row)
    return row


AUTHORITY = dict(
    decisions="state/thf/seat_brain/<seat>/lessons.jsonl",
    trace="state/thf/lesson_trace.jsonl",
    not_used_for_decisions="state/thf/learning",
    rule="A lesson is included when placed in a prompt, cited only if the reply names that ref, and validated only by a later book close. Otherwise the state is insufficient_data.",
)


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
        if not mine and seat in WAITING_FOR_WORK:
            state = "waiting_for_work"
        elif not mine:
            state = "no_evidence"
        elif validated:
            state = "validated_impact_recorded"
        elif included and not validated:
            state = "included_not_validated"
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
