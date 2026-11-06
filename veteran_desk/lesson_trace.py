"""Separate evidence that a lesson was placed in a prompt, cited, or validated.

Inclusion is not citation. Citation is not a validated change. A sentence such as
"I learned" is not adoption. Reviews stay hypotheses. This does not edit risk rules.
"""
import datetime as dt
import fcntl
import json
import os
import re

TRADERS = ("banks", "energy", "essentials_utilities", "healthcare", "news_filings", "price_action",
           "tech_ai_infra", "trend", "macro", "fx_commodities", "crypto")
ADMINS = ("head_of_desk", "head_trader", "cfo", "software_engineer", "fixer", "blind_challenger", "devils_advocate")
SEATS = TRADERS + ADMINS
CLAIM = re.compile(r"\bi\s+learned\b", re.I)
REF = re.compile(r"ref=([A-Za-z0-9_-]{3,80})")
REF_ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
# No verifier is configured. A caller-chosen check name is not one, and this set is not a new acceptance law.
GENUINE_VERIFIERS = frozenset()


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


def _parse_utc(value):
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def _rows_under_lock(handle):
    rows = []
    for line in handle:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append(path, row):
    """Append one trace row. The lock makes a repeat from a restart or a second writer one row."""
    for forbidden in ("reply", "prompt", "lesson", "text"):
        row.pop(forbidden, None)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        prior = _rows_under_lock(handle)
        key = _identity(row)
        if key is not None:
            for old in prior:
                if _identity(old) == key:
                    return old
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def record_included(seat, lesson_ids, source, path, task_id=None, account_id=None):
    if seat not in SEATS:
        raise ValueError("unknown seat")
    if not str(task_id or "").strip():
        raise ValueError("included lesson needs a task_id")
    ids = [str(item) for item in lesson_ids if item and str(item) != "unreferenced"]
    if not ids or any(REF_ID.fullmatch(item) is None for item in ids):
        raise ValueError("included lesson needs a real ref")
    row = dict(level="included", seat=seat, lesson_ids=ids, source=source, task_id=str(task_id),
               utc=_now(), validated=False, model_adoption=False)
    if account_id:
        row["account_id"] = str(account_id)
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


def _account_conflict(rows, seat, lesson_id, account_id):
    if not account_id:
        return False
    known = {row.get("account_id") for row in rows if row.get("seat") == seat and row.get("level") == "included"
             and lesson_id in [str(item) for item in (row.get("lesson_ids") or [])] and row.get("account_id")}
    return bool(known) and str(account_id) not in known or (not known and account_id)


def record_validated(seat, lesson_id, path, evidence):
    """A check name is not validation. No genuine verifier is configured, so this fail-closes.

    The configured acceptance sentence is AUTHORITY.rule. This function does not replace it.
    """
    if not isinstance(evidence, dict) or not str(evidence.get("check") or "").strip():
        raise ValueError("validated impact needs an explicit check name")
    if seat not in SEATS or REF_ID.fullmatch(str(lesson_id or "")) is None:
        raise ValueError("validation needs a known seat and a real ref")
    if evidence.get("check") not in GENUINE_VERIFIERS or not _qualifying_chain(load(path), seat, str(lesson_id)):
        raise ValueError("check name is not a genuine verifier and the provenance chain is required")
    row = dict(level="validated", seat=seat, lesson_id=str(lesson_id), utc=_now(),
               validated=True, model_adoption=False, evidence=dict(evidence))
    return append(path, row)


def _qualifying_chain(rows, seat, lesson_id):
    """Injection, a stored citation of that ref, a different-task decision, and a matching close."""
    injections = {row.get("task_id") for row in rows if row.get("seat") == seat and row.get("level") == "included"
                  and row.get("task_id") and lesson_id in [str(item) for item in (row.get("lesson_ids") or [])]}
    citations = {row.get("task_id") for row in rows if row.get("seat") == seat and row.get("task_id")
                 and lesson_id in [str(item) for item in (row.get("cited_ids") or [])]}
    decisions = [row for row in rows if row.get("seat") == seat and row.get("level") == "decision_link"
                 and row.get("lesson_id") == lesson_id
                 and (row.get("evidence") or {}).get("later_task_id") not in (None, row.get("task_id"))
                 and (row.get("evidence") or {}).get("trade_id") == lesson_id]
    closed = [row for row in rows if row.get("seat") == seat and row.get("level") == "outcome_link"
              and row.get("lesson_id") == lesson_id and (row.get("evidence") or {}).get("trade_id") == lesson_id]
    return bool(injections & citations) and bool(decisions) and bool(closed)


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


