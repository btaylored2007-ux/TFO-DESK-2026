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


def coverage(rows, seats=SEATS):
    out = []
    for seat in seats:
        mine = [row for row in rows if row.get("seat") == seat]
        included = [row for row in mine if row.get("level") == "included"]
        cited = [row for row in mine if row.get("level") == "cited" or row.get("cited_ids")]
        validated = [row for row in mine if row.get("level") == "validated" and row.get("validated") is True]
        if not mine:
            state = "no_evidence"
        elif included and not validated:
            state = "included_not_validated"
        elif validated:
            state = "validated_impact_recorded"
        else:
            state = "partial"
        out.append(dict(seat=seat, role="admin" if seat in ADMINS else "trader", state=state,
                        included=len(included), validated=len(validated),
                        note=None if mine else "no close and no non-trade task evidence; do not invent an outcome"))
    return out
