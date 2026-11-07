#!/usr/bin/env python3
"""Build Unpublished Transparent Trading School v1.2.0 from immutable v1.1.1."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC111 = Path("/workspace/transparent-trading-school-review/v1.1.1/course.json")
OUT = ROOT / "v1.2.0" / "course.json"
OUT_ROOT = ROOT / "course.json"


def load_json(p: Path):
    return json.loads(p.read_text())


def improve_terse_explanations(course: dict) -> list:
    """Expand a few clearly unhelpful distractor explanations without touching keys."""
    patches = {
        ("tts.m03.qb03", "a"): "Adding 5% and 4% as if they were independent cash amounts, or otherwise combining them without compounding, does not produce a +9% TWR.",
        ("tts.m04.q03", "c"): "A solvent coupon bond does not jump to a zero price merely because coupon and yield differ.",
        ("tts.m04.q11", "c"): "A large yield move still changes price; the question is how the actual change compares with the duration line.",
        ("tts.m18.qb06", "c"): "Removing stops increases risk just when the drawdown ladder says to reduce it.",
    }
    changed = []
    for m in course["modules"]:
        for q in m["questions"]:
            for o in q["options"]:
                key = (q["id"], o["id"])
                if key in patches:
                    o["explanation"] = patches[key]
                    changed.append(f"{q['id']}:{o['id']}")
    return changed


def main():
    course = load_json(SRC111)
    assert course["version"] == "1.1.1"
    expansions = load_json(ROOT / "src" / "expansions.json")
    rubrics = load_json(ROOT / "src" / "rubrics.json")
    new_qs = load_json(ROOT / "src" / "new_questions.json")

    course["version"] = "1.2.0"
    course["supersedes"] = "1.1.1"
    course["generated"] = "2026-11-07"
    course["status"] = (
        "UNPUBLISHED DRAFT - independent content-quality pass v1.2.0; "
        "not expert, legal or compliance reviewed; not user-delivered"
    )
    course["disclaimer"] = (
        "General education only. Not financial advice. Not a regulated or accredited "
        "qualification. All A$ examples are fictional unless an official source example "
        "is explicitly identified. App trading is paper-only. Completion does not certify "
        "trading skill, predict profits, or prove live-trading competence. Simulation "
        "results are practice evidence only. No drawdown, VaR or stop figure is a guarantee."
    )
    course["content_review"] = {
        "type": "AI content review",
        "expert_certified": False,
        "legal_review": False,
        "compliance_review": False,
        "note": (
            "Numeric items were recomputed independently and mechanically. Narrative "
            "was reviewed by the authoring model against the v1.1.1 text and cited "
            "official extracts. This is not a substitute for a market practitioner, "
            "educator, or lawyer. Do not imply that every sentence is covered by the "
            "evidence register."
        ),
    }
    course["learning_tracks"] = {
        "human_school": "Lessons, quizzes, paper exercises, journals and the Module 21 capstone.",
        "ai_upload_skills": (
            "Uploaded or described strategies: restatement of what the AI understood, "
            "paper test with costs, versioning, results and approval status. Uploads "
            "must never bypass written risk limits."
        ),
        "separation": (
            "The two tracks are recorded separately. A quiz score does not approve an "
            "upload, and an uploaded backtest does not complete the school."
        ),
    }
    course["worked_dataset"] = {
        "label": "SYNTHETIC",
        "files": [
            "datasets/synthetic_mini_backtest.csv",
            "datasets/synthetic_corporate_actions.csv",
            "datasets/compute_backtest.py",
            "datasets/results.json",
        ],
        "seed": 20261107,
        "timezone": "Australia/Sydney",
        "note": (
            "Invented series for teaching look-ahead, costs, a 2-for-1 split, missing/"
            "halted prints, and in-sample versus out-of-sample selection. Not market "
            "data, not an order book, and not evidence of alpha or expected return. "
            "The CSV has no future-looking input columns."
        ),
    }
    course["schema_migration"] = {
        "from_versions": ["1.1.0", "1.1.1"],
        "to": "1.2.0",
        "compatibility": (
            "Additive. Existing module, lesson, objective and question ids from v1.1.1 "
            "are preserved. Cloud builds that understand v1.1.0/v1.1.1 can ignore "
            "unknown top-level keys and module.practical_rubric. Replace the course "
            "file as a whole."
        ),
        "additive_fields": [
            "content_review",
            "learning_tracks",
            "worked_dataset",
            "schema_migration",
            "modules[].practical_rubric",
            "new pool-B question objects",
        ],
    }

    # Strengthen progression language without turning defaults into owner law.
    course["progression"]["critical_mistake_remediation"]["status"] = (
        "proposed configurable default, not an owner-specified rule and not automatic certification"
    )
    course["progression"]["rules"].append(
        "Human School completion records are stored separately from AI Upload Skills approval records."
    )
    course["progression"]["rules"].append(
        "Paper-simulation results are never stored or displayed as live-trading competence or as accreditation."
    )
    course["capstone"]["note"] = (
        "Paper-simulated marks are practice evidence only, not validated live skill, "
        "not accreditation, and not an automatic certificate. Scoring defaults "
        "(average >= 2.0, no criterion at 0, no unremediated critical mistake, "
        "suggested minimum journaled paper trades) are configurable product defaults, "
        "not an owner-made law. Profit is not a pass criterion."
    )
    course["capstone"]["pass_default"] = (
        "average >= 2.0, no criterion at 0, no unremediated critical mistake "
        "(proposed configurable default; profit is not required)"
    )

    nav = course.get("navigation_requirements", [])
    extra_nav = [
        "Human School progress tracked separately from AI Upload Skills strategy paper-testing; no badges presented as qualifications.",
        "Upload Skills flow: describe or upload, show the AI's restatement, correct it, paper-test with costs, then record version, results and approval; reject any instruction to bypass risk limits.",
        "Practical feedback shows rubric scores with required objective evidence; a profitable paper result cannot pass a failed risk or process criterion.",
    ]
    for item in extra_nav:
        if item not in nav:
            nav.append(item)
    course["navigation_requirements"] = nav

    # Glossary PMI qualification + new terms on relevant modules
    for m in course["modules"]:
        if m["id"] == "tts.m16":
            for g in m["glossary"]:
                if g["term"] == "PMI":
                    g["definition"] = (
                        "Purchasing managers' index. S&P Global describes its PMI "
                        "diffusion indexes as ranging 0-100, with 50.0 meaning no change; "
                        "other providers publish their own methodologies."
                    )
        if m["id"] == "tts.m18":
            existing = {g["term"] for g in m["glossary"]}
            extras = [
                {"term": "Value at risk (VaR)",
                 "definition": "Basel MAR10.17: a measure of the worst expected loss on a portfolio of instruments resulting from market movements over a given time horizon and a pre-defined confidence level. Not a maximum-loss guarantee."},
                {"term": "Expected shortfall (ES)",
                 "definition": "Basel MAR10.18: a measure of the average of all potential losses exceeding the VaR at a given confidence level. Not a drawdown cap."},
                {"term": "Liquidity horizon",
                 "definition": "Basel MAR10.20: the time assumed to be required to exit or hedge a risk position without materially affecting market prices in stressed market conditions."},
            ]
            for e in extras:
                if e["term"] not in existing:
                    m["glossary"].append(e)
        if m["id"] == "tts.m20":
            existing = {g["term"] for g in m["glossary"]}
            extras = [
                {"term": "Corporate-action adjustment",
                 "definition": "Restating raw prices for splits and similar events so that return calculations are not dominated by a mechanical price jump."},
                {"term": "Stale print",
                 "definition": "A last price carried forward through a halt or missing session and wrongly treated as tradable."},
            ]
            for e in extras:
                if e["term"] not in existing:
                    m["glossary"].append(e)
        if m["id"] == "tts.m21":
            existing = {g["term"] for g in m["glossary"]}
            extras = [
                {"term": "Precommitment",
                 "definition": "Rules written before the session that constrain size, markets and no-trade conditions."},
                {"term": "Strategic no-trading",
                 "definition": "A planned decision to take no new risk because a gate failed; treated as successful process, not as inactivity."},
            ]
            for e in extras:
                if e["term"] not in existing:
                    m["glossary"].append(e)

    # Expand lessons
    missing = []
    for m in course["modules"]:
        for les in m["lessons"]:
            extra = expansions.get(les["id"])
            if not extra:
                missing.append(les["id"])
                continue
            body = les["body"].rstrip()
            les["body"] = body + "\n\n" + extra.strip() + "\n"

    if missing:
        raise SystemExit(f"missing expansions: {missing}")

    # Attach rubrics and add questions
    by_mod = {m["id"]: m for m in course["modules"]}
    existing_qids = {q["id"] for m in course["modules"] for q in m["questions"]}
    for m in course["modules"]:
        if m["id"] not in rubrics:
            raise SystemExit(f"missing rubric {m['id']}")
        m["practical_rubric"] = rubrics[m["id"]]

    for nq in new_qs:
        mid = "tts." + nq["id"].split(".")[1]  # tts.m01.qb01 -> tts.m01
        if nq["id"] in existing_qids:
            raise SystemExit(f"duplicate new id {nq['id']}")
        mod = by_mod[mid]
        obj_ids = {o["id"] for o in mod["objectives"]}
        if nq["objective_id"] not in obj_ids:
            raise SystemExit(f"{nq['id']} bad objective {nq['objective_id']}")
        mod["questions"].append(nq)
        existing_qids.add(nq["id"])

    # Evidence ids for new sources
    by_mod["tts.m08"]["evidence_ids"] = list(dict.fromkeys(by_mod["tts.m08"].get("evidence_ids", []) + ["R17"]))
    by_mod["tts.m18"]["evidence_ids"] = list(dict.fromkeys(by_mod["tts.m18"].get("evidence_ids", []) + ["R18"]))
    by_mod["tts.m20"]["evidence_ids"] = list(dict.fromkeys(["SYNTHETIC-DS"] + by_mod["tts.m20"].get("evidence_ids", [])))

    terse_changed = improve_terse_explanations(course)
    course["evidence_register_file"] = "evidence/register-v1.2.0.md"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(course, indent=1, ensure_ascii=False) + "\n"
    OUT.write_text(text)
    OUT_ROOT.write_text(text)
    print(json.dumps({
        "version": course["version"],
        "modules": len(course["modules"]),
        "lessons": sum(len(m["lessons"]) for m in course["modules"]),
        "questions": sum(len(m["questions"]) for m in course["questions"] for _ in [0]) if False else sum(len(m["questions"]) for m in course["modules"]),
        "new_questions": len(new_qs),
        "terse_patches": terse_changed,
        "out": str(OUT),
    }))


if __name__ == "__main__":
    main()