def link_later_decision(seat, lesson_id, path, later_task_id, injection_task_id, trade_id=None, account_id=None):
    """A later task used a lesson only when that earlier task included it and the reply cited it."""
    lesson_id, later_task_id, injection_task_id = str(lesson_id or ""), str(later_task_id or ""), str(injection_task_id or "")
    if seat not in SEATS or REF_ID.fullmatch(lesson_id) is None or not later_task_id or not injection_task_id:
        return record_insufficient(seat, lesson_id or "missing", path, "missing_decision_ids")
    if later_task_id == injection_task_id:
        return record_insufficient(seat, lesson_id, path, "later_task_is_the_injection",
                                   dict(task_id=injection_task_id))
    rows = load(path)
    if _account_conflict(rows, seat, lesson_id, account_id):
        return record_insufficient(seat, lesson_id, path, "cross_account", dict(task_id=injection_task_id))
    included = any(row.get("seat") == seat and row.get("level") == "included" and row.get("task_id") == injection_task_id
                   and lesson_id in [str(item) for item in (row.get("lesson_ids") or [])] for row in rows)
    cited = any(row.get("seat") == seat and row.get("task_id") == injection_task_id
                and lesson_id in [str(item) for item in (row.get("cited_ids") or [])] for row in rows)
    if not included or not cited:
        return record_insufficient(seat, lesson_id, path, "no_citation",
                                   dict(task_id=injection_task_id, later_task_id=later_task_id))
    evidence = dict(link="decision", later_task_id=later_task_id, injection_task_id=injection_task_id, causal_edge=False)
    if trade_id:
        if str(trade_id) != lesson_id:
            return record_insufficient(seat, lesson_id, path, "cross_trade", dict(task_id=injection_task_id))
        evidence["trade_id"] = lesson_id
    if account_id:
        evidence["account_id"] = str(account_id)
    row = dict(level="decision_link", seat=seat, lesson_id=lesson_id, task_id=injection_task_id,
               utc=_now(), validated=False, model_adoption=False, causal_edge=False, evidence=evidence)
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
    decisions = [row for row in rows if row.get("seat") == seat and row.get("level") == "decision_link"
                 and row.get("lesson_id") == lesson_id and (row.get("evidence") or {}).get("later_task_id")
                 and (row.get("evidence") or {}).get("later_task_id") != row.get("task_id")]
    matched = [row for row in decisions if (row.get("evidence") or {}).get("trade_id") == lesson_id]
    evidence = dict(trade_id=lesson_id, net_aud=observed.get("net_aud"), source="book_close")
    if observed.get("account_id") and _account_conflict(rows, seat, lesson_id, observed.get("account_id")):
        return record_insufficient(seat, lesson_id, path, "cross_account", evidence)
    if not injections:
        return record_insufficient(seat, lesson_id, path, "not_in_prompt", evidence)
    if not (injections & citations):
        return record_insufficient(seat, lesson_id, path, "included_not_cited", evidence)
    if not decisions:
        return record_insufficient(seat, lesson_id, path, "no_later_decision", evidence)
    if not matched:
        return record_insufficient(seat, lesson_id, path, "unmatched_trade", evidence)
    closed = _parse_utc(observed.get("closed_utc"))
    cited_at = [_parse_utc(row.get("utc")) for row in rows if row.get("seat") == seat and row.get("task_id") in citations
                and lesson_id in [str(item) for item in (row.get("cited_ids") or [])]]
    cited_at = [stamp for stamp in cited_at if stamp is not None]
    if closed is None:
        return record_insufficient(seat, lesson_id, path, "incomplete_close", evidence)
    if cited_at and closed < min(cited_at):
        return record_insufficient(seat, lesson_id, path, "stale_close", evidence)
    row = dict(level="outcome_link", seat=seat, lesson_id=lesson_id, utc=_now(),
               validated=False, model_adoption=False, causal_edge=False,
               evidence=dict(evidence, link="outcome", causal_edge=False, closed_utc=observed.get("closed_utc")))
    return append(path, row)


AUTHORITY = dict(
    decisions="state/thf/seat_brain/<seat>/lessons.jsonl",
    trace="state/thf/lesson_trace.jsonl",
    not_used_for_decisions="state/thf/learning",
    rule="A lesson is included when placed in a prompt, cited only if the reply names that ref, and validated only by a later book close. Otherwise the state is insufficient_data.",
)
# The sentence in AUTHORITY.rule is the configured acceptance law. It is not edited here.
# It says a later book close validates a lesson. The code does not implement that sentence:
# a close writes outcome_link with validated false, and a caller check name does not validate either.
# No genuine verifier is configured. That is a gap, not a replacement law.
RULE_GAP = ("AUTHORITY.rule is unchanged and still says a lesson is validated only by a later book close. "
            "The code does not follow that sentence: observe_book_close does not set validated, and "
            "record_validated rejects a caller check name because GENUINE_VERIFIERS is empty. "
            "A close can be an outcome link only after a same-seat task id, a ref= citation, a different later task, "
            "and a matching trade id whose close is not earlier than that citation. "
            "causal_edge stays false. Existing rows are not rewritten and this source change is not deployed.")


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
